"""Filter, then ask: a run keeps the view's date option and scope label, and a
user question honours every Users-view filter and its search text.

The users population itself (``instant_eval_users``) is checked here against a
stand-in for ClickHouse: the candidates come back in hash order, and the
Users list's per-user decision is a predicate. Its live parity with the Users
list is in ``test_instant_eval_population_parity_ch25``.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.test import override_settings
from rest_framework import status

from tracer.models.eval_task import EvalTask, EvalTaskStatus
from tracer.models.instant_eval import InstantEvalRun
from tracer.models.observation_span import EvalEntryStatus, EvalLogger
from tracer.selectors.eval_tasks.row_resolver import resolve_desired_rows
from tracer.services import instant_eval_rows as rows_service
from tracer.services import instant_eval_users as users_service
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.services.instant_eval_answers import record_answer
from tracer.tests.test_instant_eval_scale import (
    CONTROL,
    ESTIMATE,
    SUMMARY,
    WINDOW,
    _answer,
    _hash,
    _read_everything,
    _start,
    _summary,
)

# The scale suite's fixtures: a fake view of 40 sessions, a recorded workflow
# start, a yes/no question and Instant eval's license.
from tracer.tests.test_instant_eval_scale import fake_view as fake_view
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.tests.test_instant_eval_scale import started as started
from tracer.tests.test_instant_eval_scale import yes_no as yes_no

RUNS = "/tracer/instant-eval/runs/"
WINDOW_FROM = datetime(2026, 10, 1, tzinfo=UTC)
WINDOW_TO = datetime(2026, 10, 15, tzinfo=UTC)


def _leaf(column_id, op, value, filter_type="number", **config):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": op,
            "filter_value": value,
            **config,
        },
    }


MORE_THAN_TWO_SESSIONS = _leaf("num_sessions", "greater_than", 2)
MORE_THAN_FIVE_TRACES = _leaf("num_traces", "greater_than", 5)


@pytest.fixture
def users_view(monkeypatch):
    """The users population, recording what it was asked."""
    calls = []

    def count(project_id, filters, *, search=None, finish=False):
        calls.append({"filters": filters, "search": search, "finish": finish})
        return rows_service.Population(
            120, False, WINDOW_FROM, WINDOW_TO, list(filters)
        )

    monkeypatch.setattr(users_service, "count_users", count)
    return calls


# ─── Date option and label ───────────────────────────────────────────────────
@pytest.mark.django_db
def test_a_run_keeps_the_views_date_option_and_label_and_returns_them(
    auth_client, observe_project, yes_no, fake_view, started
):
    estimate = auth_client.post(
        ESTIMATE,
        {
            "project_id": str(observe_project.id),
            "unit": "sessions",
            "filters": WINDOW,
            "date_preset": "7D",
            "label": "Past 7D · 1 filter",
        },
        format="json",
    )
    assert estimate.status_code == status.HTTP_200_OK, estimate.json()
    assert estimate.json()["result"]["window"] == {
        "from": fake_view.start.isoformat(),
        "to": (fake_view.start.replace(day=15)).isoformat(),
    }

    started_run = _start(
        auth_client,
        observe_project,
        [yes_no],
        sample_size=5,
        date_preset="7D",
        label="Past 7D · 1 filter",
    ).json()["result"]
    assert (started_run["date_preset"], started_run["label"]) == (
        "7D",
        "Past 7D · 1 filter",
    )
    run = InstantEvalRun.objects.get(eval_task_id=started_run["task_id"])
    assert (run.date_preset, run.label, run.search) == (
        "7D",
        "Past 7D · 1 filter",
        "",
    )

    summary = auth_client.get(
        SUMMARY,
        {"project_id": str(observe_project.id), "eval_task_id": started_run["task_id"]},
    ).json()["result"]
    assert summary["scope"]["date_preset"] == "7D"
    assert summary["scope"]["label"] == "Past 7D · 1 filter"
    assert summary["scope"]["search"] is None

    history = auth_client.get(
        RUNS, {"project_id": str(observe_project.id), "page_size": 5}
    ).json()["result"]["runs"]
    listed = next(r for r in history if r["task_id"] == started_run["task_id"])
    assert (listed["date_preset"], listed["label"]) == ("7D", "Past 7D · 1 filter")
    assert listed["window"] == {
        "from": run.date_from.isoformat(),
        "to": run.date_to.isoformat(),
    }


@pytest.mark.django_db
def test_a_custom_window_and_no_label_are_kept_as_sent(
    auth_client, observe_project, yes_no, fake_view, started
):
    result = _start(
        auth_client, observe_project, [yes_no], sample_size=5, date_preset="Custom"
    ).json()["result"]
    run = InstantEvalRun.objects.get(eval_task_id=result["task_id"])
    assert (run.date_preset, run.label) == ("Custom", None)
    # A run started without them (an older page) has neither.
    older = _start(auth_client, observe_project, [yes_no], sample_size=5)
    run = InstantEvalRun.objects.get(eval_task_id=older.json()["result"]["task_id"])
    assert (run.date_preset, run.label) == (None, None)


@pytest.mark.django_db
def test_an_unknown_date_option_is_refused(
    auth_client, observe_project, yes_no, fake_view, started
):
    response = _start(
        auth_client, observe_project, [yes_no], sample_size=5, date_preset="24h"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ─── Users filters and search ────────────────────────────────────────────────
@pytest.mark.django_db
def test_a_user_question_honours_every_users_filter_and_the_search(
    auth_client, observe_project, yes_no, users_view, started
):
    filters = [*WINDOW, MORE_THAN_TWO_SESSIONS, _leaf("eval_score", "less_than", 50)]
    estimate = auth_client.post(
        ESTIMATE,
        {
            "project_id": str(observe_project.id),
            "unit": "users",
            "filters": filters,
            "search": " acme ",
            "sample_size": 50,
        },
        format="json",
    )
    assert estimate.status_code == status.HTTP_200_OK, estimate.json()
    result = estimate.json()["result"]
    assert result["population"] == 120
    assert result["rows_to_read"] == 50 * rows_service.SESSIONS_PER_USER
    asked = users_view[-1]
    assert {f["column_id"] for f in asked["filters"]} == {
        "created_at",
        "num_sessions",
        "eval_score",
    }
    assert asked["search"].strip() == "acme" and asked["finish"] is False

    response = _start(
        auth_client,
        observe_project,
        [yes_no],
        unit="users",
        filters=filters,
        search="acme",
        sample_size=10,
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    # A run counts every user before it starts.
    assert users_view[-1]["finish"] is True
    run = InstantEvalRun.objects.get(eval_task_id=response.json()["result"]["task_id"])
    assert run.search == "acme"
    assert {f["column_id"] for f in run.filters} >= {"num_sessions", "eval_score"}
    assert (run.date_from, run.date_to) == (WINDOW_FROM, WINDOW_TO)


@pytest.mark.django_db
def test_search_text_only_narrows_a_user_question(
    auth_client, observe_project, fake_view
):
    response = auth_client.post(
        ESTIMATE,
        {
            "project_id": str(observe_project.id),
            "unit": "sessions",
            "filters": WINDOW,
            "search": "acme",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "users" in str(response.json())


@pytest.mark.django_db
def test_a_users_field_the_users_view_cant_filter_by_is_refused(
    auth_client, observe_project, users_view
):
    response = auth_client.post(
        ESTIMATE,
        {
            "project_id": str(observe_project.id),
            "unit": "users",
            "filters": [
                *WINDOW,
                _leaf("active_users", "greater_than", 1, col_type="SYSTEM_METRIC"),
            ],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not users_view


@pytest.mark.django_db
def test_users_handed_over_keep_the_views_window(
    auth_client, observe_project, yes_no, started, monkeypatch
):
    users = [str(uuid.uuid4()) for _ in range(3)]
    sessions = {u: [f"{u[:8]}-s{i}" for i in range(2)] for u in users}
    read = []

    def user_sessions(project_id, user_ids, start, end):
        read.append((list(user_ids), start, end))
        return [s for u in user_ids for s in sessions[u]]

    monkeypatch.setattr(users_service, "user_sessions", user_sessions)
    monkeypatch.setattr(
        rows_service,
        "row_hashes",
        lambda ids: {str(i): _hash(str(i)) for i in ids},
    )
    monkeypatch.setattr(
        rows_service,
        "read_facets",
        lambda project_id, row_type, ids, **kw: {
            i: {
                "time": WINDOW_FROM,
                "end_user_id": next(u for u in users if i.startswith(u[:8])),
                "user_label": "",
                "model": "",
                "turns": None,
                "duration_ms": None,
                "error": False,
                "excerpt": "",
                "trace_id": None,
            }
            for i in ids
        },
    )
    response = _start(
        auth_client,
        observe_project,
        [yes_no],
        unit="users",
        filters=WINDOW,
        ids=users,
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    task = EvalTask.objects.get(id=response.json()["result"]["task_id"])
    run = InstantEvalRun.objects.get(eval_task=task)
    assert (run.date_from, run.date_to) == (WINDOW_FROM, WINDOW_TO)
    rows_service.resolve_instant_rows(task)
    assert read and all((s, e) == (WINDOW_FROM, WINDOW_TO) for _, s, e in read)


@pytest.mark.django_db
def test_users_handed_over_without_a_window_keep_the_lists_30_days(
    auth_client, observe_project, yes_no, started
):
    users = [str(uuid.uuid4()) for _ in range(3)]
    response = _start(
        auth_client, observe_project, [yes_no], unit="users", filters=[], ids=users
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    run = InstantEvalRun.objects.get(eval_task_id=response.json()["result"]["task_id"])
    # Their sessions are never read without a window.
    assert run.date_from is not None and run.date_to is not None
    assert run.date_to - run.date_from == timedelta(days=30)


# ─── Custom sample sizes ─────────────────────────────────────────────────────
def _estimate(auth_client, project, unit="sessions", **body):
    response = auth_client.post(
        ESTIMATE,
        {"project_id": str(project.id), "unit": unit, "filters": WINDOW, **body},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    return response.json()["result"]


@pytest.mark.django_db
def test_a_sample_larger_than_the_view_is_named_and_refused(
    auth_client, observe_project, yes_no, fake_view, started
):
    too_many = _estimate(auth_client, observe_project, sample_size=41)
    assert too_many["sample_error"] == {
        "error_code": "INSTANT_EVAL_SAMPLE_TOO_LARGE",
        "message": "There are only 40 sessions in this view; choose up to 40.",
        "sample_size": 40,
    }
    # The estimate still counts the view.
    assert too_many["population"] == 40
    assert (
        _estimate(auth_client, observe_project, sample_size=40)["sample_error"] is None
    )
    assert _estimate(auth_client, observe_project)["sample_error"] is None

    refused = _start(auth_client, observe_project, [yes_no], sample_size=41)
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert refused.json()["code"] == "instant_eval_sample_too_large"
    assert refused.json()["message"] == (
        "There are only 40 sessions in this view; choose up to 40."
    )
    assert not InstantEvalRun.objects.exists()
    every = _start(auth_client, observe_project, [yes_no], sample_size=40)
    assert every.status_code == status.HTTP_200_OK, every.json()
    assert every.json()["result"]["is_sample"] is False


@pytest.mark.django_db
@override_settings(INSTANT_EVAL_MAX_ROWS=30)
def test_a_sample_over_the_rows_one_run_reads_is_named_and_refused(
    auth_client, observe_project, yes_no, fake_view, started
):
    message = "One run reads at most 30 rows; choose up to 30."
    assert _estimate(auth_client, observe_project, sample_size=35)["sample_error"] == {
        "error_code": "INSTANT_EVAL_SAMPLE_TOO_LARGE",
        "message": message,
        "sample_size": 30,
    }
    # Past both limits, the rows are what holds it back.
    assert (
        _estimate(auth_client, observe_project, sample_size=99)["sample_error"][
            "message"
        ]
        == message
    )
    refused = _start(auth_client, observe_project, [yes_no], sample_size=35)
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert refused.json()["message"] == message
    assert (
        _start(auth_client, observe_project, [yes_no], sample_size=30).status_code
        == 200
    )


@pytest.mark.django_db
@override_settings(INSTANT_EVAL_MAX_ROWS=100)
def test_a_user_sample_is_in_users_and_its_rows_are_their_sessions(
    auth_client, observe_project, yes_no, users_view, started
):
    # 120 users, five sessions each: a run reads at most 20 of them.
    error = _estimate(auth_client, observe_project, unit="users", sample_size=25)[
        "sample_error"
    ]
    assert error == {
        "error_code": "INSTANT_EVAL_SAMPLE_TOO_LARGE",
        "message": "One run reads at most 100 rows (20 users at 5 sessions each); "
        "choose up to 20.",
        "sample_size": 20,
    }
    assert (
        _estimate(auth_client, observe_project, unit="users", sample_size=20)[
            "sample_error"
        ]
        is None
    )
    refused = _start(
        auth_client, observe_project, [yes_no], unit="users", sample_size=25
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert refused.json()["message"] == error["message"]


@pytest.mark.django_db
def test_a_user_sample_past_the_users_counted_so_far_waits_for_the_count(
    auth_client, observe_project, monkeypatch
):
    monkeypatch.setattr(
        users_service,
        "count_users",
        lambda *a, **k: rows_service.Population(30, True, WINDOW_FROM, WINDOW_TO, []),
    )
    result = _estimate(auth_client, observe_project, unit="users", sample_size=500)
    assert result["population_is_lower_bound"] is True
    assert result["sample_error"] is None


@pytest.mark.django_db
def test_a_sample_of_none_is_refused(auth_client, observe_project, fake_view):
    response = auth_client.post(
        ESTIMATE,
        {
            "project_id": str(observe_project.id),
            "unit": "sessions",
            "filters": WINDOW,
            "sample_size": 0,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Choose a sample of at least 1." in response.json()["message"]


def _read_more(auth_client, project, task_id, more):
    """Read ``more`` once the run has answered every row it holds, and
    answer the rows it adds."""
    _read_everything(EvalTask.objects.get(id=task_id))
    response = auth_client.post(
        CONTROL,
        {
            "project_id": str(project.id),
            "eval_task_id": task_id,
            "action": "read_more",
            "more": more,
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    task = EvalTask.objects.get(id=task_id)
    resolve_desired_rows(task)
    _read_everything(task)
    return task


def _sample(auth_client, project, task_id):
    task = EvalTask.objects.get(id=task_id)
    return _summary(auth_client, project, task)["sample"]


@pytest.mark.django_db
def test_read_more_after_a_custom_sample_reads_the_same_size_again(
    auth_client, observe_project, yes_no, fake_view, started
):
    task_id = _start(auth_client, observe_project, [yes_no], sample_size=7).json()[
        "result"
    ]["task_id"]
    resolve_desired_rows(EvalTask.objects.get(id=task_id))
    EvalTask.objects.filter(id=task_id).update(status=EvalTaskStatus.COMPLETED)
    sample = _sample(auth_client, observe_project, task_id)
    assert (sample["requested"], sample["next_size"]) == (7, 7)
    for requested in (14, 21, 28, 35):
        _read_more(auth_client, observe_project, task_id, sample["next_size"])
        sample = _sample(auth_client, observe_project, task_id)
        assert sample["requested"] == requested
        # Seven more each time, then the five the view has left.
        assert sample["next_size"] == (7 if requested < 35 else 5)
        assert sample["can_extend"] is True
    _read_more(auth_client, observe_project, task_id, sample["next_size"])
    sample = _sample(auth_client, observe_project, task_id)
    assert (sample["requested"], sample["is_sample"], sample["next_size"]) == (
        40,
        False,
        0,
    )
    assert sample["can_extend"] is False


@pytest.mark.django_db
@override_settings(INSTANT_EVAL_MAX_ROWS=20)
def test_read_more_stops_at_the_rows_one_run_reads(
    auth_client, observe_project, yes_no, fake_view, started
):
    task_id = _start(auth_client, observe_project, [yes_no], sample_size=7).json()[
        "result"
    ]["task_id"]
    resolve_desired_rows(EvalTask.objects.get(id=task_id))
    _read_more(auth_client, observe_project, task_id, 7)
    sample = _sample(auth_client, observe_project, task_id)
    # Six more reach the twenty rows a run reads.
    assert (sample["requested"], sample["next_size"]) == (14, 6)
    _read_more(auth_client, observe_project, task_id, 6)
    sample = _sample(auth_client, observe_project, task_id)
    assert (sample["requested"], sample["is_sample"]) == (20, True)
    assert (sample["next_size"], sample["can_extend"]) == (0, False)


# ─── Control, History and scope checks from QA ───────────────────────────────
def _control(auth_client, project, task_id, action, **body):
    return auth_client.post(
        CONTROL,
        {
            "project_id": str(project.id),
            "eval_task_id": str(task_id),
            "action": action,
            **body,
        },
        format="json",
    )


def _sampled_run(auth_client, project, yes_no, size=7):
    task_id = _start(auth_client, project, [yes_no], sample_size=size).json()["result"][
        "task_id"
    ]
    task = EvalTask.objects.get(id=task_id)
    ids = list(resolve_desired_rows(task).matched_ids)
    return task, ids


@pytest.mark.django_db
def test_reading_more_of_a_run_still_answering_is_refused_before_any_work(
    auth_client, observe_project, yes_no, fake_view, started
):
    task, ids = _sampled_run(auth_client, observe_project, yes_no)
    starts = len(started)
    response = _control(auth_client, observe_project, task.id, "read_more", more=7)
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["code"] == "instant_eval_still_answering"
    assert response.json()["result"]["message"] == (
        "This run is still answering; read more once it finishes."
    )
    # Paused, with an answer still being read: still answering.
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.PAUSED)
    entry = _answer(task, yes_no, ids[0], pending=True)
    entry.status = EvalEntryStatus.RUNNING
    entry.save()
    response = _control(auth_client, observe_project, task.id, "read_more", more=7)
    assert response.json()["code"] == "instant_eval_still_answering"
    run = InstantEvalRun.objects.get(eval_task=task)
    assert run.rows_requested == 7 and len(started) == starts
    # Done answering but paused with rows never read: restarting would read
    # all of them, past the 7 rows "read more" checks, so it resumes first.
    EvalLogger.objects.filter(id=entry.id).update(status=EvalEntryStatus.COMPLETED)
    record_answer(entry.id)
    response = _control(auth_client, observe_project, task.id, "read_more", more=7)
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["code"] == "instant_eval_paused"
    # Finished with rows never read, it stalled: resumed first too.
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.COMPLETED)
    response = _control(auth_client, observe_project, task.id, "read_more", more=7)
    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["result"]["message"] == (
        "This run stopped before reading every row. Resume it to read the rest "
        "first."
    )
    run = InstantEvalRun.objects.get(eval_task=task)
    assert run.rows_requested == 7 and len(started) == starts
    # Once every row is answered, it reads more.
    _read_everything(task)
    response = _control(auth_client, observe_project, task.id, "read_more", more=7)
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert response.json()["result"]["status"] == "pending"


@pytest.mark.django_db
def test_a_cancelled_run_is_not_resumed_retried_or_read_further(
    auth_client, observe_project, yes_no, fake_view, started
):
    task, _ = _sampled_run(auth_client, observe_project, yes_no)
    assert _control(auth_client, observe_project, task.id, "cancel").status_code == 200
    for action, body in [("resume", {}), ("retry_failed", {}), ("read_more", {})]:
        response = _control(auth_client, observe_project, task.id, action, **body)
        assert response.status_code == status.HTTP_409_CONFLICT, action
        assert response.json()["code"] == "instant_eval_cancelled"
        assert response.json()["result"]["message"] == (
            "This run was cancelled. Use Run again to ask these questions again."
        )
    assert InstantEvalRun.objects.get(eval_task=task).cancelled_at is not None


@pytest.mark.django_db
def test_a_run_that_cant_start_again_says_so_instead_of_reporting_success(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    task, _ = _sampled_run(auth_client, observe_project, yes_no)
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.PAUSED)

    def unavailable(task, **kwargs):
        raise RuntimeError("Temporal is unavailable")

    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync", unavailable
    )
    response = _control(auth_client, observe_project, task.id, "resume")
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["code"] == "instant_eval_restart_failed"
    assert "Resume it to carry on" in response.json()["message"]


@pytest.mark.django_db
def test_history_reports_a_cancelled_run_and_its_answers(
    auth_client, observe_project, yes_no, fake_view, started
):
    task, ids = _sampled_run(auth_client, observe_project, yes_no)
    logs = EvalLogger.objects.bulk_create(
        [_answer(task, yes_no, sid, "Yes") for sid in ids[:3]]
    )
    for entry in logs:
        record_answer(entry.id)
    assert _control(auth_client, observe_project, task.id, "cancel").status_code == 200
    runs = auth_client.get(
        RUNS, {"project_id": str(observe_project.id), "task_id": str(task.id)}
    ).json()["result"]["runs"]
    assert len(runs) == 1
    assert (runs[0]["status"], runs[0]["answered"], runs[0]["pending"]) == (
        "cancelled",
        3,
        0,
    )
    summary = _summary(auth_client, observe_project, task)
    assert summary["status"] == "cancelled" and summary["answered"] == 3


@pytest.mark.django_db
def test_history_lists_a_run_under_its_own_unit(
    auth_client, observe_project, yes_no, users_view, started
):
    # A question first asked of sessions, asked here of users.
    task_id = _start(
        auth_client, observe_project, [yes_no], unit="users", sample_size=5
    ).json()["result"]["task_id"]
    runs = auth_client.get(
        RUNS, {"project_id": str(observe_project.id), "unit": "users"}
    ).json()["result"]["runs"]
    assert [(r["task_id"], r["unit"]) for r in runs] == [(task_id, "users")]


@pytest.mark.django_db
def test_a_users_filter_on_a_field_the_users_view_cant_filter_is_named(
    auth_client, observe_project, users_view
):
    def estimate(*filters):
        return auth_client.post(
            ESTIMATE,
            {
                "project_id": str(observe_project.id),
                "unit": "users",
                "filters": [*WINDOW, *filters],
            },
            format="json",
        )

    def system(column):
        return _leaf(column, "equals", "x", "text", col_type="SYSTEM_METRIC")

    refused = estimate(system("trace_id"), system("session_id"))
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        'The Users view can\'t filter on "trace_id", "session_id".'
        in refused.json()["message"]
    )
    assert not users_view
    # Span dimensions it reads from the user's spans, per-user columns and
    # custom attributes are the Users view's own filters.
    for leaf in [
        system("status"),
        system("model"),
        MORE_THAN_TWO_SESSIONS,
        _leaf("plan", "equals", "pro", "text", col_type="SPAN_ATTRIBUTE"),
    ]:
        assert estimate(leaf).status_code == status.HTTP_200_OK, leaf


def _window(start, end):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start, end],
            },
        }
    ]


@pytest.mark.django_db
def test_a_window_that_ends_before_it_starts_or_cant_be_read_is_refused(
    auth_client, observe_project, yes_no, fake_view, started
):
    cases = [
        (
            _window("2026-10-15T00:00:00Z", "2026-10-01T00:00:00Z"),
            "The date range ends before it starts.",
        ),
        (
            _window("last tuesday", "2026-10-15T00:00:00Z"),
            "The date range has a date that can't be read.",
        ),
    ]
    for filters, message in cases:
        estimate = auth_client.post(
            ESTIMATE,
            {
                "project_id": str(observe_project.id),
                "unit": "sessions",
                "filters": filters,
            },
            format="json",
        )
        assert estimate.status_code == status.HTTP_400_BAD_REQUEST, filters
        assert message in estimate.json()["message"]
        start = _start(
            auth_client, observe_project, [yes_no], filters=filters, sample_size=5
        )
        assert start.status_code == status.HTTP_400_BAD_REQUEST
        assert message in start.json()["message"]
    assert not InstantEvalRun.objects.exists()


# ─── The users population against a stand-in ClickHouse ──────────────────────
class FakeUsers:
    """The Users view's candidate users, served the way the walk reads them:
    in hash order, after a cursor, a page at a time. A statement "takes"
    ``page_ms`` (or ``count_ms``), so a deadline shorter than that stops it as
    ClickHouse's ``max_execution_time`` would; a batch takes ``batch_s`` to
    decide, and one of more than ``fits`` users runs out of time or memory."""

    def __init__(self, n, keep):
        self.ids = [str(uuid.UUID(int=i + 1)) for i in range(n)]
        self.keep = keep
        self.decided = 0
        self.page_ms = self.count_ms = 0
        self.batch_s = 0.0
        self.fits = None

    def read(self, sql, params, deadline=None):
        takes = self.count_ms if "count() AS total" in sql else self.page_ms
        if deadline is not None and takes > deadline.total_ms:
            raise ReadDeadlineExceeded(
                "ClickHouse statement exceeded its execution cap"
            )
        if "count() AS total" in sql:
            return [{"total": len([i for i in self.ids if self.keep(i)])}]
        ordered = sorted(self.ids, key=lambda i: (_hash(i), i))
        if params.get("ie_after") is not None and "ie_after" in sql:
            ordered = [i for i in ordered if _hash(i) > params["ie_after"]]
        if sql.lstrip().startswith("SELECT rid, h"):
            # The list's statement decides these filters itself.
            page = [i for i in ordered if self.keep(i)][: params["ie_n"]]
            return [{"rid": i, "h": _hash(i)} for i in page]
        page = ordered[: params["ie_n"]]
        return [{"end_user_id": i, "ie_h": _hash(i)} for i in page]

    def decide(self, rows, deadline=None):
        time.sleep(self.batch_s)
        if self.fits is not None and len(rows) > self.fits:
            raise ReadDeadlineExceeded(
                "ClickHouse statement exceeded its execution cap"
            )
        self.decided += len(rows)
        return {r["end_user_id"] for r in rows if self.keep(r["end_user_id"])}


@pytest.fixture
def fake_users(monkeypatch):
    def install(n, keep, *, batch=4, page=8):
        users = FakeUsers(n, keep)
        monkeypatch.setattr(users_service, "_read", users.read)
        monkeypatch.setattr(
            users_service._UsersView,
            "decide",
            lambda view, rows, deadline=None: users.decide(rows, deadline),
        )
        monkeypatch.setattr(
            users_service._UsersView,
            "statement",
            lambda view: ("SELECT end_user_id FROM candidate_users", {}),
        )
        monkeypatch.setattr(users_service, "WALK_BATCH", batch)
        monkeypatch.setattr(users_service, "BUDGET_BATCH", batch)
        monkeypatch.setattr(users_service, "MIN_BATCH", 1)
        monkeypatch.setattr(users_service, "CANDIDATE_PAGE", page)
        return users

    return install


def _even(user_id: str) -> bool:
    return int(uuid.UUID(user_id)) % 2 == 0


def _until_counted(project_id, filters, *, calls=40):
    """Estimate after estimate, as the page asks while the count is a lower
    bound: each call's count and how long it took."""
    seen = []
    for _ in range(calls):
        started = time.monotonic()
        population = users_service.count_users(project_id, filters)
        seen.append((population.count, time.monotonic() - started))
        if not population.lower_bound:
            return seen
    raise AssertionError(f"still counting after {calls} estimates: {seen}")


