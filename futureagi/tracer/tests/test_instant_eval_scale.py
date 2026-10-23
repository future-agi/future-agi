"""Instant eval at scale: server-side populations, hash-ordered samples, the
limits, and the summary, rows and export endpoints aggregating in SQL."""

import csv
import hashlib
import io
import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.test import override_settings
from rest_framework import status

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.instant_eval import InstantEvalRow, InstantEvalRun
from tracer.models.observation_span import EvalEntryStatus, EvalLogger, EvalTargetType
from tracer.selectors.eval_tasks.row_resolver import resolve_desired_rows
from tracer.services import instant_eval_rows as rows_service
from tracer.services import instant_eval_summary as summary_service
from tracer.services.instant_eval_answers import record_answer
from tracer.views import instant_eval_scale

ESTIMATE = "/tracer/instant-eval/estimate/"
START = "/tracer/instant-eval/start/"
SUMMARY = "/tracer/instant-eval/summary/"
ROWS = "/tracer/instant-eval/rows/"
EXPORT = "/tracer/instant-eval/export/"
CONTROL = "/tracer/instant-eval/control/"
WINDOW = [
    {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": ["2026-10-01T00:00:00Z", "2026-10-15T00:00:00Z"],
        },
    }
]


def _hash(row_id: str) -> int:
    # Any stable 64-bit hash stands in for ClickHouse's cityHash64.
    return int.from_bytes(hashlib.sha256(row_id.encode()).digest()[:8], "big")


class FakeView:
    """A view of ``n`` sessions, read the way ClickHouse would: in hash order,
    after a cursor."""

    def __init__(self, n, *, start=datetime(2026, 10, 2, tzinfo=UTC)):
        self.ids = [str(uuid.UUID(int=i + 1)) for i in range(n)]
        self.start = start
        self.picks = []

    def count(self, project_id, unit, filters):
        return rows_service.Population(
            len(self.ids), False, self.start, self.start + timedelta(days=13), filters
        )

    def pick(self, project_id, unit, filters, *, n, after, **_):
        self.picks.append((n, after))
        ordered = sorted(((i, _hash(i)) for i in self.ids), key=lambda x: (x[1], x[0]))
        if after is not None:
            ordered = [x for x in ordered if x[1] > after]
        return ordered[:n]

    def facets(self, project_id, row_type, ids, *, start=None, end=None, **_):
        out = {}
        for i in ids:
            k = int(uuid.UUID(i))
            out[i] = {
                "time": self.start + timedelta(hours=k),
                "end_user_id": str(uuid.UUID(int=1000 + k % 3)),
                "user_label": f"cust_{k % 3}",
                "model": "gpt-4.1-mini" if k % 2 else "gpt-4.1",
                "turns": k % 8 or None,
                "duration_ms": 1000 * k,
                "error": k % 5 == 0,
                "excerpt": f"message {k}",
                "trace_id": None,
            }
        return out


@pytest.fixture(autouse=True)
def instant_eval_enabled(monkeypatch):
    """Instant eval is Enterprise-only: these tests run it as a licensed
    install does. Every other capability keeps its real decision."""
    from tfc.capabilities import service as capability_service
    from tfc.capabilities.service import CapabilityDecision

    check = capability_service.check
    monkeypatch.setattr(capability_service, "_configured", True)
    monkeypatch.setattr(
        capability_service,
        "check",
        lambda feature_id, **kw: (
            CapabilityDecision(allowed=True, feature_id=feature_id)
            if feature_id == "instant_eval"
            else check(feature_id, **kw)
        ),
    )


# What Jev charges an answer on Cloud: its 0.0005 eval fee at a 1.2 markup.
CLOUD_CREDITS_PER_ANSWER = 0.06
# The helper itself, for the tests of where it comes from.
BILLED_RATE = instant_eval_scale.credits_per_answer


@pytest.fixture(autouse=True)
def billed(monkeypatch):
    """Instant eval billed as Cloud bills it. The tests of other setups, and
    of where the rate comes from, put the real helper back."""
    monkeypatch.setattr(
        instant_eval_scale, "credits_per_answer", lambda: CLOUD_CREDITS_PER_ANSWER
    )


@pytest.fixture
def fake_view(monkeypatch):
    view = FakeView(40)
    monkeypatch.setattr(rows_service, "count_population", view.count)
    monkeypatch.setattr(rows_service, "pick_rows", view.pick)
    monkeypatch.setattr(rows_service, "read_facets", view.facets)
    return view


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.start_eval_task_workflow_sync",
        lambda task, **kw: calls.append((str(task.id), kw)) or "wf",
    )
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.signal_pause_eval_task_workflow",
        lambda task_id: True,
    )
    # Tests run inside one transaction, so nothing ever commits.
    monkeypatch.setattr(
        instant_eval_scale.transaction, "on_commit", lambda fn, *a, **k: fn()
    )
    return calls


def _question(project, template, text, answer_type="yes_no", labels=None):
    labels = labels or (["Yes", "No"] if answer_type == "yes_no" else [])
    return CustomEvalConfig.objects.create(
        name=text,
        project=project,
        eval_template=template,
        config={
            "instant_eval": {
                "question": text,
                "answer_type": answer_type,
                "labels": labels,
                "reads": "transcript",
                "unit": "sessions",
            }
        },
        mapping={"transcript": "transcript"},
        filters={},
    )


@pytest.fixture
def yes_no(observe_project, eval_template):
    return _question(observe_project, eval_template, "Did the agent resolve it?")


@pytest.fixture
def category(observe_project, eval_template):
    return _question(
        observe_project,
        eval_template,
        "What did the customer come for?",
        "category",
        ["Order status", "Return or refund", "Something else"],
    )


def _start(auth_client, project, configs, **body):
    return auth_client.post(
        START,
        {
            "project_id": str(project.id),
            "unit": "sessions",
            "filters": WINDOW,
            "config_ids": [str(c.id) for c in configs],
            **body,
        },
        format="json",
    )


def _answer(
    task, config, session_id, label=None, *, error=False, confidence=0.9, pending=False
):
    return EvalLogger(
        target_type=EvalTargetType.SESSION,
        trace_session_id=session_id,
        eval_task_id=str(task.id),
        custom_eval_config=config,
        output_str_list=[label] if label else [],
        error=error,
        status=(
            EvalEntryStatus.PENDING
            if pending
            else EvalEntryStatus.ERRORED
            if error
            else EvalEntryStatus.COMPLETED
        ),
        output_metadata={"jev": {"confidence": confidence}},
        eval_explanation=f"because {label}",
    )


# ─── Sample, limits, concurrency ─────────────────────────────────────────────
@pytest.mark.django_db
def test_estimate_offers_a_sample_and_the_whole_view(
    auth_client, observe_project, monkeypatch
):
    view = FakeView(104_233)
    monkeypatch.setattr(rows_service, "count_population", view.count)
    result = auth_client.post(
        ESTIMATE,
        {
            "project_id": str(observe_project.id),
            "unit": "sessions",
            "filters": WINDOW,
            "sample_size": 1000,
            "questions": 2,
        },
        format="json",
    ).json()["result"]
    assert result["population"] == 104_233
    assert result["rows_to_read"] == 1000
    assert result["credits_estimate"] == pytest.approx(1000 * 2 * 0.06)
    assert result["default_sample_size"] == 1000
    every = result["choices"][-1]
    assert every["sample_size"] is None and every["rows_to_read"] == 104_233
    assert every["needs_confirm"] is True and every["allowed"] is False
    assert [c["sample_size"] for c in result["choices"][:-1]] == [500, 1000, 2000, 5000]


@pytest.mark.django_db
def test_a_sample_reads_the_same_rows_every_time_and_extends_with_the_next(
    auth_client, observe_project, yes_no, fake_view, started
):
    first = _start(auth_client, observe_project, [yes_no], sample_size=10).json()[
        "result"
    ]
    again = _start(auth_client, observe_project, [yes_no], sample_size=10).json()[
        "result"
    ]
    tasks = [EvalTask.objects.get(id=r["task_id"]) for r in (first, again)]
    rows = [resolve_desired_rows(t).matched_ids for t in tasks]
    assert len(rows[0]) == 10 and rows[0] == rows[1]
    by_hash = sorted(fake_view.ids, key=lambda i: (_hash(i), i))
    assert list(rows[0]) == by_hash[:10]
    # Resolving again (a resume) picks nothing new.
    assert resolve_desired_rows(tasks[0]).matched_ids == rows[0]

    EvalTask.objects.filter(id=tasks[0].id).update(status=EvalTaskStatus.COMPLETED)
    more = auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": first["task_id"],
            "action": "read_more",
            "more": 5,
        },
        format="json",
    )
    assert more.status_code == status.HTTP_200_OK, more.json()
    extended = resolve_desired_rows(EvalTask.objects.get(id=tasks[0].id)).matched_ids
    assert list(extended) == by_hash[:15]
    assert fake_view.picks[-1] == (5, _hash(by_hash[9]))
    ranks = list(
        InstantEvalRow.objects.filter(task_id=tasks[0].id)
        .order_by("sample_rank")
        .values_list("sample_rank", flat=True)
    )
    assert ranks == list(range(15))


