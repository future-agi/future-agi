"""Development-only, bounded historical span-attribute catalog backfill."""

from __future__ import annotations

import json
import os
import signal
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from threading import Event
from types import FrameType
from typing import Any
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError, CommandParser

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.attribute_catalog_backfill import (
    CATALOG_BACKFILL_ACK,
    CATALOG_BACKFILL_ENVIRONMENT,
    DEFAULT_MAX_RUNTIME_SECONDS,
    DEFAULT_MAX_WINDOWS,
    DEFAULT_PAGE_ROWS,
    DEFAULT_SOURCE_ATTRIBUTE_BYTES,
    DEFAULT_SOURCE_ATTRIBUTE_ENTRIES,
    MAX_CLICKHOUSE_CALL_SECONDS,
    CatalogAttributeBackfillRunner,
    CatalogBackfillConfig,
    CatalogBackfillError,
    TimedCatalogBackfillIO,
    parse_utc_hour,
)

CATALOG_URL_ENV = "FI_CATALOG_CH_URL"
CATALOG_DATABASE_ENV = "FI_CATALOG_CH_DATABASE"
CATALOG_USERNAME_ENV = "FI_CATALOG_CH_USERNAME"
CATALOG_PASSWORD_ENV = "FI_CATALOG_CH_PASSWORD"


class Command(BaseCommand):
    help = (
        "Backfill one project's CH25 span attributes into the new catalog "
        "tables. Development-only; never activates readers or source streams."
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
        parser.add_argument("--source-database", required=True)
        parser.add_argument("--target-database", required=True)
        parser.add_argument("--page-rows", type=int, default=DEFAULT_PAGE_ROWS)
        parser.add_argument("--max-windows", type=int, default=DEFAULT_MAX_WINDOWS)
        parser.add_argument(
            "--max-runtime-seconds",
            type=int,
            default=DEFAULT_MAX_RUNTIME_SECONDS,
        )
        parser.add_argument(
            "--max-source-attribute-entries",
            type=int,
            default=DEFAULT_SOURCE_ATTRIBUTE_ENTRIES,
        )
        parser.add_argument(
            "--max-source-attribute-bytes",
            type=int,
            default=DEFAULT_SOURCE_ATTRIBUTE_BYTES,
        )
        parser.add_argument("--worker-id", default="")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args: Any, **options: Any) -> str | None:
        runtime_environment = str(os.getenv("ENV_TYPE", "")).strip().lower()
        if runtime_environment not in {"dev", "development"}:
            raise CommandError("catalog backfill requires ENV_TYPE=dev or development")
        try:
            since = parse_utc_hour(options["since"], "since")
            until = parse_utc_hour(options["until"], "until")
            worker_id = options["worker_id"] or (
                f"{socket.gethostname()}:{os.getpid()}"
            )
            config = CatalogBackfillConfig(
                environment=options["environment"],
                acknowledgement=options["ack"],
                project_id=options["project_id"],
                since=since,
                until=until,
                catalog_epoch=options["epoch"],
                source_database=options["source_database"],
                target_database=options["target_database"],
                page_rows=options["page_rows"],
                max_windows=options["max_windows"],
                max_runtime_seconds=options["max_runtime_seconds"],
                max_source_attribute_entries=options["max_source_attribute_entries"],
                max_source_attribute_bytes=options["max_source_attribute_bytes"],
                dry_run=options["dry_run"],
                worker_id=worker_id,
            ).validated()
        except CatalogBackfillError as exc:
            raise CommandError(str(exc)) from exc

        # Validate all operator input before importing or connecting. The only
        # source credential is the existing CH25 environment-backed config.
        # The catalog uses its own explicit FI_CATALOG_CH_* identity so a
        # writer never needs write authority on futureagi.spans.
        source_config = get_v2_config()
        if source_config["database"] != config.source_database:
            raise CommandError(
                "--source-database must exactly match configured CH25_DATABASE"
            )
        try:
            catalog_config = _catalog_connection_config(config.target_database)
        except CatalogBackfillError as exc:
            raise CommandError(str(exc)) from exc

        source_client = None
        catalog_client = None
        source_cancel_client = None
        catalog_cancel_client = None
        try:
            import clickhouse_connect

            source_kwargs = {
                "host": source_config["host"],
                "port": source_config["http_port"],
                "username": source_config["user"],
                "password": source_config["password"] or "",
                "database": config.source_database,
                "connect_timeout": min(5, int(MAX_CLICKHOUSE_CALL_SECONDS)),
                "send_receive_timeout": MAX_CLICKHOUSE_CALL_SECONDS,
                "query_retries": 0,
                "autogenerate_query_id": False,
            }
            catalog_kwargs = {
                "host": catalog_config["host"],
                "port": catalog_config["port"],
                "secure": catalog_config["secure"],
                "username": catalog_config["username"],
                "password": catalog_config["password"],
                "database": config.target_database,
                "connect_timeout": min(5, int(MAX_CLICKHOUSE_CALL_SECONDS)),
                "send_receive_timeout": MAX_CLICKHOUSE_CALL_SECONDS,
                "query_retries": 0,
                "autogenerate_query_id": False,
            }
            source_client = clickhouse_connect.get_client(**source_kwargs)
            catalog_client = clickhouse_connect.get_client(**catalog_kwargs)
            source_cancel_client = clickhouse_connect.get_client(**source_kwargs)
            catalog_cancel_client = clickhouse_connect.get_client(**catalog_kwargs)
        except Exception as exc:
            for partial_client in (
                source_client,
                catalog_client,
                source_cancel_client,
                catalog_cancel_client,
            ):
                try:
                    if partial_client is not None:
                        partial_client.close()
                except Exception:
                    pass
            raise CommandError(
                f"could not create bounded CH25 client: {type(exc).__name__}"
            ) from exc

        stop = Event()
        try:
            with _graceful_stop(stop, self.stderr.write):
                runner = CatalogAttributeBackfillRunner(
                    TimedCatalogBackfillIO(
                        source_client,
                        catalog_client,
                        source_cancel_client,
                        catalog_cancel_client,
                    ),
                    config,
                    stop_requested=stop.is_set,
                )
                summary = runner.run()
        except CatalogBackfillError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            for client in (
                source_client,
                catalog_client,
                source_cancel_client,
                catalog_cancel_client,
            ):
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass

        rendered = json.dumps(asdict(summary), sort_keys=True, default=str)
        self.stdout.write(rendered)
        if summary.stopped:
            self.stderr.write(
                "backfill stopped at a page boundary; rerun the identical "
                "command to resume"
            )
        return rendered


