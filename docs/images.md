# Container images

Every Future AGI image is published to Docker Hub under `futureagi/`, built
from this repository by `.github/workflows/release-images.yml`, and follows
the same conventions: one tag scheme, the same OCI labels, a digest-pinned
base, a `HEALTHCHECK` that probes the service's real health endpoint, and a
documented user.

- [Images at a glance](#images-at-a-glance)
- [Backend variants](#backend-variants)
- [Tags](#tags)
- [Labels](#labels)
- [Verifying an image](#verifying-an-image)
- [Health checks](#health-checks)
- [Users](#users)
- [Stopping](#stopping)
- [Base images](#base-images)
- [Build arguments](#build-arguments)
- [EE and cloud builds](#ee-and-cloud-builds)
- [Building and linting locally](#building-and-linting-locally)

## Images at a glance

Setups: **Standalone** is the default install (`./bin/install`,
`docker-compose.yml`). **Distributed** runs one container per service
(`./bin/install --distributed`, `docker-compose.distributed.yml`). **Helm** is
Distributed on Kubernetes and uses the same images.

Size budget: the most a `docker pull` may download (compressed MB, 10^6
bytes, per architecture), from `scripts/image_size_budget.json`. A release
checks it on every architecture before it moves a tag, and pull requests
check it in `platform-ci.yml`.

| Image | What it runs | Setups | Ports | User | Health check | Architectures | Size budget |
|---|---|---|---|---|---|---|---|
| `futureagi/platform` | The Standalone app container: API with an embedded Temporal worker, Temporal dev server, fi-collector, agentcc-gateway, code-evals sandbox, UI, Redis and object storage under supervisord. Built on the `-slim` backend | Standalone | 3000 UI, 8000 API, 4317/4318 OTLP, 8080 gateway, 9005 object storage | root | the five URLs of the compose `app` healthcheck | amd64, arm64 | 549 |
| `futureagi/future-agi` | Backend, the default [variant](#backend-variants) (feature-complete): API (`SERVICE_TYPE=backend`), Temporal and Celery workers, bootstrap jobs. Base of the simulation runner and of the EE and cloud images | Distributed, Helm | 80 HTTP, 50051 gRPC, 5555 Flower | root (runs as `1000:1000` on request) | `GET /health/` on :80 for the API; roles with no listener are healthy while they run | amd64, arm64 | 890 |
| `futureagi/future-agi:*-slim` | The same backend, lean: the base of `platform` | Standalone (inside `platform`) | as above | as above | as above | amd64, arm64 | 376 |
| `futureagi/frontend` | The web UI: the React app served by nginx | Distributed, Helm (Standalone serves the same files from `platform`) | 80 | root master, `nginx` (101) workers | `GET /` on :80 | amd64, arm64 | 42 |
| `futureagi/fi-collector` | OTLP receiver that writes spans to ClickHouse; also `fi-property-catalog-consumer` and `fi-observed-catalog-backfill` | Distributed, Helm (binary bundled in `platform`) | 4317 gRPC, 4318 HTTP, 9464 admin | `nonroot` (65532) | `GET /healthz` on :9464, for the `fi-collector` command only | amd64, arm64 | 25 |
| `futureagi/agentcc-gateway` | The LLM gateway | Distributed, Helm (binary bundled in `platform`) | 8080 | 65532 | `GET /healthz` on :8080 | amd64, arm64 | 9 |
| `futureagi/serving` | Embedding and audio/image model server | Standalone (`COMPOSE_PROFILES=ml`), Distributed, Helm | 8080 | root (Helm runs it as `appuser`, 1000) | `GET /health` on :8080 | amd64, arm64 | 520 amd64, 480 arm64 |
| `futureagi/serving:*-gpu` | The same with CUDA 12.4 torch | any, on an NVIDIA host | 8080 | root (Helm: 1000) | `GET /health` on :8080 | amd64 | 3800 |
| `futureagi/code-executor` | nsjail sandbox for untrusted code evals | Standalone (`COMPOSE_PROFILES=sandbox`), Distributed, Helm; needs `privileged` | 8060 | root | `GET /health` on :8060 | amd64, arm64 | 225 |
| `futureagi/future-agi-simulation-runner` | Temporal worker for the `simulation_runner` queue: the default backend variant plus the Agent Learning Kit SDK | Distributed, Helm | none | root (from the backend) | inherited from the backend: healthy while it runs | amd64 | 1350, reported only |
| `futureagi/code-executor-base` | Build input of `code-executor` (nsjail, Node.js, sandbox libraries); never run on its own | none | | | | amd64, arm64 | counted in `code-executor` |

A fresh Standalone install downloads `platform`, `postgres:16` and
`clickhouse/clickhouse-server:25.3-alpine`: 884 MB at most (measured 803).

## Backend variants

`futureagi/Dockerfile.oss` builds the backend in two variants, chosen by one
build argument, `IMAGE_VARIANT`:

| | Default (`IMAGE_VARIANT=standard`) | Slim (`IMAGE_VARIANT=slim`) |
|---|---|---|
| Published as | `futureagi/future-agi:vX.Y.Z` (and `vX.Y`, `latest`) | `futureagi/future-agi:vX.Y.Z-slim` (and `vX.Y-slim`, `latest-slim`) |
| Used by | Distributed, Helm, the simulation runner, and the EE and cloud images; `./bin/install --distributed --from-source` and `./bin/e2e` build it | the base of `futureagi/platform`, so Standalone; `./bin/install --from-source` and `./bin/dev` build it for Standalone |
| Contents | what earlier releases shipped: the `sandbox` (Daytona, E2B), `billing`, `ops` (Flower), `gcp` (Vertex AI SDK), `langchain` and `rabbitmq` dependency groups, `uv`, git, Debian's ffmpeg, every NLTK package and untrimmed site-packages. Only development tools (type stubs, the debug toolbar) moved to the `dev` group | base dependencies only, no `uv` or git, a minimal LGPL ffmpeg build, the English NLTK data the app loads, and trimmed site-packages |
| Size | about the size of earlier releases | budget 376 MB compressed (measured 341) |

Both run the same code and dependency versions (from `uv.lock`). What the
slim variant leaves out shows up in a Standalone install as: hosted agent
runs on Daytona or E2B answer `501 sandbox_sdk_missing`, and from a GitHub
source `501 git_unavailable`; the Vertex AI partner models (Model Garden,
Gemma, Claude, Llama and Mistral on Vertex) are hidden from the model picker;
`SERVICE_TYPE=flower` and the RabbitMQ channel layer are unavailable; an audio
upload in an exotic codec (AV1, WavPack, ProRes) fails with "Decoder not
found".

`IMAGE_VARIANT` only sets the defaults of the per-feature build arguments
([Build arguments](#build-arguments)); one passed explicitly wins. A
Standalone install that needs any of the above can build its app image from
the default variant:

```bash
docker build -t futureagi/platform:local \
  --build-arg BACKEND_IMAGE=futureagi/future-agi:vX.Y.Z deploy/platform
```

or from a slim build with just the pieces it needs, e.g. `docker build -f
futureagi/Dockerfile.oss --build-arg IMAGE_VARIANT=slim --build-arg
EXTRAS=sandbox --build-arg WITH_GIT=true -t futureagi/future-agi:local
futureagi` and `--build-arg BACKEND_IMAGE=futureagi/future-agi:local`. Then set
`FUTURE_AGI_VERSION=local` in `.env`.

## Tags

| Tag | Points at | Use it for |
|---|---|---|
| `vX.Y.Z` | one release | production; pin it, or its digest |
| `vX.Y` | the newest patch release of that minor version | automatic patch upgrades |
| `latest` | the newest release | trying things out; the installers' default |
| `vX.Y.Z-slim`, `vX.Y-slim`, `latest-slim` | the slim [variant](#backend-variants) of `futureagi/future-agi`, same scheme | building your own Standalone app image; `-slim` exists for `future-agi` only |
| `vX.Y.Z-gpu`, `vX.Y-gpu`, `latest-gpu` | the CUDA build of `futureagi/serving`, same scheme | NVIDIA hosts; `-gpu` exists for `serving` only |

- A release builds every architecture, checks it, and only then points all
  three tags of an image at one multi-architecture manifest
  (`build-image-multiarch.yml`), so a tag never names a half-published image.
- A version with a suffix (`v1.43.0-rc1`, ad-hoc builds from the per-image
  `release-*.yml` workflows) gets that one tag; `vX.Y` and `latest` do not move.
- `futureagi/code-executor-base` takes exactly one immutable tag per version
  (`base-image-publish.yml` refuses to overwrite one); `code-executor` pins it.
- Tags that never reach Docker Hub: `:local` (`./bin/install --from-source`,
  `./bin/dev`), `:e2e-local` (`./bin/e2e`), `:ci` (CI's local registry).

The installers choose the tag with `FUTURE_AGI_VERSION` in `.env` (empty means
`latest`); Distributed also reads `FRONTEND_VERSION`, `AGENTCC_GATEWAY_VERSION`,
`FI_COLLECTOR_VERSION`, `SERVING_VERSION` and `CODE_EXECUTOR_VERSION`.

## Labels

Every image carries the [OCI annotation](https://github.com/opencontainers/image-spec/blob/main/annotations.md)
labels below. `version`, `revision` and `created` come from the `VERSION`,
`REVISION` and `CREATED` build arguments, which the release workflows pass
and each Dockerfile declares as its last instructions, so a new value changes
only the image configuration and never invalidates a cached layer. A local
build without them says `dev` and `unknown`.

| Label | Value |
|---|---|
| `org.opencontainers.image.title` | e.g. `Future AGI backend` |
| `org.opencontainers.image.description` | what the image runs |
| `org.opencontainers.image.source` | `https://github.com/future-agi/future-agi` |
| `org.opencontainers.image.url` | `https://futureagi.com` |
| `org.opencontainers.image.documentation` | this page |
| `org.opencontainers.image.vendor` | `Future AGI` |
| `org.opencontainers.image.licenses` | `Apache-2.0`; `Apache-2.0 AND LicenseRef-FutureAGI-Enterprise-1.0` for `future-agi`, `platform` and the simulation runner, which ship `futureagi/ee/` (see `LICENSE-EE`) |
| `org.opencontainers.image.version` | the release, e.g. `v1.43.0` (the `-slim` and `-gpu` variants have the same version) |
| `org.opencontainers.image.revision` | the 40-character commit SHA the image was built from |
| `org.opencontainers.image.created` | build time, UTC, RFC 3339 |

Extra labels: `ai.futureagi.<component>.image` on `platform` (the backend,
frontend, fi-collector, agentcc-gateway, Temporal and MinIO images it was
assembled from, with digests on a release), `ai.futureagi.sdk.version` and
`ai.futureagi.backend.image` on the simulation runner, and
`ai.futureagi.torch-backend` (`cpu` or `cu124`) on `serving`.

Read them without pulling the image:

```bash
docker buildx imagetools inspect futureagi/platform:latest --format '{{json .Image}}' \
  | jq '."linux/amd64".config.Labels'
```

or from a pulled image:

```bash
docker image inspect futureagi/platform:latest --format '{{json .Config.Labels}}' | jq
```

## Verifying an image

1. **Pin the digest.** `docker buildx imagetools inspect futureagi/platform:v1.43.0`
   prints the manifest digest. To make Compose pull exactly those bytes, name
   it in a `docker-compose.override.yml` next to `docker-compose.yml`:

   ```yaml
   services:
     app:
       image: futureagi/platform:v1.43.0@sha256:<digest>
   ```

   Every release run lists, in its summary, the digest of each
   multi-architecture image it published.
2. **Trace it to the source.** The `org.opencontainers.image.revision` label
   is the commit: `https://github.com/future-agi/future-agi/commit/<revision>`.
   Before it moves any tag, the release checks that each multi-architecture
   image carries all the labels and the version and commit it was built from.
3. **Signatures: not yet.** The images are not signed today (no cosign or
   Docker Content Trust signatures are published), so steps 1 and 2 are the
   check. Signing is planned in the release workflow (keyless cosign from
   GitHub Actions); once it ships, this section will give the
   `cosign verify` command and the expected certificate identity.

## Health checks

Each long-running image declares a Docker `HEALTHCHECK`, which `docker ps`
shows and `docker compose up --wait` waits for. Kubernetes ignores it: point
the pod probes at the same endpoints.

| Image | Probe | Interval / timeout / start period / retries |
|---|---|---|
| `platform` | GET `:8000/health/`, `:9464/healthz`, `:8080/healthz`, `:3000/`, `:9005/minio/health/live` (the compose `app` healthcheck) | 15 s / 10 s / 900 s / 5 |
| `future-agi` | `docker/healthcheck.py`: GET `:80/health/` when `SERVICE_TYPE=backend` serves HTTP, else TCP to gRPC :50051; `flower` TCP :5555; workers, beat, bootstrap jobs and containers started with their own command report healthy | 30 s / 10 s / 600 s / 3 |
| `frontend` | GET `:80/` (busybox wget) | 30 s / 5 s / 10 s / 3 |
| `fi-collector` | GET `:9464/healthz`, only when PID 1 is `fi-collector` (the consumer and backfill commands of the image serve no admin port) | 30 s / 10 s / 30 s / 3 |
| `agentcc-gateway` | GET `:8080/healthz` | 30 s / 10 s / 15 s / 3 |
| `serving` | GET `:8080/health`; models load on first use, not at start | 30 s / 10 s / 120 s / 5 |
| `code-executor` | GET `:8060/health` | 30 s / 10 s / 20 s / 3 |
| simulation runner | inherited from `future-agi` | |

- The backend's `/health/` answers 503 while an embedded Temporal worker is
  down (`tfc/asgi.py`), so the probe covers the worker too. The probe sends the
  first non-wildcard `ALLOWED_HOSTS` entry as its `Host` header, so tightening
  `ALLOWED_HOSTS` does not fail it.
- `fi-collector` and `agentcc-gateway` have no shell, so they ship a 1.8 MB
  static probe, `/usr/local/bin/healthcheck` (its source is in both
  Dockerfiles).
- `HEALTHCHECK_URL` (a URL, or for the backend a comma-separated list) moves
  the probe of `future-agi`, `fi-collector` and `agentcc-gateway`, for example
  when the gateway listens on another port. Set it in that service's
  `environment:`, not in `.env`, which several services read.
- No probe goes through `HTTP_PROXY`/`HTTPS_PROXY`.

Kubernetes probe targets: `future-agi` `GET /health/` port 80, `frontend`
`GET /` port 80, `fi-collector` `GET /healthz` port 9464, `agentcc-gateway`
`GET /healthz` port 8080, `serving` `GET /health` port 8080, `code-executor`
`GET /health` port 8060.

## Users

| Image | User | Why |
|---|---|---|
| `fi-collector` | `nonroot` (65532) | |
| `agentcc-gateway` | 65532 | |
| `serving` | root | an exception: existing deployments mount a root-owned model-cache volume (`HF_HOME`) without an `fsGroup`, which uid 1000 could not write. The image is ready for `appuser` (1000), and the Helm chart runs it that way with `fsGroup: 1000` and the caches on its `/models` volume. `NUMBA_CACHE_DIR=/tmp/numba-cache`, so an arbitrary Kubernetes `runAsUser` also starts. Model downloads go to `$HOME/.cache` unless `HF_HOME`, `SENTENCE_TRANSFORMERS_HOME` and `TORCH_HOME` say otherwise: mount a volume at `/root/.cache` (or `/home/appuser/.cache` with `user: "1000:1000"`) to keep them |
| `platform` | root | supervisord starts Redis, MinIO, nginx and the API, runs code evals as the unprivileged `sandbox` user (uid 18060), and keeps secrets in a root-only directory |
| `code-executor` | root | nsjail needs root and `privileged: true` to create each jail's namespaces and cgroups; evaluated code runs inside the jail as uid 1000 |
| `frontend` | root master | the nginx master binds :80 and writes `config.js` at start; the workers that serve requests run as `nginx` (101), as in the upstream nginx image |
| `future-agi` (both variants), simulation runner | root | the deployments that run this image today bind :80 and mount root-owned volumes (`/app/backend/logs`). The image is ready to run as `1000:1000`: see below |

Running the backend as a non-root user: `/app/backend`, its `logs`, `media`
and `static` directories, and the directories the app writes under
`/app/backend/tfc` (`logs`, which the settings create on import, `metadata`
and `saml_logs` for SAML, `compare` for dataset comparisons) belong to
`appuser` (1000), so

- Compose: add `user: "1000:1000"` to the backend services
  (`docker-compose.distributed.yml`). Docker 20.10 and later let any user
  bind :80 inside a container.
- Kubernetes: `runAsUser: 1000`, `runAsGroup: 1000`, `fsGroup: 1000` (volumes
  such as a logs volume become writable), and the safe sysctl
  `net.ipv4.ip_unprivileged_port_start=0` for port 80.

Files you mount into a non-root image (the gateway's config and Google
credentials, for example) must be readable by its user: mode 0644, or a
Kubernetes Secret or ConfigMap with its default mode. File sinks you
configure (a `disk` cache or file audit log in the gateway) need a mounted
directory that user can write.

## Stopping

Every image stops on `SIGTERM`, except `frontend` (`SIGQUIT`: nginx finishes
the requests in flight). Give the containers that drain work time to do it:
`platform` 60 s (the API drains its Temporal worker for up to 50 s), the
simulation runner more than `TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT` (compose sets
330 s), Temporal workers `TEMPORAL_GRACEFUL_SHUTDOWN_TIMEOUT` plus a margin.

## Base images

Every base is set by an `ARG` near the top of its Dockerfile, pinned by
digest, so a rebuild of the same commit reuses the same layers and an upgrade
downloads only what changed.

| Base | Pinned as | Used by |
|---|---|---|
| `python:3.11-slim-bookworm` | digest | `future-agi` (so `platform` and the simulation runner), `serving`, `code-executor-base`: one download for all of them |
| `ghcr.io/astral-sh/uv:0.11.16` | digest | build stages of `future-agi` and `serving`; the default `future-agi` variant ships its `uv` and `uvx` in `/usr/local/bin` |
| `node:22.18.0` | digest | `frontend` build stage, never shipped |
| `nginx:1.31.6-alpine-slim` | digest | `frontend` |
| `gcr.io/distroless/static-debian12:nonroot` | digest | `fi-collector` |
| `scratch` | (empty) | `agentcc-gateway` |
| `temporalio/temporal:1.9.1`, `ghcr.io/coollabsio/minio` | digest | binaries copied into `platform` |

Deliberately not pinned by digest, each explained in its Dockerfile:

- `golang:1.24-alpine` and `golang:1.26-alpine` float on the minor version:
  build stages only, and Go patch releases carry standard-library security
  fixes that are compiled into the binaries.
- `platform`'s component images and the simulation runner's backend default
  to `:latest` for a quick local build; a release passes this release's
  images pinned by digest (the `-slim` backend to `platform`, the default one
  to the simulation runner).
- `code-executor` pins `futureagi/code-executor-base` by an immutable version
  tag; add the digest once that version is published.

`base-digest-check.yml` runs weekly and fails, listing the new digests, when a
pinned tag has moved (a Debian security update, for example). To pick one up,
replace the digest in every file it lists in one pull request.

## Build arguments

Standard, on every image:

| Argument | Default | Set by a release to |
|---|---|---|
| `VERSION` | `dev` | the release tag, `vX.Y.Z` |
| `REVISION` | `unknown` | the commit SHA |
| `CREATED` | empty | the build time, UTC |

Per image:

| Image | Argument | Default | Effect |
|---|---|---|---|
| `future-agi` | `IMAGE_VARIANT` | `standard` | `standard` or `slim` ([Backend variants](#backend-variants)); sets the default of each argument below that is left empty |
| | `EXTRAS` | `standard`: `sandbox,billing,ops,gcp,langchain,rabbitmq`; `slim`: none | optional dependency groups, comma-separated: `audio`, `ml`, `voice`, `pii`, `prompt-opt`, `vectordb`, `rabbitmq`, `gcp`, `sandbox`, `billing`, `ops`, `langchain` (pinned by `uv.lock`). A value replaces the variant's list (include its groups to keep them); `none` installs no group |
| | `FFMPEG_FLAVOR` | `standard`: `debian`; `slim`: `minimal` | `minimal` (LGPL build, ~9 MB), `debian` (Debian's ffmpeg, about +145 MB), `none` (audio upload and video thumbnails fail) |
| | `WITH_GIT` | `standard`: `true`; `slim`: `false` | git (+29 MB) for hosted-harness GitHub sources, which otherwise answer 501 |
| | `WITH_UV` | `standard`: `true`; `slim`: `false` | ships `uv` and `uvx` for images that install packages on top |
| | `SLIM_SITE_PACKAGES` | `standard`: `0`; `slim`: `1` | `1` trims site-packages (test suites, type stubs, the Google API discovery documents the app does not call) |
| | `STRIP_SO` | `standard`: `0`; `slim`: `1` | with `SLIM_SITE_PACKAGES=1`, `1` also strips debug sections of extension modules |
| | `NLTK_DATA_PROFILE` | `standard`: `full`; `slim`: `minimal` | `full` bakes every NLTK package (all languages, the legacy packages and the archives); `minimal` the English data the app loads |
| | `PYTHON_IMAGE`, `UV_IMAGE` | pinned | base images |
| `serving` | `TORCH_BACKEND` | `cpu` | `cu124` (or another CUDA index) builds the `-gpu` image |
| `frontend` | `VITE_HOST_API` | `http://localhost:8000` | API URL baked into the bundle (a container overrides it at start with `VITE_HOST_API`) |
| | `VITE_ENVIRONMENT` | `production` | |
| | `PRUNE_PUBLIC_ASSETS` | README and marketing images | set it empty to keep every `public/` file |
| `platform` | `BACKEND_IMAGE`, `FRONTEND_IMAGE`, `FI_COLLECTOR_IMAGE`, `AGENTCC_GATEWAY_IMAGE` | `futureagi/<image>:latest` | the component images it is assembled from |
| | `TEMPORAL_IMAGE`, `MINIO_IMAGE` | pinned | |
| simulation runner | `FI_VERSION` | required | Agent Learning Kit SDK version (PyPI) |
| | `BACKEND_IMAGE` | `futureagi/future-agi:latest` | the backend it extends; the build fails on one without the sandbox SDKs and git, such as a `-slim` tag |
| | `LIVEKIT_AGENTS_VERSION` | `1.5.17` | |
| `code-executor` | `CODE_EXECUTOR_BASE` | `futureagi/code-executor-base:v1.1.0` | its base; bump it whenever `Dockerfile.base` changes (`backend-ci.yml` enforces this) |

## EE and cloud builds

The Enterprise and cloud backend images build on the published default
variant as it is: the private `Dockerfile.ee` starts `FROM
futureagi/future-agi:<version>` and runs `uv pip install --system -r
ee/requirements.txt` (every dependency group except `dev`). That install needs
the variant's `uv`, and the images rely at run time on its git, Debian
ffmpeg, NLTK packages and Google API discovery documents. A `-slim` base has
none of them: the install fails with `uv: not found`. The default variant's
build checks all of these (`futureagi/docker/runtime_smoke.py`), the release
checks `uv`, git and the dependency groups again before it moves any tag
(`release-images.yml`), and `scripts/verify-image-contents.sh` checks them on
a published image.

An image built `FROM` another keeps the parent's labels until it sets its
own: an overlay (such as `Dockerfile.ee`) should declare `VERSION`,
`REVISION` and `CREATED` and set every `org.opencontainers.image.*` label,
with its own title and licenses. It also inherits the backend's user (root),
`HEALTHCHECK`, `STOPSIGNAL`, `ENTRYPOINT` (`bash /app/backend/entrypoint.sh`)
and `SERVICE_VERSION` (the release it was built from, which deployment
telemetry reports when `FUTURE_AGI_VERSION` names none).

## Building and linting locally

```bash
./bin/install --from-source          # slim backend, frontend, fi-collector, gateway, then platform, as :local
./bin/install --distributed --from-source   # default backend, frontend, fi-collector, gateway, as :local
./bin/dev                            # the same images, with hot reload

docker build -f futureagi/Dockerfile.oss -t futureagi/future-agi:local \
  --build-arg REVISION="$(git rev-parse HEAD)" futureagi    # add --build-arg IMAGE_VARIANT=slim for the slim variant
docker build -t futureagi/frontend:local frontend
docker build -t futureagi/fi-collector:local fi-collector
docker build -t futureagi/agentcc-gateway:local agentcc-gateway
docker build -f futureagi/model_serving/Dockerfile.oss -t futureagi/serving:local futureagi/model_serving
docker build -f futureagi/code-executor/Dockerfile.base -t futureagi/code-executor-base:v1.1.0 futureagi/code-executor
docker build -t futureagi/code-executor:local futureagi/code-executor
```

Lint the Dockerfiles with [hadolint](https://github.com/hadolint/hadolint)
(settings in `.hadolint.yaml`; findings a Dockerfile accepts on purpose are
ignored inline with the reason) and the workflows with
[actionlint](https://github.com/rhysd/actionlint):

```bash
hadolint futureagi/Dockerfile.oss deploy/platform/Dockerfile frontend/Dockerfile \
  fi-collector/Dockerfile agentcc-gateway/Dockerfile futureagi/model_serving/Dockerfile.oss \
  futureagi/code-executor/Dockerfile futureagi/code-executor/Dockerfile.base Dockerfile.simulation-runner
actionlint .github/workflows/*.yml
```

`deploy/tests/test_image_standards.py` checks these conventions on every
Dockerfile above.
