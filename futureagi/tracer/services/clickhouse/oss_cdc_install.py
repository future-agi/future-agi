"""Explicit OSS CDC catalog bootstrap; default invocation is read-only.

Run after PG migrations, native ClickHouse initialization and PeerDB setup:
    python -m tracer.services.clickhouse.oss_cdc_install
    python -m tracer.services.clickhouse.oss_cdc_install --apply

For a fresh native database before starting any writers, use --phase native.
Its default check is also read-only; --apply creates missing native objects only.

Uses the existing PG_*, CH_*, SRC_PG_*, DST_CH_* connection settings. No Django
startup hooks, source-table migration replay, mirror creation/resync, or operator
epoch/revision. The same PG advisory lock serializes cooperating installers; the
caller must also keep source schema/deployment changes out of this window.
No operation is automatically retried after applying starts. Partial results are
retained and independently re-inspected on the next explicit invocation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import sys
import time
import urllib.request
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import urllib3

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_upgrade as upgrade
from tracer.services.clickhouse import oss_native_bootstrap as native
from tracer.services.clickhouse.oss_cdc_inventory import InventoryPending
from tracer.services.clickhouse.oss_cdc_source import inspect_source
from tracer.services.clickhouse.v2 import apply_schema
from tracer.services.clickhouse.v2.schema_topology import is_hosted_production


class InstallError(ValueError):
    """Safe-to-display configuration or operation error, without connection data."""


def _port(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise InstallError("connection port must be an integer")
    result = int(value)
    if not 0 < result < 65536:
        raise InstallError("connection port is out of range")
    return result


def _origin(value):
    try:
        parsed = urlsplit(value)
        if (
            not isinstance(value, str)
            or any(c.isspace() or ord(c) < 32 for c in value)
            or parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.port == 0
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise InstallError(
            "URL must be an HTTP(S) origin without credentials or query"
        ) from None
    return parsed


@dataclass(frozen=True)
class Config:
    source: core.PeerTarget
    destination: core.PeerTarget
    http_port: int
    pg_user: str
    ch_user: str
    pg_password: str = field(repr=False)
    ch_password: str = field(repr=False)
    peerdb_url: str
    hosted: bool = False

    def __post_init__(self):
        for target in (self.source, self.destination):
            if (
                not isinstance(target, core.PeerTarget)
                or any(c in target.host for c in "/,\\?#@\x00")
                or any(c.isspace() for c in target.host)
            ):
                raise InstallError("one explicit TCP host is required per database")
            if (
                "\x00" in target.database
                or "=" in target.database
                or target.database.startswith(("postgres://", "postgresql://"))
            ):
                raise InstallError(
                    "database must be a literal name, not a connection string"
                )
        core._definitions(self.destination.database)
        if type(self.http_port) is not int or not 0 < self.http_port < 65536:
            raise InstallError("invalid ClickHouse HTTP port")
        if type(self.hosted) is not bool:
            raise InstallError("invalid deployment topology")
        if any(
            not isinstance(v, str) or not v or "\x00" in v
            for v in (self.pg_user, self.ch_user)
        ):
            raise InstallError("explicit database users are required")
        if any(
            not isinstance(v, str) or "\x00" in v
            for v in (self.pg_password, self.ch_password)
        ):
            raise InstallError("invalid connection password")
        _origin(self.peerdb_url)

    @classmethod
    def from_env(
        cls,
        env,
        *,
        source_peer="pg_source",
        destination_peer="ch_dest",
        peerdb_url=None,
    ):
        def setting(primary, fallback, default):
            value = env.get(primary, env.get(fallback, default))
            if not isinstance(value, str) or not value or "\x00" in value:
                raise InstallError(f"{primary}/{fallback} must be configured")
            return value

        source = core.PeerTarget(
            source_peer,
            setting("SRC_PG_HOST", "PG_HOST", "postgres"),
            _port(setting("SRC_PG_PORT", "PG_PORT", "5432")),
            setting("SRC_PG_DB", "PG_DB", "futureagi"),
        )
        destination = core.PeerTarget(
            destination_peer,
            setting("DST_CH_HOST", "CH_HOST", "clickhouse"),
            _port(setting("DST_CH_PORT", "CH_PORT", "9000")),
            setting("DST_CH_DB", "CH_DATABASE", "default"),
        )
        # The current core requires co-located native prerequisites. Never quietly
        # inspect or initialize another DB because one connection setting differs.
        for key, expected in (
            ("CH_DATABASE", destination.database),
            ("CH_HOST", destination.host),
            ("CH25_DATABASE", destination.database),
            ("CH25_HOST", destination.host),
            ("FI_CH_DATABASE", destination.database),
        ):
            if key in env and env[key] != expected:
                raise InstallError(
                    "split native/CDC targets require separate startup qualification"
                )
        http_port = _port(env.get("CH_HTTP_PORT", "8123"))
        for key, expected in (
            ("CH_PORT", destination.port),
            ("CH25_TCP_PORT", destination.port),
            ("CH25_HTTP_PORT", http_port),
        ):
            if key in env and _port(env[key]) != expected:
                raise InstallError("native/CDC connection ports must agree")
        if "FI_CH_URL" in env:
            writer = _origin(env["FI_CH_URL"])
            if (
                writer.scheme != "http"
                or writer.hostname != destination.host
                or (writer.port or 80) != http_port
            ):
                raise InstallError("collector and CDC ClickHouse targets must agree")
        url = (
            peerdb_url
            if peerdb_url is not None
            else env.get("PEERDB_FLOW_SERVER_HTTP", "http://peerdb-flow-api:8113")
        )
        _origin(url)
        passwords = [
            env.get(primary, env.get(fallback, default))
            for primary, fallback, default in (
                ("SRC_PG_PASSWORD", "PG_PASSWORD", "futureagi"),
                ("DST_CH_PASSWORD", "CH_PASSWORD", ""),
            )
        ]
        if any(not isinstance(p, str) or "\x00" in p for p in passwords):
            raise InstallError("invalid connection password")
        return cls(
            source,
            destination,
            http_port,
            setting("SRC_PG_USER", "PG_USER", "futureagi"),
            setting("DST_CH_USER", "CH_USERNAME", "default"),
            *passwords,
            url.rstrip("/"),
            is_hosted_production(
                env.get("ENV_TYPE", ""), env.get("CLOUD_DEPLOYMENT", "")
            )
            or str(env.get("CH_USE_REPLICATED_ENGINES", "false")).lower()
            in ("true", "1", "yes", "on"),
        )


class Deadline:
    def __init__(self, seconds, clock=time.monotonic):
        if type(seconds) is not int or seconds <= 0:
            raise InstallError("timeout must be a positive number of seconds")
        self.clock, self.end = clock, clock() + seconds

    def remaining(self, cap=10):
        remaining = self.end - self.clock()
        if remaining <= 0:
            raise InstallError("CDC setup deadline exceeded; partial results retained")
        return min(cap, remaining)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise InstallError("PeerDB redirects are not accepted")


def peerdb_transport(origin, deadline, *, allow_create=False):
    _origin(origin)
    if type(allow_create) is not bool:
        raise InstallError("PeerDB create opt-in must be explicitly true or false")
    # Do not inherit HTTP proxies or follow redirects to another credential scope.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def request(method, path, body=None):
        if not (
            method == "GET"
            and (
                path in ("/v1/mirrors/list", "/v1/peers/list")
                or path.startswith("/v1/peers/info/")
            )
            or method == "POST"
            and path == "/v1/mirrors/status"
            or allow_create
            and method == "POST"
            and path in ("/v1/peers/create", "/v1/flows/cdc/create")
        ):
            raise InstallError("only read-only PeerDB inventory routes are permitted")
        req = urllib.request.Request(
            origin + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=deadline.remaining()) as response:
                data = response.read(1048577)
                deadline.remaining()
                if len(data) > 1048576:
                    raise InstallError("PeerDB response exceeds inventory bound")
                return json.loads(data)
        except Exception:
            raise InstallError("PeerDB inventory request failed") from None

    return request


class _SingleAttemptPool(urllib3.PoolManager):
    """Disable both urllib3 and ClickHouse Connect's implicit transport retries.

    Connect retries remotely closed sockets even with query_retries=0. Converting
    its transport exception here prevents replay after an uncertain DDL/INSERT.
    A private pool also avoids inheriting proxy routing or another job's session.
    """

    def request(self, *args, **kwargs):
        kwargs.update(retries=False, redirect=False)
        try:
            return super().request(*args, **kwargs)
        except urllib3.exceptions.HTTPError:
            raise InstallError(
                "ClickHouse transport failed; inspect partial state before retrying"
            ) from None


@contextmanager
def _wall_clock_limit(seconds):
    """The shipped Docker/Linux CLI bounds the entire job, not just each read."""
    if not hasattr(signal, "setitimer"):
        raise InstallError("run this installer in the supported Linux container")
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    if previous_timer != (0.0, 0.0):
        raise InstallError("cannot replace an existing process deadline")

    def expired(*_):
        raise InstallError("CDC setup deadline exceeded; partial results retained")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def run(
    config: Config,
    *,
    apply=False,
    phase="cdc",
    wait_for_mirrors=False,
    timeout=300,
    pg_connect=None,
    ch_connect=None,
    request=None,
):
    """Inspect by default; acquire a session-local installer lock only for apply."""
    if type(apply) is not bool:
        raise InstallError("apply must be explicitly true or false")
    if phase not in ("cdc", "native"):
        raise InstallError("unknown installation phase")
    if type(wait_for_mirrors) is not bool or (wait_for_mirrors and phase != "cdc"):
        raise InstallError("mirror readiness waiting is only supported for CDC")
    if apply and config.hosted:
        raise InstallError(
            "hosted/replicated apply requires the separately qualified deployment path"
        )
    # Empty libpq parameters do NOT override environment defaults. Refuse hidden
    # routing before either client connects; TLS settings remain configurable.
    if any(os.environ.get(key) for key in ("PGHOSTADDR", "PGSERVICE")):
        raise InstallError(
            "PGHOSTADDR/PGSERVICE routing is not supported by this installer"
        )
    import clickhouse_connect
    import psycopg

    deadline = Deadline(timeout)
    pg_connect = pg_connect or psycopg.connect
    ch_connect = ch_connect or clickhouse_connect.get_client
    request = request or peerdb_transport(config.peerdb_url, deadline)
    with ExitStack() as stack:
        # No read/write pool, guessed alternate database or automatic failover.
        pg = pg_connect(
            host=config.source.host,
            port=config.source.port,
            dbname=config.source.database,
            user=config.pg_user,
            password=config.pg_password,
            connect_timeout=max(1, math.ceil(deadline.remaining(3))),
            options="-c statement_timeout=10000 -c lock_timeout=2000",
            autocommit=False,
        )
        stack.callback(pg.close)
        pg.read_only = True
        pg.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        if apply:
            identity = json.dumps(config.destination.endpoint, separators=(",", ":"))
            key = int.from_bytes(
                hashlib.sha256(("oss-cdc-bootstrap:" + identity).encode()).digest()[:8],
                "big",
                signed=True,
            )
            if pg.execute(
                "SELECT pg_try_advisory_xact_lock(%s)", (key,)
            ).fetchall() != [(True,)]:
                raise InstallError("another catalog installer holds the target lock")
        pool = _SingleAttemptPool()
        stack.callback(pool.clear)
        raw = ch_connect(
            host=config.destination.host,
            port=config.http_port,
            database=config.destination.database,
            username=config.ch_user,
            password=config.ch_password,
            connect_timeout=deadline.remaining(3),
            send_receive_timeout=deadline.remaining(),
            query_retries=0,
            pool_mgr=pool,
            show_clickhouse_errors=False,
            autogenerate_session_id=False,
        )
        stack.callback(raw.close)
        derived = upgrade.migration_file("cdc_002_usage_eval_fields.sql")
        declarations, _ = core._definitions(config.destination.database)
        allowed = {
            core._tokens(statement)
            for statement in (
                core._LEDGER_DDL,
                *(declarations[name] for name in core.DEPENDENT),
                *apply_schema.split_statements(derived.path.read_text()),
            )
        }
        if phase == "native":
            allowed = {
                core._tokens(statement)
                for statement in native.native_definitions(
                    config.destination.database
                ).values()
            }

        class Client:
            def query(self, statement, parameters=None, settings=None):
                if not statement.lstrip().startswith("SELECT "):
                    raise InstallError("inspection must use SELECT")
                return raw.query(
                    statement,
                    parameters=parameters,
                    settings={
                        **(settings or {}),
                        "readonly": 1,
                        "max_threads": 1,
                        "max_execution_time": max(1, math.ceil(deadline.remaining(5))),
                    },
                )

            def command(self, statement):
                if not apply:
                    raise InstallError("apply was not requested")
                if core._tokens(statement) not in allowed:
                    raise InstallError(
                        "only packaged DDL for the selected phase is permitted"
                    )
                deadline.remaining()
                return raw.command(statement)

            def insert(self, table, rows, column_names):
                if not apply:
                    raise InstallError("apply was not requested")
                if (
                    phase != "cdc"
                    or table != "schema_versions"
                    or column_names != ["filename", "sha256", "applied_by", "notes"]
                    or len(rows) != 1
                    or rows[0]
                    != [
                        derived.path.name,
                        derived.sha256,
                        "oss-cdc-bootstrap",
                        "schema-topology/v1/local",
                    ]
                ):
                    raise InstallError(
                        "only the verified derived migration receipt may be inserted"
                    )
                deadline.remaining()
                return raw.insert(table, rows, column_names=column_names)

        def pg_query(statement, parameters):
            if not statement.lstrip().startswith("SELECT "):
                raise InstallError("source inspection must use SELECT")
            deadline.remaining()
            return pg.execute(statement, parameters).fetchall()

        arguments = {
            "database": config.destination.database,
            "inspect_mirrors": lambda: core.MirrorInventory.from_peerdb(
                request,
                source=config.source,
                destination=config.destination,
            ),
            "inspect_source": lambda: inspect_source(
                pg_query, source=config.source, tables=tuple(core.LANDING)
            ),
        }
        client = Client()
        if phase == "native":
            missing = native.inspect_native(
                client, database=config.destination.database
            )
            if not apply:
                return {
                    "ready": not missing,
                    "missing_objects": list(missing),
                    "applied": False,
                }
            created = native.bootstrap_native(
                client, database=config.destination.database
            )
            return {"ready": True, "created_objects": list(created), "applied": True}
        if wait_for_mirrors:
            # Only inspect until initial snapshots settle. Do not wrap apply in
            # a retry loop: a failed/uncertain write must remain terminal.
            while True:
                try:
                    arguments["inspect_mirrors"]()
                    break
                except InventoryPending:
                    time.sleep(deadline.remaining(2))
        before = core.inspect_bootstrap(client, **arguments)
        if not apply:
            return {
                "ready": not before.missing and before.upgrade_recorded,
                "missing_objects": list(before.missing),
                "derived_upgrade_required": not before.upgrade_recorded,
                "applied": False,
            }
        result = core.bootstrap_cdc(client, **arguments, applied_by="oss-cdc-bootstrap")
        return {
            "ready": True,
            "applied": True,
            "created_objects": list(result.created),
            "derived_upgrade_applied": result.upgrade_applied,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply only missing derived catalog schema after strict preflight",
    )
    parser.add_argument(
        "--timeout", type=int, default=300, help="Whole-job deadline in seconds"
    )
    parser.add_argument("--phase", choices=("cdc", "native"), default="cdc")
    parser.add_argument(
        "--wait-for-mirrors",
        action="store_true",
        help="Poll initial setup/snapshot states read-only before CDC preflight",
    )
    parser.add_argument("--source-peer", default="pg_source")
    parser.add_argument("--destination-peer", default="ch_dest")
    parser.add_argument("--peerdb-url", default=None)
    args = parser.parse_args(argv)
    try:
        config = Config.from_env(
            os.environ,
            source_peer=args.source_peer,
            destination_peer=args.destination_peer,
            peerdb_url=args.peerdb_url,
        )
        if args.timeout <= 0:
            raise InstallError("timeout must be positive")
        with _wall_clock_limit(args.timeout):
            result = run(
                config,
                apply=args.apply,
                timeout=args.timeout,
                phase=args.phase,
                wait_for_mirrors=args.wait_for_mirrors,
            )
        print(json.dumps(result))
        return 0 if result["ready"] else 1
    except InstallError as error:
        print(str(error), file=sys.stderr)
    except Exception as error:
        # Driver responses/default expressions can contain credentials or data.
        print(
            f"CDC setup failed ({type(error).__name__}); no automatic retry or cleanup. Check source/mirror/native prerequisites.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
