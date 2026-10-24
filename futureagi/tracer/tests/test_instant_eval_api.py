"""Instant eval on Sessions and Users: History's runs and their answer
split, and the session transcript input."""

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.utils.eval import render_session_transcript
from tracer.views import instant_eval

RUNS = "/tracer/instant-eval/runs/"


@pytest.fixture
def question(observe_project, eval_template):
    return CustomEvalConfig.objects.create(
        name="Did the customer get what they came for?",
        project=observe_project,
        eval_template=eval_template,
        config={
            "instant_eval": {"question": "q", "reads": "transcript", "unit": "sessions"}
        },
        mapping={"transcript": "transcript"},
        filters={},
    )


@pytest.fixture
def session_task(observe_project, question):
    task = EvalTask.objects.create(
        project=observe_project,
        name="Instant eval · q",
        filters={},
        sampling_rate=100,
        spans_limit=10,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.COMPLETED,
        row_type=RowType.SESSIONS,
    )
    task.evals.add(question)
    return task


def log(task, config, session_id, label=None, *, error=False, confidence=0.9):
    return EvalLogger(
        target_type=EvalTargetType.SESSION,
        trace_session_id=session_id,
        eval_task_id=str(task.id),
        custom_eval_config=config,
        output_str_list=[label] if label else [],
        error=error,
        output_metadata={"jev": {"confidence": confidence}},
    )


@pytest.fixture
def sessions():
    """Three sessions a run names."""
    return [str(uuid.uuid4()) for _ in range(3)]


def test_transcript_renders_ordered_turns_and_keeps_the_ends_when_long():
    traces = [
        SimpleNamespace(
            input=(
                '[{"role": "user", "content": "where is order 1?"}]'
                if i == 0
                else f"q{i}"
            ),
            output=f"a{i}",
        )
        for i in range(3)
    ]
    text = render_session_transcript(traces)
    assert text.startswith("[Turn 1] User: where is order 1?\nAssistant: a0")
    assert "[Turn 3] User: q2\nAssistant: a2" in text

    long = [SimpleNamespace(input="x" * 3900, output="y" * 3900) for _ in range(30)]
    clipped = render_session_transcript(long)
    assert len(clipped) <= 60000 + 200
    assert clipped.startswith("[Turn 1]") and "[Turn 30]" in clipped
    assert "turns omitted" in clipped


# ─── History: one run by id, its pending rows ────────────────────────────────


@pytest.fixture
def named_task(observe_project, question, sessions):
    """A run that named its three sessions outright, as the Ask page does."""
    task = EvalTask.objects.create(
        project=observe_project,
        name="Instant eval · q",
        filters={"project_id": str(observe_project.id), "session_id": sessions},
        sampling_rate=100,
        spans_limit=len(sessions),
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.RUNNING,
        row_type=RowType.SESSIONS,
    )
    task.evals.add(question)
    return task


@pytest.mark.django_db
def test_runs_opens_one_run_by_id_with_its_pending_rows(
    auth_client, observe_project, named_task, session_task, question, sessions
):
    EvalLogger.objects.bulk_create([log(named_task, question, sessions[0], "Yes")])
    runs = auth_client.get(
        RUNS,
        {
            "project_id": str(observe_project.id),
            "task_id": str(named_task.id),
        },
    ).json()["result"]["runs"]
    assert [r["task_id"] for r in runs] == [str(named_task.id)]
    # Named rows without a log yet are pending; it isn't finished.
    assert (runs[0]["rows"], runs[0]["answered"], runs[0]["pending"]) == (3, 1, 2)
    assert runs[0]["finished_at"] is None

    EvalLogger.objects.bulk_create(
        [
            log(named_task, question, sessions[1], "No"),
            log(named_task, question, sessions[2], "No"),
        ]
    )
    done = auth_client.get(
        RUNS,
        {
            "project_id": str(observe_project.id),
            "task_id": str(named_task.id),
        },
    ).json()["result"]["runs"][0]
    assert done["pending"] == 0
    assert done["finished_at"]


@pytest.mark.django_db
def test_history_leaves_out_a_keep_answering_task(
    auth_client, observe_project, session_task, question
):
    """A "Keep answering" task never finishes: listed, it would keep History
    polling and cut its pages short."""
    live = EvalTask.objects.create(
        project=observe_project,
        name="Instant eval (live) · q",
        filters={"project_id": str(observe_project.id)},
        sampling_rate=100,
        spans_limit=10,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.RUNNING,
        row_type=RowType.SESSIONS,
    )
    live.evals.add(question)
    EvalLogger.objects.bulk_create([log(live, question, str(uuid.uuid4()), "Yes")])
    project = str(observe_project.id)
    page = auth_client.get(RUNS, {"project_id": project, "page_size": 1}).json()[
        "result"
    ]
    # The newest task is the live one; the page holds the run before it.
    assert [r["task_id"] for r in page["runs"]] == [str(session_task.id)]
    assert page["has_more"] is False
    assert (
        auth_client.get(RUNS, {"project_id": project, "task_id": str(live.id)}).json()[
            "result"
        ]["runs"]
        == []
    )
    # A task from before run types were recorded is still a run.
    EvalTask.objects.filter(id=session_task.id).update(run_type=None)
    runs = auth_client.get(RUNS, {"project_id": project}).json()["result"]["runs"]
    assert [r["task_id"] for r in runs] == [str(session_task.id)]


class _FakeCursor:
    """Every run on the page stored three rows."""

    def __init__(self):
        self.tasks = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params):
        self.tasks = params["tasks"]

    def fetchall(self):
        return [(tid, 3) for tid in self.tasks]


def test_a_finished_runs_history_line_is_kept_a_month(monkeypatch):
    kept = {}

    class Cache:
        def get_many(self, keys):
            return {}

        def set(self, key, value, timeout):
            kept[key] = timeout

    monkeypatch.setattr(instant_eval, "cache", Cache())
    monkeypatch.setattr("django.db.connection", SimpleNamespace(cursor=_FakeCursor))
    monkeypatch.setattr(
        instant_eval,
        "_answer_tally",
        lambda ids, out: [out.setdefault(i, [0, 3, None, {}, 0]) for i in ids],
    )
    stamp = datetime(2026, 10, 20, 9)
    done = SimpleNamespace(id="t-done", status="completed", updated_at=stamp)
    live = SimpleNamespace(id="t-live", status="running", updated_at=stamp)
    run = SimpleNamespace(
        answers_version=2, cancelled_at=None, answers_synced=True, unit="traces"
    )
    tally = instant_eval._history_tally([(done, run, ["c1"]), (live, run, ["c1"])])
    assert set(tally) == {"t-done", "t-live"}
    # Only the finished run is kept, and not for good.
    assert kept == {
        f"ie-history:v2:t-done:2:{stamp.timestamp()}": instant_eval.HISTORY_TALLY_TTL
    }
    assert instant_eval.HISTORY_TALLY_TTL == 30 * 24 * 60 * 60
