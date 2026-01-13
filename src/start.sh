#!/usr/bin/env bash
set -euo pipefail

echo "[start.sh] Booting container..."

# Defaults (can be overridden by env)
COMFYUI_DIR="${COMFYUI_DIR:-/comfyui}"
COMFY_HOST="${COMFY_HOST:-0.0.0.0}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_HTTP="http://${COMFY_HOST}:${COMFY_PORT}"

echo "[start.sh] COMFYUI_DIR=${COMFYUI_DIR}"
echo "[start.sh] COMFY_HTTP=${COMFY_HTTP}"

# Start ComfyUI in background
cd "${COMFYUI_DIR}"

# Many ComfyUI installs use main.py; some use a comfy launcher.
# main.py is standard for the repo installed by comfy-cli.
echo "[start.sh] Starting ComfyUI..."
python -u main.py --listen "${COMFY_HOST}" --port "${COMFY_PORT}" &
COMFY_PID=$!

# Wait for ComfyUI to respond
echo "[start.sh] Waiting for ComfyUI to become ready..."
for i in $(seq 1 240); do
  if curl -fsS "${COMFY_HTTP}/system_stats" >/dev/null 2>&1; then
    echo "[start.sh] ComfyUI is ready."
    break
  fi
  sleep 1
done

# If still not ready, fail fast so RunPod shows useful logs
if ! curl -fsS "${COMFY_HTTP}/system_stats" >/dev/null 2>&1; then
  echo "[start.sh] ERROR: ComfyUI did not become ready in time."
  echo "[start.sh] ComfyUI process is still running? (pid=${COMFY_PID})"
  exit 1
fi

# Start handler (foreground)
echo "[start.sh] Starting RunPod handler..."
exec python -u /handler.py
