"""Managed credit ledger service: idempotent entries, account totals,
the versioned granted push and the zero-account bootstrap."""

import copy
import json
import threading
import uuid
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from jsonschema import Draft202012Validator
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from agentcc.services.gateway_client import GatewayClientError
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import managed_credits as mc

Kind = ManagedCreditEntry.Kind
EXAMPLES_DIR = (
    Path(__file__).resolve().parents[4] / "api_contracts" / "gateway" / "examples"
)
GATEWAY_ADMIN = "agentcc.services.gateway_admin.credits.GatewayAdminClient"
CREDIT_KINDS = [Kind.TOPUP, Kind.GRANT, Kind.REFUND, Kind.ADJUSTMENT, Kind.EXPIRY]


def _golden(name):
    return json.loads((EXAMPLES_DIR / name).read_text())


def _record(org, request_id="req_ledger_1", **overrides):
    """The managed-settlement-v1 golden, as the outbox carries it, for ``org``."""
    record = _golden("managed-settlement-v1.json")
    record.update(org_id=str(org.pk), request_id=request_id, generation_id=request_id)
    record.update({k: str(v) for k, v in overrides.items()})
    return record


def _account(org):
    return ManagedCreditAccount.objects.get(organization=org)


def _echo_gateway(method, path, json=None, params=None):
    """A gateway that applies every push, answering credits-granted-push-v1."""
    spent = json["settled_spend_floor_micros"]
    return {
        "applied": True,
        "granted_total_micros": json["granted_total_micros"],
        "granted_version": json["version"],
        "spent_micros": spent,
        "held_micros": 0,
        "available_micros": json["granted_total_micros"] - spent,
    }


@pytest.fixture
def gateway():
    with patch(GATEWAY_ADMIN) as client_class:
        client_class.return_value.request.side_effect = _echo_gateway
        yield client_class.return_value


def _pushes(gateway):
    return [c for c in gateway.request.call_args_list if c.args[0] == "PUT"]


@pytest.mark.integration
class TestIdempotentWrites:
    def test_grant_idempotent(self, organization, user):
        first, created = mc.grant(
            organization, 5_000_000, idempotency_key="g-1", actor=user, note="pilot"
        )
        again, created_again = mc.grant(
            organization, 5_000_000, idempotency_key="g-1", actor=user, note="pilot"
        )

        assert (created, created_again) == (True, False)
        assert again.pk == first.pk
        assert ManagedCreditEntry.objects.filter(organization=organization).count() == 1
        assert first.kind == Kind.GRANT and first.amount_micros == 5_000_000
        assert first.created_by == user and first.note == "pilot"
        account = _account(organization)
        assert (account.granted_total_micros, account.version) == (5_000_000, 1)

    def test_caller_keys_are_scoped_per_org(self, organization):
        other = Organization.objects.create(name="Other org")

        mc.grant(organization, 1_000_000, idempotency_key="k", actor=None, note="")
        _, created = mc.grant(
            other, 2_000_000, idempotency_key="k", actor=None, note=""
        )

        # The same caller key in another org is a new grant, not org A's entry.
        assert created is True
        assert _account(other).granted_total_micros == 2_000_000
        assert _account(organization).granted_total_micros == 1_000_000

    def test_settle_spend_twice_one_row(self, organization):
        first, created = mc.settle_spend(_record(organization))
        again, created_again = mc.settle_spend(_record(organization))

        assert (created, created_again) == (True, False)
        assert again.pk == first.pk
        assert ManagedCreditEntry.objects.filter(kind=Kind.SPEND).count() == 1
        assert first.idempotency_key == "spend:req_ledger_1"
        assert _account(organization).settled_spend_micros == 11_815

    def test_system_key_held_by_another_org_is_a_conflict(self, organization):
        other = Organization.objects.create(name="Other org")
        mc.settle_spend(_record(organization))

        with pytest.raises(mc.IdempotencyConflict):
            mc.settle_spend(_record(other))

        assert not ManagedCreditAccount.objects.filter(
            organization=other, settled_spend_micros__gt=0
        ).exists()

    def test_topups_keyed_by_stripe_ids(self, organization):
        entry, created = mc.apply_topup(
            organization,
            25_000_000,
            stripe_session_id="cs_test_1",
            payment_fee_micros=1_025_000,
            payment_fee_estimated=True,
        )
        _, created_again = mc.apply_topup(
            organization, 25_000_000, stripe_session_id="cs_test_1"
        )
        pi, _ = mc.apply_topup_pi(organization, 10_000_000, payment_intent_id="pi_1")

        assert (created, created_again) == (True, False)
        assert entry.idempotency_key == "stripe:cs_test_1"
        assert entry.stripe_ref == "cs_test_1" and entry.kind == Kind.TOPUP
        assert (entry.payment_fee_micros, entry.payment_fee_estimated) == (
            1_025_000,
            True,
        )
        assert pi.idempotency_key == "stripe_pi:pi_1" and pi.stripe_ref == "pi_1"
        assert _account(organization).granted_total_micros == 35_000_000

    @pytest.mark.parametrize(
        "write",
        [
            lambda org: mc.grant(org, 0, idempotency_key="z", actor=None, note=""),
            lambda org: mc.grant(org, 1.5, idempotency_key="f", actor=None, note=""),
            lambda org: mc.adjust(org, 0, idempotency_key="z", reason="", actor=None),
            lambda org: mc.expire(org, -1, idempotency_key="z"),
            lambda org: mc.apply_topup(org, 5, stripe_session_id=""),
            lambda org: mc.apply_topup(
                org, 5, stripe_session_id="cs", payment_fee_micros=-1
            ),
            lambda org: mc.grant(
                org, 1, idempotency_key="x" * 200, actor=None, note=""
            ),
        ],
        ids=[
            "zero-grant",
            "float",
            "zero-adjust",
            "negative-expiry",
            "no-id",
            "negative-fee",
            "long",
        ],
    )
    def test_invalid_amounts_and_keys_write_nothing(self, organization, write):
        with pytest.raises(mc.ManagedCreditError):
            write(organization)

        assert not ManagedCreditEntry.objects.filter(organization=organization).exists()


