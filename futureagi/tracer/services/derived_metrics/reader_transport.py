"""Exclusive native/HTTP adapters used only by reader_lifetime's subprocesses.

No shared pools, retries, sessions, distributed SQL, or native policy mutation.
Transport bounds are complemented by the supervisor's hard wall deadline; a
socket timeout or closed HTTP response is never returned as a drain receipt.
"""

import json
import time
from dataclasses import dataclass
from ipaddress import ip_address
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import UUID

from tracer.services.derived_metrics.reader_lifetime import (
    IncapableReadLane,
    ReadBudget,
    ReadExecutionFailed,
    ReadNotSubmitted,
    ReadReceipt,
)
from tracer.services.private_boundary_log import PrivateBoundaryLogger

private_logger = PrivateBoundaryLogger(__name__)

# A deliberately small compiler subset, not a SQL security boundary or a public
# SQL API. Unknown functions/sources stay native until separately qualified.
_LOCAL_FUNCTIONS = frozenset(
    {
        "argmax",
        "argmin",
        "array",
        "avg",
        "cast",
        "coalesce",
        "count",
        "if",
        "max",
        "min",
        "sum",
        "tuple",
        "touuid",
        "tostring",
        "tofloat64",
        "todatetime64",
        "numbers",
        "sleepeachrow",
        "sleep",
        "nullif",
    }
)


