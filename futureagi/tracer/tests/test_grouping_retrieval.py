"""Pure F6 ranking and exact-key ClickHouse read tests; no model or CH calls."""

import uuid

import pytest

from tracer.services.grouping.context import rank_f6_occurrences
from tracer.services.grouping.feature_store import GroupingFeatureStore


def _vector(x=1.0, y=0.0):
    return [x, y, *([0.0] * 382)]


def _feature(occurrence_id, view, vector):
    return {
        "occurrence_id": occurrence_id,
        "view": view,
        "dimension": 384,
        "vector": vector,
    }


def test_f6_cosine_top20_per_view_rrf60_and_id_tie_after_pair_filter():
    pending = [_feature("seed", view, _vector()) for view in ("semantics", "task")]
    candidates = [
        _feature(f"c{i:02}", view, _vector())
        for i in range(25)
        for view in ("semantics", "task")
    ]
    ranked = rank_f6_occurrences(
        pending_features=pending,
        candidate_features=candidates,
        blocked_pairs={("seed", "c00")},
    )
    assert [item[0] for item in ranked] == [f"c{i:02}" for i in range(1, 21)]
    assert ranked[0][1] == pytest.approx(2 / 61)
    assert ranked[-1][1] == pytest.approx(2 / 80)


def test_f6_cosine_precedes_id_and_rrf_fuses_views():
    pending = [_feature("seed", view, _vector()) for view in ("semantics", "task")]
    candidates = [
        _feature("a", "semantics", _vector(0.8, 0.6)),
        _feature("b", "semantics", _vector()),
        _feature("a", "task", _vector()),
        _feature("b", "task", _vector(0.8, 0.6)),
    ]
    ranked = rank_f6_occurrences(
        pending_features=pending, candidate_features=candidates, blocked_pairs=set()
    )
    assert ranked == [
        ("a", pytest.approx(1 / 61 + 1 / 62)),
        ("b", pytest.approx(1 / 61 + 1 / 62)),
    ]


class _ReadClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute_read(self, sql, params):
        self.calls.append((sql, params))
        return self.rows, [], 0


def _receipt(occurrence_id, view="semantics"):
    return {
        "occurrence_id": str(occurrence_id),
        "view": view,
        "source_digest": "sha256:" + "a" * 64,
        "feature_digest": "b" * 64,
    }


def _row(receipt):
    return (
        uuid.UUID(receipt["occurrence_id"]),
        receipt["view"],
        receipt["source_digest"],
        receipt["feature_digest"],
        _vector(),
        384,
        "all-MiniLM-L6-v2",
        None,
        "test-release",
    )


def test_exact_key_vector_batch_preserves_receipt_order_and_scope():
    receipts = [_receipt(uuid.uuid4()), _receipt(uuid.uuid4())]
    client = _ReadClient([_row(receipts[1]), _row(receipts[0])])
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    result = GroupingFeatureStore(client).read_vectors(
        organization_id=org_id, project_id=project_id, receipts=receipts
    )
    assert [row["occurrence_id"] for row in result] == [
        item["occurrence_id"] for item in receipts
    ]
    assert len(client.calls) == 1
    sql, params = client.calls[0]
    assert (
        "tuple(occurrence_id, view, source_digest, feature_digest) IN %(identities)s"
        in sql
    )
    assert params["org"] == org_id and params["project"] == project_id
    assert params["limit"] == 3
    assert len(params["identities"]) == 2


@pytest.mark.parametrize("mode", ["missing", "duplicate", "wrong_source", "nonfinite"])
def test_exact_key_vector_batch_rejects_missing_ambiguous_or_corrupt_rows(mode):
    receipt = _receipt(uuid.uuid4())
    row = _row(receipt)
    rows = {
        "missing": [],
        "duplicate": [row, row],
        "wrong_source": [(*row[:2], "sha256:" + "c" * 64, *row[3:])],
        "nonfinite": [(*row[:4], _vector(float("nan")), *row[5:])],
    }[mode]
    client = _ReadClient(rows)
    with pytest.raises(RuntimeError, match="missing or ambiguous"):
        GroupingFeatureStore(client).read_vectors(
            organization_id=uuid.uuid4(), project_id=uuid.uuid4(), receipts=[receipt]
        )
