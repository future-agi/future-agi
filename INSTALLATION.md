# Installation

How to run Future AGI on your own infrastructure: the deployment modes, what
each one needs, and how to fix what goes wrong on first boot.

If you just want to try it on your laptop, jump to [Quick start](#quick-start).

| Reference | What it covers |
| --- | --- |
| [Configuration reference](https://docs.futureagi.com/docs/self-hosting/configuration/reference) | Every environment variable: default, which setup reads it, what breaks when it is wrong |
| [Telemetry and outbound connections](https://docs.futureagi.com/docs/self-hosting/configuration/telemetry) | Exactly what an install sends to Future AGI, and every other outbound connection |
| [Container images](https://docs.futureagi.com/docs/self-hosting/images) | The container images: tags, sizes, users, health checks, verifying one |
| [deploy/helm/futureagi](deploy/helm/futureagi/README.md) | The Helm chart (Distributed on Kubernetes) |
| [deploy/README.md](deploy/README.md) | Production on Docker Compose |
| [Local development](https://docs.futureagi.com/docs/self-hosting/development) | Working on the code with hot reload |

---

## Contents

- [Quick start](#quick-start)
- [Prerequisites](#prerequisites)
  - [Download size](#download-size)
- [Deployment modes](#deployment-modes)
  - [Standalone (default)](#standalone-default)
  - [Distributed (at scale)](#distributed-at-scale)
  - [Helm (Distributed on Kubernetes)](#helm-distributed-on-kubernetes)
  - [Development (hot reload)](#development-hot-reload)
  - [Frontend only](#frontend-only)
  - [Installing a branch from source](#installing-a-branch-from-source)
  - [Switching between Standalone and Distributed](#switching-between-standalone-and-distributed)
- [Optional feature extras](#optional-feature-extras)
- [Configuration](#configuration)
  - [Secrets that must be changed](#secrets-that-must-be-changed)
  - [Ports reference](#ports-reference)
- [Telemetry and outbound connections](#telemetry-and-outbound-connections)
- [Services and what they do](#services-and-what-they-do)
- [Configuring LLM providers](#configuring-llm-providers)
- [Email](#email)
- [Upgrading](#upgrading)
- [Backups](#backups)
- [Troubleshooting](#troubleshooting)
- [Production hardening](#production-hardening)

---

## Quick start

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi
./bin/install          # Windows (PowerShell): .\bin\install.ps1
```

This installs the **Standalone** setup: three containers (the app, Postgres
and ClickHouse) from images published on Docker Hub, about 800 MB to download.
Give Docker 2 CPUs and 4 GB of memory (3 GB is the minimum). For one container
per service, run `./bin/install --distributed` instead; see
[Deployment modes](#deployment-modes). Choose before you add data: there is
no supported way to move a Standalone install's data to Distributed or Helm
later (see [Switching](#switching-between-standalone-and-distributed)). On a
branch other than `main`, add `--from-source`; see
[Installing a branch from source](#installing-a-branch-from-source).

`bin/install` does five things:

1. Copies `.env.example` to `.env` if missing, generates the secrets of a fresh
   install (see [Secrets](#secrets-that-must-be-changed)) and records the
   setup. Nothing else in `.env` is required for a local install.
2. Checks the host ports, the Docker VM's memory, CPUs and architecture, and
   that Docker can see the checkout.
3. Pulls the images, or builds them with `--from-source`.
4. Starts the stack with `docker compose up -d`.
5. Waits until the API (`http://localhost:8000/health/`) and, in Standalone,
   the UI (`http://localhost:3000`) answer, then creates the first account it
   asked you for. Before asking for that account's email, it shows what
   [deployment telemetry](#telemetry-and-outbound-connections) sends and how
   to opt out.

The first boot sets up the databases and takes a few minutes (on a slow or busy
host, its migrations alone can take over half an hour). In Standalone,
<http://localhost:3000> meanwhile shows a **Future AGI is starting** page with the
current phase, and the API answers `503` with
`{"status": "starting", "phase": "..."}`. `docker compose logs -f app` starts
with a summary of the configuration (setup, version, URLs, which integrations
are on, telemetry) and ends with `Future AGI is ready in ...`.

For production, do not rely on local-only defaults. See [Production hardening](#production-hardening).

Useful flags:

| Flag | What it does |
| --- | --- |
| `--distributed` | Install the Distributed setup (`docker-compose.distributed.yml`). Recorded in `.env`, so later runs keep it. Refused for a project that already holds a Standalone install. |
| `--from-source` | Build the images from this checkout instead of pulling them. See [Installing a branch from source](#installing-a-branch-from-source). |
| `--force` | Continue past a failed preflight check (Docker memory, path visibility, architecture). |
| `--skip-user-creation` | Skip the first-account prompt. Run the `create_user` command later. |
| `--no-up` | Write `.env` only; don't start the stack. |
| `--wipe-volumes` | Stop this project and delete its volumes, then install fresh. **Deletes data.** The setup stays the same unless you also pass `--distributed`. |
| `--new-instance` | Next to an existing install, start an isolated copy (`futureagi-2`, ...) instead. |
| `-y` | Non-interactive. Reads `FAGI_ADMIN_EMAIL`, `FAGI_ADMIN_NAME` and `FAGI_ADMIN_PASSWORD` for the first account. |
| `--no-telemetry` | Turn deployment telemetry off: writes `FUTURE_AGI_TELEMETRY_DISABLED=true` to `.env` before anything starts. See [Telemetry](#telemetry-and-outbound-connections). |

On Windows, `.\bin\install.ps1` takes the same options as `-Distributed`,
`-FromSource`, `-Force`, `-SkipUserCreation`, `-NoUp`, `-WipeVolumes`,
`-NonInteractive` and `-NoTelemetry`.

`bin/install` exits `1` when the stack came up but your first account could not
be created, so an unattended run fails loudly instead of pointing you at a login
you cannot use. Skipping account creation still exits `0`.

When the installer prints **Future AGI is up**, it lists the next steps and:

- **UI**: <http://localhost:3000>
- **API**: <http://localhost:8000>
- **Traces** (OTLP/HTTP): <http://localhost:4318>, this machine only (or
  `FI_COLLECTOR_PUBLIC_URL` when you set one)
- **LLM gateway**: <http://localhost:8090>

To open the UI from another device, see
[Opening the UI from another machine](#opening-the-ui-from-another-machine).

### Create your first account

If you skipped the prompt at install time, create the account from the command
line. The service is `app` in Standalone and `backend` in Distributed:

```bash
docker compose exec app python manage.py create_user       # Standalone
docker compose exec backend python manage.py create_user   # Distributed
```

You are asked for your email, full name and password. Then sign in at
<http://localhost:3000>. To pass them non-interactively, pipe the password in
instead of putting it on the command line, where every process in the
container (in Standalone, code evals too) can read it:

```bash
printf '%s\n' "$PASSWORD" | docker compose exec -T app python manage.py create_user \
  --email you@example.com \
  --name "Your Name"
```

On Helm: `kubectl -n futureagi exec -it deploy/futureagi-backend -c backend -- python manage.py create_user`.

### Reset a password

With [email](#email) configured, "Forgot password" on the sign-in page emails
a reset link. It answers the same whether or not the address has an account.

Without email, "Forgot password" answers with the command an administrator
runs to set a new password:

```bash
docker compose exec app python manage.py reset_password --email you@example.com       # Standalone
docker compose exec backend python manage.py reset_password --email you@example.com   # Distributed
kubectl -n futureagi exec -it deploy/futureagi-backend -c backend -- \
  python manage.py reset_password --email you@example.com                             # Helm
```

You are asked for the new password. To supply it non-interactively, pipe it
in as for `create_user`:

```bash
printf '%s\n' "$PASSWORD" | docker compose exec -T app python manage.py reset_password --email you@example.com
```

Any session already signed in as that account is signed out.

**In the browser**, as an alternative: set `OSS_RETURN_PASSWORD_RESET_LINK=true`
in `.env` and run `docker compose up -d`. "Forgot password" then returns the
reset link in its response, email or not, and takes the user straight to the
set-password screen. `./bin/install` warns while `.env` sets it, and
Standalone's start-up summary (`docker compose logs app`) marks it UNSAFE.

> Only turn this on where reaching the instance already implies full trust, such
> as a laptop install or a host behind a VPN with no other tenants. The endpoint
> takes no authentication, so anyone who can reach it can request a link for
> **any** address and take over that account. Leave it off on anything
> internet-facing and use the command above.

Team invites work without email too: the invite dialog returns a link for each
invitee for you to share. To have them emailed, see [Email](#email).

### Stop, start and remove

| To... | Run |
| --- | --- |
| Stop, keeping the data | `docker compose down` (or `./bin/uninstall`) |
| Start again | `docker compose up -d` |
| Delete all data | `./bin/uninstall --wipe-data` (or `docker compose down -v`) |
| Remove the install: containers, volumes, `.env` and the Future AGI images it uses | `./bin/uninstall --purge`. Images built by `--from-source` are shared by every checkout on the machine, so it asks about them separately, and `-y` keeps them. |

Data lives in named Docker volumes and survives restarts and upgrades.

### Without the installer

```bash
cp .env.example .env
docker compose up -d                              # Standalone
```

For Distributed, uncomment `COMPOSE_FILE=docker-compose.distributed.yml` in
`.env` first, or pass `-f docker-compose.distributed.yml` to every command.

Only `./bin/install` generates secrets. When you copy the file by hand, set the
keys listed under [Generated by the installer](https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer)
before the first `docker compose up`; otherwise the stack runs on defaults that
are published in this repository (`MINIO_ROOT_PASSWORD` even arrives as the
literal placeholder `CHANGEME-set-by-bin-install`).

---

## Prerequisites

| Requirement | Standalone | Distributed | Notes |
| --- | --- | --- | --- |
| Docker Engine | 24.0+ | 24.0+ | Docker Desktop, Colima or OrbStack on macOS and Windows, or Docker Engine on Linux |
| Docker Compose | v2.24+ | v2.24+ | `docker compose version` should print v2.x |
| CPU | 2 vCPU | 4+ vCPU | |
| Docker memory | 4 GB (3 GB minimum); idles at about 1 GB | 12–16 GB (6 GB minimum) | The Docker VM's memory, not the host's. `bin/install` measures it |
| Disk | 10 GB free | 20 GB free | Images plus data; data grows from there |
| Privileged containers | not needed | needed by `code-executor` | In Standalone only the optional `sandbox` profile needs them. Not available on Fargate, Cloud Run or some PaaS |
| Architecture | `linux/amd64` or `linux/arm64` | same | See the Apple Silicon note below |

The optional `ml` profile (model serving) adds a large image and a few GB of
memory once its models load. **Helm** needs Kubernetes 1.27+ and Helm 3.10+;
an evaluation install with bundled datastores wants about 4 CPUs and 8 GiB
free and a default StorageClass. See the
[chart's requirements](deploy/helm/futureagi/README.md#requirements).

On macOS and Windows, Docker runs in a VM, and the stack gets the VM's memory:
Colima starts with 2 GB, Docker Desktop with a share of the host's RAM. Resize
it before installing (Colima: `colima stop && colima start --cpu 2 --memory 4`;
Docker Desktop: Settings → Resources). `bin/install` checks this with a tiny
`busybox` container. Below the minimum it stops with the exact fix (`--force`
continues anyway); below the recommended size it warns. An existing
Distributed install is never blocked: re-running the installer to upgrade it
only warns.

The checkout must be on a path the Docker VM shares, because the stack
bind-mounts config files from it. Colima shares only your home directory, so a
clone under `/tmp` or `/opt` mounts as an empty folder. `bin/install` checks
this too.

**Apple Silicon (M-series) Macs and Linux arm64 hosts (e.g. Graviton):**
releases publish every Future AGI image for `linux/amd64` and `linux/arm64`,
each built natively. The exceptions are the CUDA variant of model serving
(`-gpu` tags) and the voice simulation runner, which are `linux/amd64` only.
Backend images of older releases are `linux/amd64` only, and Docker runs those
under emulation (Rosetta 2 on Docker Desktop 4.16+; `qemu-user-static` on
Linux). That works, but it is slower and the first boot can take 20+ minutes.
`bin/install` warns when this happens. `./bin/install --from-source` always
builds native images.

### Download size

What a fresh install downloads, compressed. Per-image sizes, their budgets and
how to check a release are in [Container images](https://docs.futureagi.com/docs/self-hosting/images#images-at-a-glance).

| | Standalone | Distributed |
| --- | --- | --- |
| Containers | 3 (+1 per optional profile) | 31: 22 long-running services and 9 one-shot setup jobs (`COMPOSE_PROFILES=all` adds 10) |
| First download | about 800 MB: `futureagi/standalone` + `postgres:16` + `clickhouse-server:25.3-alpine`. `ml` adds about 450 MB, `sandbox` about 185 MB | the Future AGI images, with the [default backend variant](https://docs.futureagi.com/docs/self-hosting/images#backend-variants), plus Postgres, ClickHouse, Redis, MinIO, Temporal, Kafka and PeerDB |
| Disk for images | about 2.5–3 GB unpacked | several GB more; keep 20 GB free |

Standalone's `futureagi/standalone` is built on the slim backend image
(`futureagi/future-agi:<version>-slim`), while Distributed and Helm run the
feature-complete default one (`futureagi/future-agi:<version>`); see
[Backend variants](https://docs.futureagi.com/docs/self-hosting/images#backend-variants). The two share no backend
layers, so switching between Standalone and Distributed downloads the backend
once more. Serving installs CPU PyTorch unless you pick the `-gpu` tag.
Upgrades download only the layers that changed.

---

## Deployment modes

Three ways to run Future AGI, plus a development overlay. All run the same
application code.

| | **Standalone** (default) | **Distributed** (at scale) | **Helm** (Distributed on Kubernetes) |
| --- | --- | --- | --- |
| Runs on | One Docker host | One Docker host | A Kubernetes cluster |
| Install | `./bin/install` | `./bin/install --distributed` | `helm install futureagi deploy/helm/futureagi ...` |
| Files | `docker-compose.yml` | `docker-compose.distributed.yml` | [`deploy/helm/futureagi`](deploy/helm/futureagi/README.md) |
| Containers | 3: `app`, `postgres`, `clickhouse` (+1 per optional profile) | 31: 22 services and 9 one-shot jobs (`all` profile: +10) | One Deployment per service and a bootstrap Job; datastores external (default) or bundled |
| Hardware | 2 vCPU, 4 GB (3 GB minimum); idles at about 1 GB | 4+ vCPU, 12–16 GB (6 GB minimum) | Evaluation: about 4 CPUs and 8 GiB free. Production: per service, see [Sizing](deploy/helm/futureagi/README.md#sizing) |
| Workflow engine | Temporal dev server (SQLite) inside `app`; the worker runs in the API process | Temporal server on Postgres; one all-queue worker, per-queue workers with the `all` profile | Your Temporal (or a bundled dev server); all-queue worker plus optional per-queue Deployments |
| Postgres → ClickHouse sync | In-process outbox | PeerDB | In-process outbox |
| Observed-attribute suggestions in filters, widgets and tasks | On: the collector writes them directly | On: through Kafka | On: the collector writes them directly |
| Model serving | Optional (`ml` profile) | Always on | Optional (`serving.enabled`) |
| Code-eval sandbox | In-app, unprivileged, Python only; nsjail, Python and JavaScript, with the optional `sandbox` profile. See [Code evals and the sandbox](#code-evals-and-the-sandbox) | nsjail `code-executor` (privileged) | Off by default: custom code evals are refused until you enable the privileged sandbox |
| Scaling | One machine | Per service, on one machine | Per service, with autoscaling |
| Use it for | Laptops, evaluation, a small team on one VM (a few people working at once) | High volume on one large host; the [production overlay](deploy/README.md) | Production on Kubernetes |

### Standalone (default)

`docker-compose.yml` runs three containers. Everything that is not a database
lives in the single `app` container (image `futureagi/standalone`), under a
process supervisor: the Django API with the Temporal worker in the same
process, a Temporal dev server, Redis, object storage, the trace collector,
the LLM gateway, the code-eval sandbox and nginx serving the UI. Each start
runs one bootstrap step (migrations when needed, system evals, the ClickHouse
schema, Temporal schedules) before the API opens.

```bash
./bin/install                                    # or: docker compose up -d
docker compose ps
docker compose logs -f app
```

Postgres and ClickHouse publish no host ports. Two optional profiles add a
container each; set them in `.env` and run `./bin/install` (or
`docker compose up -d`) again:

```bash
COMPOSE_PROFILES=ml               # model serving: embeddings, model-based evals
COMPOSE_PROFILES=sandbox          # nsjail code-executor (needs privileged containers)
COMPOSE_PROFILES=ml,sandbox
```

- **`ml`** adds `serving`, the embedding model server behind embedding-based
  evals and knowledge bases (and Error Feed clustering, which needs an
  Enterprise Edition license). Without it those features report
  that model serving is not deployed; everything else works. It needs a few
  more GB of memory once its models load. The image runs PyTorch on the CPU.
  On a host with an NVIDIA GPU, set `SERVING_VERSION=<version>-gpu` (or
  `latest-gpu`) in `.env` for the CUDA build (`linux/amd64` only, about 3.3 GB)
  and give the container the GPU in a `docker-compose.override.yml`
  (`deploy.resources.reservations.devices`).
- **`sandbox`** adds the privileged nsjail `code-executor`, and the app then
  sends custom code evals to it instead of its built-in sandbox. It is what
  runs JavaScript code evals: the built-in sandbox runs Python only. Set it in
  `.env`, not only with `--profile` on the command line: the app reads
  `COMPOSE_PROFILES` from `.env` to make that switch.

To turn a profile off, remove it from `COMPOSE_PROFILES` in `.env`, run
`docker compose up -d`, then remove its container yourself. Compose leaves the
containers of a profile that is no longer active running, and
`--remove-orphans` does not count them as orphans, so the privileged
`code-executor` would keep running although the app no longer uses it:

```bash
docker compose --profile sandbox rm -sf code-executor   # after turning sandbox off
docker compose --profile ml rm -sf serving              # after turning ml off
```

#### Code evals and the sandbox

Custom code evals run user-written Python or JavaScript. In Standalone without
the `sandbox` profile, JavaScript evals do not run: the `app` image has no
Node.js, so each one fails with an error that names the `sandbox` profile, and
the setup screen's **Code execution sandbox** row says so. Python evals run
inside the `app` container as an unprivileged user: with an empty environment; CPU-time, memory, process, file-size and
open-file limits; a private temporary directory that is deleted afterwards;
and the whole process group killed when the run ends or times out. That user
cannot read `/data` (object storage, Temporal's database) or the mounted
secrets (the gateway config and a Google credentials file), and the
container's Redis requires a password.

Eval code shares the app container's network namespace. Where the Docker
host runs Linux 6.7 or later with Landlock enabled, the sandbox lets it open
TCP connections only to ports 80 and 443 (`CODE_EXECUTOR_EGRESS_PORTS`) and
listen on no TCP port, which keeps the Temporal dev server on loopback (no
authentication), Postgres, ClickHouse, Redis, object storage, the API and the
gateway out of its reach; whatever your network serves on 80 or 443, and UDP,
stay reachable. On older kernels, which the app's log names at start
(`docker compose logs app`), an eval can connect to everything the app
reaches: the Temporal dev server, Postgres and ClickHouse over the Docker
network (their passwords are not in the eval's environment; ClickHouse has
none on installs made before the installer generated `CH_PASSWORD`), and
anything else your network allows. Concurrent evals also run as the same
user. The `app` container is unprivileged, so it cannot firewall one user's
traffic by address (that needs `NET_ADMIN`) or give an eval a network
namespace of its own (Docker's default seccomp profile refuses one without
`SYS_ADMIN`), and the platform does not add either capability to it.

So the built-in sandbox suits installs where everyone who can write a code
eval is trusted with the data, such as a laptop or a single team. For installs
shared by people who must not trust one another, set
`COMPOSE_PROFILES=sandbox` in `.env` and run `./bin/install` again: every run
then gets its own nsjail jail, with its own processes and a read-only view of
the filesystem. It needs privileged containers. Like Distributed's
`code-executor`, nsjail still allows network access, so keep code-eval
authorship to people you trust with the databases on any install that holds
sensitive data.

### Distributed (at scale)

`docker-compose.distributed.yml` runs every service in its own container:
frontend, backend, an all-queue Temporal worker and the exact-aggregation
worker, agentcc-gateway, serving, code-executor, fi-collector and the
observed-attribute catalog (Kafka and its consumer), Postgres, ClickHouse,
Redis, MinIO, Temporal and PeerDB, plus their one-shot setup jobs.

```bash
./bin/install --distributed
docker compose ps                                # COMPOSE_FILE in .env selects Distributed
docker compose logs -f backend
```

`./bin/install --distributed` writes `COMPOSE_FILE=docker-compose.distributed.yml`
to `.env` (plus `docker-compose.override.yml` when you have one), so later runs
of the installer and plain `docker compose` commands stay on Distributed.
`COMPOSE_PROFILES=all` in `.env` adds the per-queue workers, the Temporal UI
and the PeerDB UI; `workers`, `observability` and `peerdb` add one group each.

The UI, API, gateway and serving ports listen on all interfaces; the
code-executor and the data stores on `127.0.0.1` (see
[Host ports](https://docs.futureagi.com/docs/self-hosting/configuration/reference#host-ports)). Put a reverse proxy in front for HTTPS in
any non-laptop deployment. For production on Compose, layer the
[production overlay](deploy/README.md) on this file.

An install made before Standalone existed ran this stack from
`docker-compose.yml`. `./bin/install` recognises one by its volumes (MinIO,
Redis, RabbitMQ, PeerDB, Kafka, collector) or its containers (`backend`,
`worker`, ...), keeps it on Distributed and records `COMPOSE_FILE` for it; see
[Upgrading](#upgrading).

### Helm (Distributed on Kubernetes)

The chart in [`deploy/helm/futureagi`](deploy/helm/futureagi/README.md) runs
the Distributed setup on Kubernetes: one Deployment per service, with the
same images, and a bootstrap Job that migrates and seeds on every install and
upgrade. Postgres changes reach ClickHouse through the outbox, as in
Standalone, so there is no PeerDB.

An evaluation install, with every datastore in the cluster:

```bash
helm install futureagi deploy/helm/futureagi \
  -f deploy/helm/futureagi/examples/bundled.yaml \
  --namespace futureagi --create-namespace --timeout 20m

kubectl -n futureagi port-forward svc/futureagi-frontend 3000:80 &
kubectl -n futureagi port-forward svc/futureagi-backend 8000:8000 &
kubectl -n futureagi port-forward svc/futureagi-fi-collector 4318:4318 &   # traces
kubectl -n futureagi port-forward svc/futureagi-minio 9005:9000 &          # file downloads
kubectl -n futureagi exec -it deploy/futureagi-backend -c backend -- python manage.py create_user
```

Then open <http://localhost:3000>. SDKs on this machine send traces with
`FI_BASE_URL=http://localhost:4318`, as in the README's Quickstart.

Until a published release contains the chart's bootstrap command (none does
yet), add `--set image.registry=<registry> --set image.tag=<tag>` for
images built from this checkout and pushed where the cluster can pull them;
otherwise the bootstrap job fails with `Unknown command: 'bootstrap_install'`.
See the [chart README](deploy/helm/futureagi/README.md).

For production, start from
`examples/external.yaml` (your own Postgres, ClickHouse, Redis, Temporal and
S3-compatible storage) and `examples/ingress.yaml`; bundled datastores are for
evaluation only (one replica, no backups).

Application settings: every key in the [configuration reference](https://docs.futureagi.com/docs/self-hosting/configuration/reference)
marked **H** can be set through the chart. Most have a named value; any other
goes in `config.extraEnv` (secrets in `secrets.extra`, or your own Secrets and
ConfigMaps through `config.extraEnvFrom`). Telemetry is `config.telemetry`.
The [chart README](deploy/helm/futureagi/README.md) covers upgrades, secrets,
ingress, sizing, security, GitOps and troubleshooting, and lists every value.

### Development (hot reload)

For contributors changing the code:

```bash
./bin/dev                        # Standalone with hot reload
./bin/dev --distributed          # the Distributed topology, only when a change needs it
```

The first run builds every image from your checkout and installs through
`./bin/install --from-source`. After that, Python changes under `futureagi/`
restart the API, and the UI on <http://localhost:3000> is the Vite dev server
with hot module replacement. See [Local development](https://docs.futureagi.com/docs/self-hosting/development) for
what reloads, what needs `./bin/dev rebuild`, migrations, tests and
troubleshooting.

### Frontend only

For users who run the backend elsewhere (a VM, another Compose project, a
Kubernetes cluster) and only want a local UI container:

```bash
VITE_HOST_API=https://api.your-backend.example.com \
  docker compose -f docker-compose.frontend.yml up -d
```

Or set `VITE_HOST_API` in `.env` and run without the inline variable. Restart
the container to pick up a change; no rebuild is needed (the entrypoint
regenerates `/config.js` from `VITE_HOST_API` on each start).

### Installing a branch from source

The published images are built from `main`. A checkout of any other branch,
`dev` included, has code those images do not, so run it from source:

```bash
git checkout dev
./bin/install --from-source                   # Standalone
./bin/install --distributed --from-source     # Distributed
```

`--from-source` builds, in order: the backend (`futureagi/Dockerfile.oss`) as
`futureagi/future-agi:local` (the slim variant for Standalone, the default one
with `--distributed`; see [Backend variants](https://docs.futureagi.com/docs/self-hosting/images#backend-variants)),
then `futureagi/frontend:local`,
`futureagi/fi-collector:local` and `futureagi/agentcc-gateway:local`, and for
Standalone the app image `futureagi/standalone:local` from those four
(`deploy/standalone/Dockerfile`). It writes `FUTURE_AGI_VERSION=local` to `.env`
(Distributed also gets `FRONTEND_VERSION`, `AGENTCC_GATEWAY_VERSION` and
`FI_COLLECTOR_VERSION=local`) and never tries to pull those images.

- The builds are native, so this is also how you get arm64 images on Apple Silicon.
- The frontend build is memory-hungry: give Docker 8 GB while building. Expect
  the first build to take a while; later builds reuse Docker's cache.
- After `git pull`, run `./bin/install --from-source` again to rebuild.
- A later `./bin/install` without the flag keeps using the `local` images. To
  go back to published images, empty `FUTURE_AGI_VERSION` (and, in
  Distributed, the three tags above) in `.env` and re-run `./bin/install`.

### Switching between Standalone and Distributed

Moving an existing install from one setup to another, data included, is not
supported. That includes growing a Standalone install into Distributed or
Helm: the only way from Standalone to either is a fresh install, so if you
expect to outgrow one host, start on Distributed or Helm. Standalone keeps its
workflows and stored files in its `app-data` volume, and Distributed syncs
Postgres to ClickHouse through PeerDB instead of the outbox, so the data would
not carry over. `bin/install` therefore never switches an install that
has data:

- A re-run keeps whatever `.env` records (`COMPOSE_FILE`).
- An install made before Standalone existed is recognised by its volumes
  (MinIO, Redis, RabbitMQ, PeerDB, Kafka, collector) or containers
  (`backend`, `worker`, ...) and stays on Distributed; the installer records
  `COMPOSE_FILE` for it and says so. Deleting the `COMPOSE_FILE` line does not
  move it: the next run puts it back.
- `--distributed` stops with an error for a project that holds a Standalone
  install (its `app` container or `app-data` volume), before changing `.env`.
  `--force` does not override this.
- A `COMPOSE_FILE` set in your shell wins over `.env` in Docker Compose. It
  has to list the chosen setup's compose file and not the other one, or the
  installer stops before it records the setup or wipes volumes. Unset it
  (`unset COMPOSE_FILE`), or set it to the value the installer prints, and
  run the installer again.

To switch, back up what you need (see [Backups](#backups)), then start fresh:

```bash
# Standalone → Distributed
./bin/uninstall --wipe-data
./bin/install --distributed            # or, in one step: ./bin/install --distributed --wipe-volumes

# Distributed → Standalone
./bin/uninstall --wipe-data
# delete the COMPOSE_FILE= line from .env
./bin/install
```

`--wipe-data` keeps `.env`, so the new install reuses its secrets. To try the
other setup without touching this one, run it as a separate project instead:
a second checkout with another `COMPOSE_PROJECT_NAME` and other ports in its
`.env` (see [Host ports](https://docs.futureagi.com/docs/self-hosting/configuration/reference#host-ports)).

For Helm, install the chart (see [Helm](#helm-distributed-on-kubernetes)),
then remove the Compose install with `./bin/uninstall --wipe-data`. The chart
does not read `.env`: carry what you need from it over in the chart's values.

---

## Optional feature extras

Standalone's `app` image is built on the **slim** backend: heavy ML, audio
and voice dependencies and several SDKs are not installed, which keeps that
backend around 340 MB to download. Distributed and Helm run the default
backend, which also has the `sandbox`, `billing`, `ops`, `gcp`, `langchain`,
`rabbitmq` and `localizer` extras, `uv`, git and Debian's ffmpeg
([Backend variants](https://docs.futureagi.com/docs/self-hosting/images#backend-variants)). Neither has `audio`,
`ml`, `voice`, `pii`, `prompt-opt` or `vectordb`. Most features work out of
the box. The ones below need an optional dependency group ("extra") baked into
the image:

| Feature | Extra |
| --- | --- |
| Audio evals: TTS/STT via ElevenLabs, audio decoding (av, librosa) | `audio` |
| ML-based evals: torch models, HuggingFace datasets/transformers | `ml` |
| Voice simulation: LiveKit calls, Retell agents | `voice` |
| PII detection and scrubbing (Presidio, spaCy) | `pii` |
| Prompt optimization (Optuna, GEPA) | `prompt-opt` |
| Vector-DB dataset columns (Pinecone, Qdrant, Weaviate, Chroma) | `vectordb` |
| Vertex AI partner models (Claude, Llama, Mistral, Jamba and Codestral on Vertex), Model Garden, Gemma and PaLM, and Vertex tracing. Gemini, Imagen and embeddings on Vertex work without it | `gcp` |
| Hosted agent runs on Daytona or E2B sandboxes | `sandbox` |
| RabbitMQ channel layer (`CHANNEL_LAYER_BACKEND=rabbitmq`) | `rabbitmq` |
| Error localization with the Claude Agent SDK (`ERROR_LOCALIZER_BACKEND=claude_agent_sdk`, the default outside Standalone; needed to localize simulation call audio). Standalone runs the `legacy` localizer, which needs no extra | `localizer` |
| Stripe billing tooling, Celery Flower, LangChain (none are used by the open-source app) | `billing`, `ops`, `langchain` |

**What happens without the extra:** most optional features fail with an
`ImportError` that names the missing extra and points here; some evaluation
and clustering paths degrade gracefully and log that the capability is
unavailable. Voice simulation is gated up front and returns a clear "not
available in this build" API error. Without `gcp` the model picker does not
offer the Vertex partner models. Without `sandbox`, hosted runs on Daytona or
E2B answer `501 sandbox_sdk_missing`. Standalone's slim backend has neither
`gcp` nor `sandbox`. If PII redaction is enabled for a project, ingestion
fails closed until the `pii` extra is installed, so unredacted data is never
stored silently.

**To enable extras**, rebuild the backend image with the `EXTRAS` build
argument (comma-separated). `EXTRAS` replaces the variant's own list of
groups, so name every group you want:

```bash
# Distributed and Helm: the default backend's groups plus yours
docker build -f futureagi/Dockerfile.oss \
  --build-arg EXTRAS=sandbox,billing,ops,gcp,langchain,rabbitmq,localizer,audio,pii \
  -t future-agi-backend:with-extras ./futureagi

# Standalone: the slim backend with just the groups you add
docker build -f futureagi/Dockerfile.oss \
  --build-arg IMAGE_VARIANT=slim --build-arg EXTRAS=audio,pii \
  -t future-agi-backend:with-extras ./futureagi
```

In Distributed, point your compose file at the new tag (or add a `build:`
override for the `backend` and `worker` services). Standalone's app image is
built on top of the backend image, so rebuild it from the new tag:
`docker build -f deploy/standalone/Dockerfile --build-arg BACKEND_IMAGE=future-agi-backend:with-extras -t futureagi/standalone:local deploy/standalone`,
then set `FUTURE_AGI_VERSION=local`. Extra versions install from `uv.lock`, so
a rebuilt image gets the exact dependency resolution CI tests, not a fresh
re-resolve.

Installing every extra brings back the old "fat" image: several GB, most of it
the `ml` extra's PyTorch, which on `linux/amd64` pulls CUDA wheels. Only do
that if you need everything.

**Other build arguments** of `futureagi/Dockerfile.oss`. `IMAGE_VARIANT`
(`standard`, the default, or `slim`) sets the default of each one below; a
value you pass wins. Every image's build arguments are in
[Build arguments](https://docs.futureagi.com/docs/self-hosting/images#build-arguments).

| Build argument | Default: standard / slim | What it changes |
| --- | --- | --- |
| `FFMPEG_FLAVOR` | `debian` / `minimal` | `debian`: Debian's ffmpeg (about 145 MB more than `minimal`). `minimal`: a small LGPL `ffmpeg`/`ffprobe` build with the formats the app handles (WAV, MP3, Ogg/Opus, FLAC, AAC/M4A, AMR, WMA, WebM, MP4, H.264/HEVC/VP8/VP9, and more); an upload in an exotic codec (AV1, WavPack, ProRes) fails with "Decoder not found". `none`: no ffmpeg, so audio upload and conversion, video thumbnails and Deepgram speech-to-text fail |
| `WITH_GIT` | `true` / `false` | git (about 29 MB). Hosted agent runs from a GitHub source need it and otherwise answer `501 git_unavailable` |
| `SLIM_SITE_PACKAGES` | `0` / `1` | `1` drops package test suites, type stubs, debug symbols and the Google API discovery documents the app does not call (about 63 MB); keep `0` for code that calls other Google APIs through `googleapiclient` |
| `NLTK_DATA_PROFILE` | `full` / `minimal` | `minimal`: the English NLTK data the app loads. `full`: every language and the legacy packages (about 100 MB more) |
| `WITH_UV` | `true` / `false` | `uv` and `uvx` in the image, for images built on top of this one that install more packages |

The model-serving image (`futureagi/model_serving/Dockerfile.oss`) takes
`TORCH_BACKEND`: `cpu` by default, `cu124` for the CUDA build that releases
publish as `futureagi/serving:<version>-gpu`.

---

## Configuration

**Nothing is required for a local install.** `./bin/install` creates `.env`
from [`.env.example`](.env.example) and fills in the secrets; every other key
has a working default. Every variable, its default, which setup reads it and
what goes wrong when it is wrong is in the
**[Configuration reference](https://docs.futureagi.com/docs/self-hosting/configuration/reference)**;
[Environment variables](https://docs.futureagi.com/docs/self-hosting/configuration/environment#what-to-set-by-situation) has a table of what to
set, by situation:

| You want to... | Set |
| --- | --- |
| Run LLM evals, the prompt playground, agent features | One provider key, e.g. `OPENAI_API_KEY` ([LLM providers](#configuring-llm-providers)) |
| Open the UI from another machine or a domain | `VITE_HOST_API`, `BASE_URL`, `FRONTEND_URL`, `APP_URL`, `MINIO_URL` ([below](#opening-the-ui-from-another-machine)) |
| Send invites and password resets by email | `MAILGUN_API_KEY`, `MAILGUN_SENDER_DOMAIN`, `DEFAULT_FROM_EMAIL`, optionally `DEFAULT_REPLY_TO_EMAIL` ([Email](#email)) |
| Stop sending deployment telemetry | `FUTURE_AGI_TELEMETRY_DISABLED=true`, or `./bin/install --no-telemetry` ([Telemetry](#telemetry-and-outbound-connections)) |
| Go to production | The [production checklist](https://docs.futureagi.com/docs/self-hosting/production/checklist) |

Docker Compose reads `.env` from the directory where you run it. Two lines
choose what runs: `COMPOSE_FILE` (unset: Standalone;
`docker-compose.distributed.yml`: Distributed) and `COMPOSE_PROFILES` (optional
services). `FUTURE_AGI_VERSION` is the tag of the Future AGI images: empty
means `latest`, a release such as `v1.42.0` pins it, and `local` means images
built by `--from-source`. After changing `.env`, run `docker compose up -d`: it
recreates the containers whose settings changed and keeps the data.

The keys of Future AGI's own cloud service (`HUBSPOT_API_TOKEN`,
`SLACK_WEBHOOK_CHANNEL`, `MIX_PANEL_TOKEN`, `POSTHOG_API_KEY`, ...) stay empty
on a self-hosted install: empty means the feature is skipped and nothing is
sent. See [Telemetry and outbound connections](#telemetry-and-outbound-connections).

### Secrets that must be changed

On a fresh install `./bin/install` generates the secrets and writes them to
`.env`. Without it, the compose files fall back to defaults that are published
in this repository, and so are the same on every install (for `CH_PASSWORD`,
no password at all), while the API (port 8000) and the gateway (port 8090)
listen on all interfaces: set your own before anyone else can reach the
deployment. Which keys these are, their defaults, how to generate one, what
the installer does on an existing install and what breaks when you change one
later are in
[Generated by the installer](https://docs.futureagi.com/docs/self-hosting/configuration/reference#1-generated-by-the-installer).

`CH_PASSWORD` is the password of ClickHouse's `default` user. ClickHouse
reads it at every start, as do the app, the collector and the bootstrap
jobs, so a new value would take effect at once, except where it was
stored: the dictionaries ClickHouse reads spans through, and on
Distributed the `ch_dest` peer, which PeerDB's init job creates with it
once. The installer therefore generates it on a fresh install only. On an
existing install it stops whenever `CH_PASSWORD` (in `.env`, or exported
in the shell) differs from the password ClickHouse runs with, which
`docker compose exec clickhouse printenv CLICKHOUSE_PASSWORD` prints: set
it back, or re-run with `--force` once you mean it. An install created
before the installer generated one keeps running without a password: the
installer leaves it empty and warns. To give such an install a password,
set `CH_PASSWORD` and run `docker compose up -d`: the bootstrap then
re-creates the dictionaries with it. On Distributed the Postgres →
ClickHouse sync stops until the `ch_dest` peer in PeerDB carries the new
password too, so leave it empty there unless you update that peer. A
password that is already set changes the same way, with the same caveat on
Distributed, but removing one is not supported: the bootstrap re-creates the
dictionaries only with a password, so they would keep the old one. No `<`,
`>` or `&` in it: ClickHouse reads it into its XML configuration.

### Ports reference

All ports are configurable in `.env`. `bin/install` checks the ones the chosen
setup publishes and offers a free port when one is taken. Which ports each
setup publishes, on which interfaces, and the variable that moves each one:
[Host ports](https://docs.futureagi.com/docs/self-hosting/configuration/reference#host-ports).

To run a second copy next to an existing install, use
`./bin/install --new-instance`, or a second checkout with another
`COMPOSE_PROJECT_NAME` and other ports in its `.env`.

#### Opening the UI from another machine

The UI (3000) and the API (8000) listen on all interfaces, but a browser on
another machine needs three things:

1. **The API's address.** The UI calls the API at `VITE_HOST_API`, which
   defaults to `http://localhost:8000` (the port follows `BACKEND_PORT`): on
   another device, `localhost` is that device. Set
   `VITE_HOST_API=http://<host>:8000` (or your API's https URL) in `.env` and
   run `docker compose up -d`. The installer prints the exact line
   for your LAN address. The API accepts any origin by default (see
   `CORS_ALLOWED_ORIGINS`).
2. **A route to object storage.** Object storage (9005) listens on `127.0.0.1`
   only, in both Compose setups (`./bin/dev --distributed` excepted), because
   its API accepts the root credentials. Every link to a stored file (dataset
   uploads and downloads, exports, audio and image previews) sends the browser
   to `MINIO_URL`, which defaults to `http://localhost:9005` and works only in
   a browser on the Docker host. Put a reverse proxy on the host in front of
   `127.0.0.1:9005`, for example a `files.example.com` virtual host with TLS
   that passes the path through unchanged (file URLs are
   `<MINIO_URL>/<bucket>/<key>`). For a single user, an SSH tunnel works too:
   `ssh -L 9005:127.0.0.1:9005 your-host`.
3. **`MINIO_URL` set to the URL the browser uses**, such as
   `https://files.example.com` (or `http://localhost:9005` with the tunnel), in
   `.env`, followed by `docker compose up -d`.

Setting `MINIO_URL` alone is not enough: the port it names must be reachable
from the browser. Publishing 9005 on all interfaces instead is possible with a
`docker-compose.override.yml`, but then anyone who can reach the port can try
the object-storage credentials, so set a strong `MINIO_ROOT_PASSWORD` first.
For a domain name, also set `BASE_URL`, `FRONTEND_URL` and `APP_URL`
([Public URLs](https://docs.futureagi.com/docs/self-hosting/configuration/reference#public-urls)). To send traces from SDKs on
other machines, put the proxy in front of the collector's `127.0.0.1:4318` the
same way and set `FI_COLLECTOR_PUBLIC_URL` to its URL: the in-app SDK snippet
and the setup screen then show it as `FI_BASE_URL`.

---

## Telemetry and outbound connections

A self-hosted install sends **deployment telemetry** to Future AGI, on by
default: it registers once, with the email addresses of its owner and
administrator accounts, and then sends usage counts on a schedule. It never
sends traces, prompts, completions, datasets or any other content. To opt
out, install with `./bin/install --no-telemetry` (Windows: `-NoTelemetry`), or
set `FUTURE_AGI_TELEMETRY_DISABLED=true` in `.env` and run
`docker compose up -d` (Helm: `config.telemetry=false`). Opting out still
sends one registration, without email addresses; block `api.futureagi.com` to
send nothing. The installer shows what telemetry sends before it asks for the
first account's email.

Every other outside service (HubSpot, Slack, Mixpanel, PostHog, reCAPTCHA,
Sentry, Mailgun) stays off until you give it a key. Users' browsers load the
UI's fonts, icons and a few other assets from public hosts.

[Telemetry and outbound connections](https://docs.futureagi.com/docs/self-hosting/configuration/telemetry) is the reference: the exact payloads
and schedule, what the opt-out still sends, how to see what your install
sent, and every other outbound connection, with what an install that allows
no outbound traffic must also set.

---

## Services and what they do

Image by image (ports, users, health checks, sizes): [Container images](https://docs.futureagi.com/docs/self-hosting/images).

### Standalone

| Service | Purpose |
| --- | --- |
| `app` | Everything but the databases, under one process supervisor: the Django API with its Temporal worker, the Temporal dev server (SQLite in the `app-data` volume), Redis (in memory), object storage (in `app-data`), the fi-collector trace collector, the agentcc-gateway LLM proxy, the code-eval sandbox, and nginx serving the UI. |
| `postgres` | Primary transactional store (users, traces, datasets, evals, prompts, annotations). |
| `clickhouse` | Analytics store for traces, spans, dashboards and evaluation queries. Fed from Postgres by an in-process outbox. |
| `serving` | `ml` profile only. Embeddings and small-model inference. |
| `code-executor` | `sandbox` profile only. nsjail sandbox for code evals; the app sends code evals to it while `COMPOSE_PROFILES` in `.env` includes `sandbox`. **Requires `privileged: true`.** |

### Distributed

| Service | Purpose |
| --- | --- |
| `frontend` | The React app served by nginx. |
| `backend` | Django API: REST, gRPC and WebSockets. Reads and writes Postgres, ClickHouse, Redis and MinIO. |
| `worker` | Temporal worker polling every queue except the exact-aggregation queue and those in `TEMPORAL_EXCLUDED_QUEUES` (default: the simulation runner's). With the `all` or `workers` profile, per-queue workers (`worker-default`, `worker-tasks-s`, `-l`, `-xl`, `worker-trace-ingestion`, `worker-agent-compass`, `worker-simulation-runner`) join it. |
| `worker-exact-aggregation` | Single-slot worker for exact analytics, the admission boundary for expensive queries. |
| `agentcc-gateway` | Go LLM proxy. Routes calls to OpenAI, Anthropic, Gemini, Bedrock, Vertex and others; retries, rate limits, logging. |
| `serving` | Embeddings and small-model inference. |
| `code-executor` | nsjail sandbox for code evals. **Requires `privileged: true`.** |
| `fi-collector` | OTLP receiver: writes spans to ClickHouse. |
| `property-catalog-kafka`, `fi-property-catalog-consumer` | The observed-attribute catalog: the collector publishes span attributes to Kafka, the consumer indexes them for filter suggestions. |
| `postgres` | Primary transactional store. |
| `clickhouse` | Analytics store for traces, spans, dashboards and evaluation queries. |
| `redis` | Cache, rate limits, locks and live updates (the channel layer). Also the invalidation bus between the backend and fi-collector: the backend (`REDIS_URL`) and fi-collector (`FI_AUTH_REDIS_ADDR`) must point at the **same** Redis. A mismatch is a silent failure: key revocation and project-delete cache invalidation stop working, and the collector's auth cache only expires by TTL. |
| `minio` | S3-compatible object storage (uploaded files, eval artifacts). The backend reaches it at `S3_ENDPOINT_URL` (a Docker host name); links returned to the browser use `MINIO_URL` (default `http://localhost:9005`). See [Opening the UI from another machine](#opening-the-ui-from-another-machine). |
| `temporal` | Durable workflow server. Shares the main Postgres. |
| `peerdb-*` (7 containers) | Postgres → ClickHouse change data capture. |

The one-shot jobs (`postgres-schema-bootstrap`, `clickhouse-native-bootstrap`,
`clickhouse-cdc-bootstrap`, `peerdb-temporal-init`, `peerdb-init` and four for
the catalog) set up the schema, the mirrors and the catalog, then exit.

---

## Configuring LLM providers

Set a provider key in `.env` (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GOOGLE_API_KEY` or the AWS pair) and run `docker compose up -d`; one provider
is enough to start. Workspaces can also add their own keys in the UI.

The LLM gateway ships with `agentcc-gateway/config.example.yaml`, which routes
OpenAI. To route more providers through it:

1. Copy the example:
   ```bash
   cp agentcc-gateway/config.example.yaml agentcc-gateway/config.yaml
   ```
2. Uncomment the providers you want (Anthropic, Gemini, Bedrock, Vertex, ...).
3. Set `AGENTCC_CONFIG_PATH=agentcc-gateway/config.yaml` in `.env`.
4. Set the matching `*_API_KEY` variables in `.env`.
5. Run `docker compose up -d`, which recreates `app` in Standalone and
   `agentcc-gateway` in Distributed. Later edits to `config.yaml` alone need a
   restart: `docker compose restart app` (Distributed: `agentcc-gateway`).

Your `config.yaml` is git-ignored. The example uses `${VAR}` interpolation so
the real key never has to live in the file, but treat it as a secret anyway.
The gateway runs as uid 65532 in Distributed, so a mounted config must be
readable by that user (mode `0644`).

### Vertex AI

Vertex needs a Bearer token from a GCP service account, not an API key. The
recommended pattern:

```yaml
vertex:
  base_url: "https://us-central1-aiplatform.googleapis.com"
  api_key: "${GOOGLE_ACCESS_TOKEN}"
  api_format: "gemini"
  headers:
    x-gcp-project: "${GCP_PROJECT_ID}"
    x-gcp-location: "us-central1"
```

Set `GOOGLE_ACCESS_TOKEN` in `.env` and rotate it through a sidecar that calls
`gcloud auth print-access-token`. Where the platform needs a service-account
JSON file, set `GOOGLE_APPLICATION_CREDENTIALS` to its absolute host path:
Compose mounts it read-only (see
[Google Cloud and Vertex AI](https://docs.futureagi.com/docs/self-hosting/configuration/reference#google-cloud-and-vertex-ai)).
Never commit the file or bake it into an image.

### Local models (Ollama, vLLM)

A model server on the Docker host, or a service in your Compose project, works
as a provider that a workspace adds in the UI (Gateway > Providers) with its
OpenAI-compatible base URL. Provider URLs on private networks are refused
until you allow them, for the gateway and the API alike:

```bash
echo "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS=true" >> .env
docker compose up -d
```

Anyone who can add a provider can then point the gateway and the API at any
address on those networks, the databases included (ClickHouse's `default` user
has no password while `CH_PASSWORD` is empty, as on installs made before the
installer generated one), so allow it only where all of them are trusted.
Loopback, link-local and cloud metadata addresses stay refused. A gateway or
API from before this setting ignores it and refuses every private URL. On
Helm, set `agentccGateway.allowPrivateProviderURLs=true`.

- **On the Docker host:** use `http://host.docker.internal:11434/v1` (Ollama)
  or `http://host.docker.internal:8000/v1` (vLLM), and make the server listen
  on an address containers reach (Ollama: `OLLAMA_HOST=0.0.0.0`). Docker
  Desktop, Colima and OrbStack resolve `host.docker.internal`; on Linux, add it
  in a `docker-compose.override.yml`, to `app` in Standalone (to
  `agentcc-gateway` and `backend` in Distributed):

  ```yaml
  services:
    app:
      extra_hosts:
        - "host.docker.internal:host-gateway"
  ```
- **As a Compose service:** add it to `docker-compose.override.yml` and use its
  service name, for example `http://ollama:11434/v1`.

---

## Email

Email is optional: you can create accounts from the command line
([Create your first account](#create-your-first-account)), invites return a
link to share, and an administrator resets passwords from the host
([Reset a password](#reset-a-password)). Without email, messages are written
to the app log instead of being sent.

To send invites and password resets by email, use
[Mailgun](https://www.mailgun.com/), the provider the app supports (it has a
free tier). Sign up, add a sending domain, copy your API key, and add to
`.env`:

```bash
MAILGUN_API_KEY=your-mailgun-api-key
MAILGUN_SENDER_DOMAIN=mail.yourdomain.com
DEFAULT_FROM_EMAIL=Future AGI <noreply@mail.yourdomain.com>
# Optional: where replies go. Unset, emails carry no Reply-To header.
DEFAULT_REPLY_TO_EMAIL=support@yourdomain.com
```

Then run `docker compose up -d`, which recreates `app` (Distributed: `backend`
and the workers). Every email the app sends comes from `DEFAULT_FROM_EMAIL`,
which must be on the sending domain; left empty, it is
`Future AGI <noreply@<MAILGUN_SENDER_DOMAIN>>`. "Forgot password" now emails
the reset link. Set `APP_URL` too: the links in emails are built from it
([Public URLs](https://docs.futureagi.com/docs/self-hosting/configuration/reference#public-urls)). SMTP settings are not read
from the environment. On Helm the same settings are `secrets.mailgunApiKey`,
`config.email.mailgunSenderDomain`, `config.email.fromEmail` and
`config.email.replyTo`.

---

## Upgrading

```bash
git pull
./bin/install            # add --from-source if you run a branch from source
```

Re-running the installer keeps your `.env` secrets, adds new keys from
`.env.example`, stays on the setup `.env` records, pulls the new images and
waits for the stack to be ready. Migrations run automatically on start.

On Helm: `helm upgrade futureagi deploy/helm/futureagi --namespace futureagi -f my-values.yaml --timeout 20m`;
the bootstrap Job migrates before any Deployment is rolled. See the
[chart README](deploy/helm/futureagi/README.md#upgrade).

> **Upgrading from a release where `docker-compose.yml` was the Distributed
> stack.** The root `docker-compose.yml` is now Standalone, and the old
> topology moved to `docker-compose.distributed.yml`. `./bin/install`
> recognises an existing Distributed install by its volumes or containers,
> keeps it on Distributed and records
> `COMPOSE_FILE=docker-compose.distributed.yml` in `.env`. A `COMPOSE_FILE`
> exported in your shell wins over `.env`: if it still lists
> `docker-compose.yml`, the installer stops and says how to fix it. If you upgrade
> with plain `docker compose` instead, add that line to `.env` **before** running
> `docker compose up -d`. Without it Compose reads the new default file under
> the same project name: it recreates your `postgres` and `clickhouse`
> containers with Standalone's small-host settings (fewer Postgres
> connections, smaller ClickHouse pools) while the old backend and workers
> keep running against them, and adds an `app` container that fails on the
> ports they hold. Your data stays in its volumes. To recover, add the line,
> remove the stray container with `docker compose -f docker-compose.yml rm -sf app`,
> and run `./bin/install` (or `docker compose up -d`), which restores
> Distributed's settings.

**Retired services (upgrading from v1.41).** Distributed no longer runs
RabbitMQ (Redis carries live updates) or the Kafka catalog sequencer
(`fi-property-catalog-sequencer`, with its `property-catalog-supervisor` and
`property-catalog-postgres-bootstrap` jobs). An upgraded install keeps their
containers until you remove them (`./bin/install` points out RabbitMQ's),
because `up` leaves containers of dropped services alone. Once the upgraded
stack is healthy:

```bash
docker compose up -d --remove-orphans      # removes the retired containers
```

Their volumes, `rabbitmq-data` and `property-catalog-sequencer-data`, are
unused from then on: nothing in the current Compose files mounts them, and
`docker compose down -v` does not remove them either. Keep them until the
upgrade has proven itself and you will not roll back to v1.41, then delete
them (the prefix is your Compose project name):

```bash
docker volume rm futureagi_rabbitmq-data futureagi_property-catalog-sequencer-data
```

**Password-reset links.** `.env.example` used to ship
`OSS_RETURN_PASSWORD_RESET_LINK=true`, which hands reset links to anyone who
asks. Unless you want that (see [Reset a password](#reset-a-password)), delete
the line from your `.env` and run `docker compose up -d`. `./bin/install`
warns while the line is there.

**Pinning versions.** In Standalone, `FUTURE_AGI_VERSION` tags the app image.
In Distributed each image is versioned on its own: `FUTURE_AGI_VERSION` for
the backend and workers, `FRONTEND_VERSION`, `AGENTCC_GATEWAY_VERSION`,
`FI_COLLECTOR_VERSION`, `SERVING_VERSION`, `CODE_EXECUTOR_VERSION`. Bump the
variable(s) in `.env`, then:

```bash
docker compose pull
docker compose up -d
```

Downtime is about the time the app (or backend) takes to restart. To roll
back, set the bumped variable(s) to the previous tag and re-run the same two
commands. To pin an exact image digest, use a `docker-compose.override.yml`,
never `FUTURE_AGI_VERSION`; see [Verifying an image](https://docs.futureagi.com/docs/self-hosting/images#verifying-an-image).

## Backups

Named Docker volumes hold all state. The prefix is the Compose project name
(`futureagi` unless `COMPOSE_PROJECT_NAME` says otherwise):

```bash
docker volume ls | grep futureagi
# futureagi_postgres-data
# futureagi_clickhouse-data
# futureagi_app-data        (Standalone: object storage, Temporal, collector spool)
# futureagi_minio-data      (Distributed; also redis-data, peerdb-*, property-catalog-kafka-data, ...)
```

To back up Postgres:

```bash
docker compose exec postgres \
  pg_dump -U futureagi -d futureagi --format=custom \
  > backup-$(date +%F).dump
```

To restore:

```bash
docker compose exec -T postgres \
  pg_restore -U futureagi -d futureagi --clean --if-exists \
  < backup-2026-04-22.dump
```

For ClickHouse, prefer `BACKUP TABLE ... TO S3(...)` rather than file-level
copies. See ClickHouse's
[Backup and Restore docs](https://clickhouse.com/docs/en/operations/backup).

Distributed's MinIO can be mirrored to any S3 endpoint with `mc mirror`. In
Standalone the stored files live in the `app-data` volume; back that volume up
alongside Postgres.

---

## Troubleshooting

### `Cannot connect to the Docker daemon`

Docker isn't running. Start Docker Desktop or Colima (macOS, Windows) or
`sudo systemctl start docker` (Linux).

### `bin/install` says Docker has too little memory

The Docker VM is smaller than the setup needs: 3 GB minimum for Standalone
(4 GB recommended), 6 GB minimum for a new Distributed install (12 GB
recommended). The message carries the fix for your runtime, for example
`colima stop && colima start --cpu 2 --memory 4`, or Docker Desktop →
Settings → Resources. `--force` continues anyway, at the risk of containers
being killed for lack of memory. Above the minimum, and for an existing
Distributed install at any size, it only warns.

### The UI shows "Future AGI is starting" for a long time

Standalone's first boot sets up Postgres, ClickHouse and Temporal, which takes
a few minutes; the page shows the current phase and switches to the app by
itself. If it stays on one phase, or the phase keeps starting over,
`docker compose logs -f app` says why: a failed bootstrap step prints its
error, waits 30 seconds and tries again. The usual causes are too little
memory (see above) and a changed `PG_PASSWORD`
([below](#backend-logs-fatal-password-authentication-failed-for-user-futureagi)).

On a slow or busy host the migrations of a first boot can take over half an
hour while the log keeps printing `Applying ...` lines. Docker reports the `app`
container as starting for up to an hour (its healthcheck's `start_period`), and
`./bin/install` waits as long (`INSTALL_READY_MAX_SECONDS`, 3600 by default,
at most 7200). If the installer gives up first, the stack keeps starting:
raise that value in `.env` and run `./bin/install` again, or follow
`docker compose logs -f app`.

After it is up, Standalone keeps its Temporal server (SQLite, inside `app`)
busy with 32 schedules. Six of them fire every 10 seconds: dataset and
experiment evals, optimization runs, inline and span evals, and the Postgres
to ClickHouse sync. A host short of CPU makes those pickups, and the UI, slow;
give Docker at least the 2 CPUs the [prerequisites](#prerequisites) ask for.
The API and the Temporal worker also share one process. On 6 CPUs, three
people clicking through heavy views at once (the e2e suite with three
workers) pushed slow API calls to 10–17 s and made Temporal's SQLite log
persistence timeouts; one or two stayed responsive. For more concurrent users
or a steady eval load, use [Distributed](#distributed-at-scale).

### `bin/install` says the project already holds a standalone install

You passed `--distributed` (or `.env` records
`COMPOSE_FILE=docker-compose.distributed.yml`) for a Compose project that
already runs Standalone. Its data cannot move to Distributed; see
[Switching between Standalone and Distributed](#switching-between-standalone-and-distributed).
If you did not mean to switch, delete the `COMPOSE_FILE` line from `.env` and
run `./bin/install` again.

### `bin/install` says an existing distributed install was detected

The project has volumes or containers only Distributed creates, typically
from a release where `docker-compose.yml` was the Distributed stack. The
installer keeps it there and records `COMPOSE_FILE` in `.env`; nothing is
lost. If it also finds a Standalone `app` container, a plain
`docker compose up -d` started the new default file against the old install:
remove that container with the `docker compose ... rm -sf app` command the
installer prints, then run `./bin/install` again.

### `bin/install` says `COMPOSE_FILE` is set in your shell

Docker Compose reads `COMPOSE_FILE` from your shell before `.env`, so the value
there decides which setup every `docker compose` command runs. The installer
stopped because that value does not start the setup it chose for this
project: it has to list that setup's compose file (`docker-compose.yml` for
Standalone, `docker-compose.distributed.yml` for Distributed) and not the
other one, and an empty value lists none. Run `unset COMPOSE_FILE` (on
Windows, `Remove-Item Env:COMPOSE_FILE`), remove it from your shell profile,
and run the command the installer prints: it includes `--distributed` when
you passed that flag, since `.env` does not record it yet. To keep extra
compose files, set `COMPOSE_FILE` to the value the installer prints instead,
when it prints one.

### `docker compose pull` failed: Docker Hub has no `futureagi/standalone:…`

The release you are installing was published before Standalone's image
existed (or Docker Hub is unreachable). Build the images from your checkout
instead: `./bin/install --from-source`. It needs an 8 GB Docker VM while
building.

### `bin/install` says Docker cannot see the checkout

Docker runs in a VM that shares only some host paths, and the stack
bind-mounts config files from the checkout. Colima shares your home directory
only; Docker Desktop shares what is under Settings → Resources → File
sharing. Move the clone under `$HOME`, or share its path
(`colima start --mount /path/to/future-agi`).

### `ERROR: You don't have enough free space in /var/cache/apt/archives/`

Docker Desktop's virtual disk is full. Either:

- Settings → Resources → Disk image size: raise it to 100 GB or more.
- Clean up: `docker system prune -af && docker builder prune -af`.

### `ports are not available: exposing port ... address already in use`

Another process is using that port. Stop it, or move the port in `.env`:

```
FRONTEND_PORT=3100
BACKEND_PORT=8100
```

`./bin/install` offers a free port on its own and writes it there. The UI's
API URL follows `BACKEND_PORT`, `APP_URL` follows `FRONTEND_PORT` and
`FI_COLLECTOR_PUBLIC_URL` follows `FI_COLLECTOR_OTLP_HTTP_PORT`, unless `.env`
sets them. If it sets `VITE_HOST_API` to a `localhost` URL, change its port as
well (`VITE_HOST_API=http://localhost:8100`); the installer does that for you
when it moves the port.

### Backend logs `FATAL: password authentication failed for user "futureagi"`

You changed `PG_PASSWORD` after the volume was created. Postgres sets the
password on first boot only. Either:

- Revert `PG_PASSWORD` to the original, or
- Wipe and reinitialize: `docker compose down -v` (destroys all data).

### Frontend loads but API calls fail with CORS errors

The UI calls the API at `VITE_HOST_API`, which is unset or wrong: typically
the UI is opened from another machine or a domain while `VITE_HOST_API` still
means `http://localhost:8000` (or your `BACKEND_PORT`). Set it in `.env` (or
your production env file) to the URL the browser reaches the API on, and
recreate the container that serves the UI (`app` in Standalone, `frontend` in
Distributed):

```bash
echo "VITE_HOST_API=https://api.example.com" >> .env
docker compose up -d
```

The UI's `/config.js` is regenerated on each container start, so no rebuild
is needed.

### Setup checks (pre-flight)

The first-run setup screen checks every service the app depends on, and each
failed check links to its entry below. It knows which setup it runs in and
shows that setup's fix: Docker Compose commands for Standalone and
Distributed, and on Helm `kubectl -n <namespace> get pods`,
`kubectl -n <namespace> logs deploy/<release>-<service>` and the values to
check (for an external datastore, its host and credentials). The entries below
give the Compose commands.

### Pre-flight says **Object storage service** failed

Object storage is not answering, so dataset uploads, exports and media will
fail. Tracing, prompts and evals keep working.

In Standalone it runs inside the `app` container:

```bash
docker compose restart app
docker compose logs --tail=100 app
```

In Distributed it is the `minio` container:

```bash
docker compose up -d minio
```

Then re-run pre-flight. Standalone needs no S3 credentials at all: compose
points `S3_ENDPOINT_URL` at the bundled object storage and derives
`S3_ACCESS_KEY` / `S3_SECRET_KEY` from `MINIO_ROOT_USER` /
`MINIO_ROOT_PASSWORD`. `MINIO_ROOT_USER` defaults to `futureagi`;
`MINIO_ROOT_PASSWORD` is generated for your install by `./bin/install`. To use
your own, set both in `.env` and run `docker compose up -d`, which recreates
every container that uses them with the new values. The three `S3_*`
variables live in the compose `environment` block, which takes precedence
over `.env`, so setting them there has no effect.

### Pre-flight says **SSL/TLS certificate** failed

The UI or the API is reached on a public host name or address without a valid
https certificate, so browser and SDK traffic travels unencrypted. A
**Production** launch blocks on this; **Test flight** does not run the check.

On a local install the check is skipped instead of failing: when no
configured URL names a public host and the browser reached the API on a local
one. The configured URLs are `FRONTEND_URL` and `VITE_HOST_API` (or `BASE_URL`
when `VITE_HOST_API` is empty; on Helm, `urls.app` and `urls.api`); local
means `localhost`, a private or CGNAT (Tailscale) address, a single-label host
name, or a `.local`, `.internal` or `.lan` name. While every configured URL
is local, as on a default Helm install, a browser that came in on a public
host name or address fails the check: set the public https URLs.

To fix it, serve the UI and the API over https through a reverse proxy with a
valid certificate (Caddy, nginx, a cloud load balancer), then set both URLs in
`.env` and run `docker compose up -d` (Helm: `urls.app` and `urls.api`, or the
ingress hosts with TLS):

```bash
VITE_HOST_API=https://api.example.com
FRONTEND_URL=https://app.example.com
```

Re-run pre-flight. See [`deploy/README.md`](deploy/README.md#reverse-proxy--tls)
for proxy examples.

### Pre-flight says **Core application database** failed

Postgres is not answering, so nothing in the app loads.

```bash
docker compose up -d postgres
```

If it starts and the backend still cannot reach it, check `PG_PASSWORD` in
`.env`. A password changed after the volume was created gives
[`FATAL: password authentication failed`](#backend-logs-fatal-password-authentication-failed-for-user-futureagi).

### Pre-flight says **Tracing data warehouse** failed

ClickHouse is not answering, so traces, spans and dashboards will not load.
The rest of the app keeps working.

```bash
docker compose up -d clickhouse
```

First boot applies the schema and can take a minute; `docker compose logs
clickhouse` shows it.

### Pre-flight says **Cache and session store** failed

Redis is not answering, so sessions, caching and rate limits will not work.
In Standalone, Redis runs inside the `app` container, so restart that:
`docker compose restart app`. In Distributed:

```bash
docker compose up -d redis
```

### Pre-flight says **Websocket connection** failed

The channel layer that carries live updates is not answering, so they will
not reach the browser. Pages still load; they just stop refreshing on their
own. Both Compose setups use Redis for it. In Standalone, restart the `app`
container (`docker compose restart app`) and check `docker compose logs app`.
In Distributed, bring Redis back:

```bash
docker compose up -d redis
```

If Redis is up, check `WEBSOCKET_ENDPOINT` in `.env`: it must reach the backend
from every container (see [Application behaviour](https://docs.futureagi.com/docs/self-hosting/configuration/reference#application-behaviour)).

### Pre-flight says **LLM request gateway** failed

The gateway is not answering, so every LLM call fails: evaluations, the
playground and agents. In Standalone it runs inside the `app` container
(`docker compose restart app`, then `docker compose logs app`). In
Distributed:

```bash
docker compose up -d agentcc-gateway
```

It also needs at least one provider key to be useful. See
[Configuring LLM providers](#configuring-llm-providers).

### Pre-flight says **Async task engine** failed

Temporal is not answering, so evaluations, optimizations and scheduled jobs
will not run. In Standalone the Temporal dev server runs inside the `app`
container: `docker compose restart app`, then
`docker compose logs --tail=100 app`. In Distributed:

```bash
docker compose up -d temporal
```

If it starts and then restarts in a loop, that is usually Postgres. See
[`temporal-server` keeps restarting](#temporal-server-keeps-restarting).

### Pre-flight says **Trace ingestion** failed

The trace collector is not answering, so spans sent by the SDK will not
arrive. Traces already in ClickHouse still show. In Standalone it runs inside
the `app` container (`docker compose restart app`). In Distributed:

```bash
docker compose up -d fi-collector
```

### Pre-flight says **Django backend** failed

The backend is not answering on its port, so nothing in the app works.

```bash
docker compose up -d app                   # Standalone
docker compose logs --tail=50 app
docker compose up -d backend               # Distributed
docker compose logs --tail=50 backend
```

The logs carry the real reason. The usual one is Postgres, above.

### Pre-flight says **React frontend** failed

The frontend is not serving. If you are reading this inside the app, the
check is pointing somewhere else rather than at a dead container: confirm
`FRONTEND_URL` in `.env` matches the URL you actually opened.

```bash
docker compose up -d app          # Standalone: the UI is served by the app container
docker compose up -d frontend     # Distributed
```

### Pre-flight says **Agent fixer** failed

`serving` is not answering, so embedding-based evals, ground truth and Vector
DB columns will not run (and, with an Enterprise Edition license, Error Feed
clustering). Tracing, prompts and datasets keep working.

In Standalone `serving` is optional and off, and the check then shows as
skipped: add `ml` to `COMPOSE_PROFILES` in `.env` and run
`docker compose up -d`. In Distributed:

```bash
docker compose up -d serving
docker compose logs serving
```

It is the heaviest optional service: it needs a few GB of memory once its
models load. Distributed with an empty `MODEL_SERVING_URL` also skips the
check.

### Pre-flight says **Code execution sandbox** failed

The code sandbox is not answering, so custom code evaluations will not run.
In Standalone it runs inside the `app` container
(`docker compose restart app`), or as `code-executor` with the `sandbox`
profile (`docker compose up -d code-executor`). In Distributed:

```bash
docker compose up -d code-executor
```

If it starts and immediately dies, the host does not allow `privileged: true`.
See [`clone: Operation not permitted`](#code-executor-crashes-with-clone-operation-not-permitted).

On Helm the sandbox is off by default (`codeExecutor.enabled=false`), so this
check fails until you turn it on with `codeExecutor.enabled=true` (the nodes
must allow privileged pods). Until then, launch with **Test flight**, where it
is only a warning; custom code evals are refused meanwhile.

### `code-executor` crashes with `clone: Operation not permitted`

The host kernel or container platform disallows `privileged: true` (Fargate,
Cloud Run, some Kubernetes policies). Either run on a platform that allows
privileged containers (EC2, GKE with privileged enabled, bare metal) or turn
off code evaluation features. Standalone without the `sandbox` profile does
not need privileged containers.

### JavaScript code evals fail with `JavaScript evals need Node.js`

Standalone's built-in sandbox runs Python code evals only. Add the nsjail
sandbox, which runs both (it needs privileged containers):

```bash
echo "COMPOSE_PROFILES=sandbox" >> .env   # or add sandbox to an existing COMPOSE_PROFILES line
docker compose up -d
```

The app then sends every code eval to it. See
[Code evals and the sandbox](#code-evals-and-the-sandbox).

### Code evals fail with `Code executor unavailable`

Code evals run only on `code-executor`. When the workers cannot reach it (container not running, wrong `CODE_EXECUTOR_URL`), each code eval returns this error instead of a score. Start it with `docker compose up -d code-executor` and check `docker compose logs code-executor`.

If your platform cannot run `code-executor` at all, you can set `CODE_EXECUTOR_LOCAL_FALLBACK=true` in `.env` and restart the backend and workers. Code evals then run inside the worker container when `code-executor` cannot be reached. Enable it only on installs where every user who can create or edit code evals is trusted. The setting is ignored when `CLOUD_DEPLOYMENT` is `US`, `EU` or `DEV`, and an HTTP error, timeout or invalid response from a running `code-executor` is always returned as an eval error.

### `temporal-server` keeps restarting

Distributed. The Postgres connection is the usual cause. Check
`docker compose logs postgres` for out-of-memory kills, and raise the Docker
VM's memory to 12 GB or more.

### Filters suggest no attributes or values for older traces

Trace filters, dashboard widgets and task filters suggest the span attributes
and values that were indexed as spans arrived (the observed-attribute index,
written by the collector). Spans stored while nothing indexed them are
missing from the suggestions, although filtering on them still works when you
type the name and value. That covers Standalone and Helm installs made before
the collector wrote the index itself, and the gaps the collector logs as
`observed_catalog_handoff_gap`, with the affected project and time range in
the `observed_catalog_repair_scope` events that follow.

Index a project's older spans with `fi-observed-catalog-backfill`, which is in
the `app` container (Standalone). Without `--apply` it only previews, writing
nothing. Preview one day first:

```bash
docker compose exec app sh -c 'FI_PG_DSN="$FI_PG_READ" \
  FI_OBSERVED_BACKFILL_CH_URL="$FI_CH_URL" FI_OBSERVED_BACKFILL_CH_DATABASE="$FI_CH_DATABASE" \
  FI_OBSERVED_BACKFILL_CH_USERNAME="$CH_USERNAME" FI_OBSERVED_BACKFILL_CH_PASSWORD="$CH_PASSWORD" \
  exec fi-observed-catalog-backfill "$@"' backfill \
  --project <project UUID> --since 2026-09-01T00:00:00Z --until 2026-09-02T00:00:00Z
```

Every hour of the range takes at least one page, more for busy hours, and a
run stops after `--max-pages` pages (100 by default). A preview keeps no
progress, so a longer one needs `--max-pages` of at least the number of hours
in its range (a 30-day month has 720), or a shorter range.

To index, run it over the whole range you need with `--apply` and a
checkpoint, which records its progress. This range has 6552 hours, so
`--max-pages 7000` leaves 448 pages for busy hours:

```bash
docker compose exec app sh -c 'FI_PG_DSN="$FI_PG_READ" \
  FI_OBSERVED_BACKFILL_CH_URL="$FI_CH_URL" FI_OBSERVED_BACKFILL_CH_DATABASE="$FI_CH_DATABASE" \
  FI_OBSERVED_BACKFILL_CH_USERNAME="$CH_USERNAME" FI_OBSERVED_BACKFILL_CH_PASSWORD="$CH_PASSWORD" \
  exec fi-observed-catalog-backfill "$@"' backfill \
  --project <project UUID> --since 2026-01-01T00:00:00Z --until 2026-10-01T00:00:00Z \
  --apply --checkpoint /data/collector/backfill-<project UUID>.json --max-pages 7000
```

If it stops with "page budget reached", run the exact same command again: it
resumes from the checkpoint. Repeat until its last line says
`"scan_complete": true`. Rerunning over a range already indexed is harmless.
On Helm, run the same binary from the
`futureagi/fi-collector` image with the collector's environment; see
[fi-collector/PROPERTY_CATALOG_OSS.md](fi-collector/PROPERTY_CATALOG_OSS.md)
for every option.

---

## Production hardening

- **Docker Compose:** [`deploy/README.md`](deploy/README.md) is the production
  guide: the production overlay (`deploy/docker-compose.production.yml`,
  layered on `docker-compose.distributed.yml`), required secrets, topologies,
  reverse proxy and TLS, backups, upgrades and the pre-flight checklist.
- **Kubernetes:** the [Helm chart](deploy/helm/futureagi/README.md), with
  external datastores.
- **Either way:** work through the
  [production checklist](https://docs.futureagi.com/docs/self-hosting/production/checklist),
  pin image versions ([Tags](https://docs.futureagi.com/docs/self-hosting/images#tags)), and decide on
  [telemetry](https://docs.futureagi.com/docs/self-hosting/configuration/telemetry).

---

Questions, bugs or contributions: <https://github.com/future-agi/future-agi/issues>.
