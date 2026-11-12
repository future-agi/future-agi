"""The gateway's just-in-time fetch of a member's upstream token (M25x,
``mcp-upstream-token-v1``, MC.13).

``POST /agentcc/internal/mcp/upstream-token/ {org_id, user_id, server_id}``
answers the member's own connected token for that server, or a typed refusal
(``no_credential``, ``needs_reauth``, ``revoked``, ``expired``). Only a token
from the member's own connect (``mcp_upstream_oauth``) is served, and only
while:

- the member still belongs to the org (``revoked`` otherwise);
- the server is still one of the org's per-user servers, at the origin the
  member connected it at (``needs_reauth`` when it moved, so a token is never
  sent to a host it was not issued for).

A token within 2 minutes of its expiry is refreshed first, under a lock on
its row so concurrent fetches refresh once, as the client that issued it
(``mcp_connect_clients.issuing``: an organization's or the platform's client
proves its secret only while it is still that client). An invalid grant marks
the connection ``needs_reauth``, and so does invalid_client to a public
client's refresh (a CIMD or DCR client, or one the organization replaced or
removed); retryable failures, including an organization's or the platform's
client that cannot be read for now, keep it active, serving an unexpired
token or answering ``unavailable`` after expiry. A token
whose server gave no expiry is answered with one 15 minutes out, so the
gateway's cache (expiry minus 60 s) re-asks, and re-checks all of the above,
at least that often. The refresh token never leaves Django. Every call writes
an audit row: org, user, server and outcome, never a token.

The organization's request (``SharedRequest`` ``{org_id, server_id, scope:
shared}``, CN24) is answered only from the organization's own row for a
shared OAuth server (scope ``shared``, kind ``oauth``, no user), and only
while the server's config names that row as active, at the origin it was
signed in at; a person's request never gets it, nor it a person's. Its audit
row names no user.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, timedelta
from urllib.parse import urlsplit, urlunsplit

import structlog
from django.db import transaction
from django.utils import timezone

from accounts.models import Organization, User
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import AgentccMCPUpstreamTokenAudit
from agentcc.services import connector_k2, connector_org_app_oauth
from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.services import microsoft_client_identity as identity
from agentcc.services import mcp_connect_clients as connect_clients
from agentcc.services import mcp_shared_oauth as shared_account
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services.connector_oauth_clients import ConnectorOAuthClientError
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.services.mcp_credentials import SERVER_ID, origin_of
from agentcc.services.mcp_user_credential_lifecycle import evict as evict_user
from agentcc.services.mcp_user_credential_lifecycle import lock_identity
from agentcc.services.org_config_writer import lock_org

logger = structlog.get_logger(__name__)

Kind = AgentccMCPCredential.Kind
Scope = AgentccMCPCredential.Scope
Status = AgentccMCPCredential.Status

REFRESH_WITHIN = timedelta(minutes=2)
UNKNOWN_EXPIRY_HORIZON = timedelta(minutes=15)
REFUSALS = ("no_credential", "needs_reauth", "revoked", "expired", "unavailable")
MAX_ID_CHARS = 64


@dataclass(frozen=True)
class TokenAnswer:
    outcome: str  # "ok", "refreshed", or one of REFUSALS
    server_id: str
    access: "connect.Sealed | None" = None
    expires_at: object = None
    version: int = 0
    hint: bool = True
    message: str | None = None  # only on Microsoft's hold (a 409; Refusal message)

    def body(self) -> dict:
        """The response body; the only place the token is in clear."""
        if self.access is None:
            body = {"error": self.outcome}
            if self.message:
                body["message"] = self.message
            if self.hint:
                body["connect_hint"] = connect_hint(self.server_id)
            return body
        return {
            "access_token": self.access.reveal(),
            "token_type": "Bearer",
            "expires_at": self.expires_at.astimezone(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "credential_version": self.version,
        }


@dataclass(frozen=True)
class RefreshResult:
    """Only an authoritative invalid_grant, or invalid_client to a public
    client's refresh (no client of ours resolves to its id), ends a
    refreshable connection."""

    tokens: "connect._Tokens | None" = None
    terminal: bool = False


def connect_hint(server_id: str) -> str:
    """Where a member connects ``server_id``: its start page, on the host
    that serves the callback."""
    parts = urlsplit(connect.callback_url())
    return urlunsplit(
        (parts.scheme, parts.netloc, f"/agentcc/mcp/connect/{server_id}/start/", "", "")
    )


def valid_request(body) -> bool:
    """A request shape ``mcp-upstream-token-v1`` allows: a person's (exactly
    the three ids) or the organization's (``org_id``, ``server_id`` and
    ``scope: shared``), each id a non-empty string."""
    if not isinstance(body, dict):
        return False
    if "refresh" in body and not isinstance(body["refresh"], bool):
        return False
    fields = set(body) - {"refresh"}
    if fields == {"org_id", "server_id", "scope"}:
        if body["scope"] != shared_account.SHARED:
            return False
        keys = ("org_id", "server_id")
    elif fields == {"org_id", "user_id", "server_id"}:
        keys = ("org_id", "user_id", "server_id")
    else:
        return False
    return all(
        isinstance(body[k], str) and 0 < len(body[k]) <= MAX_ID_CHARS for k in keys
    )


def answer_request(body: dict) -> TokenAnswer:
    """The audited answer for a request ``valid_request`` accepted."""
    refresh = body.get("refresh", True)
    if body.get("scope") == shared_account.SHARED:
        return shared_token(body["org_id"], body["server_id"], refresh=refresh)
    return upstream_token(
        body["org_id"], body["user_id"], body["server_id"], refresh=refresh
    )


def _microsoft_refusal(org, server_id, entry, row):
    """Check the selected, locked row, never a separate metadata preflight."""
    try:
        identity.deny_if_excluded(
            row.oauth_client_id, origins=(row.origin, row.oauth_issuer)
        )
        if not microsoft.platform_allowed(entry, server_id=server_id):
            connect_clients.issuing(org, server_id, entry, row.oauth_client_id)
    except ConnectorOAuthClientError:
        return TokenAnswer("needs_reauth", server_id)
    return None


def _held(server_id, row):
    """Microsoft's hold, with no hint. A grant it stopped that needs a new
    sign-in anyway (no recorded issuing client, from a Microsoft host) says so;
    the row's stored fields only choose the words."""
    stopped = row is not None and identity.unproven_microsoft_grant(
        row.oauth_client_id, (row.origin, row.oauth_issuer)
    )
    return TokenAnswer(
        microsoft.CODE,
        server_id,
        hint=False,
        message=microsoft.GRANT_MESSAGE if stopped else None,
    )


