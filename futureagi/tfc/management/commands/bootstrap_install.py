"""Install or upgrade everything the application keeps in its datastores.

``python manage.py bootstrap_install`` is the bootstrap Job of the Helm chart
(deploy/helm/futureagi: a pre-install/pre-upgrade hook, or post-install when
the chart bundles its own datastores). It runs the steps of the Standalone
install's deploy/platform/bin/bootstrap.py, in the same order, so every
install path prepares the datastores the same way:

  1. wait until Postgres, ClickHouse, Redis and Temporal accept connections
  2. createcachetable (a failure is logged, not fatal, as in entrypoint.sh)
  3. migrate, then seed_system_evals
  4. ClickHouse native schema (``oss_cdc_install --phase native --apply``)
  5. observed-attribute index: its ClickHouse database, users and grants
  6. the eval-task search attributes on the Temporal namespace
  7. change data capture as FI_CDC_MODE says
     (``oss_outbox_cdc.ensure_installed``: capture triggers plus the drain
     schedules for ``outbox``, both removed for ``peerdb``/``off``)
  8. register_temporal_schedules

Every step is idempotent, so the Job runs on every install and upgrade. A
failure exits non-zero with the reason, and Kubernetes retries the Job.

Database changes need the same explicit authorization as every other
bootstrap path: NO_STARTUP_DB_MUTATIONS=false, and with a hosted ENV_TYPE
(prod, production, staging) also SERVICE_TYPE=bootstrap and
STARTUP_DB_MUTATION_MODE=operator. The chart sets them on the Job only; the
API and worker pods keep NO_STARTUP_DB_MUTATIONS=true.

Unlike bootstrap.py there is no local state (no /data volume), so migrate and
the seeds run every time; with nothing to apply they finish in seconds.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

CLICKHOUSE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
OBSERVED_CATALOG_TABLES = ("observed_attribute_keys", "observed_attribute_values")
OUTBOX_CDC_MODULE = "tracer.services.clickhouse.oss_outbox_cdc"
# The published local-only defaults every compose file falls back to. The
# chart always passes generated passwords instead.
CATALOG_WRITER_DEFAULT = "oss-observed-writer-local-only"
CATALOG_READER_DEFAULT = "oss-observed-reader-local-only"
HOSTED_ENV_TYPES = ("prod", "production", "staging")


class BootstrapError(CommandError):
    """A failure whose message already says what to do; no traceback needed."""


def endpoint(value: str, default_port: int) -> tuple[str, int]:
    """``host:port`` (or a bare host) as a (host, port) pair."""
    host, _, port = value.rpartition(":")
    if host and port.isdigit():
        return host.strip("[]"), int(port)
    return value.strip("[]"), default_port


def url_endpoint(url: str, default_port: int) -> tuple[str, int] | None:
    """(host, port) of a redis:// or http:// URL; None without a host."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname:
        return None
    return parts.hostname, port or default_port


def datastore_endpoints(env=None) -> list[tuple[str, str, int]]:
    """Every (name, host, port) the bootstrap needs, deduplicated.

    Postgres is checked where Django connects (PGBOUNCER_HOST/PORT) and where
    the CDC installer connects (PG_HOST/PG_PORT); they are the same unless a
    pooler sits in front.
    """
    env = os.environ if env is None else env
    found: list[tuple[str, str, int]] = []

    def add(name: str, host: str | None, port: int) -> None:
        if host and not any(h == host and p == port for _, h, p in found):
            found.append((name, host, port))

    database = settings.DATABASES["default"]
    add("Postgres", str(database.get("HOST") or ""), int(database.get("PORT") or 5432))
    add("Postgres", env.get("PG_HOST"), int(env.get("PG_PORT") or 5432))
    add(
        "ClickHouse",
        env.get("CH_HOST", "clickhouse"),
        int(env.get("CH_HTTP_PORT") or 8123),
    )
    redis = url_endpoint(getattr(settings, "REDIS_URL", "") or "", 6379)
    if redis is None:
        redis = (env.get("REDIS_HOST", "redis"), int(env.get("REDIS_PORT") or 6379))
    add("Redis", *redis)
    add("Temporal", *endpoint(env.get("TEMPORAL_HOST", "localhost:7233"), 7233))
    return found


