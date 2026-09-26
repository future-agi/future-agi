"""Dictionary CLICKHOUSE sources carry the connecting credentials.

A dictionary declared as ``SOURCE(CLICKHOUSE(TABLE 'traces'))`` reads its local
source as user ``default`` with an empty password. With a password on the
ClickHouse users every dictGet fails with AUTHENTICATION_FAILED, and because
spans.trace_name evaluates dictGetOrDefault('trace_dict', ...) every span insert
fails too. These tests pin the shared rewriter and every path that executes
dictionary DDL: the credentials are added only when a password is configured,
never logged, and live definitions that show them as ``'[HIDDEN]'`` still pass
the drift checks. Service-free: all transports are doubles.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import re
import socket
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import clickhouse_connect
import psycopg
import pytest

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_install as cli
from tracer.services.clickhouse import oss_native_bootstrap as native
from tracer.services.clickhouse import oss_outbox_cdc as outbox
from tracer.services.clickhouse import schema as legacy_schema
from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import (
    DATABASE as CDC_DATABASE,
)
from tracer.services.clickhouse.tests.test_oss_cdc_bootstrap import (
    MemoryClient as CDCMemoryClient,
)
from tracer.services.clickhouse.tests.test_oss_native_bootstrap import (
    DATABASE as NATIVE_DATABASE,
)
from tracer.services.clickhouse.tests.test_oss_native_bootstrap import (
    DEFAULT_NAMES as NATIVE_NAMES,
)
from tracer.services.clickhouse.tests.test_oss_native_bootstrap import (
    MemoryClient as NativeMemoryClient,
)
from tracer.services.clickhouse.v2 import apply_schema
from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    dictionary_credentials_outdated,
    redact_secret,
    split_statements,
    with_dictionary_credentials,
    without_dictionary_credentials,
)

BACKEND = Path(__file__).resolve().parents[4]
V2_SCHEMA = BACKEND / "tracer" / "services" / "clickhouse" / "v2" / "schema"
DATASET_VIEWS = (
    BACKEND / "tracer" / "services" / "clickhouse" / "schema" / "dataset_views.sql"
)
PLATFORM_BOOTSTRAP = BACKEND.parent / "deploy" / "platform" / "bin" / "bootstrap.py"

USER = "app"
# A single quote, a backslash and a double quote: every escaping hazard.
PASSWORD = "p'a\\ss\"word"
ESCAPED = "'p\\'a\\\\ss\"word'"
CREDENTIALS = f"USER 'app' PASSWORD {ESCAPED}"
NATIVE_DICTS = ("trace_dict", "end_users_dict", "trace_sessions_dict")
CDC_DICTS = tuple(
    name for name in core.DEPENDENT if name not in core._VIEW_PREREQUISITES
)
SOURCE = re.compile(r"SOURCE\s*\(\s*CLICKHOUSE\s*\(", re.IGNORECASE)
TRACE_DICT = (
    "CREATE DICTIONARY d (id UUID, name String) PRIMARY KEY id "
    "SOURCE(CLICKHOUSE(TABLE 'traces')) LIFETIME(MIN 30 MAX 60) LAYOUT(HASHED());"
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("offline test attempted a connection"))
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    yield
    forbidden.assert_not_called()


def hidden(sql: str) -> str:
    """How ClickHouse shows a dictionary's source password in its metadata."""
    return re.sub(r"PASSWORD\s+'(?:\\.|''|[^'\\])*'", "PASSWORD '[HIDDEN]'", sql)


def dictionary_statements(statements):
    return [sql for sql in statements if SOURCE.search(sql)]


def assert_credentialed(sql: str, user: str = USER, escaped: str = ESCAPED):
    assert f"USER '{user}' PASSWORD {escaped}" in sql, sql
    assert not dictionary_credentials_outdated(sql, user)


# ---------------------------------------------------------------------------
# The shared rewriter
# ---------------------------------------------------------------------------


def test_without_a_password_the_statement_is_returned_unchanged():
    assert with_dictionary_credentials(TRACE_DICT, USER, "") is TRACE_DICT
    assert with_dictionary_credentials(TRACE_DICT, "", "") is TRACE_DICT


def test_quotes_and_backslashes_are_escaped():
    sql = with_dictionary_credentials(TRACE_DICT, "o'neil\\", PASSWORD)
    assert (
        "SOURCE(CLICKHOUSE(TABLE 'traces' "
        f"USER 'o\\'neil\\\\' PASSWORD {ESCAPED})) LIFETIME" in sql
    )
    # The server's tokenizer sees exactly two literals, not an injection.
    tokens = core._tokens(sql)
    assert "'o\\'neil\\\\'" in tokens and ESCAPED in tokens
    assert not dictionary_credentials_outdated(sql, "o'neil\\")


