# Build argument for base image selection
ARG BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

FROM ${BASE_IMAGE}

ARG COMFYUI_VERSION=latest
ARG CUDA_VERSION_FOR_COMFY
ARG ENABLE_PYTORCH_UPGRADE=false
ARG PYTORCH_INDEX_URL

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_PREFER_BINARY=1
ENV PYTHONUNBUFFERED=1
ENV CMAKE_BUILD_PARALLEL_LEVEL=8
ENV PIP_NO_INPUT=1

# Install Python, git and tools ComfyUI needs
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3-pip \
    git \
    wget \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    && apt-get autoremove -y \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

# Install uv and create isolated venv
RUN wget -qO- https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"

# Install comfy-cli + deps
RUN uv pip install comfy-cli setuptools wheel

# Install ComfyUI into /comfyui
RUN if [ -n "${CUDA_VERSION_FOR_COMFY}" ]; then \
      /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --cuda-version "${CUDA_VERSION_FOR_COMFY}" --nvidia; \
    else \
      /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --nvidia; \
    fi

# Optional: upgrade torch if you explicitly set ENABLE_PYTORCH_UPGRADE=true
RUN if [ "$ENABLE_PYTORCH_UPGRADE" = "true" ]; then \
      uv pip install --force-reinstall torch torchvision torchaudio --index-url ${PYTORCH_INDEX_URL}; \
    fi

# Runtime python deps for handler
RUN uv pip install runpod requests websocket-client pillow

# ComfyUI working dir
WORKDIR /comfyui

# Network volume model paths (ComfyUI reads this)
COPY src/extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# Copy workflows into the image so tests can run even without an external volume
# (If you already have workflows/ in the repo root, this will include them.)
COPY workflows /workflows

# App code
WORKDIR /
COPY src/start.sh /start.sh
COPY src/handler.py /handler.py
COPY test_input.json /test_input.json
RUN chmod +x /start.sh

# Optional scripts (safe to keep)
COPY scripts/comfy-node-install.sh /usr/local/bin/comfy-node-install
RUN chmod +x /usr/local/bin/comfy-node-install
COPY scripts/comfy-manager-set-mode.sh /usr/local/bin/comfy-manager-set-mode
RUN chmod +x /usr/local/bin/comfy-manager-set-mode

# These defaults remove path ambiguity
ENV COMFYUI_DIR=/comfyui
ENV COMFY_HOST=127.0.0.1
ENV COMFY_PORT=8188
ENV WORKFLOW_PATH=/workflows/wan_i2v_LOCKED.json

# IMPORTANT: start ComfyUI first, then the handler
ENTRYPOINT ["/start.sh"]
