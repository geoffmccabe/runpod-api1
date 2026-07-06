import os
import time
import json
import base64
import subprocess
import tempfile
import copy
import requests
import runpod
   
COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_BASE = f"http://{COMFY_HOST}:{COMFY_PORT}"
COMFY_READY_TIMEOUT = int(os.environ.get("COMFY_READY_TIMEOUT", "1800000"))

SUPABASE_URL    = os.environ.get("SUPABASE_URL", "https://zcpyipwqlssqdbyjoolb.supabase.co")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpjcHlpcHdxbHNzcWRieWpvb2xiIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzgxMDg0NiwiZXhwIjoyMDkzMzg2ODQ2fQ.RCnIru-Cc4a49KFbCL6NWKm8uvahiur0zaKSiAsnsGs")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "videos")



DEFAULT_WORKFLOW_PATH = "/workflow.json"

COMFY_OUTPUT_DIRS = [
    "/comfyui/output",
    "/comfyui/ComfyUI/output",
    "/root/comfyui/output",
]

OUTPUT_NODE_TYPES = {
    "SaveImage", "SaveAnimatedWEBP", "SaveAnimatedPNG",
    "SaveAnimatedGIF", "SaveVideo", "VHS_VideoCombine", "PreviewImage",
}

DEFAULT_FPS              = 16
DEFAULT_FRAMES_PER_SCENE = 201   # matches workflow default
MAX_FRAMES_PER_SCENE     = 257

# ---------------------------------------------------------------------------
# 30-scene node map — derived directly from wan22_SVI_Pro_30scenes_api.json
# Each entry: (positive_node, negative_node, svi_node, ibeo_node or None)
# ibeo_node is None for scene 1 (no overlap yet)
# ---------------------------------------------------------------------------
SCENE_NODES = [
    # (pos,   neg,  svi,  ibeo)
    ("214", "212", "218", None),   # scene 1
    ("225", "223", "222", "230"),  # scene 2
    ("235", "233", "232", "240"),  # scene 3
    ("245", "243", "242", "250"),  # scene 4
    ("255", "253", "252", "260"),  # scene 5
    ("265", "263", "262", "270"),  # scene 6
    ("275", "273", "272", "280"),  # scene 7
    ("285", "283", "282", "290"),  # scene 8
    ("295", "293", "292", "300"),  # scene 9
    ("305", "303", "302", "310"),  # scene 10
    ("315", "313", "312", "320"),  # scene 11
    ("325", "323", "322", "330"),  # scene 12
    ("335", "333", "332", "340"),  # scene 13
    ("345", "343", "342", "350"),  # scene 14
    ("355", "353", "352", "360"),  # scene 15
    ("365", "363", "362", "370"),  # scene 16
    ("375", "373", "372", "380"),  # scene 17
    ("385", "383", "382", "390"),  # scene 18
    ("395", "393", "392", "400"),  # scene 19
    ("405", "403", "402", "410"),  # scene 20
    ("415", "413", "412", "420"),  # scene 21
    ("425", "423", "422", "430"),  # scene 22
    ("435", "433", "432", "440"),  # scene 23
    ("445", "443", "442", "450"),  # scene 24
    ("455", "453", "452", "460"),  # scene 25
    ("465", "463", "462", "470"),  # scene 26
    ("475", "473", "472", "480"),  # scene 27
    ("485", "483", "482", "490"),  # scene 28
    ("495", "493", "492", "500"),  # scene 29
    ("505", "503", "502", "510"),  # scene 30
]

# Final IBEO output node per batch (slot 2 = combined frames)
# Batch 1 (scenes 1-10):  final IBEO = 310
# Batch 2 (scenes 11-20): final IBEO = 410
# Batch 3 (scenes 21-30): final IBEO = 510 (same as VHS source)
BATCH_FINAL_IBEO = {
    1: "310",
    2: "410",
    3: "510",
}

SCENES_PER_BATCH = 10

_comfy_ready = False


# ===========================================================================
# SUPABASE
# ===========================================================================

def supabase_upload(local_path: str, remote_filename: str) -> str:
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY env var not set.")
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{remote_filename}"
    with open(local_path, "rb") as f:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "video/mp4",
                "x-upsert": "true",
            },
            data=f,
            timeout=300,
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Supabase upload failed {resp.status_code}: {resp.text}")
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{remote_filename}"
    print(f"[supabase] -> {public_url}")
    return public_url


# ===========================================================================
# COMFY HELPERS
# ===========================================================================

