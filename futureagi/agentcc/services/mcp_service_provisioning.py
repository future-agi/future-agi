"""Virtual-server provisioning for Omega EE (CN22a, agent mode).

Hosted Omega EE attaches one of its connections to a gateway virtual server,
so the gateway, not EE, holds the vendor's credentials and governs and meters
every call. The contract is ``omega-ee-attach/v1``
(``docs/gateway/connectors/omega-ee-attach.md``, section 3; the golden
fixtures are in ``agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach``).

- EE calls with its own service credential, one current per org (AM-C18 Q1):
  made for EE alone, accepted nowhere but this API, rotated and revoked on its
  own, and audited as EE. An org Owner or Admin issues it. Revoking it also
  revokes every agent key EE holds in the org, so EE is out for good.
- ``mint_agent_key`` makes the gateway virtual key EE calls a workspace's
  virtual servers with, and returns the raw key that once. It is idempotent
  per Idempotency-Key (CN22d, ``ee_agent_key_mint``): a retry of a mint whose
  answer EE lost names the key it made instead of making another.
- ``ensure`` gives a connection its virtual server, idempotently per (org,
  workspace, connection), through the org config writer; ``detach`` removes
  that one and nothing else.

User mode (each person's own account) needs the gateway's OAuth authorization
server for MCP clients (M24), so ensure refuses it until CN22b.

Every raw secret here (the service credential's, the agent key) is returned
to the caller once and is never logged, audited or stored in clear.
"""

import copy
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from accounts.models.audit_log import AuditScope
from accounts.models.workspace import Workspace
from agentcc.models import AgentccAPIKey, AgentccOrgConfig
from agentcc.models.mcp_service_provisioning import (
    AgentccEEAgentKey,
    AgentccEEServiceCredential,
    AgentccEEVirtualServer,
)
from agentcc.services import auth_bridge, config_push, ee_agent_key_policy
from agentcc.services import ee_agent_key_mint as mint_idempotency
from agentcc.services.ee_provisioning_availability import (
    provisioning_enabled,
    require_provisioning,
)
from agentcc.services.gateway_client import GatewayClientError, get_gateway_client
from agentcc.services.org_config_writer import (
    is_gateway_synced,
    lock_org,
    write_config_version_with,
)
from tfc.utils.audit import log_audit

logger = structlog.get_logger(__name__)

AGENT = AgentccEEVirtualServer.Mode.AGENT
USER = AgentccEEVirtualServer.Mode.USER
CredentialStatus = AgentccEEServiceCredential.Status

# The request rules of the contract's schema.json ($defs key_id,
# connection_id, servers, vs_id, uuid).
AGENT_KEY_ID = re.compile(r"^key_[0-9a-f]{24}$")
CONNECTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
VS_ID = re.compile(r"^vs_[0-9A-HJKMNP-TV-Z]{26}$")
WORKSPACE_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
MAX_SERVERS = 8

# The answer's url (schema.json $defs vs_url and non_public_url): the
# gateway's public HTTPS origin, its MCP endpoint, then /v/<vs_id>.
VS_URL = re.compile(
    r"^https://[a-z0-9-]+(\.[a-z0-9-]+)+(:[0-9]{1,5})?(/[A-Za-z0-9._~-]+)*"
    r"/v/vs_[0-9A-HJKMNP-TV-Z]{26}$"
)
VS_URL_MAX_LENGTH = 200
NON_PUBLIC_URL = re.compile(
    r"^https?://([^/:]*\.)?(0[xX][0-9a-fA-F]*|[0-9]+)([:/]|$)"
    r"|^https?://([^/:]*\.)?[0-9]{1,3}([.-][0-9]{1,3}){3}\."
    r"|^https?://([^/:]*\.)?(nip|sslip)\.io([:/]|$)"
    r"|^https?://[^/]*\.(local|localhost|localdomain|internal|home\.arpa)([:/]|$)"
    r"|^https?://[^/]+/(.*/)?\.\.?(/|$)"
    r"|^https?://[^/]+/.*//"
)

# The gateway's public URL, as the console shows it (views/gateway.py), and
# the path of its MCP endpoint (the gateway's mcp.endpoint, /mcp unless an
# operator moved it), which Django cannot read from the gateway.
PUBLIC_URL_ENV = "AGENTCC_GATEWAY_PUBLIC_URL"
FALLBACK_URL_ENV = "AGENTCC_GATEWAY_URL"
MCP_ENDPOINT_ENV = "AGENTCC_GATEWAY_MCP_ENDPOINT"
DEFAULT_MCP_ENDPOINT = "/mcp"

