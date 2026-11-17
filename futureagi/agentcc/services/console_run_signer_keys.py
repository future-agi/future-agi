"""The console-run signer's key registry (fi-security ruling 4).

Only the operator command calls add_key and revoke_key. The sign route reads
active_key; the run authorization route reads registered_deployment. Every
change is audit-logged with the key id, deployment and operator, never key
bytes.
"""

import base64
import re

import structlog
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.db import connection, transaction
from django.utils import timezone

from agentcc.models.console_run_signer_key import (
    DEPLOYMENT_PATTERN,
    KID_PATTERN,
    MAX_ACTIVE_PER_DEPLOYMENT,
    AgentccConsoleRunSignerKey,
)

audit = structlog.get_logger("agentcc.console_run.audit")


class SignerKeyRefused(ValueError):
    """An operator request the registry refuses; the message is fixed text."""


def _public_key_bytes(encoded):
    if not isinstance(encoded, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", encoded):
        raise SignerKeyRefused("The public key must be 32 bytes, base64url.")
    raw = base64.urlsafe_b64decode(encoded + "=")
    try:
        Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        raise SignerKeyRefused("The public key is not an Ed25519 key.") from None
    return raw


def _operator(operator):
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 128:
        raise SignerKeyRefused("Name the operator (at most 128 characters).")
    return operator.strip()


def add_key(*, deployment, kid, public_key, operator):
    if not re.fullmatch(DEPLOYMENT_PATTERN, deployment or ""):
        raise SignerKeyRefused("The deployment id must match [A-Za-z0-9._-]{1,64}.")
    if not re.fullmatch(KID_PATTERN, kid or ""):
        raise SignerKeyRefused("The key id must match [A-Za-z0-9._-]{1,64}.")
    raw = _public_key_bytes(public_key)
    operator = _operator(operator)
    with transaction.atomic():
        # One deployment's adds take turns, so two can never both see room.
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ["console-run-signer-key:" + deployment],
            )
        if AgentccConsoleRunSignerKey.objects.filter(kid=kid).exists():
            raise SignerKeyRefused("That key id is already registered.")
        active = AgentccConsoleRunSignerKey.objects.filter(
            deployment=deployment, revoked_at__isnull=True
        ).count()
        if active >= MAX_ACTIVE_PER_DEPLOYMENT:
            raise SignerKeyRefused(
                "This deployment already has two active keys; revoke one first."
            )
        row = AgentccConsoleRunSignerKey.objects.create(
            kid=kid, deployment=deployment, public_key=raw, created_by=operator
        )
    audit.info(
        "console_run_signer_key_added",
        kid=kid,
        deployment=deployment,
        operator=operator,
    )
    return row


def revoke_key(*, kid, operator):
    operator = _operator(operator)
    with transaction.atomic():
        row = (
            AgentccConsoleRunSignerKey.objects.select_for_update()
            .filter(kid=kid)
            .first()
        )
        if row is None:
            raise SignerKeyRefused("No key has that id.")
        if row.revoked_at is not None:
            raise SignerKeyRefused("That key is already revoked.")
        row.revoked_at = timezone.now()
        row.revoked_by = operator
        row.save(update_fields=["revoked_at", "revoked_by"])
    audit.info(
        "console_run_signer_key_revoked",
        kid=kid,
        deployment=row.deployment,
        operator=operator,
    )
    return row


def active_key(kid):
    """The Ed25519 public key registered and unrevoked under ``kid`` and its
    deployment, or None."""
    if not isinstance(kid, str) or not re.fullmatch(KID_PATTERN, kid):
        return None
    row = AgentccConsoleRunSignerKey.objects.filter(
        kid=kid, revoked_at__isnull=True
    ).first()
    if row is None:
        return None
    return Ed25519PublicKey.from_public_bytes(bytes(row.public_key)), row.deployment


def registered_deployment():
    """The one deployment with an active key, or None when there is none or
    more than one (the caller must then name it)."""
    deployments = set(
        AgentccConsoleRunSignerKey.objects.filter(revoked_at__isnull=True)
        .values_list("deployment", flat=True)
        .distinct()[:2]
    )
    return deployments.pop() if len(deployments) == 1 else None