@pytest.mark.django_db
def test_users_filters_the_list_decides_in_sql_are_counted_in_one_statement(
    observe_project, fake_users
):
    users = fake_users(30, _even)
    view = users_service._UsersView(
        str(observe_project.id), [*WINDOW, MORE_THAN_FIVE_TRACES], None
    )
    assert view.in_sql is True
    population = users_service.count_users(
        str(observe_project.id), [*WINDOW, MORE_THAN_FIVE_TRACES]
    )
    assert (population.count, population.lower_bound) == (15, False)
    assert users.decided == 0
    picked = users_service.pick_users(
        str(observe_project.id), [*WINDOW, MORE_THAN_FIVE_TRACES], n=4, after=None
    )
    expected = sorted((i for i in users.ids if _even(i)), key=lambda i: (_hash(i), i))
    assert [rid for rid, _ in picked] == expected[:4]


@pytest.mark.django_db
def test_users_filters_decided_per_user_are_walked_in_hash_order(
    observe_project, fake_users
):
    users = fake_users(30, _even)
    filters = [*WINDOW, MORE_THAN_TWO_SESSIONS]
    assert (
        users_service._UsersView(str(observe_project.id), filters, None).in_sql is False
    )
    population = users_service.count_users(str(observe_project.id), filters)
    assert (population.count, population.lower_bound) == (15, False)
    assert users.decided == 30

    expected = sorted((i for i in users.ids if _even(i)), key=lambda i: (_hash(i), i))
    first = users_service.pick_users(str(observe_project.id), filters, n=5, after=None)
    assert [rid for rid, _ in first] == expected[:5]
    # The next pick continues after the last user picked.
    after = max(h for _, h in first)
    more = users_service.pick_users(str(observe_project.id), filters, n=5, after=after)
    assert [rid for rid, _ in more] == expected[5:10]


