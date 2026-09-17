#!/usr/bin/env python3
"""Standalone development CLI for the bounded span-attribute backfill.

This entry point deliberately does not import Django or project settings. It
needs only Python 3.11+, ``clickhouse-connect``, and these sibling files:

* attribute_catalog_backfill.py
* attribute_catalog_builder.py
* attribute_catalog_codec.py
* attribute_suggestion_contract.py

That makes the five-file bundle runnable on a development host whose checked
out Django application is stale. The underlying runner remains the single
source of truth for scoping, SQL, checkpoints, write allowlists, and bounds.

Passwords are accepted only through environment variables so they do not enter
shell history or process argument listings:

* FI_CATALOG_BACKFILL_SOURCE_PASSWORD
* FI_CATALOG_BACKFILL_CATALOG_PASSWORD
* FI_CATALOG_BACKFILL_RUNTIME=development
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import signal
import socket
import sys
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from types import FrameType
from typing import Any, Protocol
from urllib.parse import urlsplit


def _load_runner_contract() -> dict[str, Any]:
    """Load sibling services without executing ClickHouse/Django package init.

    ``tracer.services.clickhouse.__init__`` imports the full analytics service
    and Django settings. A flat dev bundle has neither. Synthetic namespace
    packages let Python resolve only the four audited sibling modules.
    """

    sibling_dir = Path(__file__).resolve().parent
    repository_utils_dir = sibling_dir.parents[2] / "utils"
    package_names = (
        "tracer",
        "tracer.utils",
        "tracer.services",
        "tracer.services.clickhouse",
        "tracer.services.clickhouse.v2",
    )
    for name in package_names:
        module = sys.modules.get(name)
        if module is None:
            module = types.ModuleType(name)
            module.__package__ = name
            search_paths = [str(sibling_dir)]
            if name == "tracer.utils" and repository_utils_dir.is_dir():
                search_paths.append(str(repository_utils_dir))
            module.__path__ = search_paths  # type: ignore[attr-defined]
            sys.modules[name] = module
            parent_name, _, child_name = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None:
                setattr(parent, child_name, module)
        else:
            existing_path = getattr(module, "__path__", None)
            if existing_path is not None and str(sibling_dir) not in existing_path:
                existing_path.append(str(sibling_dir))
    module = importlib.import_module(
        "tracer.services.clickhouse.v2.attribute_catalog_backfill"
    )
    return vars(module)


_CONTRACT = _load_runner_contract()
CATALOG_BACKFILL_ACK: str = _CONTRACT["CATALOG_BACKFILL_ACK"]
CATALOG_BACKFILL_CLOUD_DEPLOYMENT: str = _CONTRACT["CATALOG_BACKFILL_CLOUD_DEPLOYMENT"]
CATALOG_BACKFILL_ENVIRONMENT: str = _CONTRACT["CATALOG_BACKFILL_ENVIRONMENT"]
DEFAULT_MAX_RUNTIME_SECONDS: int = _CONTRACT["DEFAULT_MAX_RUNTIME_SECONDS"]
DEFAULT_MAX_WINDOWS: int = _CONTRACT["DEFAULT_MAX_WINDOWS"]
DEFAULT_PAGE_ROWS: int = _CONTRACT["DEFAULT_PAGE_ROWS"]
DEFAULT_SOURCE_ATTRIBUTE_BYTES: int = _CONTRACT["DEFAULT_SOURCE_ATTRIBUTE_BYTES"]
DEFAULT_SOURCE_ATTRIBUTE_ENTRIES: int = _CONTRACT["DEFAULT_SOURCE_ATTRIBUTE_ENTRIES"]
MAX_CLICKHOUSE_CALL_SECONDS: float = _CONTRACT["MAX_CLICKHOUSE_CALL_SECONDS"]
CatalogAttributeBackfillRunner = _CONTRACT["CatalogAttributeBackfillRunner"]
CatalogBackfillConfig = _CONTRACT["CatalogBackfillConfig"]
CatalogBackfillError = _CONTRACT["CatalogBackfillError"]
TimedCatalogBackfillIO = _CONTRACT["TimedCatalogBackfillIO"]
parse_utc_hour = _CONTRACT["parse_utc_hour"]

SOURCE_PASSWORD_ENV = "FI_CATALOG_BACKFILL_SOURCE_PASSWORD"
CATALOG_PASSWORD_ENV = "FI_CATALOG_BACKFILL_CATALOG_PASSWORD"
RUNTIME_ENVIRONMENT_ENV = "FI_CATALOG_BACKFILL_RUNTIME"
RUNTIME_CLOUD_DEPLOYMENT_ENV = "CLOUD_DEPLOYMENT"
DEV_ENDPOINT_IDENTITY_ENV = "FI_PROPERTY_CATALOG_DEV_ENDPOINT_IDENTITY"
EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_USAGE = 2
EXIT_INCOMPLETE = 75


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int
    secure: bool
    database: str
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class CLIConfig:
    backfill: Any
    source: Endpoint
    catalog: Endpoint


class _Runner(Protocol):
    def run(self) -> Any: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Django-free, development-only CH25 span-attribute catalog backfill"
        )
    )
    parser.add_argument("--environment", required=True)
    parser.add_argument("--cloud-deployment", required=True)
    parser.add_argument("--dev-identity", required=True)
    parser.add_argument("--ack", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--epoch", required=True, type=int)

    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-database", required=True)
    parser.add_argument("--source-username", required=True)
    parser.add_argument("--catalog-url", required=True)
    parser.add_argument("--catalog-database", required=True)
    parser.add_argument("--catalog-username", required=True)

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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="execute bounded SELECT/build logic and perform zero writes",
    )
    mode.add_argument(
        "--execute-writes",
        action="store_true",
        help="write only the new catalog key/value/checkpoint tables",
    )
    return parser


def parse_config(
    argv: Sequence[str], *, environ: Mapping[str, str] | None = None
) -> CLIConfig:
    args = build_parser().parse_args(list(argv))
    env = os.environ if environ is None else environ
    if args.environment != CATALOG_BACKFILL_ENVIRONMENT:
        raise CatalogBackfillError("standalone catalog backfill is development-only")
    if env.get(RUNTIME_ENVIRONMENT_ENV) != CATALOG_BACKFILL_ENVIRONMENT:
        raise CatalogBackfillError(
            f"{RUNTIME_ENVIRONMENT_ENV} must explicitly equal development"
        )
    if env.get(RUNTIME_CLOUD_DEPLOYMENT_ENV) != CATALOG_BACKFILL_CLOUD_DEPLOYMENT:
        raise CatalogBackfillError(
            f"{RUNTIME_CLOUD_DEPLOYMENT_ENV} must explicitly equal DEV"
        )
    if env.get(DEV_ENDPOINT_IDENTITY_ENV) != args.dev_identity:
        raise CatalogBackfillError(
            f"{DEV_ENDPOINT_IDENTITY_ENV} must exactly match --dev-identity"
        )
    source_password = _required_environment_password(env, SOURCE_PASSWORD_ENV)
    catalog_password = _required_environment_password(env, CATALOG_PASSWORD_ENV)
    source = _parse_endpoint(
        args.source_url,
        database=args.source_database,
        username=args.source_username,
        password=source_password,
        label="source",
    )
    catalog = _parse_endpoint(
        args.catalog_url,
        database=args.catalog_database,
        username=args.catalog_username,
        password=catalog_password,
        label="catalog",
    )
    if source.database == catalog.database:
        raise CatalogBackfillError("source and catalog databases must be distinct")
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}"
    backfill_config = CatalogBackfillConfig(
        environment=args.environment,
        cloud_deployment=args.cloud_deployment,
        dev_identity=args.dev_identity,
        acknowledgement=args.ack,
        project_id=args.project_id,
        since=parse_utc_hour(args.since, "since"),
        until=parse_utc_hour(args.until, "until"),
        catalog_epoch=args.epoch,
        source_database=source.database,
        target_database=catalog.database,
        page_rows=args.page_rows,
        max_windows=args.max_windows,
        max_runtime_seconds=args.max_runtime_seconds,
        max_source_attribute_entries=args.max_source_attribute_entries,
        max_source_attribute_bytes=args.max_source_attribute_bytes,
        dry_run=bool(args.dry_run),
        worker_id=worker_id,
    ).validated()
    return CLIConfig(backfill=backfill_config, source=source, catalog=catalog)


def run(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    runner_factory: Callable[..., _Runner] = CatalogAttributeBackfillRunner,
    stdout: Callable[[str], None] = print,
    stderr: Callable[[str], None] = lambda message: print(message, file=sys.stderr),
) -> int:
    try:
        config = parse_config(argv, environ=environ)
    except CatalogBackfillError as exc:
        stderr(f"configuration refused: {exc}")
        return EXIT_USAGE

    if client_factory is None:
        try:
            import clickhouse_connect
        except ImportError:
            stderr("runtime failed: clickhouse-connect is not installed")
            return EXIT_RUNTIME_ERROR
        client_factory = clickhouse_connect.get_client

    source_client = None
    catalog_client = None
    source_cancel_client = None
    catalog_cancel_client = None
    try:
        source_client = _connect(client_factory, config.source)
        catalog_client = _connect(client_factory, config.catalog)
        source_cancel_client = _connect(client_factory, config.source)
        catalog_cancel_client = _connect(client_factory, config.catalog)
    except Exception as exc:
        _close_clients(
            source_client,
            catalog_client,
            source_cancel_client,
            catalog_cancel_client,
        )
        stderr(f"connection failed: {type(exc).__name__}")
        return EXIT_RUNTIME_ERROR

    stop = Event()
    try:
        with _graceful_stop(stop, stderr):
            runner = runner_factory(
                TimedCatalogBackfillIO(
                    source_client,
                    catalog_client,
                    source_cancel_client,
                    catalog_cancel_client,
                    target_database=config.backfill.target_database,
                ),
                config.backfill,
                stop_requested=stop.is_set,
            )
            summary = runner.run()
    except CatalogBackfillError as exc:
        stderr(f"backfill refused: {exc}")
        return EXIT_RUNTIME_ERROR
    except Exception as exc:
        # Driver exceptions can embed HTTP request details. Keep standalone
        # output credential-safe and let server-side logs carry diagnostics.
        stderr(f"backfill failed: {type(exc).__name__}")
        return EXIT_RUNTIME_ERROR
    finally:
        _close_clients(
            source_client,
            catalog_client,
            source_cancel_client,
            catalog_cancel_client,
        )

    stdout(json.dumps(asdict(summary), sort_keys=True, default=str))
    if summary.stopped:
        stderr("backfill stopped at a committed page boundary; rerun to resume")
        return EXIT_INCOMPLETE
    return EXIT_OK


def _connect(client_factory: Callable[..., Any], endpoint: Endpoint) -> Any:
    return client_factory(
        host=endpoint.host,
        port=endpoint.port,
        secure=endpoint.secure,
        username=endpoint.username,
        password=endpoint.password,
        database=endpoint.database,
        connect_timeout=min(5, int(MAX_CLICKHOUSE_CALL_SECONDS)),
        send_receive_timeout=MAX_CLICKHOUSE_CALL_SECONDS,
        query_retries=0,
        autogenerate_query_id=False,
    )


def _parse_endpoint(
    raw_url: str,
    *,
    database: str,
    username: str,
    password: str,
    label: str,
) -> Endpoint:
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise CatalogBackfillError(
            f"{label} URL must be an HTTP(S) origin without credentials, "
            "path, query, or fragment"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise CatalogBackfillError(f"{label} URL has an invalid port") from exc
    if not username:
        raise CatalogBackfillError(f"{label} username must not be empty")
    return Endpoint(
        host=parsed.hostname,
        port=port or (443 if parsed.scheme == "https" else 8123),
        secure=parsed.scheme == "https",
        database=database,
        username=username,
        password=password,
    )


def _required_environment_password(environ: Mapping[str, str], name: str) -> str:
    if name not in environ:
        raise CatalogBackfillError(
            f"required password environment variable {name} is unset"
        )
    return environ[name]


def _close_clients(*clients: Any) -> None:
    for client in clients:
        try:
            if client is not None:
                client.close()
        except Exception:
            pass


@contextmanager
def _graceful_stop(stop: Event, notify: Callable[[str], None]) -> Iterator[None]:
    previous: dict[int, Any] = {}

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        if not stop.is_set():
            notify(
                f"received signal {signum}; finishing and checkpointing the "
                "current page"
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


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