@pytest.mark.django_db
@override_settings(INSTANT_EVAL_CONFIRM_ROWS=20, INSTANT_EVAL_MAX_ROWS=30)
def test_big_runs_need_a_confirm_and_runs_over_the_cap_are_refused(
    auth_client, observe_project, yes_no, fake_view, started
):
    refused = _start(auth_client, observe_project, [yes_no], sample_size=None)
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert "at most 30 rows" in refused.json()["message"]

    asked = _start(auth_client, observe_project, [yes_no], sample_size=25)
    assert asked.status_code == status.HTTP_409_CONFLICT
    body = asked.json()
    assert body["result"]["error_code"] == "INSTANT_EVAL_CONFIRM"
    assert body["result"]["rows_to_read"] == 25
    assert body["result"]["message"] == (
        "Reading 25 rows costs about 2 credits. Confirm to start."
    )
    assert not EvalTask.objects.filter(project=observe_project).exists()

    confirmed = _start(
        auth_client, observe_project, [yes_no], sample_size=25, confirm=True
    )
    assert confirmed.status_code == status.HTTP_200_OK
    run = InstantEvalRun.objects.get(eval_task_id=confirmed.json()["result"]["task_id"])
    assert run.rows_requested == 25 and run.population == 40
    assert run.credits_estimate == pytest.approx(25 * 0.06)
    assert run.created_by is not None
    assert len(started) == 1


@pytest.mark.django_db
@override_settings(INSTANT_EVAL_MAX_ACTIVE_PER_ORG=2)
def test_an_organization_runs_at_most_two_at_once(
    auth_client, observe_project, yes_no, fake_view, started
):
    for _ in range(2):
        assert (
            _start(auth_client, observe_project, [yes_no], sample_size=5).status_code
            == 200
        )
    third = _start(auth_client, observe_project, [yes_no], sample_size=5)
    assert third.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    body = third.json()["result"]
    assert body["error_code"] == "INSTANT_EVAL_BUSY"
    assert len(body["runs"]) == 2
    assert "Did the agent resolve it?" in body["message"]
    # A paused run no longer counts.
    EvalTask.objects.filter(id=body["runs"][0]["task_id"]).update(
        status=EvalTaskStatus.PAUSED
    )
    assert (
        _start(auth_client, observe_project, [yes_no], sample_size=5).status_code == 200
    )


@pytest.mark.django_db
def test_handed_over_ids_are_read_whole_and_capped(
    auth_client, observe_project, yes_no, fake_view, started
):
    ids = fake_view.ids[:7]
    response = _start(auth_client, observe_project, [yes_no], ids=ids, sample_size=3)
    result = response.json()["result"]
    assert result["rows_requested"] == 7 and result["is_sample"] is False
    task = EvalTask.objects.get(id=result["task_id"])
    assert sorted(resolve_desired_rows(task).matched_ids) == sorted(ids)
    too_many = _start(
        auth_client,
        observe_project,
        [yes_no],
        ids=[str(uuid.uuid4()) for _ in range(5001)],
    )
    assert too_many.status_code == status.HTTP_400_BAD_REQUEST


# ─── Summary, rows, export ───────────────────────────────────────────────────
@pytest.fixture
def answered_run(auth_client, observe_project, yes_no, category, fake_view, started):
    """A finished run over 12 sessions with two questions and known answers."""
    result = _start(
        auth_client, observe_project, [yes_no, category], sample_size=12
    ).json()["result"]
    task = EvalTask.objects.get(id=result["task_id"])
    ids = list(resolve_desired_rows(task).matched_ids)
    logs = []
    for n, sid in enumerate(ids):
        k = int(uuid.UUID(sid))
        if n == 0:
            logs.append(_answer(task, yes_no, sid, error=True))
        else:
            logs.append(
                _answer(
                    task,
                    yes_no,
                    sid,
                    "No" if k % 2 else "Yes",
                    confidence=0.4 if n == 1 else 0.9,
                )
            )
        logs.append(
            _answer(
                task,
                category,
                sid,
                ["Order status", "Return or refund", "Something else"][k % 3],
            )
        )
    EvalLogger.objects.bulk_create(logs)
    # Each answer is copied over as it lands (entries.mark_terminal).
    for entry in logs:
        record_answer(entry.id)
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.COMPLETED)
    return task, ids


def _summary(auth_client, project, task, tz="UTC"):
    response = auth_client.get(
        SUMMARY, {"project_id": str(project.id), "eval_task_id": str(task.id), "tz": tz}
    )
    assert response.status_code == 200, response.content
    return response.json()["result"]


@pytest.mark.django_db
def test_summary_counts_match_the_answers(
    auth_client, observe_project, yes_no, category, answered_run
):
    task, ids = answered_run
    s = _summary(auth_client, observe_project, task)
    keys = [int(uuid.UUID(i)) for i in ids]
    yes = sum(1 for k in keys[1:] if k % 2 == 0)
    assert s["status"] == "completed"
    assert (s["total"], s["answered"], s["failed"], s["pending"]) == (12, 11, 1, 0)
    assert s["counts"][str(yes_no.id)] == {"Yes": yes, "No": 11 - yes, "__error__": 1}
    assert sum(s["counts"][str(category.id)].values()) == 12
    assert sum(s["unsure"][str(yes_no.id)].values()) == 1
    assert s["sample"] == {
        "population": 40,
        "population_is_lower_bound": False,
        "requested": 12,
        "read": 12,
        "is_sample": True,
        "can_extend": True,
        "next_size": 12,
    }
    assert [q["answer_type"] for q in s["questions"]] == ["yes_no", "category"]
    # Every row lands in exactly one timeline bin and one model group.
    assert sum(b["n"] for b in s["timeline"]["bins"]) == 12
    assert len(s["timeline"]["bins"]) <= 120
    assert sum(g["n"] for g in s["groups"]["model"]) == 12
    assert sum(g["n"] for g in s["groups"]["day_hour"]) == 12
    models = {g["key"]: g for g in s["groups"]["model"]}
    assert models["gpt-4.1"]["counts"][str(yes_no.id)].get("Yes", 0) + models[
        "gpt-4.1"
    ]["counts"][str(yes_no.id)].get("__error__", 0) == sum(
        1 for k in keys if k % 2 == 0
    )
    assert {g["key"] for g in s["groups"]["error"]} <= {"error", "ok"}
    errors = sum(1 for k in keys if k % 5 == 0)
    assert sum(b["errors"] for b in s["timeline"]["bins"]) == errors
    # Cross-question counts both ways, answered rows only.
    cross = s["cross"][str(category.id)][str(yes_no.id)]
    assert sum(n for by in cross.values() for n in by.values()) == 11
    assert s["cross"][str(yes_no.id)][str(category.id)]


@pytest.mark.django_db
def test_days_split_at_the_viewers_midnight(
    auth_client, observe_project, yes_no, answered_run
):
    task, ids = answered_run
    # 06:30 and 07:30 UTC on Oct 13 straddle midnight in Los Angeles (UTC-7).
    InstantEvalRow.objects.filter(task_id=task.id).update(
        time=datetime(2026, 10, 13, 7, 30, tzinfo=UTC)
    )
    InstantEvalRow.objects.filter(task_id=task.id, row_id=ids[0]).update(
        time=datetime(2026, 10, 13, 6, 30, tzinfo=UTC)
    )
    utc = {
        g["key"]: g["n"]
        for g in _summary(auth_client, observe_project, task)["groups"]["day"]
    }
    la = {
        g["key"]: g["n"]
        for g in _summary(auth_client, observe_project, task, "America/Los_Angeles")[
            "groups"
        ]["day"]
    }
    assert utc == {"2026-10-13": 12}
    assert la == {"2026-10-12": 1, "2026-10-13": 11}
    la_hours = _summary(auth_client, observe_project, task, "America/Los_Angeles")[
        "groups"
    ]["day_hour"]
    assert {g["key"] for g in la_hours} == {"2026-10-12|23", "2026-10-13|00"}
    # A selected local day reads exactly its rows.
    page = auth_client.get(
        ROWS,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "tz": "America/Los_Angeles",
            "group": "day:2026-10-12",
        },
    ).json()["result"]
    assert [r["id"] for r in page["rows"]] == [ids[0]]


