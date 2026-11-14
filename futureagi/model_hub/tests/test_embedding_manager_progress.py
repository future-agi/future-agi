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
        f"total; got {seen}"
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


def _stub_text_model(*_args, **_kwargs):
    return [0.1, 0.2, 0.3]


def test_data_formatter_url_check_does_not_crash_on_numeric_values(manager_module):
    manager = manager_module.EmbeddingManager()
    with patch.object(
        manager_module.EmbeddingManager,
        "input_checker",
        return_value={"q": "text"},
    ), patch.object(
        manager_module.model_manager,
        "_use_serving",
        True,
    ), patch.object(
        type(manager_module.model_manager),
        "text_model",
        new_callable=lambda: property(lambda _self: _stub_text_model),
    ), patch.object(
        manager_module.EmbeddingManager,
        "encode_path",
    ) as encode_path:
        for raw in (1, 1.5, True, False):
            row = {"q": raw, "item_id": "id-x"}
            vectors, metadata = manager.data_formatter(
                row_dict=row,
                inputs_formater=["q"],
                table_name=manager_module.GROUND_TRUTH_TABLE_NAME,
                organization_id="org-1",
                workspace_id="ws-1",
            )
            assert vectors
            assert metadata
        encode_path.assert_not_called()


def test_data_formatter_url_check_still_encodes_http_strings(manager_module):
    manager = manager_module.EmbeddingManager()
    with patch.object(
        manager_module.EmbeddingManager,
        "input_checker",
        return_value={"q": "text"},
    ), patch.object(
        manager_module.model_manager,
        "_use_serving",
        True,
    ), patch.object(
        type(manager_module.model_manager),
        "text_model",
        new_callable=lambda: property(lambda _self: _stub_text_model),
    ), patch.object(
        manager_module.EmbeddingManager,
        "encode_path",
        return_value="encoded://x",
    ) as encode_path:
        row = {"q": "http://example.com/x", "item_id": "id-x"}
        vectors, metadata = manager.data_formatter(
            row_dict=row,
            inputs_formater=["q"],
            table_name=manager_module.GROUND_TRUTH_TABLE_NAME,
            organization_id="org-1",
            workspace_id="ws-1",
        )
        assert vectors
        assert metadata
        encode_path.assert_called_once_with("http://example.com/x")


def _legacy_standard_b64_encode(text: str) -> str:
    import base64
    return base64.b64encode(text.encode()).decode()


def test_decode_path_roundtrips_new_urlsafe_encoded_strings(manager_module):
    manager = manager_module.EmbeddingManager()
    for original in (
        "https://example.com/with+plus/and-slash?q=1",
        "s3://bucket/key with spaces.png",
        "https://example.com/cat=is?cute",
    ):
        assert manager.decode_path(manager.encode_path(original)) == original


def test_decode_path_handles_legacy_standard_encoded_strings(manager_module):
    manager = manager_module.EmbeddingManager()
    saw_plus = False
    saw_slash = False
    for original in (
        "https://example.com/file?>>>",
        "https://example.com/key/path???",
        "https://example.com/?>>>///",
    ):
        legacy = _legacy_standard_b64_encode(original)
        saw_plus = saw_plus or "+" in legacy
        saw_slash = saw_slash or "/" in legacy
        assert manager.decode_path(legacy) == original
    assert saw_plus and saw_slash


def test_encode_path_emits_urlsafe_alphabet_only(manager_module):
    manager = manager_module.EmbeddingManager()
    encoded = manager.encode_path("https://example.com/cat+dog/?q=1")
    for forbidden in ("+", "/"):
        assert forbidden not in encoded
