"""Tests for incremental persistence + progress callback in
``EmbeddingManager.parallel_process_metadata``.

The refactor write rule:
  * Each row's vectors are persisted via ``bulk_upsert_vectors`` as soon
    as they are computed (no batch-end atomic write).
  * The optional ``progress_callback`` is invoked once per persisted row
    with the running total of rows done.

These guarantees together let the live ``embedded_row_count`` tick
forward in the FE while embedding runs, and let partial progress
survive a mid-batch activity death (e.g. Temporal heartbeat timeout).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _patch_db():
    """Patch ``ClickHouseVectorDB`` for the duration of a test.

    Returns ``(patcher, mock_class, mock_instance)``. Caller is
    responsible for ``patcher.stop()``.
    """
    patcher = patch(
        "agentic_eval.core.embeddings.embedding_manager.ClickHouseVectorDB"
    )
    cls = patcher.start()
    instance = MagicMock()
    instance.bulk_upsert_vectors.side_effect = lambda **kwargs: [
        m["item_id"] for m in kwargs["metadata_list"]
    ]
    cls.return_value = instance
    return patcher, cls, instance


@pytest.fixture
def manager_module():
    """Lazy import so module-level ``ModelManager()`` construction is
    deferred past test collection (the manager touches the serving
    container at import time)."""
    from agentic_eval.core.embeddings import embedding_manager as mod

    return mod


def _fake_data_formatter(self, row_dict, *_args, **_kwargs):
    return [[0.1, 0.2, 0.3]], [
        {"item_id": row_dict.get("item_id", row_dict.get("q", "x"))}
    ]


def test_progress_callback_ticks_per_row(manager_module):
    rows = [{"q": "a"}, {"q": "b"}, {"q": "c"}]
    seen = []

    patcher, _cls, _instance = _patch_db()
    try:
        with patch.object(
            manager_module.EmbeddingManager,
            "data_formatter",
            _fake_data_formatter,
        ):
            manager_module.EmbeddingManager().parallel_process_metadata(
                eval_id="ev-1",
                metadatas=rows,
                inputs_formater=["q"],
                table_name=manager_module.GROUND_TRUTH_TABLE_NAME,
                organization_id="org-1",
                workspace_id="ws-1",
                progress_callback=seen.append,
            )
    finally:
        patcher.stop()

    assert sorted(seen) == [1, 2, 3], (
        "progress_callback must be invoked once per row with the running "
        f"total — got {seen}"
    )


def test_each_row_persisted_via_its_own_upsert_call(manager_module):
    rows = [{"q": "a"}, {"q": "b"}, {"q": "c"}]

    patcher, _cls, instance = _patch_db()
    try:
        with patch.object(
            manager_module.EmbeddingManager,
            "data_formatter",
            _fake_data_formatter,
        ):
            manager_module.EmbeddingManager().parallel_process_metadata(
                eval_id="ev-1",
                metadatas=rows,
                inputs_formater=["q"],
                table_name=manager_module.GROUND_TRUTH_TABLE_NAME,
                organization_id="org-1",
                workspace_id="ws-1",
            )
    finally:
        patcher.stop()

    upsert_calls = instance.bulk_upsert_vectors.call_args_list
    assert len(upsert_calls) == 3
    for call in upsert_calls:
        kwargs = call.kwargs
        assert len(kwargs["vectors"]) == 1
        assert len(kwargs["metadata_list"]) == 1


def test_failing_row_does_not_abort_remaining_rows(manager_module):
    rows = [{"q": "a"}, {"q": "boom"}, {"q": "c"}]
    seen = []

    def flaky_formatter(self, row_dict, *_args, **_kwargs):
        if row_dict.get("q") == "boom":
            raise RuntimeError("S3 fetch failed")
        return [[0.1, 0.2, 0.3]], [{"item_id": row_dict["q"]}]

    patcher, _cls, instance = _patch_db()
    try:
        with patch.object(
            manager_module.EmbeddingManager, "data_formatter", flaky_formatter
        ):
            manager_module.EmbeddingManager().parallel_process_metadata(
                eval_id="ev-1",
                metadatas=rows,
                inputs_formater=["q"],
                table_name=manager_module.GROUND_TRUTH_TABLE_NAME,
                organization_id="org-1",
                workspace_id="ws-1",
                progress_callback=seen.append,
            )
    finally:
        patcher.stop()

    assert instance.bulk_upsert_vectors.call_count == 2
    assert sorted(seen) == [1, 2]


def test_dynamic_batch_size_fans_small_dataset_across_threads(manager_module):
    rows = [{"q": str(i)} for i in range(8)]

    patcher, cls, _instance = _patch_db()
    try:
        with patch.object(
            manager_module.EmbeddingManager,
            "data_formatter",
            _fake_data_formatter,
        ):
            manager = manager_module.EmbeddingManager()
            constructor_clients = cls.call_count
            manager.parallel_process_metadata(
                eval_id="ev-1",
                metadatas=rows,
                inputs_formater=["q"],
                table_name=manager_module.GROUND_TRUTH_TABLE_NAME,
                organization_id="org-1",
                workspace_id="ws-1",
            )
    finally:
        patcher.stop()

    # 8 rows / 20 workers → batch_size 1 → 8 batches → 8 batch-local DB
    # clients (the legacy hardcoded batch_size=50 would give 1 batch).
    batch_clients = cls.call_count - constructor_clients
    assert batch_clients == 8


def test_progress_callback_failure_is_swallowed(manager_module):
    rows = [{"q": "a"}, {"q": "b"}]

    def boom(_n):
        raise RuntimeError("DB busy")

    patcher, _cls, instance = _patch_db()
    try:
        with patch.object(
            manager_module.EmbeddingManager,
            "data_formatter",
            _fake_data_formatter,
        ):
            manager_module.EmbeddingManager().parallel_process_metadata(
                eval_id="ev-1",
                metadatas=rows,
                inputs_formater=["q"],
                table_name=manager_module.GROUND_TRUTH_TABLE_NAME,
                organization_id="org-1",
                workspace_id="ws-1",
                progress_callback=boom,
            )
    finally:
        patcher.stop()

    assert instance.bulk_upsert_vectors.call_count == 2