SERVICE_API_KEY_PREFIX = "ees_"
EE_OWNER = "omega-ee"
LAST_USED_RESOLUTION = timedelta(minutes=1)

AUDIT_CREDENTIAL_ISSUED = "agentcc.ee_service_credential.issued"
AUDIT_CREDENTIAL_ROTATED = "agentcc.ee_service_credential.rotated"
AUDIT_CREDENTIAL_REVOKED = "agentcc.ee_service_credential.revoked"
AUDIT_AGENT_KEY_MINTED = "agentcc.ee_agent_key.minted"
AUDIT_AGENT_KEY_REVOKED = "agentcc.ee_agent_key.revoked"
AUDIT_VS_ENSURED = "agentcc.ee_virtual_server.ensured"
AUDIT_VS_DETACHED = "agentcc.ee_virtual_server.detached"

AGENT_KEY_INVALID = (
    "The agent key must be an active key minted for this workspace through "
    "the provisioning API."
)
AGENT_KEY_OTHER_WORKSPACE = (
    "This agent key was minted for another workspace. Use this workspace's agent key."
)
USER_MODE_REFUSED = (
    "User mode is not available: this gateway has no OAuth authorization "
    "server for MCP clients, so no person can sign in to a virtual server. "
    "Use agent mode."
)
ENSURE_NOT_SYNCED = (
    "The virtual server was saved but the gateway did not accept it yet. "
    "Call ensure again."
)
DETACH_NOT_SYNCED = (
    "The virtual server was removed but the gateway did not accept the change "
    "yet. Call detach again."
)
MINT_UNREACHABLE = "The gateway could not be reached, so no key was made. Mint again."
MINT_CREDENTIAL_RETIRED = (
    "The EE service credential was rotated out or revoked while the key was "
    "being made, so no key was made."
)
AGENT_KEY_CAP_REACHED = (
    "This organization holds as many live EE agent keys as it may. Revoke a "
    "key it no longer uses (an old key after a rotation), then mint again."
)
REVOKE_NOT_SYNCED = (
    "The key was revoked here but the gateway could not be reached; it drops "
    "the key at its next key sync. Call revoke again."
)
URL_NOT_PUBLIC = (
    f"This deployment's gateway URL ({PUBLIC_URL_ENV} and {MCP_ENDPOINT_ENV}) "
    "is not a public HTTPS URL, so no virtual server URL can be handed out. "
    "An operator sets it."
)


class ProvisioningError(Exception):
    """A refusal, answered with Django's error envelope.

    ``attr`` names the request field at fault, when one is; ``extra`` holds
    fields the answer carries beside the envelope's (a replayed mint's key
    id, CN22d).
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        attr: str = "",
        extra: dict | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.attr = attr
        self.extra = extra


def broker_enabled() -> bool:
    """733 addendum 4: CN22 is OFF at launch, not an operator opt-in."""
    return False


def broker_off_error() -> ProvisioningError:
    return ProvisioningError(
        409,
        "feature_unavailable_733",
        "Unavailable in this release: EE provisioning is disabled (KI-733-EE-RELAY).",
    )


def require_broker():
    if not broker_enabled():
        raise broker_off_error()


def not_found() -> ProvisioningError:
    """The one answer for anything unknown or not the caller's."""
    return ProvisioningError(404, "not_found", "Not found.")


def _invalid(message: str, attr: str = "") -> ProvisioningError:
    return ProvisioningError(400, "invalid", message, attr)


# --- EE's service credential ------------------------------------------------


@dataclass(frozen=True)
class IssuedCredential:
    """A new service credential and its secret, which is shown this once."""

    credential: AgentccEEServiceCredential
    secret: str = field(repr=False)


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def current_credential(org) -> AgentccEEServiceCredential | None:
    return AgentccEEServiceCredential.objects.filter(
        organization=org, status=CredentialStatus.ACTIVE
    ).first()


def _new_credential(org, user) -> IssuedCredential:
    require_broker()
    secret = secrets.token_urlsafe(32)
    credential = AgentccEEServiceCredential.objects.create(
        organization=org,
        api_key=SERVICE_API_KEY_PREFIX + secrets.token_hex(16),
        secret_hash=_secret_hash(secret),
        created_by=user,
    )
    return IssuedCredential(credential, secret)


