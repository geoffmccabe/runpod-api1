import os
import time
import json
import requests
import runpod

COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_BASE = f"http://{COMFY_HOST}:{COMFY_PORT}"

COMFY_READY_TIMEOUT = int(os.environ.get("COMFY_READY_TIMEOUT", "180"))
COMFY_READY_POLL = 1.0

# Common locations across pods/serverless
REGISTRY_REL = "models/lora-video/registry.generated.json"
REGISTRY_CANDIDATE_BASES = [
    "/workspace/wan-storage",
    "/workspace",
    "/wan-storage",
    "/runpod-volume",
    "/workspace/runpod-volume",
]

COMFY_LOG_CANDIDATES = [
    "/comfyui/user/comfyui.log",
    "/comfyui/ComfyUI/user/comfyui.log",
]

_comfy_ready = False


def wait_for_comfy():
    global _comfy_ready
    if _comfy_ready:
        return

    start = time.time()
    last_err = None

    while time.time() - start < COMFY_READY_TIMEOUT:
        try:
            r = requests.get(f"{COMFY_BASE}/system_stats", timeout=2)
            if r.status_code == 200:
                _comfy_ready = True
                return
        except Exception as e:
            last_err = e

        time.sleep(COMFY_READY_POLL)

    raise RuntimeError(f"ComfyUI did not become ready: {last_err}")


def comfy_get(path):
    r = requests.get(f"{COMFY_BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()


def safe_listdir(path, limit=200):
    try:
        items = sorted(os.listdir(path))[:limit]
        return {"path": path, "exists": True, "items": items}
    except FileNotFoundError:
        return {"path": path, "exists": False, "items": []}
    except Exception as e:
        return {"path": path, "exists": True, "error": str(e), "items": []}


def tail_file(path, max_bytes=8000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes), os.SEEK_SET)
            data = f.read().decode("utf-8", errors="replace")
        return {"path": path, "exists": True, "tail": data}
    except FileNotFoundError:
        return {"path": path, "exists": False, "tail": ""}
    except Exception as e:
        return {"path": path, "exists": True, "error": str(e), "tail": ""}


def get_env_registry_path():
    # Read env dynamically so changes apply when a new worker boots
    return os.environ.get("LORA_REGISTRY_PATH", "").strip()


def registry_candidates():
    paths = []

    env_path = get_env_registry_path()
    if env_path:
        paths.append(env_path)

    for b in REGISTRY_CANDIDATE_BASES:
        paths.append(f"{b}/{REGISTRY_REL}")

    # de-dupe
    out, seen = [], set()
    for p in paths:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def resolve_registry_path():
    for p in registry_candidates():
        if os.path.exists(p):
            return p

    tried = "\n".join([f"- {p} (exists={os.path.exists(p)})" for p in registry_candidates()])
    raise RuntimeError(
        "LoRA registry not found in this worker.\n"
        "Tried:\n"
        f"{tried}\n\n"
        "Fix: ensure your Serverless endpoint has the network volume attached and locked to the volume's datacenter, "
        "then set LORA_REGISTRY_PATH to the correct in-worker path."
    )


def load_registry():
    path = resolve_registry_path()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data["_resolved_registry_path"] = path

    return data


def submit_prompt(prompt):
    r = requests.post(f"{COMFY_BASE}/prompt", json={"prompt": prompt}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"ComfyUI /prompt failed: HTTP {r.status_code} | {r.text[:4000]}")
    return r.json()


def wait_for_history(prompt_id):
    while True:
        r = requests.get(f"{COMFY_BASE}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        data = r.json()
        if prompt_id in data:
            return data[prompt_id]
        time.sleep(0.5)


def handler(job):
    payload = job.get("input") or {}
    action = payload.get("action")

    if action == "ping":
        return {"status": "ok"}

    if action == "diag":
        diag = {
            "env": {
                "LORA_REGISTRY_PATH": os.environ.get("LORA_REGISTRY_PATH"),
                "COMFY_HOST": os.environ.get("COMFY_HOST"),
                "COMFY_PORT": os.environ.get("COMFY_PORT"),
            },
            "mount_listings": [
                safe_listdir("/"),
                safe_listdir("/workspace"),
                safe_listdir("/workspace/wan-storage"),
                safe_listdir("/runpod-volume"),
            ],
            "registry_candidates": [{"path": p, "exists": os.path.exists(p)} for p in registry_candidates()],
            "comfy_log_tail": [tail_file(p) for p in COMFY_LOG_CANDIDATES],
        }

        try:
            wait_for_comfy()
            diag["comfy_system_stats"] = comfy_get("/system_stats")
        except Exception as e:
            diag["comfy_system_stats_error"] = str(e)

        return diag

    if action == "registry":
        return load_registry()

    if action == "comfy_system_stats":
        wait_for_comfy()
        return comfy_get("/system_stats")

    if action == "comfy_object_info":
        wait_for_comfy()
        return comfy_get("/object_info")

    wait_for_comfy()

    if "prompt" not in payload:
        raise ValueError("Missing 'prompt' in job input")

    result = submit_prompt(payload["prompt"])
    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI did not return a prompt_id")

    history = wait_for_history(prompt_id)
    return {"prompt_id": prompt_id, "history": history}


runpod.serverless.start({"handler": handler})
