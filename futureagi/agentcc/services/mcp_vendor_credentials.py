"""Authoritative, fail-closed end-user authorization and JIT credential custody."""

import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from agentcc.mcp_personal_auth import is_individual_member
from agentcc.models.mcp_vendor import AgentccMCPVendorAudit as Audit
from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection
from agentcc.services.credential_manager import decrypt_token
from agentcc.services.mcp_vendor_composio import Composio
from agentcc.services.mcp_vendor_http import VendorUnavailable, origin
from agentcc.services.mcp_vendor_pipedream import Pipedream
from agentcc.services.mcp_vendor_state import current, locked_bindings

ASSERTED = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@|+=-]{0,127}\Z")
SERVER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def enabled():
    default = os.environ.get("AGENTCC_MCP_VENDORS_ENABLED", "false").lower() == "true"
    return getattr(settings, "AGENTCC_MCP_VENDORS_ENABLED", default) is True


def valid_request(body):
    if not isinstance(body, dict) or set(body) - {
        "org_id",
        "server_id",
        "vendor",
        "end_user",
        "refresh",
    }:
        return False
    identity = body.get("end_user")
    if not isinstance(identity, dict) or set(identity) != {"source", "id"}:
        return False
    if (
        identity["source"] not in ("member", "asserted")
        or not isinstance(identity["id"], str)
        or not 1 <= len(identity["id"]) <= 128
    ):
        return False
    if not isinstance(body.get("server_id"), str) or not SERVER.fullmatch(
        body["server_id"]
    ):
        return False
    if body.get("vendor") not in ("pipedream", "composio") or (
        "refresh" in body and type(body["refresh"]) is not bool
    ):
        return False
    try:
        uuid.UUID(body.get("org_id", ""))
        if identity["source"] == "member":
            uuid.UUID(identity["id"])
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def identity_hash(org_id, source, subject):
    return hashlib.sha256(f"v1|{org_id}|{source}|{subject}".encode()).hexdigest()[:16]


def subject_key(org_id, source, subject):
    key = getattr(
        settings,
        "AGENTCC_MCP_VENDOR_SUBJECT_KEY",
        os.environ.get("AGENTCC_MCP_VENDOR_SUBJECT_KEY", ""),
    )
    if not isinstance(key, str) or len(key) < 32:
        raise VendorUnavailable()
    payload = json.dumps(
        ["mcp-vendor-subject-v1", str(org_id), source, subject], separators=(",", ":")
    )
    digest = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    return "fau_" + base64.b32encode(digest).decode().rstrip("=").lower()


def authorize(binding, identity):
    source, subject = identity["source"], identity["id"]
    if source == "member":
        try:
            user = User.objects.filter(pk=subject, is_active=True).first()
        except (ValueError, TypeError):
            return None, False
        return user, is_individual_member(
            user, binding.organization, allow_legacy=False
        )
    allowed = (
        binding.identity_policy == "member_or_asserted"
        and bool(ASSERTED.fullmatch(subject))
        and not subject.startswith("fau_")
    )
    return None, allowed


def vendor_signs_in(binding):
    """A Composio binding whose credential is the vendor's key, not an OAuth
    client: the vendor signs people in with its own app and holds no client
    of ours, so the Microsoft connect hold is not ours to apply to it."""
    return binding.vendor == "composio" and binding.credential.kind != "oauth_client"


