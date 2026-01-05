#!/usr/bin/env bash
set -e

# Canonical paths for this image
export COMFYUI_DIR="${COMFYUI_DIR:-/comfyui}"
export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"

# Ensure handler and any scripts use the venv python
PY="/opt/venv/bin/python"

# Start ComfyUI in the background
cd "$COMFYUI_DIR"
$PY main.py --listen 0.0.0.0 --port "$COMFY_PORT" > /tmp/comfyui.log 2>&1 &

# Now start the RunPod handler (foreground)
cd /
exec $PY -u /handler.py