def test_an_empty_user_means_default():
    assert "USER 'default' PASSWORD 'pw'" in with_dictionary_credentials(
        TRACE_DICT, "", "pw"
    )


@pytest.mark.parametrize(
    "source",
    [
        "SOURCE(CLICKHOUSE(TABLE 'traces' USER 'reader'))",
        "SOURCE(CLICKHOUSE(user 'reader' PASSWORD 'x' TABLE 'traces'))",
        "source ( clickhouse ( TABLE 'traces' User reader ) )",
    ],
)
def test_a_source_that_names_a_user_is_left_alone(source):
    sql = f"CREATE DICTIONARY d (id UUID) PRIMARY KEY id {source} LAYOUT(FLAT())"
    assert with_dictionary_credentials(sql, USER, PASSWORD) == sql


def test_every_dictionary_of_a_multi_statement_file_is_rewritten():
    text = DATASET_VIEWS.read_text()
    sql = with_dictionary_credentials(text, USER, PASSWORD)
    assert sql.count(CREDENTIALS) == 2 == len(SOURCE.findall(text))
    # Only the two sources changed; the view's dictGet calls are untouched.
    assert without_dictionary_credentials(sql).replace(" ", "").replace(
        "\n", ""
    ) == text.replace(" ", "").replace("\n", "")
    assert "dictGet('column_dict', 'name', c.column_id)" in sql


def test_rewriting_is_idempotent():
    once = with_dictionary_credentials(TRACE_DICT, USER, PASSWORD)
    assert with_dictionary_credentials(once, USER, PASSWORD) == once
    assert with_dictionary_credentials(once, "other", "other-password") == once


def test_comments_strings_and_columns_named_source_are_not_clauses():
    sql = (
        "-- SOURCE(CLICKHOUSE(TABLE 'commented'))\n"
        "CREATE DICTIONARY d (source String, id UUID) PRIMARY KEY id\n"
        "SOURCE(CLICKHOUSE(QUERY 'SELECT 1 AS x /* SOURCE(CLICKHOUSE( */' "
        "DB 'db'))\nLAYOUT(FLAT())"
    )
    result = with_dictionary_credentials(sql, USER, PASSWORD)
    assert result.count("PASSWORD") == 1
    assert f"DB 'db' {CREDENTIALS}))" in result
    assert result.startswith("-- SOURCE(CLICKHOUSE(TABLE 'commented'))\n")


def test_statements_without_a_clickhouse_source_are_untouched():
    for sql in (
        "CREATE TABLE t (source String) ENGINE = MergeTree ORDER BY source",
        "CREATE DICTIONARY d (id UInt64) PRIMARY KEY id "
        "SOURCE(FILE(path './d.csv' format 'CSV')) LAYOUT(FLAT()) LIFETIME(0)",
        "SELECT dictGet('trace_dict', 'name', toUUID(id)) FROM spans",
    ):
        assert with_dictionary_credentials(sql, USER, PASSWORD) == sql


def _packaged_dictionaries():
    for path in sorted(V2_SCHEMA.glob("*.sql")):
        for sql in dictionary_statements(split_statements(path.read_text())):
            yield f"v2/{path.name}", sql
    for name, sql in legacy_schema.SCHEMA_DDL_STATEMENTS:
        if SOURCE.search(sql):
            yield f"schema.py:{name}", sql
    yield "dataset_views.sql", DATASET_VIEWS.read_text()


def test_the_packaged_dictionary_sites_are_all_covered():
    sites = [name for name, _ in _packaged_dictionaries()]
    assert sum(site.startswith("v2/") for site in sites) == 3
    assert sum(site.startswith("schema.py:") for site in sites) == 12


@pytest.mark.parametrize("site,sql", list(_packaged_dictionaries()))
def test_every_packaged_dictionary_gets_the_credentials(site, sql):
    assert dictionary_credentials_outdated(sql, USER)
    result = with_dictionary_credentials(sql, USER, PASSWORD)
    assert result.count(CREDENTIALS) == len(SOURCE.findall(sql))
    assert not dictionary_credentials_outdated(result, USER)
    # Stripping them gives back the packaged contract, token for token.
    assert core._tokens(without_dictionary_credentials(result)) == core._tokens(sql)


