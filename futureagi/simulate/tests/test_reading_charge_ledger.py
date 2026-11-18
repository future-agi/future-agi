"""A reading's charge, from its cost file to the billing ledger.

The tests in test_harness_usage.py stop at ``emit``. These go through the real
usage stream and the billing consumer into ``UsageEventLog``, with charging
switched on (HARNESS_AGENT_READING_CHARGE_ENABLED), and check what a customer is
charged for one reading: the amount, the organization, one row per stage, and
one charge for every attempt at the same reading, a retried step included.
With the setting off, its default, nothing is charged and one line is logged.

The token counts are one real reading's cost file, of the OpenAI Agents SDK
reference. That reading named a gateway alias the price catalog does not list;
production reads with ``vertex_ai/gemini-3.7-flash`` (ALK_HARNESS_MODEL), so the
priced tests name that model and keep every count as the reading wrote it.
"""

from __future__ import annotations

import copy
import logging
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4, uuid5

import pytest

from simulate.services import harness_usage
from simulate.services.hosted_harness import HostedHarnessError
from tfc.ee_loader import has_ee

requires_cloud_billing = pytest.mark.skipif(
    not has_ee("ee.cloud"), reason="requires ee/cloud billing.yaml and its consumer"
)

# One real reading's cost file, as the kit wrote it.
READING_COST_FILE = {
    "complete": False,
    "schema": "futureagi.harness-spend.v1",
    "stages": [
        {
            "model_turns": {"gemini-th7957-alias": 9},
            "models": ["gemini-th7957-alias"],
            "priced": 0,
            "refusals": {"loop:mcp__contract__submit_contract": 1},
            "stage": "understand-agent",
            "tokens_cached": 155419,
            "tokens_in": 155433,
            "tokens_out": 10293,
            "tools": {
                "loop:Agent": 1,
                "loop:Glob": 1,
                "loop:Read": 5,
                "loop:mcp__contract__submit_contract": 2,
            },
            "turns": 9,
            "unpriced": 1,
            "usd": 0.0,
        }
    ],
    "total_usd": 0.0,
    "unpriced_turns": 1,
}

PRODUCTION_READING_MODEL = "vertex_ai/gemini-3.7-flash"


# Read from the platform catalog (ee/prompts/additional_models.py), so the expected
# amount holds whether or not the entry carries a cache-read rate yet:
# without one, cached input is charged at the full input rate.
def _rate(key, default=None):
    from agentic_eval.core_evals.run_prompt.model_pricing import get_model_pricing

    value = get_model_pricing(PRODUCTION_READING_MODEL).get(key, default)
    return None if value is None else Decimal(str(value)) / 1_000_000


# billing.yaml: credits = cost * ai_cost_markup (1.2) / 0.01 USD per credit.
CREDITS_PER_USD = Decimal("1.2") / Decimal("0.01")


def _cost_file(model=PRODUCTION_READING_MODEL, extra_stage=None):
    spend = copy.deepcopy(READING_COST_FILE)
    for stage in spend["stages"]:
        stage["models"] = [model]
        stage["model_turns"] = {model: stage["turns"]}
    if extra_stage is not None:
        spend["stages"].append(extra_stage)
    return spend


def _expected_usd(stage) -> Decimal:
    input_rate = _rate("input_per_1M_tokens")
    cached_rate = _rate("cached_input_per_1M_tokens") or input_rate
    return (
        (stage["tokens_in"] - stage["tokens_cached"]) * input_rate
        + stage["tokens_cached"] * cached_rate
        + stage["tokens_out"] * _rate("output_per_1M_tokens")
    )


@pytest.fixture
def organization(db):
    from accounts.models.organization import Organization

    return Organization.objects.create(name="reading-charge-ledger")


@pytest.fixture
def other_organization(db):
    from accounts.models.organization import Organization

    return Organization.objects.create(name="reading-charge-ledger-other")


@pytest.fixture
def subscription(db, organization):
    from ee.usage.models.usage import (
        OrganizationSubscription,
        PlanChoices,
        SubscriptionTier,
    )

    tier, _ = SubscriptionTier.objects.get_or_create(
        name="free", defaults={"wallet_refill_amount": 0}
    )
    return OrganizationSubscription.objects.create(
        organization=organization,
        subscription_tier=tier,
        plan=PlanChoices.FREE.value,
    )


