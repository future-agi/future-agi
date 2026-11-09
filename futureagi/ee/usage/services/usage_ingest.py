"""usage-event-v1 ingest: one validated, idempotent ``UsageEventLog`` row per event.

Both transports end here (usage-event-v1 sections 1 and 4): the out-of-process
``POST /usage/ledger/v1/events/`` (service credential, 1-500 items) and
in-process producers that call ``ingest_events`` with their ``source.system``.
Per item, in order, the first failure wins:

1. schema (``$defs/Event``) -> ``malformed_event``;
2. a version-5 id -> ``event_id_not_derived``;
3. the credential allows ``source.system`` and ``product`` -> ``source_not_permitted``;
4. an out-of-process credential is bound to ``org_id`` (and, for ``ee_daemon``,
   ``workspace_id``) -> ``org_not_permitted``, before any org lookup;
5. the org exists -> ``unknown_org``;
6. the workspace is in the org -> ``workspace_not_in_org``;
7. the catalog (price-catalog-v1 section 1) knows the dimension and event type,
   maps one to the other, and agrees on product and unit; and
   ``gateway_managed_spend`` maps only to a ``prepaid_only`` dimension (UMP d);
8. ``occurred_at <= received_at + 5 min`` and ``received_at - occurred_at <= 35 days``;
9. ``INSERT ... ON CONFLICT (event_id) DO NOTHING``: an equal ``payload_sha256`` is
   ``duplicate``, a different one ``idempotency_conflict``. The stored row is
   never overwritten.

Every refusal is a ``UsageEventRefusal`` row and a
``usage_ingest_refused_total{reason,system}`` alarm (structlog plus a tagged
Sentry message), written before the item's result is returned. ``accepted`` and
``duplicate`` are returned only after the event row committed.

``quantity / native_to_display_divisor`` is stored rounded half up to 6
places in ``amount_display`` and must have at most 10 integer digits
(``Decimal(16,6)``), else ``malformed_event``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from tfc.logging.sentry import capture_message

logger = structlog.get_logger(__name__)

FUTURE_BOUND = timedelta(minutes=5)
LATENESS_WINDOW = timedelta(days=35)
MAX_ITEMS = 500
IN_PROCESS_SYSTEMS = frozenset({"fa_platform", "fa_gateway_ingest", "fa_gateway_settlement"})
CONTRACTS_DIR = Path(__file__).resolve().parents[1] / "contracts"
TOKEN_PREFIX = "fa_ledger_"


@dataclass(frozen=True)
class IngestItemResult:
    index: int
    event_id: str | None
    status: str  # accepted, duplicate or refused
    reason: str | None = None
    detail: str | None = None

    def as_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "index": self.index,
            "event_id": self.event_id,
            "status": self.status,
        }
        if self.reason is not None:
            body["reason"] = self.reason
        if self.detail:
            body["detail"] = self.detail[:500]
        return body


class _Refused(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def derive_event_id(name: str) -> str:
    """``uuid5(NAMESPACE_URL, name)``, the usage-event-v1 section 3 rule."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def canonical_bytes(event: dict[str, Any]) -> bytes:
    """The RFC 8785 form of an event (it holds no JSON numbers)."""
    return json.dumps(
        event, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def payload_sha256(event: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(event)).hexdigest()


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _event_validator():
    """``$defs/Event`` with ``event_id`` relaxed to any UUID: the version-5 rule
    is its own reason (check 2), after the schema (check 1)."""
    from jsonschema import Draft202012Validator

    schema = json.loads((CONTRACTS_DIR / "usage-event-v1.schema.json").read_text())
    schema = copy.deepcopy(schema)
    schema["$defs"]["Event"]["properties"]["event_id"] = {"$ref": "#/$defs/Uuid"}
    root = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/Event"}
    return Draft202012Validator(root)


def _schema_errors(item: Any) -> str | None:
    errors = sorted(_event_validator().iter_errors(item), key=lambda e: list(e.path))
    if not errors:
        return None
    first = errors[0]
    where = "/".join(str(p) for p in first.path) or "(root)"
    return f"{where}: {first.message}"[:500]


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def create_service_credential(
    name: str,
    *,
    systems: list[str],
    products: list[str],
    bindings: list[tuple[Any, str | None]],
) -> tuple[Any, str]:
    """Create a credential; returns it and its token (shown once, never stored).

    ``bindings`` is ``[(org, workspace_id or None), ...]``.
    """
    import secrets

    from ee.usage.models.billing_ledger import LedgerCredentialBinding, LedgerServiceCredential

    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with transaction.atomic():
        credential = LedgerServiceCredential.objects.create(
            name=name,
            token_sha256=token_sha256(token),
            systems=list(systems),
            products=list(products),
        )
        for org, workspace_id in bindings:
            LedgerCredentialBinding.objects.create(
                credential=credential, organization=org, workspace_id=workspace_id
            )
    return credential, token


def authenticate(token: str) -> Any:
    """The ``LedgerServiceCredential`` whose token hash matches (enabled or not), else None."""
    from ee.usage.models.billing_ledger import LedgerServiceCredential

    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
        return None
    return LedgerServiceCredential.objects.filter(token_sha256=token_sha256(token)).first()


def _check(item: Any, *, credential, system_in_process, received_at) -> dict[str, Any]:
    """Run checks 1-8 on one item; returns what the insert needs, or raises _Refused."""
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from ee.usage.services import rating

    if not isinstance(item, dict):
        raise _Refused("malformed_event", "an event is a JSON object")
    problem = _schema_errors(item)
    if problem:
        raise _Refused("malformed_event", problem)
    event_id = uuid.UUID(item["event_id"])
    if event_id.version != 5 or event_id.variant != uuid.RFC_4122:
        raise _Refused("event_id_not_derived", "event_id must be a derived version-5 UUID")
    system, product = item["source"]["system"], item["product"]
    if credential is not None:
        if system not in (credential.systems or []) or product not in (credential.products or []):
            raise _Refused("source_not_permitted", f"{system}/{product} is not allowed")
        bindings = credential.bindings.filter(organization_id=item["org_id"])
        if system == "ee_daemon":
            bound = bindings.filter(workspace_id=item["workspace_id"]).exists()
        else:
            bound = bindings.exists()
        if not bound:
            raise _Refused("org_not_permitted", "this credential is not bound to the org")
    elif system != system_in_process:
        raise _Refused("source_not_permitted", f"{system} is not {system_in_process}")
    org = Organization.objects.filter(pk=item["org_id"]).first()
    if org is None:
        raise _Refused("unknown_org", "no such organization")
    workspace_id = item["workspace_id"]
    if workspace_id and not Workspace.no_workspace_objects.filter(
        pk=workspace_id, organization=org
    ).exists():
        raise _Refused("workspace_not_in_org", "the workspace is not in the org")
    occurred = _instant(item["occurred_at"])
    catalog = rating.catalog_for_validation(occurred)
    dimension = catalog.dimensions.get(item["dimension"]) if catalog else None
    if dimension is None:
        raise _Refused("unknown_dimension", f"{item['dimension']!r} is not a catalog dimension")
    mapped = catalog.event_types.get(item["event_type"])
    if mapped is None:
        raise _Refused("unknown_event_type", f"{item['event_type']!r} is not a catalog event type")
    if mapped != dimension.id:
        raise _Refused(
            "event_type_dimension_mismatch", f"{item['event_type']} maps to {mapped}"
        )
    if item["event_type"] == rating.MANAGED_SPEND_EVENT_TYPE and (
        dimension.settlement_class != "prepaid_only"
    ):
        raise _Refused("event_type_dimension_mismatch", "UMP guard d: managed spend is prepaid only")
    if dimension.product != product:
        raise _Refused("product_mismatch", f"{dimension.id} is product {dimension.product}")
    if dimension.native_unit != item["unit"]:
        raise _Refused("unit_mismatch", f"{dimension.id} is in {dimension.native_unit}")
    quantity = Decimal(item["quantity"])
    display = (quantity / Decimal(dimension.native_to_display_divisor)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )
    if display.adjusted() >= 10:
        raise _Refused("malformed_event", "quantity does not fit amount_display")
    if occurred > received_at + FUTURE_BOUND:
        raise _Refused("occurred_at_in_future", "more than 5 minutes ahead")
    if received_at - occurred > LATENESS_WINDOW:
        raise _Refused("beyond_lateness_window", "older than 35 days")
    return {
        "event_id": event_id,
        "org": org,
        "occurred": occurred,
        "quantity": quantity,
        "display": display,
        "sha": payload_sha256(item),
    }


def _store(item: dict[str, Any], ok: dict[str, Any], received_at: datetime) -> str:
    """Insert the event; ``accepted``, ``duplicate`` or raises _Refused(idempotency_conflict)."""
    from ee.usage.models.billing_ledger import UsageEventRecord
    from ee.usage.models.usage import UsageEventLog

    attribution, source = item["attribution"], item["source"]
    attributes = dict(item["attributes"])
    period = ok["occurred"].strftime("%Y-%m")
    try:
        with transaction.atomic():
            log = UsageEventLog.objects.create(
                event_id=ok["event_id"],
                organization=ok["org"],
                workspace_id=item["workspace_id"],
                event_type=item["event_type"],
                dimension=item["dimension"],
                amount_raw=ok["quantity"],
                amount_display=ok["display"],
                period=period,
                timestamp=ok["occurred"],
                properties={**attributes, "source": source["system"], "source_id": source["ref"]},
            )
            UsageEventRecord.objects.create(
                event=log,
                organization=ok["org"],
                event_uuid=ok["event_id"],
                product=item["product"],
                unit=item["unit"],
                source_system=source["system"],
                source_ref=source["ref"],
                agent_id=attribution["agent_id"] or "",
                agent_run_id=attribution["agent_run_id"] or "",
                api_key_id=attribution["api_key_id"] or "",
                purpose=attribution["purpose"] or "",
                request_id=str(attributes.get("request_id") or "")[:255],
                payload_sha256=ok["sha"],
                schema_version=item["v"],
                received_at=received_at,
                occurred_at=ok["occurred"],
                period=period,
            )
        return "accepted"
    except IntegrityError:
        pass
    existing = UsageEventLog.objects.filter(event_id=ok["event_id"]).first()
    record = UsageEventRecord.objects.filter(event_uuid=ok["event_id"]).first()
    if existing is None:
        raise _Refused("idempotency_conflict", "the event id is held by another record")
    if record is not None and record.payload_sha256:
        if record.payload_sha256 == ok["sha"]:
            return "duplicate"
        raise _Refused("idempotency_conflict", "the same event id with another payload")
    same = (
        existing.organization_id == ok["org"].pk
        and existing.event_type == item["event_type"]
        and existing.amount_raw == ok["quantity"]
    )
    if same:
        return "duplicate"
    raise _Refused("idempotency_conflict", "the same event id with another payload")


def _refuse(index, item, reason, detail, *, credential, system, received_at) -> IngestItemResult:
    from ee.usage.models.billing_ledger import UsageEventRefusal

    event_id = None
    if isinstance(item, dict):
        try:
            event_id = str(uuid.UUID(str(item.get("event_id"))))
        except (ValueError, TypeError, AttributeError):
            event_id = None
    sha = ""
    payload = None
    if isinstance(item, dict):
        try:
            sha = payload_sha256(item)
            payload = item
        except (TypeError, ValueError):
            payload = None
    UsageEventRefusal.objects.create(
        received_at=received_at,
        credential=credential,
        source_system=str(system or "")[:32],
        item_index=index,
        event_id=event_id,
        reason=reason,
        detail=detail[:500],
        payload_sha256=sha,
        payload=payload,
    )
    logger.warning(
        "usage_ingest_refused", reason=reason, system=system, index=index, event_id=event_id
    )
    capture_message(
        "usage_ingest_refused",
        level="warning",
        tags={"alarm": "usage_ingest_refused_total", "reason": reason, "system": str(system)},
    )
    return IngestItemResult(index, event_id, "refused", reason, detail or None)


def ingest_events(
    items: list[Any],
    *,
    credential: Any = None,
    in_process_system: str | None = None,
    received_at: datetime | None = None,
) -> list[IngestItemResult]:
    """Ingest ``items`` (each a dict, validated on its own) in request order.

    Exactly one of ``credential`` (a ``LedgerServiceCredential``, the HTTP route)
    and ``in_process_system`` (one of ``IN_PROCESS_SYSTEMS``) is given.
    """
    if (credential is None) == (in_process_system is None):
        raise ValueError("give exactly one of credential and in_process_system")
    if in_process_system is not None and in_process_system not in IN_PROCESS_SYSTEMS:
        raise ValueError(f"{in_process_system!r} is not an in-process system")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError(f"an ingest carries 1 to {MAX_ITEMS} events")
    received_at = received_at or timezone.now()
    results = []
    for index, item in enumerate(items):
        system = (
            item.get("source", {}).get("system")
            if isinstance(item, dict) and isinstance(item.get("source"), dict)
            else in_process_system
        )
        try:
            ok = _check(
                item,
                credential=credential,
                system_in_process=in_process_system,
                received_at=received_at,
            )
            status = _store(item, ok, received_at)
            results.append(IngestItemResult(index, str(ok["event_id"]), status))
        except _Refused as refused:
            results.append(
                _refuse(
                    index,
                    item,
                    refused.reason,
                    refused.detail,
                    credential=credential,
                    system=system,
                    received_at=received_at,
                )
            )
    return results