def _policy_selected_answer(org, user, server_id, *, shared, refresh):
    """Select once under the caller's transaction and pass THAT row to _fresh.

    Keep the protected historical selectors unchanged. Their membership,
    origin, config-reference and registration authorities are retained here;
    policy must run before _fresh can decrypt, refresh or lapse the row.
    The outer row lock also covers _refreshed's protected locked re-read.
    """
    refuse = _refusal(server_id)
    entry = connect.server_entry(org.pk, server_id, include_hidden=True)
    rows = (shared_account.active_rows(org.pk, server_id) if shared else
            AgentccMCPCredential.objects.filter(
                organization=org, server_id=server_id, scope=Scope.USER,
                user=user, kind=Kind.OAUTH,
            ))
    rows = rows.select_for_update().defer("encrypted_secret", "encrypted_refresh")
    row = rows.filter(status=Status.ACTIVE).first()
    # Held by the server, or, for a server added by URL, by the Microsoft
    # issuer its stored grant came from: read-only and unaudited.
    issuer = row.oauth_issuer if row is not None else None
    if microsoft.blocked(entry, server_id=server_id, issuer=issuer):
        return _held(server_id, row), None, True
    if row is None:
        lapsed = rows.order_by("-created_at").first()
        return refuse(lapsed.status if lapsed else "no_credential"), lapsed, False
    if shared:
        # Classify the selected identity before a missing config reference, but
        # do not resolve/decrypt a permitted registration until that reference holds.
        try:
            identity.deny_if_excluded(
                row.oauth_client_id, origins=(row.origin, row.oauth_issuer)
            )
        except ConnectorOAuthClientError:
            return refuse("needs_reauth"), row, True
        entry = connect.server_entry(org.pk, server_id)
        if not shared_account.names(entry, row):
            return refuse("no_credential"), row, False
        url = entry.get("url")
    else:
        if row.gateway_purge_pending:
            return refuse("needs_reauth"), row, False
        if not connect.is_member(user, org):
            return refuse("revoked", hint=False), row, False
        try:
            url = connect.per_user_server_url(org.pk, server_id)
        except connect.ConnectRefused as refused:
            return refuse("no_credential", hint=refused.code != "sign_in_not_served"), row, False
        entry = connect.server_entry(org.pk, server_id)
    # Refusal is read-only, including when the selected row has old provenance.
    if denied := _microsoft_refusal(org, server_id, entry, row):
        return denied, row, True
    if not row.origin or origin_of(url) != row.origin:
        return refuse("needs_reauth"), row, False
    if not shared and not connector_k2.issued_by_registration(
        org, server_id, entry, row.oauth_client_id
    ):
        return refuse("needs_reauth"), row, False
    answer, credential = _fresh(row, url, refresh=refresh)
    return answer, credential, False


