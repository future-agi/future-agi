"""TH-4804 task 784: Usage read cost on a large synthetic workspace.

Seeds (once) a traces table (a few busy projects plus many small ones) and a
usage_apicalllog-shaped table (production MATERIALIZED eval_trace_id) on the
local test ClickHouse, runs model_hub.selectors.eval_usage.read_eval_usage
against them with every readable project, and records per statement:
read_rows / read_bytes / duration from system.query_log and the traces
primary-key granules from EXPLAIN indexes = 1.

Usage (from futureagi/, test settings env):
  BENCH_OUT=out.json .venv/bin/python ../proof/784/bench/usage_scan_bench.py
Synthetic data only; local ClickHouse only.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import django

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.test")
django.setup()

from clickhouse_driver import Client  # noqa: E402

from model_hub.selectors import eval_usage  # noqa: E402
from tracer.services.clickhouse import trace_project_scope  # noqa: E402

HOST = os.environ.get("CH25_HOST", "127.0.0.1")
PORT = int(os.environ.get("CH25_TCP_PORT", "9000"))
DB = "default"
# A few busy projects hold most traces, as in a large tenant; the rest are small.
BIG_PROJECTS = int(os.environ.get("BENCH_BIG_PROJECTS", "2"))
BIG_TRACES = int(os.environ.get("BENCH_BIG_TRACES", "20000000"))
SMALL_PROJECTS = int(os.environ.get("BENCH_SMALL_PROJECTS", "248"))
SMALL_TRACES = int(os.environ.get("BENCH_SMALL_TRACES", "8000"))
PROJECTS = BIG_PROJECTS + SMALL_PROJECTS
BIG_TOTAL = BIG_PROJECTS * BIG_TRACES
TOTAL_TRACES = BIG_TOTAL + SMALL_PROJECTS * SMALL_TRACES
TRACE_TABLE = "bench784_traces"
USAGE_TABLE = "bench784_usage"
ORG = "11111111-1111-1111-1111-111111111111"
WS = "22222222-2222-2222-2222-222222222222"
TEMPLATE = "33333333-3333-3333-3333-333333333333"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)

admin = Client(host=HOST, port=PORT, database=DB)


def project_id(i: int) -> str:
    return str(uuid.UUID(int=(0xA << 124) + i))


def project_of(n: int) -> int:
    if n < BIG_TOTAL:
        return n // BIG_TRACES
    return BIG_PROJECTS + (n - BIG_TOTAL) // SMALL_TRACES


def trace_id_sql(n: str) -> str:
    return f"reinterpretAsUUID(sipHash128('t', toUInt64({n})))"


def seed() -> None:
    have = admin.execute(f"EXISTS TABLE {TRACE_TABLE}")[0][0]
    total = TOTAL_TRACES
    if have and admin.execute(f"SELECT count() FROM {TRACE_TABLE}")[0][0] == total:
        return
    admin.execute(f"DROP TABLE IF EXISTS {TRACE_TABLE}")
    admin.execute(f"DROP TABLE IF EXISTS {USAGE_TABLE}")
    admin.execute(
        f"""CREATE TABLE {TRACE_TABLE} (
            id UUID, project_id UUID, is_deleted UInt8, _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        ORDER BY (project_id, id)"""
    )
    project_sql = (
        f"if(number < {BIG_TOTAL}, toUInt64(intDiv(number, {BIG_TRACES})), "
        f"toUInt64({BIG_PROJECTS} + intDiv(toUInt64(number - {BIG_TOTAL}), {SMALL_TRACES})))"
    )
    admin.execute(
        f"""INSERT INTO {TRACE_TABLE}
        SELECT {trace_id_sql("number")},
               toUUID(concat('a0000000-0000-0000-0000-', leftPad(hex(toUInt64({project_sql})), 12, '0'))),
               0, 1
        FROM numbers({total})""",
        settings={"max_insert_threads": 4, "max_threads": 8},
    )
    admin.execute(f"OPTIMIZE TABLE {TRACE_TABLE} FINAL")
    admin.execute(
        f"""CREATE TABLE {USAGE_TABLE} (
            id Int64, log_id UUID, organization_id UUID, workspace_id Nullable(UUID),
            source_id String, status String, config String,
            eval_trace_id String MATERIALIZED JSONExtractString(JSONExtractString(config), 'trace_id'),
            deleted UInt8, created_at DateTime64(6, 'UTC'),
            _peerdb_is_deleted UInt8, _peerdb_version Int64
        ) ENGINE = MergeTree ORDER BY id"""
    )
    rows = []
    for i in range(600):
        kind = (
            i % 3
        )  # 0 double-encoded trace log, 1 object-encoded trace log, 2 no trace
        cfg = {
            "source": "tracer",
            "output": {"output": "Passed"},
            "duration": 1.5,
            "padding": "x" * 400,
        }
        if kind != 2:
            # Mostly the busy projects' traces, some in small projects.
            n = (i * 7_919_993) % (BIG_TOTAL if i % 5 else TOTAL_TRACES)
            cfg["trace_id"] = admin.execute(f"SELECT toString({trace_id_sql(str(n))})")[
                0
            ][0]
            if i % 2:
                cfg["project_id"] = project_id(project_of(n))
        encoded = json.dumps(cfg)
        config = json.dumps(encoded) if kind == 0 else encoded
        rows.append(
            (
                i + 1,
                uuid.uuid4(),
                uuid.UUID(ORG),
                uuid.UUID(WS),
                TEMPLATE,
                "success",
                config,
                0,
                NOW - timedelta(hours=i % 600),
                0,
                1,
            )
        )
    # Other templates' logs in the same workspace (the seek statements skip them).
    for i in range(600, 200_600, 1000):
        batch = [
            (
                j + 1,
                uuid.uuid4(),
                uuid.UUID(ORG),
                uuid.UUID(WS),
                f"other-{j % 50}",
                "success",
                json.dumps(json.dumps({"source": "tracer", "padding": "y" * 400})),
                0,
                NOW - timedelta(minutes=j % 40_000),
                0,
                1,
            )
            for j in range(i, i + 1000)
        ]
        rows.extend(batch)
    admin.execute(
        f"INSERT INTO {USAGE_TABLE} (id, log_id, organization_id, workspace_id, source_id, status,"
        " config, deleted, created_at, _peerdb_is_deleted, _peerdb_version) VALUES",
        rows,
    )


class RecordingClient:
    def __init__(self, tag: str):
        self.tag = tag
        self.calls: list[dict] = []

    def execute_read(self, query, params=None, timeout_ms=None, settings=None):
        op = f"{self.tag}-{len(self.calls)}"
        client = Client(host=HOST, port=PORT, database=DB)
        try:
            explain = client.execute("EXPLAIN indexes = 1 " + query, params or {})
            rows = client.execute(query, params or {}, settings={"log_comment": op})
        finally:
            client.disconnect()
        self.calls.append(
            {
                "op": op,
                "query": query,
                "explain": "\n".join(r[0] for r in explain),
                "rows": len(rows),
            }
        )
        return rows, [], 0.0


def trace_granules(explain: str) -> list[str]:
    """Granules the traces table reads after its PrimaryKey index."""

    out, in_trace, in_pk = [], False, False
    for line in explain.splitlines():
        if "ReadFromMergeTree" in line:
            in_trace = TRACE_TABLE in line
            in_pk = False
        if in_trace and "PrimaryKey" in line:
            in_pk = True
        if in_trace and in_pk and "Granules:" in line:
            out.append(line.strip())
            in_pk = False
    return out


def main() -> None:
    seed()
    eval_usage._USAGE_TABLE = USAGE_TABLE
    trace_project_scope._TRACE_TABLE = TRACE_TABLE
    recorder = RecordingClient(
        f"{os.environ.get('BENCH_TAG', 'run')}-{uuid.uuid4().hex[:8]}"
    )
    eval_usage.get_clickhouse_client = lambda: recorder
    # BENCH_PAGE > 0 with a small selection bound drives the page seek
    # statements (split counts) that a deep page uses on a busy eval.
    if os.environ.get("BENCH_MAX_SELECTION"):
        eval_usage._MAX_PAGE_SELECTION_ROWS = int(os.environ["BENCH_MAX_SELECTION"])
    result = eval_usage.read_eval_usage(
        organization_id=ORG,
        workspace_id=WS,
        project_ids=[project_id(i) for i in range(PROJECTS)],
        template_id=TEMPLATE,
        start_date=NOW - timedelta(days=30),
        end_date=NOW,
        bucket_minutes=1440,
        page=int(os.environ.get("BENCH_PAGE", "0")),
        page_size=25,
    )
    admin.execute("SYSTEM FLUSH LOGS")
    stats = {
        r[0]: r[1:]
        for r in admin.execute(
            "SELECT log_comment, read_rows, read_bytes, query_duration_ms FROM system.query_log"
            " WHERE type = 'QueryFinish' AND log_comment IN %(ops)s",
            {"ops": tuple(c["op"] for c in recorder.calls)},
        )
    }
    statements = []
    for c in recorder.calls:
        q = c["query"]
        kind = (
            "total"
            if "total_runs" in q
            else "chart"
            if "AS bucket" in q
            else "page_rows"
            if "LIMIT %(page_selection_limit)s" in q
            else "page_count"
            if "page_window_count" in q
            else "page_split_count"
            if "older_count" in q
            else "other"
        )
        read_rows, read_bytes, ms = stats.get(c["op"], (None, None, None))
        statements.append(
            {
                "statement": kind,
                "read_rows": read_rows,
                "read_bytes": read_bytes,
                "duration_ms": ms,
                "result_rows": c["rows"],
                "traces_pk_granules": trace_granules(c["explain"]),
                "explain_indexes": c["explain"],
            }
        )
    out = {
        "projects": PROJECTS,
        "traces": TOTAL_TRACES,
        "big_projects": BIG_PROJECTS,
        "big_project_traces": BIG_TRACES,
        "usage_rows_for_template": 600,
        "other_usage_rows": 200_000,
        "runs_period": result.runs_period,
        "listed_on_page": len(result.logs),
        "statements": statements,
    }
    path = os.environ.get("BENCH_OUT", "bench.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    for s in statements:
        print(
            s["statement"],
            s["read_rows"],
            s["read_bytes"],
            s["duration_ms"],
            s["traces_pk_granules"],
        )
    print("runs_period", result.runs_period, "listed", len(result.logs))


if __name__ == "__main__":
    main()
