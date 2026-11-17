"""Seeded real-read regression for a row source with more than one page of turns."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from model_hub.tests.test_row_source_reads import _observe, _pg_row, _record
from model_hub.tests.test_row_source_reads import tenants as tenants
from tracer.tests._ch_seed import seed_ch_spans, seed_ch_trace_sessions

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def test_later_persisted_turn_is_reachable_without_competing_project_turns(tenants):
    """The exact session endpoint consumed by the row UI preserves page 51."""
    a, _, clients = tenants
    project, session, trace, span = _observe(a, session=True)
    sibling, _, _, sibling_span = _observe(a)
    _record(a, a["rows"][0], "session", project, trace, span, session)
    seed_ch_trace_sessions(
        [
            session,
            SimpleNamespace(
                id=session.id,
                project_id=sibling.id,
                name=session.name,
                created_at=session.created_at,
            ),
        ]
    )
    start = timezone.now() - timedelta(minutes=5)
    rows = []
    expected = []
    for index in range(51):
        values = {
            "trace_id": str(uuid.uuid4()),
            "input": f"persisted-question-{index + 1}",
            "output": f"persisted-answer-{index + 1}",
        }
        row = {
            **_pg_row(span),
            **values,
            "id": uuid.uuid4().hex[:16],
            "parent_span_id": None,
            "trace_session_id": str(session.id),
            "start_time": start + timedelta(seconds=index),
            "end_time": start + timedelta(seconds=index, milliseconds=50),
        }
        rows.append(row)
        expected.append(values)
    # A competing record with the SAME internal session id in a sibling project
    # must not enter either page. Writes stay in the existing fixture loader.
    rows.append(
        {
            **_pg_row(sibling_span),
            "trace_session_id": str(session.id),
            "input": "competing-project-question",
            "output": "competing-project-answer",
            "start_time": start - timedelta(seconds=1),
            "end_time": start,
        }
    )
    seed_ch_spans(rows)
    path = f"/tracer/trace-session/{session.id}/query/"
    pages = []
    for page_number in (0, 1):
        response = clients["a"].post(
            path, {"page_number": page_number, "page_size": 50}, format="json"
        )
        assert response.status_code == 200, response.content
        pages.append(response.json()["result"])
    assert [len(page["response"]) for page in pages] == [50, 1]
    assert [page["next"] for page in pages] == [True, False]
    assert [
        {key: turn[key] for key in ("trace_id", "input", "output")}
        for page in pages
        for turn in page["response"]
    ] == expected
    assert pages[1]["response"][0]["output"] == "persisted-answer-51"
    refused = clients["b"].post(
        path, {"page_number": 1, "page_size": 50}, format="json"
    )
    assert refused.status_code in (400, 403, 404)
    assert b"persisted-question" not in refused.content
    assert b"persisted-answer" not in refused.content
