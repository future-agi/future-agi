"""Versioned MiniLM vectors and F6 LSH bucket entries in ClickHouse.

DDL is deliberately separate from request handling. Provision these tables in
the operator migration path before enabling feature claims; runtime fails
closed if they are absent. PostgreSQL visibility receipts are written only
after both ClickHouse inserts can be read back.
"""

import math
import re
import uuid
from collections import defaultdict
from collections.abc import Sequence

from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.read_budget import is_clickhouse_query_size_error

VECTOR_TABLE = "f6_grouping_features"
BUCKET_TABLE = "f6_grouping_buckets"
MAX_FEATURES_PER_REPORT = 200
MAX_BUCKETS_PER_FEATURE = 8
# Digest IN-lists are substituted into the SQL text (~68 bytes each), so one
# statement per view needs room for every candidate of a large project.
MAX_QUERY_BYTES = 16 * 1024 * 1024
SOURCE_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")


def nearest_sql(pending_count: int) -> str:
    """Exact cosine top-``limit`` per pending vector in one candidate scan.

    The pending vectors are a constant, so each candidate row is read once and
    keeps one minimum distance per pending vector, and only a running top
    ``limit`` per pending vector is sorted. Cost is linear in candidates; a
    join would copy both vectors into every pair. The distance terms are
    unrolled because a lambda over ``vector`` copies it once per pending.
    No FINAL: a feature digest hashes its whole record, vector included, so a
    replicated row has the same distances and the GROUP BY folds it. Self is
    scored ``inf`` so it never takes a top-``limit`` slot. A pending digest
    missing from ClickHouse fails the statement on an array size mismatch.
    """
    distances = ", ".join(
        f"cosineDistance(vector, pending.2[{index}])"
        for index in range(1, pending_count + 1)
    )
    return f"""WITH (
    SELECT (groupArray(pending_occurrence), groupArray(pending_vector)) FROM (
        SELECT feature_digest, any(occurrence_id) AS pending_occurrence,
            any(vector) AS pending_vector
        FROM {VECTOR_TABLE}
        WHERE organization_id = %(org)s AND project_id = %(project)s
          AND view = %(view)s AND feature_digest IN %(pending)s
        GROUP BY feature_digest ORDER BY feature_digest)
) AS pending
SELECT toString(pending.1[k]) AS pending_id, nearest.2 AS candidate_id,
    nearest.1 AS distance
FROM (
    SELECT groupArraySortedForEach(%(limit)s)(arrayMap(
        (d, p) -> (if(p = occurrence_id, inf, d), toString(occurrence_id)),
        distances, pending.1)) AS ranked
    FROM (
        SELECT occurrence_id, minForEach([{distances}]) AS distances
        FROM {VECTOR_TABLE}
        WHERE organization_id = %(org)s AND project_id = %(project)s
          AND view = %(view)s AND feature_digest IN %(candidates)s
        GROUP BY occurrence_id))
ARRAY JOIN ranked AS top, arrayEnumerate(ranked) AS k
ARRAY JOIN top AS nearest
WHERE candidate_id != pending_id
ORDER BY pending_id, distance, candidate_id"""


def _bucket_key(view: str, item: dict) -> str:
    if (
        set(item) != {"table", "signature"}
        or type(item["table"]) is not int
        or type(item["signature"]) is not int
    ):
        raise ValueError("invalid LSH bucket")
    if not 0 <= item["table"] < 8 or not 0 <= item["signature"] <= 255:
        raise ValueError("invalid LSH bucket")
    return f"{view}:{item['table']}:{item['signature']}"


def grouping_feature_ddl() -> tuple[str, str]:
    """Operator-provisioned schema; no lazy request-time CREATE TABLE."""
    return (
        f"""CREATE TABLE IF NOT EXISTS {VECTOR_TABLE} (
            organization_id UUID, project_id UUID, occurrence_id UUID,
            view LowCardinality(String), source_digest String,
            text_digest String, feature_digest String,
            model LowCardinality(String), model_revision Nullable(String),
            serving_release String, dimension UInt16, vector Array(Float64),
            stored_at DateTime64(6) DEFAULT now64(6)
        ) ENGINE = ReplacingMergeTree(stored_at)
        ORDER BY (organization_id, project_id, occurrence_id, view, source_digest, feature_digest)""",
        f"""CREATE TABLE IF NOT EXISTS {BUCKET_TABLE} (
            organization_id UUID, project_id UUID, bucket_key String,
            occurrence_id UUID, view LowCardinality(String),
            source_digest String, feature_digest String,
            stored_at DateTime64(6) DEFAULT now64(6)
        ) ENGINE = ReplacingMergeTree(stored_at)
        ORDER BY (organization_id, project_id, bucket_key, occurrence_id, view, source_digest, feature_digest)""",
    )


