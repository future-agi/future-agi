"""Checks the API makes before anything is stored (DESIGN.md sections 4.4, 5.1, 5.3, 6.1, 6.3, 7.8 and 14).

The URL and header rules are the core's (``webhooks.core.addresses``, ``webhooks.core.signing``): one copy of each
rule, shared with the dispatcher, which applies them again before every attempt. Nothing here makes a network call: a
URL is judged on its text, and names are never looked up at create (section 6.1); resolved addresses are judged at
send time (section 6.2).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

from django.utils import timezone

from webhooks import config, crypto
from webhooks.api_errors import WebhookApiError
from webhooks.api_locks import lock_org
from webhooks.core import fanout
from webhooks.core.addresses import CODE_HOST_NOT_ALLOWED, UrlDecision, url_decision
from webhooks.core.signing import SecretInvalid, custom_header_problems, secret_key
from webhooks.models import ApiAction, Endpoint
from webhooks.permissions import allowed_host_lists, policies

HTTP_URL_WARNING_MESSAGE = (
    "This URL uses HTTP, so deliveries are not encrypted. Webhook payloads can include "
    "event data from your organization. Use an HTTPS URL to protect payloads in transit."
)

MAX_ENDPOINTS_PER_ORG = 200
WRITES_PER_MINUTE_PER_ORG = 120
MAX_FILTER_VALUES = 100
MAX_FILTER_VALUE_CHARS = 200

_PROMPT_VERSION = {"prompt_ids", "exclude_prompt_ids"}
_PROMPT_LABEL = _PROMPT_VERSION | {"label_names", "actions"}
_RUN = {"deployment_ids", "environments", "trigger_kinds"}
FILTER_KEYS = {
    "prompt.version.created": _PROMPT_VERSION,
    "prompt.label.changed": _PROMPT_LABEL,
    "prompt.*": _PROMPT_LABEL,
    "deployment.run.completed": _RUN,
    "deployment.run.failed": _RUN | {"statuses"},
    "deployment.run.*": _RUN | {"statuses"},
}
FILTER_ENUMS = {
    "actions": ("assigned", "moved", "removed"),
    "statuses": ("failed", "interrupted", "cancelled"),
}

_URL_COPY = {
    "url.length": "The URL is longer than 2,048 characters.",
    "url.syntax": "This is not a valid URL.",
    "url.characters": "The URL has spaces, control characters or non-ASCII text. Percent-encode them.",
    "url.host_syntax": "This is not a valid host name.",
    "url.fragment": "Remove the #fragment from the URL.",
    "url.userinfo": "Remove the user name and password from the URL; send credentials in a header instead.",
    "url.port": "This port is not allowed. Use 443, or a port from 1024 to 65535 that is not a database or "
    "cluster port.",
    "url.ip_literal_nonstandard": "Use a host name or a standard IP address.",
    "url.scheme": "Webhook URLs must use HTTPS.",
    "url.host_own": "Future AGI's own hosts can't receive webhooks.",
    "url.host_localhost": "Private, local and cloud metadata addresses are blocked.",
    "url.host_metadata": "Private, local and cloud metadata addresses are blocked.",
    "url.host_no_dot": "Use a public host name. Internal names (no dot, .svc, .local, .internal) are blocked.",
    "url.host_internal_name": "Use a public host name. Internal names (no dot, .svc, .local, .internal) are "
    "blocked.",
    "addr.never": "Private, local and cloud metadata addresses are blocked.",
    "addr.private": "Private, local and cloud metadata addresses are blocked.",
    "addr.private_port": "This private address may only be reached on the ports the operator allows.",
}


def require_keys() -> None:
    """K2: refuse before any URL, secret or header value from the request is handled, so a missing or malformed
    ``WEBHOOKS_ENCRYPTION_KEYS`` never leaves plaintext in a frame that logs."""
    try:
        crypto.require_keys()  # the public K2 check (WH-1b); raises before any plaintext is touched
    except (crypto.KeysMissing, crypto.KeysInvalid):
        raise WebhookApiError("WEBHOOK_KEYS_MISSING") from None


def check_url(organization_id, workspace_id, url: str) -> UrlDecision:
    """The section 6.1 rules and the org and workspace allowlists (6.3), on the URL's text only."""
    lists = allowed_host_lists(organization_id, workspace_id)
    profile = config.profile()
    d = url_decision(url, profile, config.public_suffix, lists)
    if d.allow:
        return d
    if d.code == CODE_HOST_NOT_ALLOWED:
        if d.rule == "policy.org_allowed_hosts":
            msg = (
                f"Your organization only allows webhooks to {', '.join(lists['org_allowed_hosts'])}. An org admin "
                "can change this in Settings > Integrations > Webhook policy."
            )
        else:
            msg = (
                f"This workspace only allows webhooks to {', '.join(lists['workspace_allowed_hosts'])}. A "
                "workspace admin can change this in Settings > Integrations > Webhook policy."
            )
        raise WebhookApiError(d.code, msg, attr="url", rule=d.rule)
    message = _URL_COPY.get(d.rule or "")
    if d.rule == "url.scheme" and not profile.hosted and profile.allow_http:
        message = "Webhook URLs must use HTTPS or HTTP."
    raise WebhookApiError(d.code, message, attr="url", rule=d.rule)


def check_headers(headers: Mapping[str, str]) -> dict:
    if not isinstance(headers, Mapping):
        raise WebhookApiError("WEBHOOK_HEADER_INVALID", attr="headers")
    problems = custom_header_problems(headers)
    for p in problems:
        if p.code == "reserved":
            raise WebhookApiError(
                "WEBHOOK_HEADER_RESERVED",
                f"{p.name} is reserved: Future AGI sets it on every request.",
                attr="headers",
                header=p.name,
            )
    if problems:
        p = problems[0]
        raise WebhookApiError(
            "WEBHOOK_HEADER_INVALID", attr="headers", problem=p.code, header=p.name
        )
    return {str(k): str(v) for k, v in headers.items()}