def issue_credential(org, user) -> IssuedCredential:
    """The org's first service credential, or 409 while one is current."""
    require_broker()
    with transaction.atomic():
        lock_org(org.pk)
        if current_credential(org) is not None:
            raise ProvisioningError(
                409,
                "conflict",
                "This organization already has an EE service credential. "
                "Rotate it to get a new secret.",
            )
        issued = _new_credential(org, user)
        _audit_admin(org, user, AUDIT_CREDENTIAL_ISSUED, issued.credential)
    return issued


def rotate_credential(org, user) -> IssuedCredential:
    """A new pair; the old one stops working at once (401)."""
    require_broker()
    with transaction.atomic():
        lock_org(org.pk)
        old = _locked_current(org)
        old.status = CredentialStatus.ROTATED
        old.rotated_at = timezone.now()
        old.save(update_fields=["status", "rotated_at"])
        issued = _new_credential(org, user)
        _audit_admin(
            org,
            user,
            AUDIT_CREDENTIAL_ROTATED,
            issued.credential,
            replaces=old.api_key,
        )
    return issued


@dataclass(frozen=True)
class RevokedCredential:
    """A retired credential, the agent keys retired with it, and whether the
    gateway has heard about every one of them."""

    credential: AgentccEEServiceCredential
    agent_key_ids: tuple[str, ...]
    gateway_synced: bool


def revoke_credential(org, user) -> RevokedCredential:
    """Retire the current credential with no successor, and every live agent
    key the provisioning API minted in the org with it (under this credential
    or one it rotated out): revoking EE's credential keeps EE out for good
    (spec section 3.7), not only out of provisioning. Rotation revokes no key.

    Django marks the keys revoked under the org lock; the gateway is told
    after commit, so the lock is not held across one call per key. A key the
    gateway did not hear about yet it drops at its next key sync, since the
    list it pulls holds live keys only; ``gateway_synced`` is then False."""
    with transaction.atomic():
        lock_org(org.pk)
        credential = _locked_current(org)
        now = timezone.now()
        credential.status = CredentialStatus.REVOKED
        credential.revoked_at = now
        credential.save(update_fields=["status", "revoked_at"])
        live = AgentccAPIKey.no_workspace_objects.filter(
            ee_agent_key__organization=org, status=AgentccAPIKey.ACTIVE
        )
        key_ids = tuple(sorted(live.values_list("gateway_key_id", flat=True)))
        live.update(status=AgentccAPIKey.REVOKED, updated_at=now)
        _audit_admin(
            org,
            user,
            AUDIT_CREDENTIAL_REVOKED,
            credential,
            agent_keys_revoked=list(key_ids),
        )
    synced = [_revoke_on_gateway(org, key_id) for key_id in key_ids]
    return RevokedCredential(credential, key_ids, all(synced))


def _still_current(credential: AgentccEEServiceCredential) -> bool:
    """Whether the credential is still the org's current one. Under the org
    lock, a rotation or revocation either committed before this or waits."""
    return AgentccEEServiceCredential.objects.filter(
        pk=credential.pk, status=CredentialStatus.ACTIVE
    ).exists()


def _locked_current(org) -> AgentccEEServiceCredential:
    credential = (
        AgentccEEServiceCredential.objects.select_for_update()
        .filter(organization=org, status=CredentialStatus.ACTIVE)
        .first()
    )
    if credential is None:
        raise ProvisioningError(
            404,
            "not_found",
            "This organization has no EE service credential. Issue one first.",
        )
    return credential


def authenticate(api_key: str, secret: str) -> AgentccEEServiceCredential | None:
    """The org's current service credential that ``api_key`` and ``secret``
    name, or None (unknown, wrong secret, rotated out or revoked)."""
    if not provisioning_enabled():
        return None
    credential = (
        AgentccEEServiceCredential.objects.select_related("organization")
        .filter(api_key=api_key)
        .first()
    )
    stored = credential.secret_hash if credential is not None else _secret_hash("")
    matches = hmac.compare_digest(_secret_hash(secret).encode(), stored.encode())
    if credential is None or not matches:
        return None
    if credential.status != CredentialStatus.ACTIVE:
        return None
    now = timezone.now()
    if (
        credential.last_used_at is None
        or now - credential.last_used_at >= LAST_USED_RESOLUTION
    ):
        AgentccEEServiceCredential.objects.filter(pk=credential.pk).update(
            last_used_at=now
        )
        credential.last_used_at = now
    return credential


