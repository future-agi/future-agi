"""733 repush isolates typed placement refusals without losing retry journals."""

import json
import socket
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from structlog.testing import capture_logs

from accounts.models import Organization
from accounts.models.retirement_manifest import RetirementManifest
from accounts.services.retirement_733 import repush_configs
from agentcc.models import AgentccOrgConfig
from agentcc.services.config_push import (
    MCPCredentialPlacementRefused,
    OrgConfigRejected,
)

pytestmark = pytest.mark.django_db

CODE = "mcp_credential_placement_unsupported"
PARAM = "mcp.servers.fixture.credential.secret.type"
VALUE = "test-only-retirement-placement-canary"
EVENT = "retirement_config_push_refused"


@pytest.fixture(autouse=True)
def no_egress(db, monkeypatch):
    # The DB fixture opens the test connection first. Every unexpected new
    # socket during these mocked-gateway tests fails closed, including providers.
    def refused(*args, **kwargs):
        raise AssertionError("Unexpected socket in retirement repush test")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)


@pytest.fixture
def configs():
    # Distinct versions make the model's -version ordering deterministic: the
    # refusing org must be first, not whichever UUID the database happens to pick.
    return [
        AgentccOrgConfig._base_manager.create(
            organization=Organization._base_manager.create(name=f"Repush fixture {i}"),
            version=3 - i,
            mcp={
                "enabled": True,
                "servers": {
                    "fixture": {
                        "url": "https://mcp.example.invalid/mcp",
                        "transport": "http",
                        "credential": {
                            "mode": "shared",
                            "credential_id": "cred_fixture",
                            "state": "active",
                        },
                    }
                },
            },
        )
        for i in range(3)
    ]


@pytest.fixture
def gateway():
    client = MagicMock()
    with (
        patch("agentcc.services.config_push.get_gateway_client", return_value=client),
        patch(
            "agentcc.services.gateway_client.get_gateway_client", return_value=client
        ),
        patch("agentcc.services.config_push._assemble_providers", return_value={}),
        patch(
            "agentcc.services.config_push._assemble_delegated_grants", return_value=[]
        ),
        patch(
            "agentcc.services.managed_upstream_keys.assemble_for_push", return_value={}
        ),
    ):
        yield client


@pytest.fixture
def placement_trigger(configs):
    def assemble(org_id):
        if str(org_id) == str(configs[0].organization_id):
            return {
                "fixture": {
                    "type": "bearer",
                    "in": "query",
                    "name": "api_key",
                    "value": VALUE,
                }
            }
        return {}

    with patch(
        "agentcc.services.mcp_credentials.assemble_for_push", side_effect=assemble
    ) as mocked:
        yield mocked


@pytest.fixture
def command_dependencies():
    # Exercise the real command, run_retirement, repush, payload builder and
    # journal; only unrelated retirement phases and gateway transport are faked.
    with (
        patch(
            "accounts.services.retirement_733.GatewayAdapter.issuance_is_off",
            return_value=True,
        ),
        patch(
            "accounts.services.retirement_733.gateway_retirement",
            return_value=lambda *a, **k: {"selected": 0, "pending": []},
        ),
        patch(
            "accounts.services.retirement_733.unjournaled_connections", return_value=0
        ),
        patch("accounts.services.retirement_733.retire_fa_rows", return_value=0),
        patch("accounts.services.retirement_733.retire_connections", return_value=0),
    ):
        yield


def manifests(configs):
    return [
        RetirementManifest.objects.get(
            service="config", object_id=str(config.pk), action="repush"
        )
        for config in configs
    ]


def test_first_real_placement_refusal_continues_and_logs_only_fields(
    configs, gateway, placement_trigger
):
    with capture_logs() as logs:
        result = repush_configs()

    assert result == (3, 1, {CODE: 1})
    assert [call.args[0] for call in placement_trigger.call_args_list] == [
        str(config.organization_id) for config in configs
    ]
    assert [call.args[0] for call in gateway.set_org_config.call_args_list] == [
        str(config.organization_id) for config in configs[1:]
    ]
    rows = manifests(configs)
    assert [row.confirmed for row in rows] == [False, True, True]
    assert all(not row.superseded_by and row.reverted_at is None for row in rows)
    refusal_logs = [log for log in logs if log["event"] == EVENT]
    assert refusal_logs == [
        {
            "event": EVENT,
            "org_id": str(configs[0].organization_id),
            "code": CODE,
            "param": PARAM,
            "log_level": "warning",
        }
    ]
    assert VALUE not in json.dumps(logs)
    assert VALUE not in repr([row.__dict__ for row in rows])


