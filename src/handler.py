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

MAX_FRAMES_PER_SCENE     = 257
DEFAULT_FRAMES_PER_SCENE = 81
DEFAULT_FPS              = 16
SCENES_PER_BATCH         = 3

MAX_TOTAL_SCENES         = 30
DEFAULT_NUM_SCENES       = 30

_comfy_ready = False


# --------------------------------------------------------------------------
# Supabase / Comfy plumbing (unchanged)
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Graph-traced scene chain for the flattened SVI Pro export.
#
# This replaces the old hardcoded "193:211" / "181:xxx" / "203:xxx" keys,
# which belonged to a different (subgraph-based) workflow export and no
# longer exist in your current flattened workflow.json — that mismatch is
# what caused the KeyError you hit. Instead we trace the actual node links
# (prev_samples / source_images / new_images) at runtime, so this works
# regardless of the exact node ids in the export.
# --------------------------------------------------------------------------

def find_node_id_by_class(workflow, class_type):
    matches = [k for k, v in workflow.items()
               if isinstance(v, dict) and v.get("class_type") == class_type]
    if not matches:
        raise RuntimeError(f"No node of class_type={class_type} found in workflow.")
    if len(matches) > 1:
        raise RuntimeError(f"Multiple nodes of class_type={class_type} found: {matches}")
    return matches[0]


def trace_scene_plan(workflow):
    def inp(node, key):
        return node["inputs"].get(key)

    wan_nodes = {k: v for k, v in workflow.items() if v.get("class_type") == "WanImageToVideoSVIPro"}
    sampler_nodes = {k: v for k, v in workflow.items() if v.get("class_type") == "SamplerCustomAdvanced"}
    vaedecode_nodes = {k: v for k, v in workflow.items() if v.get("class_type") == "VAEDecode"}
    extend_nodes = {k: v for k, v in workflow.items() if v.get("class_type") == "ImageBatchExtendWithOverlap"}

    if not wan_nodes:
        raise RuntimeError("No WanImageToVideoSVIPro nodes found — is this an SVI Pro workflow?")

    starts = [k for k, v in wan_nodes.items() if inp(v, "prev_samples") is None]
    if len(starts) != 1:
        raise RuntimeError(f"Expected exactly one starting scene, found: {starts}")
    start = starts[0]

    samplerid_to_nextwan = {}
    for k, v in wan_nodes.items():
        ps = inp(v, "prev_samples")
        if ps:
            samplerid_to_nextwan[ps[0]] = k

    def find_samplers_for_wan(wan_id):
        first = None
        for k, v in sampler_nodes.items():
            li = inp(v, "latent_image")
            if li and li[0] == wan_id and li[1] == 2:
                first = k
        second = None
        for k, v in sampler_nodes.items():
            li = inp(v, "latent_image")
            if li and li[0] == first:
                second = k
        return first, second

    def find_decode_for_sampler(sampler_id):
        for k, v in vaedecode_nodes.items():
            s = inp(v, "samples")
            if s and s[0] == sampler_id:
                return k
        return None

    scene_plan = []
    cur = start
    i = 1
    while cur:
        v = wan_nodes[cur]
        pos = inp(v, "positive")[0]
        neg = inp(v, "negative")[0]
        first_s, second_s = find_samplers_for_wan(cur)
        decode_id = find_decode_for_sampler(second_s)
        guider1 = inp(sampler_nodes[first_s], "guider")[0] if first_s else None
        guider2 = inp(sampler_nodes[second_s], "guider")[0] if second_s else None
        noise1 = inp(sampler_nodes[first_s], "noise")[0] if first_s else None
        noise2 = inp(sampler_nodes[second_s], "noise")[0] if second_s else None
        scene_plan.append({
            "scene": i,
            "wan": cur,
            "positive": pos,
            "negative": neg,
            "first_sampler": first_s,
            "second_sampler": second_s,
            "decode": decode_id,
            "guider1": guider1,
            "guider2": guider2,
            "noise1": noise1,
            "noise2": noise2,
        })
        cur = samplerid_to_nextwan.get(second_s)
        i += 1

    remaining = dict(extend_nodes)
    first_extend = None
    for k, v in remaining.items():
        src = inp(v, "source_images")
        if src and src[0] in vaedecode_nodes:
            first_extend = k
            break

    ext_chain = []
    if first_extend:
        ext_chain = [first_extend]
        cur = first_extend
        del remaining[cur]
        while remaining:
            nxt = None
            for k, v in remaining.items():
                src = inp(v, "source_images")
                if src and src[0] == cur:
                    nxt = k
                    break
            if not nxt:
                break
            ext_chain.append(nxt)
            cur = nxt
            del remaining[nxt]

    for idx, ext_id in enumerate(ext_chain):
        scene_num = idx + 2
        for sp in scene_plan:
            if sp["scene"] == scene_num:
                sp["extend"] = ext_id

    return scene_plan