def test_live_metadata_with_a_hidden_password_compares_equal():
    live = (
        "CREATE DICTIONARY db.trace_dict (`id` UUID) PRIMARY KEY id "
        "SOURCE(CLICKHOUSE(TABLE 'traces' USER 'app' PASSWORD '[HIDDEN]')) "
        "LIFETIME(MIN 30 MAX 60) LAYOUT(COMPLEX_KEY_HASHED(SHARDS 4))"
    )
    assert without_dictionary_credentials(live) == (
        "CREATE DICTIONARY db.trace_dict (`id` UUID) PRIMARY KEY id "
        "SOURCE(CLICKHOUSE(TABLE 'traces')) "
        "LIFETIME(MIN 30 MAX 60) LAYOUT(COMPLEX_KEY_HASHED(SHARDS 4))"
    )
    assert not dictionary_credentials_outdated(live, USER)
    assert dictionary_credentials_outdated(live, "someone_else")
    assert dictionary_credentials_outdated(without_dictionary_credentials(live), USER)
    assert not dictionary_credentials_outdated("CREATE TABLE t (x UInt8)", USER)


def test_redaction_covers_raw_and_escaped_forms():
    text = f"failed near PASSWORD {ESCAPED} (raw {PASSWORD})"
    redacted = redact_secret(text, PASSWORD)
    assert PASSWORD not in redacted and ESCAPED[1:-1] not in redacted
    assert redact_secret(text, "") == text


# ---------------------------------------------------------------------------
# apply_schema (v2 files; ch25_apply_schema, migration 0078, conftest)
# ---------------------------------------------------------------------------

SCHEMA_FILE = """-- two dictionaries and a table
CREATE TABLE IF NOT EXISTS traces (id UUID) ENGINE = MergeTree ORDER BY id;

CREATE DICTIONARY IF NOT EXISTS a_dict (id UUID) PRIMARY KEY id
SOURCE(CLICKHOUSE(TABLE 'traces'))
LIFETIME(MIN 30 MAX 60)
LAYOUT(HASHED());

CREATE DICTIONARY IF NOT EXISTS b_dict (id UUID) PRIMARY KEY id
SOURCE(CLICKHOUSE(TABLE 'traces' WHERE 'id != toUUID(0)'))
LIFETIME(MIN 30 MAX 60)
LAYOUT(HASHED());
"""


@pytest.fixture
def schema_dir(tmp_path):
    (tmp_path / "001_dicts.sql").write_text(SCHEMA_FILE)
    return tmp_path


def _executed(client):
    return [call.args[0] for call in client.command.call_args_list]


def test_apply_file_adds_the_credentials_it_connects_with(schema_dir):
    sf = apply_schema.SchemaFile.from_path(schema_dir / "001_dicts.sql")
    client = Mock()
    assert (
        apply_schema.apply_file(
            client, sf, "tester", ch_user=USER, ch_password=PASSWORD
        )
        == 3
    )
    packaged = split_statements(SCHEMA_FILE)
    executed = _executed(client)
    assert executed[0] == packaged[0]
    for sql, original in zip(executed[1:], packaged[1:], strict=True):
        assert_credentialed(sql)
        assert core._tokens(without_dictionary_credentials(sql)) == core._tokens(
            original
        )
    # The receipt keeps the file's own hash: no drift from the injection.
    [row] = client.insert.call_args.args[1]
    assert row[1] == hashlib.sha256(SCHEMA_FILE.encode()).hexdigest() == sf.sha256


def test_apply_file_without_a_password_runs_the_packaged_text(schema_dir):
    sf = apply_schema.SchemaFile.from_path(schema_dir / "001_dicts.sql")
    client = Mock()
    apply_schema.apply_file(client, sf, "tester", ch_user=USER, ch_password="")
    assert _executed(client) == split_statements(SCHEMA_FILE)


def test_apply_file_never_logs_the_password(schema_dir, monkeypatch):
    log = Mock()
    monkeypatch.setattr(apply_schema, "log", log)
    sf = apply_schema.SchemaFile.from_path(schema_dir / "001_dicts.sql")
    client = Mock()
    client.command.side_effect = [
        None,
        RuntimeError(f"Syntax error near PASSWORD {ESCAPED} and {PASSWORD}"),
    ]
    with pytest.raises(RuntimeError):
        apply_schema.apply_file(
            client, sf, "tester", ch_user=USER, ch_password=PASSWORD
        )
    logged = repr(log.mock_calls)
    assert "statement_failed" in logged
    assert PASSWORD not in logged and ESCAPED[1:-1] not in logged


def test_packaged_trace_dict_file_is_applied_with_credentials():
    sf = apply_schema.SchemaFile.from_path(V2_SCHEMA / "015_traces_and_trace_dict.sql")
    client = Mock()
    apply_schema.apply_file(client, sf, "tester", ch_user=USER, ch_password=PASSWORD)
    [trace_dict] = dictionary_statements(_executed(client))
    assert trace_dict.startswith("CREATE OR REPLACE DICTIONARY trace_dict")
    assert "SOURCE(CLICKHOUSE(TABLE 'traces' " + CREDENTIALS + "))" in trace_dict
    assert (
        client.insert.call_args.args[1][0][1]
        == hashlib.sha256(sf.path.read_bytes()).hexdigest()
    )


