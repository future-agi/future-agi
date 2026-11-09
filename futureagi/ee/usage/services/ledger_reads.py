"""billing-ledger-read-v1 payloads for balance, entries, usage and invoices.

Served from Postgres and the gateway's credits-v1 admin GET (balance only);
nothing here calls Stripe (RA6). The views (``ee/usage/views/ledger_read.py``)
own authentication and roles; these functions take the caller's org (and
workspace) and the query parameters, and raise ``LedgerQueryError`` with the
contract's error code for a bad query.

Phase-2a scope: ``amount_source`` is always ``ledger`` (managed spend and the
wallet); the ``legacy_estimate`` rows of legacy-path platform usage come with
phase 2b. Caps, alerts, admission, the rate card and statements are not built.
"""

from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.db.models import Q
from django.utils import timezone

ENTRY_PARAMS = frozenset(
    {"from", "to", "period", "kind", "product", "dimension", "funding", "agent_id",
     "api_key_id", "purpose", "cursor", "limit", "format"}
)
USAGE_PARAMS = frozenset({"period", "group_by", "product", "format"})
INVOICE_PARAMS = frozenset({"cursor", "limit", "kind"})
GROUP_KEYS = {
    "product": "product",
    "dimension": "dimension",
    "workspace": "workspace_id",
    "agent": "agent_id",
    "api_key": "api_key_id",
    "purpose": "purpose",
    "late": "late_of_period",
}
USAGE_KINDS = ("spend", "usage_debit", "refund")
WORKSPACE_ENTRY_KINDS = ("usage_debit", "spend", "refund")
MAX_USAGE_ROWS = 5000
FUNDINGS = ("promo", "commit", "purchased", "invoice", "debt", "marketplace", "free")


class LedgerQueryError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message[:500] or code}}


# --- formatting ----------------------------------------------------------------


def utc(instant: datetime | None) -> str | None:
    if instant is None:
        return None
    instant = instant.astimezone(UTC)
    text = instant.strftime("%Y-%m-%dT%H:%M:%S")
    if instant.microsecond:
        text += f".{instant.microsecond:06d}"
    return text + "Z"


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _or_none(value: Any) -> Any:
    return value if value not in ("", None) else None


def _period(text: str | None, now: datetime) -> str:
    if text is None:
        return now.astimezone(UTC).strftime("%Y-%m")
    try:
        datetime.strptime(text, "%Y-%m")
    except ValueError as exc:
        raise LedgerQueryError("invalid_period", f"period {text!r} is not YYYY-MM") from exc
    if len(text) != 7:
        raise LedgerQueryError("invalid_period", f"period {text!r} is not YYYY-MM")
    return text


def _month_bounds(period: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(period, "%Y-%m").replace(tzinfo=UTC)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def _check_params(params, allowed) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise LedgerQueryError("invalid_query", f"unknown parameter {unknown[0]!r}")


def _limit(params, default: int, maximum: int) -> int:
    raw = params.get("limit")
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise LedgerQueryError("invalid_query", "limit is an integer") from exc
    if not 1 <= value <= maximum:
        raise LedgerQueryError("invalid_query", f"limit is 1 to {maximum}")
    return value


# --- cursors -------------------------------------------------------------------


def _filter_hash(params) -> str:
    items = sorted((k, v) for k, v in params.items() if k not in ("cursor", "limit"))
    return hashlib.sha256(urlencode(items).encode()).hexdigest()[:16]


def encode_cursor(occurred_at: datetime, row_id: str, params) -> str:
    raw = json.dumps([occurred_at.isoformat(), str(row_id), _filter_hash(params)])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, params) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        when, row_id, digest = json.loads(base64.urlsafe_b64decode(padded.encode()))
        instant = datetime.fromisoformat(when)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise LedgerQueryError("invalid_cursor", "the cursor cannot be read") from exc
    if digest != _filter_hash(params):
        raise LedgerQueryError("invalid_cursor", "the cursor belongs to other filters")
    return instant, row_id


# --- balance -------------------------------------------------------------------


