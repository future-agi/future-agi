"""For every FilterCase: seed the corpus, hit the list endpoint with the filter,
assert response total_rows equals the predicate-derived count.

The matrix carries placeholder UUIDs for eval_config_id / annotation_label_id;
we rebind to the real seeded values at parametrize-time inside the test body.
"""

import json
from datetime import timedelta

import pytest

from tracer.tests.integration._filter_matrix import all_cases_for
from tracer.tests.integration._seed import _NOW, SeededRow

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.django_db]


ENDPOINTS = {
    "spans": "/tracer/observation-span/list_spans_observe/",
    "traces": "/tracer/trace/list_traces_of_session/",
    "sessions": "/tracer/trace-session/list_sessions/",
    "voiceCalls": "/tracer/trace/list_voice_calls/",
}


def _expected_count(case, seeded: list[SeededRow]) -> int:
    """Aggregate per-span predicate to the case's target unit."""
    if case.meta_kind is not None:
        return _expected_meta_count(case, seeded)
    if case.aggregate_predicate is not None:
        return _expected_aggregate_count(case, seeded)
    if case.target_type in ("spans", "voiceCalls"):
        return sum(1 for r in seeded if case.expected_predicate(r))
    if case.target_type == "traces":
        return len({r.trace_id for r in seeded if case.expected_predicate(r)})
    if case.target_type == "sessions":
        return len({r.session_id for r in seeded if case.expected_predicate(r)})
    raise AssertionError(case.target_type)


def _expected_meta_count(case, seeded: list[SeededRow]) -> int:
    """has_eval / has_annotation are trace/session-scoped: a trace matches if
    ANY child span carries the flag. Roll the per-span flag up to the target
    grain, then apply the True/False variant."""
    flag = (
        (lambda r: r.has_eval)
        if case.meta_kind == "has_eval"
        else (lambda r: r.has_annotation)
    )
    want = bool(case.filter_value)
    traces_with = {r.trace_id for r in seeded if flag(r)}
    if case.target_type in ("spans", "voiceCalls"):
        # has_eval is trace-scoped on the span grid (every span in an
        # eval-bearing trace matches); has_annotation is span-scoped (only the
        # annotated span itself). See _build_has_eval_condition (trace_id IN …)
        # vs _build_has_annotation_condition (observation_span_id IN …).
        if case.meta_kind == "has_eval":
            return sum(1 for r in seeded if (r.trace_id in traces_with) == want)
        return sum(1 for r in seeded if flag(r) == want)
    if case.target_type == "traces":
        all_ids = {r.trace_id for r in seeded}
        return len(traces_with if want else all_ids - traces_with)
    if case.target_type == "sessions":
        # Backend rolls up at the trace grain (trace_id IN/NOT IN), then to the
        # session: a session matches has_*=true if it contains a qualifying
        # trace, and has_*=false if it contains a NON-qualifying trace (e.g.
        # session 2's extra eval/annotation-free trace).
        not_with = {r.trace_id for r in seeded} - traces_with
        target = traces_with if want else not_with
        return len({r.session_id for r in seeded if r.trace_id in target})
    raise AssertionError(case.target_type)


def _expected_aggregate_count(case, seeded: list[SeededRow]) -> int:
    """Session aggregate cases: the backend aggregates over ROOT spans only
    (session_list restricts the inner scan to parent_span_id IS NULL). Group
    each session's root spans and count groups whose aggregate_predicate holds."""
    groups: dict = {}
    for r in seeded:
        if r.parent_span_id is None:
            groups.setdefault(r.session_id, []).append(r)
    return sum(1 for g in groups.values() if case.aggregate_predicate(g))


# EVAL_METRIC / ANNOTATION / annotator cases carry placeholder ids at
# parametrize-time; the test body rebinds them to the seeded values before
# sending. Every placeholder maps to a ``corpus`` attribute of the same name.
_PLACEHOLDER_EVAL_CFG = "00000000-0000-0000-0000-000000000001"
_PLACEHOLDER_LABEL = "00000000-0000-0000-0000-000000000002"
_PLACEHOLDER_CHOICE_CFG = "00000000-0000-0000-0000-000000000003"
_PLACEHOLDER_PF_CFG = "00000000-0000-0000-0000-000000000005"
_PLACEHOLDER_TEXT_LABEL = "00000000-0000-0000-0000-000000000006"
_PLACEHOLDER_THUMBS_LABEL = "00000000-0000-0000-0000-000000000007"
_PLACEHOLDER_CATEGORICAL_LABEL = "00000000-0000-0000-0000-000000000008"
_PLACEHOLDER_ANNOTATOR = "00000000-0000-0000-0000-000000000009"

