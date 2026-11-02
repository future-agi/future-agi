"""Independent old-entry-point loader and deterministic Sessions parity hashes."""

import ast
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from tracer.services.derived_metrics.base_list_current import CurrentFactsRead
from tracer.services.derived_metrics.publication import pinned_read

BASELINE = "9fc537515"
BASELINE_FIXTURE = (
    Path(__file__).parent / "fixtures/base_list_parity/baseline_builder_9fc537515.json"
)
BASELINE_PATHS = (
    "query_builders/session_list.py",
    "v2/query_builders/session_list.py",
)


def baseline_fixture():
    """Read the checked-in oracle; no Git executable or repository is required."""
    fixture = json.loads(BASELINE_FIXTURE.read_text())
    assert fixture["header"]["source_commit"].startswith(BASELINE)
    for path in BASELINE_PATHS:
        entry = fixture["builders"][path]
        assert hashlib.sha256(entry["source"].encode()).hexdigest() == entry["sha256"]
    return fixture


def old_session_builder():
    """Run the vendored old entry points with installed application dependencies.

    The fixture includes generated SQL and hash-verified source so randomized
    project IDs, windows and cursor parameters still use the historical compiler.
    Never fall back to the current builder when history or the fixture is absent.
    """
    return _load_old_session_builder(baseline_fixture()["builders"])


def _load_old_session_builder(builders):
    def source(path):
        return builders[path]["source"]

    old = {"__name__": "baseline_session_builder"}
    exec(
        compile(
            source("query_builders/session_list.py"), "baseline_session_builder", "exec"
        ),
        old,
    )
    v2 = ast.parse(source("v2/query_builders/session_list.py"))
    v2.body = [
        node
        for node in v2.body
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module == "tracer.services.clickhouse.query_builders.session_list"
        )
    ]
    namespace = {
        "__name__": "baseline_session_builder_v2",
        "SessionListQueryBuilder": old["SessionListQueryBuilder"],
    }
    exec(compile(v2, "baseline_session_builder_v2", "exec"), namespace)
    return namespace["SessionListQueryBuilderV2"]


def baseline_statements(builder_type):
    """Deterministic native SQL/parameter snapshots, independent of Git at runtime."""
    builder = builder_type(
        project_id=str(UUID(int=1)),
        page_size=25,
        filters=filters(
            datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
        ),
    )
    calls = {
        "build_candidate_page_query": (),
        "build_candidate_count_query": (),
        "build_candidate_cursor_page_query": (),
        "build_page_hydration_query": ([str(UUID(int=101))],),
    }
    return {
        name: {
            "sql": result[0],
            "params": json.loads(
                json.dumps(
                    result[1], default=lambda value: {"datetime": value.isoformat()}
                )
            ),
        }
        for name, args in calls.items()
        for result in [getattr(builder, name)(*args)]
    }


def regenerate_baseline():
    """Explicit maintenance command only; never invoked by the test suite."""
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    folder = "futureagi/tracer/services/clickhouse/"
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", BASELINE], text=True
    ).strip()
    builders = {}
    for path in BASELINE_PATHS:
        source = subprocess.check_output(
            ["git", "-C", str(repo), "show", f"{commit}:{folder}{path}"], text=True
        )
        builders[path] = {
            "source": source,
            "sha256": hashlib.sha256(source.encode()).hexdigest(),
        }
    fixture = {
        "header": {
            "source_commit": commit,
            "regeneration_command": (
                "cd futureagi && DJANGO_SETTINGS_MODULE=tfc.settings.test "
                "NO_STARTUP_DB_MUTATIONS=true FI_SKIP_CH25_SCHEMA_APPLY=1 python -c "
                "'import django; django.setup(); "
                "from tracer.tests._base_list_parity import regenerate_baseline; "
                "regenerate_baseline()'"
            ),
            "description": (
                "Native Sessions SQL/parameters generated from the pinned v1/v2 "
                "builders, with hash-verified source for arbitrary application "
                "inputs. Regeneration alone requires Git history; tests read only "
                "this fixture. Shared dependencies are the installed application."
            ),
        },
        "builders": builders,
        "statements": baseline_statements(_load_old_session_builder(builders)),
    }
    BASELINE_FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n")


