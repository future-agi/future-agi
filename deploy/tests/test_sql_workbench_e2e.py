"""SQL-specific managed-stack contracts; no Docker daemon or database needed."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "sql_fixture_bootstrap", ROOT / "e2e/stack/backend/bootstrap_sql.py"
)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class OverlayLoader(yaml.SafeLoader):
    pass


OverlayLoader.add_constructor(
    "!reset", lambda loader, node: loader.construct_sequence(node)
)


def test_sql_worker_and_api_are_gated_on_readonly_fixture():
    services = yaml.load(
        (ROOT / "e2e/stack/docker-compose.e2e.yml").read_text(), Loader=OverlayLoader
    )["services"]
    provision = services["sql-workbench-bootstrap"]
    assert (
        provision["depends_on"]["clickhouse-cdc-bootstrap"]["condition"]
        == "service_completed_successfully"
    )
    assert provision["environment"]["FUTURE_AGI_E2E_FIXTURE"] == "sql-workbench"
    for name in ["backend", "worker-exact-aggregation"]:
        service = services[name]
        assert (
            service["depends_on"]["sql-workbench-bootstrap"]["condition"]
            == "service_completed_successfully"
        )
        assert service["environment"]["SQL_WORKBENCH_CH_USER"] == bootstrap.ACCOUNT
        assert (
            service["environment"]["SQL_WORKBENCH_CH_PASSWORD"]
            == bootstrap.FIXTURE_PASSWORD
        )
        assert service["volumes"] == []
    assert "bootstrap_sql.py" in (ROOT / "e2e/stack/backend/Dockerfile").read_text()
    assert "bootstrap_sql.py" not in (ROOT / "Dockerfile").read_text()


@pytest.mark.parametrize("existing", [False, True])
def test_fixture_normalizes_only_its_account_using_the_production_profile(existing):
    from tracer.sql_workbench.runner import QUERY_SETTINGS

    client = Mock()
    statements = []

    def execute(sql, params=None):
        if "FROM system.processes" in sql:
            return [("::ffff:172.23.0.7",)]
        if "FROM system.users" in sql:
            return [(int(existing),)]
        if "FROM system.role_grants" in sql:
            return [(0,)]
        statements.append(sql)
        return []

    client.execute.side_effect = execute
    env = {
        "FUTURE_AGI_E2E_FIXTURE": "sql-workbench",
        "SQL_WORKBENCH_CH_USER": bootstrap.ACCOUNT,
        "SQL_WORKBENCH_CH_PASSWORD": bootstrap.FIXTURE_PASSWORD,
        "CH_HOST": "clickhouse",
        "CH_DATABASE": "test_sql",
    }
    bootstrap.provision_fixture(env, lambda **_: client)
    expected = "ALTER USER" if existing else "CREATE USER"
    assert statements[0].startswith(f"{expected} {bootstrap.ACCOUNT} ")
    assert bootstrap.FIXTURE_PASSWORD not in statements[0]
    assert "HOST ANY" not in statements[0]
    assert "HOST IP '172.23.0.0/16'" in statements[0]
    for key, value in QUERY_SETTINGS.items():
        expected = (
            f"{key} = '{value}' READONLY"
            if isinstance(value, str)
            else f"{key} = {value} READONLY"
        )
        assert expected in statements[0]
    assert (
        len([sql for sql in statements if sql.startswith("GRANT SELECT ON test_sql.")])
        == 6
    )
    if existing:
        assert f"REVOKE ALL ON *.* FROM {bootstrap.ACCOUNT}" in statements
    assert not any(
        sql.startswith(("DROP", "DELETE", "TRUNCATE", "INSERT")) for sql in statements
    )
    client.disconnect.assert_called_once()


def test_fixture_guard_rejects_nonfixture_configuration_before_connecting():
    factory = Mock()
    with pytest.raises(ValueError):
        bootstrap.provision_fixture({}, factory)
    factory.assert_not_called()


def test_fixture_rejects_unexpected_roles_without_overwriting_the_account():
    client = Mock()
    client.execute.side_effect = [[("::ffff:172.23.0.7",)], [(1,)], [(1,)]]
    env = {
        "FUTURE_AGI_E2E_FIXTURE": "sql-workbench",
        "SQL_WORKBENCH_CH_USER": bootstrap.ACCOUNT,
        "SQL_WORKBENCH_CH_PASSWORD": bootstrap.FIXTURE_PASSWORD,
        "CH_HOST": "clickhouse",
    }
    with pytest.raises(ValueError, match="Unexpected roles"):
        bootstrap.provision_fixture(env, lambda **_: client)
    assert all(
        call.args[0].startswith("SELECT") for call in client.execute.call_args_list
    )
    client.disconnect.assert_called_once()