@pytest.mark.django_db
def test_rows_page_through_every_row_and_filter_like_the_charts(
    auth_client, observe_project, yes_no, category, answered_run
):
    task, ids = answered_run
    base = {
        "project_id": str(observe_project.id),
        "eval_task_id": str(task.id),
        "tz": "UTC",
    }
    seen, cursor = [], None
    while True:
        page = auth_client.get(
            ROWS, {**base, "page_size": 5, **({"cursor": cursor} if cursor else {})}
        ).json()["result"]
        assert page["total"] == 12
        seen += page["rows"]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(r["id"] for r in seen) == sorted(ids)
    times = [r["time"] for r in seen]
    assert times == sorted(times, reverse=True)
    assert seen[0]["answers"][str(category.id)]["answer"] in (
        "Order status",
        "Return or refund",
        "Something else",
    )

    keys = {i: int(uuid.UUID(i)) for i in ids}
    no = auth_client.get(
        ROWS, {**base, "config_id": str(yes_no.id), "answer": "No"}
    ).json()["result"]
    assert {r["id"] for r in no["rows"]} == {i for i in ids[1:] if keys[i] % 2}
    failed = auth_client.get(
        ROWS, {**base, "config_id": str(yes_no.id), "answer": "__error__"}
    ).json()["result"]
    assert [r["id"] for r in failed["rows"]] == [ids[0]]
    unsure = auth_client.get(ROWS, {**base, "unsure": "true"}).json()["result"]
    assert [r["id"] for r in unsure["rows"]] == [ids[1]]
    model = auth_client.get(ROWS, {**base, "group": "model:gpt-4.1-mini"}).json()[
        "result"
    ]
    assert {r["id"] for r in model["rows"]} == {i for i in ids if keys[i] % 2}
    # Several groups are repeated parameters, each key taken whole.
    either = auth_client.get(
        ROWS, {**base, "group": ["model:gpt-4.1-mini", "model:gpt-4.1"]}
    ).json()["result"]
    assert either["total"] == 12
    # A long range's pattern selects a key per day: 90 of them still read.
    many = auth_client.get(
        ROWS,
        {
            **base,
            "group": ["model:gpt-4.1-mini", "model:gpt-4.1"]
            + [f"day:2026-07-{d:02d}" for d in range(1, 31)] * 3,
        },
    )
    assert many.status_code == 200, many.content
    assert many.json()["result"]["total"] == 12
    both = auth_client.get(
        ROWS,
        {
            **base,
            "answers": f'[{{"config_id": "{yes_no.id}", "answer": "No"}}, {{"config_id": "{category.id}", "answer": "Order status"}}]',
        },
    ).json()["result"]
    assert {r["id"] for r in both["rows"]} == {
        i for i in ids[1:] if keys[i] % 2 and keys[i] % 3 == 0
    }
    first = min(r["time"] for r in seen)
    window = auth_client.get(
        ROWS, {**base, "time_from": first, "time_to": first[:19] + ".000001+00:00"}
    ).json()["result"]
    assert window["total"] == 1
    by_confidence = auth_client.get(
        ROWS,
        {**base, "sort": "confidence", "config_id": str(yes_no.id), "page_size": 1},
    ).json()["result"]
    assert by_confidence["rows"][0]["id"] == ids[1]
    bad = auth_client.get(ROWS, {**base, "group": "planet:mars"})
    assert bad.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_export_streams_every_selected_row(
    auth_client, observe_project, yes_no, category, answered_run
):
    task, ids = answered_run
    response = auth_client.get(
        EXPORT, {"project_id": str(observe_project.id), "eval_task_id": str(task.id)}
    )
    assert response.status_code == 200
    body = b"".join(response.streaming_content).decode()
    lines = list(csv.reader(io.StringIO(body)))
    assert len(lines) == 13
    assert lines[0][-3:] == [
        "What did the customer come for? · answer",
        "What did the customer come for? · confidence",
        "What did the customer come for? · explanation",
    ]
    assert sorted(line[0] for line in lines[1:]) == sorted(ids)
    filtered = auth_client.get(
        EXPORT,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "config_id": str(yes_no.id),
            "answer": "__error__",
        },
    )
    assert (
        len(
            list(csv.reader(io.StringIO(b"".join(filtered.streaming_content).decode())))
        )
        == 2
    )


def test_export_cells_never_read_as_spreadsheet_formulas():
    from tracer.services import instant_eval_summary as summary_service

    line = summary_service._csv_line(
        ['=HYPERLINK("http://x")', "+1", "-2", "@SUM(A1)", "\tx", "\rx", "ok", 0.5]
    )
    [cells] = list(csv.reader(io.StringIO(line)))
    assert cells == [
        '\'=HYPERLINK("http://x")',
        "'+1",
        "'-2",
        "'@SUM(A1)",
        "'\tx",
        "'\rx",
        "ok",
        "0.5",
    ]


def test_an_export_that_fails_after_its_headers_says_it_is_incomplete():
    from tracer.services import instant_eval_summary as summary_service

    def lines():
        yield "id,time\n"
        yield "1,2026-10-01\n"
        raise RuntimeError("connection lost")

    out = list(summary_service.marked_export(lines()))
    assert out[:2] == ["id,time\n", "1,2026-10-01\n"]
    assert out[-1] == summary_service.EXPORT_INCOMPLETE
    assert out[-1].startswith("# export incomplete")


@pytest.mark.django_db
def test_an_exported_excerpt_that_starts_like_a_formula_is_quoted(
    auth_client, observe_project, answered_run
):
    from tracer.models.instant_eval import InstantEvalRow

    task, ids = answered_run
    params = {"project_id": str(observe_project.id), "eval_task_id": str(task.id)}
    # The first read lands the run's rows; then one excerpt is a formula.
    b"".join(auth_client.get(EXPORT, params).streaming_content)
    assert InstantEvalRow.objects.filter(task_id=task.id).update(excerpt="=1+1")
    response = auth_client.get(EXPORT, params)
    body = b"".join(response.streaming_content).decode()
    lines = list(csv.reader(io.StringIO(body)))
    excerpt = lines[0].index("excerpt")
    assert {line[excerpt] for line in lines[1:]} == {"'=1+1"}
    assert not body.rstrip().endswith("export again")


@pytest.mark.django_db
def test_summary_of_another_organizations_run_is_not_found(auth_client, answered_run):
    from accounts.models.organization import Organization
    from model_hub.models.ai_model import AIModel
    from tracer.models.project import Project

    task, _ = answered_run
    other = Organization.objects.create(name="Elsewhere")
    project = getattr(Project, "no_workspace_objects", Project.objects).create(
        name="Elsewhere",
        organization=other,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    response = auth_client.get(
        SUMMARY, {"project_id": str(project.id), "eval_task_id": str(task.id)}
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ─── Control ─────────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_retry_failed_requeues_only_the_failures(
    auth_client, observe_project, yes_no, answered_run, started
):
    task, ids = answered_run
    response = auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "action": "retry_failed",
        },
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert response.json()["result"]["retried"] == 1
    entry = EvalLogger.objects.get(
        eval_task_id=str(task.id), trace_session_id=ids[0], custom_eval_config=yes_no
    )
    assert entry.status == EvalEntryStatus.PENDING and entry.error is False
    assert EvalTask.objects.get(id=task.id).status == EvalTaskStatus.PENDING
    assert started[-1][1] == {"replace_existing": True}


def _half_answered_run(auth_client, observe_project, yes_no, status_):
    """A 6-row run: one row failed, one answered, four never read."""
    result = _start(auth_client, observe_project, [yes_no], sample_size=6).json()[
        "result"
    ]
    task = EvalTask.objects.get(id=result["task_id"])
    ids = list(resolve_desired_rows(task).matched_ids)
    logs = EvalLogger.objects.bulk_create(
        [
            _answer(task, yes_no, ids[0], error=True),
            _answer(task, yes_no, ids[1], "Yes"),
        ]
        + [_answer(task, yes_no, i, pending=True) for i in ids[2:]]
    )
    for entry in logs:
        record_answer(entry.id)
    EvalTask.objects.filter(id=task.id).update(status=status_)
    return task


def _control(auth_client, observe_project, task, action, **extra):
    return auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "action": action,
            **extra,
        },
        format="json",
    )


@pytest.mark.django_db
def test_a_paused_run_with_rows_left_is_resumed_not_retried_or_extended(
    auth_client, observe_project, yes_no, fake_view, started
):
    """Retry and "read more" restart the run, which reads every unanswered
    row: past the 1 retried answer or the rows "read more" confirmed."""
    task = _half_answered_run(
        auth_client, observe_project, yes_no, EvalTaskStatus.PAUSED
    )
    before = len(started)
    for action, extra in (("retry_failed", {}), ("read_more", {"more": 5})):
        refused = _control(auth_client, observe_project, task, action, **extra)
        assert refused.status_code == status.HTTP_409_CONFLICT, refused.json()
        assert refused.json()["code"] == "instant_eval_paused"
    assert len(started) == before
    assert EvalTask.objects.get(id=task.id).status == EvalTaskStatus.PAUSED


