"""Managed-credit ledger models: the append-only money system of record."""

import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from ee.usage.models.managed_credits import (
    ImmutableLedgerEntryError,
    ManagedCreditAccount,
    ManagedCreditEntry,
)

Kind = ManagedCreditEntry.Kind


def _entry(organization, kind=Kind.GRANT, amount_micros=1_000_000, **fields):
    return ManagedCreditEntry.objects.create(
        organization=organization,
        kind=kind,
        amount_micros=amount_micros,
        idempotency_key=fields.pop("idempotency_key", f"test:{uuid.uuid4()}"),
        **fields,
    )


def _indexes(table):
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table)
    return [c for c in constraints.values() if c["index"]]


def _has_index(table, columns, orders=None):
    return any(
        c["columns"] == columns and (orders is None or c["orders"] == orders)
        for c in _indexes(table)
    )


@pytest.mark.integration
class TestManagedCreditEntryConstraints:
    def test_duplicate_idempotency_key_raises(self, organization):
        _entry(organization, idempotency_key="grant:once")

        with pytest.raises(IntegrityError), transaction.atomic():
            _entry(organization, idempotency_key="grant:once", amount_micros=5)

    def test_positive_spend_rejected_by_check(self, organization):
        with pytest.raises(IntegrityError), transaction.atomic():
            _entry(organization, kind=Kind.SPEND, amount_micros=1)

    @pytest.mark.parametrize("kind", [Kind.TOPUP, Kind.GRANT, Kind.REFUND])
    def test_negative_credit_rejected_by_check(self, organization, kind):
        with pytest.raises(IntegrityError), transaction.atomic():
            _entry(organization, kind=kind, amount_micros=-1)

    def test_unknown_kind_rejected_by_check(self, organization):
        with pytest.raises(IntegrityError), transaction.atomic():
            _entry(organization, kind="bonus", amount_micros=1)

    @pytest.mark.parametrize(
        "kind,amount",
        [
            (Kind.SPEND, -3_150_000),
            (Kind.SPEND, 0),
            (Kind.TOPUP, 25_000_000),
            (Kind.GRANT, 0),
            (Kind.REFUND, 3_150_000),
            (Kind.ADJUSTMENT, -7),
            (Kind.ADJUSTMENT, 7),
            (Kind.EXPIRY, -1_000_000),
        ],
    )
    def test_allowed_signs(self, organization, kind, amount):
        entry = _entry(organization, kind=kind, amount_micros=amount)

        entry.refresh_from_db()
        assert entry.amount_micros == amount

    def test_spend_fields_round_trip(self, organization):
        entry = _entry(
            organization,
            kind=Kind.SPEND,
            amount_micros=-3_150_000_000,
            idempotency_key="spend:req-1",
            request_id="req-1",
            attempt_count=2,
            model="openai/gpt-4o-mini",
            provider="openrouter",
            endpoint_id="openai|gpt-4o-mini",
            served_upstream_provider="azure",
            upstream_cost_micros=3_000_000_000,
            landed_cost_micros=3_266_800_000,
            upstream_fee_bps=550,
            processing_fee_bps=320,
            upstream_key_id="kh_123",
            markup_micros=150_000_000,
            markup_bps=500,
            cost_source="provider_reported",
            usage_estimated=True,
            catalog_snapshot_id="snap-1",
            policy_version="v3",
            api_key_id="key-1",
            agent_run_id="run-1",
            attempts=[{"attempt_id": "req-1.1", "billed_micros": 3_150_000_000}],
            occurred_at=timezone.now(),
        )

        entry.refresh_from_db()
        assert entry.landed_cost_micros == 3_266_800_000
        assert entry.attempts[0]["attempt_id"] == "req-1.1"
        assert entry.payment_fee_micros == 0
        assert entry.payment_fee_estimated is False
        assert entry.usage_event_emitted_at is None
        assert entry.created_at is not None