# placeholder id -> corpus attribute holding the real seeded id.
_PLACEHOLDER_ATTR = {
    _PLACEHOLDER_EVAL_CFG: "eval_config_id",
    _PLACEHOLDER_LABEL: "annotation_label_id",
    _PLACEHOLDER_CHOICE_CFG: "choice_eval_config_id",
    _PLACEHOLDER_PF_CFG: "pf_eval_config_id",
    _PLACEHOLDER_TEXT_LABEL: "text_label_id",
    _PLACEHOLDER_THUMBS_LABEL: "thumbs_label_id",
    _PLACEHOLDER_CATEGORICAL_LABEL: "categorical_label_id",
    _PLACEHOLDER_ANNOTATOR: "annotator_user_id",
}
_ALL_CASES = all_cases_for(_PLACEHOLDER_EVAL_CFG, _PLACEHOLDER_LABEL)

# BaseQueryBuilder.parse_time_range defaults to utcnow()-30d when no created_at
# bound is present, which drops the fixed-anchor corpus. Mirror the FE, which
# always appends a date filter, so every request scopes to the corpus window.
_DEFAULT_WINDOW_FILTER = {
    "column_id": "created_at",
    "filter_config": {
        "filter_type": "datetime",
        "filter_op": "between",
        "col_type": "SYSTEM_METRIC",
        "filter_value": [
            (_NOW - timedelta(days=1)).isoformat(),
            (_NOW + timedelta(days=7)).isoformat(),
        ],
    },
}


def _rebind_case(case, corpus):
    """Rewrite placeholder ids (eval configs, annotation labels, annotator
    user) to the real seeded ids — in the column_id, in annotator filter
    values, and inside any extra_filters — before sending to the endpoint.
    Predicates read only row fields, so they stay valid. All other FilterCase
    fields (contract_gap, late_bound, meta_kind, aggregate_predicate,
    extra_filters) are preserved."""
    import copy
    import dataclasses

    def _real(pid):
        attr = _PLACEHOLDER_ATTR.get(pid)
        return getattr(corpus, attr) if attr else pid

    new_col_id = _real(case.column_id)
    new_value = case.filter_value
    if case.filter_type == "annotator" and isinstance(case.filter_value, list):
        new_value = [_real(v) for v in case.filter_value]

    new_extras = []
    for f in case.extra_filters:
        f = copy.deepcopy(f)
        f["column_id"] = _real(f["column_id"])
        new_extras.append(f)

    return dataclasses.replace(
        case,
        column_id=new_col_id,
        filter_value=new_value,
        extra_filters=tuple(new_extras),
    )


@pytest.mark.parametrize("case", _ALL_CASES, ids=lambda c: c.case_id)
def test_list_endpoint_total_rows(
    case, auth_client, seeded_corpus, voice_corpus, ch_routes_on
):
    # voiceCalls → voice-only project; everything else → mixed corpus.
    corpus = voice_corpus if case.target_type == "voiceCalls" else seeded_corpus
    project = corpus.project
    seeded = corpus.rows
    case = _rebind_case(case, corpus)
    # ID-only cases carry a corpus-dependent value + predicate resolved now.
    if case.late_bound is not None:
        case = _resolve_late_bound(case, seeded)
    expected = _expected_count(case, seeded)

    filters = case.to_filter_dict()["filters"]
    if case.column_id != "created_at":
        filters = filters + [_DEFAULT_WINDOW_FILTER]
    filter_json = json.dumps(filters)
    url = ENDPOINTS[case.target_type]
    # list_voice_calls uses a 1-based `page` param; the others use page_number.
    page_params = (
        {"page": 1} if case.target_type == "voiceCalls" else {"page_number": 0}
    )
    resp = auth_client.get(
        url,
        {
            "project_id": str(project.id),
            **page_params,
            "page_size": 100,
            "filters": filter_json,
        },
    )
    if resp.status_code != 200:
        # Some contract gaps surface as a CH crash (e.g. is_null on numeric
        # cost → Code 72). Treat a non-200 on a gap case as the gap.
        if case.contract_gap:
            pytest.xfail(case.contract_gap)
        raise AssertionError(f"{case.case_id}: {getattr(resp, 'content', resp)}")
    total = _read_total(resp.data)
    if case.contract_gap:
        # The gap means the endpoint disagrees with the semantic expectation.
        # If they now agree, the backend was fixed — fail so we promote the case.
        assert total != expected, (
            f"{case.case_id}: contract gap appears resolved "
            f"(endpoint and predicate both {total}) — promote this case"
        )
        pytest.xfail(case.contract_gap)
    assert total == expected, (
        f"{case.case_id}: endpoint returned {total}, predicate expected {expected}"
    )


def _read_total(body) -> int:
    # success_response wraps payload in {"status":..., "result": <payload>}
    payload = body.get("result", body)
    metadata = payload.get("metadata")
    total = metadata.get("total_rows") if isinstance(metadata, dict) else None
    if total is None:
        # list_voice_calls uses ExtendedPageNumberPagination (DRF), which
        # reports the total under `count` with rows under `results`.
        total = payload.get("count")
    if total is None:
        table = (
            payload.get("table") or payload.get("data") or payload.get("results") or []
        )
        total = len(table)
    return total


def _resolve_late_bound(case, seeded):
    import dataclasses

    filter_value, predicate = case.late_bound(seeded)
    return dataclasses.replace(
        case, filter_value=filter_value, expected_predicate=predicate
    )