@pytest.mark.django_db
def test_retrying_on_a_stalled_run_restarts_it(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    """It says running but no workflow drains it: without a restart nothing
    would answer the requeued rows."""
    task = _half_answered_run(
        auth_client, observe_project, yes_no, EvalTaskStatus.RUNNING
    )
    monkeypatch.setattr(instant_eval_scale, "_workflow_gone", lambda task: True)
    before = len(started)
    retried = _control(auth_client, observe_project, task, "retry_failed")
    assert retried.status_code == 200, retried.json()
    assert len(started) == before + 1
    assert EvalTask.objects.get(id=task.id).status == EvalTaskStatus.PENDING


@pytest.mark.django_db
def test_cancel_stops_a_run_and_its_unread_rows_are_not_read(
    auth_client, observe_project, yes_no, fake_view, started
):
    result = _start(auth_client, observe_project, [yes_no], sample_size=6).json()[
        "result"
    ]
    task = EvalTask.objects.get(id=result["task_id"])
    ids = list(resolve_desired_rows(task).matched_ids)
    logs = EvalLogger.objects.bulk_create(
        [_answer(task, yes_no, ids[0], "Yes")]
        + [_answer(task, yes_no, i, pending=True) for i in ids[1:]]
    )
    for entry in logs:
        record_answer(entry.id)
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.RUNNING)
    running = _summary(auth_client, observe_project, task)
    assert (running["status"], running["pending"], running["answered"]) == (
        "running",
        5,
        1,
    )
    more = auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "action": "read_more",
            "more": 5,
        },
        format="json",
    )
    assert more.status_code == status.HTTP_409_CONFLICT
    cancel = auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "action": "cancel",
        },
        format="json",
    )
    assert cancel.json()["result"]["status"] == "cancelled"
    done = _summary(auth_client, observe_project, task)
    assert (done["status"], done["pending"], done["not_read"]) == ("cancelled", 0, 5)
    assert done["sample"]["can_extend"] is False


def test_hash_sample_sql_orders_by_hash_and_continues_after_the_cursor(monkeypatch):
    seen = {}

    class Builder:
        def build_id_query(self):
            return "SELECT session_id FROM spans WHERE x = %(x)s", {"x": 1}

    monkeypatch.setattr(rows_service, "_builder", lambda *a: (Builder(), "session_id"))
    monkeypatch.setattr(
        rows_service,
        "_ch",
        lambda sql, params: seen.update(sql=sql, params=params) or [],
    )
    rows_service.pick_rows("p", "sessions", [], n=1000, after=42)
    assert "cityHash64(toString(session_id))" in seen["sql"]
    assert "WHERE h > %(ie_after)s ORDER BY h, rid LIMIT %(ie_n)s" in seen["sql"]
    assert seen["params"] == {"x": 1, "ie_n": 1000, "ie_after": 42}


def test_excerpts_find_the_user_message_in_cut_off_chat_json():
    raw = '[{"role": "system", "content": "You are Harbor"}, {"role": "user", "content": "Where is order #4471?"}, {"role": "assistant", "content": "Let me ch'
    assert rows_service.excerpt_of(raw) == "Where is order #4471?"
    assert rows_service.excerpt_of("plain text") == "plain text"


def test_bins_pick_the_smallest_size_that_fits():
    assert summary_service.pick_bin(60 * 60_000) == 60_000
    assert summary_service.pick_bin(29 * 24 * 3_600_000) == 6 * 3_600_000
    # One bin is kept for the first, aligned to a local boundary before the window.
    assert summary_service.pick_bin(30 * 24 * 3_600_000) == 12 * 3_600_000
    assert summary_service._hour_range(9, 12) == "9 AM–12 PM"
    assert summary_service._hour_range(6, 9) == "6–9 AM"
    assert summary_service._hour_range(21, 24) == "9 PM–12 AM"


@pytest.mark.django_db
def test_instant_sizing_applies_at_any_size(observe_project, yes_no):
    from tfc.temporal.eval_tasks.client import _instant_sizing

    task = EvalTask.objects.create(
        project=observe_project,
        name="Instant eval · big",
        filters={},
        sampling_rate=100,
        spans_limit=100_000,
        run_type=RunType.HISTORICAL,
        status=EvalTaskStatus.PENDING,
        row_type=RowType.SESSIONS,
    )
    task.evals.add(yes_no)
    assert _instant_sizing(task) == {
        "batch_size": 200,
        "max_concurrent": 32,
        "heartbeat_s": 60,
    }


# ─── Answers as they land, stalled runs, model by day ────────────────────────
@pytest.mark.django_db
def test_an_answer_lands_in_the_answer_table_when_its_entry_finishes(
    auth_client, observe_project, yes_no, fake_view, started
):
    from tracer.models.instant_eval import InstantEvalAnswer
    from tracer.services.eval_tasks.entries import mark_terminal

    result = _start(auth_client, observe_project, [yes_no], sample_size=2).json()[
        "result"
    ]
    task = EvalTask.objects.get(id=result["task_id"])
    ids = list(resolve_desired_rows(task).matched_ids)
    entry = _answer(task, yes_no, ids[0], "No", confidence=0.3)
    entry.status = EvalEntryStatus.RUNNING
    EvalLogger.objects.bulk_create([entry])
    assert not InstantEvalAnswer.objects.filter(task_id=task.id).exists()
    assert mark_terminal(entry, EvalEntryStatus.COMPLETED, config_hash="h", epoch=None)
    answer = InstantEvalAnswer.objects.get(task_id=task.id)
    assert (answer.label, answer.confidence, answer.config_id) == ("No", 0.3, yes_no.id)
    assert answer.row.row_id == ids[0] and answer.time == answer.row.time


def _running_with_unanswered(auth_client, observe_project, yes_no):
    result = _start(auth_client, observe_project, [yes_no], sample_size=4).json()[
        "result"
    ]
    task = EvalTask.objects.get(id=result["task_id"])
    ids = list(resolve_desired_rows(task).matched_ids)
    logs = EvalLogger.objects.bulk_create(
        [_answer(task, yes_no, ids[0], "Yes")]
        + [_answer(task, yes_no, i, pending=True) for i in ids[1:]]
    )
    for entry in logs:
        record_answer(entry.id)
    long_ago = datetime.now(UTC) - timedelta(minutes=10)
    from tracer.models.instant_eval import InstantEvalAnswer

    InstantEvalAnswer.objects.filter(task_id=task.id).update(answered_at=long_ago)
    EvalTask.objects.filter(id=task.id).update(
        status=EvalTaskStatus.RUNNING, updated_at=long_ago
    )
    return task


@pytest.mark.django_db
def test_a_run_whose_workflow_vanished_reports_stalled_and_resumes(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    from django.core.cache import cache

    cache.clear()
    task = _running_with_unanswered(auth_client, observe_project, yes_no)
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.describe_eval_task_workflow_sync",
        lambda task_id: "absent",
    )
    stalled = _summary(auth_client, observe_project, task)
    assert (stalled["status"], stalled["answered"], stalled["pending"]) == (
        "stalled",
        1,
        3,
    )
    resumed = auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "action": "resume",
        },
        format="json",
    )
    assert resumed.status_code == 200, resumed.json()
    assert started[-1] == (str(task.id), {"replace_existing": True})
    assert EvalTask.objects.get(id=task.id).status == EvalTaskStatus.PENDING


@pytest.mark.django_db
def test_a_quiet_run_with_its_workflow_alive_stays_running(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    from django.core.cache import cache

    cache.clear()
    task = _running_with_unanswered(auth_client, observe_project, yes_no)
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.describe_eval_task_workflow_sync",
        lambda task_id: "progressing",
    )
    assert _summary(auth_client, observe_project, task)["status"] == "running"
    refused = auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "action": "resume",
        },
        format="json",
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_a_finished_task_with_unanswered_rows_is_stalled(
    auth_client, observe_project, yes_no, fake_view, started
):
    task = _running_with_unanswered(auth_client, observe_project, yes_no)
    EvalLogger.objects.filter(
        eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
    ).update(deleted=True)
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.COMPLETED)
    summary = _summary(auth_client, observe_project, task)
    assert (summary["status"], summary["pending"]) == ("stalled", 3)


@pytest.mark.django_db
def test_entries_the_reaper_gives_up_on_show_as_failed_while_the_run_runs(
    auth_client, observe_project, yes_no, fake_view, started
):
    from tracer.services.eval_tasks.reaper import reap_stale_running

    task = _running_with_unanswered(auth_client, observe_project, yes_no)
    stuck = EvalLogger.objects.filter(
        eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
    )
    gave_up, retried = list(stuck.values_list("id", flat=True))[:2], None
    long_ago = datetime.now(UTC) - timedelta(hours=3)
    EvalLogger.objects.filter(id__in=gave_up).update(
        status=EvalEntryStatus.RUNNING, attempts=3, updated_at=long_ago
    )
    retried = stuck.exclude(id__in=gave_up).values_list("id", flat=True)[0]
    EvalLogger.objects.filter(id=retried).update(
        status=EvalEntryStatus.RUNNING, attempts=0, updated_at=long_ago
    )
    assert reap_stale_running(task, older_than_seconds=60, max_attempts=3) == (1, 2)
    summary = _summary(auth_client, observe_project, task)
    # Two given up on (failed), one requeued (still waiting), one answered.
    assert (
        summary["status"],
        summary["answered"],
        summary["failed"],
        summary["pending"],
    ) == (
        "running",
        1,
        2,
        1,
    )
    assert summary["counts"][str(yes_no.id)]["__error__"] == 2


