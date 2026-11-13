# Attachments: deployment notes

Everything here is inert while `FI_ATTACHMENTS_ENABLED` is not `true`.
Nothing in this file implies a Cloud rollout.

## Turning it on

Set `FI_ATTACHMENTS_ENABLED=true` where the backend and workers read it; the
fi-collector follows the same switch (it maps an untyped LiveKit call root to
a Voice `conversation` only when it is on):

- Docker Compose (standalone): in `.env`. The collector runs in the `app`
  container and inherits it.
- Docker Compose (distributed, e2e): in `.env`; the `fi-collector` service
  reads `${FI_ATTACHMENTS_ENABLED}` from the same file. With the production
  overlay (`--env-file deploy/.env.production`), compose interpolates the
  collector's value from that file while the backend still reads `.env`: set
  it in both.
- Helm: `config.extraEnv` (or `backend.extraEnv`). The collector takes the
  backend's value; `fiCollector.extraEnv` overrides it.

Binding reads a parent span only if it started at most
`FI_ATTACHMENTS_BIND_LOOKBACK_HOURS` (default 48) before its upload, so a late
attach or import to an older trace needs a wider lookback.

## Lifecycle schedules

Three Temporal schedules (`tfc/temporal/schedules/attachment_lifecycle.py`)
exist only when `FI_ATTACHMENTS_ENABLED=true`. They run on the existing
`tasks_s` queue; no new queue, pod or Compose service is needed.

| Schedule | Interval | Work per tick |
|---|---|---|
| `attachment-outbox-drain` | 15 s | outbox rows by kind: up to 8 `validate` (one sandbox probe each), 16 `erase`, 100 `settled`, 100 `admitted` |
| `attachment-lifecycle-sweep` | 60 s | expire reservations; per scope, expire occurrences and (for fenced scopes) tombstone the old generation; purge expired media sessions, playback grants and old audit rows |
| `attachment-orphan-sweep` | 600 s | up to 200 bound occurrences, least recently checked first: tombstone those whose parent span is gone from ClickHouse on two sweeps at least the orphan grace apart (see below) |

Without them nothing drains the outbox: streamed uploads stay `validating`,
the budget rows never release (every tenant then gets 429 at 128 in-flight
uploads) and erased bytes stay in the buckets. Every outbox `event` kind has
exactly one consumer (`lifecycle.CONSUMERS`); a test fails if a kind is added
without one.

Each row and each scope is isolated: a failing one is logged by reason code
and pushed back (60 s on error, 300 s when an erase still has a live
occurrence), and never stalls the rest. If the private storage is not
configured, the `validate` and `erase` rows stay queued (the tick logs
`validate_skipped_<reason>`); they are never settled against a missing store.

### The validate drain needs the sandbox on `tasks_s`

`validate` runs `sandbox_probe` (bubblewrap, unprivileged user namespaces).
There is no in-process fallback: on a worker without it every upload settles
`failed` after 5 attempts. The `tasks_s` worker image needs `bwrap`, and the
host must allow unprivileged user namespaces (on Ubuntu 24.04:
`kernel.apparmor_restrict_unprivileged_userns=0`).

### Tuning

`FI_ATTACHMENTS_AUDIT_RETENTION_DAYS` (default 400) is how long
`AttachmentAuditEvent` rows are kept. Media sessions and playback grants are
deleted one hour after they expire or are revoked.

## Deletion

- Trace and trace-session deletion (`views/trace.py`,
  `views/trace_session.py`) tombstone the traces' occurrences in the same
  request; the erase drain then removes the bytes.
- Project and workspace deletion call `fence_scope`: reads are denied at once,
  and the lifecycle sweep tombstones the scope's occurrences.
- A hook failure never fails the product's deletion. The orphan sweep is the
  backstop, and it is also the only path for deletions that touch ClickHouse
  alone: TTL expiry and the enterprise retention activities
  (`ee.cloud.tasks.retention`, not in this repository) do not call a hook.
- The orphan sweep never tombstones on one read. ClickHouse is eventually
  consistent (replica lag, a partially restored replica), and a tombstone is
  irreversible and erases the bytes. So the sweep skips occurrences bound less
  than `FI_ATTACHMENTS_ORPHAN_GRACE_HOURS` ago (default 24, minimum 1), stamps
  `source_missing_since` on the first miss, clears it on any later live read,
  and tombstones only on a miss at least the grace after the first one. It
  also keeps any occurrence whose PostgreSQL Trace or ObservationSpan is still
  present and not deleted (in every edition). An occurrence whose span is gone
  is therefore erased one to two grace periods plus one sweep cycle after the
  deletion; its `source_expires_at` still bounds it.
- Deleting a user deletes their media sessions and playback grants; write
  grants they created keep `granted_by = NULL`. Audit rows keep the bare actor
  UUID.

## Bucket lifecycle backstop

The application never changes bucket policy or lifecycle; the deployment owns
it. Add these rules as a backstop for erase work that is lost or delayed:

- Quarantine bucket: expire current objects after 2 days, delete noncurrent
  versions after 1 day and abort incomplete multipart uploads after 1 day.
  Quarantine objects are only read during validation, which finishes within
  the 30-minute upload lifetime.
- Serving bucket: delete noncurrent versions and expired delete markers after
  7 days, and abort incomplete multipart uploads after 1 day.

Retention caveat: do NOT put an age-based expiry on CURRENT versions in the
serving bucket unless it is at least the longest trace retention of any
organization (unlimited retention means never). A current-version expiry
deletes live, still-referenced media regardless of the organization's policy;
the erase outbox is what implements per-organization retention.

## Database fences (0133)

