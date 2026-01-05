ARG BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04
FROM ${BASE_IMAGE}
 
ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_PREFER_BINARY=1
ENV PYTHONUNBUFFERED=1
ENV CMAKE_BUILD_PARALLEL_LEVEL=8
ENV PIP_NO_INPUT=1

# OS deps (ADD BUILD TOOLS)
RUN apt-get update && apt-get install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    build-essential \
    cmake \
    ninja-build \
    pkg-config \
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

# uv + venv
RUN wget -qO- https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx \
    && uv venv /opt/venv

ENV PATH="/opt/venv/bin:${PATH}"

# comfy-cli + ComfyUI
RUN uv pip install comfy-cli pip setuptools wheel
RUN /usr/bin/yes | comfy --workspace /comfyui install --version "latest" --nvidia

# handler runtime deps
RUN uv pip install runpod requests websocket-client pillow

# workflows + app code
COPY workflows /workflows
COPY src/start.sh /start.sh
COPY src/handler.py /handler.py
COPY src/network_volume.py /network_volume.py
COPY src/extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY test_input.json /test_input.json

RUN chmod +x /start.sh

ENTRYPOINT ["/bin/bash", "-lc", "/start.sh"]