@pytest.mark.django_db
def test_an_estimate_stops_at_its_budget_and_the_next_one_carries_on(
    observe_project, fake_users, monkeypatch
):
    users = fake_users(30, _even)
    users.batch_s = 0.05
    monkeypatch.setattr(users_service, "estimate_budget_s", lambda: 0.12)
    seen = _until_counted(str(observe_project.id), [*WINDOW, MORE_THAN_TWO_SESSIONS])
    counts = [count for count, _ in seen]
    assert counts[-1] == 15 and counts == sorted(counts) and len(seen) > 1
    # Checked between batches: a call overruns its budget by one batch at most.
    assert max(took for _, took in seen) < 0.12 + 0.05 + 0.5
    # Each user was decided once across the calls, and the count is kept.
    assert users.decided == 30
    again = users_service.count_users(
        str(observe_project.id), [*WINDOW, MORE_THAN_TWO_SESSIONS]
    )
    assert (again.count, again.lower_bound, users.decided) == (15, False, 30)


@pytest.mark.django_db
def test_a_batch_that_runs_out_of_time_or_memory_is_decided_in_smaller_ones(
    observe_project, fake_users
):
    users = fake_users(30, _even)
    # A batch of more than two users never fits.
    users.fits = 2
    seen = _until_counted(str(observe_project.id), [*WINDOW, MORE_THAN_TWO_SESSIONS])
    assert seen[0] == (0, seen[0][1])
    assert seen[-1][0] == 15 and users.decided == 30


