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

## Occurrence expiry

- An occurrence starts with a 24 h deadline: ingestion must observe its
  parent span and bind it within that window, or the lifecycle sweep expires
  it and the erase drain removes its bytes.
- Binding replaces that deadline with the source's retention
  (`retention.bind_retention`: `expires_at = source.expires_at`), so bound
  media plays and is kept for as long as its trace is retained, not 24 h.
  Binding never extends a known source expiry; a rebind with a different one
  is refused.

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