def is_platform_api_key(api_key: str, secret: str) -> bool:
    """Whether the pair is an admitted personal credential, not EE's identity.

    A proven retired pair keeps the normal retirement response; an invalid
    pair is indistinguishable from an unknown credential.
    """
    # API secrets are sealed at rest. Use the same constant-time verifier as
    # APIKeyAuthentication, including its legacy-secret migration behavior.
    from rest_framework.exceptions import AuthenticationFailed

    from accounts.api_key_policy import APIKeyRetired, authenticate_api_key

    try:
        authenticate_api_key(api_key, secret)
    except APIKeyRetired:
        raise
    except AuthenticationFailed:
        return False
    return True


# --- Agent keys -------------------------------------------------------------


def workspace_of(org, workspace_id: Any) -> Workspace:
    """An active workspace of ``org``; 400 for a malformed id, else 404."""
    if not isinstance(workspace_id, str) or not WORKSPACE_ID.fullmatch(workspace_id):
        raise _invalid("workspace must be a workspace id (UUID).", "workspace")
    workspace = Workspace.no_workspace_objects.filter(
        pk=workspace_id, organization=org, is_active=True
    ).first()
    if workspace is None:
        raise not_found()
    return workspace


def mint_agent_key(
    credential: AgentccEEServiceCredential, workspace_id: Any, idempotency_key: str
):
    """A new gateway virtual key bound to the workspace: (binding, raw key).

    ``idempotency_key`` is the mint's Idempotency-Key, a UUIDv7 in lower case
    (``ee_agent_key_mint.parse_key``). Within 24 hours of a mint with the same
    key under this service credential, a mint with the same request is 409
    ``agent_key_mint_replayed`` naming the key that mint made, and one with
    another request (another workspace) is 422 ``idempotency_key_reused``:
    nothing is made either way, so a lost answer never leaves a second key.
    The replay is checked before the cap, so the retry of a mint that filled
    the cap is still a replay.

    The key is scoped (CN22c, ee_agent_key_policy): scopes mcp and models,
    the workspace's spend budget, no model or provider list of its own (the
    org's config decides per request), not personal, no IP limits, and it
    expires 90 days after this mint. Named so that an admin can tell it
    apart in the console. When the gateway cannot be reached nothing is made
    (it creates a key before Django stores it), and the answer is 503.

    Everything happens under the org lock, in one transaction: the
    credential must still be current (a mint racing a rotation or a
    revocation makes no key at all), the org must be under its cap on live
    agent keys (409 agent_key_cap_reached, audited), and the key's row and
    its binding commit together, so a key sync never lists an agent key
    without the binding that scopes it. A mint that fails after the gateway
    made the key revokes it there.
    """
    require_broker()
    org = credential.organization
    workspace = workspace_of(org, workspace_id)
    request_hash = mint_idempotency.request_sha256({"workspace": str(workspace.id)})
    budget = ee_agent_key_policy.budget_for(workspace.id)
    expires_at = ee_agent_key_policy.expiry_from(timezone.now())
    made = None
    prior = None
    try:
        with transaction.atomic():
            lock_org(org.pk)
            if not _still_current(credential):
                raise ProvisioningError(
                    401, "not_authenticated", MINT_CREDENTIAL_RETIRED
                )
            locked_at = timezone.now()
            prior = mint_idempotency.claim(
                org, credential, idempotency_key, request_hash, locked_at
            )
            if prior is None:
                api_key, raw_key = _provision_locked(org, workspace, budget, expires_at)
                made = api_key.gateway_key_id
                binding = AgentccEEAgentKey.objects.create(
                    organization=org,
                    workspace=workspace,
                    api_key=api_key,
                    minted_by=credential,
                    mint_idempotency_key=idempotency_key,
                )
                mint_idempotency.remember(
                    binding, credential, idempotency_key, request_hash, locked_at
                )
    except _CapReached as refused:
        _audit_ee(
            credential,
            ee_agent_key_policy.AUDIT_AGENT_KEY_CAP_REFUSED,
            workspace.id,
            workspace=str(workspace.id),
            live_agent_keys=refused.live,
            cap=refused.cap,
        )
        raise ProvisioningError(
            409, ee_agent_key_policy.CODE_CAP_REACHED, AGENT_KEY_CAP_REACHED
        ) from None
    except Exception:
        # The gateway made a key that Django will not hold: revoke it there
        # rather than leave a key nobody holds.
        if made is not None:
            _revoke_on_gateway(org, made)
        raise
    if prior is not None:
        _refuse_prior(credential, prior, workspace, idempotency_key)
    _audit_ee(
        credential,
        AUDIT_AGENT_KEY_MINTED,
        binding.id,
        workspace=str(workspace.id),
        agent_key_id=made,
        scopes=ee_agent_key_policy.SCOPES,
        budget_usd=str(budget.usd),
        budget_period=budget.period,
        expires_at=expires_at.isoformat(),
        mint_idempotency_key=idempotency_key,
    )
    return binding, raw_key