def _policy_answer(org_id, server_id, user_id=None, *, refresh):
    org = _get(Organization, org_id)
    user = _get(User, user_id) if user_id is not None else None
    if (org is None or not SERVER_ID.fullmatch(server_id)
            or (user_id is not None and user is None)):
        return _refusal(server_id)("no_credential"), None, False
    with transaction.atomic():
        if user_id is None:
            # Shared writes already take this lock before the default row.
            lock_org(org.pk)
        else:
            lock_identity(org.pk, user.pk, server_id)
        return _policy_selected_answer(
            org, user, server_id, shared=user_id is None, refresh=refresh
        )


def shared_token(org_id: str, server_id: str, *, refresh: bool = True) -> TokenAnswer:
    """The answer for one fetch of the organization's account, audited."""
    answer, credential, policy_refused = _policy_answer(
        org_id, server_id, refresh=refresh
    )
    if not policy_refused:
        _audit(org_id, "", server_id, answer, credential)
    return answer


def upstream_token(
    org_id: str, user_id: str, server_id: str, *, refresh: bool = True
) -> TokenAnswer:
    """The answer for one fetch, audited."""
    answer, credential, policy_refused = _policy_answer(
        org_id, server_id, user_id, refresh=refresh
    )
    if not policy_refused:
        _audit(org_id, user_id, server_id, answer, credential)
    return answer


def _audit(org_id, user_id, server_id, answer, credential) -> None:
    AgentccMCPUpstreamTokenAudit.objects.create(
        org_id=org_id,
        user_id=user_id,
        server_id=server_id,
        outcome=answer.outcome,
        credential=credential,
    )
    logger.info(
        "mcp_upstream_token",
        org_id=org_id,
        user_id=user_id,
        server_id=server_id,
        outcome=answer.outcome,
        credential_id=str(credential.pk) if credential else None,
        scope=shared_account.SHARED if not user_id else shared_account.USER,
    )


def _answer(org_id: str, user_id: str, server_id: str, *, refresh: bool = True):
    refuse = _refusal(server_id)
    org = _get(Organization, org_id)
    user = _get(User, user_id)
    if org is None or user is None or not SERVER_ID.fullmatch(server_id):
        return refuse("no_credential"), None
    with transaction.atomic():
        lock_identity(org.pk, user.pk, server_id)
        return _locked_user_answer(org, user, server_id, refresh=refresh)


def _locked_user_answer(org, user, server_id, *, refresh=True):
    refuse = _refusal(server_id)
    rows = AgentccMCPCredential.objects.select_for_update().filter(
        organization=org,
        server_id=server_id,
        scope=Scope.USER,
        user=user,
        kind=Kind.OAUTH,
    )
    row = rows.filter(status=Status.ACTIVE).first()
    if row is None:
        lapsed = rows.order_by("-created_at").first()
        return refuse(lapsed.status if lapsed else "no_credential"), lapsed
    if row.gateway_purge_pending:
        return refuse("needs_reauth"), row
    if not connect.is_member(user, org):
        return refuse("revoked", hint=False), row
    try:
        url = connect.per_user_server_url(org.pk, server_id)
    except connect.ConnectRefused as refused:
        return refuse("no_credential", hint=refused.code != "sign_in_not_served"), row
    if not row.origin or origin_of(url) != row.origin:
        return refuse("needs_reauth"), row
    entry = connect.server_entry(org.pk, server_id)
    if not connector_k2.issued_by_registration(
        org, server_id, entry, row.oauth_client_id
    ):
        return refuse("needs_reauth"), row
    return _fresh(row, url, refresh=refresh)


