# The Omega Hub runner: node 22 plus a built Omega workspace at one pinned commit (the CLI, the
# @omega/* packages and scripts/hub/preload.mjs). Built by build.sh from a `git archive` export of
# that commit, never from a working tree. It runs one entry's keyless test and demo with the network
# off; the checker starts it with --network none --read-only --cap-drop ALL plus the judge's
# CAP_SETUID and CAP_SETGID, --user 0:0 for the judge, entry code as uid 10001 (SPEC §4.4; below).
FROM node:22-bookworm-slim
ARG OMEGA_COMMIT=unknown
ARG WITH_CHROMIUM=1
WORKDIR /omega
RUN corepack enable
COPY . .
RUN pnpm install --frozen-lockfile \
 && pnpm --filter "@omega/cli..." build \
 && pnpm store prune \
 && rm -rf /root/.cache /root/.local/share/pnpm
# Playwright Chromium, from the workspace's own pinned playwright, at a path every user can read
# (entries that drive a browser, such as shop-browser-assistant, need it for their keyless test).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$WITH_CHROMIUM" = "1" ]; then \
      pnpm --filter @omega/browser-tools-provider exec playwright install --with-deps chromium \
      && chmod -R a+rX /ms-playwright && rm -rf /var/lib/apt/lists/* \
      && ln -s "$(find /ms-playwright -path '*chrome-linux*/chrome' -type f | head -1)" /usr/bin/chromium; \
    fi
# /usr/bin/chromium: entries that start Omega with a fresh HOME (local-web-gui) look for a system
# Chromium, as they find Google Chrome on CI runners, because Playwright's own cache is per HOME.
RUN printf '#!/bin/sh\nexec node /omega/packages/cli/dist/index.js "$@"\n' > /usr/local/bin/omega \
 && chmod 755 /usr/local/bin/omega \
 && echo "$OMEGA_COMMIT" > /omega/.omega-commit
# The judge (SPEC §13, "a submission tampers with its own judge"; READINESS 3.19). PID 1, the judge
# shell, and the node that runs `omega hub check` run as root with only CAP_SETUID and CAP_SETGID
# (src/sandbox.mjs: --user 0:0 --cap-drop ALL --cap-add SETUID --cap-add SETGID, no-new-privileges);
# the preload children.cjs makes every child of that node, which is all entry code, uid 10001 with no
# capabilities, so entry code cannot reach the judge's processes. probe.cjs is the preflight's probe
# (src/preflight.mjs). The judge's files are open source and hold no secret: root-owned, readable, on
# the read-only root. home/ is the judge node's HOME while Node computes its global module folders, so
# none of them is on a tmpfs entry code can write. No setuid or setgid binary is left in the image.
# The judge's files come from the checker's runner/judge folder, a named build context (build.sh).
RUN command -v setpriv >/dev/null \
 && find / -xdev -perm /6000 -type f -exec chmod ug-s {} + \
 && install -d -o root -g root -m 0755 /opt/omega-hub-judge \
 && install -d -o root -g root -m 0555 /opt/omega-hub-judge/home
COPY --from=judge --chown=0:0 --chmod=0644 children.cjs probe.cjs /opt/omega-hub-judge/
RUN [ -z "$(find / -xdev -perm /6000 -type f)" ] \
 && [ "$(stat -c %U:%a /opt/omega-hub-judge/children.cjs /opt/omega-hub-judge/probe.cjs /opt/omega-hub-judge/home | tr '\n' ' ')" = "root:644 root:644 root:555 " ]
LABEL org.futureagi.omega-hub.runner="1" org.futureagi.omega-hub.omega-commit="$OMEGA_COMMIT" org.futureagi.omega-hub.judge="2"
ENV HOME=/tmp OMEGA_HUB_DIR=/work/hub NODE_ENV=test
USER 10001:10001
WORKDIR /work