def vendor_client(binding):
    credential = binding.credential
    if (
        credential.status != "active"
        or credential.organization_id != binding.organization_id
        or credential.server_id != binding.server_id
        or credential.scope != "shared"
    ):
        raise VendorUnavailable()
    from agentcc.services import microsoft_connect_gate as microsoft
    from agentcc.services.mcp_upstream_oauth import server_entry
    from agentcc.services.microsoft_client_identity import deny_if_excluded

    entry = server_entry(binding.organization_id, binding.server_id, include_hidden=True)
    held = not vendor_signs_in(binding)
    if held:
        microsoft.require_microsoft_connect_enabled(
            entry, server_id=binding.server_id, app_id=binding.app_slug
        )
    held_identity = microsoft.is_microsoft(
        entry, server_id=binding.server_id, app_id=binding.app_slug
    )
    for toolkit in binding.toolkits:
        if held:
            microsoft.require_microsoft_connect_enabled(app_id=toolkit)
        held_identity = held_identity or microsoft.is_microsoft(app_id=toolkit)
    if held_identity and (credential.kind == "oauth_client" or binding.vendor == "pipedream"):
        # Legacy partner JSON contains its own client_id. No stored proof binds
        # oauth_client_id to that encrypted payload, even when public metadata
        # is nonempty. Do not decrypt to discover an absent/inconsistent ID.
        # Microsoft partner OAuth requires a separately reviewed provenance
        # migration; setting the connect gate ON cannot manufacture that proof.
        raise VendorUnavailable()
    deny_if_excluded(credential.oauth_client_id, origins=(credential.origin, credential.oauth_issuer))
    raw = decrypt_token(credential.encrypted_secret)
    if binding.vendor == "pipedream":
        if origin(credential.origin) != origin("https://api.pipedream.com"):
            raise VendorUnavailable()
        try:
            values = json.loads(raw)
            if not all(
                isinstance(values.get(k), str) and values[k]
                for k in ("client_id", "client_secret")
            ):
                raise VendorUnavailable()
        except (ValueError, AttributeError):
            raise VendorUnavailable() from None
        return Pipedream(
            values["client_id"],
            values["client_secret"],
            binding.project_id,
            binding.environment,
            binding=binding,
        )
    if origin(credential.origin) != origin("https://backend.composio.dev") or not raw:
        raise VendorUnavailable()
    return Composio(raw)


def _not_connected(binding, identity):
    if identity["source"] == "member":
        return {"error": "not_connected"}
    # An asserted customer has no FutureAGI browser principal. The trusted
    # gateway and authoritative binding policy authorize this vendor link.
    from agentcc.services.mcp_vendor_connections import ConnectionRefused, start

    try:
        link = start(binding.pk, identity)
    except ConnectionRefused:
        return {"error": "identity_refused"}
    return {"error": "not_connected", "connect_hint": link}


def refusal(binding, identity, row=None):
    user, allowed = authorize(binding, identity)
    if not allowed:
        return {"error": "identity_refused"}
    credential = binding.credential
    if credential.status != "active":
        reason = (
            credential.status
            if credential.status in ("revoked", "expired", "needs_reauth")
            else "no_credential"
        )
        return {"error": reason}
    if not binding.enabled:
        return {"error": "no_credential"}
    if credential.token_expires_at and credential.token_expires_at <= timezone.now():
        return {"error": "expired"}
    if row is not None:
        if (
            row.status == "revoked"
            or row.gateway_purge_pending
            or row.vendor_revoke_pending
        ):
            return {"error": "revoked"}
        if row.connect_pending:
            return {"error": "not_connected"}
        if row.source != identity["source"] or (user and row.user_id != user.pk):
            return {"error": "identity_refused"}
    return None


