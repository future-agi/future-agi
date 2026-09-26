# Future AGI frontend (futureagi/frontend): the React SPA served by nginx.
# Conventions shared by every futureagi/* image: docs/images.md.

# Base images, pinned by digest (multi-arch indexes) so a rebuild of the same
# source gives the same layers. Bump both the tag and the digest together.
#   node:22.18.0 -> build and dev stages only, never shipped
#   nginx:1.31.6-alpine-slim -> the served image: 5.8 MB gzip, vs 26.3 MB for
#     nginx:alpine (amd64, Docker Hub, 2026-09-25). nginx.conf uses only core
#     http directives (gzip, expires, add_header, try_files) and
#     docker-entrypoint.sh below needs only busybox sh/printenv/sed, so none of
#     the modules alpine-slim leaves out (njs, geoip, image-filter, xslt, acme)
#     nor nginx:alpine's curl are used.
ARG NODE_IMAGE=node:22.18.0@sha256:3266bc9e8bee1acc8a77386eefaf574987d2729b8c5ec35b0dbd6ddbc40b0ce2
ARG NGINX_IMAGE=nginx:1.31.6-alpine-slim@sha256:f761b94f2cb9e8e05e2943d5f773609596113ef69b54e2433a996d109a8f78b7

# Stage 1: Build the React app.
# dist/ is architecture-independent, so build it once on the build host
# ($BUILDPLATFORM) instead of running Vite under QEMU for linux/arm64.
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS build

# Build args forwarded from docker-compose. Vite reads env vars prefixed
# VITE_ at build time and bakes them into the bundle.
ARG VITE_HOST_API=http://localhost:8000
ARG VITE_ENVIRONMENT=production
ENV VITE_HOST_API=${VITE_HOST_API} \
    VITE_ENVIRONMENT=${VITE_ENVIRONMENT}

WORKDIR /app

COPY package.json yarn.lock ./
RUN yarn install --network-timeout 600000

COPY . ./

# public/ directories nothing in the app references (checked with `git grep`
# over src/, index.html, vite.config.js and public/*.js(on), by directory and
# by file name). Vite copies public/ verbatim into dist/, so they would
# otherwise ship: 32.0 MB of images, 31.2 MB of the 62.7 MB gzip dist layer.
#   assets/readme          16.9 MB  README.md images (self-host.gif 15.3 MB)
#   assets/login_signup     7.0 MB
#   assets/images/home      7.2 MB, about 0.5, contact 0.2, faqs 0.1
#   assets/background       0.1 MB
# The files stay in git (README.md links to assets/readme). The build fails if
# the bundle ever references one of these paths; pass an empty value to keep
# them all: --build-arg PRUNE_PUBLIC_ASSETS=
ARG PRUNE_PUBLIC_ASSETS="assets/readme assets/login_signup assets/images/home assets/images/about assets/images/contact assets/images/faqs assets/background"

# vite.config.js already sets build.sourcemap=false; deleting *.map is a guard
# so a config change cannot ship source maps.
RUN yarn build \
    && find dist -name '*.map' -delete \
    && for dir in $PRUNE_PUBLIC_ASSETS; do rm -rf "dist/$dir"; done \
    && if [ -n "$PRUNE_PUBLIC_ASSETS" ]; then \
        pruned="$(printf '%s|' $PRUNE_PUBLIC_ASSETS)"; \
        if grep -rlE "(${pruned%|})/" dist --include='*.html' --include='*.js' --include='*.css' --include='*.json'; then \
            echo "dist/ references a pruned public/ directory above; drop it from PRUNE_PUBLIC_ASSETS" >&2; \
            exit 1; \
        fi; \
    fi

# Stage 1b (dev only): live Vite dev server for hot reload. Selected via
# `target: dev` in docker-compose.dev.yml. Source is bind-mounted at runtime;
# this stage just provides node + a baked-in node_modules so HMR works without
# a host-side `yarn install`.
FROM ${NODE_IMAGE} AS dev
WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install
EXPOSE 3000
CMD ["yarn", "dev", "--host", "--port", "3000"]

# Stage 2: Serve the React app with Nginx
FROM ${NGINX_IMAGE}

COPY --from=build /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --chmod=755 docker-entrypoint.sh /docker-entrypoint.sh

EXPOSE 80
EXPOSE 443

# The nginx master runs as root to bind :80 (docker-compose.distributed.yml
# publishes 3000:80) and to write config.js at start; its workers, which
# serve every request, run as the unprivileged `nginx` user (nginx.conf
# `user nginx;`). docs/images.md lists this exception.
# hadolint ignore=DL3002
USER root
# nginx: SIGQUIT finishes the requests in flight, SIGTERM drops them.
STOPSIGNAL SIGQUIT
# busybox wget; -Y off ignores any HTTP(S)_PROXY in the environment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "-Y", "off", "-T", "5", "-O", "/dev/null", "http://127.0.0.1/"]
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

# Image metadata (docs/images.md). Declared last: a new version, commit or
# build time changes the image config only, never a cached layer.
ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED=""
LABEL org.opencontainers.image.title="Future AGI frontend" \
      org.opencontainers.image.description="Future AGI web UI: the React single-page app served by nginx on :80." \
      org.opencontainers.image.source="https://github.com/future-agi/future-agi" \
      org.opencontainers.image.url="https://futureagi.com" \
      org.opencontainers.image.documentation="https://github.com/future-agi/future-agi/blob/main/docs/images.md" \
      org.opencontainers.image.vendor="Future AGI" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"
