import os
import io
import json
import time
import uuid
import base64
import subprocess
from typing import Any, Dict, Tuple

import requests
import runpod
from PIL import Image


COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_HTTP = f"http://{COMFY_HOST}:{COMFY_PORT}"

COMFYUI_DIR = os.environ.get("COMFYUI_DIR", "/workspace/ComfyUI")
COMFY_INPUT_DIR = os.environ.get("COMFY_INPUT_DIR", os.path.join(COMFYUI_DIR, "input"))
COMFY_OUTPUT_DIR = os.environ.get("COMFY_OUTPUT_DIR", os.path.join(COMFYUI_DIR, "output"))
COMFY_TEMP_DIR = os.environ.get("COMFY_TEMP_DIR", os.path.join(COMFYUI_DIR, "temp"))

WORKFLOW_PATH = os.environ.get("WORKFLOW_PATH", "/workspace/workflows/wan_i2v_LOCKED.json")
REPO_WORKFLOW_PATH = os.environ.get("REPO_WORKFLOW_PATH", "/workspace/runpod-api1/workflows/wan_i2v_LOCKED.json")

DEFAULT_PROMPT = "cinematic motion, subtle camera movement, high quality"
DEFAULT_NEG_PROMPT = "low quality, blurry, watermark, text, logo"

DEFAULT_SECONDS = 6.0
DEFAULT_FPS = 16.0
DEFAULT_W = 704
DEFAULT_H = 992
INTERNAL_MULTIPLE = 16

# Node IDs in your workflow
NODE_LOAD_IMAGE = 58
NODE_RESIZE = 71
NODE_EMPTY_EMBEDS = 78
NODE_SAMPLER = 27
NODE_VIDEO_COMBINE = 92
NODE_TEXT_ENCODE = 16

SAMPLER_IDX_STEPS = 0
SAMPLER_IDX_CFG = 1
SAMPLER_IDX_SEED = 3

TEXT_IDX_POS = 0
TEXT_IDX_NEG = 1

EMBEDS_IDX_W = 0
EMBEDS_IDX_H = 1
EMBEDS_IDX_FRAMES = 2

RESIZE_IDX_W = 0
RESIZE_IDX_H = 1
RESIZE_IDX_DIV = 6

VIDEO_FPS_KEY = "frame_rate"
VIDEO_PREFIX_KEY = "filename_prefix"
VIDEO_SAVE_KEY = "save_output"

# LoRA roots to scan for sidecar JSONs
LORA_SCAN_ROOTS = [
    "/workspace/wan-storage/models/lora-video",
    "/workspace/wan-storage/models/lora-image",
    "/workspace/models/lora-video",
    "/workspace/models/lora-image",
]


def _log(msg: str) -> None:
    print(msg, flush=True)


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
    candidates = [
        WORKFLOW_PATH,
        REPO_WORKFLOW_PATH,
        "/workspace/workflows/wan_i2v_LOCKED.json",
        "/workspace/i2v-workflows/wan_i2v_LOCKED.json",
    ]
    for p in candidates:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(f"Could not find wan_i2v_LOCKED.json. Tried: {candidates}")