def _gateway_block(org) -> dict[str, Any]:
    import httpx

    from agentcc.services.gateway_admin import credits as gateway_credits
    from agentcc.services.gateway_client import GatewayClientError
    from ee.usage.services.gateway_push import is_managed_off

    nulls = {
        "granted_total_micros": None,
        "granted_version": None,
        "spent_micros": None,
        "held_micros": None,
        "available_micros": None,
        "markup_percent": None,
    }
    try:
        state = gateway_credits.get_credits(str(org.pk))
    except (GatewayClientError, httpx.HTTPError, ValueError) as exc:
        return {"state": "na_managed_off" if is_managed_off(exc) else "unreachable", **nulls}
    block = {"state": "ok"}
    for name in ("granted_total_micros", "granted_version", "spent_micros", "held_micros",
                 "available_micros"):
        value = state.get(name) if isinstance(state, dict) else None
        if not isinstance(value, int) or isinstance(value, bool):
            return {"state": "unreachable", **nulls}
        block[name] = value
    markup = state.get("markup_percent")
    block["markup_percent"] = markup if isinstance(markup, (int, float)) and not isinstance(markup, bool) else None
    return block


def balance_payload(org, *, now: datetime | None = None) -> dict[str, Any]:
    from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
    from ee.usage.services import funding
    from ee.usage.services.managed_credits import billing_profile_for_account, ledger_balance

    now = now or timezone.now()
    account = ManagedCreditAccount.objects.filter(organization=org).first()
    profile = billing_profile_for_account(account, org_id=org.pk)
    bal = ledger_balance(org)
    grants = list(
        ManagedCreditEntry.objects.filter(
            organization=org,
            kind__in=["grant", "adjustment"],
            amount_micros__gt=0,
        )
        .filter(Q(funding="promo") | Q(funding=""))
        .values_list("amount_micros", "expires_at", "occurred_at")
    )
    far = datetime.max.replace(tzinfo=UTC)
    grants.sort(key=lambda g: (g[1] or far, g[2]))
    consumed = (account.promo_total_micros - bal.promo_micros) if account else 0
    left = funding.promotional_remaining([(a, e) for a, e, _ in grants], max(consumed, 0))
    expiring = [
        {"amount_micros": amount, "expires_at": utc(expires)}
        for amount, expires in left
        if expires is not None
    ][:10]
    enabled = bool(account and account.autoreload_enabled)
    return {
        "org_id": str(org.pk),
        "as_of": utc(now),
        "currency": "usd",
        "billing_channel": profile["billing_channel"],
        "funding": profile["funding"],
        "billing_path": profile["billing_path"],
        "cutover_at": utc(profile["cutover_at"]),
        "balance_micros": bal.balance_micros,
        "debt_micros": bal.debt_micros,
        "remaining": {
            "promotional_micros": bal.promo_micros,
            "commit_micros": bal.commit_micros,
            "purchased_micros": bal.purchased_micros,
        },
        "promotional_expiring": expiring,
        "refusing": profile["funding"] == "prepaid_only" and bal.balance_micros <= 0,
        "gateway": _gateway_block(org),
        "low_balance_threshold_micros": account.low_balance_threshold_micros if account else None,
        "auto_topup": {
            "enabled": enabled,
            "threshold_micros": account.autoreload_threshold_micros if account else None,
            "amount_micros": account.autoreload_amount_micros if account else None,
            "daily_count_cap": account.autoreload_daily_count_cap if account else None,
            "daily_amount_cap_micros": account.autoreload_daily_amount_cap_micros if account else None,
            "disabled_reason": _or_none(account.autoreload_disabled_reason) if account else None,
        },
    }


# --- entries -------------------------------------------------------------------


