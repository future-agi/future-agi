"""Pure F6 ranking and exact-key ClickHouse read tests; no model or CH calls."""

import hashlib
import math
import uuid

import pytest
from clickhouse_driver.errors import ServerException

from tracer.services.grouping.context import rank_f6_occurrences
from tracer.services.grouping.feature_store import (
    LSH_EXACT_FALLBACK_THRESHOLD,
    MAX_LSH_CANDIDATES,
    MAX_QUERY_BYTES,
    GroupingFeatureStore,
    _expand_lsh_probe_keys,
    lsh_bucket_sql,
    nearest_sql,
)


def _vector(x=1.0, y=0.0):
    return [x, y, *([0.0] * 382)]


def test_f6_top20_per_view_rrf60_after_self_pair_and_eligibility_filters():
    candidates = ["seed", *(f"c{i:02}" for i in range(25))]
    ranked = rank_f6_occurrences(
        nearest={("seed", view): candidates for view in ("semantics", "task")},
        eligible_ids=set(candidates) - {"c03"},
        blocked_pairs={("seed", "c00")},
    )
    expected = [f"c{i:02}" for i in range(1, 23) if i != 3][:20]
    assert [item[0] for item in ranked] == expected
    assert ranked[0][1] == pytest.approx(2 / 61)
    assert ranked[-1][1] == pytest.approx(2 / 80)


def test_f6_index_order_precedes_id_and_rrf_fuses_views():
    ranked = rank_f6_occurrences(
        nearest={("seed", "semantics"): ["b", "a"], ("seed", "task"): ["a", "b"]},
        eligible_ids={"a", "b"},
        blocked_pairs=set(),
    )
    assert ranked == [
        ("a", pytest.approx(1 / 61 + 1 / 62)),
        ("b", pytest.approx(1 / 61 + 1 / 62)),
    ]


class _ReadClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute_read(self, sql, params, **kwargs):
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


def _digest(name):
    return hashlib.sha256(name.encode()).hexdigest()


class _NearestClient:
    """Answers nearest_sql like ClickHouse: per pending id, nearest distinct
    candidates first, ties by id, at most ``limit``."""

    def __init__(self, features, error=None):
        self.features = features
        self.error = error
        self.calls = []

    def execute_read(self, sql, params, settings=None):
        self.calls.append((sql, params, settings))
        if self.error:
            raise self.error
        rows = []
        for pending in params["pending"]:
            pending_id, _, query = self.features[pending]
            nearest = {}
            for digest in params["candidates"]:
                occurrence_id, _, vector = self.features[digest]
                if occurrence_id != pending_id:
                    distance = 1 - _cosine(query, vector)
                    nearest[occurrence_id] = min(
                        distance, nearest.get(occurrence_id, distance)
                    )
            ranked = sorted((d, o) for o, d in nearest.items())[: params["limit"]]
            rows.extend((pending_id, o, d) for d, o in ranked)
        return rows, [], 0


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True)) / (
        math.hypot(*a) * math.hypot(*b)
    )


def _features():
    angles = {"near": 0.1, "far": 1.0, "mid": 0.5, "other": 1.4, "edge": 0.7}
    features = {
        _digest("seed:semantics"): ("seed", "semantics", _vector()),
        _digest("seed:task"): ("seed", "task", _vector()),
    }
    for name, angle in angles.items():
        for view in ("semantics", "task"):
            vector = _vector(math.cos(angle), math.sin(angle))
            features[_digest(f"{name}:{view}")] = (name, view, vector)
    # A second ready feature of one occurrence must not take a second rank.
    features[_digest("near:semantics:v2")] = ("near", "semantics", _vector(0.9, 0.2))
    return features


def _receipts(features, names):
    return [
        {"view": view, "feature_digest": digest}
        for digest, (name, view, _) in features.items()
        if name in names
    ]


def test_nearest_candidates_are_one_statement_per_view_in_index_order():
    features = _features()
    client = _NearestClient(features)
    candidates = _receipts(features, {"near", "mid", "far", "other", "edge"})
    result = GroupingFeatureStore(client).candidate_occurrences(
        organization_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        pending=_receipts(features, {"seed"}),
        candidates=candidates,
        limit=3,
    )
    assert result == {
        ("seed", "semantics"): ["near", "mid", "edge"],
        ("seed", "task"): ["near", "mid", "edge"],
    }
    assert [params["view"] for _, params, _ in client.calls] == ["semantics", "task"]
    for sql, params, settings in client.calls:
        assert sql == nearest_sql(1)
        assert settings == {"max_query_size": MAX_QUERY_BYTES}
        assert params["limit"] == 3
        assert sorted(params["candidates"]) == sorted(
            row["feature_digest"] for row in candidates if row["view"] == params["view"]
        )


def test_nearest_candidates_skip_reads_without_candidates():
    features = _features()
    client = _NearestClient(features)
    assert (
        GroupingFeatureStore(client).candidate_occurrences(
            organization_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            pending=_receipts(features, {"seed"}),
            candidates=[],
            limit=40,
        )
        == {}
    )
    assert client.calls == []


