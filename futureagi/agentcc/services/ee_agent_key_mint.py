"""Idempotent agent-key mints for Omega EE (CN22d; AM-C27).

A mint whose answer EE lost (a dropped connection, EE's 15 s timeout, a 502
or 504 after the key was made) must never leave a key nobody holds. EE
stores a UUIDv7 before each mint and sends it as the ``Idempotency-Key``
header, and sends the same one when it retries that mint:

- The first mint makes the key and, in the same transaction under the org
  lock, a record keyed by (org, EE service credential, SHA-256 of the
  Idempotency-Key). The record holds only hashes (of the Idempotency-Key and
  of the request body) and lives 24 hours (``WINDOW``); each mint of the org
  deletes its expired records, and a lookup never sees one.
- A retry with the same body is answered 409 ``agent_key_mint_replayed``
  with the key's id, its created_at and expires_at and whether it is
  revoked: never the key, which only its first answer carried. The same
  Idempotency-Key with another body (another workspace) is 422
  ``idempotency_key_reused``. A missing or malformed one is 400
  ``idempotency_key_invalid``.
- The binding keeps the Idempotency-Key itself (an identifier EE generated,
  not a secret), so the workspace's key list carries it and EE can find a key
  by it after a lost answer (``?mint_idempotency_key=``), revoke it, and mint
  with a new one. A record is per service credential: after a rotation the
  same Idempotency-Key mints again, and the workspace's list shows both keys.
"""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from agentcc.models import AgentccAPIKey
from agentcc.models.ee_agent_key_mint import AgentccEEAgentKeyMintRecord
from agentcc.models.mcp_service_provisioning import AgentccEEAgentKey

HEADER = "Idempotency-Key"
QUERY_PARAM = "mint_idempotency_key"
WINDOW = timedelta(hours=24)

CODE_KEY_INVALID = "idempotency_key_invalid"
CODE_REPLAYED = "agent_key_mint_replayed"
CODE_REUSED = "idempotency_key_reused"

AUDIT_MINT_REPLAYED = "agentcc.ee_agent_key.mint_replayed"

# A UUIDv7 (RFC 9562): version 7 and the RFC variant, compared lower-case.
UUID7 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

KEY_MISSING = (
    "Send an Idempotency-Key header: a UUIDv7 that EE stores before the mint "
    "and sends again when it retries the same mint."
)
KEY_MALFORMED = (
    "The Idempotency-Key header must be a UUIDv7 (RFC 9562: version 7, the "
    "RFC variant), written as 8-4-4-4-12 hex digits."
)
QUERY_MALFORMED = "Must be a UUIDv7: the Idempotency-Key EE sent with the mint."
REPLAYED = (
    "A mint with this Idempotency-Key already made the agent key named here. "
    "Its key was in that mint's answer and is never shown again: revoke this "
    "agent key, then mint with a new Idempotency-Key."
)
REUSED = (
    "This Idempotency-Key was sent in the last 24 hours with a different mint "
    "request. Send a new Idempotency-Key for a new mint."
)


class InvalidKey(ValueError):
    """The Idempotency-Key header is missing or is not a UUIDv7."""


def normalize(value) -> str | None:
    """The UUIDv7 ``value`` names, lower-case, or None. HTTP's optional
    whitespace around a field value is not part of it."""
    if not isinstance(value, str):
        return None
    value = value.strip(" \t").lower()
    return value if UUID7.fullmatch(value) else None


def parse_key(raw) -> str:
    """The mint's Idempotency-Key from its header value, lower-case."""
    if raw is None or not str(raw).strip(" \t"):
        raise InvalidKey(KEY_MISSING)
    key = normalize(raw)
    if key is None:
        raise InvalidKey(KEY_MALFORMED)
    return key


def key_sha256(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def request_sha256(fields: dict) -> str:
    """The hash of a mint request: its validated fields as canonical JSON.
    Any field a later mint request adds changes it, so the same key with
    another label or scope is a reuse too."""
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class Prior:
    """A live record for the Idempotency-Key a mint was sent with."""

    record: AgentccEEAgentKeyMintRecord
    same_request: bool


def claim(org, credential, key: str, request_hash: str, now: datetime) -> Prior | None:
    """Call under the org lock, before anything is made. Deletes the org's
    expired records, then returns the live record of ``key`` under this
    service credential, or None when the mint should go ahead. A replay (the
    same request) is counted on the record, in the caller's transaction."""
    purge_expired(org, now)
    record = (
        AgentccEEAgentKeyMintRecord.objects.select_related("agent_key__api_key")
        .filter(
            organization=org,
            service_credential=credential,
            key_sha256=key_sha256(key),
            expires_at__gt=now,
        )
        .first()
    )
    if record is None:
        return None
    same = hmac.compare_digest(record.request_sha256, request_hash)
    if same:
        record.replays += 1
        record.last_replayed_at = now
        record.save(update_fields=["replays", "last_replayed_at"])
    return Prior(record, same)


def remember(
    binding: AgentccEEAgentKey, credential, key: str, request_hash: str, now: datetime
) -> AgentccEEAgentKeyMintRecord:
    """The record of a new mint, written in the mint's transaction."""
    return AgentccEEAgentKeyMintRecord.objects.create(
        organization_id=binding.organization_id,
        workspace_id=binding.workspace_id,
        service_credential=credential,
        key_sha256=key_sha256(key),
        request_sha256=request_hash,
        agent_key=binding,
        created_at=now,
        expires_at=now + WINDOW,
    )


def purge_expired(org, now: datetime) -> int:
    """Delete the org's records past their 24 hours (the lazy purge)."""
    deleted, _ = AgentccEEAgentKeyMintRecord.objects.filter(
        organization=org, expires_at__lte=now
    ).delete()
    return deleted


def replayed_fields(binding: AgentccEEAgentKey) -> dict:
    """What a replay names: the key's id, when it was made, when it expires,
    and whether it is revoked. Never the key."""
    key = binding.api_key
    return {
        "agent_key_id": key.gateway_key_id,
        "created_at": key.created_at,
        "expires_at": key.expires_at,
        "revoked": key.status == AgentccAPIKey.REVOKED or key.deleted,
    }


def was_replayed(binding: AgentccEEAgentKey) -> bool:
    """Whether a retry of this key's mint was answered with a replay: EE
    revoking it is then the reconcile after a lost answer."""
    return AgentccEEAgentKeyMintRecord.objects.filter(
        agent_key=binding, replays__gt=0
    ).exists()
