# StoryMaker web page: Node for the page, Python + ffmpeg for storymaker.py.
# Build from the StoryMaker folder (one level up):  docker compose up -d --build
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg fonts-dejavu-core espeak-ng ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir requests pillow edge-tts kokoro-onnx soundfile

# Kokoro: smooth narrator and character voices that run inside this container (free, private)
ADD https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx /opt/kokoro/
ADD https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin /opt/kokoro/

WORKDIR /app
COPY web/package.json ./
RUN npm install --omit=dev && npm cache clean --force
COPY web/server.js ./
COPY web/public ./public
COPY storymaker.py ./storymaker.py

ENV PORT=3000 \
    PATH=/opt/venv/bin:$PATH \
    KOKORO_DIR=/opt/kokoro \
    PYTHON=/opt/venv/bin/python \
    STORYMAKER=/app/storymaker.py \
    OUTPUT_DIR=/data/output \
    MUSIC_DIR=/data/music \
    SOUNDS_DIR=/data/sounds \
    TTS_PROVIDER=auto

EXPOSE 3000
CMD ["node", "--experimental-sqlite", "--disable-warning=ExperimentalWarning", "server.js"]
