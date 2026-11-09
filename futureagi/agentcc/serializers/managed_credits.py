"""Request and response shapes of the managed-credit console API, under
``/agentcc/managed-credits/`` (GW-WALLET, ported from the integration line's
lane D4).

Every amount is USD with six decimal places: exactly the ledger's integer
micros (``ManagedCreditEntry.amount_micros``), never rounded. A spend is
negative in the entries and positive in the statement's ``spend_by_model``.
The ledger's internal columns (billing-ledger-read-v1 RA9: upstream and landed
cost, fees, the upstream key, the staff note, the author) never appear here.
"""

import base64
import binascii
import json
import re
from datetime import MAXYEAR, MINYEAR, datetime
from decimal import Decimal
from uuid import UUID

from rest_framework import serializers

from agentcc.auto_reload_terms import (
    AUTO_RELOAD_CONSENT_TERMS,
    AUTO_RELOAD_CONSENT_VERSION,
    AutoReloadConsentError,
    require_current_consent_version,
)
from agentcc.management.commands.agentcc_export_ledger import FORMULA_PREFIXES
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from tfc.utils.serializer_fields import StringOrObjectField


class ManagedCreditErrorResultField(StringOrObjectField):
    """String refusal or the bounded details emitted by ManagedCreditsApiError."""

    class Meta:
        swagger_schema_fields = {
            **StringOrObjectField.Meta.swagger_schema_fields,
            "type": "object",
            "required": ["message"],
            "additionalProperties": False,
            "properties": {
                "message": {"type": "string"},
                "amount_usd": {"type": "number"},
                "spent_usd": {"type": "number"},
                "refunded_usd": {"type": "number"},
            },
        }


class ManagedCreditErrorResponseSerializer(AgentccErrorResponseSerializer):
    result = ManagedCreditErrorResultField(
        required=False, allow_null=True,
        help_text="A refusal message, or an object with its message and credit-conflict details.",
    )

MICROS_PER_USD = Decimal(1_000_000)
USD_PLACES = Decimal("0.000001")
# BL-LEDGER-2A's credit-side kinds join R's: a Stripe refund of a top-up
# (topup_reversal), a dispute's hold and its release. Each changes the credit
# total, so each is a statement credit line. usage_debit (platform rating) is
# also returned by the entries API, but is not a statement credit line.
CREDIT_KINDS = (
    "topup",
    "grant",
    "refund",
    "adjustment",
    "expiry",
    "topup_reversal",
    "dispute_hold",
    "dispute_release",
)
ENTRY_KINDS = ("spend", "usage_debit", *CREDIT_KINDS)
GATEWAY_WARNINGS = ("syncing", "unreachable", "managed_models_disabled")
# The ledger's money freeze reasons (ee.usage.services.money_freeze).
FREEZE_REASONS = ("receivable_open", "dispute_open")
# Far above any real amount, and far below the micros column's bigint range.
MAX_REQUEST_USD = Decimal(1_000_000_000)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MONTH_PATTERN = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

ENTRY_CSV_COLUMNS = (
    "occurred_at",
    "kind",
    "amount_usd",
    "request_id",
    "model",
    "provider",
    "markup_usd",
    "markup_bps",
    "cost_source",
    "stripe_ref",
)
STATEMENT_CSV_COLUMNS = (
    "line",
    "key",
    "count",
    "amount_usd",
    "markup_usd",
    # true on a spend line billed on an estimate (usage_estimated), else false;
    # empty on the balance and credit lines.
    "estimated",
)
CSV_DESCRIPTION = (
    "With format=csv the response is a spreadsheet view: a text cell that "
    "starts with =, +, -, @, a tab or a carriage return gets a leading ' so a "
    "spreadsheet cannot run it as a formula (a model id such as @cf/... reads "
    "'@cf/...), and amounts stay numbers. Use the JSONL ledger export "
    "when an unmodified record is required."
)


def usd(micros: int | None) -> Decimal | None:
    """Integer micros as exact USD."""
    if micros is None:
        return None
    return (Decimal(micros) / MICROS_PER_USD).quantize(USD_PLACES)


