#!/usr/bin/env bash
set -e

# Flux Kontext + InfiniteYou Model Provisioning Script
# Downloads required models to Network Volume if not present
# InfiniteYou auto-downloads its own models from ByteDance/InfiniteYou
# Total: ~20GB

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/download_utils.sh"

VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}"
MODELS_DIR="${VOLUME_PATH}/models"
LOG_PREFIX="provision-kontext"

log() {
    echo "[${LOG_PREFIX}] $(date '+%Y-%m-%d %H:%M:%S') $1"
}

download_insightface() {
    local dest_dir="${MODELS_DIR}/insightface/models/antelopev2"

    if [ -d "$dest_dir" ] && [ "$(ls -A "$dest_dir" 2>/dev/null | wc -l)" -ge 5 ]; then
        log "InsightFace antelopev2 models already exist"
        return 0
    fi

    log "Downloading InsightFace antelopev2 models..."
    mkdir -p "$dest_dir"

    local zip_url="https://huggingface.co/MonsterMMORPG/tools/resolve/main/antelopev2.zip"
    local temp_zip="/tmp/antelopev2.zip"

    wget --progress=dot:giga -O "$temp_zip" "$zip_url"
    unzip -o "$temp_zip" -d "${MODELS_DIR}/insightface/models/"
    rm -f "$temp_zip"

    log "InsightFace models extracted"
}

main() {
    log "Starting Flux Kontext model provisioning"
    log "Volume path: $VOLUME_PATH"
    log "Models directory: $MODELS_DIR"

    # Check if volume is mounted
    if [ ! -d "$VOLUME_PATH" ]; then
        log "ERROR: Volume not mounted at $VOLUME_PATH"
        log "Skipping provisioning - models must be on network volume"
        exit 0
    fi

    # Cleanup: Remove deprecated PuLID models (replaced by InfiniteYou)
    if [ -d "${MODELS_DIR}/pulid" ]; then
        log "Removing deprecated PuLID models..."
        rm -rf "${MODELS_DIR}/pulid"
    fi
    if [ -f "${MODELS_DIR}/clip/EVA02_CLIP_L_336_psz14_s6B.pt" ]; then
        log "Removing deprecated EVA-CLIP model..."
        rm -f "${MODELS_DIR}/clip/EVA02_CLIP_L_336_psz14_s6B.pt"
    fi

    # Create directories
    mkdir -p "${MODELS_DIR}/diffusion_models"
    mkdir -p "${MODELS_DIR}/clip"
    mkdir -p "${MODELS_DIR}/insightface/models"
    mkdir -p "${MODELS_DIR}/infinite_you"

    # Flux Kontext diffusion model (~12GB)
    download_model \
        "https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors" \
        "${MODELS_DIR}/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors" \
        "Flux Kontext FP8 (~12GB)" \
        "$LOG_PREFIX"

    # CLIP-L text encoder
    download_model \
        "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors" \
        "${MODELS_DIR}/clip/clip_l.safetensors" \
        "CLIP-L text encoder" \
        "$LOG_PREFIX"

    # T5-XXL FP8 text encoder (~5GB)
    download_model \
        "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors" \
        "${MODELS_DIR}/clip/t5xxl_fp8_e4m3fn.safetensors" \
        "T5-XXL FP8 (~5GB)" \
        "$LOG_PREFIX"

    # VAE - ae.safetensors (shared with other Flux models)
    # Only download if not present (may already exist from Z-Image provisioning)
    if [ ! -f "${MODELS_DIR}/vae/ae.safetensors" ]; then
        download_model \
            "https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors" \
            "${MODELS_DIR}/vae/ae.safetensors" \
            "FLUX VAE (~300MB)" \
            "$LOG_PREFIX"
    else
        log "FLUX VAE already exists (shared with other models)"
    fi

    # InsightFace antelopev2 models (for face detection - used by InfiniteYou)
    download_insightface

    # Note: InfiniteYou models (InfuseNet, image_proj) auto-download from
    # ByteDance/InfiniteYou on first use to models/infinite_you/

    log "Provisioning complete"

    # List downloaded models
    log "Kontext models in volume:"
    find "$MODELS_DIR" -type f \( -name "*kontext*" -o -name "clip_l*" -o -name "t5xxl*" \) -exec ls -lh {} \; 2>/dev/null || true
}

main "$@"