@pytest.mark.django_db
@pytest.mark.parametrize("filters", [[MORE_THAN_TWO_SESSIONS], [MORE_THAN_FIVE_TRACES]])
def test_a_statement_too_slow_for_an_estimate_is_read_off_the_request(
    observe_project, fake_users, filters
):
    from django.core.cache import cache

    users = fake_users(30, _even)
    # The view's candidate users (or its count) take 5 s to read.
    users.page_ms = users.count_ms = 5000
    project_id, filters = str(observe_project.id), [*WINDOW, *filters]
    first = users_service.count_users(project_id, filters)
    assert (first.count, first.lower_bound) == (0, True)
    key = users_service._UsersView(project_id, filters, None).cache_key()

    def reading():
        return any(k.endswith(":reading") and key in k for k in cache._cache)

    # Each estimate starts from what was read off the request before it.
    for _ in range(10):
        started = time.monotonic()
        while reading():
            assert time.monotonic() - started < 5, "the read off the request hung"
            time.sleep(0.01)
        population = users_service.count_users(project_id, filters)
        if not population.lower_bound:
            break
    assert (population.count, population.lower_bound) == (15, False)


@pytest.mark.django_db
def test_a_users_count_finished_for_a_run_reads_every_user(observe_project, fake_users):
    fake_users(30, _even)
    filters = [*WINDOW, MORE_THAN_TWO_SESSIONS]
    population = users_service.count_users(
        str(observe_project.id), filters, finish=True
    )
    assert (population.count, population.lower_bound) == (15, False)