def micros(amount: Decimal | None) -> int | None:
    """Exact USD (at most six decimal places, as the fields validate) as micros."""
    if amount is None:
        return None
    return int(amount * MICROS_PER_USD)


def csv_cell(value):
    """One CSV cell: a number as a fixed six-place amount, text neutralised."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def encode_cursor(occurred_at: datetime, entry_id: UUID) -> str:
    raw = json.dumps([occurred_at.isoformat(), str(entry_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """The (occurred_at, id) an ``encode_cursor`` value names; ValueError if none."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        moment, entry_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        at = datetime.fromisoformat(moment)
        if at.tzinfo is None:
            raise ValueError("cursor time has no offset")
        return at, UUID(entry_id)
    except (AttributeError, binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("not a managed-credit entries cursor") from exc


class UsdField(serializers.DecimalField):
    """USD with the ledger's six decimal places, rendered as a JSON number."""

    def __init__(self, **kwargs):
        super().__init__(
            max_digits=20, decimal_places=6, coerce_to_string=False, **kwargs
        )


# --- requests -------------------------------------------------------------------


class ManagedCreditJsonQuerySerializer(serializers.Serializer):
    format = serializers.ChoiceField(
        choices=["json"], default="json", help_text="Omit or use json for JSON."
    )


class ManagedCreditFormatQuerySerializer(serializers.Serializer):
    format = serializers.ChoiceField(
        choices=["json", "csv"], default="json", help_text=CSV_DESCRIPTION
    )


class ManagedCreditEntriesQuerySerializer(ManagedCreditFormatQuerySerializer):
    kind = serializers.CharField(
        required=False,
        help_text=f"One kind or a comma-separated list of {', '.join(ENTRY_KINDS)}.",
    )
    to = serializers.DateTimeField(
        required=False, help_text="Entries before this time (exclusive)."
    )
    cursor = serializers.CharField(
        required=False, help_text="next_cursor from the previous page."
    )
    limit = serializers.IntegerField(
        required=False,
        default=DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=MAX_PAGE_SIZE,
        help_text="Page size (JSON only; the CSV holds every matching entry).",
    )

    def get_fields(self):
        fields = super().get_fields()
        # "from" is a Python keyword, so it cannot be a class attribute.
        fields["from"] = serializers.DateTimeField(
            required=False, help_text="Entries at or after this time."
        )
        return fields

    def validate_kind(self, value):
        kinds = [kind.strip() for kind in value.split(",") if kind.strip()]
        unknown = sorted(set(kinds) - set(ENTRY_KINDS))
        if not kinds or unknown:
            raise serializers.ValidationError(
                f"kind must be among {', '.join(ENTRY_KINDS)}"
            )
        return kinds

    def validate_cursor(self, value):
        try:
            return decode_cursor(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        start, end = attrs.get("from"), attrs.get("to")
        if start is not None and end is not None and start >= end:
            raise serializers.ValidationError({"to": "to must be after from"})
        return attrs


class ManagedCreditStatementQuerySerializer(ManagedCreditFormatQuerySerializer):
    month = serializers.RegexField(
        MONTH_PATTERN,
        help_text="The calendar month (UTC), YYYY-MM.",
        error_messages={"invalid": "month must be YYYY-MM"},
    )

    def validate_month(self, value):
        match = MONTH_PATTERN.match(value)
        year = int(match.group(1)) if match else 0
        # The month's end is the next month's first day, which must exist too.
        if match is None or not MINYEAR <= year < MAXYEAR:
            raise serializers.ValidationError("month must be YYYY-MM")
        return year, int(match.group(2))


class ManagedCreditCheckoutRequestSerializer(serializers.Serializer):
    amount_usd = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        coerce_to_string=False,
        min_value=Decimal("0.01"),
        # The same upper bound already enforced by max_digits/decimal_places.
        max_value=Decimal("99999999.99"),
    )
    success_url = serializers.URLField(max_length=2048)
    cancel_url = serializers.URLField(max_length=2048)


class ManagedCreditAutoReloadRequestSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    consent_version = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        trim_whitespace=False,
        max_length=64,
        help_text=(
            f"Send exactly {AUTO_RELOAD_CONSENT_VERSION} after acknowledging: "
            f"{AUTO_RELOAD_CONSENT_TERMS} Required on EVERY enabled settings save, "
            "including changes to amount or threshold; omitted when disabling. "
            "Missing consent returns 400 auto_reload_consent_required; a stale "
            "version returns 400 auto_reload_consent_outdated. No default is assumed."
        ),
    )
    threshold_usd = UsdField(
        required=False,
        allow_null=True,
        min_value=Decimal(0),
        max_value=MAX_REQUEST_USD,
    )
    amount_usd = UsdField(
        required=False,
        allow_null=True,
        min_value=Decimal(0),
        max_value=MAX_REQUEST_USD,
    )

    def validate(self, attrs):
        if attrs["enabled"]:
            try:
                require_current_consent_version(attrs.get("consent_version"))
            except AutoReloadConsentError as exc:
                raise serializers.ValidationError(
                    {"consent_version": str(exc)}, code=exc.code
                ) from exc
            missing = [
                name
                for name in ("threshold_usd", "amount_usd")
                if attrs.get(name) is None
            ]
            if missing:
                raise serializers.ValidationError(
                    dict.fromkeys(missing, "required when auto-reload is enabled")
                )
        return attrs