@pytest.fixture
def local_apply(monkeypatch):
    """apply_schema.main against a mock server with nothing applied yet."""
    monkeypatch.setenv("ENV_TYPE", "local")
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    client = Mock()
    connect = Mock(return_value=client)
    monkeypatch.setattr(apply_schema.clickhouse_connect, "get_client", connect)
    monkeypatch.setattr(apply_schema, "ensure_versions_table", Mock())
    monkeypatch.setattr(apply_schema, "fetch_applied", Mock(return_value={}))
    return SimpleNamespace(client=client, connect=connect)


def test_apply_schema_cli_uses_its_connection_credentials(schema_dir, local_apply):
    assert (
        apply_schema.main(
            [
                "--schema-dir",
                str(schema_dir),
                "--ch-user",
                USER,
                "--ch-password",
                PASSWORD,
            ]
        )
        == 0
    )
    assert local_apply.connect.call_args.kwargs["username"] == USER
    assert local_apply.connect.call_args.kwargs["password"] == PASSWORD
    dicts = dictionary_statements(_executed(local_apply.client))
    assert len(dicts) == 2
    for sql in dicts:
        assert_credentialed(sql)


def test_apply_schema_cli_takes_the_password_from_ch_password(
    schema_dir, local_apply, monkeypatch
):
    monkeypatch.setenv("CH_PASSWORD", "from-env")
    assert apply_schema.main(["--schema-dir", str(schema_dir)]) == 0
    for sql in dictionary_statements(_executed(local_apply.client)):
        assert_credentialed(sql, user="default", escaped="'from-env'")


def test_apply_schema_cli_without_a_password_is_unchanged(
    schema_dir, local_apply, monkeypatch
):
    monkeypatch.setenv("CH_PASSWORD", "")
    assert apply_schema.main(["--schema-dir", str(schema_dir)]) == 0
    assert _executed(local_apply.client) == split_statements(SCHEMA_FILE)


V2_CONFIG = {
    "host": "clickhouse",
    "http_port": 8123,
    "tcp_port": 9000,
    "user": USER,
    "password": PASSWORD,
    "database": "analytics",
    "server_enforced_readonly": False,
}


def _v2_dictionaries_applied_with_credentials(client):
    dicts = dictionary_statements(_executed(client))
    assert [re.search(r"DICTIONARY (?:IF NOT EXISTS )?(\w+)", s)[1] for s in dicts] == [
        "trace_dict",
        "end_users_dict",
        "trace_sessions_dict",
    ]
    for sql in dicts:
        assert_credentialed(sql)


def test_ch25_apply_schema_command_applies_dictionaries_with_credentials(
    local_apply, monkeypatch
):
    from tracer.management.commands import ch25_apply_schema

    # Absent before the command, so its CH_PASSWORD default comes from config.
    monkeypatch.setenv("CH_PASSWORD", "placeholder")
    monkeypatch.delenv("CH_PASSWORD")
    monkeypatch.setattr(ch25_apply_schema, "get_v2_config", lambda: dict(V2_CONFIG))
    ch25_apply_schema.Command().handle(
        status=False,
        force=False,
        files=None,
        replicated=False,
        cluster=None,
        zk_table_path_prefix=None,
    )
    assert local_apply.connect.call_args.kwargs["username"] == USER
    _v2_dictionaries_applied_with_credentials(local_apply.client)


def test_migration_0078_applies_dictionaries_with_credentials(local_apply, monkeypatch):
    from tracer.services.clickhouse import v2

    migration = importlib.import_module("tracer.migrations.0078_ch25_apply_schema")
    monkeypatch.delenv("FI_SKIP_CH25_MIGRATION", raising=False)
    monkeypatch.setenv("CH_PASSWORD", "placeholder")
    monkeypatch.delenv("CH_PASSWORD")
    monkeypatch.setattr(v2, "get_v2_config", lambda: dict(V2_CONFIG))
    migration.forwards(None, None)
    _v2_dictionaries_applied_with_credentials(local_apply.client)


# ---------------------------------------------------------------------------
# Native bootstrap (trace_dict, end_users_dict, trace_sessions_dict)
# ---------------------------------------------------------------------------


