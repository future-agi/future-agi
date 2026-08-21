# The harness server

The HTTP surface over the harness: one FastAPI app streaming the builder's
typed events as SSE and serving the artifacts the phases write. The app lives
in the package at `src/harness/ui/app.py`; `ui/server.py` is a launcher shim so
`python ui/server.py` (the Dockerfile CMD) keeps working from a bare checkout.

There is no bundled page. The platform frontend
(`frontend/src/sections/al-environment/`) is the renderer, reached through the
backend proxy at `/simulate/harness/<path>` → this server's `/api/<path>`.

## Run it

    pip install -e ".[ui,postgres]"
    python ui/server.py            # HARNESS_HOST / HARNESS_PORT to bind elsewhere

A standalone run needs `HARNESS_AUTH_DISABLED=1` or an `INTERNAL_API_SECRET`
matching the backend's, or startup refuses to boot.

In the platform stack it runs as the `harness` service in
`docker-compose.dev.yml`; the backend is the only thing that talks to it, and
the compose file sets `INTERNAL_API_SECRET` for both.

## Test it

    bin/test-harness               # from futureagi/ — the harness's own lane

## Known limits (v0, being rebuilt — see the Harness Rebuild Plan)

- Single-user: session state is one module-level global with one process lock.
- All state is files under `artifacts/` on the service's working directory.
- `GET /api/scenarios` re-proves gates for sqlite/in-process worlds and reports
  container-store worlds as validated without re-proving.