def wait_for_comfy():
    global _comfy_ready
    if _comfy_ready:
        return
    start = time.time()
    last_err = None
    while time.time() - start < COMFY_READY_TIMEOUT:
        try:
            r = requests.get(f"{COMFY_BASE}/system_stats", timeout=3)
            if r.status_code == 200:
                _comfy_ready = True
                return
        except Exception as e:
            last_err = e
        time.sleep(1.0)
    raise RuntimeError(f"ComfyUI not ready: {last_err}")


def comfy_get(path):
    r = requests.get(f"{COMFY_BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def load_default_workflow():
    with open(DEFAULT_WORKFLOW_PATH) as f:
        return json.load(f)


def find_output_dir():
    for d in COMFY_OUTPUT_DIRS:
        if os.path.isdir(d):
            return d
    return COMFY_OUTPUT_DIRS[0]


def fetch_image_from_url(url: str, filename: str = "input_image.png") -> str:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    image_bytes = resp.content
    ct = resp.headers.get("Content-Type", "")
    if "jpeg" in ct or "jpg" in ct:
        filename = filename.replace(".png", ".jpg")
    elif "webp" in ct:
        filename = filename.replace(".png", ".webp")
    upload_resp = requests.post(
        f"{COMFY_BASE}/upload/image",
        files={"image": (filename, image_bytes, ct or "image/png")},
        data={"overwrite": "true"},
        timeout=60,
    )
    upload_resp.raise_for_status()
    return upload_resp.json().get("name", filename)


def upload_image_bytes_to_comfy(image_bytes: bytes, filename: str) -> str:
    resp = requests.post(
        f"{COMFY_BASE}/upload/image",
        files={"image": (filename, image_bytes, "image/png")},
        data={"overwrite": "true"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("name", filename)


def upload_images_to_comfy(images):
    uploaded = []
    for img in images:
        name = img["name"]
        b64 = img["image"]
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        image_bytes = base64.b64decode(b64)
        resp = requests.post(
            f"{COMFY_BASE}/upload/image",
            files={"image": (name, image_bytes, "image/png")},
            data={"overwrite": "true"},
            timeout=60,
        )
        resp.raise_for_status()
        uploaded.append(resp.json().get("name", name))
    return uploaded


def resolve_input_image(payload: dict):
    for key in ("image_url", "source_url", "target_url"):
        url = payload.get(key)
        if url:
            return fetch_image_from_url(url, f"{key.replace('_url','')}_image.png")
    images = payload.get("images", [])
    if images:
        uploaded = upload_images_to_comfy(images)
        if uploaded:
            return uploaded[0]
    return None


def submit_prompt(prompt, client_id="runpod"):
    r = requests.post(
        f"{COMFY_BASE}/prompt",
        json={"prompt": prompt, "client_id": client_id},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def wait_for_history(prompt_id, poll_interval=2.0, timeout=14400):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(f"{COMFY_BASE}/history/{prompt_id}", timeout=30)
            r.raise_for_status()
            data = r.json()
            if prompt_id in data:
                return data[prompt_id]
        except requests.exceptions.ConnectionError:
            print("[wait_for_history] Connection dropped, retrying...")
            time.sleep(5)
            continue
        time.sleep(poll_interval)
    raise RuntimeError(f"Prompt {prompt_id} did not finish within {timeout}s")


def get_largest_output_file(history):
    output_dir = find_output_dir()
    files = []
    for _, node_output in history.get("outputs", {}).items():
        for key in ("images", "videos", "gifs", "files"):
            for item in node_output.get(key, []):
                if item.get("type") == "temp":
                    continue
                fname     = item.get("filename", "")
                subfolder = item.get("subfolder", "")
                fpath = (
                    os.path.join(output_dir, subfolder, fname)
                    if subfolder else
                    os.path.join(output_dir, fname)
                )
                if os.path.isfile(fpath):
                    size = os.path.getsize(fpath)
                    files.append({"filename": fname, "filepath": fpath, "size": size})
                    print(f"[output] {fname} ({size/1024/1024:.1f} MB)")

    if not files:
        return None
    files.sort(key=lambda x: x["size"], reverse=True)
    chosen = files[0]
    print(f"[output] Using largest: {chosen['filename']} ({chosen['size']/1024/1024:.1f} MB)")
    return chosen["filepath"]


def workflow_has_output_node(workflow):
    return any(
        isinstance(node, dict) and node.get("class_type") in OUTPUT_NODE_TYPES
        for node in workflow.values()
    )


def extract_last_frame(video_path: str, output_path: str):
    cmd = ["ffmpeg", "-y", "-sseof", "-3", "-i", video_path,
           "-vframes", "1", "-q:v", "1", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg frame extract failed: {result.stderr}")
    return output_path


# ===========================================================================
# WORKFLOW BUILDING — 10 scenes per batch
#
# The 30-scene workflow is split into 3 batches of 10 scenes each.
# Each batch uses the exact node IDs from the API workflow JSON.
# Unused scene nodes are pruned before submission to keep the graph clean.
#
# Batch 1: scenes 1-10,  IBEO output node 310 -> VHS
# Batch 2: scenes 11-20, IBEO output node 410 -> VHS
# Batch 3: scenes 21-30, IBEO output node 510 -> VHS
# ===========================================================================

def build_batch_workflow(base_workflow, batch_num, batch_scene_indices,
                         prompts, negative_text, frames_per_scene,
                         sampling_steps, uploaded_filename, fps):
    """
    batch_scene_indices: list of 0-based scene indices for this batch (e.g. 0-9, 10-19, 20-29)
    prompts: full list of all prompts (indexed by scene idx)
    """
    wf = copy.deepcopy(base_workflow)

    # 1. Patch LoadImage
    if uploaded_filename:
        if "97" in wf:
            wf["97"]["inputs"]["image"] = uploaded_filename

    # 2. Patch BasicScheduler steps
    if sampling_steps and "122" in wf:
        wf["122"]["inputs"]["steps"] = int(sampling_steps)

    # 3. Patch fps on VHS
    if "204" in wf:
        wf["204"]["inputs"]["frame_rate"] = fps
        wf["204"]["inputs"]["filename_prefix"] = f"batch_{batch_num:02d}"

    # 4. Patch prompts for each scene in this batch
    for scene_idx in batch_scene_indices:
        pos_node, neg_node, svi_node, ibeo_node = SCENE_NODES[scene_idx]
        prompt_text = prompts[min(scene_idx, len(prompts) - 1)]

        if pos_node in wf:
            wf[pos_node]["inputs"]["text"] = prompt_text
        if neg_node in wf:
            wf[neg_node]["inputs"]["text"] = negative_text
        if svi_node in wf:
            wf[svi_node]["inputs"]["length"] = frames_per_scene

    # 5. Wire VHS to the final IBEO node of this batch (slot 2 = combined frames)
    final_ibeo = BATCH_FINAL_IBEO[batch_num]
    if "204" in wf:
        wf["204"]["inputs"]["images"] = [final_ibeo, 2]

    # 6. Prune all scene nodes NOT in this batch
    #    Determine which scene indices to KEEP
    keep_scenes = set(batch_scene_indices)
    all_scene_indices = set(range(30))
    prune_scenes = all_scene_indices - keep_scenes

    nodes_to_remove = set()
    for scene_idx in prune_scenes:
        pos_node, neg_node, svi_node, ibeo_node = SCENE_NODES[scene_idx]
        nodes_to_remove.add(pos_node)
        nodes_to_remove.add(neg_node)
        nodes_to_remove.add(svi_node)
        if ibeo_node:
            nodes_to_remove.add(ibeo_node)

    # Also remove any orphaned sampler/guidance/decode nodes for pruned scenes
    # These follow the pattern: for scene N, nodes in range (svi+1) to (next_svi-1)
    # Easier: remove all nodes that reference only pruned scene nodes
    # We'll do a safe removal — only remove nodes we explicitly know about
    # The non-scene nodes (loaders, samplers 127/128, etc.) are never removed

    for node_id in nodes_to_remove:
        wf.pop(node_id, None)

    # Also prune intermediate sampler/decode nodes between scene groups
    # Pattern: each scene N (idx i) uses nodes svi_node and svi_node±1..±N
    # Safe approach: prune by known ranges
    # Scene idx 0 (scene 1): nodes 219,220,221 (sampler, vae_decode x2)
    # Scene idx 1 (scene 2): nodes 226,227,228,229 etc.
    # These nodes have no fixed pattern so we rely on graph validity —
    # ComfyUI will simply not execute orphaned nodes.

    print(f"[batch {batch_num}] Scenes {[i+1 for i in batch_scene_indices]} | "
          f"VHS wired to IBEO {final_ibeo} slot 2 | "
          f"Removed {len(nodes_to_remove)} scene nodes from other batches")

    return wf


# ===========================================================================
# FFMPEG STITCH
# ===========================================================================

def ffmpeg_concat(video_paths: list, output_path: str):
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for vp in video_paths:
            f.write(f"file '{os.path.abspath(vp)}'\n")
        list_file = f.name
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
           "-i", list_file, "-c", "copy", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(list_file)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg stitch failed: {result.stderr}")
    return output_path


# ===========================================================================
# MAIN HANDLER
# ===========================================================================

def handler(job):
    payload = job.get("input") or {}
    action  = payload.get("action")

    if action == "ping":
        return {"status": "ok"}
    if action == "comfy_system_stats":
        wait_for_comfy()
        return comfy_get("/system_stats")

    wait_for_comfy()

    base_workflow = payload.get("workflow") or payload.get("prompt")
    if base_workflow:
        if isinstance(base_workflow, str):
            base_workflow = json.loads(base_workflow)
    else:
        base_workflow = load_default_workflow()

    if not workflow_has_output_node(base_workflow):
        raise RuntimeError("Workflow has no output node.")

    uploaded_filename = resolve_input_image(payload)

    # Prompts — accept list of up to 30
    raw_prompts = payload.get("prompts")
    prompt_text = payload.get("prompt_text") or payload.get("prompt")
    if raw_prompts and isinstance(raw_prompts, list):
        prompts = raw_prompts
    elif prompt_text:
        prompts = [prompt_text]
    else:
        prompts = ["cinematic video, smooth natural motion, highly detailed, 8k"]

    negative_text    = payload.get("negative_prompt",
        "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，"
        "整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，"
        "画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，"
        "静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走")
    sampling_steps   = payload.get("sampling_steps")
    frames_per_scene = min(int(payload.get("frames_per_scene", DEFAULT_FRAMES_PER_SCENE)),
                           MAX_FRAMES_PER_SCENE)
    fps              = int(payload.get("fps", DEFAULT_FPS))
    # num_scenes always 30 for this workflow — user can send fewer prompts
    # but the workflow always runs all 30 scene nodes in 3 batches
    num_scenes       = 30

    job_id      = job.get("id", f"job_{int(time.time())}")
    output_dir  = find_output_dir()
    chunk_urls  = []
    chunk_paths = []

    total_frames  = frames_per_scene * num_scenes
    expected_secs = total_frames / fps
    print(f"[handler] 30 scenes in 3 batches of 10 | "
          f"{frames_per_scene} frames/scene @ {fps}fps | "
          f"~{expected_secs:.0f}s ({expected_secs/60:.1f} min) total")

    # 3 batches: scenes 1-10, 11-20, 21-30
    batches = [
        (1, list(range(0, 10))),   # batch 1: scene indices 0-9
        (2, list(range(10, 20))),  # batch 2: scene indices 10-19
        (3, list(range(20, 30))),  # batch 3: scene indices 20-29
    ]

    for batch_num, batch_scene_indices in batches:
        print(f"\n[handler] === Batch {batch_num}/3: scenes "
              f"{batch_scene_indices[0]+1}-{batch_scene_indices[-1]+1} ===")

        wf = build_batch_workflow(
            base_workflow,
            batch_num=batch_num,
            batch_scene_indices=batch_scene_indices,
            prompts=prompts,
            negative_text=negative_text,
            frames_per_scene=frames_per_scene,
            sampling_steps=sampling_steps,
            uploaded_filename=uploaded_filename,
            fps=fps,
        )

        result    = submit_prompt(wf, f"runpod_b{batch_num}")
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"Batch {batch_num}: no prompt_id")

        print(f"[handler] Batch {batch_num} submitted: {prompt_id}")
        history = wait_for_history(prompt_id, timeout=14400)

        chunk_path = get_largest_output_file(history)
        if not chunk_path:
            raise RuntimeError(f"Batch {batch_num}: no output files found")

        chunk_filename = f"{job_id}_batch{batch_num:02d}.mp4"
        chunk_url      = supabase_upload(chunk_path, chunk_filename)
        chunk_urls.append(chunk_url)
        chunk_paths.append(chunk_path)
        print(f"[handler] Batch {batch_num} uploaded -> {chunk_url}")

        # Extract last frame for next batch continuity
        if batch_num < 3:
            last_frame_path = os.path.join(output_dir, f"last_frame_b{batch_num}.png")
            try:
                extract_last_frame(chunk_path, last_frame_path)
                uploaded_filename = upload_image_bytes_to_comfy(
                    open(last_frame_path, "rb").read(),
                    f"last_frame_b{batch_num}.png"
                )
                print(f"[handler] Last frame -> {uploaded_filename} (next batch input)")
            except Exception as e:
                print(f"[handler] WARNING: last frame extract failed: {e}")

    # Stitch 3 batch chunks into final video
    print(f"\n[handler] Stitching {len(chunk_paths)} batch chunks...")
    final_filename = f"{job_id}_final.mp4"
    final_local    = os.path.join(output_dir, final_filename)
    ffmpeg_concat(chunk_paths, final_local)

    final_url = supabase_upload(final_local, final_filename)
    print(f"[handler] Final video -> {final_url}")

    return {
        "status":                    "success",
        "final_video_url":           final_url,
        "chunk_urls":                chunk_urls,
        "total_scenes":              30,
        "total_frames":              total_frames,
        "expected_duration_seconds": round(expected_secs),
        "expected_duration_minutes": round(expected_secs / 60, 1),
    }


runpod.serverless.start({"handler": handler})