class ServerNativeClient(NativeMemoryClient):
    """Native double that behaves like the server for dictionary credentials:
    accepts the packaged DDL with credentials in a dictionary's source, and
    CREATE OR REPLACE for an existing dictionary, and shows the password as
    '[HIDDEN]' in create_table_query."""

    def command(self, sql):
        self.events.append(("command", sql))
        packaged = without_dictionary_credentials(sql)
        replacing = packaged.startswith("CREATE OR REPLACE DICTIONARY ")
        if replacing:
            packaged = packaged.replace(
                "CREATE OR REPLACE DICTIONARY ", "CREATE DICTIONARY IF NOT EXISTS ", 1
            )
        name = next(
            n
            for n, ddl in self.definitions.items()
            if core._tokens(ddl) == core._tokens(packaged)
        )
        assert replacing == (name in self.tables)
        if name == self.fail_create:
            raise RuntimeError(f"secret-password://{sql}")
        self.install(
            name,
            hidden(sql).replace(
                "CREATE OR REPLACE DICTIONARY ", "CREATE DICTIONARY IF NOT EXISTS ", 1
            ),
        )

    def close(self):
        pass


def _native_writes(client):
    return [sql for kind, sql in client.events if kind == "command"]


def test_native_fresh_install_creates_dictionaries_with_credentials():
    client = ServerNativeClient()
    created = native.bootstrap_native(
        client, database=NATIVE_DATABASE, ch_user=USER, ch_password=PASSWORD
    )
    assert created == tuple(NATIVE_NAMES)
    writes = _native_writes(client)
    assert len(writes) == len(NATIVE_NAMES)
    for name, sql in zip(created, writes, strict=True):
        if name in NATIVE_DICTS:
            assert_credentialed(sql)
            assert sql.startswith(f"CREATE DICTIONARY IF NOT EXISTS {NATIVE_DATABASE}.")
        else:
            assert sql == client.definitions[name]
    # The next boot inspects '[HIDDEN]' credentials as compatible: no writes.
    assert (
        native.bootstrap_native(
            client, database=NATIVE_DATABASE, ch_user=USER, ch_password=PASSWORD
        )
        == ()
    )
    assert len(_native_writes(client)) == len(NATIVE_NAMES)
    assert native.inspect_native(client, database=NATIVE_DATABASE) == ()


def test_native_without_a_password_runs_the_packaged_text():
    client = ServerNativeClient()
    native.bootstrap_native(
        client, database=NATIVE_DATABASE, ch_user=USER, ch_password=""
    )
    assert _native_writes(client) == list(client.definitions.values())


def test_native_re_creates_dictionaries_created_without_credentials():
    client = ServerNativeClient(complete=True)  # the broken install's layout
    assert (
        native.bootstrap_native(
            client, database=NATIVE_DATABASE, ch_user=USER, ch_password=PASSWORD
        )
        == NATIVE_DICTS
    )
    writes = _native_writes(client)
    assert [w.split("(")[0].split()[-1] for w in writes] == [
        f"{NATIVE_DATABASE}.{name}" for name in NATIVE_DICTS
    ]
    for sql in writes:
        assert sql.startswith("CREATE OR REPLACE DICTIONARY ")
        assert_credentialed(sql)
    assert (
        native.bootstrap_native(
            client, database=NATIVE_DATABASE, ch_user=USER, ch_password=PASSWORD
        )
        == ()
    )
    assert len(_native_writes(client)) == 3


def test_native_re_creates_a_dictionary_bound_to_another_user():
    client = ServerNativeClient(complete=True)
    for name in NATIVE_DICTS:
        client.install(
            name,
            hidden(with_dictionary_credentials(client.definitions[name], "old", "x")),
        )
    assert (
        native.bootstrap_native(
            client, database=NATIVE_DATABASE, ch_user=USER, ch_password=PASSWORD
        )
        == NATIVE_DICTS
    )


def test_native_leaves_existing_dictionaries_alone_without_a_password():
    client = ServerNativeClient(complete=True)
    assert native.bootstrap_native(client, database=NATIVE_DATABASE) == ()
    assert not _native_writes(client)


def test_native_incompatible_dictionary_is_not_repaired():
    client = ServerNativeClient(complete=True)
    ddl = client.definitions["trace_dict"].replace(
        "TABLE 'traces'", "TABLE 'tracer_trace'"
    )
    client.install("trace_dict", ddl)
    with pytest.raises(native.NativeBootstrapError, match="incompatible"):
        native.bootstrap_native(
            client, database=NATIVE_DATABASE, ch_user=USER, ch_password=PASSWORD
        )
    assert not _native_writes(client)


def test_native_failed_re_create_is_terminal_and_redacted():
    client = ServerNativeClient(complete=True)
    client.fail_create = "end_users_dict"
    with pytest.raises(native.NativeBootstrapError) as error:
        native.bootstrap_native(
            client, database=NATIVE_DATABASE, ch_user=USER, ch_password=PASSWORD
        )
    assert "end_users_dict" in str(error.value)
    assert PASSWORD not in str(error.value) and "secret-password" not in str(
        error.value
    )
    assert error.value.__cause__ is None and error.value.__suppress_context__
    assert len(_native_writes(client)) == 2  # trace_dict, then the failed one