def validate_feature_rows(*, rows: Sequence[dict], occurrence_ids: set[str]) -> None:
    if not 1 <= len(rows) <= MAX_FEATURES_PER_REPORT:
        raise ValueError("feature row count exceeds report bound")
    seen = set()
    for row in rows:
        if set(row) != {
            "occurrence_id",
            "view",
            "source_digest",
            "evidence_revision",
            "text_digest",
            "feature_digest",
            "model",
            "model_revision",
            "serving_release",
            "dimension",
            "vector",
            "index_buckets",
        }:
            raise ValueError("feature row has unknown or missing fields")
        if row["occurrence_id"] not in occurrence_ids or row["view"] not in {
            "semantics",
            "task",
        }:
            raise ValueError("feature row is not a claimed occurrence/view")
        identity = (row["occurrence_id"], row["view"])
        if identity in seen:
            raise ValueError("duplicate feature row")
        seen.add(identity)
        if (
            not SOURCE_DIGEST.fullmatch(row["source_digest"])
            or not SOURCE_DIGEST.fullmatch(row["evidence_revision"])
            or any(
                not HEX_DIGEST.fullmatch(row[key])
                for key in ("text_digest", "feature_digest")
            )
        ):
            raise ValueError("feature digest invalid")
        if row["model"] != "all-MiniLM-L6-v2" or row["model_revision"] is not None:
            raise ValueError("feature model identity is unsupported or fabricated")
        if (
            not isinstance(row["serving_release"], str)
            or not 1 <= len(row["serving_release"]) <= 128
        ):
            raise ValueError("feature serving release must be operator-pinned")
        if (
            row["dimension"] != 384
            or not isinstance(row["vector"], list)
            or len(row["vector"]) != 384
        ):
            raise ValueError("feature vector dimension mismatch")
        if not all(
            type(value) in {int, float} and math.isfinite(value)
            for value in row["vector"]
        ):
            raise ValueError("feature vector is not finite")
        if math.hypot(*row["vector"]) <= 0:
            raise ValueError("feature vector is zero")
        buckets = row["index_buckets"]
        if (
            not isinstance(buckets, list)
            or len(buckets) != MAX_BUCKETS_PER_FEATURE
            or any(not isinstance(item, dict) for item in buckets)
        ):
            raise ValueError("feature LSH bucket set is invalid")
        keys = [_bucket_key(row["view"], item) for item in buckets]
        if len(set(keys)) != 8 or {item["table"] for item in buckets} != set(range(8)):
            raise ValueError("feature LSH tables are incomplete")
    if {item for item, view in seen if view == "semantics"} != occurrence_ids:
        raise ValueError("feature report is incomplete")


