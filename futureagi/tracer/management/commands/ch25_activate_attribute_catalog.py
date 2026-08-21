"""Audit and activate one immutable DEV attribute-catalog epoch."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from tracer.management.commands.ch25_backfill_attribute_catalog import (
    _catalog_connection_config,
)
from tracer.services.clickhouse.v2.attribute_catalog_activation import (
    ACTIVATION_TABLE,
    CATALOG_ACTIVATION_ACK,
    CATALOG_ACTIVATION_ENVIRONMENT,
    CATALOG_ACTIVATION_WRITE_TABLES,
    SOURCE_STREAM_TABLE,
    CatalogActivationConfig,
    CatalogActivationError,
    CatalogFrozenEpochActivator,
)
from tracer.services.clickhouse.v2.attribute_catalog_backfill import parse_utc_hour

_INSERT_TYPES = {
    SOURCE_STREAM_TABLE: (
        "UUID",
        "UInt16",
        "UUID",
        "UInt16",
        "UInt64",
        "UInt64",
        "UInt64",
        "FixedString(64)",
        "FixedString(64)",
        "Enum8('open' = 1, 'frozen' = 2, 'complete' = 3, 'gap' = 4, 'failed' = 5)",
        "UInt64",
        "Array(String)",
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "Nullable(DateTime64(6, 'UTC'))",
        "UInt64",
    ),
    ACTIVATION_TABLE: (
        "UUID",
        "UInt16",
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "Enum8('shadow' = 1, 'active' = 2, 'disabled' = 3)",
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "UInt64",
    ),
}


class _ClickHouseActivationIO:
    def __init__(self, client) -> None:
        self.client = client

    def select(self, sql, params, *, settings):
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")) or ";" in sql:
            raise CatalogActivationError("activation audit must be one SELECT")
        result = self.client.query(
            sql,
            parameters=dict(params),
            settings=dict(settings),
        )
        if hasattr(result, "named_results"):
            return list(result.named_results())
        if hasattr(result, "result_rows") and hasattr(result, "column_names"):
            return [
                dict(zip(result.column_names, row, strict=True))
                for row in result.result_rows
            ]
        raise CatalogActivationError("ClickHouse SELECT returned an unsupported result")

    def insert(self, table, rows, columns, *, settings):
        unqualified = table.rsplit(".", 1)[-1].strip("`")
        if unqualified not in CATALOG_ACTIVATION_WRITE_TABLES:
            raise CatalogActivationError("activation write target is not allowlisted")
        query_settings = dict(settings)
        query_id = str(query_settings.pop("query_id", ""))
        self.client.insert(
            table,
            list(rows),
            column_names=list(columns),
            column_type_names=list(_INSERT_TYPES[unqualified]),
            settings=query_settings,
            transport_settings=(
                {"X-ClickHouse-Query-Id": query_id} if query_id else {}
            ),
        )


class Command(BaseCommand):
    help = (
        "SELECT-audit every hour of one backfill-only DEV epoch, freeze its "
        "source evidence, and activate that exact project/window."
    )
    requires_system_checks: list[str] = []
    requires_migrations_checks = False

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--environment", required=True)
        parser.add_argument("--ack", required=True)
        parser.add_argument("--project-id", required=True)
        parser.add_argument("--since", required=True)
        parser.add_argument("--until", required=True)
        parser.add_argument("--epoch", required=True, type=int)
        parser.add_argument("--target-database", required=True)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> str:
        if str(os.getenv("ENV_TYPE", "")).strip().lower() not in {
            "dev",
            "development",
        }:
            raise CommandError("catalog activation requires ENV_TYPE=dev")
        try:
            config = CatalogActivationConfig(
                environment=options["environment"],
                acknowledgement=options["ack"],
                project_id=options["project_id"],
                catalog_epoch=options["epoch"],
                since=parse_utc_hour(options["since"], "since"),
                until=parse_utc_hour(options["until"], "until"),
                target_database=options["target_database"],
                dry_run=bool(options["dry_run"]),
            ).validated()
            connection = _catalog_connection_config(config.target_database)
        except CatalogActivationError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        client = None
        try:
            import clickhouse_connect

            client = clickhouse_connect.get_client(
                host=connection["host"],
                port=connection["port"],
                secure=connection["secure"],
                username=connection["username"],
                password=connection["password"],
                database=config.target_database,
                connect_timeout=5,
                send_receive_timeout=10,
                query_retries=0,
                autogenerate_query_id=False,
            )
            summary = CatalogFrozenEpochActivator(
                _ClickHouseActivationIO(client),
                config,
            ).run()
        except CatalogActivationError as exc:
            raise CommandError(str(exc)) from exc
        except Exception as exc:
            raise CommandError(
                f"catalog activation failed: {type(exc).__name__}"
            ) from exc
        finally:
            try:
                if client is not None:
                    client.close()
            except Exception:
                pass

        rendered = json.dumps(asdict(summary), sort_keys=True, default=str)
        self.stdout.write(rendered)
        return rendered


__all__ = [
    "CATALOG_ACTIVATION_ACK",
    "CATALOG_ACTIVATION_ENVIRONMENT",
    "Command",
]
