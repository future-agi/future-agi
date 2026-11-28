"""Independent examples for finding-volume escalation and human ownership."""

from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest

from tracer.services.feed_escalation_policy import (
    Counts,
    Policy,
    evaluate,
    settled_boundary,
)

pytestmark = pytest.mark.unit
T = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def observe(
    status="for_review",
    state=None,
    *,
    count=20,
    history=120,
    minute=0,
    policy=None,
    origin="system",
    input_revision=1,
):
    return evaluate(
        status=status,
        origin=origin,
        state=state or {},
        counts=Counts(count, history, affected_traces=8),
        policy=policy or Policy(),
        boundary=T + timedelta(minutes=minute),
        input_revision=input_revision,
    )


@pytest.mark.parametrize(
    "history,current,threshold,qualifies",
    [
        (0, 1, 20, False),
        (0, 10, 20, False),
        (0, 20, 20, True),
        (24, 5, 20, False),
        (120, 20, 20, True),
        (2400, 150, 300, False),
        (2400, 300, 300, True),
        (161, 20, Fraction(161, 8), False),
    ],
)
def test_entry_examples(history, current, threshold, qualifies):
    result = observe(count=current, history=history)
    assert result.status == "for_review"
    assert result.entry_threshold == threshold
    assert result.state["entry_checks"] == int(qualifies)


def test_entry_requires_four_distinct_boundaries_and_freezes_baseline():
    state = {}
    for minute in (0, 5, 10):
        result = observe(state=state, minute=minute, history=120 + minute * 100)
        assert result.status == "for_review"
        state = result.state
    result = observe(state=state, minute=15, history=2400)
    assert result.status == "escalating"
    assert result.transitioned
    assert result.baseline == 5
    assert result.entry_threshold == 20


def test_quiet_recovery_requires_three_checks_and_preserves_frozen_baseline():
    result = observe()
    for minute in (5, 10, 15):
        result = observe(state=result.state, minute=minute)
    for minute in (20, 25):
        result = observe(
            "escalating", result.state, minute=minute, count=8, history=2400
        )
        assert result.status == "escalating"
        assert result.recovery_threshold == 8
    result = observe("escalating", result.state, minute=30, count=8, history=2400)
    assert result.status == "for_review"
    assert result.transitioned


@pytest.mark.parametrize(
    "status", ["escalating", "for_review", "acknowledged", "resolved"]
)
@pytest.mark.parametrize("origin", ["manual", "legacy_unknown"])
def test_user_and_unknown_ownership_never_change(status, origin):
    result = observe(status, origin=origin, count=10000)
    assert result.status == status
    assert not result.transitioned
    assert result.state == {}


def test_retry_and_changed_input_at_same_boundary_do_not_confirm_twice():
    first = observe()
    retry = observe(state=first.state, input_revision=2)
    assert retry.state["entry_checks"] == 1
    assert retry.status == "for_review"
    assert not retry.transitioned


def test_changed_input_that_fails_at_same_boundary_discards_candidate():
    first = observe()
    result = observe(state=first.state, count=1, input_revision=2)
    assert result.state["entry_checks"] == 0
    assert result.state["frozen_baseline"] is None


def test_missing_boundary_resets_confirmation():
    first = observe()
    result = observe(state=first.state, minute=10)
    assert result.state["entry_checks"] == 1


def test_missing_boundary_discards_the_unconfirmed_baseline():
    first = observe(history=0)
    result = observe(state=first.state, minute=10, history=2400)
    assert result.baseline == 100
    assert result.state["entry_checks"] == 0
    assert result.state["frozen_baseline"] is None


def test_out_of_order_boundary_does_not_replace_current_evidence():
    first = observe(minute=5)
    result = observe(state=first.state, minute=0, count=0)
    assert result.state == first.state


def test_policy_change_resets_candidate_without_replaying_same_boundary():
    first = observe()
    new_policy = Policy(revision=2)
    changed = observe(state=first.state, policy=new_policy)
    assert changed.state["entry_checks"] == 0
    result = observe(state=changed.state, minute=5, policy=new_policy)
    assert result.state["entry_checks"] == 1


def test_recovery_policy_change_keeps_active_baseline_and_resets_streak():
    result = observe()
    for minute in (5, 10, 15):
        result = observe(state=result.state, minute=minute)
    quiet = observe("escalating", result.state, minute=20, count=0)
    changed = observe(
        "escalating",
        quiet.state,
        minute=20,
        count=0,
        history=2400,
        policy=Policy(revision=2),
    )
    assert changed.status == "escalating"
    assert changed.baseline == 5
    assert changed.state["recovery_checks"] == 0


def test_between_thresholds_preserves_escalation_and_clears_recovery_streak():
    result = observe()
    for minute in (5, 10, 15):
        result = observe(state=result.state, minute=minute)
    quiet = observe("escalating", result.state, minute=20, count=0)
    result = observe("escalating", quiet.state, minute=25, count=12)
    assert result.status == "escalating"
    assert result.state["recovery_checks"] == 0


def test_custom_confirmation_uses_duration_plus_one_observation():
    policy = Policy(entry_confirmation_minutes=5)
    first = observe(policy=policy)
    assert first.status == "for_review"
    assert observe(state=first.state, minute=5, policy=policy).status == "escalating"


@pytest.mark.parametrize(
    "changes",
    [
        {"min_findings": True},
        {"min_findings": 1},
        {"min_findings": 1000001},
        {"entry_multiplier": "NaN"},
        {"entry_multiplier": "Infinity"},
        {"entry_multiplier": "3.01"},
        {"entry_multiplier": 1},
        {"entry_delta": 0},
        {"entry_confirmation_minutes": 7},
        {"entry_confirmation_minutes": 0},
        {"recovery_confirmation_minutes": 65},
        {"recovery_min": 20},
        {"recovery_delta": 10},
        {"recovery_multiplier": "3"},
        {"recovery_multiplier": "0.9"},
        {"workspace_id": "foreign"},
    ],
)
def test_invalid_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        Policy.from_values(changes)


def test_partial_policy_merge_validates_cross_field_constraints():
    with pytest.raises(ValueError):
        Policy.from_values({"min_findings": 4}, current=Policy())
    policy = Policy.from_values(
        {"min_findings": 4, "recovery_min": 1}, current=Policy()
    )
    assert policy.min_findings == 4


def test_numeric_multiplier_spelling_does_not_create_a_configuration_change():
    policy = Policy.from_values({"entry_multiplier": 3, "recovery_multiplier": "1.50"})
    assert policy.values() == Policy().values()


def test_settled_boundary_uses_ten_minute_lag_and_five_minute_floor():
    now = T + timedelta(minutes=13, seconds=59)
    assert settled_boundary(now) == T


def test_counts_reject_invalid_values_and_naive_times():
    with pytest.raises(ValueError):
        Counts(-1, 0)
    with pytest.raises(ValueError):
        settled_boundary(datetime(2026, 10, 14))
