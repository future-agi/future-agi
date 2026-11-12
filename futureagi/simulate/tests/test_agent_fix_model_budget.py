"""W10-AGENT-FIX-L01: engine calls share the launch budget with check runs."""

import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from decimal import Decimal
from threading import Event
from unittest.mock import Mock

import httpx
import pytest
from django.db import connections

from simulate.selectors.agent_fix import committed_usd
from simulate.services import agent_fix_models as models
from simulate.services.agent_fix_engine import _require_budget
from simulate.services.agent_fix_models import _bill as bill_model_call
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_agent_fix_services import (
    _fix_run,
    any_suite_may_launch as any_suite_may_launch,
    environment as environment,
    source_run as source_run,
    temporal as temporal,
    versioning_on as versioning_on,
)
from simulate.tests.test_harness_versioning_backfill import storage as storage

pytestmark = pytest.mark.django_db
MODEL = "deepseek/deepseek-v4.1-flash"
BODY = json.dumps({"model": MODEL, "messages": [], "max_tokens": 1024}).encode()


def reply(cost="0.025", status=200):
    return httpx.Response(
        status,
        json={"choices": []},
        headers={"x-agentcc-cost": cost, "x-agentcc-request-id": "budget-test"},
    )


@pytest.fixture(autouse=True)
def gateway(settings, monkeypatch):
    settings.AGENT_FIX_ENGINE_MODELS = [MODEL]
    settings.AGENT_FIX_WORST_CASE_USD_PER_EXECUTION = "1"
    monkeypatch.setattr(models, "platform_model_gateway", lambda: ("unused", "test"))
    monkeypatch.setattr(
        models, "resolve_gateway_internal_url", lambda: "http://gateway.example.test"
    )
    monkeypatch.setattr(models, "billed_ai_usd", lambda usd: usd * Decimal(2))
    post = Mock(return_value=reply())
    monkeypatch.setattr(models.httpx, "post", post)
    return post


@pytest.fixture(autouse=True)
def bill(monkeypatch):
    emit = Mock()
    monkeypatch.setattr(models, "_bill", emit)
    return emit


def test_call_that_cannot_fit_is_capped_before_dispatch(source_run, gateway, bill):
    fix = _fix_run(source_run, spent_usd=Decimal("4.99"))

    def bounded_gateway(*args, headers, **kwargs):
        # The real gateway refuses/clamps before the provider call. Without a
        # cap this ordinary five-cent call used to take the fix to $5.04.
        cap = Decimal(headers.get("x-agentcc-max-cost-usd", "Infinity"))
        return reply("0", 402) if cap < Decimal("0.025") else reply()

    gateway.side_effect = bounded_gateway
    result = models.complete(fix, BODY)

    assert result.status_code == 402
    assert Decimal(
        gateway.call_args.kwargs["headers"]["x-agentcc-max-cost-usd"]
    ) == Decimal("0.005")
    fix.refresh_from_db()
    assert fix.spent_usd == Decimal("4.99")
    assert committed_usd(fix) == Decimal("4.99")
    bill.assert_not_called()


def test_reservation_is_visible_to_model_and_check_admission(source_run, gateway):
    fix = _fix_run(source_run, spent_usd=Decimal("4.9"))

    def while_in_flight(*args, **kwargs):
        assert committed_usd(fix) == Decimal("5")
        with pytest.raises(HostedHarnessError) as busy:
            models.complete(fix, BODY)
        assert (busy.value.status_code, busy.value.retryable) == (429, True)
        with pytest.raises(HostedHarnessError) as check:
            _require_budget(fix, 1)
        assert check.value.code == "fix_budget_exhausted"
        return reply()

    gateway.side_effect = while_in_flight
    assert models.complete(fix, BODY).status_code == 200
    assert committed_usd(fix) == Decimal("4.95")
    assert gateway.call_count == 1


@pytest.mark.django_db(transaction=True)
def test_simultaneous_calls_cannot_reserve_the_same_remaining_budget(
    source_run, gateway
):
    fix = _fix_run(source_run, spent_usd=Decimal("4.9"))
    started, release = Event(), Event()

    def pending_gateway(*args, **kwargs):
        started.set()
        assert release.wait(10), "test did not release the gateway"
        return reply()

    def complete():
        try:
            try:
                return models.complete(fix, BODY).status_code
            except HostedHarnessError as exc:
                return exc.status_code
        finally:
            connections.close_all()

    gateway.side_effect = pending_gateway
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(complete)
        try:
            assert started.wait(10), "first request did not reach the gateway"
            second = pool.submit(complete)
            done, _ = wait([first, second], timeout=5, return_when=FIRST_COMPLETED)
            assert done == {second}, "the second call must refuse without dispatch"
            assert second.result() == 429
        finally:
            release.set()
        assert first.result() == 200
    assert gateway.call_count == 1
    assert committed_usd(fix) == Decimal("4.95")


