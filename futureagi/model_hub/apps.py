import os

import structlog
from django.apps import AppConfig

logger = structlog.get_logger(__name__)

STARTUP_SAFE_MANAGEMENT_COMMANDS = frozenset(
    {
        "check",
        "collectstatic",
        "grpcrunaioserver",
        "runserver",
        "start_temporal_worker",
    }
)


def startup_db_mutations_disabled() -> bool:
    value = os.getenv("NO_STARTUP_DB_MUTATIONS", "false").strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError("NO_STARTUP_DB_MUTATIONS must be exactly 'true' or 'false'")
    return value == "true"


def guarded_management_command(argv: list[str]) -> str | None:
    if not argv:
        return None

    executable = os.path.basename(argv[0])
    command: str | None = None
    if executable == "manage.py" and len(argv) >= 2:
        command = argv[1]
    elif executable in {"django-admin", "django-admin.py"} and len(argv) >= 2:
        command = argv[1]
    elif (
        executable.startswith("python")
        and len(argv) >= 4
        and argv[1:3] == ["-m", "django"]
    ):
        command = argv[3]

    if command is None:
        return None
    if command in STARTUP_SAFE_MANAGEMENT_COMMANDS:
        return None
    return command


class ModelHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "model_hub"

    def ready(self):
        # Import signals to register handlers
        # Avoid ClickHouse connections during migrations
        import sys

        import model_hub.signals  # noqa: F401

        if startup_db_mutations_disabled():
            if command := guarded_management_command(sys.argv):
                raise RuntimeError(
                    f"{command} is disabled while NO_STARTUP_DB_MUTATIONS=true"
                )
            logger.info(
                "Startup database mutations disabled; skipping seed and schema setup"
            )
            return

        if "migrate" in sys.argv or "makemigrations" in sys.argv:
            return

        if "pytest" in sys.modules:
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
        # Server profiles may set data_type_default_nullable=1, which would
        # auto-wrap bare LowCardinality(String)/Array(String)/key columns into
        # Nullable and break these CREATE statements (ClickHouse Code 43/44).
        # Force it off for schema DDL so the canonical types are honored.
        ddl_settings = {"data_type_default_nullable": 0}

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
                ch.execute(ddl, settings=ddl_settings)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"CH schema {name}: {e}")

        # Ensure materialized columns on CDC tables that PeerDB may recreate
        for alter in POST_DDL_ALTERS:
            try:
                ch.execute(alter, settings=ddl_settings)
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
        """Make the v2 spans schema match the running code, regardless of
        what shape `spans` is in beforehand.

        Runs only when ``CH25_DROP_LEGACY_CDC_CHAIN`` is set (caller-gated).
        Three input states are handled:

        - ``v1``: an old CH 24.x volume left a v1 ``spans`` table behind.
          Drop it (``CREATE TABLE IF NOT EXISTS`` would silently no-op on
          shape drift — every v2 read would then 500 on a missing column).
        - ``absent``: a fresh CH with no spans tables yet. The Django
          ``migrate`` step normally applies the v2 SQL via migration 0078,
          but ``FAST_STARTUP`` skips migrations on dev containers and the
          schema runner sits unused. Run it explicitly here.
        - ``v2``: spans already matches the running code. Still call
          ``ch25_apply_schema`` so schema files added since the last apply
          (e.g. 014 — index fix) land idempotently. The runner skips
          unchanged files via the sha256 in ``schema_versions``.
        - ``unknown``: refuse to touch the table — operator decides.

        The ``--force`` flag handles the v1 case (we just dropped the
        target so the stored sha is stale). In the absent / v2 cases it's
        equivalent to a normal apply because no rows have drifted.
        """
        shape = detect_fn(ch.execute)
        logger.info("CH25 v1-spans check: %s", shape)

        if shape == "unknown":
            logger.warning(
                "CH25: `spans` table exists with neither v1 nor v2 marker — "
                "refusing to auto-migrate. Inspect manually."
            )
            return

        if shape == "v1":
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

        # Run the schema runner for all paths that pass the check above.
        # Idempotent: sha-matched files are skipped, missing files are
        # applied. --force here is for the v1 case where we dropped the
        # stored target; in absent / v2 it's a no-op against the sha check.
        try:
            from django.core.management import call_command

            call_command("ch25_apply_schema", "--force", verbosity=1)
            logger.info("CH25 v2 schema apply complete")
        except Exception as e:
            logger.error(
                "CH25 v2 schema apply failed: %s. Run "
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

        # Do not infer the live table shape from a rollout flag. During the
        # US CH25 cutover the legacy table had already been removed while
        # CH25_DROP_LEGACY_CDC_CHAIN was false in some backend workers. Every
        # worker startup consequently emitted UNKNOWN_TABLE for tracer_trace.
        # Probe the actual read-only schema and warm whichever table exists.
        trace_warmup = None
        try:
            if ch.table_exists("traces"):
                trace_warmup = (
                    "SELECT project_id, count() FROM traces "
                    "WHERE is_deleted = 0 "
                    "AND created_at >= now() - INTERVAL 7 DAY "
                    "GROUP BY project_id",
                    "traces (7d)",
                )
            elif ch.table_exists("tracer_trace"):
                trace_warmup = (
                    "SELECT project_id, count() FROM tracer_trace "
                    "WHERE _peerdb_is_deleted = 0 "
                    "AND created_at >= now() - INTERVAL 7 DAY "
                    "GROUP BY project_id",
                    "tracer_trace (7d)",
                )
        except Exception as e:
            logger.warning("CH trace cache-warm table probe failed: %s", e)

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
            # CH25 reads trace metadata from the canonical `traces` table.
            # Warming a table that does not exist used to emit four 30-second
            # failures on every backend rollout in the US cluster.
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
        if trace_warmup is not None:
            warmup_queries.insert(1, trace_warmup)

        # When the legacy chain is retained (prod default), also warm
        # the legacy aggregate so it stays hot until the cutover. The
        # rollout flag is not proof that the table still exists: during a
        # staggered CH25 cutover the aggregate may already have been removed
        # while an old worker still has the flag disabled. Probe the actual
        # read-only schema first so startup never issues a guaranteed
        # UNKNOWN_TABLE query.
        if not should_drop_legacy_chain():
            try:
                if ch.table_exists("span_metrics_hourly"):
                    warmup_queries.append(
                        (
                            "SELECT count() FROM span_metrics_hourly "
                            "WHERE hour >= now() - INTERVAL 7 DAY",
                            "span_metrics_hourly (7d, legacy)",
                        )
                    )
            except Exception as e:
                logger.warning("CH legacy metrics cache-warm table probe failed: %s", e)
        for query, label in warmup_queries:
            try:
                ch.execute_read(
                    query,
                    timeout_ms=750,
                    settings={
                        "max_threads": 2,
                        "max_memory_usage": 128 * 1024 * 1024,
                        "max_bytes_to_read": 512 * 1024 * 1024,
                        "read_overflow_mode": "break",
                        "max_result_rows": 2000,
                        "result_overflow_mode": "break",
                        "timeout_overflow_mode": "break",
                    },
                )
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

        from model_hub.services.legacy_ch_tables import (
            ensure_events_table,
            ensure_llm_logs_table,
        )
        from tfc.utils.clickhouse import ClickHouseClientSingleton

        ch_instance = ClickHouseClientSingleton()

        # Idempotent CREATE TABLE IF NOT EXISTS, replicated on a multi-replica
        # cluster. Targets the connection's current database (CH_DATABASE).
        ensure_events_table(ch_instance)
        ensure_llm_logs_table(ch_instance)


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