@pytest.mark.integration
class TestAccountTotals:
    def test_totals_equal_sum_of_entries(self, organization):
        mc.grant(organization, 10_000_000, idempotency_key="g", actor=None, note="")
        mc.apply_topup(organization, 25_000_000, stripe_session_id="cs_1")
        mc.apply_topup_pi(organization, 5_000_000, payment_intent_id="pi_1")
        mc.settle_spend(_record(organization, "req_a"))
        mc.settle_spend(_record(organization, "req_b", billed_micros=40_000))
        mc.refund(organization, "req_a", reason="bad answer", actor=None)
        mc.adjust(organization, -2_000_000, idempotency_key="a", reason="", actor=None)
        mc.adjust(organization, 700_000, idempotency_key="b", reason="", actor=None)
        mc.expire(organization, 3_000_000, idempotency_key="e")

        entries = ManagedCreditEntry.objects.filter(organization=organization)
        credits = sum(e.amount_micros for e in entries if e.kind in CREDIT_KINDS)
        spends = sum(e.amount_micros for e in entries if e.kind == Kind.SPEND)
        account = _account(organization)
        assert account.granted_total_micros == credits == 35_711_815
        assert account.settled_spend_micros == -spends == 51_815
        assert mc.balance(organization) == {
            "granted_total_micros": credits,
            "settled_spend_micros": -spends,
            "ledger_available_micros": credits + spends,
        }

    def test_balance_of_an_org_without_an_account_is_zero(self, organization):
        assert mc.balance(organization) == {
            "granted_total_micros": 0,
            "settled_spend_micros": 0,
            "ledger_available_micros": 0,
        }
        assert not ManagedCreditAccount.objects.filter(
            organization=organization
        ).exists()

    def test_version_bumps_only_credit_side(self, organization):
        def version():
            return _account(organization).version

        mc.grant(organization, 1_000_000, idempotency_key="g", actor=None, note="")
        assert version() == 1
        mc.settle_spend(_record(organization, "req_1"))
        mc.settle_spend(_record(organization, "req_2"))
        assert version() == 1
        mc.apply_topup(organization, 25_000_000, stripe_session_id="cs_1")
        mc.apply_topup_pi(organization, 1_000_000, payment_intent_id="pi_1")
        mc.refund(organization, "req_1", 100, reason="", actor=None)
        mc.adjust(organization, -5, idempotency_key="a", reason="", actor=None)
        mc.expire(organization, 5, idempotency_key="e")
        assert version() == 6
        # A duplicate changes nothing.
        mc.grant(organization, 1_000_000, idempotency_key="g", actor=None, note="")
        mc.apply_topup(organization, 25_000_000, stripe_session_id="cs_1")
        assert version() == 6

    def test_refund_cannot_exceed_spend(self, organization):
        other = Organization.objects.create(name="Other org")
        mc.settle_spend(_record(organization, "req_r"))  # billed 11815

        with pytest.raises(mc.RefundExceedsSpend):
            mc.refund(organization, "req_r", 11_816, reason="", actor=None)
        with pytest.raises(mc.RefundExceedsSpend):
            mc.refund(organization, "req_unknown", 1, reason="", actor=None)
        with pytest.raises(mc.RefundExceedsSpend):
            mc.refund(other, "req_r", 1, reason="", actor=None)
        assert not ManagedCreditEntry.objects.filter(kind=Kind.REFUND).exists()

        entry, created = mc.refund(organization, "req_r", reason="dup", actor=None)
        again, created_again = mc.refund(
            organization, "req_r", 5, reason="", actor=None
        )

        assert (created, created_again) == (True, False)
        assert again.pk == entry.pk
        assert entry.idempotency_key == "refund:req_r"
        assert (entry.amount_micros, entry.request_id, entry.note) == (
            11_815,
            "req_r",
            "dup",
        )
        assert _account(organization).granted_total_micros == 11_815


