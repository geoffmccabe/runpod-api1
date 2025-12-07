#!/usr/bin/env bash
set -e

# RMBG-2.0 Model Provisioning Script
# Downloads required models to Network Volume if not present

VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}"
MODELS_DIR="${VOLUME_PATH}/models/RMBG/RMBG-2.0"

# Model URL from briaai/RMBG-2.0
MODEL_URL="https://huggingface.co/briaai/RMBG-2.0/resolve/main/model.pth"
MODEL_PATH="${MODELS_DIR}/model.pth"

log() {
    echo "[provision-rmbg] $(date '+%Y-%m-%d %H:%M:%S') $1"
}

main() {
    log "Starting RMBG-2.0 model provisioning"

    if [ ! -d "$VOLUME_PATH" ]; then
        log "Volume not mounted at $VOLUME_PATH - skipping"
        exit 0
    fi

    mkdir -p "$MODELS_DIR"

    if [ -f "$MODEL_PATH" ]; then
        log "RMBG-2.0 model already exists"
        ls -lh "$MODEL_PATH"
        return 0
    fi

    log "Downloading RMBG-2.0 model (~175MB)..."
    wget --progress=dot:giga -O "$MODEL_PATH" "$MODEL_URL"

    log "Provisioning complete"
    ls -lh "$MODEL_PATH"
}

main "$@"
