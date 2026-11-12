"""Claim-only issuance, deterministic redaction, and transactional revocation."""

import redis
import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models.service_grant import BatchCancellationIntent, ServiceGrant
from accounts.services.envelope_keys import current_kid, pair_keys
from accounts.services.envelope_principals import live_person
from tfc.routers import primary_required
from tfc.service_envelope import KINDS, REGISTRY_FIELDS, derive_grant, issue_grant

logger = structlog.get_logger(__name__)


@primary_required
def issue_person_grant(*, kind, org, user, workspace, sub, ttl, identifier=None):
    from accounts.services.hard_erase_lifecycle import identity_keys, lifecycle_lock

    # Preserve the caller's attempt/batch atomicity requirement. The lifecycle
    # context itself opens a transaction, so this check must happen before it.
    if KINDS[kind][4] and not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Registered grants require the attempt or batch transaction")
    keys = identity_keys(organizations=[org], people=[user])
    with lifecycle_lock("default", keys=keys):
        live_person(str(org), str(user), str(workspace or "-"))
        token, claims = issue_grant(
            keys=pair_keys("fa", KINDS[kind][0]),
            kid=current_kid(),
            kind=kind,
            org=org,
            user=user,
            workspace=workspace,
            sub=sub,
            ttl=ttl,
            identifier=identifier,
        )
        row = None
        if KINDS[kind][4]:
            values = {f: claims[f] for f in REGISTRY_FIELDS}
            for f in ("workspace", "user"):
                if values[f] == "-":
                    values[f] = None
            if kind == "sandbox":
                _, agent, attempt = sub.split(":")
                values.update(agent_id=agent, attempt_id=attempt)
            if kind == "batch":
                values["batch_ref"] = sub.split(":")[1]
            row = ServiceGrant.objects.create(jti=claims["id"], **values)
        return token, claims, row


def publish_grant_revocation(jti):
    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2
        )
        try:
            client.publish("fi:auth:revoke", "grant:" + jti)
        finally:
            client.close()
    except Exception:
        logger.warning("grant_invalidation_pending", grant_id=jti)


def revoke_grants(rows, *, reason, using="default"):
    from agentcc.models.batch_job import AgentccBatchJob

    now = timezone.now()
    with transaction.atomic(using=using):
        for row in (
            rows.using(using).select_for_update().filter(revoked_at__isnull=True)
        ):
            ServiceGrant.objects.using(using).filter(pk=row.pk).update(
                revoked_at=now, revoked_reason=reason
            )
            if row.kind == "batch":
                job = (
                    AgentccBatchJob.objects.using(using)
                    .filter(pk=row.batch_ref)
                    .first()
                )
                BatchCancellationIntent.objects.using(using).get_or_create(
                    grant_id=row.pk,
                    defaults={
                        "organization_id": row.org,
                        "next_attempt_at": now,
                        "gateway_batch_id": job.gateway_batch_id
                        if job and not job.gateway_batch_id.startswith("pending-")
                        else "",
                    },
                )
            transaction.on_commit(
                lambda jti=row.pk: publish_grant_revocation(jti),
                using=using,
                robust=True,
            )


def revoke_person_grants(user_id, org_id=None, workspace_id=None, *, using="default"):
    rows = ServiceGrant.objects.filter(user=user_id)
    if org_id is not None:
        rows = rows.filter(org=org_id)
    if workspace_id is not None:
        rows = rows.filter(workspace=workspace_id)
    revoke_grants(rows, reason="membership_ended", using=using)


@primary_required
def redaction_fragments(attempt_id):
    fragments = []
    # Retain expired and revoked rows: old logs can contain their credentials.
    for row in ServiceGrant.objects.filter(kind="sandbox", attempt_id=attempt_id):
        token = derive_grant(row.registry_row(), keys=pair_keys("fa", row.aud))
        _, canonical_segment, mac_segment = token.split(".")
        fragments.extend([token, canonical_segment, mac_segment, "fit_" + row.jti])
    return fragments