@dataclass(frozen=True, repr=False)
class MetricsReadTransport:
    host: str
    port: int
    database: str
    server_uuid: str
    protocol: str
    user: str
    password: str
    local_tables: tuple[str, ...]
    secure: bool = False

    def __post_init__(self) -> None:
        # Load balancers/DNS failover are not a selected-shard cancellation lane.
        ip_address(self.host)
        UUID(self.server_uuid)
        if self.protocol not in {"native", "http"}:
            raise IncapableReadLane("unsupported metrics read protocol")
        if type(self.port) is not int or not 0 < self.port < 65536:
            raise ValueError("invalid metrics transport port")

    def _query(
        self,
        sql: str,
        params: dict,
        receipt: ReadReceipt | None,
        budget: ReadBudget,
        *,
        control: bool = False,
    ) -> list:
        settings = {
            "max_execution_time": budget.cancel_s if control else budget.execute_s,
            "timeout_overflow_mode": "throw",
            "max_memory_usage": budget.memory_bytes,
            "max_result_bytes": budget.result_bytes,
            "result_overflow_mode": "throw",
            "max_threads": budget.threads,
            # See queries.QUERY_SETTINGS: one index-analysis thread.
            "max_threads_for_indexes": 1,
            "max_parallel_replicas": 1,
            "readonly": 0 if control else 2,
            "use_query_cache": 0,
            "log_queries": 1,
            "log_queries_probability": 1,
            # Recovery requires a terminal record even for short queries and
            # errors. Probe with these same settings before data submission so a
            # locked, incompatible profile is refused without an uncertain send.
            "log_queries_min_query_duration_ms": 0,
            "log_queries_min_type": "QUERY_START",
        }
        if self.protocol == "native":
            from clickhouse_driver import Client

            client = Client(
                self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                secure=self.secure,
                connect_timeout=budget.transport_s,
                send_receive_timeout=budget.transport_s,
                sync_request_timeout=budget.transport_s,
                disable_reconnect=True,
            )
            try:
                identity = client.execute(
                    "SELECT toString(serverUUID()), currentDatabase()",
                    settings=settings,
                )
                if not identity or tuple(identity[0]) != (
                    self.server_uuid,
                    self.database,
                ):
                    raise IncapableReadLane("selected metrics shard identity changed")
                rows: list = client.execute(
                    sql,
                    params,
                    settings=settings,
                    query_id=receipt.query_id if receipt else None,
                )
                return rows
            finally:
                client.disconnect()
        import urllib3
        from clickhouse_driver.util.escape import escape_params

        # Use the same established literal binder as the native adapter. Values
        # cannot become HTTP settings or header fields. This is not string-format
        # interpolation of unescaped request input.
        if params:
            server_timezone = self._query("SELECT timezone()", {}, None, budget)[0][0]
            context = SimpleNamespace(
                server_info=SimpleNamespace(get_timezone=lambda: server_timezone)
            )
            bound = sql % escape_params(params, context)
        else:
            bound = sql
        query = {
            "database": self.database,
            "default_format": "JSONCompact",
            # This envelope is consumed by Python, not a JavaScript number
            # parser. Keep Int64/UInt64 exact without converting numeric-looking
            # String values or Bool values to integers after decoding.
            "output_format_json_quote_64bit_integers": 0,
            **settings,
        }
        if receipt:
            query["query_id"] = receipt.query_id
        host = f"[{self.host}]" if ":" in self.host else self.host
        url = f"{'https' if self.secure else 'http'}://{host}:{self.port}/?{urlencode(query)}"
        headers = urllib3.make_headers(basic_auth=f"{self.user}:{self.password}")
        pool = urllib3.PoolManager(
            retries=False,
            timeout=urllib3.Timeout(
                connect=budget.transport_s, read=budget.transport_s
            ),
        )
        response = None
        try:
            response = pool.request(
                "POST",
                url,
                body=bound.encode(),
                headers=headers,
                preload_content=False,
                redirect=False,
                retries=False,
            )
            # The HTTP envelope is also bounded; result_overflow_mode guards the
            # server's internal result representation, not encoded JSON size.
            payload = response.read(budget.result_bytes + 1)
            if response.status != 200 or len(payload) > budget.result_bytes:
                raise ReadExecutionFailed(
                    "HTTP result failed or exceeded its byte budget"
                )
            if control and not payload:
                # ClickHouse's HTTP KILL with no matching rows has no envelope.
                # This is NOT terminal evidence; drain still needs query_log.
                return []
            data: list = json.loads(payload)["data"]
            return data
        finally:
            if response is not None:
                response.close()
            pool.clear()

    def _check_identity(self, budget: ReadBudget) -> None:
        rows = self._query(
            "SELECT toString(serverUUID()), currentDatabase()", {}, None, budget
        )
        if not rows or tuple(rows[0]) != (self.server_uuid, self.database):
            raise IncapableReadLane("selected metrics shard identity changed")

    def _check_local_sql(self, sql: str, budget: ReadBudget) -> None:
        # Native pyformat parameters are represented as literals for structural
        # validation only. The actual execution uses the driver's safe binder.
        import re

        import sqlglot
        from sqlglot import exp

        try:
            parsed = sqlglot.parse(
                re.sub(r"%\([a-zA-Z_][a-zA-Z_0-9]*\)s", "NULL", sql), read="clickhouse"
            )
        except sqlglot.errors.ParseError:
            raise IncapableReadLane("unsupported metrics SELECT syntax") from None
        if len(parsed) != 1 or not isinstance(parsed[0], exp.Select | exp.Union):
            raise IncapableReadLane("bounded lane accepts local SELECTs only")
        tree = parsed[0]
        for node in tree.walk():
            if isinstance(node, exp.Command | exp.Into):
                raise IncapableReadLane("unsupported metrics statement")
            # Clauses on UNION branches/subqueries can override transport-owned
            # limits and logging too. Formats also change the HTTP result shape.
            if node.args.get("settings") or node.args.get("format"):
                raise IncapableReadLane("metrics settings and format are reader-owned")
        for function in tree.find_all(exp.Func):
            # SQLGlot models boolean connectors as Func nodes too. These are
            # structural predicates in the compiler's tenant/generation filter,
            # not callable functions. Their children are still visited below:
            # AND/OR cannot hide an unqualified function or nonlocal source.
            if isinstance(function, (exp.And, exp.Or)):
                continue
            name = (
                function.name
                if isinstance(function, exp.Anonymous)
                else function.sql_name()
            )
            if name.lower().replace("_", "") not in _LOCAL_FUNCTIONS:
                raise IncapableReadLane("unqualified metrics function")
        sources = set()
        for table in tree.find_all(exp.Table):
            if isinstance(table.this, exp.Func):
                name = (
                    table.this.name
                    if isinstance(table.this, exp.Anonymous)
                    else table.this.sql_name()
                )
                if name.lower() != "numbers":
                    raise IncapableReadLane("nonlocal metrics table function")
                continue
            if (
                table.catalog
                or table.db not in {"", self.database}
                or table.name not in self.local_tables
            ):
                raise IncapableReadLane("unqualified metrics table")
            sources.add(table.name)
        if sources:
            rows = self._query(
                "SELECT name, engine FROM system.tables WHERE database = currentDatabase() AND name IN %(tables)s",
                {"tables": tuple(sorted(sources))},
                None,
                budget,
            )
            if {row[0] for row in rows} != sources or any(
                not row[1].endswith("MergeTree") for row in rows
            ):
                raise IncapableReadLane("metrics source is not a local MergeTree")

    def execute(
        self, sql: str, params: dict, receipt: ReadReceipt, budget: ReadBudget
    ) -> list:
        try:
            self._check_identity(budget)
            self._check_local_sql(sql, budget)
        except Exception as exc:
            private_logger.exception("metric_read_preflight_refused", exc)
            # These probes only read identity/catalog metadata. No tracked data
            # query has been submitted; a completed child can attest that fact.
            raise ReadNotSubmitted("metrics lane refused before submission") from None
        return self._query(sql, params, receipt, budget)

    def drain(self, receipt: ReadReceipt, budget: ReadBudget) -> bool:
        """KILL SYNC + no live family; an empty KILL needs a terminal query log.

        We never retry the data query. Empty process lists alone cannot exclude
        a delayed HTTP/native submission. Positive terminal evidence for this
        unique ID is required. Failures leave the prepared guard for recovery.
        """
        self._check_identity(budget)
        deadline = time.monotonic() + budget.cancel_s
        params = {"id": receipt.query_id}
        rows = self._query(
            "KILL QUERY WHERE query_id = %(id)s OR initial_query_id = %(id)s SYNC",
            params,
            None,
            budget,
            control=True,
        )
        terminal = any(
            row[0] == "finished" and row[1] == receipt.query_id for row in rows
        )
        while time.monotonic() < deadline:
            if not terminal:
                evidence = self._query(
                    "SELECT count() FROM system.query_log WHERE query_id = %(id)s "
                    "AND type IN ('QueryFinish', 'ExceptionWhileProcessing', 'ExceptionBeforeStart')",
                    params,
                    None,
                    budget,
                )
                terminal = bool(int(evidence[0][0]))
            if terminal:
                live = self._query(
                    "SELECT count() FROM system.processes WHERE query_id = %(id)s OR initial_query_id = %(id)s",
                    params,
                    None,
                    budget,
                )
                if int(live[0][0]) == 0:
                    return True
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        return False
