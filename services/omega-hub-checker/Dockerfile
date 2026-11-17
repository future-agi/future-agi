# The checker worker image, omega-hub-checker:<tag> (SPEC §4.4). It holds the pinned, vendored
# @omega/hub judge and a docker CLI, and nothing else: no database or storage credentials. In
# production it runs on its own node pool or VM with gVisor and blocked egress (owner decision 7);
# the runner image it starts is built separately by runner/build.sh.
FROM docker:27-cli AS dockercli

FROM node:22-bookworm-slim
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /checker
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY src ./src
COPY vendor ./vendor
# The entrypoint checks the runner daemon (DOCKER_HOST) before the worker starts: its default runtime
# is gVisor, and the runner image is on it (bin/start.sh; deploy/README.md, "Omega Hub").
COPY --chmod=755 bin/start.sh ./bin/start.sh
USER node
ENV NODE_ENV=production
ENTRYPOINT ["/checker/bin/start.sh"]