def build_scene_workflow(base_workflow, scene_prompts, negative_text,
                          frames_per_scene, sampling_steps, uploaded_filename,
                          fps, filename_prefix="batch"):
    """
    Truncates the traced scene chain to len(scene_prompts) scenes
    (starting from scene 1 of the chain) and rewires VHS_VideoCombine to
    read from the correct final node. Used per-batch: every batch reuses
    the workflow's own "scene 1/2/3" chain as a self-contained 3-scene
    unit, with `uploaded_filename` swapped to the previous batch's last
    frame to carry continuity across batches — same idea as the original
    handler's per-batch subgraph approach, just graph-traced instead of
    hardcoded.
    """
    wf = copy.deepcopy(base_workflow)
    scene_plan = trace_scene_plan(wf)
    max_scenes = len(scene_plan)

    n = len(scene_prompts)
    if n > max_scenes:
        n = max_scenes
        scene_prompts = scene_prompts[:max_scenes]

    if isinstance(frames_per_scene, (list, tuple)):
        frame_lengths = [
            min(int(frames_per_scene[min(i, len(frames_per_scene) - 1)]), MAX_FRAMES_PER_SCENE)
            for i in range(n)
        ]
    else:
        frame_lengths = [min(int(frames_per_scene), MAX_FRAMES_PER_SCENE)] * n

    if uploaded_filename:
        load_image_id = find_node_id_by_class(wf, "LoadImage")
        wf[load_image_id]["inputs"]["image"] = uploaded_filename

    if sampling_steps:
        scheduler_id = find_node_id_by_class(wf, "BasicScheduler")
        wf[scheduler_id]["inputs"]["steps"] = int(sampling_steps)

    for i in range(n):
        sp = scene_plan[i]
        wf[sp["positive"]]["inputs"]["text"] = scene_prompts[i]
        wf[sp["negative"]]["inputs"]["text"] = negative_text
        wf[sp["wan"]]["inputs"]["length"] = frame_lengths[i]

    for sp in scene_plan[n:]:
        for key in ("positive", "negative", "wan", "first_sampler", "second_sampler",
                    "decode", "extend", "guider1", "guider2", "noise2"):
            node_id = sp.get(key)
            if node_id and node_id in wf:
                del wf[node_id]

    vhs_id = find_node_id_by_class(wf, "VHS_VideoCombine")
    if n == 1:
        wf[vhs_id]["inputs"]["images"] = [scene_plan[0]["decode"], 0]
    else:
        final_extend = scene_plan[n - 1]["extend"]
        wf[vhs_id]["inputs"]["images"] = [final_extend, 2]

    wf[vhs_id]["inputs"]["frame_rate"] = fps
    wf[vhs_id]["inputs"]["filename_prefix"] = filename_prefix

    return wf, n