@dataclass(frozen=True)
class FixtureAuthority:
    """Static fixture assumptions ONLY, not a production freshness contract."""

    manifest_id: str
    remap_revision: int
    complete_retention_coverage: bool = False
    fresh: bool = False
    unique_physical_versions: bool = False
    anchor_authoritative: bool = False

    def permits(self, manifest):
        return (
            str(manifest.id) == self.manifest_id
            and manifest.remap_revision == self.remap_revision
            and all(
                value is True
                for value in (
                    self.complete_retention_coverage,
                    self.fresh,
                    self.unique_physical_versions,
                    self.anchor_authoritative,
                )
            )
        )


class FixtureCurrentFactsRead(CurrentFactsRead):
    """Exercise inactive SQL with declared assumptions inside synthetic tests."""

    def __init__(self, manifest, org_id, authority):
        super().__init__(manifest, org_id)
        self.authority = authority

    def permits(self, project_id):
        return (
            self.active
            and str(self.manifest.project_id) == str(project_id)
            and self.manifest.revoked_at is None
            and self.authority.permits(self.manifest)
        )


@contextmanager
def fixture_current_facts(store, *, project_id, org_id, authority, pin=None):
    """Pin real publication rows, without pretending to qualify live source state."""
    with pinned_read(store, project_id, pin=pin) as (manifest, _):
        proof = authority(manifest)
        if not isinstance(proof, FixtureAuthority) or not proof.permits(manifest):
            yield None
            return
        read = FixtureCurrentFactsRead(manifest, org_id, proof)
        try:
            yield read
        finally:
            read.active = False


