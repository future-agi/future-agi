# Local development

`./bin/dev` runs Future AGI from your checkout with hot reload. Save a Python
file and the API restarts with it; save a React file and the browser updates in
place. It uses the **Standalone** setup by default, the same one
`./bin/install` gives users, so what you test is what they run. Use the
**Distributed** setup only when a change needs it (see
[When to use `--distributed`](#when-to-use---distributed)).

For installing and running Future AGI without changing its code, see
[INSTALLATION.md](../INSTALLATION.md). For how to contribute, see
[CONTRIBUTING.md](../CONTRIBUTING.md).

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [What reloads and what needs a rebuild](#what-reloads-and-what-needs-a-rebuild)
- [Why the reloaders poll](#why-the-reloaders-poll)
- [Commands](#commands)
- [Database changes](#database-changes)
- [Reaching the databases](#reaching-the-databases)
- [Running tests](#running-tests)
- [When to use `--distributed`](#when-to-use---distributed)
- [Troubleshooting](#troubleshooting)

## Requirements

- Docker Desktop, Colima or OrbStack on macOS and Windows, or Docker Engine on
  Linux, with Docker Compose v2.24 or newer.
- Docker memory: **8 GB for the first build** (the frontend build is
  memory-hungry), 4 GB afterwards for Standalone, 12 GB for Distributed. On
  Colima: `colima stop && colima start --cpu 4 --memory 8`.
- The checkout under your home directory. The stack bind-mounts it, and Colima
  shares only `$HOME` with its VM.
- For running tests on the host: Python 3.11 with
  [uv](https://docs.astral.sh/uv/) (backend), Node 20 with Yarn (frontend).

## Quick start

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi
./bin/dev
```

The first run builds every image from your checkout (backend, frontend,
fi-collector, agentcc-gateway, then the Standalone `futureagi/platform:local`
image from those four) and installs through `./bin/install --from-source`. The
backend is its slim variant, the one a published Standalone install runs
([docs/images.md](images.md#backend-variants)). The installer writes `.env`
with generated secrets, sets up the databases and asks for your first account.
Expect it to take a while. Later runs skip the build and the
install and only start the development overlay.

When it prints `Up`:

| | URL | |
| --- | --- | --- |
| UI | <http://localhost:3000> | Vite dev server, hot module replacement |
| API | <http://localhost:8000> | reloads when you save under `futureagi/` |
| Traces (OTLP/HTTP) | <http://localhost:4318> | this machine only |
| LLM gateway | <http://localhost:8090> | |

`./bin/dev` layers [`docker-compose.dev.yml`](../docker-compose.dev.yml) on
[`docker-compose.yml`](../docker-compose.yml): your `futureagi/` directory is
mounted over the backend code in the `app` container, and a separate
`frontend` container runs Vite on port 3000 with your `frontend/` directory
mounted. Everything else (Postgres, ClickHouse, Temporal, Redis, object
storage, the collector and the gateway) runs as in a normal Standalone install.

## What reloads and what needs a rebuild

| You change | What happens |
| --- | --- |
| Python under `futureagi/` | The API and the Temporal worker embedded in it restart (`granian --reload`): the change is live in about 10 seconds, most of it the new worker booting Django (measured on Colima, Apple Silicon). |
| `frontend/src/**` | Vite updates the page in the browser, in about a second. |
| A model, then a migration | Reloads like any Python change. Apply the migration: see [Database changes](#database-changes). |
| `.env` | Run `./bin/dev` again: it recreates the containers whose settings changed. |
| Python dependencies (`futureagi/pyproject.toml`, `futureagi/uv.lock`) | `./bin/dev rebuild backend` |
| Node dependencies (`frontend/package.json`, `frontend/yarn.lock`) | `./bin/dev rebuild frontend` |
| `fi-collector/**` (Go) | `./bin/dev rebuild collector` |
| `agentcc-gateway/**` (Go) | `./bin/dev rebuild gateway` |
| `deploy/platform/**` (supervisord, nginx, `bin/start`, `bootstrap.py`) | `./bin/dev rebuild` |
| `docker-compose.yml`, `docker-compose.dev.yml` | Run `./bin/dev` again. |

In Standalone, every rebuild target also rebuilds the `futureagi/platform:local`
image on top of what it built; images that did not change come from Docker's
build cache. `./bin/dev rebuild` with no target rebuilds everything, and the
backend is always the slim variant, as on the first run.

In Distributed, `./bin/dev --distributed rebuild` rebuilds every image the
Compose files build from your checkout (backend and workers, the Vite frontend,
fi-collector, the simulation runner), whatever the target. The gateway keeps
the image the first install built: after a gateway change, run
`docker build -t futureagi/agentcc-gateway:local agentcc-gateway`, then
`./bin/dev --distributed`.

Which Python directories are watched: every top-level directory of
`futureagi/` that contains Python code, except `tests`, `docs`, `docker`,
`logs`, `media` and `static`. So editing a test does not restart the API, and
a new top-level directory is watched from the next container start
(`./bin/dev down && ./bin/dev`). Log files, `*.sqlite3`, `*.db`, `*.pyc` and
tool caches are ignored.

## Why the reloaders poll

Docker Desktop and Colima share your checkout with their VM through a
file-sharing layer that does not deliver inotify events to containers: an edit
made on the host changes the file inside the container, but nothing inside is
told. So both reloaders poll instead of waiting for events:

- `WATCHFILES_FORCE_POLLING=true` for the API (granian's reloader) and, in
  Distributed, for the Temporal workers (`watchfiles`).
- `VITE_USE_POLLING=true` for Vite, every 100 ms.

Polling reads every watched file on each pass, which is why only code
directories are watched and why the overlay hides a `futureagi/.venv` from the
container (an anonymous volume over `/app/backend/.venv`): the container uses
the image's own interpreter and packages, and the reloader never scans your
virtualenv. On Linux with Docker Engine, inotify would work, and polling costs
little.

## Commands

| Command | What it does |
| --- | --- |
| `./bin/dev` | Start the stack with hot reload, or bring it up to date with the compose files and `.env`. |
| `./bin/dev logs [service]` | Follow the logs; default `app` (Distributed: `backend`). E.g. `./bin/dev logs frontend`. |
| `./bin/dev shell` | A bash shell in the backend container, in `/app/backend`. |
| `./bin/dev manage <command> [args]` | `python manage.py <command>` in the backend container, allowed to change the database: `migrate`, `makemigrations`, `create_user`, `shell`, ... |
| `./bin/dev rebuild [target]` | Rebuild images and restart. Targets: `all` (default), `backend`, `frontend`, `collector`, `gateway`. |
| `./bin/dev ps` | Container status. |
| `./bin/dev down` | Stop, keeping the data. `./bin/dev down -v` also deletes the data. |
| `./bin/dev --distributed [command]` | The same commands on the Distributed setup. The choice is remembered in `.dev-mode` (git-ignored); `./bin/dev --standalone` switches back. |

`./bin/dev --help` prints this list. Under the hood it runs
`docker compose -p <project> -f docker-compose.yml -f docker-compose.dev.yml ...`
(Distributed: `-f docker-compose.distributed.yml -f docker-compose.distributed.dev.yml`),
where the project is `COMPOSE_PROJECT_NAME` from your shell or `.env`, and
`futureagi` by default. Any other Compose command works with the same flags.

## Database changes

```bash
./bin/dev manage makemigrations tracer   # write the migration for an app
./bin/dev manage migrate                 # apply it
```

The API, the workers and every `docker compose exec` session run with
`NO_STARTUP_DB_MUTATIONS=true`, so nothing changes the schema behind your back.
`./bin/dev manage` lifts that for the one command you run.

- **Standalone** also applies pending migrations the next time the `app`
  container starts: its bootstrap step migrates whenever the code has
  migrations the database has not applied.
- **Distributed** never migrates on start. Its `postgres-schema-bootstrap` job
  only checks (`migrate --check`), and a pending migration stops the backend
  and workers from starting. Run `./bin/dev --distributed manage migrate` while
  the backend runs. If it is already stopped, run the check job with a
  migrate command instead:

  ```bash
  docker compose -f docker-compose.distributed.yml -f docker-compose.distributed.dev.yml \
    run --rm -e NO_STARTUP_DB_MUTATIONS=false postgres-schema-bootstrap manage.py migrate --noinput
  ./bin/dev --distributed
  ```

New migration files appear in your checkout through the bind mount. On Linux
the container writes them as root: `sudo chown -R "$USER" futureagi` gives
them back to you.

## Reaching the databases

Standalone publishes no database ports. Use the containers' own clients:

```bash
docker compose exec postgres psql -U futureagi futureagi
docker compose exec clickhouse clickhouse-client
./bin/dev manage shell                     # Django shell with the app's models
```

The Distributed overlay publishes them on every interface, so a database GUI
on your machine can connect: Postgres `5432`, PgBouncer `6432`, ClickHouse
`8123` and `9000`, Redis `6379`, MinIO `9005` (console `9006`) and Temporal
`7233`, plus the Temporal UI on <http://localhost:8085>. The passwords are in
`.env`. Anyone on your network can reach those ports: keep Distributed
development on a trusted network.

## Running tests

Tests run on the host, against their own throwaway services, and never touch
the development stack.

**Backend** (pytest; Postgres, Redis, ClickHouse and MinIO in the
`futureagi-test` Compose project on ports 15432, 16379, 18123 and 19005):

```bash
cd futureagi
uv sync --frozen                          # once, and after dependency changes; creates futureagi/.venv
bin/test app tracer                       # one Django app
bin/test tracer/tests/test_foo.py::test_bar
make test                                 # everything
```

`bin/test` starts the services it needs and uses `futureagi/.venv`. CI installs
every optional dependency (`uv sync --frozen --all-extras`); do the same to run
tests of features behind an [extra](../INSTALLATION.md#optional-feature-extras).

**Frontend** (Vitest):

```bash
cd frontend
yarn install
yarn test:run                             # or `yarn test` for watch mode
```

**Deployment files and docs** (no Docker daemon needed):

```bash
python3 -m unittest discover -s deploy/tests -v
```

**End to end** (Playwright against its own `futureagi-e2e` stack): `bin/e2e up`,
then `bin/e2e test`. See [e2e/README.md](../e2e/README.md).

[TESTING.md](../TESTING.md) has the full picture: every suite, git hooks and CI.

## When to use `--distributed`

Standalone runs the same application code as Distributed, packed into one
`app` container. Develop there unless your change depends on something only
Distributed has:

- the PeerDB change data capture from Postgres to ClickHouse (Standalone uses
  an in-process outbox, `FI_CDC_MODE=outbox`);
- per-queue Temporal workers and queue routing;
- the Kafka-backed observed-attribute catalog;
- the voice simulation runner (`worker-simulation-runner`);
- the gRPC server (port `50051`);
- `docker-compose.distributed.yml` or the production overlay themselves.

```bash
./bin/dev --distributed
```

The first run installs the Distributed setup from source, as above. The
overlay ([`docker-compose.distributed.dev.yml`](../docker-compose.distributed.dev.yml))
then builds `futureagi/future-agi:dev` and the Vite frontend from your
checkout; the gateway, fi-collector, serving and code-executor keep the images
from the install. It runs every per-queue worker instead of the single
all-queue `worker`, publishes the database ports (above), puts an API proxy on
port 8000 that also routes `/v1/traces` to the collector, and never migrates on
start. The API reloads with granian. The Temporal workers restart through
`watchfiles` on changes under `tfc`, `accounts`, `analytics`, `model_hub`,
`sockets`, `tracer`, `usage`, `utils`, `simulate` and `agent_playground`; after
a change elsewhere, restart them (`docker compose -f docker-compose.distributed.yml -f docker-compose.distributed.dev.yml restart worker-default`,
or `./bin/dev --distributed down && ./bin/dev --distributed`).

The optional `local-sandbox` profile adds a local server for hosted agent
runs; it needs `ALK_HOST_WORKSPACE_ROOT`, `FI_API_KEY` and `FI_SECRET_KEY`
(see [docs/configuration.md](configuration.md#development-overlays-bindev)).
When one is missing, its `alk-harness-sandbox-check` step stops that profile
and names the missing settings; commands without the profile do not need
them.

## Troubleshooting

**The API does not reload when I save.** `./bin/dev logs` should show
`[start] development mode: ...` when the container starts and a reload after
each save. No such line: the stack was started without the overlay (a plain
`docker compose up`); run `./bin/dev`. The line is there but nothing reloads:
the file is outside the watched directories (see
[What reloads](#what-reloads-and-what-needs-a-rebuild)).

**The API reloads over and over.** Something keeps writing into a watched
directory, typically a script or test that writes output under `futureagi/`.
Write it elsewhere, or under `logs/`, `media/` or `static/`, which are ignored.

**The UI does not update.** `./bin/dev logs frontend` shows Vite's errors.
After a dependency change, run `./bin/dev rebuild frontend`, which also
replaces the `node_modules` volume. Distributed keeps `node_modules` in an
anonymous volume: after `./bin/dev --distributed rebuild`, add
`docker compose -f docker-compose.distributed.yml -f docker-compose.distributed.dev.yml up -d --renew-anon-volumes frontend`.

**`futureagi/platform:local` or `futureagi/frontend:dev` not found.** Development
images are built, never pulled. Run `./bin/dev rebuild`.

**The first build runs out of memory or is killed.** Give Docker 8 GB (see
[Requirements](#requirements)) and run `./bin/dev` again; finished images come
from the build cache.

**A port is already in use.** Another stack holds it, often a normal install of
the same checkout. Stop that one, or move the ports in `.env` (`FRONTEND_PORT`,
`BACKEND_PORT`, ...; see the [ports reference](../INSTALLATION.md#ports-reference))
and run `./bin/dev` again.

**Switching between Standalone and Distributed.** The two setups keep their
data differently, and one setup cannot start on the other's data. Either delete
the data first (`./bin/dev down -v`, then `./bin/dev --distributed`), or keep
both in separate checkouts with different `COMPOSE_PROJECT_NAME` values and
ports in their `.env` files.

**Distributed stops at `postgres-schema-bootstrap` after a `git pull`.** The
pull brought migrations. Apply them as shown in
[Database changes](#database-changes).

**Where is everything else?** Settings: [docs/configuration.md](configuration.md).
Images and build arguments: [docs/images.md](images.md). The install itself
and the setup checks: [INSTALLATION.md](../INSTALLATION.md#troubleshooting).