def entry_json(e) -> dict[str, Any]:
    """One entry in the closed ``$defs/Entry`` shape; internal columns never appear."""
    spend = e.kind == "spend"
    return {
        "id": str(e.id),
        "kind": e.kind,
        "amount_micros": e.amount_micros,
        "occurred_at": utc(e.occurred_at),
        "created_at": utc(e.created_at),
        "product": e.product or ("gateway" if spend else None),
        "dimension": e.dimension or ("managed_spend" if spend else None),
        "quantity": _decimal_text(e.quantity)
        if e.quantity is not None
        else (str(-e.amount_micros) if spend else None),
        "unit": e.unit or ("usd_micros" if spend else None),
        "funding": _or_none(e.funding),
        "period": _or_none(e.period) or (e.occurred_at.astimezone(UTC).strftime("%Y-%m") if spend else None),
        "late_of_period": _or_none(e.late_of_period),
        "workspace_id": str(e.workspace_id) if e.workspace_id else None,
        "agent_id": _or_none(e.agent_id),
        "agent_run_id": _or_none(e.agent_run_id),
        "api_key_id": _or_none(e.api_key_id),
        "purpose": _or_none(e.purpose),
        "source_kind": _or_none(e.source_kind),
        "source_ref": _or_none(e.source_ref),
        "request_id": _or_none(e.request_id),
        "model": _or_none(e.model),
        "markup_bps": e.markup_bps,
        "price_catalog_version": _or_none(e.price_catalog_version),
        "gateway_catalog_snapshot_id": _or_none(e.catalog_snapshot_id),
        "stripe_ref": _or_none(e.stripe_ref),
        "expires_at": utc(e.expires_at),
        "category": _or_none(e.category),
    }


def _entries_queryset(org, params, workspace_id):
    from ee.usage.models.managed_credits import ManagedCreditEntry

    qs = ManagedCreditEntry.objects.filter(organization=org)
    if workspace_id is not None:
        qs = qs.filter(workspace_id=workspace_id, kind__in=WORKSPACE_ENTRY_KINDS)
    for name, column in (("from", "occurred_at__gte"), ("to", "occurred_at__lt")):
        if name in params:
            try:
                instant = datetime.fromisoformat(params[name].replace("Z", "+00:00"))
            except ValueError as exc:
                raise LedgerQueryError("invalid_query", f"{name} is an instant") from exc
            if instant.tzinfo is None:
                raise LedgerQueryError("invalid_query", f"{name} needs a UTC offset")
            qs = qs.filter(**{column: instant})
    if "period" in params:
        period = _period(params["period"], timezone.now())
        start, end = _month_bounds(period)
        qs = qs.filter(Q(period=period) | Q(period="", occurred_at__gte=start, occurred_at__lt=end))
    if "kind" in params:
        kinds = [k for k in params["kind"].split(",") if k]
        allowed = {k.value for k in ManagedCreditEntry.Kind}
        if not kinds or set(kinds) - allowed:
            raise LedgerQueryError("invalid_query", "kind takes known entry kinds")
        qs = qs.filter(kind__in=kinds)
    if "product" in params:
        product = params["product"]
        legacy_spend = Q(kind="spend", product="") if product == "gateway" else Q(pk__in=[])
        qs = qs.filter(Q(product=product) | legacy_spend)
    for name in ("dimension", "funding", "agent_id", "api_key_id", "purpose"):
        if name in params:
            qs = qs.filter(**{name: params[name]})
    return qs.order_by("-occurred_at", "-id")


def entries_page(org, params, *, workspace_id=None) -> dict[str, Any] | str:
    """``$defs/EntriesPage``; with ``format=csv`` the CSV text of every match."""
    _check_params(params, ENTRY_PARAMS)
    fmt = params.get("format")
    if fmt not in (None, "csv"):
        raise LedgerQueryError("invalid_query", "format is csv")
    qs = _entries_queryset(org, params, workspace_id)
    if fmt == "csv":
        return _csv([entry_json(e) for e in qs.iterator()], _ENTRY_COLUMNS)
    limit = _limit(params, 50, 200)
    if params.get("cursor"):
        when, row_id = decode_cursor(params["cursor"], params)
        qs = qs.filter(Q(occurred_at__lt=when) | Q(occurred_at=when, id__lt=row_id))
    rows = list(qs[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    return {
        "order": "occurred_at_desc",
        "entries": [entry_json(e) for e in rows],
        "next_cursor": encode_cursor(rows[-1].occurred_at, rows[-1].id, params) if more else None,
    }


_ENTRY_COLUMNS = (
    "id", "kind", "amount_micros", "occurred_at", "created_at", "product", "dimension",
    "quantity", "unit", "funding", "period", "late_of_period", "workspace_id", "agent_id",
    "agent_run_id", "api_key_id", "purpose", "source_kind", "source_ref", "request_id",
    "model", "markup_bps", "price_catalog_version", "gateway_catalog_snapshot_id",
    "stripe_ref", "expires_at", "category",
)


def _csv(rows: list[dict], columns) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _csv_cell(row.get(k)) for k in columns})
    return out.getvalue()