def workflow_nodes_by_id(wf: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for n in wf.get("nodes", []):
        out[int(n["id"])] = n
    return out


def decode_base64_image(image_b64: str) -> Image.Image:
    raw = base64.b64decode(image_b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def save_input_image_for_comfy(img: Image.Image, filename: str) -> str:
    ensure_dirs()
    path = os.path.join(COMFY_INPUT_DIR, filename)
    img.save(path, format="PNG", optimize=True)
    return filename


def round_to_multiple(x: int, m: int) -> int:
    if m <= 1:
        return max(1, x)
    return max(m, int(round(x / m) * m))


def compute_internal_dims(req_w: int, req_h: int) -> Tuple[int, int]:
    if req_w <= 0 or req_h <= 0:
        req_w, req_h = DEFAULT_W, DEFAULT_H
    w_int = round_to_multiple(req_w, INTERNAL_MULTIPLE)
    h_int = round_to_multiple(req_h, INTERNAL_MULTIPLE)
    return w_int, h_int


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

    n_text = nodes.get(NODE_TEXT_ENCODE)
    if n_text and "widgets_values" in n_text:
        wv = list(n_text["widgets_values"])
        if len(wv) > TEXT_IDX_POS:
            wv[TEXT_IDX_POS] = prompt
        if len(wv) > TEXT_IDX_NEG:
            wv[TEXT_IDX_NEG] = negative_prompt
        n_text["widgets_values"] = wv

    n_load = nodes.get(NODE_LOAD_IMAGE)
    if n_load and "widgets_values" in n_load:
        wv = list(n_load["widgets_values"])
        if len(wv) >= 1:
            wv[0] = input_image_filename
        n_load["widgets_values"] = wv

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

    n_vid = nodes.get(NODE_VIDEO_COMBINE)
    if n_vid and "widgets_values" in n_vid and isinstance(n_vid["widgets_values"], dict):
        d = dict(n_vid["widgets_values"])
        d[VIDEO_FPS_KEY] = float(fps)
        d[VIDEO_PREFIX_KEY] = "api_video"
        d[VIDEO_SAVE_KEY] = False
        n_vid["widgets_values"] = d

    return wf


def submit_workflow_to_comfy(wf: Dict[str, Any]) -> str:
    r = comfy_post("/prompt", {"prompt": wf})
    if r.status_code != 200:
        raise RuntimeError(f"ComfyUI /prompt failed: {r.status_code} {r.text}")
    data = r.json()
    pid = data.get("prompt_id")
    if not pid:
        raise RuntimeError(f"ComfyUI /prompt missing prompt_id: {data}")
    return pid


def wait_for_prompt_done(prompt_id: str, poll_s: float = 1.0, timeout_s: int = 900) -> Dict[str, Any]:
    t0 = time.time()
    while True:
        r = comfy_get("/history")
        if r.status_code == 200:
            hist = r.json()
            if prompt_id in hist and isinstance(hist[prompt_id], dict) and hist[prompt_id].get("outputs"):
                return hist[prompt_id]
        if time.time() - t0 > timeout_s:
            raise RuntimeError("Timed out waiting for ComfyUI prompt completion.")
        time.sleep(poll_s)


def find_video_output_from_history(history_entry: Dict[str, Any]) -> Tuple[str, str, str]:
    outputs = history_entry.get("outputs", {})
    for _, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for key in ("videos", "gifs", "files", "images"):
            items = node_out.get(key)
            if isinstance(items, list) and items and isinstance(items[0], dict):
                item = items[0]
                filename = item.get("filename")
                subfolder = item.get("subfolder", "")
                ftype = item.get("type", "temp")
                if filename and filename.lower().endswith(".mp4"):
                    return filename, subfolder, ftype
    raise RuntimeError("No MP4 video output found in ComfyUI history.")


def download_comfy_file(filename: str, subfolder: str, ftype: str) -> bytes:
    r = comfy_get("/view", filename=filename, subfolder=subfolder, type=ftype)
    if r.status_code != 200:
        raise RuntimeError(f"Failed to download from Comfy /view: {r.status_code} {r.text}")
    return r.content


def list_loras() -> Dict[str, Any]:
    loras = []
    seen = set()

    for root in LORA_SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        for dp, _, files in os.walk(root):
            for f in files:
                if not f.endswith(".json"):
                    continue
                if f == "registry.json":
                    continue
                fp = os.path.join(dp, f)
                try:
                    d = json.load(open(fp, "r", encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                alias = d.get("alias")
                category = d.get("category")
                if not alias or not category:
                    continue
                key = (alias, category)
                if key in seen:
                    continue
                seen.add(key)
                loras.append({
                    "alias": alias,
                    "category": category,
                    "is_nsfw": bool(d.get("is_nsfw", False)),
                    "default_weight": float(d.get("default_weight", 1.0)),
                    "min_weight": float(d.get("min_weight", 0.0)),
                    "max_weight": float(d.get("max_weight", 2.0)),
                })

    loras = sorted(loras, key=lambda x: (x["category"], x["alias"]))
    return {"loras": loras, "count": len(loras)}


def handler(job: Dict[str, Any]) -> Dict[str, Any]:
    inp = job.get("input") or {}

    # IMPORTANT: RunPod build/test calls handler with empty input
    if not inp:
        return {"ok": True, "status": "ready", "message": "worker initialized"}

    # LoRA listing
    if inp.get("action") == "list_loras" or inp.get("mode") == "list_loras":
        return list_loras()

    # I2V requires an image
    image_b64 = inp.get("image_base64") or inp.get("image")
    if not image_b64:
        return {"error": "Missing input.image_base64 (base64-encoded image)."}

    prompt = inp.get("prompt", DEFAULT_PROMPT)
    negative_prompt = inp.get("negative_prompt", DEFAULT_NEG_PROMPT)

    req_w = int(inp.get("width", DEFAULT_W) or DEFAULT_W)
    req_h = int(inp.get("height", DEFAULT_H) or DEFAULT_H)

    fps = float(inp.get("fps", DEFAULT_FPS) or DEFAULT_FPS)
    seconds = float(inp.get("seconds", DEFAULT_SECONDS) or DEFAULT_SECONDS)
    fps = fps if fps > 0 else DEFAULT_FPS
    seconds = seconds if seconds > 0 else DEFAULT_SECONDS

    num_frames = max(1, int(round(fps * seconds)))

    seed = int(inp.get("seed", 47) or 47)
    steps = int(inp.get("steps", 30) or 30)
    cfg = float(inp.get("cfg", 5.0) or 5.0)

    ensure_dirs()
    wait_for_comfyui_ready()

    img = decode_base64_image(image_b64)

    w_int, h_int = compute_internal_dims(req_w, req_h)

    in_name = f"api_input_{uuid.uuid4().hex}.png"
    save_input_image_for_comfy(img, in_name)

    wf = load_workflow()
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
        input_image_filename=in_name,
    )

    prompt_id = submit_workflow_to_comfy(wf)
    hist_entry = wait_for_prompt_done(prompt_id)

    filename, subfolder, ftype = find_video_output_from_history(hist_entry)
    mp4_bytes = download_comfy_file(filename, subfolder, ftype)

    out_b64 = base64.b64encode(mp4_bytes).decode("utf-8")

    return {
        "prompt_id": prompt_id,
        "requested": {"width": req_w, "height": req_h, "fps": fps, "seconds": seconds, "frames": num_frames},
        "internal": {"width": w_int, "height": h_int, "multiple": INTERNAL_MULTIPLE},
        "video_base64": out_b64,
        "video_mime": "video/mp4",
    }


runpod.serverless.start({"handler": handler})
