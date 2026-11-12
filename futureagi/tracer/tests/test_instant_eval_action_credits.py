"""W10-INSTANT-EVAL-U02: Read more, Read all, Retry and Run again each bill on
one click, so the run's summary says what each would cost, priced as the
control and start views bill it; nothing where answers aren't billed."""

from types import SimpleNamespace

import pytest

from tracer.models.eval_task import EvalTaskStatus
from tracer.services import instant_eval_rows as rows_service
from tracer.services import instant_eval_run as run_service

# What Jev charges an answer on Cloud.
RATE = 0.06


@pytest.fixture
def action_credits(monkeypatch):
    def summary(
        *,
        price=RATE,
        unit="sessions",
        requested=12,
        population=40,
        sample_size=12,
        questions=2,
        settled=24,
        given=23,
    ):
        task = SimpleNamespace(
            status=EvalTaskStatus.COMPLETED,
            filters={},
            start_time=None,
            updated_at=None,
        )
        run = SimpleNamespace(
            rows_requested=requested,
            population=population,
            population_is_lower_bound=False,
            sample_size=sample_size,
            questions=questions,
            cancelled_at=None,
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
        view = SimpleNamespace(
            unit=unit,
            row_type=unit,
            config_ids=[f"question-{n}" for n in range(questions)],
        )
        monkeypatch.setattr(
            run_service.summary_service,
            "summary",
            lambda _: {
                "total": requested,
                "pending": 0,
                "failed": settled - given,
                "rows_stored": requested,
                "_progress": {
                    "first_ms": None,
                    "last_ms": None,
                    "recent_entries": 0,
                    "settled_entries": settled,
                    "answers_given": given,
                },
            },
        )
        monkeypatch.setattr(run_service, "_in_flight", lambda *_: 0)
        # The deployment's row cap stays far above these runs.
        monkeypatch.setattr(run_service, "max_rows", lambda: 100_000)
        return run_service._summary_payload(task, run, view, [], price)[
            "action_credits"
        ]

    return summary


def test_a_billed_sample_prices_each_control(action_credits):
    # Two questions over a sample of 12 of 40 sessions, one answer failed.
    assert action_credits() == pytest.approx(
        {
            # The sample's size again, then the 28 sessions left.
            "read_more": 12 * 2 * RATE,
            "read_all": 28 * 2 * RATE,
            # Each failed answer is read again.
            "retry_failed": 1 * RATE,
            # The same sample of 12, asked again.
            "run_again": 12 * 2 * RATE,
        }
    )


def test_a_user_question_prices_the_sessions_it_reads(action_credits):
    per_user = rows_service.SESSIONS_PER_USER
    assert action_credits(unit="users", questions=1, settled=12, given=12) == (
        pytest.approx(
            {
                "read_more": 12 * per_user * RATE,
                "read_all": 28 * per_user * RATE,
                "retry_failed": None,
                "run_again": 12 * per_user * RATE,
            }
        )
    )


def test_a_run_that_read_every_row_prices_only_run_again(action_credits):
    assert action_credits(
        requested=40, sample_size=None, settled=80, given=80
    ) == pytest.approx(
        {
            "read_more": None,
            "read_all": None,
            "retry_failed": None,
            "run_again": 40 * 2 * RATE,
        }
    )


def test_nothing_is_priced_where_answers_are_not_billed(action_credits):
    assert action_credits(price=None) is None