# A spreadsheet runs a cell that starts with one of these as a formula.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?")


def _csv_cell(value):
    """A CSV cell a spreadsheet shows as text, never runs as a formula.

    Caller-controlled text (agent ids, key ids, request ids, models) that starts
    with a formula character gets a leading ``'``. Numbers are left alone, so a
    debit's ``-1000`` stays a number.
    """
    if value is None:
        return ""
    if (
        isinstance(value, str)
        and value.startswith(_FORMULA_LEADS)
        and not _PLAIN_NUMBER.fullmatch(value)
    ):
        return "'" + value
    return value


# --- usage ---------------------------------------------------------------------


def _funded(e) -> dict[str, int]:
    """Minus the entry's per-bucket change: what the usage was paid with."""
    split = dict.fromkeys(FUNDINGS, 0)
    if e.funded_promo_micros is not None:
        split["promo"] = -e.funded_promo_micros
        split["commit"] = -e.funded_commit_micros
        split["purchased"] = -e.funded_purchased_micros
        split["debt"] = -e.funded_debt_micros
    elif e.funding in ("invoice", "marketplace"):
        split[e.funding] = -e.amount_micros
    elif e.funding == "free":
        pass  # a free-band row has amount 0
    else:
        split["purchased"] = -e.amount_micros  # entries from before usage/0031
    return split


def usage_payload(org, params, *, workspace_id=None, now: datetime | None = None):
    from ee.usage.models.managed_credits import ManagedCreditEntry
    from ee.usage.services import rating

    _check_params(params, USAGE_PARAMS)
    now = now or timezone.now()
    fmt = params.get("format")
    if fmt not in (None, "csv"):
        raise LedgerQueryError("invalid_query", "format is csv")
    period = _period(params.get("period"), now)
    groups = ["product"]
    for key in [g for g in params.get("group_by", "").split(",") if g]:
        if key not in GROUP_KEYS:
            raise LedgerQueryError("invalid_query", f"group_by takes {sorted(GROUP_KEYS)}")
        if key not in groups:
            groups.append(key)
    start, end = _month_bounds(period)
    qs = ManagedCreditEntry.objects.filter(organization=org, kind__in=USAGE_KINDS).filter(
        Q(period=period) | Q(period="", occurred_at__gte=start, occurred_at__lt=end)
    )
    if workspace_id is not None:
        qs = qs.filter(workspace_id=workspace_id)
    rows: dict[tuple, dict] = {}
    totals = dict.fromkeys(FUNDINGS, 0)
    total_amount = 0
    for e in qs.iterator():
        record = entry_json(e)
        product = record["product"]
        if product is None:
            continue
        if "product" in params and product != params["product"]:
            continue
        values = {
            "product": product,
            "dimension": record["dimension"],
            "workspace_id": record["workspace_id"],
            "agent_id": record["agent_id"],
            "api_key_id": record["api_key_id"],
            "purpose": record["purpose"],
            "late_of_period": record["late_of_period"],
        }
        key = tuple(values[GROUP_KEYS[g]] for g in groups)
        row = rows.get(key)
        if row is None:
            row = {
                "product": product,
                "dimension": None,
                "unit": None,
                "workspace_id": None,
                "agent_id": None,
                "api_key_id": None,
                "purpose": None,
                "late_of_period": None,
                "quantity": Decimal(0) if "dimension" in groups else None,
                "amount_micros": 0,
                "funded": dict.fromkeys(FUNDINGS, 0),
                "amount_source": "ledger",
            }
            for g in groups:
                row[GROUP_KEYS[g]] = values[GROUP_KEYS[g]]
            if "dimension" in groups:
                row["unit"] = record["unit"]
            rows[key] = row
            if len(rows) > MAX_USAGE_ROWS:
                raise LedgerQueryError("invalid_query", "more than 5000 rows: narrow group_by")
        amount = -e.amount_micros
        row["amount_micros"] += amount
        total_amount += amount
        if row["quantity"] is not None and record["quantity"] is not None:
            sign = -1 if e.kind == "refund" else 1
            row["quantity"] += sign * Decimal(record["quantity"])
        for bucket, value in _funded(e).items():
            row["funded"][bucket] += value
            totals[bucket] += value
    out_rows = []
    for row in rows.values():
        quantity = row["quantity"]
        row["quantity"] = _decimal_text(quantity) if quantity is not None and quantity >= 0 else None
        out_rows.append(row)
    out_rows.sort(key=lambda r: tuple(str(r[GROUP_KEYS[g]] or "") for g in groups))
    if fmt == "csv":
        flat = [
            {**{k: v for k, v in r.items() if k != "funded"},
             **{f"funded_{b}": r["funded"][b] for b in FUNDINGS}}
            for r in out_rows
        ]
        columns = [k for k in out_rows[0] if k != "funded"] if out_rows else list(GROUP_KEYS.values())
        return _csv(flat, [*columns, *(f"funded_{b}" for b in FUNDINGS)])
    return {
        "org_id": str(org.pk),
        "workspace_id": str(workspace_id) if workspace_id else None,
        "period": period,
        "group_by": groups,
        "as_of": utc(now),
        "rated_through": utc(rating.rated_through(now)),
        "estimate": now < end + timedelta(hours=6),
        "rows": out_rows,
        "totals": {"amount_micros": total_amount, "funded": totals},
    }


