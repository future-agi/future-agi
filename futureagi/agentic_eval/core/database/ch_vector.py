import os
import re
import threading
import time
import uuid
import weakref
from datetime import datetime
from pprint import pprint
from typing import Any

import clickhouse_driver
import structlog

from tracer.services.clickhouse.server_readonly import ensure_read_statement

logger = structlog.get_logger(__name__)


# The vector store keeps its own native client because it is also responsible
# for DDL and embedding writes, and because its connection is sourced directly
# from CH_* environment variables.  Ordinary reads still need the same finite
# application envelope as the analytics transport.  Keep that policy local so
# a read can never be silently rerouted to a differently configured database.
_VECTOR_READ_TIMEOUT_SECONDS = 9.5
_VECTOR_READ_MAX_BYTES = 36 * 1024 * 1024 * 1024
_VECTOR_READ_MAX_THREADS = 4
_VECTOR_READ_MAX_RESULT_ROWS = 1_000_000
_VECTOR_READ_MAX_RESULT_BYTES = 512 * 1024 * 1024
_VECTOR_READ_ADMISSION_SLOTS = 4
_VECTOR_NATIVE_CLIENT_LOCKS_GUARD = threading.Lock()
_VECTOR_NATIVE_CLIENT_LOCKS = weakref.WeakKeyDictionary()
_VECTOR_UNWEAKREFABLE_NATIVE_CLIENT_LOCK = threading.Lock()


def _vector_native_client_read_lock(client):
    """Return the process-wide read mutex for one native driver client."""

    with _VECTOR_NATIVE_CLIENT_LOCKS_GUARD:
        try:
            lock = _VECTOR_NATIVE_CLIENT_LOCKS.get(client)
        except TypeError:
            return _VECTOR_UNWEAKREFABLE_NATIVE_CLIENT_LOCK
        if lock is None:
            lock = threading.Lock()
            _VECTOR_NATIVE_CLIENT_LOCKS[client] = lock
        return lock


def _ensure_vector_read_statement(query: str) -> None:
    """Reject non-read SQL before it reaches the write-capable native client."""

    try:
        ensure_read_statement(query)
    except RuntimeError as exc:
        raise RuntimeError(
            "Only read statements are allowed for guarded ClickHouse vector reads."
        ) from exc


def _finite_read_setting(
    settings: dict[str, Any], name: str, default: int, ceiling: int
) -> int:
    requested = int(settings.get(name, 0) or 0)
    return default if requested <= 0 else min(requested, ceiling)


