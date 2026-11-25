"""The synchronous chart bundle degrades on a Skills replay-size refusal.

A Skills load filter compiles to an exact replay with a fixed key budget; past
it the statement raises the compiler's throwIf. The single-metric path already
maps that refusal; the bundle (project charts, SYSTEM_METRICS) must degrade the
same way instead of surfacing HTTP 500, while unrelated errors still raise.
No ClickHouse: the bundle reader is stubbed to raise the exact driver error.
"""

from __future__ import annotations

import uuid

import pytest
from clickhouse_driver.errors import ServerException

from tracer.services.clickhouse import graph_dispatch

_OVERFLOW = (
    "Code: 395. DB::Exception: Skill load replay key budget exceeded: while "
    "executing 'FUNCTION throwIf(...)'. (FUNCTION_THROW_IF_VALUE_IS_NON_ZERO)"
)


def _bundle(monkeypatch, exc):
    def raise_it(**_kwargs):
        raise exc

    monkeypatch.setattr(graph_dispatch, "read_exact_all_system_metrics", raise_it)
    return graph_dispatch.fetch_all_system_metrics_ch(
        analytics=object(),
        project_id=str(uuid.uuid4()),
        filters=[],
        interval="hour",
    )


def test_skill_replay_refusal_degrades_the_bundle(monkeypatch):
    response = _bundle(monkeypatch, ServerException(_OVERFLOW, code=395))
    # On 14bba335 this raised the driver error, which the view turns into 500.
    assert response["query_status"] == "degraded"
    # The API contract's "read too large; narrow it" code.
    assert response["query_error_code"] == "read_budget_exceeded"
    assert response["query_complete"] is False


def test_unrelated_throw_if_still_raises(monkeypatch):
    unrelated = ServerException(
        "Code: 395. DB::Exception: Value passed to 'throwIf' function is "
        "non-zero. (FUNCTION_THROW_IF_VALUE_IS_NON_ZERO)",
        code=395,
    )
    with pytest.raises(ServerException):
        _bundle(monkeypatch, unrelated)
