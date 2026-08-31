# Slim on purpose: the harness needs Python, Node (the Agent SDK drives the
# Claude Code CLI as a subprocess), a docker *client*, and six packages. It
# never imports Django, so the 17.8 GB backend image buys nothing here.
# 3.11 to match Dockerfile.base across the stack.
FROM python:3.11-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
 && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs docker-ce-cli git \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# artifacts/ resolves relative to the working directory; the volume mounts here.
WORKDIR /app

COPY pyproject.toml LICENSE NOTICE ./
COPY src ./src
COPY ui ./ui
RUN pip install --no-cache-dir -e .[ui,postgres]

# The Agent Learning Kit SDK, for voice only: a spoken run hands off to a case under voice/,
# and those import fi.simulate and fi.alk. Installed from a local wheel because the package is
# on no index -- build it first with ./scripts/build-alk-wheel.sh at the repository root.
# Pinned with the SDK's own lock: an unconstrained livekit-agents resolves 1.6.x, whose native
# local-inference module crashes in linux/amd64 containers under emulation.
ARG ALK_WHEEL=agent_learning_kit-0.1.0-py3-none-any.whl
ARG LIVEKIT_AGENTS_VERSION=1.5.17
COPY ${ALK_WHEEL} /tmp/alk/${ALK_WHEEL}
RUN pip install --no-cache-dir "/tmp/alk/${ALK_WHEEL}[livekit]" \
        "livekit-agents==${LIVEKIT_AGENTS_VERSION}" \
    && rm -rf /tmp/alk

COPY voice ./voice

ENV HARNESS_HOST=0.0.0.0
EXPOSE 8777 8787
CMD ["python", "ui/server.py"]
