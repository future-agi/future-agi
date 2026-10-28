"""A user question counts each user once, under one answer per question:
the most common answer among the sessions Jev answered, a tie going to the
tied answer whose latest session started last, as a low-confidence answer.
The summary, the rows and their filters, the exports and History all read
that one rollup."""

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.instant_eval import InstantEvalRow, InstantEvalRun
from tracer.models.observation_span import EvalLogger
from tracer.services.instant_eval_answers import record_answer
from tracer.tests.test_instant_eval_scale import (
    EXPORT,
    ROWS,
    SUMMARY,
    _answer,
    _question,
)
from tracer.tests.test_instant_eval_scale import answered_run as answered_run
from tracer.tests.test_instant_eval_scale import category as category
from tracer.tests.test_instant_eval_scale import fake_view as fake_view
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.tests.test_instant_eval_scale import started as started
from tracer.tests.test_instant_eval_scale import yes_no as yes_no
from tracer.views.instant_eval import INSTANT_TASK_PREFIX

RUNS = "/tracer/instant-eval/runs/"
START = datetime(2026, 10, 6, 9, tzinfo=UTC)
ORDER, REFUND, ELSE = "Order status", "Return or refund", "Something else"

# (user, minutes after START, yes/no answer, category answer). An answer of
# "error" is one Jev couldn't give; None is one not given yet.
SESSIONS = [
    # Two No against one Yes.
    ("ana", 0, "No", ORDER),
    ("ana", 60, "Yes", ORDER),
    ("ana", 120, "No", REFUND),
    # 1–1 ties: the later session's answer, whatever the alphabet says.
    ("ben", 0, "Yes", ORDER),
    ("ben", 30, "No", REFUND),
    ("cy", 0, "No", REFUND),
    ("cy", 30, "Yes", ORDER),
    # 2–2–1: the tied answer whose latest session is later, not the newest
    # session's own answer.
    ("dee", 0, "Yes", ORDER),
    ("dee", 10, "Yes", ORDER),
    ("dee", 20, "Yes", REFUND),
    ("dee", 30, "Yes", REFUND),
    ("dee", 40, "Yes", ELSE),
    # A session Jev couldn't answer doesn't vote.
    ("eve", 0, "error", "error"),
    ("eve", 10, "error", ORDER),
    ("eve", 20, "Yes", ORDER),
    # No session answered: couldn't be answered.
    ("fay", 0, "error", "error"),
    ("fay", 5, "error", "error"),
    # No in every session read.
    *(("gus", 10 * i, "No", REFUND) for i in range(5)),
]
EXPECTED_YES_NO = {
    "ana": "No",
    "ben": "No",
    "cy": "Yes",
    "dee": "Yes",
    "eve": "Yes",
    "fay": "__error__",
    "gus": "No",
}
EXPECTED_CATEGORY = {
    "ana": ORDER,
    "ben": REFUND,
    "cy": ORDER,
    "dee": REFUND,
    "eve": ORDER,
    "fay": "__error__",
    "gus": REFUND,
}


def _uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))