def _shared_answer(org_id: str, server_id: str, *, refresh: bool = True):
    refuse = _refusal(server_id)
    org = _get(Organization, org_id)
    if org is None or not SERVER_ID.fullmatch(server_id):
        return refuse("no_credential"), None
    rows = shared_account.active_rows(org.pk, server_id)
    row = rows.filter(status=Status.ACTIVE).first()
    if row is None:
        lapsed = rows.order_by("-created_at").first()
        return refuse(lapsed.status if lapsed else "no_credential"), lapsed
    entry = connect.server_entry(org.pk, server_id)
    if not shared_account.names(entry, row):
        return refuse("no_credential"), row
    url = entry.get("url")
    if not row.origin or origin_of(url) != row.origin:
        return refuse("needs_reauth"), row
    return _fresh(row, url, refresh=refresh)


def _fresh(row: AgentccMCPCredential, url: str, *, refresh: bool = True):
    """Refresh once, or authoritatively read the unexpired current grant.

    Post-eviction revalidation disables proactive refresh, not membership,
    origin, status, cleanup or hard-expiry checks. This lets a provider's
    freshly issued token remain usable even within REFRESH_WITHIN.
    """
    now = timezone.now()
    expires = row.token_expires_at
    if not refresh and expires is not None and expires <= now:
        if not row.encrypted_refresh:
            _lapse(row, Status.EXPIRED)
        return _refusal(row.server_id)("expired"), row
    if refresh and expires is not None and expires - now <= REFRESH_WITHIN:
        return _refreshed(row, url, now)
    return _served(row, "ok", expires or now + UNKNOWN_EXPIRY_HORIZON), row


def _refreshed(row: AgentccMCPCredential, url: str, now):
    """Refresh ``row`` under a lock on it; a concurrent fetch that took the
    lock first has refreshed it already, and its token is served."""
    with transaction.atomic():
        row = AgentccMCPCredential.objects.select_for_update().get(pk=row.pk)
        if row.status != Status.ACTIVE:
            return _refusal(row.server_id)(row.status), row
        expires = row.token_expires_at
        if expires is not None and expires - now > REFRESH_WITHIN:
            return _served(row, "ok", expires), row
        if not row.encrypted_refresh:
            if expires is not None and expires <= now:
                _lapse(row, Status.EXPIRED)
                return _refusal(row.server_id)("expired"), row
            return _served(row, "ok", expires), row
        refreshed = _refresh(row, url)
        if refreshed is None:  # a policy refusal, not grant failure
            return _refusal(row.server_id)("needs_reauth"), row
        tokens = refreshed.tokens
        if tokens is None:
            if refreshed.terminal:
                _lapse(row, Status.NEEDS_REAUTH)
                return _refusal(row.server_id)("needs_reauth"), row
            # Provider outages do not invalidate the grant. Recheck the clock:
            # an access token may have expired while waiting for the provider.
            if expires is not None and expires > timezone.now():
                return _served(row, "ok", expires), row
            return _refusal(row.server_id)("unavailable", hint=False), row
        row.encrypted_secret = encrypt_token(tokens.access.reveal())
        if tokens.refresh is not None:
            row.encrypted_refresh = encrypt_token(tokens.refresh.reveal())
        row.token_expires_at = tokens.expires_at
        if tokens.scopes:
            row.scopes = list(tokens.scopes)
        row.version += 1
        row.rotated_at = timezone.now()
        row.save(
            update_fields=[
                "encrypted_secret",
                "encrypted_refresh",
                "token_expires_at",
                "scopes",
                "version",
                "rotated_at",
            ]
        )
        if row.scope == Scope.USER and not evict_user(row):
            return _refusal(row.server_id)("needs_reauth"), row
    logger.info(
        "mcp_upstream_token_refreshed",
        org_id=str(row.organization_id),
        server_id=row.server_id,
        credential_id=str(row.pk),
        version=row.version,
    )
    expires = row.token_expires_at or timezone.now() + UNKNOWN_EXPIRY_HORIZON
    return _served(row, "refreshed", expires, access=tokens.access), row


