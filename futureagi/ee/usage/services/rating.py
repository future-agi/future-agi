"""Catalog selection and the phase-2a rater (PLAN 1.4, usage-event-v1 sections 6-8).

**The catalog.** The one price catalog lives in the private future-agi/ee repo
(``catalog/price-catalog.v<N>.json``), mounted at runtime under
``settings.BILLING_PRICE_CATALOG_DIR`` (default ``<BASE_DIR>/ee/cloud/catalog``).
This public tree never holds a price: ``LedgerCatalog`` reads only ids,
products, units, divisors, settlement classes, event-type mappings and
``model_pricing.markup_bps`` (public by price-catalog-v1 section 3). Which
version a consumer may use is price-catalog-v1 section 1:

- validation (ingest): the approved version in force at ``occurred_at``, else
  the newest of any status;
- stamping and rating: an approved version in force, or, in a test-mode
  deployment started with ``BILLING_CATALOG_ALLOW_UNCONFIRMED``, the newest
  unconfirmed one. The flag is refused (``ImproperlyConfigured``) with an
  ``sk_live_``/``rk_live_`` Stripe key, ``STRIPE_MODE=live`` or a production
  ``ENV_TYPE`` (DT15); ``catalog_in_force`` then ignores it and logs an error.

No catalog directory (OSS, a tree without the private overlay) means no
catalog: validation refuses ``unknown_dimension`` and stamping writes null. The
gateway drain never fails for a missing catalog. A catalog that maps
``gateway_managed_spend`` to a dimension that is not ``prepaid_only`` loses that
one mapping at load, with the named error ``ledger_catalog_guard_d`` (UMP d).

**The phase-2a rater** is the I1 linker. Every ``UsageEventLog`` row ends with
exactly one of ``rated_entry`` and ``not_billable_reason`` on its
``UsageEventRecord``:

- ``managed_spend`` is always the managed-credit ledger: linked to the SPEND
  entry of its request id (usage-event-v1 section 6);
- every other dimension follows the cutover rule by ``occurred_at``: with
  ``cutover_at`` null or later, ``legacy_period`` (FA dimensions) or
  ``legacy_ee`` (agents, sandboxes, deployments). A ledger-path platform event
  is left unrated and counted (``pending_ledger_path``), so I1 fails loudly:
  graduated rating is phase 2b.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

logger = structlog.get_logger(__name__)


class NotBillableReason:
    LEGACY_PERIOD = "legacy_period"
    LEGACY_EE = "legacy_ee"
    LEGACY_PERIOD_CLOSED = "legacy_period_closed"
    FAILED_INFRA = "failed_infra"
    MARKETPLACE_DIMENSION_UNMAPPED = "marketplace_dimension_unmapped"


EE_PRODUCTS = frozenset({"agents", "sandboxes", "deployments"})
MANAGED_SPEND_DIMENSION = "managed_spend"
MANAGED_SPEND_EVENT_TYPE = "gateway_managed_spend"
ALLOW_UNCONFIRMED_SETTING = "BILLING_CATALOG_ALLOW_UNCONFIRMED"
CATALOG_FILE = re.compile(r"^price-catalog\.v(0|[1-9][0-9]{0,5})\.json$")
LIVE_KEY_PREFIXES = ("sk_live_", "rk_live_")
PRODUCTION_ENV_TYPES = frozenset({"prod", "production"})


@dataclass(frozen=True)
class CatalogDimension:
    id: str
    product: str
    native_unit: str
    native_to_display_divisor: int
    settlement_class: str  # prepaid_only, wallet_then_invoice or invoice


@dataclass(frozen=True)
class LedgerCatalog:
    """The price-free part of one catalog version."""

    catalog_id: str
    version: int
    status: str  # unconfirmed or approved
    effective_at: datetime | None
    dimensions: Mapping[str, CatalogDimension]
    event_types: Mapping[str, str]  # event_type -> dimension id
    markup_bps: int | None


@dataclass(frozen=True)
class RateResult:
    linked: int  # rated to an entry (managed_spend -> its SPEND)
    not_billable: int  # given a not_billable_reason
    waiting: int  # managed_spend whose SPEND is not written yet
    pending_ledger_path: int  # ledger-path platform events (phase 2b)


# --- the catalog -------------------------------------------------------------

_cache: dict[str, tuple[float, tuple, tuple[LedgerCatalog, ...]]] = {}
# check_usage asks on every billable action: re-list the directory at most
# this often. A new catalog version takes effect within this many seconds.
CATALOG_RECHECK_SECONDS = 60.0


def catalog_dir() -> Path:
    configured = getattr(settings, "BILLING_PRICE_CATALOG_DIR", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "ee" / "cloud" / "catalog"


def reset_catalog_cache() -> None:
    _cache.clear()


def _value(node):
    return node.get("value") if isinstance(node, dict) else node


def _parse_instant(raw) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)


def _parse(path: Path) -> LedgerCatalog:
    doc = json.loads(path.read_text(encoding="utf-8"))
    dimensions = {}
    for row in doc.get("dimensions") or []:
        dim = CatalogDimension(
            id=str(row["id"]),
            product=str(row["product"]),
            native_unit=str(row["native_unit"]),
            native_to_display_divisor=int(row.get("native_to_display_divisor") or 1),
            settlement_class=str(_value(row.get("settlement_class")) or ""),
        )
        dimensions[dim.id] = dim
    event_types = {}
    for row in doc.get("event_types") or []:
        event_type, dimension = str(row["event_type"]), str(row["dimension"])
        if event_type == MANAGED_SPEND_EVENT_TYPE and (
            dimensions.get(dimension) is None
            or dimensions[dimension].settlement_class != "prepaid_only"
        ):
            # UMP guard (d): never invoiced on top of the ledger debit.
            logger.error(
                "ledger_catalog_guard_d",
                catalog=str(path.name),
                event_type=event_type,
                dimension=dimension,
            )
            continue
        event_types[event_type] = dimension
    markup = _value((doc.get("model_pricing") or {}).get("markup_bps"))
    return LedgerCatalog(
        catalog_id=str(doc["catalog_id"]),
        version=int(doc["version"]),
        status=str(doc.get("status") or "unconfirmed"),
        effective_at=_parse_instant(doc.get("effective_at")),
        dimensions=dimensions,
        event_types=event_types,
        markup_bps=markup if isinstance(markup, int) and not isinstance(markup, bool) else None,
    )


def catalogs() -> tuple[LedgerCatalog, ...]:
    """Every catalog version in the catalog directory, oldest first (cached)."""
    directory = catalog_dir()
    now = time.monotonic()
    hit = _cache.get(str(directory))
    if hit and now - hit[0] < CATALOG_RECHECK_SECONDS:
        return hit[2]
    try:
        files = sorted(
            (p for p in directory.iterdir() if CATALOG_FILE.match(p.name)),
            key=lambda p: int(CATALOG_FILE.match(p.name).group(1)),
        )
        stamp = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    except (FileNotFoundError, NotADirectoryError):
        _cache[str(directory)] = (now, (), ())
        return ()
    if hit and hit[1] == stamp:
        _cache[str(directory)] = (now, stamp, hit[2])
        return hit[2]
    loaded = []
    for path in files:
        try:
            loaded.append(_parse(path))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("ledger_catalog_unreadable", catalog=path.name, error=str(exc))
    result = tuple(sorted(loaded, key=lambda c: c.version))
    _cache[str(directory)] = (now, stamp, result)
    return result


def _approved_at(occurred_at: datetime) -> LedgerCatalog | None:
    approved = [
        c
        for c in catalogs()
        if c.status == "approved" and c.effective_at and c.effective_at <= occurred_at
    ]
    return max(approved, key=lambda c: c.effective_at) if approved else None


def catalog_for_validation(occurred_at: datetime) -> LedgerCatalog | None:
    """The version that validates an event's ids, products and units."""
    found = _approved_at(occurred_at)
    if found is not None:
        return found
    every = catalogs()
    return every[-1] if every else None


