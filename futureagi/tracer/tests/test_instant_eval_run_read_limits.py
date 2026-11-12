"""W10-INSTANT-EVAL-L05: a permitted increment need not permit reading all."""

from types import SimpleNamespace

import pytest
from django.test import override_settings

from tracer.models.eval_task import EvalTaskStatus
from tracer.services import instant_eval_run as run_service


@pytest.fixture
def sample_summary(monkeypatch):
    def summary(
        *,
        unit="traces",
        requested=1000,
        population=250_000,
        status=EvalTaskStatus.COMPLETED,
        pending=0,
        cancelled=False,
    ):
        task = SimpleNamespace(
            status=status, filters={}, start_time=None, updated_at=None
        )
        run = SimpleNamespace(
            rows_requested=requested,
            population=population,
            population_is_lower_bound=False,
            sample_size=1000,
            cancelled_at="cancelled" if cancelled else None,
            created_at=None,
            created_by=None,
            credits_estimate=0,
            date_from=None,
            date_to=None,
            filters=[],
            date_preset=None,
            label=None,
            search=None,
        )
        view = SimpleNamespace(unit=unit, row_type=unit, config_ids=["question"])
        monkeypatch.setattr(
            run_service.summary_service,
            "summary",
            lambda _: {
                "total": requested,
                "pending": pending,
                "failed": 0,
                "rows_stored": requested,
                "_progress": {
                    "first_ms": None,
                    "last_ms": None,
                    "recent_entries": 0,
                    "settled_entries": 0,
                    "answers_given": requested - pending,
                },
            },
        )
        monkeypatch.setattr(run_service, "_in_flight", lambda *_: 0)
        return run_service._summary_payload(task, run, view, [], None)["sample"]

    return summary


@override_settings(INSTANT_EVAL_MAX_ROWS=100_000)
@pytest.mark.parametrize(
    "unit,requested,population,next_size,can_extend,can_read_all",
    [
        pytest.param("traces", 1000, 250_000, 1000, True, False, id="large-view"),
        pytest.param("traces", 1000, 100_000, 1000, True, True, id="at-limit"),
        pytest.param("traces", 1000, 100_001, 1000, True, False, id="over-limit"),
        pytest.param("sessions", 99_500, 250_000, 500, True, False, id="last-chunk"),
        pytest.param("spans", 100_000, 250_000, 0, False, False, id="cap-reached"),
        pytest.param("traces", 1000, 1500, 500, True, True, id="remaining-fits"),
        pytest.param("traces", 1000, 1000, 0, False, False, id="all-read"),
        pytest.param("users", 1000, 20_000, 1000, True, True, id="users-at-limit"),
        pytest.param("users", 1000, 20_001, 1000, True, False, id="users-over-limit"),
    ],
)
def test_read_all_eligibility_is_separate_from_bounded_read_more(
    sample_summary, unit, requested, population, next_size, can_extend, can_read_all
):
    sample = sample_summary(unit=unit, requested=requested, population=population)

    assert sample["next_size"] == next_size
    assert sample["can_extend"] is can_extend
    assert sample["can_read_all"] is can_read_all


@override_settings(INSTANT_EVAL_MAX_ROWS=100_000)
@pytest.mark.parametrize(
    "state",
    [
        pytest.param({"status": EvalTaskStatus.RUNNING}, id="running"),
        pytest.param({"pending": 1}, id="stalled"),
        pytest.param(
            {"status": EvalTaskStatus.PAUSED, "pending": 1}, id="paused-with-rows-left"
        ),
        pytest.param({"cancelled": True}, id="cancelled"),
    ],
)
def test_read_all_preserves_run_state_guards(sample_summary, state):
    sample = sample_summary(population=2000, **state)

    assert sample["can_extend"] is False
    assert sample["can_read_all"] is False


@override_settings(INSTANT_EVAL_MAX_ROWS=1500)
def test_read_all_uses_the_deployment_limit(sample_summary):
    sample = sample_summary(population=2000)

    assert sample["next_size"] == 500
    assert sample["can_extend"] is True
    assert sample["can_read_all"] is False
