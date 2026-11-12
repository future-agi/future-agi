"""An organization's SIEM destinations and dead letters, changed by its admins.

Every change runs in one transaction with its audit row (the existing audit
mechanism) and is undone when that row cannot be written, as span-capture
consent is. Audit rows carry names, kinds, streams, the URL's host and the
enabled flag: never a secret, a header value or a URL's path. Removing a
secret is a change like any other and is audited; a webhook's signing secret
is never removed by an update (only rotated). Creates hold the organization's
row lock while they count, so concurrent creates cannot pass the limit.
"""

import re
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction

from accounts.models import Organization
from accounts.models.audit_log import AuditScope
from agentcc.models.siem import STREAMS, AgentccSIEMDelivery, AgentccSIEMDestination
from agentcc.services import siem_hec, siem_signing, siem_transport
from agentcc.services.credential_manager import ENCRYPTED_PREFIX, encrypt_token
from agentcc.services.siem_settings import export_enabled
from tfc.utils import audit

MAX_DESTINATIONS = 5
CREATED = "agentcc.siem_destination_created"
UPDATED = "agentcc.siem_destination_updated"
ENABLED = "agentcc.siem_destination_enabled"
DISABLED = "agentcc.siem_destination_disabled"
ROTATED = "agentcc.siem_destination_secret_rotated"
DELETED = "agentcc.siem_destination_deleted"
RETRIED = "agentcc.siem_delivery_retried"
DISCARDED = "agentcc.siem_delivery_discarded"

Kind = AgentccSIEMDestination.Kind
Status = AgentccSIEMDelivery.Status

_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_HEADER_VALUE = re.compile(r"^[\x20-\x7e]{1,1024}$")
_REFUSED_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "content-type",
        "cookie",
        "expect",
        "host",
        "proxy-authorization",
        "te",
        "transfer-encoding",
        "upgrade",
        "user-agent",
        "webhook-id",
        "webhook-signature",
        "webhook-timestamp",
    }
)
_OTLP_SIGNAL_PATHS = ("/v1/traces", "/v1/logs")


