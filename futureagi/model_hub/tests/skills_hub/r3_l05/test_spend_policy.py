"""Real shared Postgres accounting; no provider, time sleeps or invented usage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import close_old_connections

from model_hub.models.skills_hub_r3 import HubSkillSpendReservation
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.spend_policy import (
    close_admission,
    mark_paid_call_sent,
    reserve_admission,
    reserve_paid_call,
    settle_paid_call,
    spend_summary,
    update_spend_policy,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def principal(organization, workspace, user):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def budget(principal):
    update_spend_policy(
        principal=principal,
        revision=0,
        enabled=True,
        period_limit_usd="10",
        idempotency_key=uuid4(),
    )
    return principal


def admit(principal, amount="8", **kwargs):
    return reserve_admission(
        principal=principal,
        source=kwargs.pop("source", "skills_text"),
        source_id=kwargs.pop("source_id", uuid4()),
        operation_id=kwargs.pop("operation_id", uuid4()),
        ceiling_usd=amount,
        **kwargs,
    )


def call(principal, root, amount="3", **kwargs):
    return reserve_paid_call(
        principal=principal,
        admission_id=root.pk,
        operation_id=kwargs.pop("operation_id", uuid4()),
        kind=kwargs.pop("kind", "task"),
        allowance_usd=amount,
        generation=kwargs.pop("generation", 0),
    )


def test_no_policy_and_no_default_amount(principal):
    assert spend_summary(principal=principal)["period_limit_usd"] is None
    with pytest.raises(SkillsHubError, match="explicitly enable"):
        admit(principal)
    assert HubSkillSpendReservation.objects.count() == 0


@pytest.mark.parametrize(
    "amount", ["-1", "NaN", "Infinity", "0.000000001", 0.1, True, "10000000000"]
)
def test_invalid_money_cannot_authorize_spend(principal, amount):
    with pytest.raises(SkillsHubError) as caught:
        update_spend_policy(
            principal=principal,
            revision=0,
            enabled=True,
            period_limit_usd=amount,
            idempotency_key=uuid4(),
        )
    assert caught.value.code == "invalid_spend_policy"


def test_children_do_not_double_count_and_close_releases_only_unsent(budget):
    root = admit(budget)
    sent, unsent = call(budget, root), call(budget, root, "4", kind="judge")
    assert Decimal(spend_summary(principal=budget)["committed_usd"]) == 8
    mark_paid_call_sent(principal=budget, reservation_id=sent.pk, generation=0)
    close_admission(scope=budget.scope, admission_id=root.pk)
    unsent.refresh_from_db()
    assert unsent.state == "released"
    assert Decimal(spend_summary(principal=budget)["committed_usd"]) == 3
    settle_paid_call(
        scope=budget.scope,
        reservation_id=sent.pk,
        generation=0,
        actual_usd=None,
        sender_stopped=False,
    )
    summary = spend_summary(principal=budget)
    assert Decimal(summary["unknown_reserved_usd"]) == 3
    assert summary["active_senders"] == 1
    settle_paid_call(
        scope=budget.scope,
        reservation_id=sent.pk,
        generation=0,
        actual_usd="2",
        sender_stopped=True,
    )
    assert Decimal(spend_summary(principal=budget)["committed_usd"]) == 2
    assert spend_summary(principal=budget)["active_senders"] == 0


def test_stale_generation_and_duplicate_send_are_not_retry_permission(budget):
    root = admit(budget)
    row = call(budget, root, generation=7)
    mark_paid_call_sent(principal=budget, reservation_id=row.pk, generation=7)
    with pytest.raises(SkillsHubError) as caught:
        mark_paid_call_sent(principal=budget, reservation_id=row.pk, generation=7)
    assert caught.value.code == "execution_unknown"
    with pytest.raises(SkillsHubError) as caught:
        settle_paid_call(
            scope=budget.scope,
            reservation_id=row.pk,
            generation=6,
            actual_usd="0",
            sender_stopped=True,
        )
    assert caught.value.code == "stale_generation"
    row.refresh_from_db()
    assert row.sender_active and row.state == "in_flight"


def test_policy_reduction_blocks_prepared_transmission_but_not_settlement(budget):
    root = admit(budget)
    sent, pending = call(budget, root), call(budget, root)
    mark_paid_call_sent(principal=budget, reservation_id=sent.pk, generation=0)
    update_spend_policy(
        principal=budget,
        revision=1,
        enabled=True,
        period_limit_usd="1",
        idempotency_key=uuid4(),
    )
    with pytest.raises(SkillsHubError) as caught:
        mark_paid_call_sent(principal=budget, reservation_id=pending.pk, generation=0)
    assert caught.value.code == "budget_exhausted"
    settle_paid_call(
        scope=budget.scope,
        reservation_id=sent.pk,
        generation=0,
        actual_usd="2",
        sender_stopped=True,
    )
    with pytest.raises(SkillsHubError):
        admit(budget, "0.1")
    assert Decimal(spend_summary(principal=budget)["remaining_usd"]) == 0


def test_replay_precedes_rollover_and_policy_disable(budget, monkeypatch):
    key, source = uuid4(), uuid4()
    root = admit(budget, operation_id=key, source_id=source)
    update_spend_policy(
        principal=budget,
        revision=1,
        enabled=False,
        period_limit_usd="0",
        idempotency_key=uuid4(),
    )
    monkeypatch.setattr(
        "model_hub.services.skills_hub.r3.spend_policy.timezone.now",
        lambda: datetime(2035, 2, 1, tzinfo=UTC),
    )
    assert admit(budget, operation_id=key, source_id=source).pk == root.pk
    with pytest.raises(SkillsHubError) as caught:
        admit(budget, "7", operation_id=key, source_id=source)
    assert caught.value.code == "idempotency_conflict"


def test_unknown_original_period_survives_rollover(budget, monkeypatch):
    monkeypatch.setattr(
        "model_hub.services.skills_hub.r3.spend_policy.timezone.now",
        lambda: datetime(2030, 12, 31, tzinfo=UTC),
    )
    root = admit(budget)
    row = call(budget, root)
    mark_paid_call_sent(principal=budget, reservation_id=row.pk, generation=0)
    settle_paid_call(
        scope=budget.scope,
        reservation_id=row.pk,
        generation=0,
        actual_usd=None,
        sender_stopped=False,
    )
    close_admission(scope=budget.scope, admission_id=root.pk)
    monkeypatch.setattr(
        "model_hub.services.skills_hub.r3.spend_policy.timezone.now",
        lambda: datetime(2031, 1, 1, tzinfo=UTC),
    )
    new = admit(budget, "10", source="simulation_native")
    assert new.period_id != root.period_id
    summary = spend_summary(principal=budget)
    assert Decimal(summary["prior_period_unknown_usd"]) == 3
    assert Decimal(summary["committed_usd"]) == 10
    assert summary["active_senders"] == 1


def test_simultaneous_native_and_text_share_remaining_allowance(budget):
    barrier = Barrier(2)

    def work(source):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return admit(budget, "6", source=source).pk
        except SkillsHubError as exc:
            return exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(work, ["skills_text", "simulation_native"]))
    assert outcomes.count("budget_exhausted") == 1
    assert HubSkillSpendReservation.objects.count() == 1


def test_simultaneous_task_and_judge_cannot_overdraw_admission(budget):
    root = admit(budget, "5")
    barrier = Barrier(2)

    def work(kind):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return call(budget, root, "3", kind=kind).pk
        except SkillsHubError as exc:
            return exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(work, ["task", "judge"]))
    assert outcomes.count("budget_exhausted") == 1


def test_actual_overrun_is_never_discarded(budget):
    root = admit(budget, "2")
    row = call(budget, root, "2")
    mark_paid_call_sent(principal=budget, reservation_id=row.pk, generation=0)
    settle_paid_call(
        scope=budget.scope,
        reservation_id=row.pk,
        generation=0,
        actual_usd="12",
        sender_stopped=True,
    )
    assert Decimal(spend_summary(principal=budget)["committed_usd"]) == 12
    close_admission(scope=budget.scope, admission_id=root.pk)
    assert Decimal(spend_summary(principal=budget)["committed_usd"]) == 12


def test_wrong_scope_cannot_settle(budget, hub_other_workspace):
    root = admit(budget)
    row = call(budget, root)
    with pytest.raises(SkillsHubError) as caught:
        settle_paid_call(
            scope=SkillScope(budget.scope.organization_id, hub_other_workspace.pk),
            reservation_id=row.pk,
            generation=0,
            actual_usd="0",
            sender_stopped=True,
        )
    assert caught.value.code == "resource_not_found"