@pytest.mark.integration
class TestSettlementRecord:
    def test_settle_spend_copies_landed_fields(self, organization):
        record = _record(organization, "req_01JD8Z3K4M5N6P7Q8R9S")

        entry, _ = mc.settle_spend(record)

        entry.refresh_from_db()
        attempts = json.loads(record["attempts"])
        assert entry.kind == Kind.SPEND and entry.amount_micros == -11_815
        assert entry.idempotency_key == "spend:req_01JD8Z3K4M5N6P7Q8R9S"
        assert entry.request_id == "req_01JD8Z3K4M5N6P7Q8R9S"
        # v2 landed-cost fields.
        assert entry.landed_cost_micros == 12_249
        assert entry.upstream_fee_bps == 550
        assert entry.processing_fee_bps == 320
        assert entry.served_upstream_provider == "anthropic"
        assert entry.upstream_key_id == "kh_3b9e"
        # The rest of managed-settlement-v1.
        assert entry.upstream_cost_micros == 11_252
        assert (entry.markup_micros, entry.markup_bps) == (563, 500)
        assert entry.model == "anthropic/claude-sonnet-5.5"
        assert entry.provider == "openrouter"
        assert entry.endpoint_id == "openrouter:anthropic:anthropic/claude-sonnet-5.5"
        assert entry.cost_source == "provider_reported"
        assert entry.usage_estimated is False
        assert entry.catalog_snapshot_id == "3f9a1c0b2d4e"
        assert entry.policy_version == "v12"
        assert entry.api_key_id == "key_7f3a"
        assert entry.agent_run_id == "run_R2"
        assert entry.attempts == attempts and entry.attempt_count == 2
        assert entry.occurred_at == datetime.fromtimestamp(1792056612.419, tz=UTC)

    def test_integer_valued_record_is_accepted(self, organization):
        record = _record(organization, "req_int", usage_estimated=1)
        for field in ("billed_micros", "landed_micros", "markup_bps", "ts_ms"):
            record[field] = int(record[field])
        record["attempts"] = json.loads(record["attempts"])

        entry, created = mc.settle_spend(record)

        assert created and entry.amount_micros == -11_815
        assert entry.usage_estimated is True and entry.attempt_count == 2

    @pytest.mark.parametrize(
        "change",
        [
            {"kind": "void"},
            {"v": "2"},
            {"billed_micros": "-1"},
            {"billed_micros": "12.5"},
            {"billed_micros": "\u00b2"},
            {"request_id": 42},
            {"request_id": ""},
            {"org_id": str(uuid.uuid4())},
            {"org_id": "not-a-uuid"},
            {"usage_estimated": "2"},
            {"attempts": "{not json"},
            {"attempts": '{"attempt_id": "x"}'},
            {"catalog_snapshot_id": "x" * 33},
        ],
        ids=lambda change: (
            next(iter(change)) + ":" + str(next(iter(change.values())))[:12]
        ),
    )
    def test_unsettleable_records_are_refused(self, organization, change):
        record = _record(organization)
        record.update(change)

        with pytest.raises(mc.InvalidSettlementRecord):
            mc.settle_spend(record)

        assert not ManagedCreditEntry.objects.exists()