@pytest.mark.django_db
def test_each_answer_lists_the_users_who_get_it_most_beyond_the_run(
    auth_client, observe_project, yes_no, category, answered_run
):
    import math

    task, ids = answered_run
    # Worked out from the fixture: each session's user and yes/no answer.
    rows: dict[str, list] = {}
    for n, sid in enumerate(ids):
        k = int(uuid.UUID(sid))
        user = str(uuid.UUID(int=1000 + k % 3))
        rows.setdefault(user, []).append(None if n == 0 else "No" if k % 2 else "Yes")

    answered = [x for a in rows.values() for x in a if x]

    def z_score(hits, n_answered, label):
        p0 = answered.count(label) / len(answered)
        return (hits - n_answered * p0) / math.sqrt(n_answered * p0 * (1 - p0))

    expected = {}
    for label in ("Yes", "No"):
        scored = [
            (z_score(a.count(label), len([x for x in a if x]), label), len(a), user)
            for user, a in rows.items()
            if len(a) >= 3 and a.count(label)
        ]
        expected[label] = [
            u for _, _, u in sorted(scored, key=lambda x: (-x[0], -x[1], x[2]))
        ]

    by_answer = _summary(auth_client, observe_project, task)["groups"]["user_by_answer"]
    yes_no_users = by_answer[str(yes_no.id)]
    assert set(yes_no_users) == {label for label in expected if expected[label]}
    for label, users in expected.items():
        got = yes_no_users.get(label, [])
        assert [entry["key"] for entry in got] == users
        for entry in got:
            assert entry["n"] == len(rows[entry["key"]])
            assert entry["counts"][str(yes_no.id)].get(label) == rows[
                entry["key"]
            ].count(label)
            # Every question's answers, as in groups.user.
            assert sum(entry["counts"][str(category.id)].values()) == entry["n"]
    assert set(by_answer[str(category.id)]) <= {
        "Order status",
        "Return or refund",
        "Something else",
    }


@pytest.mark.django_db
def test_answers_from_elsewhere_are_copied_once_and_a_retry_recounts(
    auth_client, observe_project, yes_no, category, answered_run, started
):
    from tracer.models.instant_eval import InstantEvalAnswer

    task, ids = answered_run
    InstantEvalAnswer.objects.filter(task_id=task.id).delete()
    InstantEvalRun.objects.filter(eval_task_id=task.id).update(answers_synced=False)
    first = _summary(auth_client, observe_project, task)
    assert first["answered"] == 11
    assert InstantEvalAnswer.objects.filter(task_id=task.id).count() == 24
    version = InstantEvalRun.objects.get(eval_task_id=task.id).answers_version
    retried = auth_client.post(
        CONTROL,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "action": "retry_failed",
        },
        format="json",
    )
    assert retried.status_code == 200
    assert (
        InstantEvalRun.objects.get(eval_task_id=task.id).answers_version == version + 1
    )
    # The failed answer waits again.
    assert InstantEvalAnswer.objects.filter(task_id=task.id).count() == 23
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.RUNNING)
    again = _summary(auth_client, observe_project, task)
    assert (again["failed"], again["pending"]) == (0, 1)


@pytest.mark.django_db
def test_model_by_day_groups_and_selects_rows(
    auth_client, observe_project, yes_no, answered_run
):
    task, ids = answered_run
    s = _summary(auth_client, observe_project, task)
    model_day = {g["key"]: g for g in s["groups"]["model_day"]}
    assert sum(g["n"] for g in model_day.values()) == 12
    key, entry = next(iter(model_day.items()))
    model, _, day = key.partition("|")
    assert entry["label"].startswith(model)
    page = auth_client.get(
        ROWS,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "tz": "UTC",
            "group": f"model_day:{key}",
        },
    ).json()["result"]
    assert page["total"] == entry["n"]
    assert all(
        r["facets"]["model"] == model and r["time"].startswith(day)
        for r in page["rows"]
    )


def _credit_cap(monkeypatch, left=None, code=None, raises=None):
    """Billing's credit check: refuse more than ``left`` credits
    (FREE_TIER_LIMIT, 100 used), refuse every run with ``code``, or raise."""
    calls = []

    def check_usage(org_id, event_type, amount=0):
        calls.append((event_type, round(amount, 2)))
        if raises:
            raise raises
        if code:
            return SimpleNamespace(
                allowed=False, error_code=code, reason="", current_usage=0, limit=0
            )
        if left is not None and amount > left:
            return SimpleNamespace(
                allowed=False,
                error_code="FREE_TIER_LIMIT",
                reason="Free tier limit reached",
                current_usage=100.0,
                limit=100.0 + left,
            )
        return SimpleNamespace(allowed=True, error_code="", reason="")

    monkeypatch.setattr("ee.usage.services.metering.check_usage", check_usage)
    return calls


def _estimate(auth_client, project, sample_size=20, questions=1):
    return auth_client.post(
        ESTIMATE,
        {
            "project_id": str(project.id),
            "unit": "sessions",
            "filters": WINDOW,
            "sample_size": sample_size,
            "questions": questions,
        },
        format="json",
    ).json()["result"]


