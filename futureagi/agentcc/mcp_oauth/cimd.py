"""Client ID metadata documents (draft-ietf-oauth-client-id-metadata-document):
a ``client_id`` that is an https URL with a path names its own metadata.

The document is fetched through the platform's SSRF-guarded fetcher
(``tfc.utils.ssrf_guard.safe_fetch``: DNS resolved once and the connection
pinned to the checked address; loopback, private, link-local, CGNAT and
cloud-metadata addresses refused at every hop), with a size and time limit.
Any redirect is refused: the document must be at the URL it names. It must
name that same ``client_id``, list valid redirect URIs and use no shared
secret. It is cached on the client row with ``last_fetched_at`` and fetched
again after an hour; when a fetch fails, a copy under a day old is used,
otherwise the client is unknown. AGENTCC_MCP_OAUTH_CIMD_ALLOWED_HOSTS, when
set, limits the hosts whose documents are accepted.
"""

import json
from datetime import timedelta
from urllib.parse import urlsplit

import structlog
from django.utils import timezone

from agentcc.mcp_oauth import clients, config, dcr
from agentcc.models.mcp_oauth import AgentccMCPOAuthClient
from tfc.utils.ssrf_guard import safe_fetch

logger = structlog.get_logger(__name__)

MAX_DOCUMENT_BYTES = 5 * 1024
FETCH_TIMEOUT_SECONDS = 5
REFRESH_AFTER = timedelta(hours=1)
STALE_LIMIT = timedelta(days=1)


class DocumentRefused(Exception):
    pass


def names_a_document(client_id) -> bool:
    if not isinstance(client_id, str) or len(client_id) > 2048:
        return False
    try:
        parts = urlsplit(client_id)
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.path not in ("", "/")
        and not parts.query
        and not parts.fragment
        and "#" not in client_id
        and not parts.username
        and not parts.password
    )


def _host_allowed(client_id) -> bool:
    allowed = config.cimd_allowed_hosts()
    return not allowed or (urlsplit(client_id).hostname or "").lower() in allowed


def fetch(client_id) -> dict:
    try:
        response = safe_fetch(
            client_id,
            method="GET",
            timeout=FETCH_TIMEOUT_SECONDS,
            max_bytes=MAX_DOCUMENT_BYTES,
            headers={"Accept": "application/json"},
        )
    except ValueError as e:
        raise DocumentRefused(f"fetch refused ({type(e).__name__})") from None
    if response.final_url != client_id:
        raise DocumentRefused("the document URL redirects")
    if response.status_code != 200:
        raise DocumentRefused(f"status {response.status_code}")
    try:
        doc = json.loads(response.content)
    except (ValueError, UnicodeDecodeError):
        raise DocumentRefused("not JSON") from None
    if not isinstance(doc, dict):
        raise DocumentRefused("not a JSON object")
    return doc


def validate(client_id, doc) -> dict:
    """The row fields for a valid document; raises DocumentRefused."""
    if doc.get("client_id") != client_id:
        raise DocumentRefused("client_id differs from the document URL")
    if "client_secret" in doc or doc.get("token_endpoint_auth_method", "none") != (
        "none"
    ):
        raise DocumentRefused("a metadata document client uses no shared secret")
    try:
        redirect_uris = dcr.check_redirect_uris(doc.get("redirect_uris"))
        application_type = dcr.application_type_for(
            redirect_uris, doc.get("application_type")
        )
        grant_types, response_types = dcr.check_grants(doc)
        name = dcr._text(doc.get("client_name"), "client_name")
    except dcr.RegistrationError as e:
        raise DocumentRefused(e.description) from None
    metadata = {
        "client_name": name,
        "token_endpoint_auth_method": "none",
        "grant_types": grant_types,
        "response_types": response_types,
    }
    return {
        "metadata": {k: v for k, v in metadata.items() if v is not None},
        "redirect_uris": redirect_uris,
        "application_type": application_type,
    }


def resolve(client_id) -> clients.Client | None:
    if not names_a_document(client_id) or not _host_allowed(client_id):
        return None
    row = AgentccMCPOAuthClient.objects.filter(client_id=client_id).first()
    if row is not None and row.kind != AgentccMCPOAuthClient.Kind.CIMD:
        return None
    now = timezone.now()
    if (
        row is not None
        and row.last_fetched_at
        and now - row.last_fetched_at < (REFRESH_AFTER)
    ):
        return clients.from_row(row)
    try:
        fields = validate(client_id, fetch(client_id))
    except DocumentRefused as e:
        logger.warning(
            "mcp_oauth_cimd_refused",
            client_host=urlsplit(client_id).hostname,
            reason=str(e),
        )
        if (
            row is not None
            and row.last_fetched_at
            and now - row.last_fetched_at < (STALE_LIMIT)
        ):
            return clients.from_row(row)
        return None
    row, _ = AgentccMCPOAuthClient.objects.update_or_create(
        client_id=client_id,
        defaults={
            "kind": AgentccMCPOAuthClient.Kind.CIMD,
            "last_fetched_at": now,
            **fields,
        },
    )
    return clients.from_row(row)
