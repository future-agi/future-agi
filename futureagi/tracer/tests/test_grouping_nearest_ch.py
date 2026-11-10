"""nearest_sql on a real ClickHouse: order, ties, self, view, digests, dedupe."""

import hashlib
import uuid

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.grouping.feature_store import (
    VECTOR_TABLE,
    GroupingFeatureStore,
    grouping_feature_ddl,
)

pytestmark = pytest.mark.integration


class _NativeReads:
    """The ClickHouseClient read surface over a native test client."""

    def __init__(self, client):
        self.client = client

    def execute_read(self, sql, params=None, settings=None):
        return self.client.execute(sql, params, settings=settings), [], 0


def _vector(x, y):
    return [float(x), float(y), *([0.0] * 382)]


def _digest(name, view, version):
    return hashlib.sha256(f"{name}:{view}:{version}".encode()).hexdigest()


def test_nearest_sql_ranks_exact_cosine_per_pending_view():
    org, project = uuid.uuid4(), uuid.uuid4()
    names = ["seed", "near", "mid", "twin", "far", "outside", "other_view"]
    ids = {name: uuid.UUID(int=index + 1) for index, name in enumerate(names)}
    rows = [
        ("seed", "semantics", "v1", _vector(1, 0)),
        ("near", "semantics", "v1", _vector(0.99, 0.14)),
        # A second ready feature of one occurrence keeps only its nearest distance.
        ("near", "semantics", "v2", _vector(0.6, 0.8)),
        ("mid", "semantics", "v1", _vector(0.8, 0.6)),
        ("twin", "semantics", "v1", _vector(0.8, 0.6)),
        ("far", "semantics", "v1", _vector(0, 1)),
        # Identical to the seed, but not an eligible candidate digest.
        ("outside", "semantics", "v1", _vector(1, 0)),
        ("other_view", "task", "v1", _vector(1, 0)),
    ]
    candidates = [
        {"view": view, "feature_digest": _digest(name, view, version)}
        for name, view, version, _ in rows
        if name != "outside"
    ]
    with _ch_test_owned_database("test_f6_nearest_") as database:
        with _ch_test_native_client(
            database=database, owned_database=database
        ) as client:
            client.execute(grouping_feature_ddl()[0])
            client.execute(
                f"INSERT INTO {VECTOR_TABLE} (organization_id, project_id, "
                "occurrence_id, view, source_digest, text_digest, feature_digest, "
                "model, model_revision, serving_release, dimension, vector) VALUES",
                [
                    (
                        org,
                        project,
                        ids[name],
                        view,
                        "sha256:" + "0" * 64,
                        "0" * 64,
                        _digest(name, view, version),
                        "all-MiniLM-L6-v2",
                        None,
                        "test-release",
                        384,
                        vector,
                    )
                    for name, view, version, vector in rows
                ],
            )
            nearest = GroupingFeatureStore(_NativeReads(client)).candidate_occurrences(
                organization_id=org,
                project_id=project,
                pending=[
                    {
                        "view": "semantics",
                        "feature_digest": _digest(name, "semantics", "v1"),
                    }
                    for name in ("seed", "far")
                ],
                candidates=candidates,
                limit=4,
            )

    # Self excluded, the twin tie broken by id, the outside row and the other
    # view never scored, and the duplicate occurrence ranked once at its
    # nearest feature: v1 for the seed, v2 for the far pending vector.
    assert nearest == {
        (str(ids["seed"]), "semantics"): [
            str(ids["near"]),
            str(ids["mid"]),
            str(ids["twin"]),
            str(ids["far"]),
        ],
        (str(ids["far"]), "semantics"): [
            str(ids["near"]),
            str(ids["mid"]),
            str(ids["twin"]),
            str(ids["seed"]),
        ],
    }