class ManagedCreditSettingsRequestSerializer(serializers.Serializer):
    low_balance_threshold_usd = UsdField(
        allow_null=True, min_value=Decimal(0), max_value=MAX_REQUEST_USD
    )
    auto_reload = ManagedCreditAutoReloadRequestSerializer()


class StaffOrgRequestSerializer(serializers.Serializer):
    org_id = serializers.UUIDField(
        required=False,
        help_text=(
            "The org to credit. Required with the gateway admin token; a staff "
            "user defaults to the organization the request acts in."
        ),
    )


class ManagedCreditGrantRequestSerializer(StaffOrgRequestSerializer):
    amount_usd = UsdField(min_value=Decimal("0.000001"), max_value=MAX_REQUEST_USD)
    note = serializers.CharField(max_length=2000)
    idempotency_key = serializers.CharField(max_length=128)


class ManagedCreditAdjustRequestSerializer(StaffOrgRequestSerializer):
    amount_usd = UsdField(
        min_value=-MAX_REQUEST_USD,
        max_value=MAX_REQUEST_USD,
        help_text="Positive adds credit, negative removes it.",
    )
    reason = serializers.CharField(max_length=2000)
    idempotency_key = serializers.CharField(max_length=128)

    def validate_amount_usd(self, value):
        if value == 0:
            raise serializers.ValidationError("an adjustment is not zero")
        return value


class ManagedCreditRefundRequestSerializer(StaffOrgRequestSerializer):
    request_id = serializers.CharField(
        max_length=200,
        help_text=(
            "The spend entry's own request_id, as entries/ shows it: a managed "
            "capability attempt is <request_id>.<n>."
        ),
    )
    amount_usd = UsdField(
        required=False,
        min_value=Decimal("0.000001"),
        max_value=MAX_REQUEST_USD,
        help_text="Defaults to the entry's whole spend.",
    )
    reason = serializers.CharField(max_length=2000)


# --- responses ------------------------------------------------------------------


class GatewayCreditStateSerializer(serializers.Serializer):
    """The gateway's hot-path credit account in USD."""

    spent_usd = UsdField()
    held_usd = UsdField()
    available_usd = UsdField()


class ManagedCreditAutoReloadSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    threshold_usd = UsdField(allow_null=True)
    amount_usd = UsdField(allow_null=True)
    disabled_reason = serializers.CharField(allow_blank=True)


