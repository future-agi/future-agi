# Attachment recording workers: deployment notes

Everything here is inert while `FI_ATTACHMENTS_ENABLED` is not `true`.
Nothing in this file implies a Cloud rollout.

## Worker queue and schedules

The work runs on the existing `tasks_xl` worker queue. No new queue, pod or
Compose service is needed. Two Temporal schedules
(`tfc/temporal/schedules/tracer.py`) drive it:

| Schedule | Interval | Work per tick |
|---|---|---|
| `run-attachment-recording-jobs` | 30 s | up to 2 import jobs, then up to 20 segments' derivatives |
| `sweep-attachment-recordings` | 300 s | delete expired source envelopes; expire jobs past their deadline, erase their staged parts, release their reservations |

Jobs are durable database rows. A lost tick or a worker restart only delays
progress. Each job gets at most 3 attempts and a deadline of at most 3 hours.

When attachments are enabled, each tick first runs `readiness.check()`. If any
requirement below is missing, the tick does nothing and logs
`attachment_worker_not_ready` with a reason code. No media is processed.

## Required configuration (when enabled)

| Variable | Purpose |
|---|---|
| `FI_ATTACHMENTS_ENVELOPE_KEYS` | `id:base64url(32 bytes)[,id:key...]`. The first key seals and every key can open, which allows rotation. Keep it in a secret, never in plain values. It is never `SECRET_KEY`. |
| `FI_ATTACHMENTS_IMPORT_HOSTS` | Comma-separated hosts (or parent domains) the importer may fetch from. Empty denies every remote source. |
| `FI_ATTACHMENTS_WORK_DIR` | Private, non-shared scratch directory. Size it for each concurrent job's source snapshot plus one window, at most 1 GiB + 8 MiB per job, with up to 2 jobs per tick per worker. |
| `FI_ATTACHMENTS_TRANSFER_SECONDS` | Per-range timeout (default 120). |
| `FI_ATTACHMENTS_IMPORT_BYTES_PER_SECOND` | Importer egress token bucket (default 8 MiB/s). |
| `FI_ATTACHMENTS_STORAGE_*` | The private storage from the backend-core lane (DPL01). |

With Compose these pass through `env_file`. With Helm, put the plain values in
`config.extraEnv` and the keys in the chart's secret-backed environment.

## Runtime requirements

- The decoder sandbox (`SANDBOX.md`) installs its own seccomp-bpf filter, so
  the runtime only has to allow `prctl(PR_SET_SECCOMP)`. The Docker default
  profile and Kubernetes `RuntimeDefault` both do. No privileged mode, added
  capability or user namespace is needed. A runtime that blocks it fails
  readiness.
- The worker image's Python must be able to start with `-I -S`. No extra
  packages and no codec binaries are needed: only canonical PCM WAV is
  segmented today.

## Gateway routing (for when the media gateway is mounted)

Media must be served from the same origin as the app, through the
authenticated gateway only, never straight from storage. A proxy in front of
`/tracer/v1/attachment-media/` must:

- pass `Range`, `If-Range` and `If-None-Match` through unchanged, and return
  206 responses unaltered;
- not buffer or cache: `proxy_buffering off`, `proxy_cache off`, and keep the
  upstream `Cache-Control: private, no-store`;
- not log query strings or cookies for that path.