def check_secret(secret: str) -> str:
    """Bring your own secret (section 5.3): ``whsec_`` + base64 of 24-64 bytes."""
    try:
        secret_key(secret)
    except SecretInvalid:
        raise WebhookApiError("WEBHOOK_SECRET_INVALID", attr="secret") from None
    return secret


def _filter_values(key: str, values, where: str) -> list[str]:
    if not isinstance(values, list) or len(values) > MAX_FILTER_VALUES:
        raise WebhookApiError(
            "WEBHOOK_FILTER_INVALID",
            f"{key} must be a list of at most {MAX_FILTER_VALUES} values.",
            attr=where,
        )
    out: list[str] = []
    for v in values:
        if not isinstance(v, str) or not v.strip() or len(v) > MAX_FILTER_VALUE_CHARS:
            raise WebhookApiError(
                "WEBHOOK_FILTER_INVALID",
                f"{key} values must be non-empty text of at most {MAX_FILTER_VALUE_CHARS} characters.",
                attr=where,
            )
        allowed = FILTER_ENUMS.get(key)
        if allowed and v not in allowed:
            raise WebhookApiError(
                "WEBHOOK_FILTER_INVALID",
                f"{key} takes {', '.join(allowed)}.",
                attr=where,
            )
        if v not in out:
            out.append(v)
    return out


def check_subscriptions(subs) -> list[dict]:
    """1-50 subscriptions, each an exact catalog type or a group wildcard, with the filters of its family. An empty
    filter list is dropped (it would filter nothing, or, for ``statuses``, everything)."""
    if not isinstance(subs, list) or not subs:
        raise WebhookApiError(
            "WEBHOOK_EVENT_TYPE_UNKNOWN",
            "Choose at least one event type.",
            attr="subscriptions",
        )
    if len(subs) > fanout.MAX_SUBSCRIPTIONS:
        raise WebhookApiError(
            "WEBHOOK_ENDPOINT_LIMIT",
            f"An endpoint has at most {fanout.MAX_SUBSCRIPTIONS} subscriptions.",
            attr="subscriptions",
        )
    out: list[dict] = []
    seen: set[str] = set()
    for i, sub in enumerate(subs):
        where = f"subscriptions[{i}]"
        t = sub.get("event_type") if isinstance(sub, Mapping) else None
        if t not in FILTER_KEYS:
            raise WebhookApiError(
                "WEBHOOK_EVENT_TYPE_UNKNOWN",
                f"{t!r} is not in the webhooks event catalog.",
                attr=f"{where}.event_type",
            )
        if t in seen:
            raise WebhookApiError(
                "WEBHOOK_FILTER_INVALID",
                f"{t} is listed twice.",
                attr=f"{where}.event_type",
            )
        seen.add(t)
        filters = sub.get("filters") or {}
        if not isinstance(filters, Mapping):
            raise WebhookApiError("WEBHOOK_FILTER_INVALID", attr=f"{where}.filters")
        clean: dict = {}
        for key, values in filters.items():
            if key not in FILTER_KEYS[t]:
                raise WebhookApiError(
                    "WEBHOOK_FILTER_INVALID",
                    f"{key} is not a filter of {t}.",
                    attr=f"{where}.filters.{key}",
                )
            vals = _filter_values(key, values, f"{where}.filters.{key}")
            if vals:
                clean[key] = vals
        out.append({"event_type": t, "filters": clean})
    return out


def check_endpoint_limits(organization_id, workspace_id) -> None:
    """50 per workspace (the org policy's ``max_endpoints_per_workspace``), 200 per org (section 7.8). Called after
    :func:`note_write`, under the organization's API lock, so two replicas cannot both pass the count."""
    live = Endpoint.objects.filter(
        organization_id=organization_id, deleted=False
    ).exclude(kind="cli_listen")
    if live.count() >= MAX_ENDPOINTS_PER_ORG:
        raise WebhookApiError(
            "WEBHOOK_ENDPOINT_LIMIT",
            f"Your organization has {MAX_ENDPOINTS_PER_ORG} webhooks, the most it can have.",
        )
    if workspace_id is not None:
        org, _ = policies(organization_id, None)
        cap = org.max_endpoints_per_workspace if org else 50
        if live.filter(workspace_id=workspace_id).count() >= cap:
            raise WebhookApiError(
                "WEBHOOK_ENDPOINT_LIMIT",
                f"This workspace has {cap} webhooks, the most it can have.",
            )


def count_recent(organization_id, kind: str, seconds: int, **match) -> int:
    since = timezone.now() - timedelta(seconds=seconds)
    return ApiAction.objects.filter(
        organization_id=organization_id, kind=kind, created_at__gte=since, **match
    ).count()


def note_write(caller, workspace_id=None, endpoint=None) -> None:
    """Management writes: 120/min per org (section 7.8); the refused write is not counted. The first step of every
    API write's transaction: it takes the organization's API lock (``api_locks``, the first lock in the one order), so
    this count and every count after it in the transaction are atomic across replicas."""
    lock_org(caller.organization_id)
    if count_recent(caller.organization_id, "write", 60) >= WRITES_PER_MINUTE_PER_ORG:
        raise WebhookApiError("WEBHOOK_RATE_LIMITED", retry_after_s=60)
    ApiAction.objects.create(
        organization_id=caller.organization_id,
        workspace_id=workspace_id,
        actor_id=caller.user_id,
        kind="write",
        endpoint=endpoint,
    )