`0133_attachment_fences` installs plpgsql triggers. Their policy tuple
(`enabled`, `require_media_redaction`, `allow_unredacted_media_with_text_pii`,
`record_audio`, `allow_import`), their identity array (including the
placement columns) and their state-transition table are literal SQL. Adding a
scope policy column, an immutable column or a state needs a follow-up RunSQL
migration that redefines the function, with its own `UPDATE_SAFETY`
declaration. A model change alone is not fenced.

`0132`'s deferred foreign keys take `SHARE ROW EXCLUSIVE` on `accounts_user`,
`accounts_organization`, `accounts_workspace`, `accounts_orgapikey` and
`tracer_project` for the length of that transaction. Run `migrate` with a
`lock_timeout` and retry on timeout.

## Recording imports

Completed-recording imports (`imports.py`) and their derivatives
(`derivatives.py`) run on the existing `tasks_xl` worker queue. No new queue,
pod or Compose service is needed. Two Temporal schedules
(`tfc/temporal/schedules/tracer.py`) drive them:

| Schedule | Interval | Work per tick |
|---|---|---|
| `run-attachment-recording-jobs` | 30 s | up to 2 import jobs, then up to 20 segments' derivatives (live, enabled segments only, oldest first) |
| `sweep-attachment-recordings` | 300 s | delete expired source envelopes; expire jobs past their deadline, erase their staged parts, release their reservations; release any finished job whose reservation is still held |

Jobs are durable database rows. A lost tick or a worker restart only delays
progress. Each job gets at most 3 attempts and a deadline of at most 3 hours.

Each schedule has the default `ScheduleOverlapPolicy.SKIP`. On top of that,
each job has a 15-minute lease (`imports.LEASE_SECONDS`): `run()` claims it
with a fresh token, renews it at every staged segment and re-checks it under
the job row lock before publication. A second worker skips a leased job, and a
worker that lost its lease (crash, pause, a tick started by hand) cannot
reserve, publish or finish; after the lease lapses the next tick retries.

A job is charged to the deployment row `global:imports` (full deployment caps:
8 GiB, 128 transfers, 64 recordings), plus its organization and project rows.
Imports do not use the upload shards `global:0..15`: a shard holds 1/16 of the
deployment caps (512 MiB), less than one valid import, and a long import on a
shard would throttle every upload hashed to it. A job holds the declared source
plus one window, one transfer, recording and validation slot, two objects and
one link from admission, plus the bytes, one object and one link of each staged
segment, until it finishes; the sweep releases any finished job whose release
failed. Derivatives are produced by the same bounded tick (at most 20 segments
per 30 s per worker, one at a time) and are not separately reserved.

### Opt-in

Imports are a per-project opt-in: `AttachmentScope.allow_import` (default
off). Admission needs it on the scope AND in the capture session's policy
snapshot, so switching it off denies live sessions and queued jobs at once.
`allow_import` is in the 0133 scope-fence policy tuple, so flipping it (either
way) must bump `policy_version` in the same update. Live capture sessions then
carry a stale policy version and the SDK has to bootstrap a new session before
it can import. While it is on, bootstrap advertises `RemoteUrl` in
`Policy.allowed_sources`; `ProviderFile` is advertised only once a provider
output reader is qualified (`provider_recordings.READERS`). `record_audio`
(creating new recordings) is a separate switch and never implies it.

When attachments are enabled, each recording tick first runs
`readiness.check()`. If any requirement below is missing, the tick does
nothing and logs `attachment_worker_not_ready` with a reason code. No media is
processed.

### Binding and deletion of imported segments

Each published segment is its own attachment with one occurrence, parented on
the import request's `association.parent` and unbound for 24 hours. The
binding worker binds it like any other occurrence once that parent span is in
ClickHouse within `FI_ATTACHMENTS_BIND_LOOKBACK_HOURS` of the import. An
imported segment has no upload row: its tombstone queues erase work keyed on
the attachment, and the erase drain removes the segment object
(`original/{attachment id}`, all versions) and its channel and peaks
derivatives, then repeats once 15 minutes after the tombstone
(`retention.ATTACHMENT_ERASE_HORIZON`) to catch a derivative that was being
produced at the moment of deletion.

### Required configuration (when enabled)

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

## Two media sandboxes

Two different isolation runtimes handle untrusted media; see `SANDBOX.md`.

- Upload validation (the `validate` drain on `tasks_s`, `validation.py`) runs
  in bubblewrap. It needs `bwrap` in the `tasks_s` image and unprivileged user
  namespaces on the host (see "The validate drain needs the sandbox on
  `tasks_s`" above).
- Recording imports and derivatives (`tasks_xl`, `sandbox.py` +
  `media_worker.py`) run in a stdlib-only child that installs its own
  seccomp-bpf filter, so the runtime only has to allow
  `prctl(PR_SET_SECCOMP)`. The Docker default profile and Kubernetes
  `RuntimeDefault` both do. No privileged mode, added capability or user
  namespace is needed. A runtime that blocks it fails readiness. The worker
  image's Python must be able to start with `-I -S`; no extra packages and no
  codec binaries are needed, because only canonical PCM WAV is segmented
  today.

## Gateway routing (for when the media gateway is mounted)

Media must be served from the same origin as the app, through the
authenticated gateway only, never straight from storage. A proxy in front of
`/tracer/v1/attachment-media/` must:

- pass `Range`, `If-Range` and `If-None-Match` through unchanged, and return
  206 responses unaltered;
- not buffer or cache: `proxy_buffering off`, `proxy_cache off`, and keep the
  upstream `Cache-Control: private, no-store`;
- not log query strings or cookies for that path.
