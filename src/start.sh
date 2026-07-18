#!/usr/bin/env bash
set -euo pipefail

echo "[start.sh] Booting container..."

COMFY_PORT="8188"
COMFY_HTTP="http://127.0.0.1:${COMFY_PORT}"
# FIX 1: Point to the correct virtual environment path created by uv
PY="/comfyui/.venv/bin/python"

echo "[start.sh] COMFY_HTTP=${COMFY_HTTP}"
echo "[start.sh] PY=${PY}"

# ---------------------------------------------------------------------------
# PRE-FLIGHT: verify every lora_name referenced in /workflow.json actually
# exists in models/loras/ before we ever start ComfyUI. Fails fast with a
# clear message instead of burning a full job on a missing/misnamed file.
# ---------------------------------------------------------------------------
echo "[start.sh] Running LoRA preflight check..."
"${PY}" - <<'PYEOF'
import json
import os
import sys

WORKFLOW_PATH = "/workflow.json"
LORA_DIR = "/comfyui/models/loras"

if not os.path.isfile(WORKFLOW_PATH):
    print(f"[preflight] WARNING: {WORKFLOW_PATH} not found, skipping check.")
    sys.exit(0)

with open(WORKFLOW_PATH) as f:
    wf = json.load(f)

referenced = set()
for node_id, node in wf.items():
    if isinstance(node, dict) and node.get("class_type") == "LoraLoaderModelOnly":
        name = node.get("inputs", {}).get("lora_name")
        if name:
            referenced.add((node_id, node.get("_meta", {}).get("title", ""), name))

if not referenced:
    print("[preflight] No LoraLoaderModelOnly nodes found in workflow.json.")
    sys.exit(0)

on_disk = set()
if os.path.isdir(LORA_DIR):
    on_disk = set(os.listdir(LORA_DIR))
else:
    print(f"[preflight] ERROR: lora directory {LORA_DIR} does not exist.")
    sys.exit(1)

missing = [(nid, title, name) for nid, title, name in referenced if name not in on_disk]

print(f"[preflight] {len(referenced)} lora node(s) referenced, "
      f"{len(on_disk)} file(s) found in {LORA_DIR}")

if missing:
    print("[preflight] ERROR: the following LoRAs are referenced in workflow.json "
          "but NOT found on disk:")
    for nid, title, name in missing:
        print(f"  - node {nid} ({title}): '{name}'")
    sys.exit(1)

print("[preflight] All referenced LoRAs found on disk. OK.")
for nid, title, name in sorted(referenced):
    print(f"  - node {nid} ({title}): {name}")
PYEOF

echo "[start.sh] Starting ComfyUI via comfy-cli (workspace: /comfyui/ComfyUI)..."
# FIX 2: Point workspace to the exact directory where main.py exists
comfy --workspace /comfyui/ComfyUI launch -- --listen 0.0.0.0 --port ${COMFY_PORT} &
COMFY_PID=$!

echo "[start.sh] Waiting for ComfyUI to become ready..."
for i in $(seq 1 240); do
  if curl -fsS "${COMFY_HTTP}/system_stats" >/dev/null 2>&1; then
    echo "[start.sh] ComfyUI is ready."
    break
  fi
  sleep 1
done

if ! curl -fsS "${COMFY_HTTP}/system_stats" >/dev/null 2>&1; then
  echo "[start.sh] ERROR: ComfyUI did not become ready in time."
  echo "[start.sh] ComfyUI PID=${COMFY_PID}"
  exit 1
fi

echo "[start.sh] Starting RunPod handler..."
# This executes your handler using the correct python interpreter from your root directory
exec ${PY} -u /handler.py
