Media-session wire cutover
==========================

This is an unreleased, gated protocol replacement, not a rolling compatibility
mode. It changes neither application login/logout policy nor token families.
Do not enable the backend until the matching client is available. The procedure
below is an operator action; deploying or restarting code does not run it.

1. Disable ``FI_ATTACHMENTS_ENABLED`` on **all** API and lifecycle workers.
   Drain in-flight exchange/resolve requests and terminate/drain existing media
   responses. Stop old workers from accepting further requests; verify that no
   legacy issuer remains. A local settings check cannot prove fleet quiescence.
2. Record a timezone-aware UTC cutoff **after** the last legacy exchange has
   committed. Keep this exact timestamp in the private rollout record. Confirm
   worker/database clock synchronization. Do not recalculate it on retries or
   restarts. New v2 issuance must start strictly after this cutoff.
3. With the feature still disabled, run a dry run, review the count, then apply::

       SERVICE_TYPE=bootstrap STARTUP_DB_MUTATION_MODE=operator python manage.py cutover_attachment_media_sessions --quiesced --issued-through "$RECORDED_CUTOFF"
       SERVICE_TYPE=bootstrap STARTUP_DB_MUTATION_MODE=operator python manage.py cutover_attachment_media_sessions --quiesced --issued-through "$RECORDED_CUTOFF" --apply

   Use the existing dedicated operator/bootstrap execution mode; ordinary API
   and worker processes must continue to refuse this command. Do not disable
   the mutation-free startup guard or enable arbitrary shell execution.

   The command updates only ``AttachmentMediaSession.revoked_at`` for rows
   created at or before the recorded cutoff. Existing grant foreign keys make
   those credentials unusable, including legacy cookies renamed to v2. It never
   deactivates an AuthToken or unrelated application session. Repeating with the
   same cutoff is idempotent; it cannot revoke later v2 sessions. Never use this
   command as a recurring job or startup action.
4. Verify zero unrevoked rows at/before that cutoff with a read-only database
   count. Deploy the coordinated backend and client while still disabled.
   Confirm the old two-identity gateway route is absent, missing handles deny,
   and the client consumes its own exchange's non-secret handle and expiry.
5. Reload old clients and enable the feature. Perform an authorized smoke check
   with two independently bound sessions and verify both work. Keep old parent
   ``fi_media_session`` cookies ignored until normal expiry; do not clear other
   tabs' v2 cookies. Do not log Cookie, Authorization, Set-Cookie or grant values.
6. On failure, disable/quiesce again. Do not reactivate legacy rows, restore the
   old route or old issuance, or use the legacy name as a fallback. A rollback
   that restores the old wire protocol must stay disabled until a separately
   reviewed cutover. Reactivation/restarts of v2 alone must not revoke sessions.

Security and admission
----------------------

Exchange and resolve require explicit application bearer authentication and
exact configured HTTPS Origin. These adapters do not authenticate application
cookies or enforce a Django CSRF token. Gateway reads use only the HttpOnly
``fi_media_session_v2`` cookie at the session-specific path and require equality
between route session, cookie-derived row and grant session. Duplicate v2 cookie
names fail closed; the HTTP Cookie header cannot reveal an individual cookie's
Path, so legacy-row revocation is essential, not just renaming the cookie.

Each original access row and organization may exchange at most 60 times per
fixed minute using shared-cache atomic counters (cache failures deny issuance).
This bounds churn, not concurrent players; it never revokes predecessors or
extends session/grant deadlines. Clients should reuse a usable handle within a
captured bearer/organization context instead of exchanging for every resolve.

With ``FI_ATTACHMENTS_ENABLED=true``, startup rejects unsupported admission
cache configurations **before** storage readiness. The supported configuration
is ``CACHES['default']`` using ``django_redis.cache.RedisCache``,
``django_redis.client.DefaultClient`` (explicit or its default), and one
``redis://`` or ``rediss://`` writer URL. All API workers must share that Redis
database, ``KEY_PREFIX`` and ``VERSION``; keep worker clocks synchronized and
retain counter keys for their 120-second TTL (no eviction/flush). API process
restarts do not reset the allowance; losing Redis counter state does, so this
is not a durable accounting/quota guarantee across Redis data loss.

Use the standard ``django_redis.pool.ConnectionFactory`` and default key
function. Supported ``OPTIONS`` are ``CLIENT_CLASS``, ``SOCKET_CONNECT_TIMEOUT``,
``SOCKET_TIMEOUT``, ``PASSWORD`` and ``IGNORE_EXCEPTIONS`` (only false). Global
``DJANGO_REDIS_IGNORE_EXCEPTIONS`` must also be false unless explicitly
overridden to false for this cache. Custom clients, pools, factories, key
functions and other options are not qualified; neither are multi-location
configurations, LocMem, Dummy or other cache backend implementations.
The existing shared-Redis default satisfies this condition.

The configuration check is static, performs no cache network I/O, and does not
probe fleet topology. Operators must verify the shared writer/namespace before
activation. Runtime counter failures still deny issuance with ``429``; no
local or database fallback is used. With attachments OFF, startup does not
inspect or contact the admission cache, so ``DJANGO_CACHE_BACKEND=locmem``
remains valid. Changing the feature/cache settings requires restarting workers;
there is no supported hot activation that bypasses startup readiness.

Original AuthToken revocation/expiry is checked during resolve and every media
read, including stream rechecks. Refresh creates another access row; logout of
that new row does not revoke a pre-refresh row. Login-family revocation remains
a separate release/security decision, not something this cutover changes.
