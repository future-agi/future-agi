import pytest

from ee.agenthub.trace_scanner.evidence_checks import EvidenceChecks


def test_strict_equality_and_escaped_pointer():
    checks = EvidenceChecks("trace", [{"id": "e1", "value": {"a/b": {"~": [True, 1]}}}])
    result = checks.execute(
        scope="trace",
        operation="equal",
        operands=[
            {"id": "e1", "pointer": "/a~1b/~0/0"},
            {"id": "e1", "pointer": "/a~1b/~0/1"},
        ],
    )
    assert result["status"] == "observed"
    assert result["value"] is False


def test_collect_preserves_duplicates_and_derived_sum_is_cached():
    records = [{"id": "e1", "value": [2, 3]}]
    checks = EvidenceChecks("trace", records, max_checks=2)
    records[0]["value"][0] = 100
    collected = checks.execute(
        scope="trace",
        operation="collect",
        operands=[
            {"id": "e1", "pointer": "/0"},
            {"id": "e1", "pointer": "/0"},
            {"id": "e1", "pointer": "/1"},
        ],
    )
    assert collected["value"] == [2, 2, 3]
    operands = [{"id": collected["result_id"], "pointer": ""}]
    result = checks.execute(scope="trace", operation="sum", operands=operands)
    assert result["value"] == 7
    assert result["completeness"] == "not_established"
    result["value"] = 999
    assert (
        checks.execute(scope="trace", operation="sum", operands=operands)["value"] == 7
    )
    assert len(checks.receipts) == 2
    assert (
        checks.execute(
            scope="trace", operation="read", operands=[{"id": "e1", "pointer": ""}]
        )["status"]
        == "budget_exhausted"
    )


@pytest.mark.parametrize("value", [[True, 2], ["2", 3], {"x": 1}])
def test_sum_rejects_non_numeric_values(value):
    checks = EvidenceChecks("trace", [{"id": "e1", "value": value}])
    assert (
        checks.execute(
            scope="trace", operation="sum", operands=[{"id": "e1", "pointer": ""}]
        )["status"]
        == "incompatible"
    )


def test_unavailable_is_not_a_negative_observation():
    checks = EvidenceChecks("trace", [{"id": "e1", "value": [None]}])
    assert (
        checks.execute(
            scope="trace", operation="read", operands=[{"id": "e1", "pointer": "/1"}]
        )["status"]
        == "unavailable"
    )
    assert (
        checks.execute(
            scope="trace", operation="read", operands=[{"id": "e1", "pointer": "/0"}]
        )["value"]
        is None
    )
    with pytest.raises(ValueError, match="scope"):
        checks.execute(
            scope="other", operation="read", operands=[{"id": "e1", "pointer": ""}]
        )
    with pytest.raises(ValueError, match="literal"):
        checks.execute(
            scope="trace", operation="equal", operands=[{"value": 1}, {"value": 2}]
        )


def test_set_difference_preserves_strict_types():
    checks = EvidenceChecks("trace", [{"id": "e1", "value": [[True, 1, 1, 2], [True]]}])
    result = checks.execute(
        scope="trace",
        operation="set_difference",
        operands=[{"id": "e1", "pointer": "/0"}, {"id": "e1", "pointer": "/1"}],
    )
    assert result["value"] == [1, 2]