class ManagedCreditSummarySerializer(serializers.Serializer):
    managed_models_available = serializers.BooleanField(
        help_text=(
            "Whether the gateway currently offers models on Future AGI credits. "
            "False when no provider offers credits or the gateway cannot be read."
        )
    )
    enabled = serializers.BooleanField(
        help_text="Whether this organization has opted in to Future AGI credits."
    )
    granted_usd = UsdField(help_text="The total credit recorded in the ledger.")
    settled_spend_usd = UsdField(help_text="Gateway spend settled in the ledger.")
    available_usd = UsdField(
        help_text=(
            "Ledger balance: credits minus settled gateway spend minus "
            "wallet-funded platform spend; a negative balance is debt."
        )
    )
    balance_exhausted = serializers.BooleanField(
        help_text=(
            "The ledger refuses new spend on Future AGI credits: the "
            "balance is at or below zero until credit is added, or spending "
            "is paused (freeze_reason)."
        )
    )
    freeze_reason = serializers.ChoiceField(
        choices=FREEZE_REASONS,
        allow_blank=True,
        help_text=(
            "Why spending on Future AGI credits is paused, or empty: "
            "receivable_open (the organization owes for credit it already "
            "used, after a refunded or disputed payment; credit added pays it "
            "first) or dispute_open (a payment dispute is open). While paused "
            "the spendable balance is zero."
        ),
    )
    gateway = GatewayCreditStateSerializer(
        allow_null=True,
        help_text=(
            "The gateway's hot-path account; null when unreachable, syncing, "
            "or managed_models_disabled (the shipped default)."
        ),
    )
    gateway_warning = serializers.ChoiceField(
        choices=GATEWAY_WARNINGS,
        allow_null=True,
        help_text=(
            "Why gateway is null: syncing (the gateway held no account for this "
            "opted-in org; it was just pushed), unreachable, or "
            "managed_models_disabled (the gateway has no managed providers)."
        ),
    )
    pending_settlement_usd = UsdField(
        allow_null=True,
        help_text="Spent at the gateway and not yet settled into the ledger.",
    )
    markup_percent = serializers.FloatField(allow_null=True)
    min_topup_usd = UsdField()
    low_balance_threshold_usd = UsdField(allow_null=True)
    auto_reload = ManagedCreditAutoReloadSerializer()
    last_reconciled_at = serializers.DateTimeField(allow_null=True)
    billing_available = serializers.BooleanField(
        help_text=(
            "Credits can be bought here (cloud billing is installed); without "
            "it, checkout answers 503 billing_unavailable."
        )
    )
    currency = serializers.CharField()


def entry_values(entry) -> dict:
    """A ledger entry's API fields, amounts as exact USD. Upstream and landed
    cost, fees, upstream key ids, the note and the author are internal
    (billing-ledger-read-v1 RA9) and never leave the server."""
    return {
        "id": entry.id,
        "occurred_at": entry.occurred_at,
        "kind": entry.kind,
        "amount_usd": usd(entry.amount_micros),
        "request_id": entry.request_id,
        "model": entry.model,
        "provider": entry.provider,
        "markup_usd": usd(entry.markup_micros),
        "markup_bps": entry.markup_bps,
        "cost_source": entry.cost_source,
        "stripe_ref": entry.stripe_ref,
    }


def entry_csv_row(entry) -> list:
    values = entry_values(entry)
    return [csv_cell(values[column]) for column in ENTRY_CSV_COLUMNS]


class ManagedCreditEntrySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    occurred_at = serializers.DateTimeField()
    kind = serializers.ChoiceField(choices=ENTRY_KINDS)
    amount_usd = UsdField(help_text="Credits positive, spend negative.")
    request_id = serializers.CharField(allow_blank=True)
    model = serializers.CharField(allow_blank=True)
    provider = serializers.CharField(allow_blank=True)
    markup_usd = UsdField(allow_null=True)
    markup_bps = serializers.IntegerField(
        allow_null=True, help_text="The spend's markup in basis points (5% is 500)."
    )
    cost_source = serializers.CharField(allow_blank=True)
    stripe_ref = serializers.CharField(allow_blank=True)

    def to_representation(self, instance):
        return super().to_representation(entry_values(instance))