class _CapReached(Exception):
    def __init__(self, live: int, cap: int):
        super().__init__(f"{live} live agent keys, cap {cap}")
        self.live, self.cap = live, cap


def _provision_locked(org, workspace, budget, expires_at):
    """Under the org lock: the cap (409 agent_key_cap_reached), then the key
    at the gateway (503 when it cannot be reached; nothing is made)."""
    require_broker()
    live = ee_agent_key_policy.live_agent_keys(org).count()
    cap = ee_agent_key_policy.cap()
    if live >= cap:
        raise _CapReached(live, cap)
    try:
        api_key, raw_key = auth_bridge.provision_key(
            name=f"Omega EE agent key: {workspace.name}"[:255],
            owner=EE_OWNER,
            organization=org,
            server_metadata=ee_agent_key_policy.server_owned_metadata(budget),
            expires_at=expires_at,
        )
    except (GatewayClientError, auth_bridge.GatewayKeyIdCollision) as exc:
        logger.warning(
            "ee_agent_key_mint_failed",
            org_id=str(org.id),
            error=type(exc).__name__,
        )
        raise ProvisioningError(503, "gateway_sync_failed", MINT_UNREACHABLE) from exc
    return api_key, raw_key


def _refuse_prior(credential, prior, workspace, idempotency_key: str):
    """The answer to a mint whose Idempotency-Key has a live record: 409
    ``agent_key_mint_replayed`` naming that mint's key (audited as a replay
    with the workspace), or 422 ``idempotency_key_reused`` for another
    request. Neither carries a key."""
    if not prior.same_request:
        logger.warning(
            "ee_agent_key_mint_idempotency_key_reused",
            org_id=str(credential.organization_id),
            workspace=str(workspace.id),
        )
        raise ProvisioningError(
            422, mint_idempotency.CODE_REUSED, mint_idempotency.REUSED
        )
    binding = prior.record.agent_key
    fields = mint_idempotency.replayed_fields(binding)
    _audit_ee(
        credential,
        mint_idempotency.AUDIT_MINT_REPLAYED,
        binding.id,
        workspace=str(binding.workspace_id),
        agent_key_id=fields["agent_key_id"],
        mint_idempotency_key=idempotency_key,
        replays=prior.record.replays,
        revoked=fields["revoked"],
    )
    raise ProvisioningError(
        409, mint_idempotency.CODE_REPLAYED, mint_idempotency.REPLAYED, extra=fields
    )


def revoke_agent_key(credential: AgentccEEServiceCredential, agent_key_id: str):
    """Revoke a key the provisioning API minted for this org: (binding,
    synced). Django marks it revoked even when the gateway cannot be reached;
    then ``synced`` is False and a repeat retries the gateway."""
    org = credential.organization
    if not AGENT_KEY_ID.fullmatch(agent_key_id):
        raise not_found()
    binding = (
        AgentccEEAgentKey.objects.select_related("api_key")
        .filter(organization=org, api_key__gateway_key_id=agent_key_id)
        .order_by("-created_at")
        .first()
    )
    if binding is None:
        raise not_found()
    key = binding.api_key
    if key.status != AgentccAPIKey.REVOKED:
        key.status = AgentccAPIKey.REVOKED
        key.save(update_fields=["status", "updated_at"])
    synced = _revoke_on_gateway(org, key.gateway_key_id)
    _audit_ee(
        credential,
        AUDIT_AGENT_KEY_REVOKED,
        binding.id,
        workspace=str(binding.workspace_id),
        agent_key_id=agent_key_id,
        gateway_synced=synced,
        # CN22d: a revoke after EE's mint retry was answered with a replay is
        # EE's reconcile of a lost answer.
        mint_idempotency_key=(
            str(binding.mint_idempotency_key) if binding.mint_idempotency_key else None
        ),
        reconcile=mint_idempotency.was_replayed(binding),
    )
    return binding, synced


