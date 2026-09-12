"""Engine proof for the span latest-state collapse, on a real ClickHouse 25.3.

Every span statement whose ``FROM`` source is the latest-state collapse is
rendered from the real builder, parameter-bound and **executed**. A statement
the analyzer rejects fails this module; it does not skip it. That distinction
is the whole point: the offline shape assertions in
``test_span_latest_state_argmax`` are string assertions and cannot see a
``Code: 47 UNKNOWN_IDENTIFIER``, which is exactly how a collapse that unpacked
its winner tuple in the same ``SELECT`` as the aggregate — an alias cycle the
25.3 analyzer refuses — passed review once.

The same statements are then executed a second time with the three latest-state
sources replaced by the ``SELECT * FROM spans FINAL`` shape they replaced, and
the two arms must return the same rows over a synthetic multi-version
population (three versions of one key, tombstones, a revival, an in-hour
timestamp correction, one that crosses the hour, and an equal-``_version`` tie).

Two engines, tried in this order:

``live``
    An isolated ClickHouse the caller opts into with ``FI_LIVE_CH_TESTS=1``.
    This repo has no shared ``require_live_clickhouse()`` helper, so the opt-in
    is defined here; no workflow sets it yet, so this path does not gate in CI
    today. The opt-in is required rather than inferred because port 19000 on a
    developer box is a *production* port-forward: an unguarded default there
    once wrote test tables into production. The target must be loopback and its
    database must be a throwaway ``test_*`` one, which this module creates and
    drops.

``docker``
    A fresh ``clickhouse/clickhouse-server:25.3-alpine`` container started with
    ``--network none``, driven by ``docker exec clickhouse-client``. The image
    is never pulled — when it is not already present there is no engine.

When neither engine is available the module skips.

The deployed DDL is read from the schema files. The only deviation is
``DDL_DEVIATIONS``: a stock container has neither the ``tiered`` storage policy
nor the ``cold`` volume its TTL moves parts to. Every column, skip index,
projection, engine, partition expression and sorting key is verbatim.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)

pytestmark = pytest.mark.integration

DOCKER_IMAGE = "clickhouse/clickhouse-server:25.3-alpine"
DDL_DEVIATIONS = ("storage_policy = 'tiered'", "TTL ... TO VOLUME 'cold'")
SCHEMA_FILES = ("002_spans_v2.sql", "019_id_remap.sql")
SCHEMA_DIR = (
    Path(__file__).resolve().parents[1] / "services" / "clickhouse" / "v2" / "schema"
)

PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER_PROJECT = "22222222-2222-2222-2222-222222222222"
VERSION_ID = "33333333-3333-3333-3333-333333333333"
END_USER = "44444444-4444-4444-4444-444444444444"
START = datetime(2026, 8, 8, 12)
WINDOW_END = START + timedelta(days=7)
# The identity with no storage winner: FINAL keeps the last row in selection
# order and argMax the first one it meets, so the two arms may legitimately
# publish different rows for it. Both must publish exactly ONE.
TIED_ID = "tied"


class EngineError(RuntimeError):
    """The server rejected a statement."""


# ── the deployed DDL ─────────────────────────────────────────────────────────


def _strip_sql_comments(text):
    """Drop ``--`` comments, honouring quotes (a comment here contains a ';')."""

    lines = []
    for line in text.splitlines():
        cut, quoted, index = None, False, 0
        while index < len(line) - 1:
            if line[index] == "'":
                quoted = not quoted
            elif not quoted and line[index : index + 2] == "--":
                cut = index
                break
            index += 1
        lines.append(line if cut is None else line[:cut])
    return "\n".join(lines)


def schema_statements():
    statements = []
    for name in SCHEMA_FILES:
        body = _strip_sql_comments((SCHEMA_DIR / name).read_text())
        for raw in body.split(";"):
            statement = raw.strip()
            if not statement:
                continue
            if "CREATE TABLE" in statement:
                statement = re.sub(r"TTL[\s\S]*?(?=SETTINGS)", "", statement)
                statement = re.sub(r"storage_policy\s*=\s*'tiered',\s*", "", statement)
            statements.append(statement.strip())
    return statements


# ── the synthetic multi-version population ──────────────────────────────────


def population():
    base = {
        "project_id": PROJECT,
        "observation_type": "span",
        "service_name": "service-a",
        "trace_id": "trace",
        "start_time": START + timedelta(minutes=20),
        "is_deleted": 0,
        "account_id": "acct-1",
        "model": "model-a",
    }
    return [
        # three versions of one key: only the newest value is public
        {**base, "id": "three", "_version": 1},
        {**base, "id": "three", "_version": 2, "account_id": "acct-2"},
        {**base, "id": "three", "_version": 3, "account_id": "acct-1"},
        # the newest version is a tombstone
        {**base, "id": "gone", "_version": 1},
        {**base, "id": "gone", "_version": 2, "is_deleted": 1},
        # an older tombstone does not remove a revived identity
        {**base, "id": "revived", "_version": 1, "is_deleted": 1},
        {**base, "id": "revived", "_version": 2},
        # a producer-time correction inside the hour keeps one identity
        {**base, "id": "corrected", "_version": 1},
        {
            **base,
            "id": "corrected",
            "_version": 2,
            "start_time": START + timedelta(minutes=5),
        },
        # a correction that crosses the hour is a second stored row
        {**base, "id": "crossed", "_version": 1},
        {
            **base,
            "id": "crossed",
            "_version": 2,
            "start_time": START + timedelta(minutes=95),
        },
        # equal versions: no storage winner exists at all
        {**base, "id": TIED_ID, "_version": 7},
        {**base, "id": TIED_ID, "_version": 7, "account_id": "acct-2"},
        # the same textual id in another service is a different physical span
        {**base, "id": "three", "service_name": "service-b", "_version": 5},
        # another project is never in scope
        {**base, "id": "three", "project_id": OTHER_PROJECT, "_version": 9},
    ]


def insert_statements():
    columns = (
        "project_id, observation_type, service_name, start_time, trace_id, id, "
        "name, model, attrs_string, attrs_number, attrs_bool, "
        "project_version_id, end_user_id, is_deleted, _version"
    )
    statements = []
    for row in population():
        values = (
            f"'{row['project_id']}', '{row['observation_type']}', "
            f"'{row['service_name']}', "
            f"toDateTime64('{row['start_time'].isoformat(sep=' ')}', 6, 'UTC'), "
            f"'{row['trace_id']}', '{row['id']}', 'n', '{row['model']}', "
            f"{{'account_id': '{row['account_id']}'}}, "
            "{'account_id': 1}, {'account_id': 1}, "
            f"toUUID('{VERSION_ID}'), toUUID('{END_USER}'), "
            f"{row['is_deleted']}, {row['_version']}"
        )
        # One INSERT per row: a single multi-row INSERT would let the engine
        # collapse the equal-version pair before it is ever stored.
        statements.append(f"INSERT INTO spans ({columns}) VALUES ({values})")
    return statements


# ── engines ─────────────────────────────────────────────────────────────────


class DockerEngine:
    """A throwaway 25.3 server with no network at all."""

    kind = "docker"

    def __init__(self):
        self.container = f"fi-span-engine-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def available():
        try:
            probe = subprocess.run(
                ["docker", "image", "inspect", DOCKER_IMAGE],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        # Never pull: a pull is network access, and Docker Hub is denying it.
        return probe.returncode == 0

    def start(self):
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--network",
                "none",
                "--name",
                self.container,
                DOCKER_IMAGE,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        for _ in range(120):
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    self.container,
                    "clickhouse-client",
                    "--query",
                    "SELECT 1",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if probe.returncode == 0:
                return
        raise EngineError(f"{DOCKER_IMAGE} did not become ready")

    def execute(self, sql):
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                self.container,
                "clickhouse-client",
                "--multiquery",
            ],
            input=sql,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0 or "Code: " in (result.stderr or ""):
            raise EngineError((result.stderr or result.stdout or "").strip())
        return result.stdout

    def stop(self):
        subprocess.run(
            ["docker", "rm", "-f", self.container], capture_output=True, timeout=180
        )


class LiveEngine:
    """A caller-nominated isolated ClickHouse, in a throwaway test_* database."""

    kind = "live"

    def __init__(self):
        self.database = f"test_span_engine_{uuid.uuid4().hex[:8]}"
        self.host = os.environ.get("CH25_HOST") or "127.0.0.1"
        self.port = int(
            os.environ.get("CH25_NATIVE_PORT")
            or os.environ.get("CH25_TCP_PORT")
            or 19000
        )
        self._client = None
        self._admin = None

    @staticmethod
    def available():
        if os.environ.get("FI_LIVE_CH_TESTS", "").strip().lower() not in (
            "1",
            "true",
            "yes",
        ):
            return False
        host = os.environ.get("CH25_HOST") or "127.0.0.1"
        # Loopback only. A remote target is never nominated implicitly.
        return host in ("127.0.0.1", "localhost", "::1")

    def start(self):
        from clickhouse_driver import Client

        settings = {"optimize_on_insert": 0}
        user = os.environ.get("CH25_USER") or "default"
        password = os.environ.get("CH25_PASSWORD") or ""
        self._admin = Client(
            host=self.host,
            port=self.port,
            user=user,
            password=password,
            connect_timeout=5,
            settings=settings,
        )
        self._admin.execute("SELECT 1")
        self._admin.execute(f"CREATE DATABASE {self.database}")
        self._client = Client(
            host=self.host,
            port=self.port,
            user=user,
            password=password,
            database=self.database,
            connect_timeout=5,
            settings=settings,
        )

    def execute(self, sql):
        from clickhouse_driver.errors import Error as DriverError

        try:
            rows = self._client.execute(sql)
        except DriverError as exc:
            raise EngineError(str(exc)) from exc
        return "\n".join("\t".join(str(value) for value in row) for row in rows)

    def stop(self):
        if self._admin is not None:
            self._admin.execute(f"DROP DATABASE IF EXISTS {self.database}")


@pytest.fixture(scope="module")
def engine():
    for candidate in (LiveEngine, DockerEngine):
        if not candidate.available():
            continue
        instance = candidate()
        try:
            instance.start()
        except Exception as exc:  # the next candidate, or no engine at all
            instance.stop()
            pytest.skip(f"{candidate.kind} ClickHouse unavailable: {exc!r}")
        try:
            for statement in schema_statements():
                instance.execute(statement)
            instance.execute("SYSTEM STOP MERGES spans")
            for statement in insert_statements():
                instance.execute(statement)
            yield instance
        finally:
            instance.stop()
        return
    pytest.skip(
        "no ClickHouse engine: set FI_LIVE_CH_TESTS=1 for a loopback CH 25.3, "
        f"or make the {DOCKER_IMAGE} image available locally"
    )


# ── the statements ──────────────────────────────────────────────────────────


def time_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), WINDOW_END.isoformat()],
        },
    }


def attr_filter():
    return {
        "column_id": "account_id",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["acct-1", "acct-2"],
            "attribute_value_types": ["string", "string"],
        },
    }


def column_filter():
    return {
        "column_id": "model",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "model-a",
        },
    }


def builder(filters=None, **kwargs):
    return SpanListQueryBuilderV2(
        **{
            "project_id": PROJECT,
            "filters": [time_filter(), attr_filter()] if filters is None else filters,
            "bounded_internal_scan": True,
            **kwargs,
        }
    )


def seed_rows():
    return [
        {
            "project_id": PROJECT,
            "trace_id": "trace",
            "id": "three",
            "observation_type": "span",
            "service_name": "service-a",
            "start_time": START + timedelta(minutes=20),
            "_version": 3,
        }
    ]


SLICE = {
    "slice_start": START,
    "slice_end": START + timedelta(hours=1),
    "limit": 64,
}
KEYSET = ("three", "trace", PROJECT, "span", "service-a")


def statements():
    """Every span statement whose FROM source is a latest-state collapse.

    Names match the adversarial verification that found the analyzer rejection,
    so its 18 rejected shapes are all here; ``normal_list_eu`` is the
    nineteenth, which that harness could not reach for want of the id-remap
    table this module creates.
    """

    plain = builder([time_filter()])
    return {
        "seed_text_in": builder().build_filter_seed_page(**SLICE),
        "seed_newer": builder().build_filter_seed_page(direction="newer", **SLICE),
        "seed_keyset": builder().build_filter_seed_page(
            before_start_time=START + timedelta(minutes=30),
            before_id=KEYSET,
            **SLICE,
        ),
        "seed_native_col": builder(
            [time_filter(), column_filter()]
        ).build_filter_seed_page(**SLICE),
        "seed_org": SpanListQueryBuilderV2(
            project_ids=[PROJECT],
            filters=[time_filter(), attr_filter()],
            bounded_internal_scan=True,
        ).build_filter_seed_page(**SLICE),
        "seed_pv": builder(project_version_id=VERSION_ID).build_filter_seed_page(
            **SLICE
        ),
        "nav_seed": builder().build_filter_navigation_seed_page(
            direction="older", **SLICE
        ),
        "nav_target": builder().build_filter_navigation_target_query(target_id="three"),
        "anchor_probe": builder().build_filter_anchor_probe(
            slice_start=START, slice_end=WINDOW_END, limit=64
        ),
        "anchor_probe_bounded": builder(
            bounded_anchor_probe=True
        ).build_filter_anchor_probe(slice_start=START, slice_end=WINDOW_END, limit=64),
        "graph_key_witness": builder().build_filter_graph_key_witness_probe(
            slice_start=START, slice_end=WINDOW_END, limit=64
        ),
        "match_from_seed": builder().build_filter_match_query_from_seed_rows(
            seed_rows()
        ),
        "match_identity_only": builder(
            bounded_identity_only=True
        ).build_filter_match_query_from_seed_rows(seed_rows()),
        "match_query": builder().build_filter_match_query(["three"]),
        "match_full_state": builder().build_filter_match_query(
            ["three"], candidate_full_state=True
        ),
        "content": builder().build_content_query(
            ["three"],
            span_identities=[
                (
                    PROJECT,
                    "trace",
                    "three",
                    START + timedelta(minutes=20),
                    "span",
                    "service-a",
                )
            ],
        ),
        "content_v1style": builder().build_content_query(["three"]),
        "normal_list": SpanListQueryBuilderV2(
            project_id=PROJECT,
            filters=[time_filter()],
            page=1,
            page_size=25,
        ).build(),
        "normal_list_eu": SpanListQueryBuilderV2(
            project_id=PROJECT,
            filters=[time_filter()],
            page=1,
            page_size=25,
            end_user_id=END_USER,
        ).build(),
        "unfiltered_anchor": plain.build_filter_anchor_probe(
            slice_start=START, slice_end=WINDOW_END, limit=64
        ),
    }


# ── the FINAL twin the collapse replaced ────────────────────────────────────


def _twin_window_source(self, prefix, *, raw_key_predicate="", consumer_sql=""):
    population_scope = ""
    if raw_key_predicate:
        population_scope = f"""
              AND (project_id, observation_type, service_name, toStartOfHour(start_time)) IN (
                  SELECT DISTINCT project_id, observation_type, service_name, toStartOfHour(start_time)
                  FROM {self.TABLE}
                  PREWHERE {self.project_filter_sql()}
                    AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_start_us)s))
                    AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_end_us)s - 1))
                  WHERE {raw_key_predicate}
              )
            """
    return f"""(
            SELECT * FROM {self.TABLE} FINAL
            PREWHERE {self.project_filter_sql()}
              AND toStartOfHour(start_time) >= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_start_us)s))
              AND toStartOfHour(start_time) <= toStartOfHour(fromUnixTimestamp64Micro(%({prefix}_end_us)s - 1))
              {population_scope}
        ) AS latest_seed_spans"""


def _twin_normal_source(self, *, consumer_sql=""):
    return f"""(
            SELECT * FROM {self.TABLE} FINAL
            PREWHERE {self.project_filter_sql()}
              AND toStartOfHour(start_time) >= toStartOfHour(toDateTime64(%(start_date)s, 6, 'UTC'))
              AND toStartOfHour(start_time) <= toStartOfHour(toDateTime64(%(end_date)s, 6, 'UTC'))
        ) AS latest_list_spans"""


def _twin_match_source(self, candidate_scope, *, consumer_sql=""):
    return f"""(
            SELECT * FROM {self.TABLE} FINAL
            PREWHERE {self.project_filter_sql()}
              AND id IN %(candidate_span_ids)s
              {candidate_scope}
        ) AS latest_candidate_spans"""


def twin_statements():
    """The same statements, resolving latest state the way they used to."""

    with (
        mock.patch.object(
            SpanListQueryBuilderV2, "_latest_window_source_sql", _twin_window_source
        ),
        mock.patch.object(
            SpanListQueryBuilderV2, "_normal_span_source_sql", _twin_normal_source
        ),
        mock.patch.object(
            SpanListQueryBuilderV2, "_filter_match_source_sql", _twin_match_source
        ),
    ):
        return statements()


# ── parameter binding ───────────────────────────────────────────────────────


def _literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple, set)):
        return "(" + ", ".join(_literal(item) for item in value) + ")"
    if isinstance(value, datetime):
        return f"'{value.isoformat(sep=' ')}'"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


# ``build_count_query``-style statements read the window from params the caller
# injects at dispatch, so supply them here rather than binding NULL.
EXTRA_PARAMS = {"start_date": START, "end_date": WINDOW_END}


def bind(sql, params):
    resolved = {**EXTRA_PARAMS, **(params or {})}
    missing = []

    def replace(match):
        name = match.group(1)
        if name not in resolved:
            missing.append(name)
            return "NULL"
        return _literal(resolved[name])

    bound = re.sub(r"%\((\w+)\)s", replace, sql)
    assert not missing, f"unbound parameters: {sorted(set(missing))}"
    return bound.rstrip().rstrip(";")


STATEMENT_NAMES = sorted(statements())


@pytest.mark.parametrize("name", STATEMENT_NAMES)
def test_every_latest_state_statement_parses_and_runs(engine, name):
    """A statement the analyzer rejects is a failure here, never a skip."""

    sql, params = statements()[name]
    assert "spans FINAL" not in sql, name
    try:
        engine.execute(bind(sql, params))
    except EngineError as exc:
        pytest.fail(f"{engine.kind} engine rejected {name}:\n{exc}")


def _rows(text):
    return sorted(line for line in text.splitlines() if line.strip())


@pytest.mark.parametrize("name", STATEMENT_NAMES)
def test_collapse_returns_the_same_rows_as_the_final_twin(engine, name):
    """Latest state by collapse == latest state by the engine's own merge."""

    head_sql, head_params = statements()[name]
    twin_sql, twin_params = twin_statements()[name]
    assert "spans FINAL" in twin_sql, name

    head = _rows(engine.execute(bind(head_sql, head_params)))
    twin = _rows(engine.execute(bind(twin_sql, twin_params)))
    # Two empty arms agree about nothing. Every statement here must reach the
    # population, or this comparison is not an oracle.
    assert head, f"{name} returned no rows; the twin comparison would be vacuous"

    # The equal-version identity has no storage winner: FINAL keeps the last
    # row in selection order, argMax the first one it meets. Both arms must
    # still publish exactly one whole row for it — never a mixture, never two.
    head_tied = [row for row in head if TIED_ID in row]
    twin_tied = [row for row in twin if TIED_ID in row]
    assert len(head_tied) == len(twin_tied) <= 1, (name, head_tied, twin_tied)
    assert [row for row in head if TIED_ID not in row] == [
        row for row in twin if TIED_ID not in row
    ], name