# ---------------------------------------------------------------------------
# CDC dependents (prompt_dict, column_dict, simulate_*_dict, ...)
# ---------------------------------------------------------------------------


class ServerCDCClient(CDCMemoryClient):
    """CDC double with the server's view of dictionary credentials."""

    def command(self, sql):
        packaged = without_dictionary_credentials(sql)
        tokens = core._tokens(packaged)
        if tokens[:4] == ("CREATE", "OR", "REPLACE", "DICTIONARY"):
            name = tokens[4]
            assert name in self.tables
            assert tokens == core._tokens(
                core.replace_dictionary_ddl(self.create[name])
            )
            self.events.append(("REPLACE", name, sql))
            self.install(name, hidden(sql).replace("CREATE OR REPLACE", "CREATE", 1))
            return
        if tokens[:2] == ("CREATE", "DICTIONARY"):
            self.events.append(("DDL", sql))
        super().command(packaged)
        if tokens[:2] == ("CREATE", "DICTIONARY"):
            self.install(tokens[5], hidden(sql))


def _bootstrap_cdc(client, **credentials):
    return core.bootstrap_cdc(
        client,
        database=CDC_DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        applied_by="offline-test",
        **credentials,
    )


def test_cdc_dependents_are_created_with_credentials():
    client = ServerCDCClient()
    result = _bootstrap_cdc(client, ch_user=USER, ch_password=PASSWORD)
    ddl = [event[1] for event in client.events if event[0] == "DDL"]
    assert len(ddl) == len(CDC_DICTS)
    for sql in ddl:
        assert_credentialed(sql)
    assert not [event for event in client.events if event[0] == "REPLACE"]
    assert set(CDC_DICTS) <= set(result.created)
    before = list(client.writes)
    _bootstrap_cdc(client, ch_user=USER, ch_password=PASSWORD)
    assert client.writes == before


def test_cdc_dependents_without_a_password_are_the_packaged_text():
    client = ServerCDCClient()
    _bootstrap_cdc(client, ch_user=USER, ch_password="")
    ddl = [event[1] for event in client.events if event[0] == "DDL"]
    assert ddl == [client.create[name] for name in CDC_DICTS]


def test_cdc_dependents_created_without_credentials_are_re_created():
    client = ServerCDCClient(complete=True, ledger=True)
    result = _bootstrap_cdc(client, ch_user=USER, ch_password=PASSWORD)
    replaced = [event for event in client.events if event[0] == "REPLACE"]
    assert [name for _, name, _ in replaced] == list(CDC_DICTS)
    for _, _, sql in replaced:
        assert sql.lstrip().startswith("CREATE OR REPLACE DICTIONARY ")
        assert_credentialed(sql)
    assert result.created == CDC_DICTS
    before = list(client.writes)
    assert _bootstrap_cdc(client, ch_user=USER, ch_password=PASSWORD).created == ()
    assert client.writes == before


def test_cdc_complete_install_without_a_password_stays_write_free():
    client = ServerCDCClient(complete=True, ledger=True)
    assert _bootstrap_cdc(client).created == ()
    assert not client.writes


def test_cdc_inspection_accepts_hidden_credentials_but_not_other_drift():
    client = CDCMemoryClient(complete=True, ledger=True)
    for name in CDC_DICTS:
        client.install(
            name,
            hidden(with_dictionary_credentials(client.create[name], USER, PASSWORD)),
        )
    core.inspect_bootstrap(
        client,
        database=CDC_DATABASE,
        inspect_mirrors=client.inspect_mirrors,
        require_complete=True,
    )
    row = client.tables["prompt_dict"]
    core._check_dependent("prompt_dict", row, client.create["prompt_dict"])
    drifted = (*row[:-1], row[-1].replace("_peerdb_is_deleted = 0", "1 = 1"))
    with pytest.raises(core.BootstrapError, match="prompt_dict"):
        core._check_dependent("prompt_dict", drifted, client.create["prompt_dict"])


# ---------------------------------------------------------------------------
# oss_cdc_install: both phases, the DDL allow-list, bootstrap.py and
# bootstrap_install (the Helm Job) through its CLI
# ---------------------------------------------------------------------------


def _config(password=PASSWORD, database=NATIVE_DATABASE):
    return replace(
        cli.Config.from_env(
            {"CH_USERNAME": USER, "CH_PASSWORD": password, "CH_DATABASE": database}
        ),
        include_usage_schema=True,
    )


