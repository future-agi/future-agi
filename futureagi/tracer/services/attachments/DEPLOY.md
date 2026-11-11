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

Each schedule has the default `ScheduleOverlapPolicy.SKIP`. On top of that,
each job has a 15-minute lease (`imports.LEASE_SECONDS`): `run()` claims it
with a fresh token, renews it at every staged segment and re-checks it under
the job row lock before publication. A second worker skips a leased job, and a
worker that lost its lease (crash, pause, a tick started by hand) cannot
reserve, publish or finish; after the lease lapses the next tick retries.

Imports are a per-project opt-in: `AttachmentScope.allow_import` (default
off). Admission needs it on the scope AND in the capture session's policy
snapshot, so switching it off denies live sessions and queued jobs at once.
Switching it ON is not immediate for existing sessions: 0132's scope fence
does not list `allow_import`, so the change does not force a
`policy_version` bump, and a live session keeps its `allow_import: false`
snapshot until it expires or the SDK bootstraps a new session.
`record_audio` (creating new recordings) is a separate switch and never
implies it. F01 `Policy` has no field for it yet, so bootstrap does not
advertise it.

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
