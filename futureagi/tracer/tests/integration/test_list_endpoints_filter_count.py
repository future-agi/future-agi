"""For every FilterCase: seed the corpus, hit the list endpoint with the filter,
assert response total_rows equals the predicate-derived count.

The matrix carries placeholder UUIDs for eval_config_id / annotation_label_id;
we rebind to the real seeded values at parametrize-time inside the test body.
"""
import json

import pytest

from tracer.tests.integration._filter_matrix import all_cases_for
from tracer.tests.integration._seed import SeededRow

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.django_db]


ENDPOINTS = {
    "spans": "/tracer/observation-span/list_spans_observe/",
    "traces": "/tracer/trace/list_traces_of_session/",
    "sessions": "/tracer/trace-session/list_sessions/",
    "voiceCalls": "/tracer/trace/list_voice_calls/",
}


def _expected_count(case, seeded: list[SeededRow]) -> int:
    """Aggregate per-span predicate to the case's target unit."""
    if case.target_type == "spans":
        return sum(1 for r in seeded if case.expected_predicate(r))
    if case.target_type == "voiceCalls":
        return sum(1 for r in seeded if case.expected_predicate(r))
    if case.target_type == "traces":
        return len({r.trace_id for r in seeded if case.expected_predicate(r)})
    if case.target_type == "sessions":
        return len({r.session_id for r in seeded if case.expected_predicate(r)})
    raise AssertionError(case.target_type)


# We need eval_config_id / annotation_label_id to be filled in for EVAL_METRIC
# and ANNOTATION cases. Parametrize uses placeholder UUIDs; the fixture
# rebinds them before each test.
_PLACEHOLDER_EVAL_CFG = "00000000-0000-0000-0000-000000000001"
_PLACEHOLDER_LABEL = "00000000-0000-0000-0000-000000000002"
_PLACEHOLDER_CHOICE_CFG = "00000000-0000-0000-0000-000000000003"
_ALL_CASES = all_cases_for(
    _PLACEHOLDER_EVAL_CFG, _PLACEHOLDER_LABEL, _PLACEHOLDER_CHOICE_CFG
)


def _rebind_case(case, eval_config_id: str, label_id: str, choice_eval_config_id: str):
    """If a case targets a placeholder eval_config / label, rewrite the
    column_id to the real seeded id before sending to the endpoint. The
    predicate doesn't read column_id, only row fields, so it stays valid."""
    from tracer.tests.integration._filter_matrix import FilterCase

    new_col_id = case.column_id
    if case.col_type == "EVAL_METRIC":
        if case.column_id == _PLACEHOLDER_EVAL_CFG:
            new_col_id = eval_config_id
        elif case.column_id == _PLACEHOLDER_CHOICE_CFG:
            new_col_id = choice_eval_config_id
    if case.col_type == "ANNOTATION" and case.column_id == _PLACEHOLDER_LABEL:
        new_col_id = label_id
    if new_col_id == case.column_id:
        return case
    return FilterCase(
        case_id=case.case_id,
        target_type=case.target_type,
        col_type=case.col_type,
        filter_type=case.filter_type,
        filter_op=case.filter_op,
        column_id=new_col_id,
        filter_value=case.filter_value,
        expected_predicate=case.expected_predicate,
    )


@pytest.mark.parametrize("case", _ALL_CASES, ids=lambda c: c.case_id)
def test_list_endpoint_total_rows(
    case, auth_client, seeded_corpus, voice_corpus, ch_routes_on
):
    # voiceCalls → voice-only project; everything else → mixed corpus.
    corpus = voice_corpus if case.target_type == "voiceCalls" else seeded_corpus
    project = corpus.project
    seeded = corpus.rows
    case = _rebind_case(
        case,
        corpus.eval_config_id,
        corpus.annotation_label_id,
        corpus.choice_eval_config_id,
    )
    expected = _expected_count(case, seeded)

    filter_json = json.dumps(case.to_filter_dict()["filters"])
    url = ENDPOINTS[case.target_type]
    resp = auth_client.get(
        url,
        {
            "project_id": str(project.id),
            "page_number": 0,
            "page_size": 100,
            "filters": filter_json,
        },
    )
    assert resp.status_code == 200, getattr(resp, "content", resp)
    body = resp.data
    # success_response wraps payload in {"status":..., "result": <payload>}
    payload = body.get("result", body)
    total = payload.get("metadata", {}).get("total_rows")
    if total is None:
        # Some endpoints (sessions list) return total_rows at a different
        # path — fall back to len(table)/data length as a best-effort.
        table = payload.get("table") or payload.get("data") or []
        total = len(table)
    assert total == expected, (
        f"{case.case_id}: endpoint returned {total}, predicate expected {expected}"
    )
