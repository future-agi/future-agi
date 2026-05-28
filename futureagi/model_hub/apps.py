import structlog
from django.apps import AppConfig

logger = structlog.get_logger(__name__)


class ModelHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "model_hub"

    def ready(self):
        # Import signals to register handlers
        # Avoid ClickHouse connections during migrations
        import sys

        import model_hub.signals  # noqa: F401

        if "migrate" in sys.argv or "makemigrations" in sys.argv:
            return

        # Seed system eval templates from YAML (idempotent)
        try:
            from model_hub.management.commands.seed_system_evals import seed_evals

            seed_evals(verbose=False)
        except Exception as e:
            logger.warning(f"System eval seeding skipped: {e}")

        # Existing initialization code - fail gracefully if ClickHouse unavailable
        try:
            self.check_and_create_clickhouse_tables()
        except Exception as e:
            # During tests or development, ClickHouse may not be available
            logger.warning(f"ClickHouse initialization skipped: {e}")

        # Ensure ClickHouse analytics schema (tables, MVs, dicts) exists
        try:
            self._ensure_analytics_schema()
        except Exception as e:
            logger.warning(f"ClickHouse analytics schema init skipped: {e}")

    def _ensure_analytics_schema(self):
        """Ensure all ClickHouse analytics tables, MVs, and dicts exist.
        Idempotent — uses CREATE IF NOT EXISTS for everything."""
        from tracer.services.clickhouse.client import get_clickhouse_client
        from tracer.services.clickhouse.schema import (
            POST_DDL_ALTERS,
            detect_spans_table_shape,
            get_all_schema_ddl,
            get_legacy_chain_drop_statements,
            should_drop_legacy_chain,
        )

        ch = get_clickhouse_client()

        # CH25 cutover (dev/local default): drop the legacy CDC chain
        # before re-applying schema. `CREATE IF NOT EXISTS` would leave
        # stale tables in place; explicit `DROP IF EXISTS` cleans them.
        # No-op when the env flag is unset (prod default).
        if should_drop_legacy_chain():
            # First check whether `spans` itself is the v1 shape (from
            # an older CH 24.x volume / legacy SPANS_TABLE registry
            # entry). If so we have to drop it before the legacy chain
            # because `spans_mv` references it, and reapply v2 schema.
            # The detector is idempotent: a v2 or absent table no-ops.
            self._migrate_v1_spans_if_needed(ch, detect_spans_table_shape)

            for name, drop_sql in get_legacy_chain_drop_statements():
                try:
                    ch.execute(drop_sql)
                    logger.info(f"CH legacy DDL dropped: {name}")
                except Exception as e:
                    logger.warning(f"CH legacy DDL drop {name}: {e}")

        for name, ddl in get_all_schema_ddl():
            try:
                ch.execute(ddl)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"CH schema {name}: {e}")

        # Ensure materialized columns on CDC tables that PeerDB may recreate
        for alter in POST_DDL_ALTERS:
            try:
                ch.execute(alter)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"CH post-DDL alter: {e}")

        logger.info("ClickHouse analytics schema ensured")

        # Warm CH page cache in background (prod only — skip in local dev)
        try:
            from ee.usage.deployment import DeploymentMode
        except ImportError:
            DeploymentMode = None

        if DeploymentMode.is_cloud():
            import threading

            threading.Thread(
                target=self._warm_ch_cache, args=(ch,), daemon=True
            ).start()

    @staticmethod
    def _migrate_v1_spans_if_needed(ch, detect_fn):
        """Drop a v1 ``spans`` table and re-apply v2 schema if v1 detected.

        Runs only when ``CH25_DROP_LEGACY_CDC_CHAIN`` is set (caller-gated).
        Idempotent: no-op when the table is already v2 or doesn't exist.

        Why this is needed: ``CREATE TABLE IF NOT EXISTS`` doesn't fail
        when the existing table has a different shape. A dev box that
        used to run CH 24.x with the legacy ``SPANS_TABLE`` definition
        keeps that v1 ``spans`` table forever — and every v2 read
        (``spans.attrs_string`` etc.) silently 500s.

        The fix:
          1. Detect: read ``system.columns`` for the typed-Map marker.
          2. If v1: drop the table (cascades to dependent MVs).
          3. Tell ``apply_schema`` to re-run the v2 SQL via
             ``ch25_apply_schema --force`` so the schema_versions sha
             check doesn't skip the (now missing) v2 file.

        The schema_versions row for ``002_spans_v2.sql`` is not deleted
        here; ``--force`` is the documented way to bypass drift checks
        and is harmless on a fresh table.
        """
        shape = detect_fn(ch.execute)
        if shape != "v1":
            logger.info("CH25 v1-spans check: %s — no v1 → v2 migration needed", shape)
            return

        logger.warning(
            "CH25 v1-spans detected — dropping v1 `spans` and re-applying "
            "v2 schema. This is one-time per dev environment; the legacy "
            "table predated typed-JSON columns."
        )
        try:
            ch.execute("DROP TABLE IF EXISTS spans SYNC")
        except Exception as e:
            logger.error("CH25 v1-spans DROP failed: %s", e)
            return

        try:
            from django.core.management import call_command

            call_command("ch25_apply_schema", "--force", verbosity=1)
            logger.info("CH25 v2 schema re-applied after v1 drop")
        except Exception as e:
            logger.error(
                "CH25 v2 schema re-apply failed after v1 drop: %s. Run "
                "`python manage.py ch25_apply_schema --force` manually.",
                e,
            )

    @staticmethod
    def _warm_ch_cache(ch):
        """Pre-warm ClickHouse page cache by touching recent data.

        Runs lightweight queries that load index + light columns into the
        OS page cache.  Subsequent user queries hit warm cache (~300ms)
        instead of cold disk (~5s).
        """
        from tracer.services.clickhouse.schema import should_drop_legacy_chain

        warmup_queries = [
            # Warm spans index + light columns for recent data. The v2
            # spans table uses `is_deleted`; pre-cutover prod still
            # carries the `_peerdb_is_deleted` ALIAS column for back-
            # compat, but the canonical name is what we read.
            (
                "SELECT project_id, count() FROM spans "
                "WHERE is_deleted = 0 "
                "AND start_time >= now() - INTERVAL 7 DAY "
                "GROUP BY project_id",
                "spans (7d)",
            ),
            # Warm tracer_trace for recent data
            (
                "SELECT project_id, count() FROM tracer_trace "
                "WHERE _peerdb_is_deleted = 0 "
                "AND created_at >= now() - INTERVAL 7 DAY "
                "GROUP BY project_id",
                "tracer_trace (7d)",
            ),
            # Warm usage_apicalllog for eval metrics
            (
                "SELECT organization_id, count() FROM usage_apicalllog "
                "WHERE _peerdb_is_deleted = 0 "
                "AND created_at >= now() - INTERVAL 7 DAY "
                "GROUP BY organization_id",
                "usage_apicalllog (7d)",
            ),
            # Warm spans_hourly_rollup (v2 dashboard aggregate). Replaces
            # span_metrics_hourly post-CH25 cutover; see
            # docs/CH25_MIGRATION.md. countMerge collapses the aggregate
            # state column.
            (
                "SELECT countMerge(n) FROM spans_hourly_rollup "
                "WHERE hour >= now() - INTERVAL 7 DAY",
                "spans_hourly_rollup (7d)",
            ),
        ]

        # When the legacy chain is retained (prod default), also warm
        # the legacy aggregate so it stays hot until the cutover.
        if not should_drop_legacy_chain():
            warmup_queries.append(
                (
                    "SELECT count() FROM span_metrics_hourly "
                    "WHERE hour >= now() - INTERVAL 7 DAY",
                    "span_metrics_hourly (7d, legacy)",
                )
            )
        for query, label in warmup_queries:
            try:
                ch.execute_read(query, timeout_ms=30000)
                logger.info(f"CH cache warmed: {label}")
            except Exception as e:
                logger.warning(f"CH cache warm failed for {label}: {e}")

    def check_and_create_clickhouse_tables(self):
        from agentic_eval.core.embeddings.embedding_manager import FEEDBACK_TABLE_NAME

        # vector dbs
        vector_dbs = [FEEDBACK_TABLE_NAME]

        from agentic_eval.core.database.ch_vector import ClickHouseVectorDB

        db_client = ClickHouseVectorDB()

        for vector_db in vector_dbs:
            db_client.create_table(vector_db)

        from tfc.utils.clickhouse import ClickHouseClientSingleton

        ch_instance = ClickHouseClientSingleton()

        # Example: Check if table exists
        result = ch_instance.execute("SHOW TABLES FROM default")
        tables = [row[0] for row in result]

        if "events" in tables:
            # Check if original_uuid column exists
            columns = ch_instance.execute("DESCRIBE TABLE events")
            column_names = [col[0] for col in columns]

            if "original_uuid" not in column_names:
                # Add the original_uuid column
                ch_instance.execute(
                    """
                    ALTER TABLE events
                    ADD COLUMN IF NOT EXISTS original_uuid UUID DEFAULT UUID
                    """
                )

        if "events" not in tables:
            # Create the table with original_uuid included
            ch_instance.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    UUID UUID,
                    original_uuid UUID DEFAULT UUID,
                    EventDate Date,
                    EventDateTime DateTime,
                    EventName String,
                    EventType String,
                    AIModel String DEFAULT '',
                    OrgID String,
                    PredictionID String DEFAULT '',
                    ModelVersion String DEFAULT '',
                    BatchID String DEFAULT '',
                    Environment UInt8 DEFAULT 0,
                    Properties Nested(
                        Key String,
                        Value String,
                        DataType String
                    ),
                    Features Nested(
                        Key String,
                        Value String,
                        DataType String
                    ),
                    ActualLabel Nested(
                        Key String,
                        Value String,
                        DataType String
                    ),
                    PredictionLabel Nested(
                        Key String,
                        Value String,
                        DataType String
                    ),
                    EvalResults Nested(
                        Key String,
                        Value String,
                        DataType String
                    ),
                    ShapValues Nested(
                        Key String,
                        Value String,
                        DataType String
                    ),
                    Tags Nested(
                        Key String,
                        Value String,
                        DataType String
                    ),
                    Embedding Array(Float32) DEFAULT [],
                    deleted UInt8 DEFAULT 0

                ) ENGINE = MergeTree()
                PARTITION BY toYYYYMM(EventDate)
                ORDER BY (EventDate, EventName, OrgID, UUID);
            """
            )

        if "llm_logs" not in tables:
            ch_instance.execute(
                """
            CREATE TABLE IF NOT EXISTS llm_logs
            (
                `EventDateTime` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
                `EventDate` Date,
                `TraceId` String CODEC(ZSTD(1)),
                `SpanId` String CODEC(ZSTD(1)),
                `SeverityText` LowCardinality(String) CODEC(ZSTD(1)),
                `SeverityNumber` Int32 CODEC(ZSTD(1)),
                `ServiceName` LowCardinality(String) CODEC(ZSTD(1)),
                `LLMModelName` LowCardinality(String) CODEC(ZSTD(1)), -- Name of the LLM model used
                `UserId` String CODEC(ZSTD(1)), -- User identifier
                `SessionId` String CODEC(ZSTD(1)), -- Session identifier
                `RequestBody` String CODEC(ZSTD(1)), -- Request sent to the LLM
                `ResponseBody` String CODEC(ZSTD(1)), -- Response received from the LLM
                `RequestTokens` Int32 CODEC(ZSTD(1)), -- Number of tokens in the request
                `ResponseTokens` Int32 CODEC(ZSTD(1)), -- Number of tokens in the response
                `ResponseTime` Float32 CODEC(ZSTD(1)), -- Time taken to get the response
                `LogAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)), -- Additional log attributes
                `ResourceAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
                INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1,
                INDEX idx_llm_model_name LLMModelName TYPE bloom_filter(0.001) GRANULARITY 1,
                INDEX idx_user_id UserId TYPE bloom_filter(0.001) GRANULARITY 1,
                INDEX idx_session_id SessionId TYPE bloom_filter(0.001) GRANULARITY 1,
                INDEX idx_request_body RequestBody TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 1,
                INDEX idx_response_body ResponseBody TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 1
            )
            ENGINE = MergeTree
            PARTITION BY EventDate
            ORDER BY (LLMModelName, EventDateTime)
            SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;

            """
            )


##################################################
##################################################
##################################################

# types of model input
# numerical data: continuous , Discrete Data
# Categorical Data
# Text Data
# Natural Language: Human language text (e.g., articles, social media posts).
# Structured Text: Data that comes in a structured format like JSON or XML.
# Unstructured Text: Free-form text without any specific forma
# Image Data
# Audio Data
# Video Data
# Time-Series Data:
# Sequential Data: Data points indexed in time order (e.g., stock prices, weather data).
# Event Log Data: Timestamped logs of events (e.g., web logs, transaction logs).
# Geospatial Data:
# Coordinate Data: Latitude and longitude points.
# Map Data: Data used in mapping and geographical information systems (GIS).
# Sensor Data:

# IoT Data: Data from Internet of Things devices.
# Biometric Data: Fingerprints, facial recognition data.
# Graph Data:

# Networks: Data representing nodes and connections (e.g., social networks, neural networks).
# Trees: Hierarchical data structures.
# Complex Data Structures:

# Hierarchical Data: Data in tree-like structures.
# Mixed Data Types: Combinations of different types (e.g., a dataset with images, text, and numerical values).
# Synthetic / Artificial Data:

# Generated Data: Data generated through simulations or algorithms to model real-world phenomena.
# Encoded Data:

# One-Hot Encoding: Representing categorical data as binary vectors.
# Feature Vectors: Numerically encoded features of complex data (e.g., text, images).
# Sequence Data:

# DNA Sequences: Genetic data.
# Instruction Sequences: Step-by-step instructions or commands.

##################################################
##################################################
##################################################

# supported ai tasks

# text to speech
# ner
# classification
# regression
# image all
# lllm all
