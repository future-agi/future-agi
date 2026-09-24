"""Versioned MiniLM vectors and F6 LSH bucket entries in ClickHouse.

DDL is deliberately separate from request handling. Provision these tables in
the operator migration path before enabling feature claims; runtime fails
closed if they are absent. PostgreSQL visibility receipts are written only
after both ClickHouse inserts can be read back.
"""

import math
import re
import uuid
from collections.abc import Sequence

from tracer.services.clickhouse.client import ClickHouseClient

VECTOR_TABLE = "f6_grouping_features"
BUCKET_TABLE = "f6_grouping_buckets"
MAX_FEATURES_PER_REPORT = 200
MAX_BUCKETS_PER_FEATURE = 8
MAX_CANDIDATE_IDS = 512
SOURCE_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
BUCKET_KEY = re.compile(
    r"^(semantics|task):[0-7]:(?:[0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$"
)


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


def probe_bucket_keys(rows: Sequence[dict]) -> list[str]:
    keys = set()
    for row in rows:
        for bucket in row["bucket_keys"]:
            table, signature = bucket["table"], bucket["signature"]
            for candidate in [signature, *(signature ^ (1 << bit) for bit in range(8))]:
                keys.add(f"{row['view']}:{table}:{candidate}")
    return sorted(keys)


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
        bucket_keys: Sequence[str],
    ) -> list[str]:
        if not 1 <= len(bucket_keys) <= 144 or any(
            not BUCKET_KEY.fullmatch(key) for key in bucket_keys
        ):
            raise ValueError("invalid bounded LSH probe keys")
        rows = self.client.execute_read(
            f"SELECT DISTINCT occurrence_id FROM {BUCKET_TABLE} FINAL "
            "WHERE organization_id=%(org)s AND project_id=%(project)s AND bucket_key IN %(keys)s "
            "ORDER BY occurrence_id LIMIT 513",
            {"org": organization_id, "project": project_id, "keys": tuple(bucket_keys)},
        )[0]
        if len(rows) > MAX_CANDIDATE_IDS:
            raise RuntimeError(
                "LSH bucket candidate limit reached; retry with bounded pagination"
            )
        return [str(row[0]) for row in rows]