# --------------------------------------------------------------------------
# Handler — 10 batches of 3 scenes, chained via last-frame continuity
# --------------------------------------------------------------------------

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

    raw_prompts = payload.get("prompts")
    prompt_text = payload.get("prompt_text") or payload.get("prompt")
    if raw_prompts and isinstance(raw_prompts, list):
        prompts = raw_prompts
    elif prompt_text:
        prompts = [prompt_text]
    else:
        prompts = ["cinematic video, smooth motion, highly detailed"]

    negative_text    = payload.get("negative_prompt", "blurry, static, low quality, deformed")
    sampling_steps   = payload.get("sampling_steps")
    frames_per_scene = min(int(payload.get("frames_per_scene", DEFAULT_FRAMES_PER_SCENE)), MAX_FRAMES_PER_SCENE)
    fps              = int(payload.get("fps", DEFAULT_FPS))

    num_scenes = max(1, int(payload.get("num_scenes", DEFAULT_NUM_SCENES)))
    if num_scenes > MAX_TOTAL_SCENES:
        print(f"[handler] Requested {num_scenes} scenes, capping at {MAX_TOTAL_SCENES}.")
        num_scenes = MAX_TOTAL_SCENES

    # reuse last prompt if fewer prompts than scenes were given
    if len(prompts) < num_scenes:
        prompts = prompts + [prompts[-1]] * (num_scenes - len(prompts))

    job_id      = job.get("id", f"job_{int(time.time())}")
    output_dir  = find_output_dir()
    chunk_urls  = []
    chunk_paths = []

    total_frames  = frames_per_scene * num_scenes
    expected_secs = total_frames / fps
    print(f"[handler] {num_scenes} scenes x {frames_per_scene} frames @ {fps}fps "
          f"= {total_frames} frames = {expected_secs:.0f}s ({expected_secs/60:.1f} min)")

    scene_idx = 0
    batch_num = 0

    while scene_idx < num_scenes:
        batch_num  += 1
        batch_size  = min(SCENES_PER_BATCH, num_scenes - scene_idx)
        batch_prompts = prompts[scene_idx:scene_idx + batch_size]

        print(f"[handler] Batch {batch_num}: scenes {scene_idx+1}-{scene_idx+batch_size} "
              f"({batch_size} scene(s) chained)")

        wf, actual = build_scene_workflow(
            base_workflow,
            scene_prompts=batch_prompts,
            negative_text=negative_text,
            frames_per_scene=frames_per_scene,
            sampling_steps=sampling_steps,
            uploaded_filename=uploaded_filename,
            fps=fps,
            filename_prefix=f"{job_id}_batch{batch_num:02d}",
        )

        result    = submit_prompt(wf, f"runpod_b{batch_num}")
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"Batch {batch_num}: no prompt_id")

        history = wait_for_history(prompt_id, timeout=14400)

        chunk_path = get_largest_output_file(history)
        if not chunk_path:
            raise RuntimeError(f"Batch {batch_num}: no output files found")

        chunk_filename = f"{job_id}_batch{batch_num:02d}.mp4"
        chunk_url      = supabase_upload(chunk_path, chunk_filename)
        chunk_urls.append(chunk_url)
        chunk_paths.append(chunk_path)
        print(f"[handler] Batch {batch_num} done -> {chunk_url}")

        # Extract last frame for next batch continuity
        if scene_idx + batch_size < num_scenes:
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

        scene_idx += batch_size

    # Stitch all batch chunks
    if len(chunk_paths) == 1:
        final_local    = chunk_paths[0]
        final_filename = os.path.basename(final_local)
    else:
        print(f"[handler] Stitching {len(chunk_paths)} chunks...")
        final_filename = f"{job_id}_final.mp4"
        final_local    = os.path.join(output_dir, final_filename)
        ffmpeg_concat(chunk_paths, final_local)

    final_url = supabase_upload(final_local, final_filename)
    print(f"[handler] Final -> {final_url}")

    return {
        "status":                    "success",
        "final_video_url":           final_url,
        "chunk_urls":                chunk_urls,
        "total_scenes":              num_scenes,
        "total_frames":              total_frames,
        "expected_duration_seconds": round(expected_secs),
        "expected_duration_minutes": round(expected_secs / 60, 1),
    }


runpod.serverless.start({"handler": handler})
