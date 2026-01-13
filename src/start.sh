#!/usr/bin/env bash
set -euo pipefail

echo "[start.sh] Booting container..."

# Always use venv python
PYTHON="${PYTHON:-/opt/venv/bin/python}"

# Defaults (can be overridden by env)
COMFYUI_DIR="${COMFYUI_DIR:-/comfyui}"
COMFY_HOST="${COMFY_HOST:-0.0.0.0}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_HTTP="http://127.0.0.1:${COMFY_PORT}"

echo "[start.sh] PYTHON=${PYTHON}"
echo "[start.sh] COMFYUI_DIR=${COMFYUI_DIR}"
echo "[start.sh] COMFY_HOST=${COMFY_HOST}"
echo "[start.sh] COMFY_PORT=${COMFY_PORT}"
echo "[start.sh] COMFY_HTTP=${COMFY_HTTP}"

# Start ComfyUI in background
cd "${COMFYUI_DIR}"

echo "[start.sh] Starting ComfyUI..."
"${PYTHON}" -u main.py --listen "${COMFY_HOST}" --port "${COMFY_PORT}" &
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
  echo "[start.sh] ComfyUI PID=${COMFY_PID}"
  echo "[start.sh] Last 200 lines of ComfyUI log (if present):"
  tail -n 200 /tmp/comfyui.log 2>/dev/null || true
  exit 1
fi

# Start handler (foreground)
echo "[start.sh] Starting RunPod handler..."
exec "${PYTHON}" -u /handler.py