def _catalog_connection_config(target_database: str) -> dict[str, Any]:
    raw_url = os.getenv(CATALOG_URL_ENV, "").strip()
    database = os.getenv(CATALOG_DATABASE_ENV, "").strip()
    username = os.getenv(CATALOG_USERNAME_ENV, "").strip()
    password = os.getenv(CATALOG_PASSWORD_ENV)
    missing = [
        name
        for name, value in (
            (CATALOG_URL_ENV, raw_url),
            (CATALOG_DATABASE_ENV, database),
            (CATALOG_USERNAME_ENV, username),
            (CATALOG_PASSWORD_ENV, password),
        )
        if value is None or value == ""
    ]
    # Empty passwords are permitted only when the variable is present (the
    # isolated dev ClickHouse currently uses one); distinguish unset below.
    if CATALOG_PASSWORD_ENV in missing and password == "":
        missing.remove(CATALOG_PASSWORD_ENV)
    if missing:
        raise CatalogBackfillError(
            "missing explicit catalog connection setting(s): " + ", ".join(missing)
        )
    if database != target_database:
        raise CatalogBackfillError(
            "--target-database must exactly match FI_CATALOG_CH_DATABASE"
        )
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise CatalogBackfillError(
            "FI_CATALOG_CH_URL must be an HTTP(S) origin without credentials, "
            "path, query, or fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise CatalogBackfillError("FI_CATALOG_CH_URL has an invalid port") from exc
    return {
        "host": parsed.hostname,
        "port": port or (443 if parsed.scheme == "https" else 8123),
        "secure": parsed.scheme == "https",
        "username": username,
        "password": password or "",
    }


@contextmanager
def _graceful_stop(stop: Event, notify: Any) -> Iterator[None]:
    previous: dict[int, Any] = {}

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        if not stop.is_set():
            notify(
                f"received signal {signum}; finishing the current page and "
                "checkpoint before stopping"
            )
        stop.set()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


__all__ = ["Command", "CATALOG_BACKFILL_ACK", "CATALOG_BACKFILL_ENVIRONMENT"]
