# syntax=docker/dockerfile:1
# Minimal LiveKit Agents worker image for a FutureAGI RL-environment seed agent.
# Tool backends are mocked inline (see agent.py), so the worker runs standalone.
# LIVEKIT_* and provider API keys (OpenAI / Deepgram / Cartesia) are injected by
# the harness at runtime and are NEVER baked into the image.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY agent.py config.json README.md ./

# Pre-fetch model assets (e.g. Silero VAD weights) so the first dispatch is fast
# and does not depend on network egress mid-call. Tolerated if it needs no files.
RUN python agent.py download-files || true

# `start` registers the worker with LiveKit and waits for the harness to dispatch
# a call into the room. `dev` is the local hot-reload equivalent.
CMD ["python", "agent.py", "start"]
