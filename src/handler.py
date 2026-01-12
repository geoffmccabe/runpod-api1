import os
import time
import requests
import runpod

COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))
COMFY_BASE = f"http://{COMFY_HOST}:{COMFY_PORT}"

COMFY_READY_TIMEOUT = int(os.environ.get("COMFY_READY_TIMEOUT", "180"))
COMFY_READY_POLL = 1.0

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


def submit_prompt(prompt):
    r = requests.post(
        f"{COMFY_BASE}/prompt",
        json={"prompt": prompt},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def wait_for_history(prompt_id):
    while True:
        r = requests.get(f"{COMFY_BASE}/history/{prompt_id}", timeout=10)
        r.raise_for_status()
        data = r.json()
        if prompt_id in data:
            return data[prompt_id]
        time.sleep(0.5)


def handler(job):
    payload = job.get("input") or {}

    # Fast path for RunPod tests and health checks.
    # IMPORTANT: does NOT wait for ComfyUI.
    if payload.get("action") == "ping":
        return {"status": "ok"}

    # Real work path
    wait_for_comfy()

    if "prompt" not in payload:
        raise ValueError("Missing 'prompt' in job input")

    result = submit_prompt(payload["prompt"])
    prompt_id = result.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUI did not return a prompt_id")

    history = wait_for_history(prompt_id)

    return {
        "prompt_id": prompt_id,
        "history": history,
    }


runpod.serverless.start({"handler": handler})