def _pg():
    pg = Mock()
    pg.execute.return_value.fetchall.return_value = [(True,)]
    return pg


def _run(raw, *, password=PASSWORD, phase="native", **kwargs):
    return cli.run(
        _config(password, NATIVE_DATABASE if phase == "native" else CDC_DATABASE),
        apply=True,
        phase=phase,
        pg_connect=Mock(return_value=_pg()),
        ch_connect=Mock(return_value=raw),
        request=Mock(),
        **kwargs,
    )


def test_installer_native_phase_creates_dictionaries_with_credentials():
    raw = ServerNativeClient()
    assert _run(raw)["created_objects"] == list(NATIVE_NAMES)
    for sql in dictionary_statements(_native_writes(raw)):
        assert_credentialed(sql)
    assert len(dictionary_statements(_native_writes(raw))) == 3


def test_installer_native_phase_without_a_password_is_unchanged():
    raw = ServerNativeClient()
    _run(raw, password="")
    assert _native_writes(raw) == list(raw.definitions.values())


def test_installer_native_phase_re_creates_uncredentialed_dictionaries():
    raw = ServerNativeClient(complete=True)
    assert _run(raw)["created_objects"] == list(NATIVE_DICTS)
    assert all(
        w.startswith("CREATE OR REPLACE DICTIONARY") for w in _native_writes(raw)
    )


@pytest.mark.parametrize("password", [PASSWORD, ""])
def test_installer_allows_dictionaries_only_with_the_configured_credentials(
    monkeypatch, password
):
    monkeypatch.setattr(cli.native, "inspect_native", Mock(return_value=()))
    raw = Mock()
    ddl = native.native_definitions(NATIVE_DATABASE)["trace_dict"]
    replacing = core.replace_dictionary_ddl(ddl)

    def bootstrap(client, **kwargs):
        assert kwargs["ch_user"] == USER and kwargs["ch_password"] == password
        allowed = [with_dictionary_credentials(ddl, USER, password)]
        refused = [
            with_dictionary_credentials(ddl, USER, "not-the-password"),
            with_dictionary_credentials(ddl, "someone", password or "x"),
        ]
        if password:
            allowed.append(with_dictionary_credentials(replacing, USER, password))
            refused.append(ddl)  # never a passwordless source when one is set
        else:
            refused.append(replacing)
        for sql in refused:
            with pytest.raises(cli.InstallError, match="packaged DDL"):
                client.command(sql)
        for sql in allowed:
            client.command(sql)
        return ()

    monkeypatch.setattr(cli.native, "bootstrap_native", bootstrap)
    _run(raw, password=password)
    assert [c.args[0] for c in raw.command.call_args_list] == (
        [
            with_dictionary_credentials(ddl, USER, password),
            with_dictionary_credentials(replacing, USER, password),
        ]
        if password
        else [ddl]
    )


def test_installer_cdc_phase_passes_credentials_and_allows_credentialed_dependents(
    monkeypatch,
):
    ready = SimpleNamespace(missing=(), upgrade_recorded=True)
    monkeypatch.setattr(cli.core, "inspect_bootstrap", Mock(return_value=ready))
    raw = Mock()
    declarations, _ = core._definitions(CDC_DATABASE, include_usage_schema=True)

    def bootstrap(client, **kwargs):
        assert kwargs["ch_user"] == USER and kwargs["ch_password"] == PASSWORD
        with pytest.raises(cli.InstallError, match="packaged DDL"):
            client.command(declarations["prompt_dict"])
        client.command(
            with_dictionary_credentials(declarations["prompt_dict"], USER, PASSWORD)
        )
        client.command(declarations["dataset_cells"])  # a view: unchanged
        return SimpleNamespace(
            created=("prompt_dict", "dataset_cells"), upgrade_applied=False
        )

    monkeypatch.setattr(cli.core, "bootstrap_cdc", bootstrap)
    assert _run(raw, phase="cdc")["created_objects"] == ["prompt_dict", "dataset_cells"]
    executed = [c.args[0] for c in raw.command.call_args_list]
    assert_credentialed(executed[0])
    assert executed[1] == declarations["dataset_cells"]


INSTALL_ENV = (
    "CH_HOST CH_PORT CH_HTTP_PORT CH_DATABASE CH_USERNAME CH_USER CH_PASSWORD "
    "CH25_HOST CH25_DATABASE CH25_TCP_PORT CH25_HTTP_PORT FI_CH_DATABASE FI_CH_URL "
    "DST_CH_HOST DST_CH_PORT DST_CH_DB DST_CH_USER DST_CH_PASSWORD SRC_PG_HOST "
    "SRC_PG_PORT SRC_PG_DB SRC_PG_USER SRC_PG_PASSWORD PGHOSTADDR PGSERVICE "
    "CLOUD_DEPLOYMENT CH_USE_REPLICATED_ENGINES PEERDB_FLOW_SERVER_HTTP"
).split()