def _users_run(project, configs, sessions, *, status=EvalTaskStatus.COMPLETED):
    """A user question's run over ``sessions``, its answers recorded as
    they landed."""
    for config in configs:
        config.config["instant_eval"]["unit"] = "users"
        config.save(update_fields=["config"])
    task = EvalTask.objects.create(
        project=project,
        name=f"{INSTANT_TASK_PREFIX} · users",
        filters={},
        sampling_rate=100,
        run_type=RunType.HISTORICAL,
        row_type="sessions",
        status=status,
    )
    task.evals.set(configs)
    users = {name for name, *_ in sessions}
    run = InstantEvalRun.objects.create(
        eval_task=task,
        project=project,
        organization=project.organization,
        unit="users",
        row_type="sessions",
        population=len(users),
        rows_requested=len(users),
        questions=len(configs),
        credits_estimate=0,
        answers_synced=True,
    )
    logs = []
    for rank, (name, minutes, *answers) in enumerate(sessions):
        sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}-{minutes}"))
        InstantEvalRow.objects.create(
            task_id=task.id,
            row_id=sid,
            unit="sessions",
            sample_rank=rank,
            time=START + timedelta(minutes=minutes),
            end_user_id=_uid(name),
            user_label=name,
            model="gpt-4.1" if minutes % 20 else "gpt-4.1-mini",
            turns=2,
            error=name == "ana" and minutes == 0,
            excerpt=f"{name} at {minutes}",
        )
        for config, label in zip(configs, answers, strict=True):
            logs.append(
                _answer(
                    task,
                    config,
                    sid,
                    None if label in ("error", None) else label,
                    error=label == "error",
                    pending=label is None,
                    confidence=0.4 if name == "cy" else 0.9,
                )
            )
    EvalLogger.objects.bulk_create(logs)
    for entry in logs:
        record_answer(entry.id)
    return task, run


@pytest.fixture
def category_q(observe_project, eval_template):
    return _question(
        observe_project,
        eval_template,
        "What did the customer come for?",
        "category",
        [ORDER, REFUND, ELSE],
    )


@pytest.fixture
def yes_no_q(observe_project, eval_template):
    return _question(observe_project, eval_template, "Did the agent resolve it?")


@pytest.fixture
def users_run(observe_project, yes_no_q, category_q):
    return _users_run(observe_project, [yes_no_q, category_q], SESSIONS)


def _get(client, path, project, task, **params):
    response = client.get(
        path,
        {"project_id": str(project.id), "eval_task_id": str(task.id), **params},
    )
    assert response.status_code == 200, response.content
    return response


def _rows(client, project, task, **params):
    params = {"page_size": 100, **params}
    return _get(client, ROWS, project, task, **params).json()["result"]


def _csv(response) -> list[list[str]]:
    return list(csv.reader(io.StringIO(b"".join(response.streaming_content).decode())))


@pytest.mark.django_db
def test_a_user_counts_once_under_their_rolled_up_answer(
    auth_client, observe_project, yes_no_q, category_q, users_run
):
    task, _ = users_run
    s = _get(auth_client, SUMMARY, observe_project, task).json()["result"]
    yn, cat = str(yes_no_q.id), str(category_q.id)
    assert (s["total"], s["answered"], s["failed"], s["pending"]) == (7, 6, 1, 0)
    assert s["sessions_read"] == len(SESSIONS)
    expected = {}
    for answer in EXPECTED_YES_NO.values():
        expected[answer] = expected.get(answer, 0) + 1
    assert {k: v for k, v in s["counts"][yn].items() if v} == expected
    for cfg in (yn, cat):
        assert sum(s["counts"][cfg].values()) == 7
    # Each user sits once on the timeline and the heatmap, at their latest
    # session, and once in each facet.
    assert sum(b["n"] for b in s["timeline"]["bins"]) == 7
    assert sum(g["n"] for g in s["groups"]["day_hour"]) == 7
    assert sum(g["n"] for g in s["groups"]["model"]) == 7
    assert {g["key"]: g["n"] for g in s["groups"]["error"]} == {"error": 1, "ok": 6}
    assert s["groups"]["user"] == []
    # Users answered both questions, once each.
    cross = s["cross"][yn][cat]
    assert sum(n for by in cross.values() for n in by.values()) == 6
    assert cross["No"] == {ORDER: 1, REFUND: 2}
    # Unsure: the sessions behind cy's answer averaged 0.4 confident, and
    # ben's and cy's answers are 1–1 ties, which never read as sure.
    assert s["unsure"][yn] == {"Yes": 1, "No": 1}


