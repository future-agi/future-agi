# syntax=docker/dockerfile:1.6

# Stage 1: Build the React app
FROM node:20.17 AS build

WORKDIR /app

# Install deps first, before any ARG/ENV that could change between builds.
# Putting build-args above this layer would invalidate `yarn install` every
# time a VITE_* value changes — those values aren't needed for the install,
# only for `yarn build` below.
COPY package.json yarn.lock ./
# BuildKit cache mount keeps yarn's package cache on the host between
# builds, so even if this layer's cache misses (e.g. after Dockerfile
# changes or CI eviction), yarn doesn't re-download from npm — it resolves
# from the local cache and finishes in seconds.
RUN --mount=type=cache,target=/usr/local/share/.cache/yarn,sharing=locked \
    yarn install --frozen-lockfile

COPY . ./

# Build args forwarded from docker-compose. Vite reads env vars prefixed
# VITE_ at build time and bakes them into the bundle. Keep this list in sync
# with the build.args block in docker-compose.yml / docker-compose.frontend.yml
# and the VITE_* keys in the root .env / .env.example.
ARG VITE_HOST_API=http://localhost:8000
ARG VITE_ASSETS_API=
ARG VITE_ENVIRONMENT=production
ARG VITE_MIXPANEL_TOKEN=
ARG VITE_HELP_LINK=
ARG VITE_GENERATE_LINK=
ARG VITE_GOOGLE_SITE_KEY=
ARG VITE_AG_GRID_LICENSE_KEY=
ARG VITE_STRIPE_PUBLIC_KEY=
ARG VITE_PENDO_API_KEY=
ARG VITE_SENTRY_DSN=
ENV VITE_HOST_API=${VITE_HOST_API} \
    VITE_ASSETS_API=${VITE_ASSETS_API} \
    VITE_ENVIRONMENT=${VITE_ENVIRONMENT} \
    VITE_MIXPANEL_TOKEN=${VITE_MIXPANEL_TOKEN} \
    VITE_HELP_LINK=${VITE_HELP_LINK} \
    VITE_GENERATE_LINK=${VITE_GENERATE_LINK} \
    VITE_GOOGLE_SITE_KEY=${VITE_GOOGLE_SITE_KEY} \
    VITE_AG_GRID_LICENSE_KEY=${VITE_AG_GRID_LICENSE_KEY} \
    VITE_STRIPE_PUBLIC_KEY=${VITE_STRIPE_PUBLIC_KEY} \
    VITE_PENDO_API_KEY=${VITE_PENDO_API_KEY} \
    VITE_SENTRY_DSN=${VITE_SENTRY_DSN}

RUN --mount=type=cache,target=/app/node_modules/.cache,sharing=locked \
    --mount=type=cache,target=/app/node_modules/.vite,sharing=locked \
    yarn build

# Stage 1b (dev only): live Vite dev server for hot reload. Selected via
# `target: dev` in docker-compose.dev.yml. Source is bind-mounted at runtime;
# this stage just provides node + a baked-in node_modules so HMR works without
# a host-side `yarn install`.
FROM node:20.17 AS dev
WORKDIR /app
COPY package.json yarn.lock ./
RUN yarn install
EXPOSE 3000
CMD ["yarn", "dev", "--host", "--port", "3000"]

# Stage 2: Serve the React app with Nginx
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
EXPOSE 443

CMD ["nginx", "-g", "daemon off;"]