@pytest.fixture
def install_env(monkeypatch):
    """What the Helm chart / standalone container give the installer, with the
    datastores replaced by doubles."""

    def configure(password):
        for name in INSTALL_ENV:
            monkeypatch.delenv(name, raising=False)
        for name, value in {
            "ENV_TYPE": "local",
            "CH_HOST": "clickhouse",
            "CH_DATABASE": NATIVE_DATABASE,
            "CH_USER": USER,
            "CH_USERNAME": USER,
            "CH_PASSWORD": password,
        }.items():
            monkeypatch.setenv(name, value)
        raw = ServerNativeClient()
        connect = Mock(return_value=raw)
        monkeypatch.setattr(clickhouse_connect, "get_client", connect)
        monkeypatch.setattr(psycopg, "connect", Mock(return_value=_pg()))
        return SimpleNamespace(raw=raw, connect=connect)

    return configure


@pytest.mark.parametrize("password", [PASSWORD, ""])
def test_bootstrap_install_native_schema_uses_ch_password(install_env, password):
    from tfc.management.commands import bootstrap_install

    server = install_env(password)
    bootstrap_install.clickhouse_native_schema(lambda _: None, 120)
    assert server.connect.call_args.kwargs["username"] == USER
    assert server.connect.call_args.kwargs["password"] == password
    dicts = dictionary_statements(_native_writes(server.raw))
    assert len(dicts) == 3
    for sql in dicts:
        if password:
            assert_credentialed(sql)
        else:
            assert "PASSWORD" not in sql and sql in server.raw.definitions.values()


def test_platform_bootstrap_native_schema_uses_ch_password(install_env):
    spec = importlib.util.spec_from_file_location(
        "platform_bootstrap", PLATFORM_BOOTSTRAP
    )
    platform_bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(platform_bootstrap)
    server = install_env(PASSWORD)
    platform_bootstrap.clickhouse_native_schema()
    dicts = dictionary_statements(_native_writes(server.raw))
    assert len(dicts) == 3
    for sql in dicts:
        assert_credentialed(sql)


# ---------------------------------------------------------------------------
# Outbox CDC (FI_CDC_MODE=outbox: standalone install and the Helm chart)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("password", [PASSWORD, ""])
def test_outbox_install_passes_its_connection_credentials(monkeypatch, password):
    config = _config(password, CDC_DATABASE)

    @contextmanager
    def lock(pg, *, wait):
        yield True

    @contextmanager
    def writer(pg, *, wait):
        yield SimpleNamespace(last=0)

    for name, value in {
        "peerdb_objects": lambda pg, ch: {
            "slots": [],
            "active_slots": [],
            "publications": [],
            "raw_tables": [],
        },
        "inspect_source": lambda query, *, source, tables: SimpleNamespace(tables={}),
        "advisory_lock": lock,
        "writer": writer,
        "install_capture": lambda pg, tables: {"armed": [], "new_state": list(tables)},
        "ensure_landing_tables": lambda ch, source, **_: ([], []),
        "_ch_max_version": lambda ch, tables: 0,
        "_reset_snapshots": lambda pg, tables: None,
        "load_specs": lambda pg, ch, tables: ({}, {}),
        "_pending_snapshots": lambda pg, tables: [],
    }.items():
        monkeypatch.setattr(outbox, name, value)
    bootstrap = Mock(return_value=SimpleNamespace(created=(), upgrade_applied=False))
    monkeypatch.setattr(outbox.core, "bootstrap_cdc", bootstrap)
    outbox.install(Mock(), Mock(), config=config, apply=True)
    assert bootstrap.call_args.kwargs["ch_user"] == USER
    assert bootstrap.call_args.kwargs["ch_password"] == password


def test_outbox_config_reads_the_same_credentials_as_the_chart_sets():
    config = outbox.load_config(
        {"CH_USERNAME": USER, "CH_USER": USER, "CH_PASSWORD": PASSWORD}
    )
    assert (config.ch_user, config.ch_password) == (USER, PASSWORD)


# ---------------------------------------------------------------------------
# Scripts: peerdb-init.sh (legacy schema.py DDL)
# ---------------------------------------------------------------------------


def test_peerdb_init_applies_legacy_dictionaries_with_client_credentials():
    script = (BACKEND / "scripts" / "peerdb-init.sh").read_text()
    assert (
        "ch.execute(with_dictionary_credentials(ddl, ch.user, ch.password))" in script
    )
    assert "ch.execute(ddl)" not in script