@pytest.fixture
def usage_schema_current(transactional_db):
    """Bring ``usage`` back to its latest migration before a committing test,
    in case an earlier test left it at an older one.
    """
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes("usage")
    if executor.migration_plan(leaves):
        executor.migrate(leaves)


def _run_concurrently(calls):
    """Start every call at once, each on its own thread and DB connection."""
    barrier = threading.Barrier(len(calls))
    results, errors = [], []

    def run(call):
        try:
            barrier.wait(timeout=10)
            results.append(call())
        except Exception as exc:  # surfaced by the callers' assertions
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


@pytest.mark.django_db(transaction=True)
def test_concurrent_settle_same_request_one_row(usage_schema_current):
    org = Organization.objects.create(name="Concurrent settle org")
    record = _record(org, "req_concurrent")

    results, errors = _run_concurrently(
        [partial(mc.settle_spend, copy.deepcopy(record)) for _ in range(8)]
    )

    assert errors == []
    assert sorted(created for _, created in results) == [False] * 7 + [True]
    assert len({entry.pk for entry, _ in results}) == 1
    assert ManagedCreditEntry.objects.filter(organization=org).count() == 1
    assert _account(org).settled_spend_micros == 11_815


@pytest.mark.django_db(transaction=True)
def test_concurrent_writes_keep_the_account_exact(usage_schema_current, gateway):
    org = Organization.objects.create(name="Concurrent writes org")
    spends = [
        partial(mc.settle_spend, _record(org, f"req_{i}", billed_micros=1_000 + i))
        for i in range(6)
    ]
    grants = [
        partial(mc.grant, org, 1_000_000, idempotency_key=f"g{i}", actor=None, note="")
        for i in range(6)
    ]

    results, errors = _run_concurrently(spends + grants)

    assert errors == [] and all(created for _, created in results)
    account = _account(org)
    # No lost update: every write saw the one before it under the account lock.
    assert account.settled_spend_micros == sum(1_000 + i for i in range(6))
    assert (account.granted_total_micros, account.version) == (6_000_000, 6)
    assert len(_pushes(gateway)) == 6


