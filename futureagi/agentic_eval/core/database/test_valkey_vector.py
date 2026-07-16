"""
Tests for ValkeyVectorDB.

Unit tests use mocks; integration tests require VALKEY_TEST_ADDRESS to be set.

Run unit tests:
    pytest agentic_eval/core/database/test_valkey_vector.py -v -k "not integration"

Run integration tests (requires Valkey with vector search module):
    VALKEY_TEST_ADDRESS=localhost:6389 pytest agentic_eval/core/database/test_valkey_vector.py -v -k integration
"""

import json
import os
import struct
import uuid
from unittest.mock import MagicMock, patch

import pytest


class TestFloatListToBytes:
    def test_basic_conversion(self):
        from agentic_eval.core.database.valkey_vector import _float_list_to_bytes

        vector = [1.0, 0.5, -1.0, 0.0]
        result = _float_list_to_bytes(vector)
        assert len(result) == 16
        unpacked = struct.unpack("<4f", result)
        assert unpacked == (1.0, 0.5, -1.0, 0.0)

    def test_empty_vector(self):
        from agentic_eval.core.database.valkey_vector import _float_list_to_bytes

        result = _float_list_to_bytes([])
        assert result == b""


class TestEscapeTag:
    def test_escapes_special_characters(self):
        from agentic_eval.core.database.valkey_vector import _escape_tag

        assert _escape_tag("hello world") == "hello\\ world"
        assert "\\-" in _escape_tag("a-b")
        assert _escape_tag("simple") == "simple"

    def test_uuid(self):
        from agentic_eval.core.database.valkey_vector import _escape_tag

        test_uuid = "123e4567-e89b-12d3-a456-426614174000"
        escaped = _escape_tag(test_uuid)
        assert "\\-" in escaped

    def test_pipe_escaped(self):
        from agentic_eval.core.database.valkey_vector import _escape_tag

        assert _escape_tag("id1|id2") == "id1\\|id2"

    def test_backslash_escaped(self):
        from agentic_eval.core.database.valkey_vector import _escape_tag

        assert _escape_tag("path\\to\\file") == "path\\\\to\\\\file"
        assert _escape_tag("no\\|inject") == "no\\\\\\|inject"


