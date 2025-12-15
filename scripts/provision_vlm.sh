#!/usr/bin/env bash
set -e

# VLM Directory Setup Script
# Creates directories for Qwen2.5-VL vision-language models
# VLM models are auto-downloaded by ComfyUI node on first use

VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}"
MODELS_DIR="${VOLUME_PATH}/models"
LOG_PREFIX="provision-vlm"

log() {
    echo "[${LOG_PREFIX}] $(date '+%Y-%m-%d %H:%M:%S') $1"
}

main() {
    log "Setting up VLM directories"
    log "Volume path: $VOLUME_PATH"

    # Check if volume is mounted
    if [ ! -d "$VOLUME_PATH" ]; then
        log "ERROR: Volume not mounted at $VOLUME_PATH"
        log "Skipping VLM setup - models must be on network volume"
        exit 0
    fi

    # Create VLM directory for Qwen2.5-VL models
    mkdir -p "${MODELS_DIR}/VLM"

    log "VLM setup complete"
    log "VLM models will be auto-downloaded on first captioning request"
}

main "$@"