def test_settlement_releases_only_unused_budget(source_run, gateway):
    fix = _fix_run(source_run, spent_usd=Decimal("4.9"))
    assert models.complete(fix, BODY).status_code == 200
    assert committed_usd(fix) == Decimal("4.95")
    assert models.complete(fix, BODY).status_code == 200
    assert committed_usd(fix) == Decimal("5")
    with pytest.raises(HostedHarnessError) as exhausted:
        models.complete(fix, BODY)
    assert exhausted.value.code == "fix_budget_exhausted"
    assert gateway.call_count == 2


@pytest.mark.parametrize(
    "error", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout]
)
def test_failure_before_dispatch_releases_reservation(source_run, gateway, bill, error):
    fix = _fix_run(source_run)
    gateway.side_effect = error("not sent")
    with pytest.raises(HostedHarnessError) as failed:
        models.complete(fix, BODY)
    assert failed.value.retryable is True
    assert committed_usd(fix) == 0
    bill.assert_not_called()


def test_ambiguous_timeout_keeps_liability_reserved(source_run, gateway, bill):
    fix = _fix_run(source_run, spent_usd=Decimal("4.9"))
    gateway.side_effect = httpx.ReadTimeout("response lost after dispatch")
    with pytest.raises(HostedHarnessError):
        models.complete(fix, BODY)
    assert committed_usd(fix) == Decimal("5")
    fix.refresh_from_db()
    assert fix.spent_usd == Decimal("4.9")
    bill.assert_not_called()


@pytest.mark.parametrize("cost", [None, "unreadable", "NaN", "Infinity", "-1"])
def test_unknown_cost_does_not_reopen_budget(source_run, gateway, bill, cost):
    fix = _fix_run(source_run, spent_usd=Decimal("4.9"))
    headers = {} if cost is None else {"x-agentcc-cost": cost}
    gateway.return_value = httpx.Response(200, json={}, headers=headers)
    models.complete(fix, BODY)
    assert committed_usd(fix) == Decimal("5")
    bill.assert_not_called()


def test_gateway_cap_violation_cannot_overbill_the_fix(source_run, gateway, bill):
    fix = _fix_run(source_run, spent_usd=Decimal("4.99"))
    # Defense in depth if the gateway violates the cap we sent.
    models.complete(fix, BODY)
    fix.refresh_from_db()
    assert fix.spent_usd == Decimal("5")
    assert bill.call_args.kwargs["billed_usd"] == Decimal("0.01")


def test_billable_credits_cannot_exceed_reserved_billed_dollars(
    source_run, monkeypatch
):
    from ee.usage.services import emitter

    events = []
    monkeypatch.setattr(models, "_bill", bill_model_call)
    monkeypatch.setattr(models, "is_oss", lambda: False)
    monkeypatch.setattr(emitter, "emit", events.append)
    fix = _fix_run(source_run, spent_usd=Decimal("4.99"))
    models.complete(fix, BODY)
    [event] = events
    assert event.amount == 1.0  # One remaining credit, not the five-cent cost.
    assert event.properties["raw_cost_usd"] == "0.025"


def test_unbilled_refusal_releases_the_hold(source_run, gateway, bill):
    fix = _fix_run(source_run, spent_usd=Decimal("4.99"))
    gateway.return_value = httpx.Response(429, json={"error": "rate limited"})
    assert models.complete(fix, BODY).status_code == 429
    assert committed_usd(fix) == Decimal("4.99")
    gateway.return_value = reply("0.001")
    assert models.complete(fix, BODY).status_code == 200
    assert committed_usd(fix) == Decimal("4.992")
    assert bill.call_count == 1


def test_small_costs_round_up_for_admission_not_down(source_run, gateway, bill):
    fix = _fix_run(source_run, spent_usd=Decimal("4.9999"))
    gateway.return_value = reply("0.00002")
    models.complete(fix, BODY)
    assert committed_usd(fix) == Decimal("5")
    # The customer pays the actual cost, not the conservative accounting round-up.
    assert bill.call_args.kwargs["billed_usd"] == Decimal("0.00004")


def test_gateway_cap_rounds_down_after_markup_conversion(
    source_run, gateway, monkeypatch
):
    fix = _fix_run(source_run, spent_usd=Decimal("4.99"))
    monkeypatch.setattr(models, "billed_ai_usd", lambda usd: usd * Decimal(3))
    gateway.return_value = reply("0")
    models.complete(fix, BODY)
    cap = Decimal(gateway.call_args.kwargs["headers"]["x-agentcc-max-cost-usd"])
    assert cap == Decimal("0.003333")
    assert cap * 3 <= Decimal("0.01")
