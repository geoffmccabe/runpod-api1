import os
import io
import json
import time
import uuid
import base64
import shutil
import subprocess
from typing import Any, Dict, Tuple, Optional

import requests
import runpod
from PIL import Image


COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_HTTP = f"http://{COMFY_HOST}:{COMFY_PORT}"

# Typical worker-comfyui layout
COMFYUI_DIR = os.environ.get("COMFYUI_DIR", "/workspace/ComfyUI")
COMFY_INPUT_DIR = os.environ.get("COMFY_INPUT_DIR", os.path.join(COMFYUI_DIR, "input"))
COMFY_OUTPUT_DIR = os.environ.get("COMFY_OUTPUT_DIR", os.path.join(COMFYUI_DIR, "output"))
COMFY_TEMP_DIR = os.environ.get("COMFY_TEMP_DIR", os.path.join(COMFYUI_DIR, "temp"))

WORKFLOW_PATH = os.environ.get(
    "WORKFLOW_PATH",
    "/workspace/workflows/wan_i2v_LOCKED.json"  # you committed this into repo/workflows; container path will differ,
    # but we also copy it at runtime if needed. See load_workflow().
)

# Repo path inside container (common in this template)
REPO_WORKFLOW_PATH = os.environ.get(
    "REPO_WORKFLOW_PATH",
    "/workspace/runpod-api1/workflows/wan_i2v_LOCKED.json"
)

DEFAULT_PROMPT = "cinematic motion, subtle camera movement, high quality"
DEFAULT_NEG_PROMPT = "low quality, blurry, watermark, text, logo"

DEFAULT_SECONDS = 6.0
DEFAULT_FPS = 16.0

# Demo defaults, API can override
DEFAULT_W = 704
DEFAULT_H = 992

# Internal model constraints
INTERNAL_MULTIPLE = 16

# Node IDs in the workflow you committed
NODE_LOAD_IMAGE = 58
NODE_RESIZE = 71
NODE_EMPTY_EMBEDS = 78
NODE_SAMPLER = 27
NODE_VIDEO_COMBINE = 92
NODE_TEXT_ENCODE = 16

# Sampler widget_values layout (from your workflow)
# widgets_values = [steps, cfg, shift, seed, scheduler, force_offload, rope_function, riflex_freq_index,
#                   denoise_strength, batched_cfg, start_step, end_step, add_noise_to_samples]
SAMPLER_IDX_STEPS = 0
SAMPLER_IDX_CFG = 1
SAMPLER_IDX_SHIFT = 2
SAMPLER_IDX_SEED = 3

# Text encode widgets_values = [positive_prompt, negative_prompt, force_offload, use_disk_cache, device]
TEXT_IDX_POS = 0
TEXT_IDX_NEG = 1

# Empty embeds widgets_values = [width, height, num_frames]
EMBEDS_IDX_W = 0
EMBEDS_IDX_H = 1
EMBEDS_IDX_FRAMES = 2

# Resize widgets_values = [width, height, upscale_method, keep_proportion, pad_color, crop_position, divisible_by, device]
RESIZE_IDX_W = 0
RESIZE_IDX_H = 1
RESIZE_IDX_DIV = 6

# Video combine uses widgets_values as a dict in your workflow
VIDEO_FPS_KEY = "frame_rate"
VIDEO_PREFIX_KEY = "filename_prefix"
VIDEO_SAVE_KEY = "save_output"


def _log(msg: str) -> None:
    print(msg, flush=True)


def round_to_multiple(x: int, m: int) -> int:
    if m <= 1:
        return max(1, x)
    return max(m, int(round(x / m) * m))


