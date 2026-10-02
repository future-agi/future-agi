"""Regression coverage for continuous candidate SQL exceeding the parser cap."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client
from clickhouse_driver.errors import ServerException

from tracer.selectors.eval_tasks import continuous_candidates as candidates

pytestmark = pytest.mark.unit


def _identities(count: int):
    return tuple(
        (str(uuid.UUID(int=index + 1)), f"{index:016x}", 1_789_110_000_000_000 + index)
        for index in range(count)
    )


def _budget():
    return candidates._ReadBudget(candidates.time.monotonic() + 30)


class _SizeLimitedAnalytics:
    """Encode SQL with the production driver and enforce a parser byte limit."""

    def __init__(self, max_query_bytes=262_144):
        self.client = Client("localhost")
        self.max_query_bytes = max_query_bytes
        self.calls = []
        self.accepted = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        sql = self.client.substitute_params(
            query, params, self.client.connection.context
        )
        self.calls.append((len(sql.encode("utf-8")), params))
        assert timeout_ms > 0
        assert "max_query_size" not in settings
        if self.calls[-1][0] > self.max_query_bytes:
            raise ServerException("Max query size exceeded", code=62)
        self.accepted.extend(params["changed_span_identities"])
        return SimpleNamespace(
            data=[
                {"trace_id": trace_id, "id": span_id, "session_id": candidates.NIL_UUID}
                for trace_id, span_id, _ in params["changed_span_identities"]
            ]
        )


def _expand(analytics, identities, budget=None):
    return candidates._expand_changed_span_identities(
        analytics,
        project_id="00000000-0000-0000-0000-000000000001",
        identities=identities,
        budget=budget or _budget(),
    )


def test_full_candidate_window_fits_parser_limit_without_losing_identities():
    identities = _identities(10_000)
    analytics = _SizeLimitedAnalytics()
    budget = _budget()

    result = _expand(analytics, identities, budget)

    assert set(result) == {
        (trace_id, span_id, candidates.NIL_UUID) for trace_id, span_id, _ in identities
    }
    assert sorted(analytics.accepted) == sorted(identities)
    assert all(size < 262_144 for size, _ in analytics.calls)
    assert 1 < budget.attempts == len(analytics.calls) < candidates._MAX_QUERY_ATTEMPTS


def test_long_escaped_identifiers_split_only_rejected_batches():
    identities = tuple(
        (trace_id + "'\\雪" * 400, span_id, start)
        for trace_id, span_id, start in _identities(100)
    )
    analytics = _SizeLimitedAnalytics(max_query_bytes=32_768)
    budget = _budget()

    result = _expand(analytics, identities, budget)

    assert len(result) == len(identities)
    assert sorted(analytics.accepted) == sorted(identities)
    assert any(size > 32_768 for size, _ in analytics.calls)
    assert budget.attempts == len(analytics.calls)


def test_versions_across_batches_keep_distinct_public_relations(monkeypatch):
    monkeypatch.setattr(candidates, "_SPAN_IDENTITY_BATCH_SIZE", 1)
    identity = _identities(1)[0]
    analytics = _SizeLimitedAnalytics()

    result = _expand(analytics, (identity, (*identity[:2], identity[2] + 1)))

    assert result == [(identity[0], identity[1], candidates.NIL_UUID)]
    assert len(analytics.calls) == 2


def test_combined_batches_still_enforce_candidate_cap(monkeypatch):
    monkeypatch.setattr(candidates, "_SPAN_IDENTITY_BATCH_SIZE", 2)
    monkeypatch.setattr(candidates, "_MAX_PUBLIC_CANDIDATES", 3)

    with pytest.raises(candidates.ContinuousCandidateOverflow):
        _expand(_SizeLimitedAnalytics(), _identities(4))


def test_later_batch_failure_never_returns_partial_candidates(monkeypatch):
    monkeypatch.setattr(candidates, "_SPAN_IDENTITY_BATCH_SIZE", 2)
    analytics = _SizeLimitedAnalytics()
    execute = analytics.execute_ch_query

    def fail_second(query, params, **kwargs):
        if analytics.calls:
            raise TimeoutError("second batch unavailable")
        return execute(query, params, **kwargs)

    monkeypatch.setattr(analytics, "execute_ch_query", fail_second)
    with pytest.raises(candidates.ContinuousCandidateReadError):
        _expand(analytics, _identities(3))
    assert len(analytics.accepted) == 2


def test_all_batches_share_the_original_query_budget(monkeypatch):
    monkeypatch.setattr(candidates, "_SPAN_IDENTITY_BATCH_SIZE", 1)
    analytics = _SizeLimitedAnalytics()
    budget = _budget()
    budget.attempts = candidates._MAX_QUERY_ATTEMPTS - 1

    with pytest.raises(candidates.ContinuousCandidateQueryCapExceeded):
        _expand(analytics, _identities(2), budget)

    assert len(analytics.calls) == 1
    assert budget.attempts == candidates._MAX_QUERY_ATTEMPTS


def test_single_oversized_identity_fails_without_infinite_retry():
    analytics = _SizeLimitedAnalytics(max_query_bytes=1)

    with pytest.raises(ServerException, match="Max query size exceeded"):
        _expand(analytics, _identities(1))

    assert len(analytics.calls) == 1


def test_unrelated_syntax_error_is_not_split(monkeypatch):
    analytics = _SizeLimitedAnalytics()

    def syntax_error(*args, **kwargs):
        raise ServerException("Syntax error: unexpected token", code=62)

    monkeypatch.setattr(analytics, "execute_ch_query", syntax_error)
    with pytest.raises(ServerException, match="unexpected token"):
        _expand(analytics, _identities(2))


def test_empty_identity_set_issues_no_queries():
    analytics = _SizeLimitedAnalytics()
    assert _expand(analytics, ()) == []
    assert analytics.calls == []


_STAGES = ("relations", "sessions", "voice_roots", "end_users", "sampling")


class _StageAnalytics(_SizeLimitedAnalytics):
    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        sql = self.client.substitute_params(
            query, params, self.client.connection.context
        )
        self.calls.append((len(sql.encode("utf-8")), params))
        assert timeout_ms > 0
        assert "max_query_size" not in settings
        if self.calls[-1][0] > self.max_query_bytes:
            raise ServerException("Max query size exceeded", code=62)
        if "relation_session_ids" in params:
            ids = params["relation_session_ids"]
            rows = self._relations(ids)
        elif "candidate_end_user_ids" in params:
            ids = params["candidate_end_user_ids"]
            rows = self._relations(ids)
        elif "candidate_trace_ids" in params:
            ids = params["candidate_trace_ids"]
            rows = [{"id": value} for value in ids]
        else:
            prefix, key = (
                ("sample_id_", "row_id")
                if "sampling_salt" in params
                else ("session_id_", "session_id")
            )
            ids = tuple(
                value for name, value in params.items() if name.startswith(prefix)
            )
            rows = [{key: value} for value in ids]
        self.accepted.extend(ids)
        return SimpleNamespace(data=rows)

    @staticmethod
    def _relations(ids):
        return [
            {"trace_id": value, "id": value, "session_id": candidates.NIL_UUID}
            for value in ids
        ]


def _run_stage(stage, analytics, ids, budget=None):
    kwargs = {"budget": budget or _budget()}
    project = "00000000-0000-0000-0000-000000000001"
    if stage == "relations":
        return candidates._expand_relation_ref_page(
            analytics,
            project_id=project,
            refs=[("", "", value) for value in ids],
            **kwargs,
        )
    if stage == "sessions":
        return candidates._resolve_session_ids(analytics, ids, **kwargs)
    if stage == "voice_roots":
        return candidates._read_root_ids_for_traces(
            analytics, project_id=project, trace_ids=ids, **kwargs
        )
    if stage == "end_users":
        return candidates._expand_end_user_ids(
            analytics, project_id=project, end_user_ids=ids, **kwargs
        )
    return candidates._sample_ids(
        analytics, ids, salt="task-id", sampling_rate=50, **kwargs
    )


def _stage_expected(stage, ids):
    if stage in {"relations", "end_users"}:
        return {(value, value, candidates.NIL_UUID) for value in ids}
    return set(ids)


@pytest.mark.parametrize("stage", _STAGES)
def test_other_candidate_stages_fit_full_window_without_losing_ids(stage):
    ids = tuple(row[0] for row in _identities(10_000))
    analytics = _StageAnalytics()
    budget = _budget()

    result = _run_stage(stage, analytics, ids, budget)

    assert set(result) == _stage_expected(stage, ids)
    assert sorted(analytics.accepted) == sorted(ids)
    assert all(size < 262_144 for size, _ in analytics.calls)
    assert budget.attempts == len(analytics.calls) < candidates._MAX_QUERY_ATTEMPTS


@pytest.mark.parametrize("stage", _STAGES)
def test_other_candidate_stages_split_only_rejected_batches(stage):
    ids = tuple(row[0] for row in _identities(1_000))
    analytics = _StageAnalytics(max_query_bytes=4_096)
    budget = _budget()

    result = _run_stage(stage, analytics, ids, budget)

    assert set(result) == _stage_expected(stage, ids)
    assert sorted(analytics.accepted) == sorted(ids)
    assert any(size > 4_096 for size, _ in analytics.calls)
    assert budget.attempts == len(analytics.calls)


@pytest.mark.parametrize("stage", _STAGES)
def test_other_candidate_stages_fail_before_returning_an_incomplete_union(stage):
    ids = tuple(row[0] for row in _identities(2_000))
    analytics = _StageAnalytics()
    budget = _budget()
    budget.attempts = candidates._MAX_QUERY_ATTEMPTS - 1

    with pytest.raises(candidates.ContinuousCandidateQueryCapExceeded):
        _run_stage(stage, analytics, ids, budget)

    assert 0 < len(analytics.accepted) < len(ids)
    assert len(analytics.calls) == 1


@pytest.mark.parametrize("stage", _STAGES)
def test_other_candidate_stages_keep_a_global_distinct_result_cap(stage, monkeypatch):
    monkeypatch.setattr(candidates, "_MAX_PUBLIC_CANDIDATES", 3)
    monkeypatch.setattr(candidates, "_CANDIDATE_ID_BATCH_SIZE", 2)
    monkeypatch.setattr(candidates, "_RELATION_PAGE_SIZE", 2)
    monkeypatch.setattr(candidates, "_SAMPLE_CHUNK", 2)
    ids = tuple(row[0] for row in _identities(4))
    analytics = _StageAnalytics()

    with pytest.raises(candidates.ContinuousCandidateOverflow):
        _run_stage(stage, analytics, ids)

    if stage != "sampling":  # Sampling rejects excess input before any reads.
        assert len(analytics.calls) == 2


@pytest.mark.parametrize("stage", _STAGES)
def test_other_candidate_stages_deduplicate_results_across_batches(stage, monkeypatch):
    monkeypatch.setattr(candidates, "_CANDIDATE_ID_BATCH_SIZE", 2)
    monkeypatch.setattr(candidates, "_RELATION_PAGE_SIZE", 2)
    monkeypatch.setattr(candidates, "_SAMPLE_CHUNK", 2)
    ids = tuple(row[0] for row in _identities(3))
    analytics = _StageAnalytics()
    execute = analytics.execute_ch_query

    def same_result_in_every_batch(query, params, **kwargs):
        response = execute(query, params, **kwargs)
        for row in response.data:
            for key in row:
                if key != "session_id" or stage == "sessions":
                    row[key] = ids[0]
        return response

    monkeypatch.setattr(analytics, "execute_ch_query", same_result_in_every_batch)

    result = _run_stage(stage, analytics, ids)

    assert len(result) == 1
    assert set(result) == _stage_expected(stage, ids[:1])
    assert len(analytics.calls) == 2


@pytest.mark.parametrize("stage", _STAGES)
def test_other_candidate_stages_skip_empty_input(stage):
    analytics = _StageAnalytics()
    assert not _run_stage(stage, analytics, ())
    assert analytics.calls == []