@pytest.mark.django_db
def test_the_credits_left_limit_the_estimate_and_refuse_the_start(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    # 0.6 credits left pay for 10 answers.
    calls = _credit_cap(monkeypatch, left=0.6)
    estimate = _estimate(auth_client, observe_project)
    check = estimate["credit_check"]
    assert check["allowed"] is False
    assert check["error_code"] == "FREE_TIER_LIMIT"
    assert check["blocked"] is False
    assert check["current_usage"] == 100.0
    assert round(check["credits_left"], 2) == 0.6
    assert estimate["credit_max_sample"] == 10
    assert check["message"] == (
        "This run needs about 1.2 credits; you have 0.6 left this month. "
        "A sample of up to 10 sessions fits."
    )
    by_size = {c["sample_size"]: c for c in estimate["choices"]}
    assert by_size[None]["credit_limited"] is True
    assert ("jev_evaluator", 1.2) in calls
    # The time is the assumed pace only: 20 answers at 300 a minute.
    assert estimate["seconds_estimate"] == 10 + 4

    refused = _start(auth_client, observe_project, [yes_no], sample_size=20)
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    detail = refused.json()["result"]
    assert detail["error_code"] == "FREE_TIER_LIMIT"
    assert detail["sample_size"] == 10
    assert detail["credits_estimate"] == 1.2
    assert not InstantEvalRun.objects.filter(project=observe_project).exists()
    assert (
        _start(auth_client, observe_project, [yes_no], sample_size=10).status_code
        == 200
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("code", "http", "words"),
    [
        ("BUDGET_PAUSED", status.HTTP_403_FORBIDDEN, "paused by your budget"),
        ("PAYMENT_REQUIRED", status.HTTP_402_PAYMENT_REQUIRED, "unpaid invoices"),
    ],
)
def test_a_paused_budget_or_unpaid_invoice_stops_every_run(
    auth_client,
    observe_project,
    yes_no,
    fake_view,
    started,
    monkeypatch,
    code,
    http,
    words,
):
    _credit_cap(monkeypatch, code=code)
    estimate = _estimate(auth_client, observe_project)
    check = estimate["credit_check"]
    assert (check["allowed"], check["error_code"], check["blocked"]) == (
        False,
        code,
        True,
    )
    assert words in check["message"]
    assert estimate["credit_max_sample"] is None
    assert not any(c["allowed"] for c in estimate["choices"])

    refused = _start(auth_client, observe_project, [yes_no], sample_size=1)
    assert refused.status_code == http
    detail = refused.json()["result"]
    assert detail["error_code"] == code
    assert words in detail["message"]
    assert detail["sample_size"] is None


@pytest.mark.django_db
def test_a_priced_run_is_refused_when_billing_cannot_answer(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    """A Redis or billing outage on Cloud refuses with a 503: a priced run
    never starts, and bills, unchecked."""
    _credit_cap(monkeypatch, raises=RuntimeError("redis down"))
    estimate = auth_client.post(
        ESTIMATE,
        {
            "project_id": str(observe_project.id),
            "unit": "sessions",
            "filters": WINDOW,
            "sample_size": 20,
            "questions": 1,
        },
        format="json",
    )
    assert estimate.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    refused = _start(auth_client, observe_project, [yes_no], sample_size=20)
    assert refused.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "Credits can't be checked" in str(refused.json())
    assert not InstantEvalRun.objects.filter(project=observe_project).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("billing", ["allowed", "missing"])
def test_a_run_starts_when_billing_allows_it_or_is_not_set_up(
    auth_client,
    observe_project,
    yes_no,
    fake_view,
    started,
    monkeypatch,
    billing,
):
    if billing == "missing":
        # No ee here: the import fails.
        monkeypatch.setitem(sys.modules, "ee.usage.services.metering", None)
    else:
        _credit_cap(monkeypatch)
    check = _estimate(auth_client, observe_project)["credit_check"]
    assert (check["allowed"], check["credits_left"], check["message"]) == (
        True,
        None,
        None,
    )
    assert (
        _start(auth_client, observe_project, [yes_no], sample_size=20).status_code
        == 200
    )


@pytest.mark.django_db
def test_reading_more_and_retrying_check_the_credits_left_too(
    auth_client, observe_project, answered_run, monkeypatch
):
    task, _ = answered_run

    def control(action, **body):
        return auth_client.post(
            CONTROL,
            {
                "project_id": str(observe_project.id),
                "eval_task_id": str(task.id),
                "action": action,
                **body,
            },
            format="json",
        )

    # Nothing left: neither five more rows of two questions nor the one
    # failed answer fits.
    calls = _credit_cap(monkeypatch, left=0.0)
    more = control("read_more", more=5)
    assert more.status_code == status.HTTP_403_FORBIDDEN
    assert more.json()["result"]["error_code"] == "FREE_TIER_LIMIT"
    retry = control("retry_failed")
    assert retry.status_code == status.HTTP_403_FORBIDDEN
    assert ("jev_evaluator", 0.6) in calls and ("jev_evaluator", 0.06) in calls
    run = InstantEvalRun.objects.get(eval_task_id=task.id)
    assert run.rows_requested == 12
    assert EvalLogger.objects.filter(
        eval_task_id=str(task.id), status=EvalEntryStatus.ERRORED
    ).exists()

    _credit_cap(monkeypatch, left=10.0)
    assert control("retry_failed").status_code == status.HTTP_200_OK


def _billing(monkeypatch, *, cloud=True, markup="1.2", config=True):
    """This deployment's billing as Instant eval reads it: on Cloud or not,
    and the markup on the 0.0005 eval fee its billing config sets, or no
    billing config at all."""
    from decimal import Decimal

    from ee.usage.deployment import DeploymentMode
    from ee.usage.schemas.billing_config import BillingConfigSchema
    from ee.usage.services.config import BillingConfig, BillingConfigError

    monkeypatch.setattr(instant_eval_scale, "credits_per_answer", BILLED_RATE)
    monkeypatch.setattr(DeploymentMode, "is_cloud", staticmethod(lambda: cloud))
    if config:
        schema = BillingConfigSchema(
            plans={},
            dimensions={},
            pricing_tiers={},
            entitlements={},
            api_call_types={},
            ee_bands={},
            ai_credit_models={},
            ai_cost_markup=Decimal(markup),
            eval_per_run_fee=Decimal("0.0005"),
        )
        monkeypatch.setattr(BillingConfig, "_instance", BillingConfig(schema))
        return

    def missing(cls):
        raise BillingConfigError("billing.yaml not found")

    monkeypatch.setattr(BillingConfig, "_instance", None)
    monkeypatch.setattr(BillingConfig, "_load", classmethod(missing))


@pytest.mark.requires_ee
@pytest.mark.django_db
@pytest.mark.parametrize(("markup", "rate"), [("1.2", 0.06), ("1.0", 0.05)])
def test_the_credits_are_what_jev_is_charged(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch, markup, rate
):
    from ee.jev import metering
    from ee.usage.services.config import BillingConfig

    _billing(monkeypatch, markup=markup)
    billing = BillingConfig.get()
    charged = billing.calculate_ai_credits(billing.get_eval_per_run_fee())
    assert charged == pytest.approx(rate)
    # One rate: what the estimate prices and what metering charges.
    assert instant_eval_scale.credits_per_answer() == charged
    assert metering.credits_per_answer() == charged

    estimate = _estimate(auth_client, observe_project, sample_size=20, questions=2)
    assert estimate["credits_per_answer"] == charged
    assert estimate["credits_estimate"] == pytest.approx(20 * 2 * charged)
    by_size = {c["sample_size"]: c for c in estimate["choices"]}
    assert by_size[None]["credits_estimate"] == pytest.approx(40 * 2 * charged)
    result = _start(auth_client, observe_project, [yes_no], sample_size=20).json()[
        "result"
    ]
    assert result["credits_estimate"] == pytest.approx(20 * charged)


@pytest.mark.requires_ee
@pytest.mark.django_db
@override_settings(INSTANT_EVAL_CONFIRM_ROWS=20)
@pytest.mark.parametrize(
    ("cloud", "config"),
    [(False, True), (True, False)],
    ids=["self-hosted", "cloud-without-billing-config"],
)
def test_no_credits_are_shown_or_checked_where_nothing_is_billed(
    auth_client,
    observe_project,
    yes_no,
    fake_view,
    started,
    monkeypatch,
    cloud,
    config,
):
    _billing(monkeypatch, cloud=cloud, config=config)
    # A cap that would refuse any run: it is never asked.
    calls = _credit_cap(monkeypatch, left=0.0)
    estimate = _estimate(auth_client, observe_project, sample_size=25)
    assert estimate["credits_per_answer"] is None
    assert estimate["credits_estimate"] is None
    assert estimate["credit_max_sample"] is None
    assert {c["credits_estimate"] for c in estimate["choices"]} == {None}
    assert not any(c["credit_limited"] for c in estimate["choices"])
    check = estimate["credit_check"]
    assert (
        check["allowed"],
        check["credits_left"],
        check["current_usage"],
        check["limit"],
        check["message"],
    ) == (True, None, None, None, None)

    # A large run still waits for its confirm, which names no credits.
    held = _start(auth_client, observe_project, [yes_no], sample_size=25)
    assert held.status_code == status.HTTP_409_CONFLICT
    detail = held.json()["result"]
    assert detail["message"] == "Reading 25 rows. Confirm to start."
    assert detail["credits_estimate"] is None
    confirmed = _start(
        auth_client, observe_project, [yes_no], sample_size=25, confirm=True
    )
    assert confirmed.status_code == status.HTTP_200_OK
    result = confirmed.json()["result"]
    assert result["credits_estimate"] is None
    task = EvalTask.objects.get(id=result["task_id"])
    summary = _summary(auth_client, observe_project, task)
    assert (summary["credits_estimate"], summary["credits_used"]) == (None, None)
    [history] = auth_client.get(
        "/tracer/instant-eval/runs/",
        {"project_id": str(observe_project.id), "task_id": result["task_id"]},
    ).json()["result"]["runs"]
    assert history["credits_estimate"] is None
    assert calls == []


@pytest.mark.requires_ee
@pytest.mark.django_db
@pytest.mark.parametrize("config", [True, False], ids=["billed", "no-billing-config"])
def test_an_estimate_reads_the_billing_config_once(
    auth_client, observe_project, monkeypatch, config
):
    """Every figure an estimate prices (each choice, the cap's check, the
    largest sample that fits) comes from one read of the rate: a billing
    config that fails to load is tried, and logged, once per estimate."""
    from ee.usage.services.config import BillingConfig

    view = FakeView(6000)
    monkeypatch.setattr(rows_service, "count_population", view.count)
    _billing(monkeypatch, config=config)
    # A cap that refuses the chosen run, so the sample that fits is priced too.
    _credit_cap(monkeypatch, left=30.0)
    get = BillingConfig.get.__func__
    reads = []

    def counted(cls):
        reads.append(cls)
        return get(cls)

    monkeypatch.setattr(BillingConfig, "get", classmethod(counted))
    estimate = _estimate(auth_client, observe_project, sample_size=1000, questions=2)
    assert len(estimate["choices"]) == 5
    assert len(reads) == 1
    if config:
        # 30 credits at 2 answers of 0.06 each per session.
        assert estimate["credits_per_answer"] == pytest.approx(0.06)
        assert estimate["credit_max_sample"] == 250
        assert estimate["credit_check"]["error_code"] == "FREE_TIER_LIMIT"
    else:
        assert estimate["credits_per_answer"] is None
        assert estimate["credit_max_sample"] is None


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_a_question_on_jev_needs_the_organizations_typesafe_key(
    auth_client, observe_project, eval_template, fake_view, started
):
    from agentic_eval.core_evals.run_prompt.litellm_models import (
        missing_provider_key_message,
    )
    from model_hub.models.api_key import ApiKey

    question = _question(observe_project, eval_template, "Did the agent resolve it?")
    CustomEvalConfig.objects.filter(id=question.id).update(model="jev-latest")
    refusal = {
        "message": missing_provider_key_message("typesafe"),
        "error_code": "JEV_KEY_MISSING",
        "settings_path": "/dashboard/settings/ai-providers",
    }
    refused = _start(auth_client, observe_project, [question], sample_size=5)
    assert refused.status_code == status.HTTP_400_BAD_REQUEST
    assert refused.json()["code"] == "JEV_KEY_MISSING"
    assert refused.json()["result"] == refusal
    # Refused before anything was counted or queued.
    assert fake_view.picks == [] and started == []
    assert not EvalTask.objects.filter(project=observe_project).exists()

    key = ApiKey.objects.create(
        provider="typesafe",
        organization=observe_project.organization,
        key="org-typesafe-key",
    )
    result = _start(auth_client, observe_project, [question], sample_size=5)
    assert result.status_code == status.HTTP_200_OK, result.json()
    task_id = result.json()["result"]["task_id"]

    def control(action, **body):
        return auth_client.post(
            CONTROL,
            {
                "project_id": str(observe_project.id),
                "eval_task_id": task_id,
                "action": action,
                **body,
            },
            format="json",
        )

    # Reading more checks again: the key may have gone since.
    EvalTask.objects.filter(id=task_id).update(status=EvalTaskStatus.COMPLETED)
    key.delete()
    more = control("read_more", more=5)
    assert more.status_code == status.HTTP_400_BAD_REQUEST
    assert more.json()["result"] == refusal
    assert InstantEvalRun.objects.get(eval_task_id=task_id).rows_requested == 5

    # So do resuming and retrying the failed answers: both queue Jev answers.
    EvalTask.objects.filter(id=task_id).update(status=EvalTaskStatus.PAUSED)
    task = EvalTask.objects.get(id=task_id)
    _answer(task, question, str(uuid.uuid4()), error=True).save()
    for action in ("resume", "retry_failed"):
        response = control(action)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, action
        assert response.json()["code"] == "JEV_KEY_MISSING"
        assert response.json()["result"] == refusal
    # Nothing was requeued or started again.
    assert EvalTask.objects.get(id=task_id).status == EvalTaskStatus.PAUSED
    failed = EvalLogger.objects.filter(
        eval_task_id=task_id, status=EvalEntryStatus.ERRORED
    )
    assert failed.count() == 1
    assert len(started) == 1

    ApiKey.objects.create(
        provider="typesafe",
        organization=observe_project.organization,
        key="org-typesafe-key",
    )
    assert control("retry_failed").status_code == status.HTTP_200_OK
    assert not failed.exists()
    assert len(started) == 2


@pytest.mark.requires_ee
@pytest.mark.django_db
def test_a_key_saved_for_the_organization_counts_from_any_workspace(
    observe_project, eval_template, user
):
    """The runner finds a key saved for the whole organization from a
    project in any workspace; a request scoped to a non-default workspace
    would hide it, so the check doesn't read through that scope."""
    from accounts.models.workspace import Workspace
    from model_hub.models.api_key import ApiKey
    from tfc.middleware.workspace_context import (
        get_current_workspace,
        workspace_context,
    )
    from tfc.utils.general_methods import GeneralMethods

    other = Workspace.objects.create(
        name="Support", organization=observe_project.organization, created_by=user
    )
    observe_project.workspace = other
    observe_project.save(update_fields=["workspace"])
    question = _question(observe_project, eval_template, "Did the agent resolve it?")
    question.model = "jev-latest"
    key = ApiKey.objects.create(
        provider="typesafe",
        organization=observe_project.organization,
        key="org-typesafe-key",
    )
    # Saved for the whole organization (saving puts it in the default one).
    ApiKey.no_workspace_objects.filter(id=key.id).update(workspace=None)
    with workspace_context(other, organization=observe_project.organization):
        # The request's scope hides it from this workspace.
        assert not ApiKey.objects.filter(id=key.id).exists()
        missing = instant_eval_scale._jev_key_missing(
            GeneralMethods(), observe_project, [question]
        )
        assert get_current_workspace() == other
    assert missing is None


@pytest.mark.django_db
def test_history_lists_a_runs_questions_in_the_order_it_asked_them(
    auth_client, observe_project, yes_no, category, fake_view, started
):
    result = _start(
        auth_client, observe_project, [category, yes_no], sample_size=2
    ).json()["result"]
    runs = auth_client.get(
        "/tracer/instant-eval/runs/",
        {"project_id": str(observe_project.id), "page_size": 5},
    ).json()["result"]["runs"]
    run = next(r for r in runs if r["task_id"] == result["task_id"])
    assert run["config_ids"] == [str(category.id), str(yes_no.id)]
    assert run["questions"][0] == category.config["instant_eval"]["question"]


@pytest.mark.django_db
def test_a_paused_run_gives_back_entries_instead_of_asking_jev(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    from tfc.temporal.eval_tasks import activities

    task = _running_with_unanswered(auth_client, observe_project, yes_no)
    entry = EvalLogger.objects.filter(
        eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
    ).first()
    EvalLogger.objects.filter(id=entry.id).update(status=EvalEntryStatus.RUNNING)
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.PAUSED)

    def jev(*args, **kwargs):
        raise AssertionError("a paused run must not ask Jev")

    monkeypatch.setattr("tracer.services.eval_tasks.run_entry.run_entry", jev)
    # The activity recycles connections; the test's transaction keeps its own.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    out = activities._run_entry_sync(str(entry.id))
    assert out["status"] == "paused"
    entry.refresh_from_db()
    assert entry.status == EvalEntryStatus.PENDING

    # The page says the pause is draining while answers are still in flight.
    EvalLogger.objects.filter(
        eval_task_id=str(task.id), status=EvalEntryStatus.PENDING
    ).exclude(id=entry.id).update(
        status=EvalEntryStatus.RUNNING, updated_at=datetime.now(UTC)
    )
    summary = _summary(auth_client, observe_project, task)
    assert (summary["status"], summary["in_flight"]) == ("pausing", 2)
    EvalLogger.objects.filter(
        eval_task_id=str(task.id), status=EvalEntryStatus.RUNNING
    ).update(status=EvalEntryStatus.PENDING)
    summary = _summary(auth_client, observe_project, task, tz="Asia/Tokyo")
    assert (summary["status"], summary["in_flight"]) == ("paused", 0)


@pytest.mark.django_db
def test_a_quiet_running_run_says_it_waits_for_workers(
    auth_client, observe_project, yes_no, fake_view, started, monkeypatch
):
    task = _running_with_unanswered(auth_client, observe_project, yes_no)
    # The workflow is alive (not stalled): the run is just waiting.
    monkeypatch.setattr(
        "tfc.temporal.eval_tasks.client.describe_eval_task_workflow_sync",
        lambda task_id: "progressing",
    )
    EvalTask.objects.filter(id=task.id).update(
        start_time=datetime.now(UTC) - timedelta(minutes=30)
    )
    summary = _summary(auth_client, observe_project, task)
    assert summary["status"] == "running"
    assert summary["waiting_s"] >= 600
    # Resumed just now: not waiting for the time it spent paused.
    EvalTask.objects.filter(id=task.id).update(start_time=datetime.now(UTC))
    resumed = _summary(auth_client, observe_project, task, tz="Asia/Tokyo")
    assert resumed["waiting_s"] is None


def test_a_lost_worker_is_told_apart_from_a_failed_answer():
    from temporalio.exceptions import ActivityError, ApplicationError, TimeoutType
    from temporalio.exceptions import TimeoutError as ActivityTimeoutError

    from tfc.temporal.eval_tasks.workflows import _worker_lost

    def failed(cause):
        error = ActivityError(
            "activity failed",
            scheduled_event_id=1,
            started_event_id=2,
            identity="worker",
            activity_type="run_eval_entry_activity",
            activity_id="1",
            retry_state=None,
        )
        error.__cause__ = cause
        return error

    lost = ActivityTimeoutError(
        "heartbeat", type=TimeoutType.HEARTBEAT, last_heartbeat_details=[]
    )
    slow = ActivityTimeoutError(
        "start to close", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[]
    )
    assert _worker_lost(failed(lost))
    assert not _worker_lost(failed(slow))
    assert not _worker_lost(failed(ApplicationError("Jev said no")))
    assert not _worker_lost(RuntimeError("not an activity"))


@pytest.mark.django_db
def test_instant_eval_answers_stay_out_of_error_feed_clustering(
    auth_client, observe_project, yes_no, answered_run, monkeypatch
):
    from tracer.queries.eval_clustering import get_unclustered_eval_results
    from tracer.services.eval_tasks import run_entry

    task, ids = answered_run
    # The same failing answer, from this run and from a regular eval task.
    instant = EvalLogger.objects.get(
        eval_task_id=str(task.id), trace_session_id=ids[1], custom_eval_config=yes_no
    )
    EvalLogger.objects.filter(id=instant.id).update(
        output_bool=False,
        eval_explanation="the customer left without an answer",
        status=EvalEntryStatus.COMPLETED,
    )
    instant.refresh_from_db()
    regular = EvalLogger.objects.create(
        target_type=EvalTargetType.SESSION,
        trace_session_id=ids[2],
        eval_task_id="regular-eval-task",
        custom_eval_config=yes_no,
        output_bool=False,
        eval_explanation="the customer left without an answer",
        status=EvalEntryStatus.COMPLETED,
    )
    fetched = {
        r.eval_logger_id for r in get_unclustered_eval_results(str(observe_project.id))
    }
    assert str(regular.id) in fetched
    assert str(instant.id) not in fetched

    dispatched = []
    monkeypatch.setattr(
        "tracer.tasks.eval_clustering.dispatch_eval_clustering", dispatched.append
    )
    monkeypatch.setattr(
        "tracer.queries.eval_clustering.is_clusterable_eval_failure", lambda e: True
    )
    run_entry._reseed_eval_clustering(instant, observe_project.id)
    assert dispatched == []
    run_entry._reseed_eval_clustering(regular, observe_project.id)
    assert dispatched == [observe_project.id]


@pytest.mark.django_db
def test_a_run_reports_the_time_it_ran_apart_from_the_time_it_sat(
    auth_client, observe_project, answered_run
):
    from tracer.models.instant_eval import InstantEvalAnswer

    task, ids = answered_run
    start = datetime.now(UTC) - timedelta(hours=4)
    InstantEvalRun.objects.filter(eval_task_id=task.id).update(created_at=start)
    answers = list(InstantEvalAnswer.objects.filter(task_id=task.id).order_by("id"))
    # Answers land over three distinct minutes: the first hour, then after
    # a long pause, the end.
    for n, answer in enumerate(answers):
        minute = (0, 1, 200)[n % 3]
        InstantEvalAnswer.objects.filter(id=answer.id).update(
            answered_at=start + timedelta(minutes=minute, seconds=5)
        )
    summary = _summary(auth_client, observe_project, task, tz="Asia/Seoul")
    assert summary["active_s"] == 180
    assert summary["idle_s"] == 200 * 60 + 5 - 180


class _KeptCache:
    """A cache that remembers each key's value and how long it was kept."""

    def __init__(self):
        self.values, self.timeouts = {}, {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, timeout):
        self.values[key], self.timeouts[key] = value, timeout


@pytest.fixture
def counted(monkeypatch):
    """``compute_summary`` around a fake count: its cache, and the ``check``
    each count was synced with."""
    kept, checks = _KeptCache(), []
    monkeypatch.setattr(instant_eval_scale, "cache", kept)
    monkeypatch.setattr(
        "tracer.services.instant_eval_answers.ensure_synced",
        lambda run, *, check: checks.append(check),
    )
    monkeypatch.setattr(
        instant_eval_scale,
        "_summary_payload",
        lambda task, run, *_: {"status": instant_eval_scale._status(task, run)},
    )
    monkeypatch.setattr(
        instant_eval_scale,
        "_summary_key",
        lambda task, run, status, view: f"summary:{status}:{run.answers_version}",
    )
    return kept, checks


def test_a_finished_runs_summary_and_answer_check_expire(counted, monkeypatch):
    kept, checks = counted
    monkeypatch.setattr(
        instant_eval_scale, "_status", lambda task, run: EvalTaskStatus.COMPLETED
    )
    run = SimpleNamespace(eval_task_id="t1", answers_version=3)
    instant_eval_scale.compute_summary(
        SimpleNamespace(), run, SimpleNamespace(), [], None
    )
    assert kept.timeouts == {
        "ie-answers-checked:t1:3": instant_eval_scale.ANSWERS_CHECKED_TTL,
        "summary:completed:3": instant_eval_scale.FINISHED_SUMMARY_TTL,
    }
    assert instant_eval_scale.FINISHED_SUMMARY_TTL == 24 * 60 * 60
    # The check outlives the summaries, so counting one again (another time
    # zone, or after it expired) doesn't compare the answers again.
    assert instant_eval_scale.ANSWERS_CHECKED_TTL == 7 * 24 * 60 * 60
    instant_eval_scale.compute_summary(
        SimpleNamespace(), run, SimpleNamespace(), [], None
    )
    assert checks == [True, False]
    # Once the check expires the answers are compared again, and kept again.
    del kept.values["ie-answers-checked:t1:3"]
    instant_eval_scale.compute_summary(
        SimpleNamespace(), run, SimpleNamespace(), [], None
    )
    assert checks == [True, False, True]
    assert kept.values["ie-answers-checked:t1:3"] == 1


def test_a_live_runs_summary_is_kept_a_minute_and_checks_nothing(counted, monkeypatch):
    kept, checks = counted
    monkeypatch.setattr(
        instant_eval_scale, "_status", lambda task, run: EvalTaskStatus.RUNNING
    )
    run = SimpleNamespace(eval_task_id="t1", answers_version=0)
    instant_eval_scale.compute_summary(
        SimpleNamespace(), run, SimpleNamespace(), [], None
    )
    assert kept.timeouts == {"summary:running:0": 60}
    assert checks == [False]


@pytest.mark.parametrize(
    ("live", "timeout"), [(False, summary_service.USERS_BY_ANSWER_TTL), (True, 30)]
)
def test_users_by_answer_are_kept_as_long_as_the_summary(monkeypatch, live, timeout):
    kept = _KeptCache()
    monkeypatch.setattr(summary_service, "cache", kept)
    monkeypatch.setattr(summary_service, "users_by_answer", lambda view: {"c1": {}})
    view = SimpleNamespace(task_id="t1", rows_version=2, config_ids=["c1"], live=live)
    assert summary_service.kept_users_by_answer(view) == {"c1": {}}
    assert list(kept.timeouts.values()) == [timeout]
    # Kept as long as the summaries it is part of.
    finished = instant_eval_scale.FINISHED_SUMMARY_TTL
    assert summary_service.USERS_BY_ANSWER_TTL == finished


@pytest.mark.django_db
def test_reading_another_eval_task_creates_no_run(auth_client, observe_project):
    """Summary, rows and export read Instant eval runs only: a read of an
    ordinary (here continuous) eval task is a 404 and writes no run, so it
    can never count against the org's runs in flight."""
    task = EvalTask.objects.create(
        project=observe_project,
        name="Continuous monitor",
        filters={"project_id": str(observe_project.id)},
        sampling_rate=100,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.RUNNING,
    )
    params = {
        "project_id": str(observe_project.id),
        "eval_task_id": str(task.id),
        "tz": "UTC",
    }
    for url in (SUMMARY, ROWS):
        assert auth_client.get(url, params).status_code == status.HTTP_404_NOT_FOUND
    assert not InstantEvalRun.objects.filter(eval_task=task).exists()


def test_legacy_time_zone_names_reach_sql_by_their_canonical_name(monkeypatch):
    """Chrome reports Asia/Calcutta; Postgres on current Debian images only
    knows Asia/Kolkata, so every summary read failed with a 503."""
    from tracer.services import instant_eval_summary as summary_service

    assert summary_service.zone("Asia/Calcutta").key == "Asia/Kolkata"
    assert summary_service.zone("US/Pacific").key == "America/Los_Angeles"
    assert summary_service.zone(None).key == "UTC"
    # A name Postgres can't convert to is a 400, not a summary that never loads.
    monkeypatch.setattr(summary_service, "_postgres_knows", lambda name: False)
    with pytest.raises(summary_service.BadFilter):
        summary_service.zone("Europe/Paris")


@pytest.mark.django_db
def test_the_summary_reads_with_a_legacy_browser_time_zone(
    auth_client, observe_project, answered_run
):
    task, _ = answered_run
    response = auth_client.get(
        SUMMARY,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "tz": "Asia/Calcutta",
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.content
    assert InstantEvalRun.objects.get(eval_task=task).last_tz == "Asia/Kolkata"


def test_a_landed_entry_of_another_eval_task_costs_no_instant_eval_sql(monkeypatch):
    from tracer.services import instant_eval_answers
    from tracer.services.eval_tasks import entries

    copied = []
    monkeypatch.setattr(instant_eval_answers, "record_answer", copied.append)
    monkeypatch.setattr(instant_eval_answers, "is_instant_task", lambda t: t == "run")
    entries._record_instant_answer("entry-1", "continuous-task")
    entries._record_instant_answer("entry-2", None)
    assert copied == []
    entries._record_instant_answer("entry-3", "run")
    assert copied == ["entry-3"]


@pytest.mark.django_db
def test_whether_a_task_is_an_instant_eval_run_is_asked_once(
    answered_run, django_assert_num_queries
):
    import uuid

    from tracer.services import instant_eval_answers

    task, _ = answered_run
    instant_eval_answers._INSTANT_TASKS.clear()
    with django_assert_num_queries(1):
        assert instant_eval_answers.is_instant_task(task.id)
    with django_assert_num_queries(0):
        assert instant_eval_answers.is_instant_task(task.id)
    other = uuid.uuid4()
    with django_assert_num_queries(1):
        assert not instant_eval_answers.is_instant_task(other)
    with django_assert_num_queries(0):
        assert not instant_eval_answers.is_instant_task(other)