@pytest.mark.django_db
def test_rows_list_each_user_with_the_answer_the_charts_count(
    auth_client, observe_project, yes_no_q, category_q, users_run
):
    task, _ = users_run
    yn, cat = str(yes_no_q.id), str(category_q.id)
    page = _rows(auth_client, observe_project, task)
    assert page["total"] == 7
    by_user = {r["user_id"]: r for r in page["rows"]}
    for name, answer in EXPECTED_YES_NO.items():
        assert by_user[name]["answers"][yn]["answer"] == answer, name
        assert by_user[name]["answers"][cat]["answer"] == EXPECTED_CATEGORY[name], name
    # The evidence behind an answer: the sessions per answer.
    assert by_user["ana"]["answers"][yn]["counts"] == {"No": 2, "Yes": 1}
    # A tie is the winning answer's share of the sessions as sure as they
    # were: half of 0.9 for ben's 1–1, two fifths for dee's 2–2–1. A clear
    # majority keeps its sessions' confidence.
    assert by_user["ben"]["answers"][yn]["confidence"] == pytest.approx(0.45)
    assert by_user["dee"]["answers"][cat]["confidence"] == pytest.approx(0.36)
    assert by_user["ana"]["answers"][yn]["confidence"] == pytest.approx(0.9)
    assert by_user["ana"]["sessions"] == 3
    assert by_user["eve"]["answers"][yn]["counts"] == {"__error__": 2, "Yes": 1}
    # The latest session gives the user's time and excerpt.
    assert by_user["gus"]["time"].startswith("2026-10-06T09:40")
    assert by_user["gus"]["excerpt"] == "gus at 40"
    assert by_user["ana"]["facets"]["error"] is True

    # Filtering by an answer selects the users the charts count under it.
    s = _get(auth_client, SUMMARY, observe_project, task).json()["result"]
    for cfg in (yn, cat):
        totals = 0
        for label, n in s["counts"][cfg].items():
            got = _rows(auth_client, observe_project, task, config_id=cfg, answer=label)
            assert got["total"] == n, (cfg, label)
            totals += got["total"]
        assert totals == 7
    no = _rows(auth_client, observe_project, task, config_id=yn, answer="No")
    assert {r["user_id"] for r in no["rows"]} == {"ana", "ben", "gus"}
    both = _rows(
        auth_client,
        observe_project,
        task,
        answers=f'[{{"config_id": "{yn}", "answer": "No"}}, '
        f'{{"config_id": "{cat}", "answer": "{REFUND}"}}]',
    )
    assert {r["user_id"] for r in both["rows"]} == {"ben", "gus"}
    unsure = _rows(auth_client, observe_project, task, unsure="true")
    assert {r["user_id"] for r in unsure["rows"]} == {"ben", "cy", "dee"}
    errored = _rows(auth_client, observe_project, task, group="error:error")
    assert [r["user_id"] for r in errored["rows"]] == ["ana"]
    # A user sits at their latest session: ana's last one is at 11:00.
    late = _rows(
        auth_client,
        observe_project,
        task,
        time_from=(START + timedelta(minutes=100)).isoformat(),
    )
    assert [r["user_id"] for r in late["rows"]] == ["ana"]
    text = _rows(auth_client, observe_project, task, q="dee at 40")
    assert [r["user_id"] for r in text["rows"]] == ["dee"]
    # Pages walk every user once, newest first.
    seen, cursor = [], None
    while True:
        page = _rows(
            auth_client,
            observe_project,
            task,
            page_size=3,
            **({"cursor": cursor} if cursor else {}),
        )
        seen += [r["user_id"] for r in page["rows"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(EXPECTED_YES_NO)


@pytest.mark.django_db
def test_users_are_counted_by_how_consistently_their_sessions_got_an_answer(
    auth_client, observe_project, yes_no_q, users_run
):
    task, _ = users_run
    yn = str(yes_no_q.id)
    s = _get(auth_client, SUMMARY, observe_project, task).json()["result"]
    consistency = s["groups"]["user_consistency"][yn]
    # Sessions answered per answer, users by sessions answered, and users at
    # k of n (n of 3 or more) per answer.
    assert consistency["sessions"] == {"No": 9, "Yes": 9, "__error__": 4}
    assert consistency["users"] == {"2": 3, "3": 2, "5": 2}
    assert consistency["tiers"]["No"] == [[5, 5, 1], [3, 2, 1]]
    assert consistency["tiers"]["Yes"] == [[5, 5, 1], [3, 1, 2]]
    # Each tier the summary counts selects exactly its users.
    for label, tiers in consistency["tiers"].items():
        for n, k, users in tiers:
            got = _rows(
                auth_client,
                observe_project,
                task,
                group=f"consistent:{yn}|{label}|{k}|{n}",
            )
            assert got["total"] == users, (label, n, k)
    page = _rows(auth_client, observe_project, task, group=f"consistent:{yn}|No|5|5")
    assert [r["user_id"] for r in page["rows"]] == ["gus"]
    either = _rows(
        auth_client,
        observe_project,
        task,
        group=[f"consistent:{yn}|No|5|5", f"consistent:{yn}|Yes|1|3"],
    )
    assert {r["user_id"] for r in either["rows"]} == {"gus", "ana", "eve"}
    bad = auth_client.get(
        ROWS,
        {
            "project_id": str(observe_project.id),
            "eval_task_id": str(task.id),
            "group": "consistent:nope",
        },
    )
    assert bad.status_code == 400


@pytest.mark.django_db
def test_a_user_question_exports_a_line_per_user_or_per_session(
    auth_client, observe_project, yes_no_q, category_q, users_run
):
    task, _ = users_run
    yn = str(yes_no_q.id)
    lines = _csv(_get(auth_client, EXPORT, observe_project, task))
    header, body = lines[0], lines[1:]
    assert len(body) == 7
    q = "Did the agent resolve it?"
    assert header[:4] == ["user_id", "end_user_id", "latest_session", "sessions_read"]
    assert header[8:14] == [
        f"{q} · answer",
        f"{q} · confidence",
        f"{q} · Yes sessions",
        f"{q} · No sessions",
        f"{q} · couldn't answer sessions",
        "What did the customer come for? · answer",
    ]
    by_user = {line[0]: line for line in body}
    assert by_user["ana"][3] == "3"
    assert by_user["ana"][8:13] == ["No", "0.9", "1", "2", "0"]
    assert by_user["fay"][8] == "__error__"
    assert by_user["eve"][10:13] == ["1", "0", "2"]
    assert {line[0]: line[8] for line in body} == EXPECTED_YES_NO
    # The sessions themselves, as a second export: every session, or the
    # sessions of the users a filter selects.
    sessions = _csv(_get(auth_client, EXPORT, observe_project, task, sessions="true"))
    assert len(sessions) - 1 == len(SESSIONS)
    assert sessions[0][:2] == ["id", "time"]
    picked = _csv(
        _get(
            auth_client,
            EXPORT,
            observe_project,
            task,
            sessions="true",
            config_id=yn,
            answer="No",
        )
    )
    assert {line[2] for line in picked[1:]} == {"ana", "ben", "gus"}
    assert len(picked) - 1 == 3 + 2 + 5
    # Users and sessions alike come newest first, as the rows table lists
    # them, or oldest first when asked.
    for export in (body, sessions[1:]):
        when = [line[2 if export is body else 1] for line in export]
        assert when == sorted(when, reverse=True)
    oldest = _csv(_get(auth_client, EXPORT, observe_project, task, sort="time"))[1:]
    assert [line[2] for line in oldest] == sorted(line[2] for line in body)


@pytest.mark.django_db
def test_history_counts_users_and_says_how_many_sessions_were_read(
    auth_client, observe_project, yes_no_q, users_run
):
    task, _ = users_run
    runs = auth_client.get(RUNS, {"project_id": str(observe_project.id)}).json()[
        "result"
    ]["runs"]
    entry = next(r for r in runs if r["task_id"] == str(task.id))
    assert (entry["unit"], entry["rows"], entry["sessions"]) == (
        "users",
        7,
        len(SESSIONS),
    )
    assert (entry["answered"], entry["pending"]) == (7, 0)
    assert {k: v for k, v in entry["counts"][str(yes_no_q.id)].items() if v} == {
        "No": 3,
        "Yes": 3,
        "__error__": 1,
    }


@pytest.mark.django_db
def test_a_user_waits_for_every_session_unless_the_run_was_cancelled(
    auth_client, observe_project, yes_no_q
):
    task, run = _users_run(
        observe_project,
        [yes_no_q],
        [("ann", 0, "No"), ("ann", 10, None), ("bo", 0, "Yes")],
        status=EvalTaskStatus.PAUSED,
    )
    yn = str(yes_no_q.id)
    s = _get(auth_client, SUMMARY, observe_project, task).json()["result"]
    assert (s["total"], s["answered"], s["pending"]) == (2, 1, 1)
    assert {k: v for k, v in s["counts"][yn].items() if v} == {"Yes": 1}
    waiting = {
        r["user_id"]: r["answers"][yn]
        for r in _rows(auth_client, observe_project, task)["rows"]
    }
    assert (waiting["ann"]["answer"], waiting["ann"]["status"]) == (None, "running")
    pending = _rows(
        auth_client, observe_project, task, config_id=yn, answer="__pending__"
    )
    assert [r["user_id"] for r in pending["rows"]] == ["ann"]

    # A cancelled run reads no more: what was answered is the answer.
    InstantEvalRun.objects.filter(id=run.id).update(
        cancelled_at=START, answers_version=run.answers_version + 1
    )
    EvalTask.objects.filter(id=task.id).update(status=EvalTaskStatus.COMPLETED)
    s = _get(auth_client, SUMMARY, observe_project, task).json()["result"]
    assert {k: v for k, v in s["counts"][yn].items() if v} == {"No": 1, "Yes": 1}
    rows = _rows(auth_client, observe_project, task)["rows"]
    assert {r["user_id"]: r["answers"][yn]["answer"] for r in rows} == {
        "ann": "No",
        "bo": "Yes",
    }


@pytest.mark.django_db
def test_search_text_is_matched_literally(auth_client, observe_project, users_run):
    """``%`` and ``_`` in the search are the characters, not LIKE wildcards:
    for a user question's users and a session question's rows alike."""
    task, _ = users_run
    # No text of the run holds either character.
    assert _rows(auth_client, observe_project, task, q="%")["total"] == 0
    assert _rows(auth_client, observe_project, task, q="_")["total"] == 0
    InstantEvalRow.objects.filter(task_id=task.id, user_label="ana").update(
        excerpt="refund 5000 off"
    )
    InstantEvalRow.objects.filter(
        task_id=task.id, user_label="ben", time=START + timedelta(minutes=30)
    ).update(excerpt="refund 50% off")
    page = _rows(auth_client, observe_project, task, q="50%")
    assert [r["user_id"] for r in page["rows"]] == ["ben"]


@pytest.mark.django_db
def test_search_text_is_matched_literally_in_rows(
    auth_client, observe_project, answered_run
):
    task, ids = answered_run
    assert _rows(auth_client, observe_project, task, q="%")["total"] == 0
    # Every row's user is "cust_<n>": the underscore itself.
    assert _rows(auth_client, observe_project, task, q="_")["total"] == 12
    InstantEvalRow.objects.filter(task_id=task.id).update(user_label="customer")
    assert _rows(auth_client, observe_project, task, q="_")["total"] == 0
    InstantEvalRow.objects.filter(task_id=task.id, row_id=ids[0]).update(
        excerpt="50% off"
    )
    InstantEvalRow.objects.filter(task_id=task.id, row_id=ids[1]).update(
        excerpt="5000 off"
    )
    page = _rows(auth_client, observe_project, task, q="50%")
    assert [r["id"] for r in page["rows"]] == [ids[0]]
    exported = _csv(_get(auth_client, EXPORT, observe_project, task, q="50%"))
    assert [line[0] for line in exported[1:]] == [ids[0]]