def floor_to_multiple(x: int, m: int) -> int:
    return max(m, (x // m) * m)


def center_crop_to_aspect(img: Image.Image, aspect_w: int, aspect_h: int) -> Image.Image:
    """Center-crop image to target aspect ratio (aspect_w:aspect_h)."""
    iw, ih = img.size
    target = aspect_w / aspect_h
    current = iw / ih

    if abs(current - target) < 1e-6:
        return img

    if current > target:
        # too wide: crop width
        new_w = int(ih * target)
        left = (iw - new_w) // 2
        return img.crop((left, 0, left + new_w, ih))
    else:
        # too tall: crop height
        new_h = int(iw / target)
        top = (ih - new_h) // 2
        return img.crop((0, top, iw, top + new_h))


def decode_base64_image(image_b64: str) -> Image.Image:
    raw = base64.b64decode(image_b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def ensure_dirs() -> None:
    os.makedirs(COMFY_INPUT_DIR, exist_ok=True)
    os.makedirs(COMFY_OUTPUT_DIR, exist_ok=True)
    os.makedirs(COMFY_TEMP_DIR, exist_ok=True)


def comfy_get(path: str, **params) -> requests.Response:
    url = f"{COMFY_HTTP}{path}"
    return requests.get(url, params=params, timeout=60)


def comfy_post(path: str, json_body: Dict[str, Any]) -> requests.Response:
    url = f"{COMFY_HTTP}{path}"
    return requests.post(url, json=json_body, timeout=60)


def wait_for_comfyui_ready(max_wait_s: int = 180) -> None:
    """Wait until ComfyUI is reachable."""
    t0 = time.time()
    while True:
        try:
            r = comfy_get("/system_stats")
            if r.status_code == 200:
                return
        except Exception:
            pass

        if time.time() - t0 > max_wait_s:
            raise RuntimeError("ComfyUI did not become ready in time.")
        time.sleep(1.0)


def load_workflow() -> Dict[str, Any]:
    """
    Load the workflow json.
    In this template, the repo is usually cloned into the container; try both paths.
    """
    candidates = [
        WORKFLOW_PATH,
        REPO_WORKFLOW_PATH,
        "/workspace/workflows/wan_i2v_LOCKED.json",
        "/workspace/i2v-workflows/wan_i2v_LOCKED.json",
    ]
    for p in candidates:
        if os.path.exists(p):
            _log(f"Loading workflow: {p}")
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)

    raise FileNotFoundError(
        "Could not find wan_i2v_LOCKED.json in expected locations. "
        f"Tried: {candidates}"
    )


def workflow_nodes_by_id(wf: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    out = {}
    for n in wf.get("nodes", []):
        out[int(n["id"])] = n
    return out


def save_input_image_for_comfy(img: Image.Image, filename: str) -> str:
    """
    Save into ComfyUI input dir. Comfy's LoadImage node expects a file name in input/.
    Return the file name (not full path) to place into the workflow.
    """
    ensure_dirs()
    path = os.path.join(COMFY_INPUT_DIR, filename)
    img.save(path, format="PNG", optimize=True)
    return filename


def set_workflow_params(
    wf: Dict[str, Any],
    prompt: str,
    negative_prompt: str,
    seed: int,
    steps: int,
    cfg: float,
    width_internal: int,
    height_internal: int,
    num_frames: int,
    fps: float,
    input_image_filename: str,
) -> Dict[str, Any]:
    nodes = workflow_nodes_by_id(wf)

    # 1) Replace prompts in WanVideoTextEncode (node 16)
    n_text = nodes.get(NODE_TEXT_ENCODE)
    if n_text and "widgets_values" in n_text:
        wv = list(n_text["widgets_values"])
        if len(wv) > TEXT_IDX_POS:
            wv[TEXT_IDX_POS] = prompt
        if len(wv) > TEXT_IDX_NEG:
            wv[TEXT_IDX_NEG] = negative_prompt
        n_text["widgets_values"] = wv

    # 2) LoadImage node: set filename
    n_load = nodes.get(NODE_LOAD_IMAGE)
    if n_load and "widgets_values" in n_load:
        wv = list(n_load["widgets_values"])
        if len(wv) >= 1:
            wv[0] = input_image_filename
        n_load["widgets_values"] = wv

    # 3) Resize node: set target dims and divisible_by
    n_resize = nodes.get(NODE_RESIZE)
    if n_resize and "widgets_values" in n_resize:
        wv = list(n_resize["widgets_values"])
        if len(wv) > RESIZE_IDX_W:
            wv[RESIZE_IDX_W] = width_internal
        if len(wv) > RESIZE_IDX_H:
            wv[RESIZE_IDX_H] = height_internal
        if len(wv) > RESIZE_IDX_DIV:
            wv[RESIZE_IDX_DIV] = INTERNAL_MULTIPLE
        n_resize["widgets_values"] = wv

    # 4) Empty embeds: width/height/frames
    n_emb = nodes.get(NODE_EMPTY_EMBEDS)
    if n_emb and "widgets_values" in n_emb:
        wv = list(n_emb["widgets_values"])
        if len(wv) > EMBEDS_IDX_W:
            wv[EMBEDS_IDX_W] = width_internal
        if len(wv) > EMBEDS_IDX_H:
            wv[EMBEDS_IDX_H] = height_internal
        if len(wv) > EMBEDS_IDX_FRAMES:
            wv[EMBEDS_IDX_FRAMES] = num_frames
        n_emb["widgets_values"] = wv

    # 5) Sampler: steps/cfg/seed
    n_samp = nodes.get(NODE_SAMPLER)
    if n_samp and "widgets_values" in n_samp:
        wv = list(n_samp["widgets_values"])
        if len(wv) > SAMPLER_IDX_STEPS:
            wv[SAMPLER_IDX_STEPS] = int(steps)
        if len(wv) > SAMPLER_IDX_CFG:
            wv[SAMPLER_IDX_CFG] = float(cfg)
        if len(wv) > SAMPLER_IDX_SEED:
            wv[SAMPLER_IDX_SEED] = int(seed)
        n_samp["widgets_values"] = wv

    # 6) Video combine: fps, prefix, save_output false
    n_vid = nodes.get(NODE_VIDEO_COMBINE)
    if n_vid and "widgets_values" in n_vid and isinstance(n_vid["widgets_values"], dict):
        d = dict(n_vid["widgets_values"])
        d[VIDEO_FPS_KEY] = float(fps)
        d[VIDEO_PREFIX_KEY] = "api_i2v"
        d[VIDEO_SAVE_KEY] = False
        n_vid["widgets_values"] = d

    return wf


def submit_workflow_to_comfy(wf: Dict[str, Any]) -> str:
    """
    Submit a workflow to ComfyUI. Return prompt_id.
    """
    # ComfyUI expects {"prompt": <workflow-json-as-graph>} for /prompt,
    # but it also accepts full workflow JSON in some installs via /prompt.
    # The worker-comfyui template usually supports sending the "api prompt graph".
    #
    # Here we rely on Comfy's ability to accept the workflow format you have (nodes/links).
    # If your Comfy build requires API-graph format, we will convert later, but for now
    # this is the simplest path consistent with many Wan/Comfy setups.
    r = comfy_post("/prompt", {"prompt": wf})
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI /prompt failed: {r.status_code} {r.text}")

    data = r.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI /prompt response missing prompt_id: {data}")
    return prompt_id


def wait_for_prompt_done(prompt_id: str, poll_s: float = 1.0, timeout_s: int = 900) -> Dict[str, Any]:
    """
    Wait for completion and return history JSON entry for prompt_id.
    """
    t0 = time.time()
    while True:
        r = comfy_get("/history")
        if r.status_code != 200:
            time.sleep(poll_s)
            continue

        hist = r.json()
        if prompt_id in hist:
            entry = hist[prompt_id]
            # Comfy marks outputs when complete
            if isinstance(entry, dict) and entry.get("outputs"):
                return entry

        if time.time() - t0 > timeout_s:
            raise RuntimeError("Timed out waiting for ComfyUI prompt completion.")
        time.sleep(poll_s)


def find_video_output_from_history(history_entry: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Return (filename, subfolder, type) for the first video-like output found.
    This is tailored for VHS_VideoCombine output structures.
    """
    outputs = history_entry.get("outputs", {})
    for node_id, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue

        # VHS often puts results in "gifs" even for mp4, or "videos"
        for key in ("videos", "gifs", "images", "files"):
            items = node_out.get(key)
            if not items:
                continue
            if isinstance(items, list) and len(items) > 0 and isinstance(items[0], dict):
                item = items[0]
                filename = item.get("filename")
                subfolder = item.get("subfolder", "")
                ftype = item.get("type", "temp")
                if filename and (filename.lower().endswith(".mp4") or key in ("videos", "gifs")):
                    return filename, subfolder, ftype

    raise RuntimeError(f"No video output found in history entry outputs: {list(outputs.keys())}")


def download_comfy_file(filename: str, subfolder: str, ftype: str) -> bytes:
    """
    Download a file from ComfyUI's /view endpoint.
    """
    r = comfy_get("/view", filename=filename, subfolder=subfolder, type=ftype)
    if r.status_code != 200:
        raise RuntimeError(f"Failed to download file from Comfy /view: {r.status_code} {r.text}")
    return r.content


def ffmpeg_scale_crop_to_exact(mp4_bytes: bytes, out_w: int, out_h: int) -> bytes:
    """
    Postprocess MP4 to exact dimensions using ffmpeg.
    We do a center crop/scale chain that preserves content as best as possible.
    """
    ensure_dirs()
    tmp_in = os.path.join(COMFY_TEMP_DIR, f"api_in_{uuid.uuid4().hex}.mp4")
    tmp_out = os.path.join(COMFY_TEMP_DIR, f"api_out_{uuid.uuid4().hex}.mp4")

    with open(tmp_in, "wb") as f:
        f.write(mp4_bytes)

    # Scale to cover, then center-crop to exact output
    # - Use lanczos for quality
    # - Force yuv420p for broad compatibility
    vf = (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
        f"crop={out_w}:{out_h}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", tmp_in,
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "19",
        "-preset", "veryfast",
        "-an",
        tmp_out
    ]

    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.decode('utf-8', errors='ignore')[:2000]}")

    with open(tmp_out, "rb") as f:
        out = f.read()

    # cleanup best-effort
    for fp in (tmp_in, tmp_out):
        try:
            os.remove(fp)
        except Exception:
            pass

    return out


def compute_internal_dims(
    req_w: int,
    req_h: int,
    enforce_aspect_5x7: bool = True
) -> Tuple[int, int]:
    """
    Internal dims must be divisible by 16.
    If enforce_aspect_5x7, we snap to 5:7 aspect before rounding.
    """
    if req_w <= 0 or req_h <= 0:
        req_w, req_h = DEFAULT_W, DEFAULT_H

    if enforce_aspect_5x7:
        # keep 5:7 ratio while staying close to requested size
        target = 5 / 7
        cur = req_w / req_h
        if cur > target:
            # too wide, reduce width
            req_w = int(req_h * target)
        else:
            # too tall, reduce height
            req_h = int(req_w / target)

    w_int = round_to_multiple(req_w, INTERNAL_MULTIPLE)
    h_int = round_to_multiple(req_h, INTERNAL_MULTIPLE)
    return w_int, h_int


def handler(job: Dict[str, Any]) -> Dict[str, Any]:
    inp = job.get("input", {}) or {}

    mode = (inp.get("mode") or "i2v").lower()
    if mode not in ("i2v", "t2v"):
        return {"error": "Invalid mode. Use 'i2v' or 't2v'."}

    prompt = inp.get("prompt", DEFAULT_PROMPT)
    negative_prompt = inp.get("negative_prompt", DEFAULT_NEG_PROMPT)

    req_w = int(inp.get("width", DEFAULT_W) or DEFAULT_W)
    req_h = int(inp.get("height", DEFAULT_H) or DEFAULT_H)

    fps = float(inp.get("fps", DEFAULT_FPS) or DEFAULT_FPS)
    seconds = float(inp.get("seconds", DEFAULT_SECONDS) or DEFAULT_SECONDS)
    if fps <= 0:
        fps = DEFAULT_FPS
    if seconds <= 0:
        seconds = DEFAULT_SECONDS

    num_frames = max(1, int(round(fps * seconds)))

    seed = int(inp.get("seed", 47) or 47)
    steps = int(inp.get("steps", 30) or 30)
    cfg = float(inp.get("cfg", 5.0) or 5.0)

    enforce_aspect_5x7 = bool(inp.get("enforce_aspect_5x7", True))
    postprocess_exact = bool(inp.get("postprocess_exact", True))

    ensure_dirs()
    wait_for_comfyui_ready()

    # Compute internal dims
    w_int, h_int = compute_internal_dims(req_w, req_h, enforce_aspect_5x7=enforce_aspect_5x7)

    input_image_filename = None

    if mode == "i2v":
        image_b64 = inp.get("image_base64") or inp.get("image")
        if not image_b64:
            return {"error": "Missing input.image_base64 for i2v mode."}

        img = decode_base64_image(image_b64)

        if enforce_aspect_5x7:
            img = center_crop_to_aspect(img, 5, 7)

        in_name = f"api_input_{uuid.uuid4().hex}.png"
        save_input_image_for_comfy(img, in_name)
        input_image_filename = in_name

    # Load workflow
    wf = load_workflow()
    nodes = workflow_nodes_by_id(wf)

    # If T2V, disable LoadImage node input
    if mode == "t2v":
        n_load = nodes.get(NODE_LOAD_IMAGE)
        if n_load and "widgets_values" in n_load:
            n_load["widgets_values"][0] = ""

    wf = set_workflow_params(
        wf=wf,
        prompt=prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        steps=steps,
        cfg=cfg,
        width_internal=w_int,
        height_internal=h_int,
        num_frames=num_frames,
        fps=fps,
        input_image_filename=input_image_filename or "",
    )

    prompt_id = submit_workflow_to_comfy(wf)
    hist_entry = wait_for_prompt_done(prompt_id)

    filename, subfolder, ftype = find_video_output_from_history(hist_entry)
    mp4_bytes = download_comfy_file(filename, subfolder, ftype)

    if postprocess_exact and (req_w != w_int or req_h != h_int):
        mp4_bytes = ffmpeg_scale_crop_to_exact(mp4_bytes, req_w, req_h)

    out_b64 = base64.b64encode(mp4_bytes).decode("utf-8")

    return {
        "mode": mode,
        "prompt_id": prompt_id,
        "requested": {
            "width": req_w,
            "height": req_h,
            "fps": fps,
            "seconds": seconds,
            "frames": num_frames,
        },
        "internal": {
            "width": w_int,
            "height": h_int,
            "multiple": INTERNAL_MULTIPLE,
        },
        "video_base64": out_b64,
        "video_mime": "video/mp4",
    }



runpod.serverless.start({"handler": handler})
