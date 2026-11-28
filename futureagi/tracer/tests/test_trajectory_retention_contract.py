"""Cloud retention and the trajectory item store, checked in this repo's CI.

Trajectory item GC (tracer.services.clickhouse.v2.trajectory_lifecycle)
derives every cut from the ClickHouse span rows still present. It stays safe
only while Cloud retention (ee.cloud.tasks.retention) never writes ClickHouse
span versions, and requests trajectory item erasure if it ever deletes span
rows. The private package carries the same checks, but no CI runs its tests;
these run on every internal backend run, where the package is checked out.
"""

from __future__ import annotations

import ast
import inspect
import re

import pytest

_CLICKHOUSE_MODULES = (
    "clickhouse_driver",
    "clickhouse_connect",
    "tracer.services.clickhouse",
)
_SPAN_REWRITE = re.compile(r"\b(INSERT\s+INTO|ALTER\s+TABLE)\s+spans\b", re.I)
_SPAN_DELETE = re.compile(r"\bDELETE\s+FROM\s+spans\b", re.I)


def _retention_tree():
    # The Cloud scheduler lives in the private ee/cloud tree, which fork CI
    # does not check out; these tests run wherever that tree exists.
    retention = pytest.importorskip(
        "ee.cloud.tasks.retention",
        reason="the Cloud retention scheduler ships in the private ee/cloud tree",
    )
    source = inspect.getsource(retention)
    return source, ast.parse(source)


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def _string_literals(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            yield "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )


@pytest.mark.unit
def test_cloud_retention_never_rewrites_clickhouse_span_versions():
    """No new span version can replace created_at or the pointer columns."""
    _source, tree = _retention_tree()
    imported = [m for m in _imports(tree) if m.startswith(_CLICKHOUSE_MODULES)]
    assert not imported, imported
    assert not [s for s in _string_literals(tree) if _SPAN_REWRITE.search(s)]


@pytest.mark.unit
def test_cloud_retention_requests_trajectory_erasure_if_it_deletes_spans():
    source, tree = _retention_tree()
    deletes_spans = any(_SPAN_DELETE.search(s) for s in _string_literals(tree))
    if deletes_spans:
        assert "request_trajectory_erasure" in source
    else:
        # Postgres only today: ClickHouse spans, and so their items, are kept.
        assert "request_trajectory_erasure" not in source
