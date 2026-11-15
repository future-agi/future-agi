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

       python manage.py cutover_attachment_media_sessions --quiesced --issued-through "$RECORDED_CUTOFF"
       python manage.py cutover_attachment_media_sessions --quiesced --issued-through "$RECORDED_CUTOFF" --apply

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

Original AuthToken revocation/expiry is checked during resolve and every media
read, including stream rechecks. Refresh creates another access row; logout of
that new row does not revoke a pre-refresh row. Login-family revocation remains
a separate release/security decision, not something this cutover changes.
