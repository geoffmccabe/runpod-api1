#!/usr/bin/env bash
set -euo pipefail

echo "[start.sh] Booting container..."

# Defaults (can be overridden by env)
COMFYUI_DIR="${COMFYUI_DIR:-/comfyui/ComfyUI}"
COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
COMFY_PORT="${COMFY_PORT:-8188}"
COMFY_HTTP="http://${COMFY_HOST}:${COMFY_PORT}"
PY="/opt/venv/bin/python"

echo "[start.sh] COMFYUI_DIR=${COMFYUI_DIR}"
echo "[start.sh] COMFY_HTTP=${COMFY_HTTP}"
echo "[start.sh] PY=${PY}"

# Start ComfyUI in background
cd "${COMFYUI_DIR}"
echo "[start.sh] Starting ComfyUI..."
${PY} -u main.py --listen "${COMFY_HOST}" --port "${COMFY_PORT}" &
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

if ! curl -fsS "${COMFY_HTTP}/system_stats" >/dev/null 2>&1; then
  echo "[start.sh] ERROR: ComfyUI did not become ready in time."
  echo "[start.sh] ComfyUI PID=${COMFY_PID}"
  exit 1
fi

echo "[start.sh] Starting RunPod handler..."
exec ${PY} -u /handler.py