@pytest.mark.parametrize(
    "receipt",
    [
        {"view": "lexical", "feature_digest": "a" * 64},
        {"view": "task", "feature_digest": "x"},
    ],
)
def test_nearest_candidates_reject_unknown_view_or_digest(receipt):
    with pytest.raises(ValueError, match="invalid candidate feature receipt"):
        GroupingFeatureStore(_NearestClient({})).candidate_occurrences(
            organization_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            pending=[receipt],
            candidates=[],
            limit=40,
        )


def test_nearest_candidates_reject_nonfinite_distance():
    client = _ReadClient([("seed", "near", float("nan"))])
    with pytest.raises(RuntimeError, match="distance is invalid"):
        GroupingFeatureStore(client).candidate_occurrences(
            organization_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            pending=[{"view": "semantics", "feature_digest": "a" * 64}],
            candidates=[{"view": "semantics", "feature_digest": "b" * 64}],
            limit=40,
        )


def test_nearest_candidates_name_an_oversized_statement():
    client = _NearestClient(
        {}, error=ServerException("Max query size exceeded at position 9", code=62)
    )
    with pytest.raises(RuntimeError, match="max_query_size"):
        GroupingFeatureStore(client).candidate_occurrences(
            organization_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            pending=[{"view": "semantics", "feature_digest": "a" * 64}],
            candidates=[{"view": "semantics", "feature_digest": "b" * 64}],
            limit=40,
        )


def test_lsh_probe_expansion_generates_exact_and_1bit_hamming_neighbors():
    keys = [f"semantics:{t}:0" for t in range(8)]
    expanded = _expand_lsh_probe_keys("semantics", keys)
    # 8 tables * (1 exact + 8 1-bit flips) = 72 unique keys
    assert len(expanded) == 72
    assert "semantics:0:0" in expanded
    assert "semantics:0:1" in expanded
    assert "semantics:0:128" in expanded
    # Ignores keys from other views or malformed inputs
    assert _expand_lsh_probe_keys("task", keys + ["bad:key"]) == []


def test_large_project_narrows_candidates_via_f6_grouping_buckets():
    seed_digest = _digest("seed:semantics")
    near_digest = _digest("near:semantics")
    mid_digest = _digest("mid:semantics")
    ineligible_digest = _digest("ineligible:semantics")
    total_candidates = LSH_EXACT_FALLBACK_THRESHOLD + 25
    features = {
        seed_digest: ("seed", "semantics", _vector(1.0, 0.0)),
        near_digest: ("near", "semantics", _vector(0.99, 0.14)),
        mid_digest: ("mid", "semantics", _vector(0.8, 0.6)),
    }
    candidates = [
        {"view": "semantics", "feature_digest": near_digest},
        {"view": "semantics", "feature_digest": mid_digest},
    ]
    for i in range(total_candidates - 2):
        d = _digest(f"bg:{i}")
        features[d] = (f"bg-{i}", "semantics", _vector(0.0, 1.0))
        candidates.append({"view": "semantics", "feature_digest": d})

    class _BucketAndNearestClient:
        def __init__(self):
            self.calls = []

        def execute_read(self, sql, params, settings=None):
            self.calls.append((sql, params, settings))
            if sql == lsh_bucket_sql():
                return [(near_digest,), (ineligible_digest,), (mid_digest,)], [], 0
            # Verify nearest_sql received ONLY eligible bucket-narrowed digests!
            assert set(params["candidates"]) == {near_digest, mid_digest}
            return [("seed", "near", 0.01), ("seed", "mid", 0.2)], [], 0

    client = _BucketAndNearestClient()
    result = GroupingFeatureStore(client).candidate_occurrences(
        organization_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        pending=[
            {
                "view": "semantics",
                "feature_digest": seed_digest,
                "bucket_keys": [f"semantics:{t}:42" for t in range(8)],
            }
        ],
        candidates=candidates,
        limit=2,
    )
    assert result == {("seed", "semantics"): ["near", "mid"]}
    assert len(client.calls) == 2
    assert client.calls[0][0] == lsh_bucket_sql()
    assert len(client.calls[0][1]["buckets"]) == 72
    assert client.calls[1][0] == nearest_sql(1)


def test_huge_project_stays_bounded_even_when_bucket_hits_are_sparse():
    seed_digest = _digest("seed:semantics")
    near_digest = _digest("near:semantics")
    candidates = [{"view": "semantics", "feature_digest": near_digest}] + [
        {"view": "semantics", "feature_digest": _digest(f"huge:{i}")}
        for i in range(MAX_LSH_CANDIDATES + 5)
    ]

    class _HugeClient:
        def __init__(self):
            self.calls = []

        def execute_read(self, sql, params, settings=None):
            self.calls.append((sql, params))
            if sql == lsh_bucket_sql():
                return [(near_digest,)], [], 0
            assert params["candidates"] == (near_digest,)
            return [("seed", "near", 0.01)], [], 0

    client = _HugeClient()
    result = GroupingFeatureStore(client).candidate_occurrences(
        organization_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        pending=[
            {
                "view": "semantics",
                "feature_digest": seed_digest,
                "bucket_keys": [f"semantics:{t}:7" for t in range(8)],
            }
        ],
        candidates=candidates,
        limit=40,
    )
    assert result == {("seed", "semantics"): ["near"]}
    assert len(client.calls) == 2
