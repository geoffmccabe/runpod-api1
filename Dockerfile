# Build argument for base image selection
ARG BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

# Stage 1: Base image with common dependencies
FROM ${BASE_IMAGE} AS base

# IMPORTANT: do not default to "latest" for comfy-cli --version
ARG COMFYUI_VERSION=
ARG CUDA_VERSION_FOR_COMFY
ARG ENABLE_PYTORCH_UPGRADE=false
ARG PYTORCH_INDEX_URL

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_PREFER_BINARY=1
ENV PYTHONUNBUFFERED=1
ENV CMAKE_BUILD_PARALLEL_LEVEL=8

# Install Python, git and other necessary tools
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    git \
    wget \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    ffmpeg \
    && ln -sf /usr/bin/python3.12 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

RUN apt-get autoremove -y && apt-get clean -y && rm -rf /var/lib/apt/lists/*

# Install uv and create isolated venv
RUN wget -qO- https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv venv /opt/venv

# Use the virtual environment for all subsequent commands
ENV PATH="/opt/venv/bin:${PATH}"

# CRITICAL FIX: ensure pip exists inside the venv (comfy-cli requires it)
RUN /opt/venv/bin/python -m ensurepip --upgrade \
    && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel

# Install comfy-cli
RUN /opt/venv/bin/python -m pip install comfy-cli

# Install ComfyUI
RUN set -eux; \
    if [ -n "${CUDA_VERSION_FOR_COMFY:-}" ]; then \
      if [ -n "${COMFYUI_VERSION:-}" ]; then \
        /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --cuda-version "${CUDA_VERSION_FOR_COMFY}" --nvidia; \
      else \
        /usr/bin/yes | comfy --workspace /comfyui install --cuda-version "${CUDA_VERSION_FOR_COMFY}" --nvidia; \
      fi; \
    else \
      if [ -n "${COMFYUI_VERSION:-}" ]; then \
        /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --nvidia; \
      else \
        /usr/bin/yes | comfy --workspace /comfyui install --nvidia; \
      fi; \
    fi

# Upgrade PyTorch if needed (for newer CUDA versions)
RUN if [ "$ENABLE_PYTORCH_UPGRADE" = "true" ]; then \
      /opt/venv/bin/python -m pip install --force-reinstall torch torchvision torchaudio --index-url ${PYTORCH_INDEX_URL}; \
    fi

# Change working directory to ComfyUI
WORKDIR /comfyui

# Support for the network volume
ADD src/extra_model_paths.yaml ./

# Go back to the root
WORKDIR /

# Install Python runtime dependencies for the handler
RUN /opt/venv/bin/python -m pip install runpod requests websocket-client

# Add application code and scripts
ADD src/start.sh src/network_volume.py src/handler.py test_input.json ./
RUN chmod +x /start.sh

# Add script to install custom nodes
COPY scripts/comfy-node-install.sh /usr/local/bin/comfy-node-install
RUN chmod +x /usr/local/bin/comfy-node-install

ENV PIP_NO_INPUT=1

# Copy helper script to switch Manager network mode at container start
COPY scripts/comfy-manager-set-mode.sh /usr/local/bin/comfy-manager-set-mode
RUN chmod +x /usr/local/bin/comfy-manager-set-mode

# RunPod Serverless entrypoint
ENTRYPOINT ["python3", "-u", "/handler.py"]

# Stage 2: Download models (optional)
FROM base AS downloader

ARG HUGGINGFACE_ACCESS_TOKEN
ARG MODEL_TYPE=flux1-dev-fp8

WORKDIR /comfyui
RUN mkdir -p models/checkpoints models/vae models/unet models/clip

RUN if [ "$MODEL_TYPE" = "flux1-dev-fp8" ]; then \
      wget -q -O models/checkpoints/flux1-dev-fp8.safetensors https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors; \
    fi

# Stage 3: Final image
FROM base AS final
COPY --from=downloader /comfyui/models /comfyui/models