class ManagedCreditEntriesPageSerializer(serializers.Serializer):
    entries = ManagedCreditEntrySerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True)


class CreditsByKindSerializer(serializers.Serializer):
    topup = UsdField()
    grant = UsdField()
    refund = UsdField()
    adjustment = UsdField()
    expiry = UsdField()
    topup_reversal = UsdField(help_text="Stripe refunds of top-ups (negative).")
    dispute_hold = UsdField(help_text="Top-up money held by a dispute (negative).")
    dispute_release = UsdField(help_text="Holds given back by won disputes.")


class SpendByModelSerializer(serializers.Serializer):
    model = serializers.CharField(allow_blank=True)
    count = serializers.IntegerField()
    spend_usd = UsdField(help_text="Billed to the org, positive.")
    markup_usd = UsdField()
    estimated = serializers.BooleanField(
        help_text=(
            "Billed on an estimate: the upstream reported no usage, so the "
            "gateway billed its own estimated token count."
        )
    )


class ManagedCreditStatementSerializer(serializers.Serializer):
    month = serializers.CharField()
    currency = serializers.CharField()
    opening_balance = UsdField(help_text="Sum of every entry before the month.")
    credits_by_kind = CreditsByKindSerializer()
    spend_by_model = SpendByModelSerializer(many=True)
    closing_balance = UsdField(
        help_text="opening_balance plus every entry of the month."
    )


class ManagedCreditCheckoutSerializer(serializers.Serializer):
    checkout_url = serializers.URLField()
    session_id = serializers.CharField()


class ManagedCreditCheckoutStatusSerializer(serializers.Serializer):
    session_id = serializers.CharField(
        allow_null=True,
        help_text="Ledger-confirmed session id; null while the session is pending.",
    )
    status = serializers.ChoiceField(choices=["pending", "complete"])
    credited = serializers.BooleanField(
        help_text="The ledger holds this org's top-up for the session."
    )


class ManagedCreditSettingsSerializer(serializers.Serializer):
    low_balance_threshold_usd = UsdField(allow_null=True)
    auto_reload = ManagedCreditAutoReloadSerializer()
    min_topup_usd = UsdField()


class ManagedCreditWriteSerializer(serializers.Serializer):
    entry = ManagedCreditEntrySerializer()
    created = serializers.BooleanField(
        help_text="False when the request repeats an earlier one: nothing was written."
    )
    idempotency_key = serializers.CharField(
        required=False, help_text="The key as sent (grant and adjust)."
    )
    debt_repaid_usd = UsdField(
        required=False,
        allow_null=True,
        help_text=(
            "Grant amount used to repay existing debt before crediting the remainder. "
            "Replays report the original allocation; null for legacy entries "
            "without a recorded allocation."
        ),
    )


class ManagedCreditContextErrorResponseSerializer(AgentccErrorResponseSerializer):
    """A refusal with money context, or a string refusal at the same HTTP status.

    idempotency_key_reused adds amount_usd, already_refunded adds refunded_usd,
    and refund_exceeds_spend adds spent_usd alongside message. Use the project's
    existing Swagger 2.0 string/object union, supported by both client generators.
    Only the responses which can return these objects opt into this envelope.
    """

    # The same bounded details ManagedCreditsApiError emits (message plus one
    # money field), so the published object is shaped, not an open object.
    result = ManagedCreditErrorResultField(
        required=False,
        allow_null=True,
        help_text="A refusal message, or an object with its message and credit-conflict details.",
    )


class ManagedCreditSummaryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCreditSummarySerializer()


class ManagedCreditEntriesResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCreditEntriesPageSerializer()


class ManagedCreditStatementResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCreditStatementSerializer()


class ManagedCreditCheckoutResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCreditCheckoutSerializer()


class ManagedCreditCheckoutStatusResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCreditCheckoutStatusSerializer()


class ManagedCreditSettingsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCreditSettingsSerializer()


class ManagedCreditWriteResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ManagedCreditWriteSerializer()
