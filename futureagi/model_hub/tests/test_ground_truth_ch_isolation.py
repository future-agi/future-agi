"""Live ClickHouse end-to-end tests for GT tenant isolation.

Exercises the real ``futureagi.ground_truths`` table on the test
ClickHouse instance: vectors written under tenant A's ``organization_id``
/ ``workspace_id`` metadata must not be returned to tenant B's retrieval
call, and vice versa.

The embedding model is patched to return a deterministic vector so the
test never reaches the embedding-serving cluster. The DB call itself is
real - this is the regression test for the historic CH-side filter that
made tenant scope possible.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings import embedding_manager as em
from agentic_eval.core.embeddings.embedding_manager import (
    GROUND_TRUTH_TABLE_NAME,
    EmbeddingManager,
)

_DIM = 384


@pytest.fixture
def gt_table(monkeypatch):
    # ``ClickHouseVectorDB`` reads CH connection from raw env vars (not
    # Django settings), so point them at the test compose CH on
    # localhost:19000 before construction.
    from django.conf import settings
    ch = getattr(settings, "CLICKHOUSE", {}) or {}
    monkeypatch.setenv("CH_HOST", str(ch.get("CH_HOST") or "localhost"))
    monkeypatch.setenv("CH_PORT", str(ch.get("CH_PORT") or "19000"))
    monkeypatch.setenv("CH_USERNAME", str(ch.get("CH_USERNAME") or "default"))
    monkeypatch.setenv("CH_PASSWORD", str(ch.get("CH_PASSWORD") or ""))
    monkeypatch.setenv("CH_DATABASE", str(ch.get("CH_DATABASE") or "test_tfc"))

    try:
        db = ClickHouseVectorDB()
        db.client.execute("SELECT 1")
    except Exception as exc:
        pytest.skip(f"test ClickHouse not reachable: {exc}")
    # ``get_or_create_collection`` has a truthiness bug on the EXISTS
    # check (a returned ``[(0,)]`` is truthy), so always call create_table
    # directly. CREATE TABLE IF NOT EXISTS is idempotent.
    db.create_table(GROUND_TRUTH_TABLE_NAME)
    yield db
    # No teardown drop: other tests may share the table within the same
    # session. Each test scopes by a fresh ``eval_id`` so rows do not leak
    # across cases.


@pytest.fixture
def stub_text_model(monkeypatch):
    """Force ``model_manager.text_model`` to a deterministic 384-dim
    vector so retrieval never reaches the embedding-serving cluster."""

    def _fixed_vector(*_args, **_kwargs):
        return [0.1] * _DIM

    class _Model:
        def encode(self, *_args, **_kwargs):
            return _FixedArray(_fixed_vector())

    class _FixedArray(list):
        def tolist(self):
            return list(self)

    monkeypatch.setattr(em.model_manager, "_use_serving", True)
    monkeypatch.setattr(
        type(em.model_manager),
        "text_model",
        property(lambda _self: _fixed_vector),
    )
    yield


def _insert_gt_vector(
    db: ClickHouseVectorDB, *,
    eval_id: str, organization_id: str, workspace_id: str,
    column_name: str = "input", item_value: str = "hello",
    vector: list[float] | None = None,
) -> str:
    metadata: dict[str, Any] = {
        "item_id": str(uuid.uuid4()).replace("-", "_"),
        "organization_id": organization_id,
        "workspace_id": workspace_id,
        "column_name": column_name,
        "input_type": "text",
        "index_column": item_value,
        column_name: item_value,
    }
    return db.upsert_vector(
        table_name=GROUND_TRUTH_TABLE_NAME,
        eval_id=eval_id,
        vector=vector if vector is not None else [0.1] * _DIM,
        metadata=metadata,
        unique_keys=["item_id"],
    )


def test_ch_retrieve_does_not_leak_across_organizations(
    gt_table, stub_text_model,
):
    """Vectors written under org A's ``organization_id`` are invisible to
    a retrieval call scoped to org B - even though both rows share the
    same ``eval_id`` (i.e. the same SYSTEM template)."""
    eval_id = str(uuid.uuid4())
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())

    a_item = _insert_gt_vector(
        gt_table, eval_id=eval_id,
        organization_id=org_a, workspace_id=ws_a,
        item_value="tenant-A-answer",
    )
    b_item = _insert_gt_vector(
        gt_table, eval_id=eval_id,
        organization_id=org_b, workspace_id=ws_b,
        item_value="tenant-B-answer",
    )

    a_results = EmbeddingManager().retrieve_avg_rag_based_examples(
        eval_id=eval_id,
        inputs=["query"],
        input_cols=["input"],
        table_name=GROUND_TRUTH_TABLE_NAME,
        organization_id=org_a, workspace_id=ws_a,
        top_k=10,
    )
    b_results = EmbeddingManager().retrieve_avg_rag_based_examples(
        eval_id=eval_id,
        inputs=["query"],
        input_cols=["input"],
        table_name=GROUND_TRUTH_TABLE_NAME,
        organization_id=org_b, workspace_id=ws_b,
        top_k=10,
    )

    a_item_ids = [m["item_id"] for group in a_results for m in group]
    b_item_ids = [m["item_id"] for group in b_results for m in group]

    a_meta = {m["item_id"]: m for group in a_results for m in group}
    b_meta = {m["item_id"]: m for group in b_results for m in group}

    assert a_meta, f"org A expected at least one match, got {a_results!r}"
    assert b_meta, f"org B expected at least one match, got {b_results!r}"
    assert all(m["organization_id"] == org_a for m in a_meta.values())
    assert all(m["organization_id"] == org_b for m in b_meta.values())
    assert set(a_item_ids).isdisjoint(b_item_ids)


def test_ch_retrieve_isolates_workspaces_within_same_org(
    gt_table, stub_text_model,
):
    """Two workspaces under the same org get independent CH results -
    matching the partial UniqueConstraint we hold on the PG side."""
    eval_id = str(uuid.uuid4())
    org = str(uuid.uuid4())
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())

    _insert_gt_vector(
        gt_table, eval_id=eval_id,
        organization_id=org, workspace_id=ws_a,
        item_value="workspace-A-answer",
    )
    _insert_gt_vector(
        gt_table, eval_id=eval_id,
        organization_id=org, workspace_id=ws_b,
        item_value="workspace-B-answer",
    )

    a_results = EmbeddingManager().retrieve_avg_rag_based_examples(
        eval_id=eval_id,
        inputs=["query"],
        input_cols=["input"],
        table_name=GROUND_TRUTH_TABLE_NAME,
        organization_id=org, workspace_id=ws_a,
        top_k=10,
    )
    b_results = EmbeddingManager().retrieve_avg_rag_based_examples(
        eval_id=eval_id,
        inputs=["query"],
        input_cols=["input"],
        table_name=GROUND_TRUTH_TABLE_NAME,
        organization_id=org, workspace_id=ws_b,
        top_k=10,
    )

    a_meta = {m["item_id"]: m for group in a_results for m in group}
    b_meta = {m["item_id"]: m for group in b_results for m in group}

    assert a_meta and b_meta
    assert all(m["workspace_id"] == ws_a for m in a_meta.values())
    assert all(m["workspace_id"] == ws_b for m in b_meta.values())


def test_ch_retrieve_omits_soft_deleted_rows(gt_table, stub_text_model):
    """``soft_delete_vectors`` sets ``deleted = 1``. The retrieval path
    filters ``deleted = 0``, so soft-deleted rows must vanish from
    subsequent queries even before any merge runs."""
    eval_id = str(uuid.uuid4())
    org = str(uuid.uuid4())
    ws = str(uuid.uuid4())

    _insert_gt_vector(
        gt_table, eval_id=eval_id,
        organization_id=org, workspace_id=ws,
        item_value="live-answer",
    )
    _insert_gt_vector(
        gt_table, eval_id=eval_id,
        organization_id=org, workspace_id=ws,
        item_value="gone-answer",
    )

    EmbeddingManager().soft_delete_vectors(
        table_name=GROUND_TRUTH_TABLE_NAME,
        eval_id=eval_id,
        organization_id=org,
        workspace_id=ws,
    )

    results = EmbeddingManager().retrieve_avg_rag_based_examples(
        eval_id=eval_id,
        inputs=["query"],
        input_cols=["input"],
        table_name=GROUND_TRUTH_TABLE_NAME,
        organization_id=org, workspace_id=ws,
        top_k=10,
    )

    flat = [m for group in results for m in group]
    assert flat == [], f"soft-deleted rows leaked through retrieval: {flat!r}"