def parity_hashes(rows):
    """Structure hash masks floats; a separate hash covers exact float.hex bytes.

    Do not round floats or conceal drift in a structural hash. Datetimes are UTC
    microsecond strings, UUIDs their canonical strings. Lists keep response order;
    maps sort their keys. Both digests must match.
    """
    floats = []

    def normalize(value):
        if isinstance(value, float):
            floats.append(value.hex())
            return {"float_index": len(floats) - 1}
        if isinstance(value, datetime):
            return (
                value.replace(tzinfo=value.tzinfo or UTC)
                .astimezone(UTC)
                .isoformat(timespec="microseconds")
            )
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, dict):
            return {str(k): normalize(v) for k, v in sorted(value.items())}
        if isinstance(value, list | tuple):
            return [normalize(v) for v in value]
        return value

    structural = json.dumps(normalize(rows), sort_keys=True, separators=(",", ":"))
    return {
        "structural_sha256": hashlib.sha256(structural.encode()).hexdigest(),
        "float_sha256": hashlib.sha256(
            json.dumps(floats, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def native_source_schema(client):
    """Use the native physical replacement key, not the older metric fixture key."""
    client.execute("DROP TABLE spans")
    client.execute("""CREATE TABLE spans (
        project_id UUID, org_id Nullable(UUID), observation_type LowCardinality(String) DEFAULT 'span',
        service_name LowCardinality(String) DEFAULT '', trace_id String, id String,
        trace_session_id Nullable(UUID), end_user_id Nullable(UUID),
        start_time DateTime64(6,'UTC'), end_time Nullable(DateTime64(6,'UTC')),
        parent_span_id String DEFAULT '', name String DEFAULT '', input String DEFAULT '',
        input_length UInt32 MATERIALIZED lengthUTF8(input),
        cost Float64 DEFAULT 0, total_tokens Int32 DEFAULT 0, prompt_tokens Int32 DEFAULT 0,
        completion_tokens Int32 DEFAULT 0, attrs_string Map(String,String),
        attrs_number Map(String,Float64), attrs_bool Map(String,UInt8),
        attributes_extra String DEFAULT '{}',
        created_at DateTime64(6,'UTC'), updated_at DateTime64(6,'UTC'),
        _version UInt64, is_deleted UInt8 DEFAULT 0,
        model String DEFAULT '', provider String DEFAULT '', status String DEFAULT '', latency_ms Int32 DEFAULT 0
    ) ENGINE=ReplacingMergeTree(_version) PARTITION BY toDate(start_time)
    ORDER BY (project_id,observation_type,service_name,toStartOfHour(start_time),trace_id,id)""")


def source_row(
    writer, *, span="root", session, at, version=1, trace="trace-a", **payload
):
    data = {
        "org_id": UUID(str(writer.org_id)),
        "project_id": UUID(str(writer.lease.project_id)),
        "trace_id": trace,
        "id": span,
        "trace_session_id": UUID(str(session)),
        "start_time": at,
        "end_time": at,
        "created_at": at,
        "updated_at": at,
        "_version": version,
        **payload,
    }
    writer.store.client.execute(
        f"INSERT INTO spans ({', '.join(data)}) VALUES", [tuple(data.values())]
    )


def filters(start, end):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "col_type": "SYSTEM",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    ]


class ApplicationQueries:
    """Run native and dictionary helper SQL on the same isolated test database.

    The application's hydration futures are real; serialize access to this one
    test driver's connection, which is not safe for concurrent execute calls.
    No helper rows, name maps or user maps are mocked.
    """

    def __init__(self, client):
        from threading import Lock

        self.client = client
        self.lock = Lock()
        self.statements = []

    def query(self, sql, parameters=None, **kwargs):
        from types import SimpleNamespace

        with self.lock:
            self.statements.append(sql)
            rows, columns = self.client.execute(
                sql,
                parameters or {},
                with_column_types=True,
                settings={
                    "max_threads": 1,
                    "max_memory_usage": 512 * 1024 * 1024,
                    "max_block_size": 500,
                    "max_joined_block_size_rows": 500,
                    "use_query_cache": 0,
                },
            )
        names = [name for name, _ in columns]
        return SimpleNamespace(
            result_rows=rows,
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
        )

    def execute_ch_query(self, sql, params, **kwargs):
        return self.query(sql, parameters=params)


def native_label_schema(client, *, sessions, users):
    """Real dictionary-backed label helpers, using only synthetic fixture rows."""
    client.execute(
        "CREATE TABLE bl01_session_labels (trace_session_id UUID, external_session_id String) "
        "ENGINE=MergeTree ORDER BY trace_session_id"
    )
    client.execute(
        "CREATE TABLE bl01_user_labels (end_user_id UUID, user_id String, "
        "user_id_type Nullable(String), user_id_hash String) "
        "ENGINE=MergeTree ORDER BY end_user_id"
    )
    client.execute("INSERT INTO bl01_session_labels VALUES", sessions)
    client.execute("INSERT INTO bl01_user_labels VALUES", users)
    client.execute(
        """CREATE DICTIONARY trace_sessions_dict (
        trace_session_id UUID, external_session_id String DEFAULT ''
    ) PRIMARY KEY trace_session_id
    SOURCE(CLICKHOUSE(USER %(dictionary_user)s PASSWORD %(dictionary_password)s TABLE 'bl01_session_labels'))
    LAYOUT(COMPLEX_KEY_HASHED()) LIFETIME(0)""",
        {
            "dictionary_user": client.connection.user,
            "dictionary_password": client.connection.password,
        },
    )
    client.execute(
        """CREATE DICTIONARY end_users_dict (
        end_user_id UUID, user_id String DEFAULT '',
        user_id_type Nullable(String), user_id_hash String DEFAULT ''
    ) PRIMARY KEY end_user_id
    SOURCE(CLICKHOUSE(USER %(dictionary_user)s PASSWORD %(dictionary_password)s TABLE 'bl01_user_labels'))
    LAYOUT(COMPLEX_KEY_HASHED()) LIFETIME(0)""",
        {
            "dictionary_user": client.connection.user,
            "dictionary_password": client.connection.password,
        },
    )