# --- invoices ------------------------------------------------------------------


def invoices_page(org, params, *, mode: str) -> dict[str, Any]:
    from ee.usage.models.billing_ledger import BillingInvoiceMirror

    _check_params(params, INVOICE_PARAMS)
    limit = _limit(params, 25, 100)
    qs = BillingInvoiceMirror.objects.filter(organization=org, mode=mode)
    if "kind" in params:
        kinds = [k for k in params["kind"].split(",") if k]
        if not kinds or set(kinds) - {"usage", "plan", "topup", "one_off", "legacy"}:
            raise LedgerQueryError("invalid_query", "kind takes invoice kinds")
        qs = qs.filter(kind__in=kinds)
    qs = qs.order_by("-stripe_created_at", "-stripe_invoice_id")
    if params.get("cursor"):
        when, invoice_id = decode_cursor(params["cursor"], params)
        qs = qs.filter(
            Q(stripe_created_at__lt=when) | Q(stripe_created_at=when, stripe_invoice_id__lt=invoice_id)
        )
    rows = list(qs[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]

    def https(url):
        return url if url and url.startswith("https://") else None

    invoices = [
        {
            "stripe_invoice_id": r.stripe_invoice_id,
            "mode": r.mode,
            "kind": r.kind,
            "status": r.status,
            "period_start": r.period_start.isoformat() if r.period_start else None,
            "period_end": r.period_end.isoformat() if r.period_end else None,
            "currency": "usd",
            "subtotal_cents": r.subtotal_cents,
            "tax_cents": r.tax_cents,
            "total_cents": r.total_cents,
            "amount_due_cents": r.amount_due_cents,
            "hosted_invoice_url": https(r.hosted_invoice_url),
            "invoice_pdf_url": https(r.invoice_pdf_url),
            # Phase 2a serves no statements/<YYYY-MM>/ route, so no row links
            # one (a link that answers 404 is a dead end). The contract's
            # "set for usage and legacy" applies once the route lands (2b).
            "statement_url": None,
            "created_at": utc(r.stripe_created_at),
        }
        for r in rows
    ]
    return {
        "invoices": invoices,
        "next_cursor": (
            encode_cursor(rows[-1].stripe_created_at, rows[-1].stripe_invoice_id, params)
            if more
            else None
        ),
    }