@pytest.mark.django_db
def test_a_run_that_cant_finish_counting_says_so_and_starting_again_carries_on(
    auth_client, observe_project, yes_no, started, fake_users, monkeypatch
):
    users = fake_users(30, _even)
    users.batch_s = 0.05
    monkeypatch.setattr(users_service, "run_budget_s", lambda: 0.12)
    body = {"unit": "users", "filters": [*WINDOW, MORE_THAN_TWO_SESSIONS]}
    response = _start(auth_client, observe_project, [yes_no], sample_size=5, **body)
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["code"] == "users_still_counting"
    assert "so far" in response.json()["message"]
    assert not InstantEvalRun.objects.exists()
    # Each attempt carries on from the last.
    for _ in range(20):
        response = _start(auth_client, observe_project, [yes_no], sample_size=5, **body)
        if response.status_code == status.HTTP_200_OK:
            break
        assert response.json()["code"] == "users_still_counting"
    assert response.status_code == status.HTTP_200_OK, response.json()
    run = InstantEvalRun.objects.get(eval_task_id=response.json()["result"]["task_id"])
    assert (run.population, run.population_is_lower_bound) == (15, False)
    assert users.decided == 30


@pytest.mark.django_db
def test_users_sessions_are_each_users_latest_inside_the_window(
    observe_project, monkeypatch
):
    from types import SimpleNamespace

    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    asked = {}

    def root(user, session, day):
        return SimpleNamespace(
            end_user_id=user,
            trace_session_id=session,
            start_time=datetime(2026, 10, day, tzinfo=UTC),
        )

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def roots_by_end_user_ids(self, ids, *, project_id, since, until):
            asked.update(ids=ids, since=since, until=until)
            return [
                *(root(user_a, f"a{day}", day) for day in range(1, 9)),
                # A second turn of a session doesn't make it newer.
                root(user_a, "a1", 9),
                root(user_b, "b1", 3),
                root(user_b, None, 4),
            ]

    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: Reader())
    sessions = users_service.user_sessions(
        str(observe_project.id), [user_a, user_b], WINDOW_FROM, WINDOW_TO
    )
    assert sessions == ["a8", "a7", "a6", "a5", "a4", "b1"]
    assert (asked["since"], asked["until"]) == (WINDOW_FROM, WINDOW_TO)