def _guarded_vector_read_settings(
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the finite settings envelope for one native vector SELECT."""

    normalized = dict(settings or {})
    # Row-scan caps made valid sparse/dense reads fail before their bounded
    # result could be produced.  Bytes, time, result size and admission remain
    # finite instead.
    normalized.pop("max_rows_to_read", None)
    normalized["readonly"] = 2
    normalized["max_memory_usage"] = _finite_read_setting(
        normalized,
        "max_memory_usage",
        _VECTOR_READ_MAX_BYTES,
        _VECTOR_READ_MAX_BYTES,
    )
    normalized["max_bytes_to_read"] = _finite_read_setting(
        normalized,
        "max_bytes_to_read",
        _VECTOR_READ_MAX_BYTES,
        _VECTOR_READ_MAX_BYTES,
    )
    normalized["max_threads"] = _finite_read_setting(
        normalized,
        "max_threads",
        _VECTOR_READ_MAX_THREADS,
        _VECTOR_READ_MAX_THREADS,
    )
    normalized["max_result_rows"] = _finite_read_setting(
        normalized,
        "max_result_rows",
        _VECTOR_READ_MAX_RESULT_ROWS,
        _VECTOR_READ_MAX_RESULT_ROWS,
    )
    normalized["max_result_bytes"] = _finite_read_setting(
        normalized,
        "max_result_bytes",
        _VECTOR_READ_MAX_RESULT_BYTES,
        _VECTOR_READ_MAX_RESULT_BYTES,
    )
    normalized["result_overflow_mode"] = "throw"
    normalized["timeout_overflow_mode"] = "throw"

    requested_timeout = float(normalized.get("max_execution_time", 0) or 0)
    normalized["max_execution_time"] = (
        _VECTOR_READ_TIMEOUT_SECONDS
        if requested_timeout <= 0
        else min(requested_timeout, _VECTOR_READ_TIMEOUT_SECONDS)
    )
    return normalized


def get_clickhouse_client_kwargs() -> dict[str, str | int]:
    port = os.getenv("CH_PORT") or "9000"
    return {
        "host": os.getenv("CH_HOST") or "clickhouse",
        "port": int(port),
        "user": os.getenv("CH_USERNAME") or os.getenv("CH_USER") or "default",
        "password": os.getenv("CH_PASSWORD") or "",
        "database": os.getenv("CH_DATABASE") or "default",
    }


def get_clickhouse_cluster_name() -> str:
    """Cluster name used in `ON CLUSTER` / `clusterAllReplicas(...)` DDL and reads.

    Default is `'cluster'`: every Future AGI deployment (US AWS, US GCP, EU GCP)
    pins `name: "cluster"` in the ClickHouseInstallation manifest. Override per
    environment via `CH_CLUSTER_NAME` if a future deployment uses a different
    name in its `remote_servers` config.
    """
    return os.getenv("CH_CLUSTER_NAME") or "cluster"


_MERGE_TREE_ENGINE_RE = re.compile(
    r"^\s*(?P<family>[A-Za-z]*MergeTree)\s*(?:\((?P<args>.*)\))?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def build_replicated_engine(
    base_engine: str,
    table_name: str,
    *,
    clustered: bool,
    database: str | None = None,
    cluster: str | None = None,
) -> tuple[str, str]:
    """Return ``(engine_clause, on_cluster_clause)`` for a ``CREATE TABLE``.

    On a multi-replica cluster a plain ``*MergeTree[(args)]`` engine is rewritten
    to its ``Replicated*`` form, preserving the sub-family and any version/sign
    argument: ``MergeTree`` -> ``ReplicatedMergeTree``,
    ``ReplacingMergeTree(ts)`` -> ``ReplicatedReplacingMergeTree('<zk>', '{replica}', ts)``.
    The Keeper path follows the deployment convention
    ``/clickhouse/tables/{shard}/<database>/<table>`` so two tables with the same
    short name in different databases never share a znode.

    On single-node CH the base engine is returned unchanged with an empty
    ``ON CLUSTER`` clause, so dev and single-replica deployments keep plain
    engines. One engine-selection rule for every legacy ``default.*`` table;
    ``ClickHouseVectorDB.create_table`` is built on the same helper.
    """
    if not clustered:
        return base_engine, ""
    match = _MERGE_TREE_ENGINE_RE.match(base_engine)
    if not match:
        raise ValueError(
            f"build_replicated_engine: {base_engine!r} is not a recognised "
            "*MergeTree engine, so no Replicated* form can be derived."
        )
    family = match.group("family")
    inner = (match.group("args") or "").strip()
    zk_database_segment = f"{database}/" if database else ""
    zk_path = f"/clickhouse/tables/{{shard}}/{zk_database_segment}{table_name}"
    engine_args = [f"'{zk_path}'", "'{replica}'"]
    if inner:
        engine_args.append(inner)
    engine = f"Replicated{family}({', '.join(engine_args)})"
    on_cluster = f" ON CLUSTER '{cluster or get_clickhouse_cluster_name()}'"
    return engine, on_cluster


def sanitize_sql_value(value: str) -> str:
    """
    Sanitize and escape a string value to make it safe for SQL queries.

    This function handles:
    - Escaping single quotes by replacing them with double single quotes.
    - Escaping backslashes by replacing them with double backslashes.
    - Wrapping reserved SQL keywords in backticks (`` ` ``).
    - Handling null characters and any unexpected special characters.
    """
    value = str(value)
    # Escape single quotes
    value = value.replace("'", "''")

    # Escape backslashes
    value = value.replace("\\", "\\\\")

    # Remove null characters
    value = value.replace("\0", "")

    # If the value matches a reserved SQL keyword, wrap it in backticks
    reserved_keywords = [
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "WHERE",
        "FROM",
        "JOIN",
        "INNER",
        "LEFT",
        "RIGHT",
        "ON",
        "GROUP",
        "ORDER",
        "HAVING",
        "DISTINCT",
        # Add more SQL reserved keywords as needed
    ]
    if value.upper() in reserved_keywords:
        value = f"`{value}`"

    # Optionally, further sanitize by removing or replacing any other potentially harmful characters
    value = re.sub(r"[^\w\s\.,@#\-&()]", "", value)

    return value


def sanitize_metadata(metadata: dict[str, str]) -> dict[str, str]:
    """
    Sanitize all keys and values in the metadata dictionary.

    This function applies the `sanitize_sql_value` function to both keys and values.
    """
    sanitized_metadata = {}
    for key, value in metadata.items():
        sanitized_key = sanitize_sql_value(key)
        sanitized_value = sanitize_sql_value(value)
        sanitized_metadata[sanitized_key] = sanitized_value
        if key == "image_enc":
            sanitized_metadata[sanitized_key] = value

    return sanitized_metadata


def sanitize_keys(keys: list[str]) -> list[str]:
    """
    Sanitize a list of keys.

    This function applies the `sanitize_sql_value` function to each key in the list.
    """
    return [sanitize_sql_value(key) for key in keys]


class ClickHouseVectorDB:
    # Process-level cache: each Django/Celery process sees one CH cluster shape
    # for its lifetime, so the probe can run once per process and re-use the
    # answer across every instance.
    _is_clustered_cached: bool | None = None
    _read_admission = threading.BoundedSemaphore(_VECTOR_READ_ADMISSION_SLOTS)

    def __init__(
        self,
    ):
        self.client = clickhouse_driver.Client(**get_clickhouse_client_kwargs())

    @staticmethod
    def _execute_native_read_with_deadline(
        client,
        query: str,
        params: dict[str, Any],
        *,
        settings: dict[str, Any],
        deadline: float,
    ):
        """Execute on the existing native client inside the remaining wall.

        ``max_execution_time`` bounds server work, but the driver's default
        socket timeout can otherwise leave an API request waiting for minutes.
        A clickhouse-driver connection is exclusive while ``execute`` runs, so
        temporarily narrowing its connect/socket timeout is safe and avoids
        changing the transport used by any write call.

        Lightweight test doubles do not expose a native ``Connection``.  They
        still receive the guarded server settings and follow the normal path.
        """

        connection = getattr(client, "connection", None)
        connected = getattr(connection, "connected", None)
        if connected not in {True, False}:
            return client.execute(query, params, settings=settings)

        original_connect_timeout = getattr(connection, "connect_timeout", None)
        original_send_receive_timeout = getattr(
            connection, "send_receive_timeout", None
        )
        socket = getattr(connection, "socket", None)
        original_socket_timeout = socket.gettimeout() if socket is not None else None
        try:
            if connected is False:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("ClickHouse vector read deadline exhausted")
                if original_connect_timeout is not None:
                    connection.connect_timeout = min(
                        float(original_connect_timeout), remaining
                    )
                if original_send_receive_timeout is not None:
                    connection.send_receive_timeout = min(
                        float(original_send_receive_timeout), remaining
                    )
                connection.connect()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ClickHouse vector read deadline exhausted")
            if original_send_receive_timeout is not None:
                connection.send_receive_timeout = min(
                    float(original_send_receive_timeout), remaining
                )
            socket = getattr(connection, "socket", None)
            if socket is not None:
                if original_socket_timeout is None:
                    original_socket_timeout = original_send_receive_timeout
                socket.settimeout(remaining)

            settings["max_execution_time"] = min(
                float(settings["max_execution_time"]), remaining
            )
            return client.execute(query, params, settings=settings)
        finally:
            if socket is not None and original_socket_timeout is not None:
                try:
                    socket.settimeout(original_socket_timeout)
                except Exception:
                    pass
            if original_connect_timeout is not None:
                connection.connect_timeout = original_connect_timeout
            if original_send_receive_timeout is not None:
                connection.send_receive_timeout = original_send_receive_timeout

    @classmethod
    def _execute_read_on_client(
        cls,
        client,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        settings: dict[str, Any] | None = None,
    ):
        """Execute one read on this vector client's exact database/credentials."""

        _ensure_vector_read_statement(query)
        guarded_settings = _guarded_vector_read_settings(settings)
        wall_seconds = float(guarded_settings["max_execution_time"])
        deadline = time.monotonic() + wall_seconds
        acquired = cls._read_admission.acquire(timeout=wall_seconds)
        if not acquired:
            raise TimeoutError("ClickHouse vector read admission deadline exhausted")
        client_lock = None
        client_lock_acquired = False

        try:
            client_lock = _vector_native_client_read_lock(client)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not client_lock.acquire(timeout=remaining):
                raise TimeoutError("ClickHouse vector client read deadline exhausted")
            client_lock_acquired = True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ClickHouse vector read deadline exhausted")
            guarded_settings["max_execution_time"] = min(wall_seconds, remaining)
            return cls._execute_native_read_with_deadline(
                client,
                query,
                params or {},
                settings=guarded_settings,
                deadline=deadline,
            )
        finally:
            if client_lock_acquired:
                client_lock.release()
            cls._read_admission.release()

    def execute_read(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        *,
        settings: dict[str, Any] | None = None,
        max_result_rows: int | None = None,
    ):
        """Run a guarded read on this instance's exact CH_* connection.

        Vector and clustering tables can live in a database that differs from
        Django's analytics client.  Keeping the public read on the same native
        client as the workflow's writes prevents a successful write followed
        by a false-empty read from another database.
        """

        normalized = dict(settings or {})
        if max_result_rows is not None:
            requested_rows = int(max_result_rows)
            if requested_rows <= 0:
                raise ValueError("max_result_rows must be positive")
            existing_rows = int(normalized.get("max_result_rows", 0) or 0)
            normalized["max_result_rows"] = (
                requested_rows
                if existing_rows <= 0
                else min(existing_rows, requested_rows)
            )
        return self._execute_read_on_client(
            self.client,
            query,
            params,
            settings=normalized,
        )

    @classmethod
    def is_clustered(cls, client) -> bool:
        """True iff CH has a genuinely multi-replica cluster; fails safe to False.

        Checking `system.macros` for the `replica` macro was too lenient: a
        single-node dev CH set up via Helm / docker-compose can have the macro
        too (it's a 1-replica cluster with the macro pre-baked). Such a node
        has no DDLWorker, so any `ON CLUSTER ...` query fails at runtime.

        Using `system.clusters.replica_num` is the precise check: prod multi-
        replica clusters have rows with `replica_num > 1`; a single-node
        cluster only has `replica_num = 1`.

        Classmethod taking any client with `.execute`, so callers that hold a
        raw driver client (boot-time DDL, the LLM usage logger) share the same
        per-process answer instead of each re-probing.
        """
        if cls._is_clustered_cached is not None:
            return cls._is_clustered_cached
        try:
            rows = cls._execute_read_on_client(
                client, "SELECT count() FROM system.clusters WHERE replica_num > 1"
            )
        except Exception:
            # Only cache successful probes: a transient CH outage on the very
            # first call would otherwise poison the process cache with False,
            # and every later table create in this worker would silently emit
            # a non-replicated engine on what is actually a clustered CH.
            logger.warning("ch_vector_cluster_detect_failed", exc_info=True)
            return False
        cls._is_clustered_cached = bool(rows and rows[0][0])
        return cls._is_clustered_cached

    def _is_clustered(self) -> bool:
        return self.is_clustered(self.client)

    def drop_table(self, table_name: str) -> None:
        """
        DROPS a table after use is over. DO NOT USE if not required.
        """
        drop_table_query = f"""
        DROP TABLE IF EXISTS {table_name}
        """
        start_time = datetime.now()
        self.client.execute(drop_table_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"create query took {elapsed_time:.2f} seconds to execute")

    def create_table(
        self,
        table_name: str,
        *,
        cluster: str | None = None,
        database: str | None = None,
        keeper_table_name: str | None = None,
    ) -> None:
        """Create a vector table. Replicated engine on clustered CH, plain otherwise.

        `cluster` overrides the deployment-wide default returned by
        `get_clickhouse_cluster_name()`. Migration paths pass it explicitly;
        runtime callers (EmbeddingManager) use the env-driven default.

        `database` qualifies the CREATE TABLE target and the Keeper path.
        When unset the table lands in the connection's current database
        (`CH_DATABASE`); passing it explicitly is the only way to create the
        table in a different database on the same connection — the
        `clickhouse-driver` HELLO-time database setting cannot be rebound by
        mutating the connection attribute. Including the database in the
        Keeper path matches the deployment-wide
        `<default_replica_path>/clickhouse/tables/{shard}/{database}/{table}</default_replica_path>`
        convention so two replicated tables with the same short name in
        different databases do not coordinate on the same znode.

        `keeper_table_name` overrides the Keeper path component (default: the
        table name), so the conversion swap can keep the canonical path.
        """
        clustered = self._is_clustered()
        cluster_name = cluster or get_clickhouse_cluster_name()
        qualified = f"{database}.{table_name}" if database else table_name
        engine, on_cluster = build_replicated_engine(
            "ReplacingMergeTree()",
            keeper_table_name or table_name,
            clustered=clustered,
            database=database,
            cluster=cluster_name,
        )

        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {qualified}{on_cluster} (
            id UUID,
            eval_id UUID,
            vector Array(Float32),
            metadata Nested (
                key String,
                value Nullable(String)
            ),
            deleted UInt8 DEFAULT 0
        ) ENGINE = {engine}
        ORDER BY id
        """
        start_time = datetime.now()

        self.client.execute(
            create_table_query,
            settings={"data_type_default_nullable": 0},
        )
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(
            "ch_vector_create_table_done",
            table=qualified,
            engine="ReplicatedReplacingMergeTree"
            if clustered
            else "ReplacingMergeTree",
            cluster=cluster_name if clustered else None,
            elapsed_sec=round(elapsed_time, 3),
        )

    def get_or_create_collection(self, table_name: str) -> None:
        """
        Checks if a table exists and creates it if it does not.
        """
        start_time = datetime.now()

        table_exists_query = f"EXISTS TABLE {table_name}"
        table_exists = self.execute_read(table_exists_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"create query took {elapsed_time:.2f} seconds to execute")
        if not table_exists:
            self.create_table(table_name)

    def upsert_vector(
        self,
        table_name: str,
        eval_id: str,
        vector: list[float],
        metadata: dict[str, str],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> str:
        """
        Upserts a vector into the specified table, marking previous entries with the same metadata as deleted.
        Returns the ID of the newly inserted or updated entry.
        """
        new_id = str(uuid.uuid4())
        metadata = sanitize_metadata(metadata)
        unique_keys = sanitize_keys(unique_keys)
        if exclude_keys:
            exclude_keys = sanitize_keys(exclude_keys)

        update_query = (
            f"ALTER TABLE {table_name} UPDATE deleted = 1 WHERE deleted = 0 AND "
        )

        metadata_filter = []
        for unique_key in unique_keys:
            unique_value = metadata[unique_key]
            metadata_filter.append(
                f"has(metadata.key, '{unique_key}') AND metadata.value[indexOf(metadata.key, '{unique_key}')] = '{unique_value}'"
            )

        if exclude_keys:
            for exclude_key in exclude_keys:
                metadata_filter.append(f"NOT has(metadata.key, '{exclude_key}')")

        metadata_filter_query = " AND ".join(metadata_filter)
        update_query += metadata_filter_query
        start_time = datetime.now()
        self.client.execute(update_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"ALTER query took {elapsed_time:.2f} seconds to execute")
        # Flatten metadata into two arrays
        metadata_keys = list(metadata.keys())
        metadata_values = list(metadata.values())

        insert_query = f"INSERT INTO {table_name} (id, eval_id, vector, metadata.key, metadata.value) VALUES"
        start_time = datetime.now()
        # vector_str = "[" + ",".join(map(str, vector)) + "]"
        self.client.execute(
            insert_query,
            [(new_id, eval_id, vector, metadata_keys, metadata_values)],
            types_check=True,
        )
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"Insert query took {elapsed_time:.2f} seconds to execute")
        return new_id

    def fetch_vector_by_id(
        self, table_name: str, id: str
    ) -> dict[str, str | list[float] | dict[str, str]] | None:
        """
        Fetches a vector by its ID from the specified table if it is not marked as deleted.
        Returns the vector row or None if not found.
        """
        id = sanitize_sql_value(id)
        select_query = f"SELECT id, vector, arrayJoin(metadata.key) AS key, arrayJoin(metadata.value) AS value FROM {table_name} WHERE id = '{id}' AND deleted = 0"
        start_time = datetime.now()

        result = self.execute_read(select_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"SELECT query took {elapsed_time:.2f} seconds to execute")
        if result:
            id, vector, keys, values = result[0]
            metadata = dict(zip(keys, values, strict=False))
            return {"id": id, "vector": vector, "metadata": metadata}
        return None

    def fetch_all_vectors(
        self, table_name: str, filter_by: dict[str, str] | None = None
    ) -> list[tuple[str, list[float], dict[str, str]]]:
        """
        Fetches all vectors from the specified table that are not marked as deleted.
        Optionally filters by metadata criteria.
        """

        if filter_by is None:
            filter_by = {}
        start_time = datetime.now()

        select_query = f"SELECT id, vector, arrayJoin(metadata.key) AS key, arrayJoin(metadata.value) AS value FROM {table_name} WHERE deleted = 0"

        if filter_by:
            metadata_filter = [
                f"has(metadata.key, '{key}') AND metadata.value[indexOf(metadata.key, '{key}')] = '{value}'"
                for key, value in filter_by.items()
            ]
            select_query += " AND " + " AND ".join(metadata_filter)

        results = self.execute_read(select_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"select query took {elapsed_time:.2f} seconds to execute")
        vectors = []
        for result in results:
            id, vector, keys, values = result
            metadata = dict(zip(keys, values, strict=False))
            vectors.append((id, vector, metadata))
        return vectors

    def fetch_vectors_by_query(self, query: str):
        start_time = datetime.now()

        results = self.execute_read(query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"fetch query took {elapsed_time:.2f} seconds to execute")
        vectors = []

        for result in results:
            id, vector, keys, values = result
            metadata = dict(zip(keys, values, strict=False))
            vectors.append((id, vector, metadata))

        return vectors

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
        """
        tracebacka similarity search against vectors in the specified table using cosine distance.
        Returns vectors that match the threshold criteria and/or top_k limit.

        Args:
            table_name: The database table to search in
            query_vector: The vector to compare against
            filter_by: Optional metadata filters as key-value pairs
            metadata_column_not_null: Optional metadata column that must not be null
            dataset_id: Optional dataset ID to filter by
            top_k: Optional limit for number of results (default: None, returns all matches)
            threshold: Optional maximum distance threshold (default: 0.7)

        Returns:
            List of tuples containing (id, vector, metadata, similarity)
        """
        if filter_by is None:
            filter_by = {}
        filter_by = sanitize_metadata(filter_by)

        query_vector_str = "[" + ",".join(map(str, query_vector)) + "]"

        metadata_filter_query = ""
        if filter_by:
            metadata_filter = [
                f"has(metadata.key, '{key}') AND metadata.value[indexOf(metadata.key, '{key}')] = '{value}'"
                for key, value in filter_by.items()
            ]
            metadata_filter_query += " AND " + " AND ".join(metadata_filter)

        # Here the column is named eval_id but we are storing the dataset id there in this case
        dataset_id_filter = f" AND eval_id = '{dataset_id}'" if dataset_id else ""

        metadata_not_null_filter = ""
        if metadata_column_not_null:
            metadata_not_null_filter = f"""
                AND has(metadata.key, '{metadata_column_not_null}')
                AND isNotNull(metadata.value[indexOf(metadata.key, '{metadata_column_not_null}')])
            """

        # Add threshold filtering
        threshold_filter = ""
        if threshold is not None:
            threshold_filter = f" AND distance <= {threshold}"

        # Determine limit clause
        limit_clause = f"LIMIT {top_k}" if top_k is not None else ""

        query = f"""
        SELECT
            *,
            cosineDistance(vector, {query_vector_str}) AS distance
        FROM {table_name}
        WHERE deleted = 0
        {dataset_id_filter}
        {metadata_not_null_filter}
        {metadata_filter_query}
        {threshold_filter}
        ORDER BY distance ASC
        {limit_clause}
        """
        # Execute the query
        try:
            results = self.execute_read(query)
        except clickhouse_driver.errors.PartiallyConsumedQueryError as e:
            logger.error(f"PartiallyConsumedQueryError: {e}")
            self.close()
            raise
        except Exception as e:
            logger.info(
                f"Error executing query vector_similarity_search_with_threshold: {e}"
            )
            return None

        similarities = []
        for row in results:
            id, dataset_id, vector, keys, values, _, similarity = row
            metadata = dict(zip(keys, values, strict=False))
            similarities.append(
                {
                    "id": id,
                    "dataset_id": dataset_id,
                    "vector": vector,
                    "metadata": metadata,
                    "similarity": similarity,
                }
            )

        return similarities

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
        """
        Performs a similarity search against vectors in the specified table using cosine distance.
        Returns the top_k vectors sorted by similarity to the query vector.
        """
        if filter_by is None:
            filter_by = {}
        filter_by = sanitize_metadata(filter_by)
        # Convert query_vector to a string representation
        query_vector_str = "[" + ",".join(map(str, query_vector)) + "]"

        # Construct the WHERE clause for metadata filtering
        metadata_filter_query = ""
        if filter_by:
            metadata_filter = [
                f"has(metadata.key, '{key}') AND metadata.value[indexOf(metadata.key, '{key}')] = '{value}'"
                for key, value in filter_by.items()
            ]
            metadata_filter_query += " AND " + " AND ".join(metadata_filter)
        if syn_data_flag:
            if eval_id is not None:
                ids_sql = ", ".join(f"'{u}'" for u in eval_id)  # type: ignore[union-attr]
                eval_id_filter = f" AND id IN ({ids_sql})"
            else:
                eval_id_filter = ""
        else:
            eval_id_filter = f" AND eval_id = '{eval_id}'" if eval_id else ""

        metadata_not_null_filter = ""
        if metadata_column_not_null:
            metadata_not_null_filter = f"""
                AND has(metadata.key, '{metadata_column_not_null}')
                AND isNotNull(metadata.value[indexOf(metadata.key, '{metadata_column_not_null}')])
            """
        # Construct the full query
        query = f"""
        SELECT
            *,
            cosineDistance(vector, {query_vector_str}) AS distance
        FROM {table_name}
        WHERE deleted = 0
        {eval_id_filter}
        {metadata_not_null_filter}
        {metadata_filter_query}
        ORDER BY distance ASC
        LIMIT {top_k}
        """
        start_time = datetime.now()
        results = None
        # Execute the query
        try:
            results = self.execute_read(query)
        except Exception:
            import traceback

            traceback.print_exc()
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"sim search query took {elapsed_time:.2f} seconds to execute")
        similarities = []
        if results:
            for row in results:
                id, eval_id, vector, keys, values, _, similarity = row
                metadata = dict(zip(keys, values, strict=False))
                similarities.append((id, vector, metadata, similarity))
        # Process and return the results
        return similarities

    def close(self) -> None:
        """
        Closes the ClickHouse connection and releases resources.
        Should be called when the database connection is no longer needed.
        """
        if hasattr(self, "client") and self.client is not None:
            self.client.disconnect()
            self.client = None

    def get_num_vectors(self, doc_ids, table_name):
        ids_sql = ", ".join(f"'{u}'" for u in doc_ids)

        query = f"SELECT COUNT(*) FROM {table_name} WHERE id IN ({ids_sql})"
        return self.execute_read(query)

    def get_random_examples(
        self, doc_ids: list[str], table_name: str, limit: int
    ) -> list:
        """
        Get random examples from the table for a specific doc_id.

        Args:
            doc_id: The document ID to filter by
            table_name: The name of the table to query
            percentage: Float between 0 and 1 representing the percentage of chunks to return
        """
        ids_sql = ", ".join(f"'{u}'" for u in doc_ids)
        query = f"""
        SELECT *
        FROM {table_name}
        WHERE id IN ({ids_sql})
        ORDER BY rand()
        LIMIT {limit}
        """
        return self.execute_read(query)

    def bulk_upsert_vectors(
        self,
        table_name: str,
        eval_id: str,
        vectors: list[list[float]],
        metadata_list: list[dict[str, str]],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> list[str]:
        """
        Bulk upserts multiple vectors into the specified table in a single query.

        Args:
            table_name: Name of the table to insert into
            eval_id: Evaluation ID to associate with all vectors
            vectors: List of vector embeddings to insert
            metadata_list: List of metadata dictionaries corresponding to each vector
            unique_keys: List of metadata keys that determine uniqueness
            exclude_keys: Optional list of keys to exclude from uniqueness check

        Returns:
            List of IDs for the newly inserted vectors
        """
        if len(vectors) != len(metadata_list):
            raise ValueError(
                "Number of vectors must match number of metadata dictionaries"
            )

        # Generate IDs for all vectors
        new_ids = [str(uuid.uuid4()) for _ in range(len(vectors))]

        # Sanitize all metadata and keys
        sanitized_metadata_list = [
            sanitize_metadata(metadata) for metadata in metadata_list
        ]
        unique_keys = sanitize_keys(unique_keys)
        if exclude_keys:
            exclude_keys = sanitize_keys(exclude_keys)

        # Build the update query to mark existing entries as deleted
        update_query = (
            f"ALTER TABLE {table_name} UPDATE deleted = 1 WHERE deleted = 0 AND "
        )

        # For bulk operations, we need to handle the uniqueness check differently
        # We'll create a condition that checks if any of the new entries would match
        metadata_filter = []
        for unique_key in unique_keys:
            # Get all unique values for this key across all metadata dictionaries
            unique_values = {
                metadata[unique_key]
                for metadata in sanitized_metadata_list
                if unique_key in metadata
            }

            # Create a condition that checks if any of these values match
            value_conditions = [
                f"metadata.value[indexOf(metadata.key, '{unique_key}')] = '{value}'"
                for value in unique_values
            ]
            metadata_filter.append(
                f"has(metadata.key, '{unique_key}') AND ({' OR '.join(value_conditions)})"
            )

        if exclude_keys:
            for exclude_key in exclude_keys:
                metadata_filter.append(f"NOT has(metadata.key, '{exclude_key}')")

        metadata_filter_query = " AND ".join(metadata_filter)
        update_query += metadata_filter_query

        # Execute the update query
        start_time = datetime.now()
        self.client.execute(update_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"Bulk ALTER query took {elapsed_time:.2f} seconds to execute")

        # Prepare the bulk insert
        insert_query = f"INSERT INTO {table_name} (id, eval_id, vector, metadata.key, metadata.value) VALUES"

        # Prepare the data for bulk insert
        insert_data = []
        for i, (vector, metadata) in enumerate(
            zip(vectors, sanitized_metadata_list, strict=False)
        ):
            metadata_keys = list(metadata.keys())
            metadata_values = list(metadata.values())
            insert_data.append(
                (new_ids[i], eval_id, vector, metadata_keys, metadata_values)
            )

        # Execute the bulk insert
        start_time = datetime.now()
        self.client.execute(insert_query, insert_data, types_check=True)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"Bulk insert query took {elapsed_time:.2f} seconds to execute")

        return new_ids


# Example Usage
if __name__ == "__main__":
    db = ClickHouseVectorDB()
    # LogExceptions

    db.create_table("vectors")

    id1 = db.upsert_vector(
        "vectors",
        "eval_1",
        [0.1, 0.2, 0.3],
        {"description": "vector1", "category": "A"},
        ["category"],
    )
    id2 = db.upsert_vector(
        "vectors",
        "eval_1",
        [0.4, 0.5, 0.6],
        {"description": "vector2", "category": "B"},
        ["category"],
    )
    id3 = db.upsert_vector(
        "vectors",
        "eval_1",
        [0.7, 0.8, 0.9],
        {"description": "vector1", "category": "A"},
        ["category"],
    )

    print(db.fetch_vector_by_id("vectors", id1))
    print(db.fetch_vector_by_id("vectors", id3))

    print(db.fetch_all_vectors("vectors"))

    query_vector = [0.1, 0.2, 0.3]
    pprint(
        db.vector_similarity_search("vectors", query_vector, {"category": "B"}, top_k=2)
    )