def _grant(binding, row):
    # No transaction surrounds the vendor calls (including bearer mint/cache).
    from agentcc.services.mcp_vendor_connections import require_microsoft_binding_enabled

    require_microsoft_binding_enabled(
        binding.organization_id, binding.server_id,
        app_slug=binding.app_slug, toolkits=binding.toolkits, binding=binding,
    )
    api = vendor_client(binding)
    if binding.vendor == "pipedream":
        account = api.account(
            row.vendor_subject, binding.app_slug, row.vendor_account_ref
        )
        if not account:
            return None, None
        answer = {
            "vendor": "pipedream",
            "access_token": api.bearer(),
            "expires_at": api.expires_at.isoformat(),
            "project_id": binding.project_id,
            "environment": binding.environment,
            "app_slug": binding.app_slug,
            "external_user_id": row.vendor_subject,
            "account_id": account,
            "credential_version": row.version,
        }
    else:
        # Stage 1 supports a single explicitly configured toolkit per binding.
        # Reject multiple toolkits rather than silently picking a default account.
        if (
            len(binding.toolkits) != 1
            or not row.encrypted_session
            or not row.encrypted_endpoint
        ):
            return None, None
        session = decrypt_token(row.encrypted_session)
        if row.vendor_account_ref:
            api.pin_account(session, binding.toolkits[0], row.vendor_account_ref)
        account = api.account(session, binding.toolkits[0])
        if not account or (
            row.vendor_account_ref and account != row.vendor_account_ref
        ):
            return None, None
        if not row.vendor_account_ref:
            api.pin_account(session, binding.toolkits[0], account)
        endpoint = decrypt_token(row.encrypted_endpoint)
        if origin(endpoint) != origin(api.base):
            raise VendorUnavailable()
        answer = {
            "vendor": "composio",
            "api_key": api.api_key,
            "endpoint": endpoint,
            "expires_at": (timezone.now() + timedelta(minutes=5)).isoformat(),
            "credential_version": row.version,
        }
    return answer, account


def _answer_request(body):
    identity = body["end_user"]
    with transaction.atomic():
        binding = (
            locked_bindings()
            .filter(
                organization_id=body["org_id"],
                server_id=body["server_id"],
                vendor=body["vendor"],
            )
            .first()
        )
        if binding is None:
            return {"error": "no_credential"}
        if not authorize(binding, identity)[1]:
            return {"error": "identity_refused"}
        from agentcc.services.mcp_vendor_connections import require_microsoft_binding_enabled

        require_microsoft_binding_enabled(
            binding.organization_id, binding.server_id,
            app_slug=binding.app_slug, toolkits=binding.toolkits, binding=binding,
        )
        if denied := refusal(binding, identity):
            return denied
        subject = subject_key(
            binding.organization_id, identity["source"], identity["id"]
        )
        row = (
            Connection.objects.select_for_update()
            .filter(binding=binding, end_user_key=subject)
            .first()
        )
        if denied := refusal(binding, identity, row):
            return denied
    # In particular, asserted start() must not be nested in a savepoint.
    if row is None:
        return _not_connected(binding, identity)
    answer, account = _grant(binding, row)
    with transaction.atomic():
        fresh = locked_bindings().filter(pk=binding.pk).first()
        saved = Connection.objects.select_for_update().filter(pk=row.pk).first()
        if (
            fresh is None
            or saved is None
            or not current(fresh, binding)
            or saved.version != row.version
        ):
            return {"error": "revoked"}
        if denied := refusal(fresh, identity, saved):
            return denied
        if answer:
            if saved.vendor_account_ref and saved.vendor_account_ref != account:
                return {"error": "not_connected"}
            saved.vendor_account_ref = account
            saved.status = "active"
            saved.save(update_fields=["vendor_account_ref", "status", "updated_at"])
    return answer or _not_connected(binding, identity)


def answer_request(body):
    if not enabled() or not valid_request(body):
        return {"error": "identity_refused"}
    from agentcc.services import microsoft_connect_gate as microsoft
    from agentcc.services.mcp_upstream_oauth import ConnectRefused

    try:
        answer = _answer_request(body)
    except ConnectRefused as refused:
        if refused.code != microsoft.CODE:
            raise
        # Policy refusal is not a connection/token mutation or an audit write.
        # Only a binding the hold applies to (not vendor_signs_in) gets here.
        return {"error": microsoft.CODE, "message": microsoft.BINDING_MESSAGE}
    Audit.objects.create(
        organization_id=body["org_id"],
        server_id=body["server_id"],
        end_user_hash=identity_hash(
            body["org_id"], body["end_user"]["source"], body["end_user"]["id"]
        ),
        outcome=answer.get("error", "issued"),
    )
    return answer