@pytest.mark.integration
class TestManagedCreditEntryIsAppendOnly:
    def test_saving_an_existing_entry_raises(self, organization):
        entry = _entry(organization)
        entry.amount_micros = 999

        with pytest.raises(ImmutableLedgerEntryError):
            entry.save()

        entry.refresh_from_db()
        assert entry.amount_micros == 1_000_000

    def test_saving_a_reloaded_entry_raises(self, organization):
        entry = ManagedCreditEntry.objects.get(pk=_entry(organization).pk)

        with pytest.raises(ImmutableLedgerEntryError):
            entry.save()

    def test_deleting_an_entry_raises(self, organization):
        entry = _entry(organization)

        with pytest.raises(ImmutableLedgerEntryError):
            entry.delete()

        assert ManagedCreditEntry.objects.filter(pk=entry.pk).exists()

    def test_queryset_delete_raises(self, organization):
        _entry(organization)

        with pytest.raises(ImmutableLedgerEntryError):
            ManagedCreditEntry.objects.filter(organization=organization).delete()

        assert ManagedCreditEntry.objects.filter(organization=organization).count() == 1

    def test_queryset_update_of_money_fields_raises(self, organization):
        _entry(organization)

        with pytest.raises(ImmutableLedgerEntryError):
            ManagedCreditEntry.objects.filter(organization=organization).update(
                amount_micros=5
            )
        with pytest.raises(ImmutableLedgerEntryError):
            ManagedCreditEntry.objects.filter(organization=organization).update(
                usage_event_emitted_at=timezone.now(), note="rewrite"
            )

    def test_usage_event_emitted_at_is_the_one_mutable_field(self, organization):
        entry = _entry(organization, kind=Kind.SPEND, amount_micros=-5)
        emitted = timezone.now()

        updated = ManagedCreditEntry.objects.filter(pk=entry.pk).update(
            usage_event_emitted_at=emitted
        )

        entry.refresh_from_db()
        assert updated == 1
        assert entry.usage_event_emitted_at == emitted
        assert entry.amount_micros == -5

    def test_bulk_create_ignore_conflicts_inserts_once(self, organization):
        rows = [
            ManagedCreditEntry(
                organization=organization,
                kind=Kind.SPEND,
                amount_micros=-10,
                idempotency_key="spend:req-9",
            )
            for _ in range(2)
        ]

        ManagedCreditEntry.objects.bulk_create(rows[:1], ignore_conflicts=True)
        ManagedCreditEntry.objects.bulk_create(rows[1:], ignore_conflicts=True)

        assert (
            ManagedCreditEntry.objects.filter(idempotency_key="spend:req-9").count()
            == 1
        )

    def test_deleting_the_creator_keeps_the_entry(self, organization, user):
        entry = _entry(organization, created_by=user)

        type(user).objects.filter(pk=user.pk).delete()

        entry.refresh_from_db()
        assert entry.created_by_id is None
        assert entry.amount_micros == 1_000_000

    def test_org_with_ledger_entries_cannot_be_deleted(self, organization):
        _entry(organization)

        with pytest.raises(ProtectedError), transaction.atomic():
            organization.delete()


@pytest.mark.integration
class TestManagedCreditEntryIndexes:
    table = "usage_managedcreditentry"

    def test_agent_run_index_exists(self, db):
        assert _has_index(self.table, ["organization_id", "agent_run_id"])

    def test_listing_indexes_exist(self, db):
        assert _has_index(
            self.table, ["organization_id", "occurred_at"], ["ASC", "DESC"]
        )
        assert _has_index(
            self.table,
            ["organization_id", "kind", "occurred_at"],
            ["ASC", "ASC", "DESC"],
        )
        assert _has_index(self.table, ["request_id"])


@pytest.mark.integration
class TestManagedCreditAccount:
    def test_new_account_starts_at_zero(self, organization):
        account = ManagedCreditAccount.objects.create(organization=organization)

        account.refresh_from_db()
        assert account.pk == organization.pk
        assert account.granted_total_micros == 0
        assert account.settled_spend_micros == 0
        assert account.version == 0
        assert account.gateway_acked_version == 0
        assert account.autoreload_enabled is False
        assert account.low_balance_threshold_micros is None
        assert account.last_reconciled_at is None

    def test_one_account_per_org(self, organization):
        ManagedCreditAccount.objects.create(organization=organization)

        with pytest.raises(IntegrityError), transaction.atomic():
            ManagedCreditAccount.objects.create(organization=organization)

    def test_totals_hold_more_than_32_bits_of_micros(self, organization):
        account = ManagedCreditAccount.objects.create(organization=organization)
        account.granted_total_micros = 1_000_000 * 1_000_000  # $1M
        account.settled_spend_micros = 999_999 * 1_000_000
        account.version = 2**40
        account.save()

        account.refresh_from_db()
        assert account.granted_total_micros == 10**12
        assert account.version == 2**40

    def test_account_is_mutable(self, organization):
        account = ManagedCreditAccount.objects.create(organization=organization)
        before = account.updated_at
        account.gateway_acked_version = 3
        account.autoreload_enabled = True
        account.autoreload_payment_method_id = "pm_test"
        account.save()

        account.refresh_from_db()
        assert account.gateway_acked_version == 3
        assert account.autoreload_enabled is True
        assert account.updated_at >= before
