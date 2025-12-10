#!/usr/bin/env bash
set -e

# ESRGAN Upscale Model Provisioning Script
# Downloads RealESRGAN_x4plus for 4x image upscaling
# Run at worker deploy/startup time

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/download_utils.sh"

VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}"
MODELS_DIR="${VOLUME_PATH}/models"
LOG_PREFIX="provision-upscale"

# Model URL from Comfy-Org/Real-ESRGAN_repackaged
ESRGAN_URL="https://huggingface.co/Comfy-Org/Real-ESRGAN_repackaged/resolve/main/RealESRGAN_x4plus.safetensors"

# Target path
ESRGAN_PATH="${MODELS_DIR}/upscale_models/RealESRGAN_x4plus.safetensors"

log() {
    echo "[${LOG_PREFIX}] $(date '+%Y-%m-%d %H:%M:%S') $1"
}

main() {
    log "Starting ESRGAN upscale model provisioning"
    log "Volume path: $VOLUME_PATH"
    log "Models directory: $MODELS_DIR"

    # Check if volume is mounted
    if [ ! -d "$VOLUME_PATH" ]; then
        log "ERROR: Volume not mounted at $VOLUME_PATH"
        log "Skipping provisioning - models must be on network volume"
        exit 0
    fi

    # Create model directory
    mkdir -p "${MODELS_DIR}/upscale_models"

    # Download model (~64MB)
    download_model "$ESRGAN_URL" "$ESRGAN_PATH" "RealESRGAN_x4plus (~64MB)" "$LOG_PREFIX"

    log "Provisioning complete"

    # List downloaded models
    log "Upscale models in volume:"
    find "${MODELS_DIR}/upscale_models" -type f -name "*.safetensors" -exec ls -lh {} \; 2>/dev/null || true
}

main "$@"