def _revoke_on_gateway(org, agent_key_id: str) -> bool:
    """Tell the gateway a key is revoked: True once it has. A 404 means the
    gateway no longer holds the key, so there is nothing left to revoke."""
    try:
        get_gateway_client().revoke_key(agent_key_id)
    except GatewayClientError as exc:
        if exc.status_code == 404:
            return True
        logger.warning(
            "ee_agent_key_revoke_not_synced",
            org_id=str(org.id),
            agent_key_id=agent_key_id,
            status_code=exc.status_code,
        )
        return False
    return True


def _bound_agent_key(org, workspace, agent_key_id: str) -> AgentccEEAgentKey:
    """Rule 3: an active key the provisioning API minted for ``workspace``."""
    if not AGENT_KEY_ID.fullmatch(agent_key_id):
        raise ProvisioningError(
            400, "agent_key_invalid", AGENT_KEY_INVALID, "agent_key_id"
        )
    binding = (
        AgentccEEAgentKey.objects.select_related("api_key")
        .filter(
            organization=org,
            api_key__gateway_key_id=agent_key_id,
            api_key__deleted=False,
        )
        .first()
    )
    if binding is None:
        raise ProvisioningError(
            400, "agent_key_invalid", AGENT_KEY_INVALID, "agent_key_id"
        )
    if binding.workspace_id != workspace.id:
        raise ProvisioningError(
            400, "agent_key_invalid", AGENT_KEY_OTHER_WORKSPACE, "agent_key_id"
        )
    key = binding.api_key
    if key.status != AgentccAPIKey.ACTIVE or (
        key.expires_at is not None and key.expires_at <= timezone.now()
    ):
        raise ProvisioningError(
            400, "agent_key_invalid", AGENT_KEY_INVALID, "agent_key_id"
        )
    return binding


# --- Ensure and detach ------------------------------------------------------


@dataclass(frozen=True)
class EnsureRequest:
    workspace: str
    connection_id: str
    servers: tuple[str, ...]
    mode: str
    agent_key_id: str | None


@dataclass(frozen=True)
class EnsureResult:
    row: AgentccEEVirtualServer
    url: str
    created: bool
    synced: bool
    agent_key_expires_at: Any = None  # the bound key's expiry: EE rotates before it


def gateway_mcp_url() -> str:
    """The gateway's public URL and its MCP endpoint path, to which a virtual
    server's url adds ``/v/<vs_id>``. 503 unless every such url is a public
    HTTPS URL EE can call (rule 8): it is checked with a vs_id of the one
    length and alphabet every vs_id has.

    The url is built per answer, not stored: changing these settings is a
    re-attach event. The gateway then serves virtual servers only at the new
    address, so the old url stops working whatever ensure said, and the next
    ensure hands EE the one that works. (In user mode the url is also the
    M24 audience; whether CN22b stores it is CN22b's call.)"""
    base = (
        os.environ.get(PUBLIC_URL_ENV, "").strip()
        or os.environ.get(FALLBACK_URL_ENV, "").strip()
    )
    endpoint = os.environ.get(MCP_ENDPOINT_ENV, "").strip() or DEFAULT_MCP_ENDPOINT
    mcp_url = f"{base.rstrip('/')}/{endpoint.strip('/')}"
    sample = f"{mcp_url}/v/vs_{'0' * 26}"
    if (
        not base
        or len(sample) > VS_URL_MAX_LENGTH
        or not VS_URL.fullmatch(sample)
        or NON_PUBLIC_URL.search(sample)
    ):
        raise ProvisioningError(503, "gateway_url_not_public", URL_NOT_PUBLIC)
    return mcp_url