def wait_tcp(
    name: str,
    host: str,
    port: int,
    timeout: float,
    log: Callable[[str], None],
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = clock() + timeout
    announced = None
    while True:
        try:
            socket.create_connection((host, port), timeout=2).close()
            return
        except OSError:
            now = clock()
            if now >= deadline:
                raise BootstrapError(
                    f"{name} at {host}:{port} is not reachable after {timeout:.0f}s. "
                    "Check the host, port and network policy in the chart values."
                ) from None
            if announced is None or now - announced >= 30:
                log(f"waiting for {name} at {host}:{port}")
                announced = now
            sleep(1)


def run_cli(main, argv: list[str]) -> int:
    """Run a module's CLI entry point in this process and return its exit code."""
    try:
        return main(argv) or 0
    except SystemExit as exit_:
        return exit_.code if isinstance(exit_.code, int) else 1


def with_retries(
    what: str,
    action: Callable[[], None],
    log: Callable[[str], None],
    *,
    attempts: int = 12,
    delay: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Temporal accepts connections a little before it serves requests.

    A BootstrapError is final: it already says what to do."""
    for attempt in range(1, attempts + 1):
        try:
            action()
            return
        except BootstrapError:
            raise
        except Exception as exc:
            if attempt == attempts:
                raise
            log(
                f"{what} failed ({type(exc).__name__}: {exc}); retrying in {delay:.0f}s"
            )
            sleep(delay)


def mutations_authorized(command: str = "migrate") -> bool:
    from model_hub.apps import explicit_management_mutation_authorized

    return explicit_management_mutation_authorized(["manage.py", command])


def authorization_hint() -> str:
    env_type = os.environ.get("ENV_TYPE", "")
    hint = "Set NO_STARTUP_DB_MUTATIONS=false on the bootstrap job"
    if env_type.strip().lower() in HOSTED_ENV_TYPES:
        hint += (
            f"; with ENV_TYPE={env_type} also SERVICE_TYPE=bootstrap and "
            "STARTUP_DB_MUTATION_MODE=operator"
        )
    return hint + ". The Helm chart sets these on its bootstrap job only."


def call(command: str, log: Callable[[str], None], **options) -> None:
    """call_command under the same authorization as ``manage.py <command>``."""
    from django.apps import apps
    from django.core.management import call_command
    from django.db.models.signals import post_migrate

    from model_hub.apps import (
        OPERATOR_STARTUP_MUTATION_COMMANDS,
        _seed_prompt_labels_after_migrate,
    )

    if command in OPERATOR_STARTUP_MUTATION_COMMANDS and not mutations_authorized(
        command
    ):
        raise BootstrapError(
            f"{command} is not authorized here. {authorization_hint()}"
        )
    if command == "migrate":
        # ModelHubConfig.ready() connects this only when argv is `migrate`.
        post_migrate.connect(
            _seed_prompt_labels_after_migrate,
            sender=apps.get_app_config("model_hub"),
            dispatch_uid="model_hub_seed_default_prompt_labels",
        )
    started = time.monotonic()
    log(f"{command} ...")
    call_command(command, **options)
    log(f"{command} done in {time.monotonic() - started:.1f}s")


def migrate_and_seed(log: Callable[[str], None]) -> None:
    try:
        call("createcachetable", log, database="default")
    except BootstrapError:
        raise
    except Exception as exc:  # non-fatal, as in entrypoint.sh and bootstrap.py
        log(f"createcachetable failed (continuing): {exc}")
    call("migrate", log, interactive=False, verbosity=1)
    call("seed_system_evals", log)


def clickhouse_native_schema(log: Callable[[str], None], timeout: int) -> None:
    from tracer.services.clickhouse import oss_cdc_install

    log("ClickHouse native schema ...")
    started = time.monotonic()
    if run_cli(
        oss_cdc_install.main,
        ["--phase", "native", "--apply", "--timeout", str(timeout)],
    ):
        raise BootstrapError(
            "ClickHouse native schema install failed (see above). Check CH_HOST, "
            "CH_PORT, CH_HTTP_PORT, CH_DATABASE and that the ClickHouse user may "
            "create tables."
        )
    log(f"ClickHouse native schema done in {time.monotonic() - started:.1f}s")


def property_catalog(log: Callable[[str], None], env=None) -> None:
    """The observed-attribute index: database, schema, users and grants.

    Same statements as bootstrap.py's property_catalog() and
    scripts/property_catalog_oss/bootstrap_clickhouse.sh. The ClickHouse user
    needs CREATE DATABASE and access management (CREATE USER, GRANT)."""
    import clickhouse_connect

    from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements

    env = os.environ if env is None else env
    root = Path(settings.BASE_DIR).parent
    source = (
        env.get("FI_CH_DATABASE")
        or env.get("CH25_DATABASE")
        or env.get("CH_DATABASE")
        or "default"
    )
    target = env.get("PROPERTY_CATALOG_DATABASE") or "property_catalog"
    for name, value in (("source", source), ("PROPERTY_CATALOG_DATABASE", target)):
        if not CLICKHOUSE_IDENTIFIER.fullmatch(value):
            raise BootstrapError(f"{name} database must be a ClickHouse identifier")
    if target.lower() in ("system", "information_schema") or target == source:
        raise BootstrapError("the observed-attribute index needs its own database")
    writer = env.get("PROPERTY_CATALOG_CONSUMER_PASSWORD") or CATALOG_WRITER_DEFAULT
    # The same reader password the API connects with.
    reader = env.get("PROPERTY_CATALOG_CH_PASSWORD") or CATALOG_READER_DEFAULT

    log(f"observed-attribute index in {target} ...")
    connect = {
        "host": env.get("CH_HOST", "clickhouse"),
        "port": int(env.get("CH_HTTP_PORT") or 8123),
        "username": env.get("CH_USERNAME") or env.get("CH_USER") or "default",
        "password": env.get("CH_PASSWORD", ""),
    }
    admin = clickhouse_connect.get_client(**connect)
    try:
        admin.command(f"CREATE DATABASE IF NOT EXISTS `{target}`")
        catalog = clickhouse_connect.get_client(database=target, **connect)
        try:
            schema = root / "tracer/services/clickhouse/v2/observed_catalog/schema.sql"
            for statement in split_statements(schema.read_text()):
                catalog.command(statement)
        finally:
            catalog.close()
        # IF NOT EXISTS must not make an incompatible existing index look ready.
        validation = root / "scripts/property_catalog_oss/validate_clickhouse.sql"
        rows = admin.query(
            validation.read_text().strip().rstrip(";"), parameters={"database": target}
        ).result_rows
        if [tuple(row) for row in rows] not in ([(1,)], [(True,)]):
            raise BootstrapError(
                f"observed-attribute index in {target} is incompatible "
                "(columns, identity, engine or constraints)"
            )
        for user, password, extra in (
            ("observed_catalog_writer", writer, ""),
            ("observed_catalog_reader", reader, " SETTINGS readonly=2"),
        ):
            identified = "IDENTIFIED WITH sha256_password BY {password:String} HOST ANY"
            admin.command(
                f"CREATE USER IF NOT EXISTS {user} {identified}",
                parameters={"password": password},
            )
            admin.command(
                f"ALTER USER {user} {identified}{extra}",
                parameters={"password": password},
            )
        for table in OBSERVED_CATALOG_TABLES:
            admin.command(
                f"GRANT SELECT, INSERT ON `{target}`.{table} TO observed_catalog_writer"
            )
            admin.command(
                f"GRANT SELECT ON `{target}`.{table} TO observed_catalog_reader"
            )
    finally:
        admin.close()
    log("observed-attribute index done")


def register_search_attributes(log: Callable[[str], None]) -> None:
    from tfc.temporal import TEMPORAL_NAMESPACE
    from tfc.temporal.common.client import get_client
    from tfc.temporal.eval_tasks.registration import (
        register_search_attributes as register,
    )

    async def run() -> bool:
        return await register(await get_client(), TEMPORAL_NAMESPACE)

    if asyncio.run(run()):
        log("registered the eval-task search attributes")
    else:
        log("eval-task search attributes already registered")


def change_data_capture(
    log: Callable[[str], None], *, attempts: int, delay: float
) -> None:
    """Make Postgres -> ClickHouse capture match FI_CDC_MODE.

    ensure_installed() installs capture and its drain schedules for
    ``outbox`` and removes what an earlier ``outbox`` run left for
    ``peerdb``/``off``, so capture never runs without a drain."""
    try:
        from tracer.services.clickhouse import oss_outbox_cdc
    except ModuleNotFoundError as exc:
        if exc.name != OUTBOX_CDC_MODULE:
            raise
        log("this image has no outbox CDC installer; skipping change data capture")
        return
    try:
        mode = oss_outbox_cdc.cdc_mode()
    except ValueError as exc:
        raise BootstrapError(str(exc)) from None

    def ensure() -> None:
        try:
            result = oss_outbox_cdc.ensure_installed()
        except ValueError as exc:
            # The installer's own errors say what to do; retrying cannot help.
            raise BootstrapError(
                f"change data capture (FI_CDC_MODE={mode}): {exc}"
            ) from None
        except Exception as exc:
            # Driver messages can carry credentials or row data.
            raise RuntimeError(f"{type(exc).__name__} from the CDC installer") from None
        log(f"change data capture: {json.dumps(result, default=str)}")

    log(f"change data capture (FI_CDC_MODE={mode}) ...")
    with_retries("change data capture", ensure, log, attempts=attempts, delay=delay)


def summary(env=None) -> list[str]:
    """What this run is about to prepare. Never a secret's value."""
    env = os.environ if env is None else env
    database = settings.DATABASES["default"]
    lines = [
        f"Future AGI {env.get('FUTURE_AGI_VERSION') or 'unknown version'} "
        f"(ENV_TYPE={env.get('ENV_TYPE', 'local')}, FI_CDC_MODE="
        f"{env.get('FI_CDC_MODE', 'peerdb')})",
        f"Postgres   {database.get('HOST')}:{database.get('PORT')}/{database.get('NAME')}"
        f" as {database.get('USER')}",
        f"ClickHouse {env.get('CH_HOST', 'clickhouse')}:{env.get('CH_HTTP_PORT', '8123')}"
        f"/{env.get('CH_DATABASE', 'default')}",
        f"Temporal   {env.get('TEMPORAL_HOST', 'localhost:7233')} namespace "
        f"{env.get('TEMPORAL_NAMESPACE', 'default')}",
    ]
    redis = url_endpoint(getattr(settings, "REDIS_URL", "") or "", 6379)
    if redis:
        lines.append(f"Redis      {redis[0]}:{redis[1]}")
    return lines


class Command(BaseCommand):
    help = (
        "Install or upgrade the database schema, seeds, ClickHouse schema, "
        "change data capture and Temporal schedules (idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--wait-timeout",
            type=int,
            default=int(os.environ.get("FI_BOOTSTRAP_WAIT_SECONDS") or 600),
            help="Seconds to wait for each datastore to accept connections (default 600).",
        )
        parser.add_argument(
            "--clickhouse-timeout",
            type=int,
            default=600,
            help="Deadline in seconds for the ClickHouse native schema (default 600).",
        )
        parser.add_argument(
            "--skip-property-catalog",
            action="store_true",
            default=os.environ.get("FI_BOOTSTRAP_SKIP_PROPERTY_CATALOG", "").lower()
            in ("1", "true", "yes", "on"),
            help=(
                "Do not create the observed-attribute index database and users "
                "(for a ClickHouse user without CREATE USER; create them yourself)."
            ),
        )
        parser.add_argument(
            "--temporal-attempts",
            type=int,
            default=12,
            help="Attempts for each Temporal step, 5 s apart (default 12).",
        )

    def log(self, message: str) -> None:
        self.stdout.write(f"[bootstrap] {message}")
        self.stdout.flush()

    def handle(self, *args, **options):
        started = time.monotonic()
        # Django's historical 0078 migration must never replay the ClickHouse SQL glob.
        os.environ.setdefault("FI_SKIP_CH25_MIGRATION", "1")
        if not mutations_authorized():
            raise BootstrapError(
                f"database changes are not authorized in this process. {authorization_hint()}"
            )
        for line in summary():
            self.log(line)

        for name, host, port in datastore_endpoints():
            wait_tcp(name, host, port, options["wait_timeout"], self.log)

        attempts = max(1, options["temporal_attempts"])
        migrate_and_seed(self.log)
        clickhouse_native_schema(self.log, options["clickhouse_timeout"])
        if options["skip_property_catalog"]:
            self.log("observed-attribute index skipped (--skip-property-catalog)")
        else:
            property_catalog(self.log)
        with_retries(
            "Temporal search attributes",
            lambda: register_search_attributes(self.log),
            self.log,
            attempts=attempts,
        )
        change_data_capture(self.log, attempts=attempts, delay=5)
        with_retries(
            "Temporal schedules",
            lambda: call("register_temporal_schedules", self.log),
            self.log,
            attempts=attempts,
        )
        self.log(f"ready in {time.monotonic() - started:.0f}s")
