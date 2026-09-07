"""Exercise the actual bootstrap handoff with external commands intercepted."""

from types import SimpleNamespace
from unittest.mock import Mock

import application_smoke as application
import pytest


class BootstrapReached(Exception):
    pass


def run(tmp_path):
    return SimpleNamespace(
        directory=tmp_path,
        deadline=123,
        manifest={
            "run_id": "0123456789abcdef",
            "database": "property_catalog_dev_smoke_0123456789abcdef",
            "candidate_topic": "fixture.candidate",
            "ordered_topic": "fixture.ordered",
        },
    )


def test_only_exact_old_catalog_capture_grants_revoked_before_bootstrap(
    tmp_path, monkeypatch
):
    lane = run(tmp_path)
    old = lane.manifest["database"]
    commands = []

    def execute(service, args):
        assert service == "clickhouse"
        commands.append(args)
        if args[0] == "env":
            raise BootstrapReached

    lane.execute = execute
    monkeypatch.setattr(application, "snapshot", Mock())
    monkeypatch.setattr(application, "Run", lambda *args: SimpleNamespace())
    with pytest.raises(BootstrapReached):
        application.execute(lane, "offline-python")
    sql = [args[-1] for args in commands[:-1]]
    expected = {
        f"REVOKE SELECT, INSERT ON `{old}`.* FROM property_catalog_oss_control",
        f"REVOKE INSERT ON `{old}`.* FROM property_catalog_oss_consumer",
        f"REVOKE SELECT ON `{old}`.* FROM property_catalog_oss_ledger",
        f"REVOKE SELECT ON `{old}`.* FROM property_catalog_oss_api",
        f"REVOKE SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON `{old}_source_capture`.* FROM property_catalog_oss_control",
        f"REVOKE SELECT ON `{old}_source_capture`.* FROM property_catalog_oss_source",
    }
    assert set(sql) == expected and len(sql) == len(expected)
    assert all(statement.startswith("REVOKE ") for statement in sql)
    assert all(
        "default.spans" not in statement and "system." not in statement
        for statement in sql
    )
    assert (
        "PROPERTY_CATALOG_TARGET_DATABASE=property_catalog_dev_app_0123456789abcdef"
        in commands[-1]
    )


@pytest.mark.parametrize(
    "database",
    [
        "default",
        "property_catalog_dev_foreign",
        "property_catalog_dev_smoke_0123456789abcdef_source_capture",
        "property_catalog_dev_smoke_0123456789abcdef`; DROP DATABASE default",
    ],
)
def test_cleanup_refuses_nonowned_namespace_before_any_command(
    tmp_path, monkeypatch, database
):
    lane = run(tmp_path)
    lane.manifest["database"] = database
    lane.execute = Mock()
    snapshot = Mock()
    monkeypatch.setattr(application, "snapshot", snapshot)
    with pytest.raises(RuntimeError, match="exact owned transport namespace"):
        application.execute(lane, "offline-python")
    lane.execute.assert_not_called()
    snapshot.assert_not_called()