@pytest.fixture
def ledger(monkeypatch, settings, subscription):
    """The real usage stream and consumer, with charging forced on."""
    from ee.usage.services.emitter import get_redis

    settings.USAGE_EVENTS_ENABLED = True
    settings.HARNESS_AGENT_READING_CHARGE_ENABLED = True
    monkeypatch.setattr(harness_usage, "is_oss", lambda: False)
    redis = get_redis()
    # ee.cloud is absent in the open-source tree, so it is imported on its own.
    from ee.cloud.billing.consumer import _ensure_consumer_group

    def purge():
        for pattern in ("usage:*", "plan:*", "pause:*", "budget:*"):
            for key in redis.keys(pattern):
                redis.delete(key)

    purge()
    _ensure_consumer_group()
    yield
    purge()


def _record(organization, run_id, spend, subject_id=None, purpose="agent"):
    return harness_usage.record_agent_reading_usage(
        organization_id=organization.id,
        workspace_id=None,
        subject_id=subject_id or uuid4(),
        run_id=str(run_id),
        purpose=purpose,
        spend=spend,
    )


def _drain():
    from ee.cloud.billing.consumer import process_batch

    while process_batch():
        pass


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_a_reading_is_charged_its_cost_file_once_to_its_organization(
    ledger, organization, other_organization
):
    from ee.usage.models.usage import UsageEventLog, UsageSummary

    spend = _cost_file()
    stage = spend["stages"][0]
    run_id, subject_id = uuid4(), uuid4()

    report = _record(organization, run_id, spend, subject_id)
    _drain()

    expected_usd = _expected_usd(stage)
    expected_credits = expected_usd * CREDITS_PER_USD
    row = UsageEventLog.objects.get()
    assert row.organization_id == organization.id
    assert row.event_id == uuid5(subject_id, "reading:agent:understand-agent")
    assert row.event_type == "harness_authoring"
    assert row.dimension == "ai_credits"
    assert row.amount_raw == pytest.approx(expected_credits, abs=Decimal("0.001"))
    assert Decimal(str(report["records"][0]["cost_usd"])) == pytest.approx(
        expected_usd, abs=Decimal("0.000002")
    )
    assert row.properties["model"] == PRODUCTION_READING_MODEL
    assert row.properties["input_tokens"] == stage["tokens_in"]
    assert row.properties["cached_input_tokens"] == stage["tokens_cached"]
    assert row.properties["output_tokens"] == stage["tokens_out"]
    assert row.properties["source_id"] == str(subject_id)
    assert row.properties["reading_run_id"] == str(run_id)
    # A cost file the kit marked incomplete is charged all the same.
    assert row.properties["complete"] is False
    assert not UsageEventLog.objects.filter(organization=other_organization).exists()
    summary = UsageSummary.objects.get(
        organization=organization, dimension="ai_credits"
    )
    charged = summary.total_usage

    # The same reading run recorded again (a replayed activity) adds nothing.
    _record(organization, run_id, spend, subject_id)
    _drain()

    assert UsageEventLog.objects.count() == 1
    summary.refresh_from_db()
    assert summary.total_usage == charged


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_each_stage_of_a_reading_is_one_row(ledger, organization):
    from ee.usage.models.usage import UsageEventLog

    check = dict(
        copy.deepcopy(READING_COST_FILE["stages"][0]),
        stage="check-agent",
        tokens_in=20_000,
        tokens_cached=0,
        tokens_out=2_000,
        models=[PRODUCTION_READING_MODEL],
    )
    spend = _cost_file(extra_stage=check)
    run_id, subject_id = uuid4(), uuid4()

    _record(organization, run_id, spend, subject_id)
    _drain()
    _record(organization, run_id, spend, subject_id)
    _drain()

    rows = {
        row.properties["authoring_stage"]: row
        for row in UsageEventLog.objects.filter(organization=organization)
    }
    assert set(rows) == {"understand-agent", "check-agent"}
    assert UsageEventLog.objects.filter(organization=organization).count() == 2
    for stage in spend["stages"]:
        row = rows[stage["stage"]]
        assert row.event_id == uuid5(subject_id, f"reading:agent:{stage['stage']}")
        assert row.amount_raw == pytest.approx(
            _expected_usd(stage) * CREDITS_PER_USD, abs=Decimal("0.001")
        )


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_three_attempts_at_one_reading_are_charged_once(ledger, organization):
    """Each attempt reads again under a new run id; the charge is the same."""
    from ee.usage.models.usage import UsageEventLog, UsageSummary

    spend, subject_id = _cost_file(), uuid4()

    _record(organization, uuid4(), spend, subject_id)
    _drain()
    charged = UsageSummary.objects.get(
        organization=organization, dimension="ai_credits"
    ).total_usage
    _record(organization, uuid4(), spend, subject_id)
    _record(organization, uuid4(), spend, subject_id)
    _drain()

    assert UsageEventLog.objects.filter(organization=organization).count() == 1
    assert (
        UsageSummary.objects.get(
            organization=organization, dimension="ai_credits"
        ).total_usage
        == charged
    )


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_a_retried_reading_step_is_charged_once(ledger, organization):
    """The workflow's step retry runs read_version again, which bills under a new
    run id each time (agent_reading_sandbox._bill). One charge, not three."""
    from ee.usage.models.usage import UsageEventLog
    from simulate.services import agent_reading_sandbox

    runtime = SimpleNamespace(
        organization_id=organization.id, workspace_id=None, agent_version_id=uuid4()
    )
    spend = _cost_file()

    for _attempt in range(3):
        agent_reading_sandbox._bill(runtime, str(uuid4()), spend)
    _drain()

    row = UsageEventLog.objects.get(organization=organization)
    assert row.event_id == uuid5(
        runtime.agent_version_id, "reading:agent:understand-agent"
    )
    assert row.amount_raw == pytest.approx(
        _expected_usd(spend["stages"][0]) * CREDITS_PER_USD, abs=Decimal("0.001")
    )


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_two_versions_are_two_charges(ledger, organization):
    from ee.usage.models.usage import UsageEventLog

    spend = _cost_file()

    _record(organization, uuid4(), spend, uuid4())
    _record(organization, uuid4(), spend, uuid4())
    _drain()

    assert UsageEventLog.objects.filter(organization=organization).count() == 2


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
@pytest.mark.parametrize("purpose", ["agent", "scan"])
def test_with_charging_off_a_reading_records_nothing_and_logs_once(
    ledger, settings, organization, caplog, purpose
):
    from ee.usage.models.usage import UsageEventLog

    settings.HARNESS_AGENT_READING_CHARGE_ENABLED = False
    spend, subject_id, run_id = _cost_file(), uuid4(), uuid4()
    caplog.set_level(logging.INFO, logger=harness_usage.__name__)

    assert _record(organization, run_id, spend, subject_id, purpose) is None
    _drain()

    assert not UsageEventLog.objects.filter(organization=organization).exists()
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("agent_reading_not_charged")
    ]
    assert len(lines) == 1
    stage = spend["stages"][0]
    assert f"subject_id={subject_id}" in lines[0]
    assert f"purpose={purpose}" in lines[0]
    assert f"run_id={run_id}" in lines[0]
    assert PRODUCTION_READING_MODEL in lines[0]
    for count in ("tokens_in", "tokens_cached", "tokens_out"):
        assert f'"{count}": {stage[count]}' in lines[0]
    assert '"credits"' in lines[0]