def test_command_exits_pending_after_all_orgs_and_retries_only_refused(
    configs, gateway, placement_trigger, command_dependencies
):
    output = StringIO()
    with pytest.raises(CommandError, match="propagation is pending"):
        call_command("retire_org_keys_733", stdout=output)
    summary = json.loads(output.getvalue())
    assert summary["configs"] == 3
    assert summary["config_pending"] == 1
    assert summary["config_refused"] == {CODE: 1}
    assert placement_trigger.call_count == 3
    assert gateway.set_org_config.call_count == 2
    assert [row.confirmed for row in manifests(configs)] == [False, True, True]
    assert VALUE not in output.getvalue()
    assert PARAM not in output.getvalue()
    for config in configs:
        assert str(config.organization_id) not in output.getvalue()

    gateway.reset_mock()
    placement_trigger.reset_mock()
    placement_trigger.side_effect = None
    placement_trigger.return_value = {}
    output = StringIO()
    call_command("retire_org_keys_733", stdout=output)
    summary = json.loads(output.getvalue())
    assert summary["config_pending"] == 0
    assert summary["config_refused"] == {}
    placement_trigger.assert_called_once_with(str(configs[0].organization_id))
    gateway.set_org_config.assert_called_once()
    assert gateway.set_org_config.call_args.args[0] == str(configs[0].organization_id)
    assert all(row.confirmed and not row.superseded_by for row in manifests(configs))
    assert RetirementManifest.objects.filter(service="config").count() == 3


def test_dry_run_counts_without_pushing_journaling_or_refusal(
    configs, gateway, placement_trigger, command_dependencies
):
    before = list(AgentccOrgConfig._base_manager.values())
    with capture_logs() as logs:
        output = StringIO()
        call_command("retire_org_keys_733", "--dry-run", stdout=output)
    summary = json.loads(output.getvalue())
    assert summary["configs"] == 3
    assert summary["config_pending"] == 0
    assert summary["config_refused"] == {}
    gateway.set_org_config.assert_not_called()
    placement_trigger.assert_not_called()
    assert not RetirementManifest.objects.exists()
    assert list(AgentccOrgConfig._base_manager.values()) == before
    assert not [log for log in logs if log["event"] == EVENT]


def test_refused_push_rolls_back_inner_writes_but_keeps_journal(configs):
    def push(org_id, config):
        if config.pk == configs[0].pk:
            AgentccOrgConfig._base_manager.filter(pk=config.pk).update(
                change_description="must roll back"
            )
            raise MCPCredentialPlacementRefused(PARAM, VALUE)
        return True

    with (
        patch("agentcc.services.config_push.push_org_config", side_effect=push),
        capture_logs() as logs,
    ):
        assert repush_configs() == (3, 1, {CODE: 1})
    configs[0].refresh_from_db()
    assert configs[0].change_description == ""
    assert [row.confirmed for row in manifests(configs)] == [False, True, True]
    # Even a future exception message containing material must never be logged.
    assert VALUE not in json.dumps(logs)


@pytest.mark.parametrize(
    "error_type", [RuntimeError, OrgConfigRejected], ids=["runtime", "other-rejection"]
)
def test_unrelated_exception_still_aborts_and_rolls_back(configs, error_type):
    with patch(
        "agentcc.services.config_push.push_org_config",
        side_effect=error_type("fixture failure"),
    ) as push:
        with pytest.raises(error_type, match="fixture failure"):
            repush_configs()
    push.assert_called_once()
    assert not RetirementManifest.objects.exists()


def test_false_push_is_pending_but_not_a_typed_refusal(configs):
    with patch(
        "agentcc.services.config_push.push_org_config", side_effect=[False, True, True]
    ):
        assert repush_configs() == (3, 1, {})
    assert [row.confirmed for row in manifests(configs)] == [False, True, True]


def test_confirmed_retry_preserves_change_fence_and_reverted_repush(configs):
    with patch(
        "agentcc.services.config_push.push_org_config", return_value=True
    ) as push:
        assert repush_configs() == (3, 0, {})
        rows = manifests(configs)
        AgentccOrgConfig._base_manager.filter(pk=configs[0].pk).update(version=4)
        rows[1].reverted_at = timezone.now()
        rows[1].save(update_fields=["reverted_at"])
        push.reset_mock()
        assert repush_configs() == (3, 0, {})
        push.assert_called_once()
        assert push.call_args.args[0] == str(configs[1].organization_id)
    rows = manifests(configs)
    assert rows[0].superseded_by == "config_changed"
    assert rows[1].confirmed and rows[1].reverted_at is None
    assert rows[2].confirmed and not rows[2].superseded_by
