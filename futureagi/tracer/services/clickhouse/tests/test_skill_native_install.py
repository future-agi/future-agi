"""Run the shipped native ClickHouse stage on isolated fresh/retained sidecar DBs.

No application bootstrap or CDC job is simulated. The Continue/Ready ordering is
covered separately by each topology's installer contracts; here every selected
native call goes through the real PG lock, driver, native DDL and schema ledger.
"""

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from django.conf import settings

from conftest import (
    _ch_test_http_port,
    _ch_test_native_port,
    _ch_test_owned_database,
    _is_loopback_ch25_host,
    _open_ch_test_http_client,
)
from tracer.tests.test_skill_filters import insert_rows, span_row

ROOT = Path(__file__).resolve().parents[5]
NATIVE = "tracer.services.clickhouse.oss_cdc_install"


def installer(topology):
    if topology == "distributed":
        rendered = subprocess.check_output(
            [
                "docker",
                "compose",
                "-f",
                "docker-compose.distributed.yml",
                "config",
                "--format",
                "json",
                "clickhouse-native-bootstrap",
            ],
            cwd=ROOT,
            text=True,
        )
        service = json.loads(rendered)["services"]["clickhouse-native-bootstrap"]
        python, *args = service["entrypoint"] + service["command"]
        assert python == "python" and args[:2] == ["-m", NATIVE]
        return args

    # Extract the actual native stage call, including its options, from the
    # shipping Python entrypoint. Execute it with the real installer bindings,
    # not a hand-written `--files 028` or a fake installer.
    if topology == "standalone":
        path = ROOT / "deploy/standalone/bin/bootstrap.py"
        method = "main"
        bindings = (
            "import tfc.management.commands.bootstrap_install as steps; log = print"
        )
    else:
        path = ROOT / "futureagi/tfc/management/commands/bootstrap_install.py"
        method = "handle"
        bindings = "from tfc.management.commands.bootstrap_install import Command, clickhouse_native_schema; self = Command(); options = {'clickhouse_timeout': 300}"
    tree = ast.parse(path.read_text())
    (func,) = (
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == method
    )
    calls = [
        stmt.value
        for stmt in func.body
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
    ]
    (native,) = (
        c
        for c in calls
        if ast.unparse(c.func).split(".")[-1] == "clickhouse_native_schema"
    )
    return ["-c", f"{bindings}; {ast.unparse(native)}"]


def native_boot(args, db):
    pg = settings.DATABASES["default"]
    ch_host = os.environ.get("CH25_HOST", "127.0.0.1")
    assert _is_loopback_ch25_host(ch_host) and _is_loopback_ch25_host(pg["HOST"])
    # Bind both sides of the independent installer to the same verified test
    # sidecars as pytest; ignore any operator endpoint in the caller's shell.
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "DJANGO_SETTINGS_MODULE",
            "LITELLM_LOCAL_MODEL_COST_MAP",
        }
    }
    env.update(
        CH_HOST=ch_host,
        CH_HTTP_PORT=str(_ch_test_http_port().port),
        CH_PORT=str(_ch_test_native_port().port),
        CH_DATABASE=db,
        CH_USERNAME=os.environ.get("CH25_USER")
        or os.environ.get("CH_USERNAME")
        or "default",
        CH_PASSWORD=os.environ.get("CH25_PASSWORD")
        or os.environ.get("CH_PASSWORD", ""),
        PG_HOST=pg["HOST"],
        PG_PORT=str(pg["PORT"]),
        PG_DB=pg["NAME"],
        PG_USER=pg["USER"],
        PG_PASSWORD=pg["PASSWORD"],
    )
    subprocess.run(
        [sys.executable, *args],
        cwd=ROOT / "futureagi",
        env=env,
        check=True,
        timeout=100,
    )


@pytest.mark.integration
@pytest.mark.parametrize("topology", ["distributed", "helm", "standalone"])
def test_native_stage_fresh_and_retry(topology):
    with _ch_test_owned_database("test_skill_native_") as db:
        with _open_ch_test_http_client(database=db) as client:
            args = installer(topology)

            def query(sql):
                return client.query(sql).result_rows

            assert query("SHOW TABLES") == []
            project = str(uuid4())
            native_boot(args, db)
            insert_rows(client, [span_row(project, "installed")])
            native_boot(args, db)
            assert query("SELECT count() FROM skill_trace_links FINAL") == [(1,)]
            packaged = (
                ROOT
                / "futureagi/tracer/services/clickhouse/v2/schema/028_skill_attribution.sql"
            )
            # No unrelated schema file or duplicate receipt on re-entry.
            assert query(
                "SELECT filename, toString(sha256), notes FROM schema_versions"
            ) == [
                (
                    packaged.name,
                    hashlib.sha256(packaged.read_bytes()).hexdigest(),
                    "schema-topology/v1/local",
                )
            ]
            assert query("SELECT count() FROM spans FINAL") == [(1,)]


@pytest.mark.integration
def test_native_stage_upgrade_without_boot_backfill():
    with _ch_test_owned_database("test_skill_upgrade_") as db:
        with _open_ch_test_http_client(database=db) as client:
            native_boot(["-m", NATIVE, "--phase", "native", "--apply"], db)
            project = str(uuid4())
            insert_rows(client, [span_row(project, "retained")])
            native_boot(installer("distributed"), db)
            assert client.query("SELECT count() FROM spans FINAL").result_rows == [(1,)]
            assert client.query(
                "SELECT count() FROM skill_trace_links FINAL"
            ).result_rows == [(0,)]
            insert_rows(client, [span_row(project, "new-after-upgrade")])
            native_boot(installer("distributed"), db)
            assert client.query(
                "SELECT count() FROM skill_trace_links FINAL"
            ).result_rows == [(1,)]