@pytest.mark.integration
class TestGrantedPush:
    def test_push_payload_has_version_and_floor(
        self, organization, gateway, django_capture_on_commit_callbacks
    ):
        schema = _golden("credits-granted-push-v1.schema.json")["$defs"]["Request"]
        mc.settle_spend(_record(organization, "req_floor"))

        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            mc.grant(organization, 7_000_000, idempotency_key="g", actor=None, note="")
        assert _pushes(gateway) == []  # nothing is sent before the commit

        for callback in callbacks:
            callback()

        (push,) = _pushes(gateway)
        payload = push.kwargs["json"]
        assert push.args == ("PUT", f"/-/orgs/{organization.pk}/credits/granted")
        assert payload == {
            "granted_total_micros": 7_000_000,
            "version": 1,
            "settled_spend_floor_micros": 11_815,
        }
        Draft202012Validator(schema).validate(payload)
        assert _account(organization).gateway_acked_version == 1

    def test_spend_and_duplicates_do_not_push(
        self, organization, gateway, django_capture_on_commit_callbacks
    ):
        mc.grant(organization, 1_000_000, idempotency_key="g", actor=None, note="")

        with django_capture_on_commit_callbacks(execute=True):
            mc.settle_spend(_record(organization))
            mc.grant(organization, 1_000_000, idempotency_key="g", actor=None, note="")

        assert _pushes(gateway) == []

    @pytest.mark.parametrize(
        "failure",
        [
            GatewayClientError("Gateway returned 503", status_code=503),
            httpx.ReadError("connection reset"),
            ValueError("gateway answered with a body that is not JSON"),
        ],
        ids=["gateway-error", "transport", "bad-body"],
    )
    def test_push_failure_is_logged_not_raised(
        self, organization, gateway, failure, django_capture_on_commit_callbacks
    ):
        gateway.request.side_effect = failure

        with capture_logs() as logs:
            with django_capture_on_commit_callbacks(execute=True):
                entry, created = mc.grant(
                    organization, 1_000_000, idempotency_key="g", actor=None, note=""
                )
            assert mc.push_granted(organization) is None

        assert created and entry.pk
        failures = [e for e in logs if e["event"] == "managed_credits_push_failed"]
        assert len(failures) == 2
        assert failures[0]["log_level"] == "warning"
        assert failures[0]["org_id"] == str(organization.pk)
        account = _account(organization)
        assert (account.version, account.gateway_acked_version) == (1, 0)

    def test_push_clamps_a_negative_granted_total_to_zero(self, organization, gateway):
        mc.grant(organization, 1_000_000, idempotency_key="g", actor=None, note="")
        mc.expire(organization, 3_000_000, idempotency_key="e")

        state = mc.push_granted(organization)

        assert _account(organization).granted_total_micros == -2_000_000
        assert _pushes(gateway)[-1].kwargs["json"]["granted_total_micros"] == 0
        assert state["granted_version"] == 2

    def test_acked_version_follows_the_gateway_and_never_regresses(
        self, organization, gateway
    ):
        mc.grant(organization, 1_000_000, idempotency_key="g1", actor=None, note="")
        mc.grant(organization, 1_000_000, idempotency_key="g2", actor=None, note="")

        # The gateway still holds version 1: nothing is acked.
        gateway.request.side_effect = lambda *a, **k: {
            **_echo_gateway(*a, **k),
            "applied": False,
            "granted_version": 1,
        }
        mc.push_granted(organization)
        assert _account(organization).gateway_acked_version == 0

        gateway.request.side_effect = _echo_gateway
        mc.push_granted(organization)
        assert _account(organization).gateway_acked_version == 2

        ManagedCreditAccount.objects.filter(organization=organization).update(
            gateway_acked_version=5
        )
        mc.push_granted(organization)
        assert _account(organization).gateway_acked_version == 5

    def test_ensure_account_pushed_creates_zero_account(self, organization, gateway):
        assert not ManagedCreditAccount.objects.filter(
            organization=organization
        ).exists()

        state = mc.ensure_account_pushed(organization)

        account = _account(organization)
        assert (
            account.granted_total_micros,
            account.settled_spend_micros,
            account.version,
        ) == (0, 0, 0)
        assert not ManagedCreditEntry.objects.filter(organization=organization).exists()
        (push,) = _pushes(gateway)
        assert push.args == ("PUT", f"/-/orgs/{organization.pk}/credits/granted")
        assert push.kwargs["json"] == {
            "granted_total_micros": 0,
            "version": 0,
            "settled_spend_floor_micros": 0,
        }
        assert state["applied"] is True

        # Idempotent: an existing account is pushed as it stands, never reset.
        mc.grant(organization, 3_000_000, idempotency_key="g", actor=None, note="")
        mc.ensure_account_pushed(organization)
        assert (
            ManagedCreditAccount.objects.filter(organization=organization).count() == 1
        )
        assert _pushes(gateway)[-1].kwargs["json"]["granted_total_micros"] == 3_000_000

    def test_ensure_account_pushed_reports_a_failed_push(self, organization, gateway):
        gateway.request.side_effect = GatewayClientError("down", status_code=503)

        assert mc.ensure_account_pushed(organization) is None
        assert _account(organization).granted_total_micros == 0