def _flag_problem() -> str | None:
    key = str(getattr(settings, "STRIPE_SECRET_KEY", "") or "")
    if key.startswith(LIVE_KEY_PREFIXES):
        return "a live Stripe key is configured"
    mode = str(getattr(settings, "STRIPE_MODE", "") or os.getenv("STRIPE_MODE", ""))
    if mode.lower() == "live":
        return "STRIPE_MODE is live"
    if str(getattr(settings, "ENV_TYPE", "")).lower() in PRODUCTION_ENV_TYPES:
        return "this is a production deployment"
    return None


def check_unconfirmed_flag() -> None:
    """Raise ``ImproperlyConfigured`` when the allow-unconfirmed flag is set
    with a live Stripe key, ``STRIPE_MODE=live`` or in production (DT15)."""
    if not getattr(settings, ALLOW_UNCONFIRMED_SETTING, False):
        return
    problem = _flag_problem()
    if problem:
        raise ImproperlyConfigured(
            f"{ALLOW_UNCONFIRMED_SETTING} is refused: {problem}. An unconfirmed "
            "price catalog may only rate usage in a test-mode deployment."
        )


def _flag_on() -> bool:
    if not getattr(settings, ALLOW_UNCONFIRMED_SETTING, False):
        return False
    problem = _flag_problem()
    if problem:
        logger.error("ledger_catalog_flag_refused", reason=problem)
        return False
    return True


def catalog_in_force(occurred_at: datetime) -> LedgerCatalog | None:
    """The version that stamps and rates usage at ``occurred_at``, or None."""
    if _flag_on():
        unconfirmed = [c for c in catalogs() if c.status == "unconfirmed"]
        if unconfirmed:
            return unconfirmed[-1]
    return _approved_at(occurred_at)


