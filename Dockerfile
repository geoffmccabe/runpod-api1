# Build argument for base image selection
ARG BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

FROM ${BASE_IMAGE}

# Prevent prompts from packages asking for user input during installation
ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_PREFER_BINARY=1
ENV PYTHONUNBUFFERED=1
ENV CMAKE_BUILD_PARALLEL_LEVEL=8

# Install OS deps
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
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/pip3 /usr/bin/pip \
    && apt-get autoremove -y \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/*

# Install uv and create venv
RUN wget -qO- https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"

# Install comfy-cli and dependencies
RUN uv pip install comfy-cli pip setuptools wheel

# Install ComfyUI into /comfyui (this is the canonical location for this image)
RUN /usr/bin/yes | comfy --workspace /comfyui install --version "latest" --nvidia

# Install runtime deps for handler
RUN uv pip install runpod requests websocket-client pillow

# Copy workflow(s) into the image (so the worker always has them)
COPY workflows /workflows

# Copy app code
COPY src/start.sh /start.sh
COPY src/handler.py /handler.py
COPY src/network_volume.py /network_volume.py
COPY src/extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY test_input.json /test_input.json

RUN chmod +x /start.sh

# IMPORTANT: Run start.sh, not handler.py directly.
# start.sh boots ComfyUI first, then launches the handler.
ENTRYPOINT ["/bin/bash", "-lc", "/start.sh"]
