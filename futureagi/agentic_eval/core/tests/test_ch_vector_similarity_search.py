import uuid
from unittest.mock import MagicMock

import pytest

from agentic_eval.core.database.ch_vector import (
    _MAX_VECTOR_SEARCH_TOP_K,
    ClickHouseVectorDB,
)


@pytest.fixture
def vector_db():
    db = ClickHouseVectorDB.__new__(ClickHouseVectorDB)
    db.client = MagicMock()
    db.client.execute.return_value = []
    return db


@pytest.mark.parametrize("eval_id", ["not-a-uuid", "", 123, [], ["not-a-uuid"]])
def test_similarity_search_rejects_invalid_eval_uuid_without_query(vector_db, eval_id):
    result = vector_db.vector_similarity_search(
        "feedbacks",
        [0.1, 0.2],
        eval_id=eval_id,
        syn_data_flag=isinstance(eval_id, list),
    )

    assert result == []
    vector_db.client.execute.assert_not_called()


@pytest.mark.parametrize("dataset_id", ["not-a-uuid", "", 123])
def test_threshold_search_rejects_invalid_dataset_uuid_without_query(
    vector_db, dataset_id
):
    result = vector_db.vector_similarity_search_with_threshold(
        "dataset_embeddings",
        [0.1, 0.2],
        dataset_id=dataset_id,
    )

    assert result == []
    vector_db.client.execute.assert_not_called()


@pytest.mark.parametrize(
    "query_vector",
    [[], [float("nan")], [float("inf")], [True, 0.5], ["0.1", 0.2]],
)
def test_similarity_search_rejects_invalid_vectors_without_query(
    vector_db, query_vector
):
    result = vector_db.vector_similarity_search("feedbacks", query_vector)

    assert result == []
    vector_db.client.execute.assert_not_called()


def test_similarity_search_parameterizes_and_dimension_prefilters(vector_db):
    eval_uuid = uuid.uuid4()

    vector_db.vector_similarity_search(
        "feedbacks",
        [1, 2.5],
        eval_id=eval_uuid.hex.upper(),
        top_k=_MAX_VECTOR_SEARCH_TOP_K + 500,
    )

    query, params = vector_db.client.execute.call_args.args
    assert "CAST(%(query_vector)s AS Array(Float32))" in query
    assert "FROM (" in query
    assert "AND length(vector) = %(vector_dim)s" in query
    assert "eval_id = %(eval_id)s" in query
    assert "LIMIT %(top_k)s" in query
    assert str(eval_uuid) not in query
    assert params == {
        "query_vector": [1.0, 2.5],
        "vector_dim": 2,
        "top_k": _MAX_VECTOR_SEARCH_TOP_K,
        "eval_id": str(eval_uuid),
    }


def test_threshold_search_parameterizes_uuid_threshold_vector_and_limit(vector_db):
    dataset_uuid = uuid.uuid4()

    vector_db.vector_similarity_search_with_threshold(
        "dataset_embeddings",
        [0.25, -0.5, 1],
        dataset_id=dataset_uuid.hex,
        threshold=0.4,
        top_k=8,
    )

    query, params = vector_db.client.execute.call_args.args
    assert "CAST(%(query_vector)s AS Array(Float32))" in query
    assert "AND length(vector) = %(vector_dim)s" in query
    assert "eval_id = %(dataset_id)s" in query
    assert "distance <= %(threshold)s" in query
    assert "LIMIT %(top_k)s" in query
    assert str(dataset_uuid) not in query
    assert params == {
        "query_vector": [0.25, -0.5, 1.0],
        "vector_dim": 3,
        "dataset_id": str(dataset_uuid),
        "threshold": 0.4,
        "top_k": 8,
    }


def test_threshold_search_without_top_k_preserves_unlimited_semantics(vector_db):
    vector_db.vector_similarity_search_with_threshold(
        "dataset_embeddings",
        [0.1],
        threshold=None,
        top_k=None,
    )

    query, params = vector_db.client.execute.call_args.args
    assert "LIMIT %(top_k)s" not in query
    assert "distance <= %(threshold)s" not in query
    assert "top_k" not in params
    assert "threshold" not in params


def test_synthetic_search_parameterizes_uuid_collection_and_preserves_results(
    vector_db,
):
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    vector_db.client.execute.return_value = [
        (
            first_id,
            uuid.uuid4(),
            [0.1, 0.2],
            ["item_id"],
            ["item-1"],
            0,
            0.125,
        )
    ]

    result = vector_db.vector_similarity_search(
        "feedbacks",
        [0.1, 0.2],
        eval_id=[first_id.hex.upper(), str(second_id)],
        syn_data_flag=True,
        top_k=5,
    )

    query, params = vector_db.client.execute.call_args.args
    assert "id IN %(eval_ids)s" in query
    assert params["eval_ids"] == (str(first_id), str(second_id))
    assert result == [(first_id, [0.1, 0.2], {"item_id": "item-1"}, 0.125)]


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("vector_similarity_search", {"top_k": 0}),
        (
            "vector_similarity_search_with_threshold",
            {"top_k": 1, "threshold": float("nan")},
        ),
    ],
)
def test_invalid_numeric_controls_fail_without_query(vector_db, method_name, kwargs):
    method = getattr(vector_db, method_name)

    assert method("feedbacks", [0.1], **kwargs) == []
    vector_db.client.execute.assert_not_called()