def _refresh(row: AgentccMCPCredential, url: str) -> "RefreshResult | None":
    """Refresh outcome, or None when policy refuses delivery: the issuing
    client's, or a declared sign-in that cannot be read now."""
    org_app = None
    try:
        entry = connect.server_entry(row.organization_id, row.server_id)
        microsoft.require_microsoft_connect_enabled(
            entry, server_id=row.server_id, issuer=row.oauth_issuer
        )
        identity.deny_if_excluded(row.oauth_client_id, origins=(row.origin, row.oauth_issuer, url))
        if not microsoft.platform_allowed(entry, server_id=row.server_id):
            # Check the issuing id before discovery or refresh-token decryption.
            connect_clients.issuing(
                row.organization, row.server_id, entry, row.oauth_client_id
            )
        org_app = connector_org_app_oauth.declared(entry)
        declared = connector_k2.pilot(entry) or org_app
        if declared is None and connector_org_app_oauth.is_org_app_server(entry):
            # Never send the organization's secret or the refresh token to a
            # token endpoint read from metadata in place of the declared one.
            return None
        if declared is not None:  # a declared OAuth 2 server: nothing discovered
            endpoint, auth_methods = declared.token_url, None
        else:
            meta = connect.authorization_server_metadata(row.oauth_issuer)
            endpoint = connect.endpoint_of(
                meta, "token_endpoint", row.oauth_issuer, required=True
            )
            auth_methods = meta.get("token_endpoint_auth_methods_supported")
        client, ref = connect_clients.issuing(
            row.organization,
            row.server_id,
            entry,
            row.oauth_client_id,
            auth_methods=auth_methods,
        )
        form = {
            "grant_type": "refresh_token",
            "refresh_token": decrypt_token(row.encrypted_refresh),
        }
        resource = row.oauth_resource
        if resource is None:
            # Legacy grants did not retain the resource. Keep their previous
            # behavior until a new explicit sign-in records the exact grant.
            resource = "" if declared is not None else url
        if resource:
            form["resource"] = resource
    except ConnectorOAuthClientError:
        return None
    except Exception as error:
        logger.warning("mcp_upstream_refresh_failed", error_type=type(error).__name__)
        if isinstance(error, connect.ConnectRefused) and error.code == microsoft.CODE:
            return None
        return RefreshResult()
    response = connect_clients.post(
        endpoint, form, client, ref, max_bytes=connect.METADATA_LIMIT
    )
    if response is None:
        return RefreshResult()
    # A server that signs in only with the organization's own OAuth app may
    # name its own token type, as at its sign-in.
    tokens = connect.parse_token_response(response, any_token_type=org_app is not None)
    if tokens is not None:
        return RefreshResult(tokens=tokens)
    logger.warning("mcp_upstream_refresh_refused", status=response.status)
    terminal = False
    if response.status in (400, 401):
        try:
            error = response.json().get("error")
        except (ValueError, TypeError, AttributeError):
            error = None
        # invalid_client to a public client's refresh is definitive: the
        # client resolved cleanly to none of ours with this id (the org
        # replaced or removed it, or it is a CIMD or DCR client the server
        # no longer takes). An unresolved client (its secret cannot be read
        # for now) and a confidential one that sent its secret stay retryable.
        terminal = error == "invalid_grant" or (
            error == "invalid_client" and client.kind == connect_clients.PUBLIC
        )
    return RefreshResult(terminal=terminal)


def _served(row, outcome, expires, *, access=None) -> TokenAnswer:
    """The success answer for ``row``, its token decrypted only into the
    sealed holder."""
    try:
        identity.deny_if_excluded(
            row.oauth_client_id, origins=(row.origin, row.oauth_issuer)
        )
    except ConnectorOAuthClientError:
        return TokenAnswer("needs_reauth", row.server_id)
    if access is None:
        access = connect.Sealed(decrypt_token(row.encrypted_secret))
    AgentccMCPCredential.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
    return TokenAnswer(
        outcome=outcome,
        server_id=row.server_id,
        access=access,
        expires_at=expires,
        version=row.version,
    )


def _lapse(row: AgentccMCPCredential, status: str) -> None:
    row.status = status
    row.save(update_fields=["status"])
    evict_user(row)
    logger.warning(
        "mcp_upstream_token_lapsed",
        org_id=str(row.organization_id),
        server_id=row.server_id,
        credential_id=str(row.pk),
        status=status,
    )


def _refusal(server_id: str):
    def refuse(outcome: str, *, hint: bool = True) -> TokenAnswer:
        return TokenAnswer(outcome=outcome, server_id=server_id, hint=hint)

    return refuse


def _get(model, pk: str):
    try:
        uuid.UUID(pk)
    except (TypeError, ValueError):
        return None
    return model.objects.filter(pk=pk).first()