def ensure(credential: AgentccEEServiceCredential, req: EnsureRequest):
    """Give the connection its virtual server, or update it in place.

    Rule 1: idempotent per (org, workspace, connection); a repeat keeps the
    id and url and changes only ``members`` and ``access.keys``, and pushes
    to the gateway again even when nothing changed. Rule 9: ``synced`` is
    False when the gateway has not accepted it; what was saved stands.
    """
    require_broker()
    org = credential.organization
    if req.mode == USER:
        raise _invalid(USER_MODE_REFUSED, "mode")
    mcp_url = gateway_mcp_url()  # refused before anything is written
    workspace = workspace_of(org, req.workspace)
    with transaction.atomic():
        lock_org(org.pk)
        binding = _bound_agent_key(org, workspace, req.agent_key_id)
        active = _active_config(org)
        mcp = active.mcp if active is not None and isinstance(active.mcp, dict) else {}
        _check_servers(mcp, req.servers)
        row = (
            AgentccEEVirtualServer.objects.select_for_update()
            .filter(
                organization=org,
                workspace=workspace,
                connection_id=req.connection_id,
            )
            .first()
        )
        if row is not None and row.detached_at is not None:
            row.delete()  # detached; its removal is in the config already
            row = None
        previous_key = (
            row.agent_key.api_key.gateway_key_id
            if row is not None and row.agent_key is not None
            else None
        )
        virtual_servers = mcp.get("virtual_servers")
        virtual_servers = virtual_servers if isinstance(virtual_servers, dict) else {}
        current = virtual_servers.get(row.slug) if row is not None else None
        changed = True
        if isinstance(current, dict) and current.get("id") == row.vs_id:
            slug, created = row.slug, False
            entry = _updated_entry(current, req.servers, req.agent_key_id)
            if entry == current:
                config, synced = active, _push(org, active)
                changed = False
            else:
                config, synced = _write(org, slug, entry, req, "update")
        else:
            if row is not None and row.slug not in virtual_servers:
                slug = row.slug
            else:
                slug = _new_slug(org, virtual_servers)
            entry = _new_entry(req, workspace)
            config, synced = _write(org, slug, entry, req, "create")
            created = True
        vs_id = config.mcp["virtual_servers"][slug]["id"]
        if row is None:
            row = AgentccEEVirtualServer(
                organization=org,
                workspace=workspace,
                connection_id=req.connection_id,
                mode=AGENT,
            )
        row.slug, row.vs_id, row.agent_key = slug, vs_id, binding
        row.save()
        if changed:  # a recovery ensure that changes nothing is not audited
            _audit_ee(
                credential,
                AUDIT_VS_ENSURED,
                row.id,
                workspace=str(workspace.id),
                connection_id=req.connection_id,
                vs_id=vs_id,
                created=created,
                members=list(req.servers),
                agent_key_id=req.agent_key_id,
                gateway_synced=synced,
            )
        if previous_key is not None and previous_key != req.agent_key_id:
            # Rotation by ensure (CN22c): the virtual server moved to the
            # workspace's new key; EE revokes the old one when it is done.
            _audit_ee(
                credential,
                ee_agent_key_policy.AUDIT_AGENT_KEY_ROTATED,
                row.id,
                workspace=str(workspace.id),
                connection_id=req.connection_id,
                vs_id=vs_id,
                from_agent_key_id=previous_key,
                to_agent_key_id=req.agent_key_id,
            )
    return EnsureResult(
        row,
        f"{mcp_url}/v/{vs_id}",
        created,
        synced,
        agent_key_expires_at=binding.api_key.expires_at,
    )


def detach(credential: AgentccEEServiceCredential, vs_id: str):
    """Remove one connection's virtual server and nothing else: (row,
    synced). The removal is saved even when the gateway does not accept it;
    then the row stays, marked, and detaching again pushes again."""
    org = credential.organization
    if not VS_ID.fullmatch(vs_id):
        raise not_found()
    with transaction.atomic():
        lock_org(org.pk)
        row = (
            AgentccEEVirtualServer.objects.select_for_update()
            .filter(organization=org, vs_id=vs_id)
            .first()
        )
        if row is None:
            raise not_found()
        active = _active_config(org)
        mcp = active.mcp if active is not None and isinstance(active.mcp, dict) else {}
        virtual_servers = mcp.get("virtual_servers")
        current = (
            virtual_servers.get(row.slug) if isinstance(virtual_servers, dict) else None
        )
        if isinstance(current, dict) and current.get("id") == row.vs_id:
            _, synced = _write_removal(org, row)
        else:
            synced = active is None or _push(org, active)
        row_id = row.id
        if synced:
            row.delete()
        elif row.detached_at is None:
            row.detached_at = timezone.now()
            row.save(update_fields=["detached_at", "updated_at"])
        _audit_ee(
            credential,
            AUDIT_VS_DETACHED,
            row_id,
            workspace=str(row.workspace_id),
            connection_id=row.connection_id,
            vs_id=vs_id,
            gateway_synced=synced,
        )
    return row, synced


def _active_config(org) -> AgentccOrgConfig | None:
    return AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True, deleted=False
    ).first()