def catalog_version_at(occurred_at: datetime) -> str:
    """The ``catalog_id`` in force at ``occurred_at``, or "" when none is."""
    catalog = catalog_in_force(occurred_at)
    return catalog.catalog_id if catalog else ""


# --- the phase-2a rater ------------------------------------------------------


def _period_of(instant: datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m")


def _product_of(log) -> str:
    catalog = catalog_for_validation(log.timestamp)
    if catalog is not None and log.dimension in catalog.dimensions:
        return catalog.dimensions[log.dimension].product
    if log.dimension == MANAGED_SPEND_DIMENSION:
        return "gateway"
    return ""


def _outcome(record, cutover_cache: dict) -> tuple[str, object]:
    """("link", entry) | ("reason", text) | ("wait", None) | ("ledger", None)."""
    from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry

    if record.event.dimension == MANAGED_SPEND_DIMENSION:
        request_id = record.request_id or record.source_ref
        spend = (
            ManagedCreditEntry.objects.filter(
                organization_id=record.organization_id,
                kind=ManagedCreditEntry.Kind.SPEND,
                request_id=request_id,
            ).first()
            if request_id
            else None
        )
        return ("link", spend) if spend is not None else ("wait", None)
    org_id = record.organization_id
    if org_id not in cutover_cache:
        cutover_cache[org_id] = (
            ManagedCreditAccount.objects.filter(organization_id=org_id)
            .values_list("cutover_at", flat=True)
            .first()
        )
    cutover_at = cutover_cache[org_id]
    if cutover_at is None or record.occurred_at < cutover_at:
        if record.product in EE_PRODUCTS:
            return "reason", NotBillableReason.LEGACY_EE
        return "reason", NotBillableReason.LEGACY_PERIOD
    return "ledger", None


def _ensure_records(limit: int) -> None:
    from ee.usage.models.billing_ledger import UsageEventRecord
    from ee.usage.models.usage import UsageEventLog

    missing = UsageEventLog.objects.filter(ledger_record__isnull=True).order_by("id")[:limit]
    for log in missing:
        props = log.properties or {}
        record = UsageEventRecord(
            event=log,
            organization_id=log.organization_id,
            event_uuid=log.event_id,
            product=_product_of(log),
            source_system=str(props.get("source") or "")[:32],
            source_ref=str(props.get("source_id") or "")[:255],
            request_id=str(props.get("request_id") or "")[:255],
            received_at=log.created_at,
            occurred_at=log.timestamp,
            period=log.period or _period_of(log.timestamp),
        )
        try:
            with transaction.atomic():
                record.save(force_insert=True)
        except IntegrityError:
            pass  # another rater created it


def rate_pending(*, limit: int = 1000, now: datetime | None = None) -> RateResult:
    """Link or mark up to ``limit`` unrated events; a re-run writes nothing new."""
    from ee.usage.models.billing_ledger import UsageEventRecord

    now = now or timezone.now()
    _ensure_records(limit)
    pending = (
        UsageEventRecord.objects.select_related("event")
        .filter(rated_entry__isnull=True, not_billable_reason="")
        .order_by("received_at", "event_id")[:limit]
    )
    linked = not_billable = waiting = ledger_path = 0
    cutover_cache: dict = {}
    for record in pending:
        kind, value = _outcome(record, cutover_cache)
        unrated = UsageEventRecord.objects.filter(
            pk=record.pk, rated_entry__isnull=True, not_billable_reason=""
        )
        if kind == "link":
            if unrated.update(rated_entry=value, booked_period=record.period, rated_at=now):
                linked += 1
        elif kind == "reason":
            if unrated.update(not_billable_reason=value, rated_at=now):
                not_billable += 1
        elif kind == "wait":
            waiting += 1
        else:
            ledger_path += 1
    if ledger_path:
        logger.error("ledger_rater_ledger_path_pending", count=ledger_path)
    return RateResult(linked, not_billable, waiting, ledger_path)


def rated_through(now: datetime | None = None) -> datetime | None:
    """The I1 watermark: every event received at or before it is rated or marked."""
    from ee.usage.models.billing_ledger import UsageEventRecord
    from ee.usage.models.usage import UsageEventLog

    now = now or timezone.now()
    oldest = []
    unrecorded = (
        UsageEventLog.objects.filter(ledger_record__isnull=True)
        .order_by("created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if unrecorded is not None:
        oldest.append(unrecorded)
    unrated = (
        UsageEventRecord.objects.filter(rated_entry__isnull=True, not_billable_reason="")
        .order_by("received_at")
        .values_list("received_at", flat=True)
        .first()
    )
    if unrated is not None:
        oldest.append(unrated)
    if not oldest:
        return now
    return min(oldest) - timedelta(microseconds=1)
