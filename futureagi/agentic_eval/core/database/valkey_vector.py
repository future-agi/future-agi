"""
Valkey-backed vector database for the Knowledge Base.

This module provides a ValkeyVectorDB class that implements the same interface as
ClickHouseVectorDB, using Valkey's vector search module (FT.CREATE / FT.SEARCH)
for similarity search over embeddings.

Requires Valkey 8.0+ with the valkey-search module, or Redis Stack.
"""

import json
import os
import random
import struct
import uuid
from datetime import datetime

import redis
import structlog

logger = structlog.get_logger(__name__)


def _float_list_to_bytes(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def _escape_tag(value: str) -> str:
    """Escape special characters for RediSearch TAG field queries."""
    special = set(r' ,.<>{}[]"' + r"':;!@#$%^&*()-+=~/|")
    escaped = []
    for ch in value:
        if ch in special:
            escaped.append(f"\\{ch}")
        else:
            escaped.append(ch)
    return "".join(escaped)


class ValkeyVectorDB:
    """
    Vector database backed by Valkey with the vector search module.

    Provides the same public interface as ClickHouseVectorDB so it can be used
    as a drop-in replacement for knowledge base vector storage.

    Thread safety: the underlying redis-py client uses a connection pool and is
    safe for concurrent use from multiple threads. The close() method should only
    be called after all operations are complete.

    Environment variables:
        VALKEY_HOST: Valkey server host (default: localhost)
        VALKEY_PORT: Valkey server port (default: 6379)
        VALKEY_PASSWORD: Valkey password (default: empty)
        VALKEY_DB: Valkey database number (default: 0)
        VALKEY_VECTOR_DIMS: Embedding vector dimensions (default: 768)
    """

    def __init__(self, dims: int | None = None):
        host = os.getenv("VALKEY_HOST", "localhost")
        port = int(os.getenv("VALKEY_PORT", "6379"))
        password = os.getenv("VALKEY_PASSWORD", "")
        db = int(os.getenv("VALKEY_DB", "0"))
        self.dims = dims or int(os.getenv("VALKEY_VECTOR_DIMS", "768"))

        self.client = redis.Redis(
            host=host,
            port=port,
            password=password or None,
            db=db,
            decode_responses=False,
            protocol=2,
            socket_connect_timeout=5,
            socket_timeout=10,
            retry_on_timeout=True,
        )
        try:
            self.client.ping()
        except redis.ConnectionError as e:
            logger.warning("valkey: ping failed on init, will retry on first use", error=str(e))
        self._created_indices: set[str] = set()

    def _index_name(self, table_name: str) -> str:
        return f"idx:{table_name}"

    def _key_prefix(self, table_name: str) -> str:
        return f"{table_name}:"

    def create_table(self, table_name: str) -> None:
        index_name = self._index_name(table_name)
        if index_name in self._created_indices:
            return

        try:
            self.client.execute_command("FT.INFO", index_name)
            self._created_indices.add(index_name)
            return
        except redis.ResponseError as e:
            # Redis Stack says "Unknown index name"; valkey-search says
            # "Index with name '...' not found". Treat both as "index missing".
            msg = str(e).lower()
            if "unknown index" not in msg and "not found" not in msg:
                raise

        prefix = self._key_prefix(table_name)
        try:
            # Only VECTOR, TAG, and NUMERIC field types are supported by
            # valkey-search. metadata_json is stored in the hash but not
            # indexed; FT.SEARCH returns all hash fields regardless.
            self.client.execute_command(
                "FT.CREATE", index_name,
                "ON", "HASH",
                "PREFIX", "1", prefix,
                "SCHEMA",
                "id", "TAG",
                "eval_id", "TAG",
                "deleted", "NUMERIC",
                "vector", "VECTOR", "HNSW", "6",
                "TYPE", "FLOAT32",
                "DIM", str(self.dims),
                "DISTANCE_METRIC", "COSINE",
            )
            logger.info("valkey vector index created", index=index_name, dims=self.dims)
        except redis.ResponseError as e:
            if "Index already exists" in str(e):
                pass
            else:
                raise
        self._created_indices.add(index_name)

    def drop_table(self, table_name: str) -> None:
        index_name = self._index_name(table_name)
        try:
            self.client.execute_command("FT.DROPINDEX", index_name, "DD")
        except redis.ResponseError:
            pass
        self._created_indices.discard(index_name)

    def get_or_create_collection(self, table_name: str) -> None:
        self.create_table(table_name)

    def _store_hash(
        self,
        table_name: str,
        doc_id: str,
        eval_id: str,
        vector: list[float],
        metadata: dict[str, str],
        deleted: int = 0,
    ) -> None:
        key = f"{self._key_prefix(table_name)}{doc_id}"
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        vector_bytes = _float_list_to_bytes(vector)

        mapping = {
            b"id": doc_id.encode(),
            b"eval_id": eval_id.encode(),
            b"metadata_json": metadata_json.encode("utf-8"),
            b"deleted": str(deleted).encode(),
            b"vector": vector_bytes,
        }
        self.client.hset(key, mapping=mapping)

    def _parse_metadata_from_field(self, raw) -> dict[str, str]:
        if not raw:
            return {}
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def upsert_vector(
        self,
        table_name: str,
        eval_id: str,
        vector: list[float],
        metadata: dict[str, str],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> str:
        new_id = str(uuid.uuid4())

        self._mark_deleted(table_name, eval_id, metadata, unique_keys, exclude_keys)
        self._store_hash(table_name, new_id, eval_id, vector, metadata)

        return new_id

    def _mark_deleted(
        self,
        table_name: str,
        eval_id: str,
        metadata: dict[str, str],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> None:
        index_name = self._index_name(table_name)
        self.create_table(table_name)

        filter_parts = ["@deleted:[0 0]"]
        if eval_id:
            filter_parts.append(f"@eval_id:{{{_escape_tag(str(eval_id))}}}")

        filter_query = " ".join(filter_parts)
        prefix = self._key_prefix(table_name)
        keys_to_mark = []
        page_size = 500
        offset = 0

        while True:
            try:
                result = self.client.execute_command(
                    "FT.SEARCH", index_name, filter_query,
                    "LIMIT", str(offset), str(page_size),
                )
            except redis.ResponseError:
                break

            if not result or result[0] == 0:
                break

            parsed = self._parse_raw_results(result)
            if not parsed:
                break

            for doc_id, fields in parsed:
                stored_meta = self._parse_metadata_from_field(fields.get("metadata_json"))

                match = True
                for uk in unique_keys:
                    if stored_meta.get(uk) != str(metadata.get(uk, "")):
                        match = False
                        break

                if match and exclude_keys:
                    for ek in exclude_keys:
                        if ek in stored_meta:
                            match = False
                            break

                if match:
                    keys_to_mark.append(f"{prefix}{doc_id}")

            if len(parsed) < page_size:
                break
            offset += page_size

        if keys_to_mark:
            pipe = self.client.pipeline(transaction=False)
            for key in keys_to_mark:
                pipe.hset(key, b"deleted", b"1")
            pipe.execute()

    def fetch_vector_by_id(
        self, table_name: str, id: str
    ) -> dict[str, str | list[float] | dict[str, str]] | None:
        key = f"{self._key_prefix(table_name)}{id}"
        data = self.client.hgetall(key)
        if not data:
            return None
        if data.get(b"deleted", b"0") == b"1":
            return None

        vector_bytes = data.get(b"vector", b"")
        n_floats = len(vector_bytes) // 4
        vector = list(struct.unpack(f"<{n_floats}f", vector_bytes))
        metadata = self._parse_metadata_from_field(data.get(b"metadata_json", b"{}"))
        return {"id": id, "vector": vector, "metadata": metadata}

    def fetch_all_vectors(
        self, table_name: str, filter_by: dict[str, str] | None = None
    ) -> list[tuple[str, list[float], dict[str, str]]]:
        if filter_by is None:
            filter_by = {}

        prefix = self._key_prefix(table_name)
        results = []
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor, match=f"{prefix}*", count=500)
            for key in keys:
                data = self.client.hgetall(key)
                if not data or data.get(b"deleted", b"0") == b"1":
                    continue

                metadata = self._parse_metadata_from_field(data.get(b"metadata_json", b"{}"))

                if filter_by:
                    skip = False
                    for fk, fv in filter_by.items():
                        if metadata.get(fk) != str(fv):
                            skip = True
                            break
                    if skip:
                        continue

                vector_bytes = data.get(b"vector", b"")
                n_floats = len(vector_bytes) // 4
                vector = list(struct.unpack(f"<{n_floats}f", vector_bytes))
                doc_id = data.get(b"id", b"").decode()
                results.append((doc_id, vector, metadata))

            if cursor == 0:
                break
        return results

    def vector_similarity_search(
        self,
        table_name: str,
        query_vector: list[float],
        filter_by: dict[str, str] | None = None,
        metadata_column_not_null: str | None = None,
        eval_id: str | None = None,
        top_k: int = 5,
        syn_data_flag=False,
    ):
        index_name = self._index_name(table_name)
        self.create_table(table_name)

        filter_parts = ["@deleted:[0 0]"]
        if eval_id and not syn_data_flag:
            filter_parts.append(f"@eval_id:{{{_escape_tag(str(eval_id))}}}")
        elif syn_data_flag and eval_id:
            if isinstance(eval_id, (list, tuple)):
                id_filter = "|".join(_escape_tag(str(eid)) for eid in eval_id)
                filter_parts.append(f"@id:{{{id_filter}}}")
            else:
                filter_parts.append(f"@id:{{{_escape_tag(str(eval_id))}}}")

        filter_query = " ".join(filter_parts) if filter_parts else "*"
        query = f"({filter_query})=>[KNN {top_k} @vector $BLOB AS distance]"

        blob = _float_list_to_bytes(query_vector)

        try:
            start_time = datetime.now()
            result = self.client.execute_command(
                "FT.SEARCH", index_name, query,
                "PARAMS", "2", "BLOB", blob,
                "LIMIT", "0", str(top_k),
                "DIALECT", "2",
            )
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"valkey similarity search took {elapsed:.2f}s")
        except redis.ResponseError as e:
            logger.error("valkey vector search error", error=str(e))
            return []

        return self._parse_search_results(result, filter_by, metadata_column_not_null, eval_id, syn_data_flag)

    def vector_similarity_search_with_threshold(
        self,
        table_name: str,
        query_vector: list[float],
        filter_by: dict[str, str] | None = None,
        metadata_column_not_null: str | None = None,
        dataset_id: str | None = None,
        top_k: int | None = None,
        threshold: float = 0.75,
    ):
        index_name = self._index_name(table_name)
        self.create_table(table_name)

        search_k = top_k or 100

        filter_parts = ["@deleted:[0 0]"]
        if dataset_id:
            filter_parts.append(f"@eval_id:{{{_escape_tag(str(dataset_id))}}}")

        filter_query = " ".join(filter_parts) if filter_parts else "*"
        query = f"({filter_query})=>[KNN {search_k} @vector $BLOB AS distance]"

        blob = _float_list_to_bytes(query_vector)

        try:
            start_time = datetime.now()
            result = self.client.execute_command(
                "FT.SEARCH", index_name, query,
                "PARAMS", "2", "BLOB", blob,
                "LIMIT", "0", str(search_k),
                "DIALECT", "2",
            )
            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"valkey threshold search took {elapsed:.2f}s")
        except redis.ResponseError as e:
            logger.error("valkey vector threshold search error", error=str(e))
            return None

        similarities = []
        parsed = self._parse_raw_results(result)
        for doc_id, fields in parsed:
            distance = self._extract_float(fields.get("distance"), 1.0)
            if distance > threshold:
                continue

            metadata = self._parse_metadata_from_field(fields.get("metadata_json"))

            if filter_by:
                skip = False
                for fk, fv in filter_by.items():
                    if metadata.get(fk) != str(fv):
                        skip = True
                        break
                if skip:
                    continue

            if metadata_column_not_null and not metadata.get(metadata_column_not_null):
                continue

            vector_bytes = fields.get("vector", b"")
            n_floats = len(vector_bytes) // 4 if isinstance(vector_bytes, bytes) and vector_bytes else 0
            vector = list(struct.unpack(f"<{n_floats}f", vector_bytes)) if n_floats else []

            eval_id_raw = fields.get("eval_id", b"")
            stored_dataset_id = eval_id_raw.decode() if isinstance(eval_id_raw, bytes) else str(eval_id_raw)

            similarities.append({
                "id": doc_id,
                "dataset_id": stored_dataset_id,
                "vector": vector,
                "metadata": metadata,
                "similarity": distance,
            })

        if top_k:
            similarities = similarities[:top_k]

        return similarities

    def _extract_float(self, value, default: float = 0.0) -> float:
        if value is None:
            return default
        if isinstance(value, bytes):
            value = value.decode()
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def _parse_raw_results(self, result) -> list[tuple[str, dict]]:
        if not result or result[0] == 0:
            return []

        count = result[0]
        parsed = []
        i = 1
        while i < len(result) and len(parsed) < count:
            doc_key = result[i]
            if isinstance(doc_key, bytes):
                doc_key = doc_key.decode()
            fields_flat = result[i + 1] if i + 1 < len(result) else []
            fields = {}
            for j in range(0, len(fields_flat) - 1, 2):
                k = fields_flat[j]
                v = fields_flat[j + 1]
                if isinstance(k, bytes):
                    k = k.decode()
                fields[k] = v
            doc_id = doc_key.split(":")[-1] if ":" in doc_key else doc_key
            parsed.append((doc_id, fields))
            i += 2

        return parsed

    def _parse_search_results(self, result, filter_by, metadata_column_not_null, eval_id, syn_data_flag):
        parsed = self._parse_raw_results(result)
        similarities = []

        for doc_id, fields in parsed:
            distance = self._extract_float(fields.get("distance"), 1.0)

            metadata = self._parse_metadata_from_field(fields.get("metadata_json"))

            if filter_by:
                skip = False
                for fk, fv in filter_by.items():
                    if metadata.get(fk) != str(fv):
                        skip = True
                        break
                if skip:
                    continue

            if metadata_column_not_null and not metadata.get(metadata_column_not_null):
                continue

            stored_eval_id = fields.get("eval_id", b"")
            if isinstance(stored_eval_id, bytes):
                stored_eval_id = stored_eval_id.decode()

            vector_bytes = fields.get("vector", b"")
            if isinstance(vector_bytes, bytes) and vector_bytes:
                n_floats = len(vector_bytes) // 4
                vector = list(struct.unpack(f"<{n_floats}f", vector_bytes))
            else:
                vector = []

            similarities.append((doc_id, vector, metadata, distance))

        return similarities

    def bulk_upsert_vectors(
        self,
        table_name: str,
        eval_id: str,
        vectors: list[list[float]],
        metadata_list: list[dict[str, str]],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> list[str]:
        if len(vectors) != len(metadata_list):
            raise ValueError("Number of vectors must match number of metadata dictionaries")

        self._bulk_mark_deleted(table_name, eval_id, metadata_list, unique_keys, exclude_keys)

        new_ids = [str(uuid.uuid4()) for _ in range(len(vectors))]

        pipe = self.client.pipeline(transaction=False)
        prefix = self._key_prefix(table_name)

        for i, (vector, metadata) in enumerate(zip(vectors, metadata_list, strict=False)):
            doc_id = new_ids[i]
            key = f"{prefix}{doc_id}"
            metadata_json = json.dumps(metadata, ensure_ascii=False)
            vector_bytes = _float_list_to_bytes(vector)

            mapping = {
                b"id": doc_id.encode(),
                b"eval_id": eval_id.encode(),
                b"metadata_json": metadata_json.encode("utf-8"),
                b"deleted": b"0",
                b"vector": vector_bytes,
            }
            pipe.hset(key, mapping=mapping)

        start_time = datetime.now()
        pipe.execute()
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"valkey bulk upsert took {elapsed:.2f}s", count=len(new_ids))

        return new_ids

    def _bulk_mark_deleted(
        self,
        table_name: str,
        eval_id: str,
        metadata_list: list[dict[str, str]],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> None:
        index_name = self._index_name(table_name)
        self.create_table(table_name)

        filter_parts = ["@deleted:[0 0]"]
        if eval_id:
            filter_parts.append(f"@eval_id:{{{_escape_tag(str(eval_id))}}}")
        filter_query = " ".join(filter_parts)

        unique_values_set = set()
        for metadata in metadata_list:
            key_tuple = tuple(str(metadata.get(uk, "")) for uk in unique_keys)
            unique_values_set.add(key_tuple)

        prefix = self._key_prefix(table_name)
        keys_to_mark = []
        page_size = 500
        offset = 0

        while True:
            try:
                result = self.client.execute_command(
                    "FT.SEARCH", index_name, filter_query,
                    "LIMIT", str(offset), str(page_size),
                )
            except redis.ResponseError:
                break

            if not result or result[0] == 0:
                break

            parsed = self._parse_raw_results(result)
            if not parsed:
                break

            for doc_key_id, fields in parsed:
                stored_meta = self._parse_metadata_from_field(fields.get("metadata_json"))
                stored_tuple = tuple(stored_meta.get(uk, "") for uk in unique_keys)

                if stored_tuple in unique_values_set:
                    if exclude_keys:
                        skip = False
                        for ek in exclude_keys:
                            if ek in stored_meta:
                                skip = True
                                break
                        if skip:
                            continue
                    keys_to_mark.append(f"{prefix}{doc_key_id}")

            if len(parsed) < page_size:
                break
            offset += page_size

        if keys_to_mark:
            pipe = self.client.pipeline(transaction=False)
            for key in keys_to_mark:
                pipe.hset(key, b"deleted", b"1")
            pipe.execute()

    def get_num_vectors(self, doc_ids: list[str], table_name: str):
        prefix = self._key_prefix(table_name)
        pipe = self.client.pipeline(transaction=False)
        for doc_id in doc_ids:
            pipe.exists(f"{prefix}{doc_id}")
        results = pipe.execute()
        count = sum(1 for r in results if r)
        return [(count,)]

    def get_random_examples(self, doc_ids: list[str], table_name: str, limit: int) -> list:
        prefix = self._key_prefix(table_name)
        shuffled = list(doc_ids)
        random.shuffle(shuffled)

        results = []
        for doc_id in shuffled:
            key = f"{prefix}{doc_id}"
            data = self.client.hgetall(key)
            if data and data.get(b"deleted", b"0") == b"0":
                vector_bytes = data.get(b"vector", b"")
                n_floats = len(vector_bytes) // 4
                vector = list(struct.unpack(f"<{n_floats}f", vector_bytes)) if n_floats else []
                metadata = self._parse_metadata_from_field(data.get(b"metadata_json", b"{}"))
                eval_id = data.get(b"eval_id", b"").decode()
                metadata_keys = list(metadata.keys())
                metadata_values = list(metadata.values())
                results.append((doc_id, eval_id, vector, metadata_keys, metadata_values, 0))
            if len(results) >= limit:
                break
        return results

    def get_feedback_count(
        self, table_name: str, eval_id: str, organization_id: str | None = None, workspace_id: str | None = None
    ) -> int:
        index_name = self._index_name(table_name)
        self.create_table(table_name)

        filter_parts = ["@deleted:[0 0]", f"@eval_id:{{{_escape_tag(eval_id)}}}"]
        filter_query = " ".join(filter_parts)

        try:
            result = self.client.execute_command(
                "FT.SEARCH", index_name, filter_query,
                "LIMIT", "0", "0",
            )
            total = result[0] if result else 0
        except redis.ResponseError:
            return 0

        if not organization_id and not workspace_id:
            return total

        try:
            result = self.client.execute_command(
                "FT.SEARCH", index_name, filter_query,
                "LIMIT", "0", str(total),
            )
        except redis.ResponseError:
            return 0

        count = 0
        parsed = self._parse_raw_results(result)
        for _, fields in parsed:
            metadata = self._parse_metadata_from_field(fields.get("metadata_json"))
            if organization_id and metadata.get("organization_id") != str(organization_id):
                continue
            if workspace_id and metadata.get("workspace_id") != str(workspace_id):
                continue
            count += 1
        return count

    def delete_chunks_by_file(
        self, table_name: str, kb_id: str, file_id: str, organization_id: str
    ) -> None:
        index_name = self._index_name(table_name)
        self.create_table(table_name)

        filter_parts = ["@deleted:[0 0]", f"@eval_id:{{{_escape_tag(kb_id)}}}"]
        filter_query = " ".join(filter_parts)

        try:
            result = self.client.execute_command(
                "FT.SEARCH", index_name, filter_query,
                "LIMIT", "0", "10000",
            )
        except redis.ResponseError:
            return

        if not result or result[0] == 0:
            return

        keys_to_delete = []
        prefix = self._key_prefix(table_name)
        parsed = self._parse_raw_results(result)
        for doc_id, fields in parsed:
            metadata = self._parse_metadata_from_field(fields.get("metadata_json"))
            if metadata.get("file_id") == file_id and metadata.get("organization_id") == organization_id:
                keys_to_delete.append(f"{prefix}{doc_id}")

        if keys_to_delete:
            pipe = self.client.pipeline(transaction=False)
            for key in keys_to_delete:
                pipe.delete(key)
            pipe.execute()
            logger.info(
                "valkey deleted chunks",
                kb_id=kb_id, file_id=file_id, count=len(keys_to_delete),
            )

    def table_exists(self, table_name: str) -> bool:
        index_name = self._index_name(table_name)
        try:
            self.client.execute_command("FT.INFO", index_name)
            return True
        except redis.ResponseError:
            return False

    def close(self) -> None:
        if hasattr(self, "client") and self.client is not None:
            self.client.close()
