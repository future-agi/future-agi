# fi-collector — Deployment Modes

fi-collector is a single Go binary (~22 MB distroless). It runs in three deployment shapes from the same image. Pick the one that matches your control-plane:

1. **Standalone** — its own pod / docker container. Ingests OTLP for the whole org.
2. **Embedded sidecar** — runs in the same Kubernetes pod as the Django backend, sharing localhost. Lowest latency from app → collector → CH; tied to backend pod lifecycle.
3. **Supervised subprocess** — Django launches and supervises the collector process. Useful when you want one-binary deploys and don't run Kubernetes (single-host VMs, docker compose, local dev).

The Go binary is the same across all three modes. The deployment style is what changes.

---

## 1. Standalone (default — recommended for prod)

The default mode shipped in `docker-compose.standalone.yml`. Two pods per region, behind the existing LB on `:4317`.

```bash
docker compose -f docker-compose.standalone.yml up --build
```

Pros: independent scaling, independent failure domain. Cons: one extra deploy artifact.

---

## 2. Embedded sidecar (Kubernetes)

Same image, runs as a sidecar container in the Django backend pod. SDKs in the app process can target `localhost:4317`, getting localhost-loopback latency instead of a network hop.

Helm chart fragment for the existing `web` deployment:

```yaml
# infra/helm/web/templates/deployment.yaml
spec:
  template:
    spec:
      containers:
        - name: web
          image: ghcr.io/future-agi/web:{{ .Values.image.tag }}
          env:
            # Django's internal OTel SDK points at the sidecar
            - name: OTEL_EXPORTER_OTLP_ENDPOINT
              value: "http://127.0.0.1:4317"
          # ... rest of web container spec ...

        - name: fi-collector
          image: ghcr.io/future-agi/fi-collector:{{ .Values.ficollector.tag }}
          env:
            - name: FI_CH_URL
              value: "http://clickhouse-lb.fi.internal:8123"
            - name: FI_GRPC_ADDR
              value: ":4317"
            - name: FI_ADMIN_ADDR
              value: ":9464"
          ports:
            - containerPort: 4317
              name: otlp
            - containerPort: 9464
              name: admin
          resources:
            requests:
              cpu: 100m
              memory: 64Mi
            limits:
              cpu: 1
              memory: 512Mi
          # The dead-letter queue must survive pod restarts. Use an
          # emptyDir for ephemeral durability or a persistent volume
          # for stronger guarantees.
          volumeMounts:
            - mountPath: /var/lib/fi-collector
              name: fi-collector-dl
      volumes:
        - name: fi-collector-dl
          emptyDir: { sizeLimit: 1Gi }
```

Pros: same lifecycle, localhost-loopback latency, single deploy unit. Cons: scaling is coupled (more web pods → more collectors); restarts of either container restart the pod.

---

## 3. Supervised subprocess (Django launches the collector)

For single-host / non-Kubernetes deployments, or for local dev where you want one process tree. The `supervisor.py` shim wraps the binary in a child process the Django startup can manage:

```python
# In tfc/asgi.py or wherever app boot lives:
from fi_collector_supervisor import start as start_fi_collector

if settings.FI_COLLECTOR_EMBED_MODE == "subprocess":
    start_fi_collector(
        binary_path="/usr/local/bin/fi-collector",
        ch_url=settings.CH_HTTP_URL,
        grpc_addr="127.0.0.1:4317",
        dead_letter_dir="/var/lib/fi-collector",
    )
```

The supervisor:

- Launches `fi-collector` as a child process with the resolved env.
- Streams stdout/stderr through Django's logger so log aggregation works unchanged.
- Restarts on non-zero exit with exponential backoff (capped at 30 s).
- Cleans up on Django shutdown via `atexit`.

Drop the supervisor file in your Django repo (`utils/fi_collector_supervisor.py`); see the example at the bottom of this doc.

Pros: one binary to ship; no k8s required. Cons: collector and Django share a process tree — an OOM-killer that kills Django takes the collector with it.

---

## When to pick which

| Mode                  | Use if                                                               | Avoid if                                         |
| --------------------- | -------------------------------------------------------------------- | ------------------------------------------------ |
| Standalone            | Prod with HPA, multiple-tenant orgs, want independent failure domain | Single-host VM, want minimum moving parts        |
| Sidecar               | Prod K8s, latency-sensitive paths, want localhost loopback           | Web/collector capacity needs scale independently |
| Supervised subprocess | Single-host VM, docker-compose, local dev                            | Prod K8s (use sidecar instead)                   |

All three use the same `fi-collector` binary built from `cmd/fi-collector/main.go`. No code change required to switch — only deployment manifests.

---

## Reference: minimal `fi_collector_supervisor.py`

This file lives in the Django repo (not in this Go module). Drop it at
`tracer/utils/fi_collector_supervisor.py` and import from app startup.

```python
"""Supervise the fi-collector child process from Django app boot.

Why we don't use systemd / kubectl: this is for single-host setups and
local dev where the operator wants one process to manage. In K8s, use the
sidecar pattern from EMBEDDED.md instead.
"""
import atexit
import logging
import os
import signal
import subprocess
import threading
import time

log = logging.getLogger(__name__)
_proc: subprocess.Popen | None = None
_stop = threading.Event()


def start(*, binary_path: str, ch_url: str, grpc_addr: str = ":4317",
          dead_letter_dir: str = "/var/lib/fi-collector",
          restart_backoff_max_sec: float = 30.0) -> None:
    """Launch fi-collector as a child process; restart on crash with
    capped exponential backoff. Idempotent — safe to call twice.
    """
    global _proc
    if _proc is not None and _proc.poll() is None:
        return  # already running

    env = os.environ.copy()
    env.update({
        "FI_CH_URL": ch_url,
        "FI_GRPC_ADDR": grpc_addr,
        "FI_DEAD_LETTER_FILE": os.path.join(dead_letter_dir, "dead_letter.jsonl"),
    })

    def _supervise():
        backoff = 0.5
        while not _stop.is_set():
            log.info("fi-collector starting", extra={"binary": binary_path})
            proc = subprocess.Popen(
                [binary_path],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            globals()["_proc"] = proc

            # Pipe child output into Django's logger
            t = threading.Thread(target=_stream, args=(proc,), daemon=True)
            t.start()

            rc = proc.wait()
            if _stop.is_set():
                return
            log.warning("fi-collector exited",
                        extra={"rc": rc, "next_restart_sec": backoff})
            time.sleep(backoff)
            backoff = min(backoff * 2, restart_backoff_max_sec)

    threading.Thread(target=_supervise, daemon=True).start()
    atexit.register(stop)


def stop() -> None:
    _stop.set()
    if _proc and _proc.poll() is None:
        _proc.send_signal(signal.SIGTERM)
        try:
            _proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _proc.kill()


def _stream(proc: subprocess.Popen) -> None:
    if not proc.stdout:
        return
    for line in proc.stdout:
        log.info("fi-collector", extra={"line": line.decode(errors="replace").rstrip()})
```

This is ~80 lines of glue. The `restart_backoff_max_sec` keeps a thrashing
collector from saturating CPU. The `atexit.register(stop)` makes sure the
collector exits cleanly when Django shuts down.