def test_charging_for_readings_is_off_by_default():
    """Unset, the setting is off: production needs no new value for it."""
    from django.conf import settings

    assert settings.HARNESS_AGENT_READING_CHARGE_ENABLED is False


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_a_cost_file_naming_an_unpriced_alias_charges_nothing(ledger, organization):
    """The reading names a model the platform catalog does not price, so no stage
    is charged: no row, not a zero row. The reading itself is kept by the caller
    (agent_reading_sandbox._bill logs agent_reading_not_billed)."""
    from ee.usage.models.usage import UsageEventLog

    with pytest.raises(HostedHarnessError) as refused:
        _record(organization, uuid4(), copy.deepcopy(READING_COST_FILE))
    _drain()

    assert refused.value.code == "authoring_model_unpriced"
    assert not UsageEventLog.objects.exists()


@pytest.mark.django_db
@pytest.mark.requires_ee
@requires_cloud_billing
def test_without_a_licence_a_reading_is_not_charged(
    monkeypatch, settings, organization, subscription
):
    """On a self-hosted install with no enterprise licence (is_oss), nothing is
    recorded at all."""
    from ee.usage.models.usage import UsageEventLog

    settings.USAGE_EVENTS_ENABLED = True
    monkeypatch.setattr(harness_usage, "is_oss", lambda: True)
    from ee.cloud.billing.consumer import _ensure_consumer_group

    _ensure_consumer_group()

    assert _record(organization, uuid4(), _cost_file()) is None
    _drain()

    assert not UsageEventLog.objects.filter(organization=organization).exists()
