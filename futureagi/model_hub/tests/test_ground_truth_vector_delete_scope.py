"""W11-EVALS-GT-NULL-WS-DELETE: replacing NULL-workspace ground-truth
vectors must not delete another workspace's vectors.

``GroundTruthService.embed_dataset`` invalidates sibling datasets with
``workspace_id=None`` and then soft-deletes the shared vectors for the same
scope. ``EmbeddingManager.data_formatter`` writes a ``workspace_id`` metadata
key only when the dataset has a workspace, so the NULL scope is "vectors
without a workspace_id key", not every workspace in the organization.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from agentic_eval.core.database import ch_vector
from agentic_eval.core.embeddings import embedding_manager
from agentic_eval.core.embeddings.embedding_manager import GROUND_TRUTH_TABLE_NAME

_NULL_WORKSPACE_PREDICATE = "NOT has(metadata.key, 'workspace_id')"
_WORKSPACE_PREDICATE = (
    "metadata.value[indexOf(metadata.key, 'workspace_id')] = %(workspace_id)s"
)
_DIM = 384


def _soft_delete_statement(workspace_id):
    """Run ``soft_delete_vectors`` against a recording client; return its ALTER."""
    with patch.object(embedding_manager, "ClickHouseVectorDB") as db_class:
        client = db_class.return_value.client
        client.execute.side_effect = lambda sql, *args: (
            [(1,)] if sql.startswith("EXISTS TABLE") else None
        )
        embedding_manager.EmbeddingManager().soft_delete_vectors(
            table_name=GROUND_TRUTH_TABLE_NAME,
            eval_id="eval-1",
            organization_id="org-1",
            workspace_id=workspace_id,
        )
    alters = [
        call.args
        for call in client.execute.call_args_list
        if call.args[0].startswith("ALTER TABLE")
    ]
    assert len(alters) == 1
    return alters[0]


def test_null_workspace_delete_targets_only_vectors_without_a_workspace():
    sql, params = _soft_delete_statement(None)

    assert _NULL_WORKSPACE_PREDICATE in sql
    assert _WORKSPACE_PREDICATE not in sql
    assert "workspace_id" not in params
    assert params == {"eval_id": "eval-1", "organization_id": "org-1"}


def test_workspace_delete_keeps_the_exact_workspace_predicate():
    sql, params = _soft_delete_statement("ws-1")

    assert _WORKSPACE_PREDICATE in sql
    assert _NULL_WORKSPACE_PREDICATE not in sql
    assert params["workspace_id"] == "ws-1"


@pytest.fixture
def gt_vector_manager(monkeypatch):
    from conftest import _open_ch_test_native_client

    database = f"test_gt_delete_scope_{uuid.uuid4().hex}"
    admin = _open_ch_test_native_client(owned_database=database)
    admin.execute(f"CREATE DATABASE {database}")
    host, port = admin.connection.hosts[0]
    client_kwargs = {
        "host": host,
        "port": port,
        "user": admin.connection.user,
        "password": admin.connection.password,
        "database": database,
    }
    monkeypatch.setattr(
        ch_vector, "get_clickhouse_client_kwargs", lambda: dict(client_kwargs)
    )
    monkeypatch.setenv("CH_DATABASE", database)
    manager = embedding_manager.EmbeddingManager()
    try:
        manager.db_client.create_table(GROUND_TRUTH_TABLE_NAME, database=database)
        yield manager
    finally:
        manager.db_client.close()
        admin.execute(f"DROP DATABASE {database} SYNC")
        admin.disconnect()


def _insert_vector(manager, *, eval_id, organization_id, workspace_id, value):
    # Same metadata shape as data_formatter: no workspace key for NULL.
    metadata = {
        "item_id": uuid.uuid4().hex,
        "organization_id": organization_id,
        "column_name": "input",
        "input_type": "text",
        "index_column": value,
        "input": value,
    }
    if workspace_id is not None:
        metadata["workspace_id"] = workspace_id
    return manager.db_client.upsert_vector(
        table_name=GROUND_TRUTH_TABLE_NAME,
        eval_id=eval_id,
        vector=[0.1] * _DIM,
        metadata=metadata,
        unique_keys=["item_id"],
    )


def _deleted_by_id(manager, eval_id):
    rows = manager.db_client.client.execute(
        f"SELECT id, deleted FROM {GROUND_TRUTH_TABLE_NAME} "
        "WHERE eval_id = %(eval_id)s",
        {"eval_id": eval_id},
    )
    return {str(row_id): deleted for row_id, deleted in rows}


@pytest.mark.integration
def test_null_workspace_replacement_keeps_other_workspace_vectors(
    gt_vector_manager,
):
    """Mixed workspaces under one organization and template."""
    eval_id = str(uuid.uuid4())
    org = str(uuid.uuid4())
    workspace = str(uuid.uuid4())
    scoped = _insert_vector(
        gt_vector_manager,
        eval_id=eval_id,
        organization_id=org,
        workspace_id=workspace,
        value="workspace answer",
    )
    unscoped = _insert_vector(
        gt_vector_manager,
        eval_id=eval_id,
        organization_id=org,
        workspace_id=None,
        value="no-workspace answer",
    )

    gt_vector_manager.soft_delete_vectors(
        table_name=GROUND_TRUTH_TABLE_NAME,
        eval_id=eval_id,
        organization_id=org,
        workspace_id=None,
    )

    assert _deleted_by_id(gt_vector_manager, eval_id) == {scoped: 0, unscoped: 1}

    gt_vector_manager.soft_delete_vectors(
        table_name=GROUND_TRUTH_TABLE_NAME,
        eval_id=eval_id,
        organization_id=org,
        workspace_id=workspace,
    )

    assert _deleted_by_id(gt_vector_manager, eval_id) == {scoped: 1, unscoped: 1}
