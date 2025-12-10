#!/usr/bin/env bash

# Use libtcmalloc for better memory management
TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1)"
export LD_PRELOAD="${TCMALLOC}"

# Set HuggingFace cache to network volume for persistent model downloads
# This ensures VLM models (Qwen2.5-VL) are cached on the network volume
if [ -d "${RUNPOD_VOLUME_PATH:-/runpod-volume}" ]; then
    export HF_HOME="${RUNPOD_VOLUME_PATH:-/runpod-volume}/huggingface"
    mkdir -p "$HF_HOME"
    echo "worker-comfyui: HuggingFace cache set to $HF_HOME"

    # Symlink VLM models directory to network volume
    # Qwen2.5-VL node downloads to /comfyui/models/VLM/ - redirect to network volume
    VLM_VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}/models/VLM"
    VLM_COMFYUI_PATH="/comfyui/models/VLM"
    mkdir -p "$VLM_VOLUME_PATH"
    if [ ! -L "$VLM_COMFYUI_PATH" ]; then
        rm -rf "$VLM_COMFYUI_PATH"
        ln -sf "$VLM_VOLUME_PATH" "$VLM_COMFYUI_PATH"
        echo "worker-comfyui: VLM models symlinked to $VLM_VOLUME_PATH"
    fi

    # Symlink InsightFace models directory to network volume
    # InfiniteYou uses /comfyui/models/insightface/models/antelopev2/ for face detection
    INSIGHTFACE_VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}/models/insightface"
    INSIGHTFACE_COMFYUI_PATH="/comfyui/models/insightface"
    mkdir -p "$INSIGHTFACE_VOLUME_PATH"
    if [ ! -L "$INSIGHTFACE_COMFYUI_PATH" ]; then
        rm -rf "$INSIGHTFACE_COMFYUI_PATH"
        ln -sf "$INSIGHTFACE_VOLUME_PATH" "$INSIGHTFACE_COMFYUI_PATH"
        echo "worker-comfyui: InsightFace models symlinked to $INSIGHTFACE_VOLUME_PATH"
    fi

    # Symlink InfiniteYou models directory to network volume
    # Stores InfuseNet and image_proj models from ByteDance/InfiniteYou
    INFINITEYOU_VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}/models/infinite_you"
    INFINITEYOU_COMFYUI_PATH="/comfyui/models/infinite_you"
    mkdir -p "$INFINITEYOU_VOLUME_PATH"
    if [ ! -L "$INFINITEYOU_COMFYUI_PATH" ]; then
        rm -rf "$INFINITEYOU_COMFYUI_PATH"
        ln -sf "$INFINITEYOU_VOLUME_PATH" "$INFINITEYOU_COMFYUI_PATH"
        echo "worker-comfyui: InfiniteYou models symlinked to $INFINITEYOU_VOLUME_PATH"
    fi

    # Symlink upscale_models directory to network volume
    # ESRGAN and other upscale models for post-processing
    UPSCALE_VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}/models/upscale_models"
    UPSCALE_COMFYUI_PATH="/comfyui/models/upscale_models"
    mkdir -p "$UPSCALE_VOLUME_PATH"
    if [ ! -L "$UPSCALE_COMFYUI_PATH" ]; then
        rm -rf "$UPSCALE_COMFYUI_PATH"
        ln -sf "$UPSCALE_VOLUME_PATH" "$UPSCALE_COMFYUI_PATH"
        echo "worker-comfyui: Upscale models symlinked to $UPSCALE_VOLUME_PATH"
    fi
fi

# Ensure ComfyUI-Manager runs in offline network mode inside the container
comfy-manager-set-mode offline || echo "worker-comfyui - Could not set ComfyUI-Manager network_mode" >&2

# Provision Qwen-Image models if not present
if [ -f /scripts/provision_qwen.sh ]; then
    echo "worker-comfyui: Running Qwen-Image model provisioning"
    /scripts/provision_qwen.sh
fi

# Provision Z-Image models if not present
if [ -f /scripts/provision_z_image.sh ]; then
    echo "worker-comfyui: Running Z-Image model provisioning"
    /scripts/provision_z_image.sh
fi

# Provision Kontext + PuLID models if not present
if [ -f /scripts/provision_kontext_pulid.sh ]; then
    echo "worker-comfyui: Running Kontext + PuLID model provisioning"
    /scripts/provision_kontext_pulid.sh
fi

# Provision upscale models if not present
if [ -f /scripts/provision_upscale.sh ]; then
    echo "worker-comfyui: Running upscale model provisioning"
    /scripts/provision_upscale.sh
fi

echo "worker-comfyui: Starting ComfyUI"

# Allow operators to tweak verbosity; default is DEBUG.
: "${COMFY_LOG_LEVEL:=DEBUG}"

# Serve the API and don't shutdown the container
if [ "$SERVE_API_LOCALLY" == "true" ]; then
    python -u /comfyui/main.py --disable-auto-launch --disable-metadata --listen --verbose "${COMFY_LOG_LEVEL}" --log-stdout &

    echo "worker-comfyui: Starting RunPod Handler"
    python -u /handler.py --rp_serve_api --rp_api_host=0.0.0.0
else
    python -u /comfyui/main.py --disable-auto-launch --disable-metadata --verbose "${COMFY_LOG_LEVEL}" --log-stdout &

    echo "worker-comfyui: Starting RunPod Handler"
    python -u /handler.py
fi