def _check_servers(mcp: dict, servers: tuple[str, ...]) -> None:
    """Each member is an org server the gateway loads (it loads none while
    mcp.enabled is off, and skips a server whose enabled is false), and rule
    2: agent mode needs shared or no credentials."""
    if mcp.get("enabled") is not True:
        raise _invalid(
            "MCP is turned off for this organization. An org admin turns it on "
            "before Omega EE can attach a connection."
        )
    configured = mcp.get("servers")
    configured = configured if isinstance(configured, dict) else {}
    for server_id in servers:
        entry = configured.get(server_id)
        if not isinstance(entry, dict):
            raise ProvisioningError(
                400,
                "server_not_found",
                f"Server {server_id} is not configured in this organization.",
                "servers",
            )
        if entry.get("enabled") is False:
            raise _invalid(
                f"Server {server_id} is turned off in this organization.", "servers"
            )
        credential = entry.get("credential")
        mode = (credential.get("mode") if isinstance(credential, dict) else None) or (
            "none"
        )
        if mode not in ("none", "shared"):
            raise ProvisioningError(
                400,
                "mode_mismatch",
                f"Server {server_id} uses {mode} credentials; agent mode needs "
                "servers with shared or no credentials.",
                "servers",
            )


def _new_entry(req: EnsureRequest, workspace) -> dict:
    """Rules 4 to 6: prefixed names and no overrides, posture left to EE
    (no approval, no deny rule), and only the agent key may address it."""
    return {
        "title": f"Omega EE connection {req.connection_id}",
        "description": (
            f"Provisioned by Omega EE for its connection {req.connection_id} "
            f"in workspace {workspace.name}."
        ),
        "members": list(req.servers),
        "naming": "prefixed",
        "access": {"keys": [req.agent_key_id]},
        "approval": {"require": "none"},
    }


def _updated_entry(current: dict, servers, agent_key_id: str) -> dict:
    """Rule 1: only members and access.keys change; every field an admin set
    (access.rules, approval, ...) stays."""
    entry = copy.deepcopy(current)
    entry["members"] = list(servers)
    access = entry.get("access")
    entry["access"] = {
        **(access if isinstance(access, dict) else {}),
        "keys": [agent_key_id],
    }
    return entry


def _new_slug(org, virtual_servers: dict) -> str:
    taken = set(virtual_servers) | set(
        AgentccEEVirtualServer.objects.filter(organization=org).values_list(
            "slug", flat=True
        )
    )
    while True:
        slug = "ee-" + secrets.token_hex(8)
        if slug not in taken:
            return slug


def _write(org, slug: str, entry: dict, req: EnsureRequest, verb: str):
    def updater(config):
        mcp = copy.deepcopy(config.mcp) if isinstance(config.mcp, dict) else {}
        virtual_servers = mcp.get("virtual_servers")
        mcp["virtual_servers"] = {
            **(virtual_servers if isinstance(virtual_servers, dict) else {}),
            slug: entry,
        }
        config.mcp = mcp

    return _written(
        org,
        updater,
        f"Omega EE: {verb} virtual server {slug} for connection {req.connection_id}",
    )


def _write_removal(org, row: AgentccEEVirtualServer):
    def updater(config):
        mcp = copy.deepcopy(config.mcp)
        mcp["virtual_servers"] = {
            slug: entry
            for slug, entry in mcp["virtual_servers"].items()
            if slug != row.slug
        }
        config.mcp = mcp

    return _written(
        org,
        updater,
        f"Omega EE: detach virtual server {row.slug} of connection {row.connection_id}",
    )


def _written(org, updater, description: str):
    try:
        config, warnings = write_config_version_with(
            org, updater, description=description
        )
    except config_push.OrgConfigRejected as exc:
        raise _invalid(f"The organization's MCP config was refused: {exc}") from exc
    return config, is_gateway_synced(warnings)


def _push(org, config: AgentccOrgConfig) -> bool:
    """Push the active config again, under the caller's org lock."""
    try:
        return config_push.push_org_config(str(org.id), config)
    except config_push.OrgConfigRejected as exc:
        raise _invalid(f"The organization's MCP config was refused: {exc}") from exc


# --- Audit ------------------------------------------------------------------


def _audit_admin(org, user, action: str, credential, **metadata) -> None:
    log_audit(
        organization=org,
        action=action,
        scope=AuditScope.ORGANIZATION,
        target_id=credential.id,
        actor=user,
        metadata={"api_key": credential.api_key, **metadata},
    )


def _audit_ee(credential, action: str, target_id, **metadata) -> None:
    """Audited as EE, never as a person (AM-C18 Q1)."""
    log_audit(
        organization=credential.organization,
        action=action,
        scope=AuditScope.ORGANIZATION,
        target_id=target_id,
        actor=None,
        metadata={
            "actor": EE_OWNER,
            "service_credential": credential.api_key,
            **metadata,
        },
    )