class GroupingFeatureStore:
    def __init__(self, client: ClickHouseClient | None = None):
        self.client = client or ClickHouseClient()

    def write_and_verify(
        self, *, organization_id: uuid.UUID, project_id: uuid.UUID, rows: Sequence[dict]
    ) -> None:
        vectors = [
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "occurrence_id": uuid.UUID(row["occurrence_id"]),
                "view": row["view"],
                "source_digest": row["source_digest"],
                "text_digest": row["text_digest"],
                "feature_digest": row["feature_digest"],
                "model": row["model"],
                "model_revision": None,
                "serving_release": row["serving_release"],
                "dimension": 384,
                "vector": row["vector"],
            }
            for row in rows
        ]
        buckets = [
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "bucket_key": _bucket_key(row["view"], bucket),
                "occurrence_id": uuid.UUID(row["occurrence_id"]),
                "view": row["view"],
                "source_digest": row["source_digest"],
                "feature_digest": row["feature_digest"],
            }
            for row in rows
            for bucket in row["index_buckets"]
        ]
        self.client.insert(VECTOR_TABLE, vectors)
        self.client.insert(BUCKET_TABLE, buckets)
        # Read-after-write is required; PG must not mark ready after only one
        # of the two writes. The exact source/feature version is part of WHERE.
        for row in rows:
            params = {
                "org": organization_id,
                "project": project_id,
                "occurrence": uuid.UUID(row["occurrence_id"]),
                "view": row["view"],
                "source": row["source_digest"],
                "feature": row["feature_digest"],
            }
            vector_count = self.client.execute_read(
                f"SELECT count() FROM {VECTOR_TABLE} FINAL WHERE organization_id=%(org)s AND project_id=%(project)s "
                "AND occurrence_id=%(occurrence)s AND view=%(view)s AND source_digest=%(source)s "
                "AND feature_digest=%(feature)s",
                params,
            )[0][0][0]
            bucket_count = self.client.execute_read(
                f"SELECT count() FROM {BUCKET_TABLE} FINAL WHERE organization_id=%(org)s AND project_id=%(project)s "
                "AND occurrence_id=%(occurrence)s AND view=%(view)s AND source_digest=%(source)s "
                "AND feature_digest=%(feature)s",
                params,
            )[0][0][0]
            if vector_count < 1 or bucket_count < MAX_BUCKETS_PER_FEATURE:
                raise RuntimeError("grouping feature index is not yet readable")

    def read_vectors(
        self,
        *,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        receipts: Sequence[dict],
    ) -> list[dict]:
        if len(receipts) > MAX_FEATURES_PER_REPORT * 4:
            raise ValueError("feature read exceeds bound")
        if not receipts:
            return []
        identities = [
            (
                uuid.UUID(receipt["occurrence_id"]),
                receipt["view"],
                receipt["source_digest"],
                receipt["feature_digest"],
            )
            for receipt in receipts
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("feature read identities are duplicated")
        rows = self.client.execute_read(
            f"SELECT occurrence_id, view, source_digest, feature_digest, vector, dimension, "
            f"model, model_revision, serving_release FROM {VECTOR_TABLE} FINAL "
            "WHERE organization_id=%(org)s AND project_id=%(project)s "
            "AND tuple(occurrence_id, view, source_digest, feature_digest) IN %(identities)s "
            "LIMIT %(limit)s",
            {
                "org": organization_id,
                "project": project_id,
                "identities": tuple(identities),
                "limit": len(identities) + 1,
            },
        )[0]
        if len(rows) != len(identities):
            raise RuntimeError("grouping vector is missing or ambiguous")
        by_identity = {}
        for row in rows:
            key = (uuid.UUID(str(row[0])), row[1], row[2], row[3])
            vector = row[4]
            if (
                key not in identities
                or key in by_identity
                or row[5] != 384
                or not isinstance(vector, (list, tuple))
                or len(vector) != 384
                or any(
                    type(value) not in (int, float) or not math.isfinite(value)
                    for value in vector
                )
                or math.hypot(*vector) <= 0
            ):
                raise RuntimeError("grouping vector is missing or ambiguous")
            by_identity[key] = row
        if len(by_identity) != len(identities):
            raise RuntimeError("grouping vector is missing or ambiguous")
        return [
            {
                **receipt,
                "vector": list(by_identity[key][4]),
                "dimension": by_identity[key][5],
                "model": by_identity[key][6],
                "model_revision": by_identity[key][7],
                "serving_release": by_identity[key][8],
            }
            for receipt, key in zip(receipts, identities, strict=True)
        ]

    def candidate_occurrences(
        self,
        *,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        pending: Sequence[dict],
        candidates: Sequence[dict],
        limit: int,
    ) -> dict[tuple[str, str], list[str]]:
        """Exact cosine top-``limit`` candidates per pending occurrence and view.

        ``pending`` and ``candidates`` are ready feature receipts (``view`` and
        ``feature_digest``). The caller restricts candidates to eligible
        features, so the bound is the requested neighbours, not the index size.
        Each list is nearest first, ties broken by id; one statement per view.
        """
        if limit < 1:
            raise ValueError("candidate limit must be positive")
        digests = defaultdict(lambda: ([], []))
        for side, rows in ((0, pending), (1, candidates)):
            for row in rows:
                if row["view"] not in {"semantics", "task"} or not HEX_DIGEST.fullmatch(
                    row["feature_digest"]
                ):
                    raise ValueError("invalid candidate feature receipt")
                digests[row["view"]][side].append(row["feature_digest"])
        nearest = defaultdict(list)
        for view, (pending_digests, candidate_digests) in sorted(digests.items()):
            if not pending_digests or not candidate_digests:
                continue
            try:
                rows = self.client.execute_read(
                    nearest_sql(len(set(pending_digests))),
                    {
                        "org": organization_id,
                        "project": project_id,
                        "view": view,
                        "pending": tuple(pending_digests),
                        "candidates": tuple(candidate_digests),
                        "limit": limit,
                    },
                    settings={"max_query_size": MAX_QUERY_BYTES},
                )[0]
            except Exception as exc:
                if is_clickhouse_query_size_error(exc):
                    raise RuntimeError(
                        "candidate digest list exceeds ClickHouse max_query_size"
                    ) from exc
                raise
            for pending_id, candidate_id, distance in rows:
                if not math.isfinite(distance):
                    raise RuntimeError("grouping candidate distance is invalid")
                nearest[(pending_id, view)].append(candidate_id)
        return dict(nearest)