class TestValkeyVectorDBUnit:
    """Unit tests with mocked redis client."""

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_init_connects(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=128)
        mock_client.ping.assert_called_once()
        assert db.dims == 128

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_create_table_creates_index(self, mock_redis_cls):
        import redis as redis_pkg

        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.execute_command.side_effect = [
            redis_pkg.ResponseError("Unknown index name"),
            "OK",
        ]

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=4)
        db.create_table("test_table")

        calls = mock_client.execute_command.call_args_list
        assert calls[0][0][0] == "FT.INFO"
        assert calls[1][0][0] == "FT.CREATE"

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_create_table_skips_if_exists(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.execute_command.return_value = ["some", "info"]

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=4)
        db.create_table("test_table")

        assert mock_client.execute_command.call_count == 1
        assert mock_client.execute_command.call_args[0][0] == "FT.INFO"

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_upsert_vector_stores_hash(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.execute_command.return_value = [0]

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=4)
        db._created_indices.add("idx:test_table")
        result_id = db.upsert_vector(
            "test_table",
            "eval_1",
            [0.1, 0.2, 0.3, 0.4],
            {"key1": "val1", "key2": "val2"},
            ["key1"],
        )

        assert result_id is not None
        uuid.UUID(result_id)
        mock_client.hset.assert_called_once()
        call_args = mock_client.hset.call_args
        mapping = call_args[1]["mapping"]
        metadata_json = json.loads(mapping[b"metadata_json"].decode())
        assert metadata_json == {"key1": "val1", "key2": "val2"}

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_bulk_upsert_uses_pipeline(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_pipe = MagicMock()
        mock_client.pipeline.return_value = mock_pipe
        mock_client.execute_command.return_value = [0]
        mock_redis_cls.return_value = mock_client

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=3)
        db._created_indices.add("idx:test_table")
        ids = db.bulk_upsert_vectors(
            "test_table",
            "eval_1",
            [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            [{"k": "v1"}, {"k": "v2"}],
            ["k"],
        )

        assert len(ids) == 2
        assert mock_pipe.hset.call_count == 2
        mock_pipe.execute.assert_called()

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_fetch_vector_by_id(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client

        vector_bytes = struct.pack("<4f", 0.1, 0.2, 0.3, 0.4)
        metadata = {"key1": "val1", "key2": "val2"}
        mock_client.hgetall.return_value = {
            b"id": b"doc-123",
            b"eval_id": b"eval-1",
            b"metadata_json": json.dumps(metadata).encode(),
            b"deleted": b"0",
            b"vector": vector_bytes,
        }

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=4)
        result = db.fetch_vector_by_id("test_table", "doc-123")

        assert result is not None
        assert result["id"] == "doc-123"
        assert result["metadata"] == {"key1": "val1", "key2": "val2"}
        assert len(result["vector"]) == 4

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_fetch_vector_by_id_deleted_returns_none(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.hgetall.return_value = {
            b"id": b"doc-123",
            b"deleted": b"1",
        }

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=4)
        result = db.fetch_vector_by_id("test_table", "doc-123")
        assert result is None

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_metadata_with_special_chars(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        mock_client.execute_command.return_value = [0]

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=4)
        db._created_indices.add("idx:test_table")

        metadata = {"text": "hello\x1fworld", "path": "a/b|c", "quote": 'she said "hi"'}
        db.upsert_vector("test_table", "eval_1", [1, 0, 0, 0], metadata, ["text"])

        call_args = mock_client.hset.call_args
        mapping = call_args[1]["mapping"]
        recovered = json.loads(mapping[b"metadata_json"].decode())
        assert recovered == metadata

    @patch("agentic_eval.core.database.valkey_vector.redis.Redis")
    def test_close_does_not_set_none(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client

        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

        db = ValkeyVectorDB(dims=4)
        db.close()
        mock_client.close.assert_called_once()


@pytest.mark.skipif(
    not os.getenv("VALKEY_TEST_ADDRESS"),
    reason="VALKEY_TEST_ADDRESS not set; skipping Valkey integration test",
)
class TestValkeyVectorDBIntegration:
    """Integration tests against a live Valkey instance with vector search."""

    @pytest.fixture(autouse=True)
    def setup(self):
        addr = os.getenv("VALKEY_TEST_ADDRESS", "localhost:6379")
        host, port = addr.rsplit(":", 1)

        with patch.dict(os.environ, {"VALKEY_HOST": host, "VALKEY_PORT": port, "VALKEY_PASSWORD": ""}):
            from agentic_eval.core.database.valkey_vector import ValkeyVectorDB

            self.db = ValkeyVectorDB(dims=4)
            self.table_name = f"test_kb_{uuid.uuid4().hex[:8]}"
            self.db.create_table(self.table_name)

        yield

        self.db.drop_table(self.table_name)
        self.db.close()

    def test_upsert_and_fetch(self):
        doc_id = self.db.upsert_vector(
            self.table_name,
            "eval_1",
            [0.5, 0.5, 0.5, 0.5],
            {"chunk_text": "hello world", "file_id": "f1"},
            ["chunk_text"],
        )

        result = self.db.fetch_vector_by_id(self.table_name, doc_id)
        assert result is not None
        assert result["metadata"]["chunk_text"] == "hello world"

    def test_metadata_roundtrip_with_special_chars(self):
        metadata = {"text": "line1\nline2", "path": "a/b/c", "json_like": '{"key": "val"}'}
        doc_id = self.db.upsert_vector(
            self.table_name,
            "eval_1",
            [1.0, 0.0, 0.0, 0.0],
            metadata,
            ["text"],
        )
        result = self.db.fetch_vector_by_id(self.table_name, doc_id)
        assert result is not None
        assert result["metadata"] == metadata

    def test_similarity_search(self):
        import time

        self.db.upsert_vector(
            self.table_name,
            "eval_1",
            [1.0, 0.0, 0.0, 0.0],
            {"chunk_text": "doc A", "input_type": "text"},
            ["chunk_text"],
        )
        self.db.upsert_vector(
            self.table_name,
            "eval_1",
            [0.0, 1.0, 0.0, 0.0],
            {"chunk_text": "doc B", "input_type": "text"},
            ["chunk_text"],
        )
        time.sleep(1.5)

        results = self.db.vector_similarity_search(
            self.table_name,
            [0.9, 0.1, 0.0, 0.0],
            filter_by={"input_type": "text"},
            eval_id="eval_1",
            top_k=2,
        )

        assert len(results) >= 1
        first_meta = results[0][2]
        assert first_meta["chunk_text"] == "doc A"

    def test_bulk_upsert(self):
        ids = self.db.bulk_upsert_vectors(
            self.table_name,
            "eval_bulk",
            [[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]],
            [{"chunk_text": "c1", "input_type": "text"}, {"chunk_text": "c2", "input_type": "text"}],
            ["chunk_text"],
        )
        assert len(ids) == 2
        for doc_id in ids:
            result = self.db.fetch_vector_by_id(self.table_name, doc_id)
            assert result is not None

    def test_bulk_upsert_marks_old_deleted(self):
        import time

        self.db.upsert_vector(
            self.table_name,
            "eval_dedup",
            [1.0, 0.0, 0.0, 0.0],
            {"item_id": "item1", "input_type": "text"},
            ["item_id"],
        )
        time.sleep(0.5)

        new_ids = self.db.bulk_upsert_vectors(
            self.table_name,
            "eval_dedup",
            [[0.0, 1.0, 0.0, 0.0]],
            [{"item_id": "item1", "input_type": "text"}],
            ["item_id"],
        )
        time.sleep(1.5)

        results = self.db.vector_similarity_search(
            self.table_name,
            [0.0, 1.0, 0.0, 0.0],
            eval_id="eval_dedup",
            top_k=10,
        )
        item_ids = [r[2].get("item_id") for r in results]
        assert item_ids.count("item1") == 1

    def test_threshold_search(self):
        import time

        self.db.upsert_vector(
            self.table_name,
            "eval_thr",
            [1.0, 0.0, 0.0, 0.0],
            {"chunk_text": "close", "input_type": "text"},
            ["chunk_text"],
        )
        time.sleep(1.5)

        results = self.db.vector_similarity_search_with_threshold(
            self.table_name,
            [1.0, 0.0, 0.0, 0.0],
            dataset_id="eval_thr",
            threshold=0.1,
        )

        assert results is not None
        assert len(results) >= 1
        assert results[0]["similarity"] <= 0.1

    def test_delete_chunks_by_file(self):
        import time

        self.db.upsert_vector(
            self.table_name,
            "kb_1",
            [1.0, 0.0, 0.0, 0.0],
            {"file_id": "f1", "organization_id": "org1", "chunk_text": "hello"},
            ["chunk_text"],
        )
        self.db.upsert_vector(
            self.table_name,
            "kb_1",
            [0.0, 1.0, 0.0, 0.0],
            {"file_id": "f2", "organization_id": "org1", "chunk_text": "world"},
            ["chunk_text"],
        )
        time.sleep(1.5)

        self.db.delete_chunks_by_file(self.table_name, "kb_1", "f1", "org1")
        time.sleep(0.5)

        results = self.db.vector_similarity_search(
            self.table_name,
            [1.0, 0.0, 0.0, 0.0],
            eval_id="kb_1",
            top_k=10,
        )
        file_ids = [r[2].get("file_id") for r in results]
        assert "f1" not in file_ids
        assert "f2" in file_ids
