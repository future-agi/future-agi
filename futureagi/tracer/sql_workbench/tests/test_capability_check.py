"""The published corpus is exhaustive and its matrix fails on mismatches.

Execution proof is in test_clickhouse; these are offline contract checks.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from tracer.sql_workbench.catalog import Scope
from tracer.sql_workbench.compiler import UnsafeQuery, compile_query


def _checker():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/sql_workbench/capability_check.py"
    )
    spec = importlib.util.spec_from_file_location("sql_capability_check_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _checker()


@pytest.mark.parametrize("case", CHECKER.QUERIES, ids=lambda case: case[0])
def test_capability_corpus_compile_contract(case):
    _, _, sql, expected = case
    end = datetime(2026, 10, 21, tzinfo=UTC)
    scope = Scope(
        uuid4(),
        uuid4(),
        uuid4(),
        end - timedelta(days=1),
        end,
        (uuid4(),),
        database="analytics",
    )
    if expected == CHECKER.REJECTED:
        with pytest.raises(UnsafeQuery):
            compile_query(sql, scope)
    else:
        assert compile_query(sql, scope)


def test_matrix_requires_every_case_and_reports_failure():
    cases = CHECKER.QUERIES + CHECKER.LIFECYCLE
    assert len(CHECKER.case_ids()) == len(set(CHECKER.case_ids()))
    results = {case[0]: {"status": case[3]} for case in cases}
    assert CHECKER.matrix(results, {})["matches_expectations"]
    results[cases[0][0]] = {"status": CHECKER.FAILED, "reason": "server refused"}
    matrix = CHECKER.matrix(results, {})
    assert not matrix["matches_expectations"]
    assert matrix["cases"][0]["reason"] == "server refused"
    del results[cases[-1][0]]
    with pytest.raises(ValueError, match="Every corpus case"):
        CHECKER.matrix(results, {})
