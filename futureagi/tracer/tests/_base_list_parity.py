"""Independent old-entry-point loader and deterministic Sessions parity hashes."""

import builtins
import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

from tracer.selectors.filter_seed_width import EMPTY_DENSITY_ESTIMATE
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
    for key, commit in (
        ("historical_closure", "9fc537515f48ae3b2da48d9084892de987a84028"),
        ("native_dev_closure", "8ddeb72ab0fa8d2f46dc38c212252f9ebb0d35ca"),
    ):
        closure = fixture[key]
        assert closure["source_commit"] == commit
        for entry in (*closure["modules"].values(), *closure["data_files"].values()):
            assert (
                hashlib.sha256(entry["source"].encode()).hexdigest() == entry["sha256"]
            )
    for path in BASELINE_PATHS:
        name = "tracer.services.clickhouse." + path.removesuffix(".py").replace(
            "/", "."
        )
        assert (
            fixture["builders"][path]["source"]
            == fixture["historical_closure"]["modules"][name]["source"]
        )
    return fixture


def historical_session_builder():
    """Historical 9fc537515 compiler, including its historical SQL helpers.

    This is NOT the native fallback oracle after the pinned-dev merge: dev
    intentionally changed cost nullability, remap identity and usage coverage.
    Keep it for historical controls, never mix it with installed SQL helpers.
    """
    return _load_frozen_session_builder(baseline_fixture()["historical_closure"])


def native_session_builder():
    """Independent native oracle from the exact dev base of the Metrics merge.

    8ddeb72ab0 is dev without the Metrics source seam and owns the approved
    native semantic changes (re-pinned from 13b60b895 to take dev's #3762
    hydration usage SQL). It is exported from dev, never from the merged
    candidate, so its source and SQL helper closure are not current snapshots.
    """
    return _load_frozen_session_builder(baseline_fixture()["native_dev_closure"])


def old_session_builder():
    """Compatibility entry point for native-vs-Metrics integration comparisons.

    'Old' now explicitly means the pre-seam native dev base (8ddeb72ab0), not
    9fc537515 plus today's helpers. Historical replay uses the separately named
    historical_session_builder and unchanged recorded statements. This keeps
    existing hydration/application parity consumers on the intended native
    contract without teaching them exceptions for approved dev deltas.
    """
    return native_session_builder()


def _load_frozen_session_builder(closure):
    """Private import graph; no global application module replacement or Git.

    Declared Django/ORM interfaces and the opaque density-result sentinel are
    the explicit live boundaries. SQL helpers and density reducers stay frozen.
    Filter-contract data is materialized privately, so __file__-relative reads
    cannot accidentally use today's contract. No fake installed packages.
    """
    root = tempfile.TemporaryDirectory(prefix="session-parity-")
    modules = closure["modules"]
    loaded = {}
    identity = hashlib.sha256(json.dumps(closure, sort_keys=True).encode()).hexdigest()
    prefix = (
        "_session_parity_" + identity + "_" + Path(root.name).name.replace("-", "_")
    )
    for path, entry in closure["data_files"].items():
        destination = Path(root.name) / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(entry["source"])

    def frozen_import(name, globals=None, locals=None, fromlist=(), level=0):
        assert level == 0, "new relative import requires explicit frozen resolution"
        if name in modules:
            assert fromlist, "compiler imports must name their imported interface"
            return load(name)
        children = {item: name + "." + item for item in (fromlist or ())}
        if children and all(child in modules for child in children.values()):
            return SimpleNamespace(
                **{item: load(child) for item, child in children.items()}
            )
        if name.startswith(("tracer.", "model_hub.")):
            assert name in closure["external_application_interfaces"], name
        return builtins.__import__(name, globals, locals, fromlist, level)

    def load(name):
        if name not in loaded:
            entry = modules[name]
            assert (
                hashlib.sha256(entry["source"].encode()).hexdigest() == entry["sha256"]
            )
            module = ModuleType(prefix + "." + name)
            module.__file__ = str(Path(root.name) / entry["path"])
            module.__dict__["__builtins__"] = {
                **vars(builtins),
                "__import__": frozen_import,
            }
            loaded[name] = module
            # Dataclasses consult their defining module; production names stay untouched.
            sys.modules[module.__name__] = module
            exec(compile(entry["source"], module.__file__, "exec"), module.__dict__)
            if name == "tracer.selectors.filter_seed_width":
                # Live selectors compare this non-SQL result by identity. Share
                # only the marker, not the module's reducers or compiler helpers.
                module.EMPTY_DENSITY_ESTIMATE = EMPTY_DENSITY_ESTIMATE
        return loaded[name]

    builder = load(
        "tracer.services.clickhouse.v2.query_builders.session_list"
    ).SessionListQueryBuilderV2
    builder._parity_fixture_root = root
    return builder


def assert_sql_and_parameters_equal(actual, expected):
    """Ignore ONLY lexer whitespace, never literals, comments, operators or params.

    sqlparse is the maintained Django dependency pinned to 0.5.5 in uv.lock.
    Its lexer preserves exact token values; this is not split/join, SQL rendering,
    alias folding or an optimizer. Parameter type/value equality remains exact.
    """
    from sqlparse import lexer, tokens

    def significant(sql):
        return tuple(
            (kind, value)
            for kind, value in lexer.tokenize(sql)
            if kind not in tokens.Whitespace
        )

    def parameter_types(value):
        if isinstance(value, dict):
            return (
                type(value),
                {key: parameter_types(item) for key, item in value.items()},
            )
        if isinstance(value, list | tuple):
            return (type(value), tuple(parameter_types(item) for item in value))
        return type(value)

    assert actual[1] == expected[1] and parameter_types(actual[1]) == parameter_types(
        expected[1]
    ), "SQL parameter drift"
    assert significant(actual[0]) == significant(expected[0]), "SQL token drift"


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
