"""Run the shipped native ClickHouse stage on isolated fresh/retained sidecar DBs.

No application bootstrap or CDC job is simulated. The Continue/Ready ordering is
covered separately by each topology's installer contracts; here every selected
native call uses the real driver, and native writes take the real PG lock.
"""

import ast
import json
import os
import shlex
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
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    SkillRuntimeQueryBuilder,
)
from tracer.tests.test_skill_filters import END, START, insert_rows, span_row
from tracer.tests.test_skill_trace_links import assert_no_skill_objects

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
    # not a hand-written argument list.
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


def native_boot(args, db, *, check=True):
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
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT / "futureagi",
        env=env,
        check=check,
        capture_output=not check,
        text=True,
        timeout=100,
    )


@pytest.mark.integration
@pytest.mark.parametrize("topology", ["distributed", "helm", "standalone"])
def test_native_stage_reads_retained_and_new_loads_without_extra_schema(topology):
    with _ch_test_owned_database("test_skill_native_") as db:
        with _open_ch_test_http_client(database=db) as client:
            args = installer(topology)
            assert client.query("SHOW TABLES").result_rows == []
            native_boot(args, db)
            assert_no_skill_objects(client)

            project = str(uuid4())
            retained = span_row(project, "retained")
            new = span_row(project, "new")
            insert_rows(client, [retained])
            native_boot(args, db)
            insert_rows(client, [new])
            assert_no_skill_objects(client)
            query = SkillRuntimeQueryBuilder(
                project_ids=[project], start=START, end=END
            )
            sql, params = query.build_trace_loads(
                trace_ids=[retained["trace_id"], new["trace_id"]]
            )
            rows = client.query(sql, parameters=params).named_results()
            assert sorted(row["span_id"] for row in rows) == sorted(
                [retained["id"], new["id"]]
            )


@pytest.mark.integration
def test_production_native_operator_command_and_read_only_gate(monkeypatch):
    # The deployment contract module is outside the backend package. Its
    # renderer supplies only synthetic config values and never starts services.
    import importlib

    monkeypatch.syspath_prepend(str(ROOT))
    render = importlib.import_module(
        "deploy.tests.test_observed_catalog_startup"
    ).render

    service_name = "clickhouse-native-bootstrap"
    service = render("production")[service_name]
    entry, *module = service["entrypoint"]
    assert entry == "python" and module == ["-m", NATIVE]
    check = module + service["command"]
    assert "--apply" not in check
    # `compose run SERVICE ARGS...` replaces (does not append to) command.
    # Use the args an operator actually copies, not the unoverlaid defaults.
    prefix = f"$COMPOSE run --rm {service_name} "
    (line,) = (
        line
        for line in (ROOT / "deploy/README.md").read_text().splitlines()
        if line.startswith(prefix)
    )
    apply = module + shlex.split(line[len(prefix) :])
    with _ch_test_owned_database("test_skill_production_") as db:
        with _open_ch_test_http_client(database=db) as client:
            assert client.query("SHOW TABLES").result_rows == []
            missing = native_boot(check, db, check=False)
            assert missing.returncode == 1
            result = json.loads(missing.stdout.splitlines()[-1])
            assert "spans" in result["missing_objects"]
            assert client.query("SHOW TABLES").result_rows == []
            for _ in range(2):
                native_boot(apply, db)
                native_boot(check, db)
                assert_no_skill_objects(client)