class DestinationError(Exception):
    def __init__(self, message, code="invalid", status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


class AuditUnavailable(Exception):
    """The audit row could not be written, so the change was not made."""


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise DestinationError("A destination needs a name of 1 to 120 characters.")
    if any(ord(ch) < 0x20 for ch in value):
        raise DestinationError("A destination's name cannot hold control characters.")
    return value.strip()


def _kind(value):
    if value not in Kind.values:
        raise DestinationError("A destination's kind is webhook, otlp_http or hec.")
    return value


def _streams(value):
    if not isinstance(value, list) or not value or any(s not in STREAMS for s in value):
        raise DestinationError("Streams are one or both of audit_events and spans.")
    return list(dict.fromkeys(value))


def _url(kind, value):
    try:
        siem_transport.check_destination_url(value)
    except ValueError as exc:
        raise DestinationError(str(exc)) from None
    if kind == Kind.OTLP_HTTP:
        value = value.rstrip("/")
        if value.endswith(_OTLP_SIGNAL_PATHS):
            raise DestinationError(
                "Give the collector's base URL: the gateway adds /v1/traces "
                "and /v1/logs itself."
            )
    return value


def _header(kind, value):
    if not value:
        return ""
    if kind != Kind.OTLP_HTTP:
        raise DestinationError("Only an OTLP/HTTP destination takes an auth header.")
    if (
        not isinstance(value, str)
        or not _HEADER.match(value)
        or (value.lower() in _REFUSED_HEADERS)
    ):
        raise DestinationError("That auth header name cannot be used.")
    return value


def _header_value(value):
    if (
        not isinstance(value, str)
        or not _HEADER_VALUE.match(value)
        or value.startswith(ENCRYPTED_PREFIX)
    ):
        raise DestinationError(
            "An auth header value is 1 to 1024 printable characters."
        )
    return value


def _host(url):
    return urlsplit(url).hostname or ""


def _record(organization, actor, action, target_id, changes, metadata=None):
    entry = audit.log_audit(
        organization=organization,
        actor=actor,
        action=action,
        scope=AuditScope.ORGANIZATION,
        target_id=str(target_id),
        changes=changes,
        metadata=metadata or {},
    )
    if entry is None:
        raise AuditUnavailable()


def _set_secret(destination, secret):
    destination.encrypted_secret = encrypt_token(secret) if secret else None
    destination.secret_hint = (
        "" if destination.kind == Kind.HEC else (secret[-4:] if secret else "")
    )


def create(organization, actor, data):
    """Add a disabled destination; returns it and its new signing secret
    (webhooks only, shown this once)."""
    kind = _kind(data.get("kind"))
    destination = AgentccSIEMDestination(
        organization=organization,
        name=_name(data.get("name")),
        kind=kind,
        url=_url(kind, data.get("url")),
        streams=_streams(data.get("streams")),
        auth_header=_header(kind, data.get("auth_header")),
        created_by=actor,
    )
    secret = None
    if kind != Kind.HEC and data.get("token"):
        raise DestinationError("Only a HEC destination takes a token.")
    if kind == Kind.HEC:
        siem_hec.validate_streams(destination.streams)
        if data.get("auth_value"):
            raise DestinationError("HEC uses a token, not an auth header value.")
        _set_secret(destination, _header_value(data.get("token")))
    elif kind == Kind.WEBHOOK:
        if data.get("auth_value"):
            raise DestinationError(
                "A webhook is signed; it takes no auth header value."
            )
        secret = siem_signing.new_signing_secret()
        _set_secret(destination, secret)
    elif destination.auth_header:
        _set_secret(destination, _header_value(data.get("auth_value")))
    elif data.get("auth_value"):
        raise DestinationError("An auth header value needs an auth header name.")
    else:
        _set_secret(destination, "")
    with transaction.atomic():
        # The org's row serializes its creates: locking only the existing
        # destinations locks nothing when there are none yet. FOR NO KEY
        # UPDATE, as the config writer's org lock (org_config_writer.lock_org):
        # inserts of the org's other rows are not held up.
        Organization.objects.select_for_update(no_key=True).only("id").get(
            pk=organization.pk
        )
        existing = AgentccSIEMDestination.objects.filter(organization=organization)
        if existing.count() >= MAX_DESTINATIONS:
            raise DestinationError(
                f"An organization has at most {MAX_DESTINATIONS} SIEM destinations."
            )
        try:
            with transaction.atomic():
                destination.save()
        except IntegrityError:
            raise DestinationError(
                "Another destination already has this name."
            ) from None
        _record(
            organization,
            actor,
            CREATED,
            destination.id,
            {
                "name": destination.name,
                "kind": kind,
                "streams": destination.streams,
                "url_host": _host(destination.url),
            },
        )
    return destination, secret


def update(destination, actor, data):
    """Apply a PATCH; enabling needs the deployment's export switch."""
    old = {
        "name": destination.name,
        "url_host": _host(destination.url),
        "streams": list(destination.streams or []),
        "auth_header": destination.auth_header,
    }
    if "name" in data:
        destination.name = _name(data["name"])
    if "url" in data:
        destination.url = _url(destination.kind, data["url"])
    if "streams" in data:
        destination.streams = _streams(data["streams"])
    if destination.kind == Kind.HEC:
        siem_hec.validate_streams(destination.streams)
        if "auth_value" in data or "auth_header" in data:
            raise DestinationError("HEC uses a token, not a configurable auth header.")
    if "token" in data and destination.kind != Kind.HEC:
        raise DestinationError("Only a HEC destination takes a token.")
    had_secret = bool(destination.encrypted_secret)
    if "auth_header" in data:
        if destination.kind != Kind.OTLP_HTTP:
            # Even an empty one: it would otherwise clear the signing secret.
            raise DestinationError(
                "Only an OTLP/HTTP destination takes an auth header; a webhook "
                "is signed, and its secret changes only by rotation."
            )
        destination.auth_header = _header(destination.kind, data["auth_header"])
        if not destination.auth_header:
            _set_secret(destination, "")
    value_changed = False
    if "token" in data:
        _set_secret(destination, _header_value(data["token"]))
        value_changed = True
    if data.get("auth_value"):
        if not destination.auth_header:
            raise DestinationError("An auth header value needs an auth header name.")
        _set_secret(destination, _header_value(data["auth_value"]))
        value_changed = True
    elif (
        destination.kind == Kind.OTLP_HTTP
        and destination.auth_header
        and (not destination.encrypted_secret)
    ):
        raise DestinationError("An auth header needs its value.")
    enabled = data.get("enabled", destination.enabled)
    if not isinstance(enabled, bool):
        raise DestinationError("enabled is true or false.")
    was_enabled = destination.enabled
    if enabled and not was_enabled and not export_enabled():
        raise DestinationError(
            "SIEM export is not turned on for this deployment.",
            code="siem_export_unavailable",
            status=409,
        )
    destination.enabled = enabled
    new = {
        "name": destination.name,
        "url_host": _host(destination.url),
        "streams": list(destination.streams),
        "auth_header": destination.auth_header,
    }
    changes = {k: {"old": old[k], "new": new[k]} for k in old if old[k] != new[k]}
    if value_changed:
        changes["auth_value"] = "changed"
    elif had_secret and not destination.encrypted_secret:
        changes["auth_value"] = "cleared"
    if "url" in data and "url_host" not in changes:
        changes["url"] = "changed"
    with transaction.atomic():
        try:
            with transaction.atomic():
                destination.save()
        except IntegrityError:
            raise DestinationError(
                "Another destination already has this name."
            ) from None
        if changes:
            _record(destination.organization, actor, UPDATED, destination.id, changes)
        if enabled != was_enabled:
            _record(
                destination.organization,
                actor,
                ENABLED if enabled else DISABLED,
                destination.id,
                {"enabled": {"old": was_enabled, "new": enabled}},
                {"name": destination.name},
            )
    return destination


def rotate_secret(destination, actor, data):
    """A webhook gets a new signing secret (returned once); an OTLP/HTTP
    destination takes the new header value it is given."""
    if destination.kind == Kind.HEC:
        _set_secret(destination, _header_value(data.get("token")))
        secret = None
    elif destination.kind == Kind.WEBHOOK:
        secret = siem_signing.new_signing_secret()
        _set_secret(destination, secret)
    else:
        if not destination.auth_header:
            raise DestinationError("This destination has no auth header to rotate.")
        _set_secret(destination, _header_value(data.get("auth_value")))
        secret = None
    with transaction.atomic():
        destination.save()
        _record(
            destination.organization,
            actor,
            ROTATED,
            destination.id,
            {"secret": "rotated"},
            {"name": destination.name},
        )
    return secret


def delete(destination, actor):
    with transaction.atomic():
        _record(
            destination.organization,
            actor,
            DELETED,
            destination.id,
            {"name": destination.name, "kind": destination.kind},
        )
        destination.delete()


def retry(delivery, actor):
    if delivery.status != Status.DEAD_LETTER:
        raise DestinationError(
            "Only a dead-lettered delivery can be retried.",
            code="delivery_not_dead_letter",
            status=409,
        )
    with transaction.atomic():
        delivery.status = Status.PENDING
        delivery.attempts = 0
        delivery.next_retry_at = None
        delivery.save(update_fields=["status", "attempts", "next_retry_at"])
        _record(
            delivery.organization,
            actor,
            RETRIED,
            delivery.id,
            {"status": {"old": Status.DEAD_LETTER, "new": Status.PENDING}},
            {"destination_id": str(delivery.destination_id)},
        )
    return delivery


def discard(delivery, actor):
    if delivery.status not in (Status.DEAD_LETTER, Status.PENDING):
        raise DestinationError(
            "Only a pending or dead-lettered delivery can be discarded.",
            code="delivery_not_discardable",
            status=409,
        )
    old = delivery.status
    with transaction.atomic():
        delivery.status = Status.DISCARDED
        delivery.next_retry_at = None
        delivery.save(update_fields=["status", "next_retry_at"])
        _record(
            delivery.organization,
            actor,
            DISCARDED,
            delivery.id,
            {"status": {"old": old, "new": Status.DISCARDED}},
            {"destination_id": str(delivery.destination_id)},
        )
    return delivery
