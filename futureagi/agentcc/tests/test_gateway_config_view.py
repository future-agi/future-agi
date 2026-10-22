"""Settings shows what the gateway reports.

The config action passes the gateway's server, logging and batch blocks and
its org_sections_enforced list through unchanged; every health check records
when it ran (and what it found), and list, retrieve and config return it. A
health check stays open to every member; only its side effect, re-pushing
every org's config to the gateway, needs an Owner or Admin of the org.
"""

import copy
import datetime
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services.gateway_client import GatewayClientError
from agentcc.tests.console_support import (
    GATEWAY,
    NODE_GATEWAY_CONFIG,
    ClientFactory,
    FakeGateway,
    owner_of_new_org,
    user_with_level,
)
from tfc.constants.levels import Level

# The health check's fleet re-push runs at most once per 5 minutes.
REPUSH_THROTTLE_KEY = "agentcc:gateway_config_synced"


@pytest.fixture(autouse=True)
def _no_repush_throttle(organization):
    cache.delete(REPUSH_THROTTLE_KEY)
    yield
    cache.delete(REPUSH_THROTTLE_KEY)


@pytest.fixture
def clients():
    factory = ClientFactory()
    yield factory
    factory.close()


@pytest.fixture
def client_with_level(organization, workspace, clients):
    def build(level):
        user, ws = user_with_level(organization, level, workspace)
        return clients(user, ws)

    return build


@pytest.fixture
def gateway():
    fake = FakeGateway(config=copy.deepcopy(NODE_GATEWAY_CONFIG))
    with patch("agentcc.views.gateway.get_gateway_client", return_value=fake):
        yield fake


@pytest.fixture
def repush():
    with patch("agentcc.views.gateway.push_all_org_configs") as push:
        yield push


def _result(response):
    assert response.status_code == 200, response.content
    return response.json()["result"]


# --- config: the gateway's blocks, passed through ------------------------------


@pytest.mark.integration
def test_config_passes_the_gateway_blocks_through_unchanged(
    organization, auth_client, gateway
):
    # An org that has an org config.
    AgentccOrgConfig.no_workspace_objects.create(
        organization=organization,
        version=1,
        is_active=True,
        budgets={"org_limit": {"limit": 5, "on_exceed": "warn"}},
    )

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert "get_config" in gateway.calls
    for block in ("server", "logging", "batch", "org_sections_enforced"):
        assert result[block] == NODE_GATEWAY_CONFIG[block], block
    # The org's own sections and providers stay the org's.
    assert result["budgets"] == {"org_limit": {"limit": 5, "on_exceed": "warn"}}
    assert isinstance(result["providers"], dict)
    assert result["gateway"]["status"] == "healthy"
    # Only those four blocks come from /-/config.
    for key in ("providers_count", "models_count", "plugins_count"):
        assert key not in result


@pytest.mark.integration
def test_config_without_an_org_config_still_carries_the_blocks(auth_client, gateway):
    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert result["logging"]["request_logging"] is True
    assert result["server"]["read_timeout_ms"] == 60000
    assert result["batch"]["max_body_bytes"] == 10485760
    assert result["org_sections_enforced"] == [
        "alerting",
        "budgets",
        "ip_acl",
        "tool_policy",
    ]


@pytest.mark.integration
def test_config_leaves_out_what_an_older_gateway_does_not_report(auth_client, gateway):
    # A gateway that reports only host and port.
    gateway.config = {"server": {"host": "0.0.0.0", "port": 8080}, "plugins_count": 1}

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert result["server"] == {"host": "0.0.0.0", "port": 8080}
    # Absent stays absent: the console says "not reported", never a default.
    for key in ("logging", "batch", "org_sections_enforced"):
        assert key not in result


@pytest.mark.integration
def test_config_keeps_an_empty_enforced_list(auth_client, gateway):
    # A node that enforces no org section reports [], which is not "unknown".
    gateway.config["org_sections_enforced"] = []

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert result["org_sections_enforced"] == []


@pytest.mark.integration
def test_config_when_the_gateway_config_read_fails(auth_client, gateway):
    gateway.config_error = GatewayClientError("Gateway returned 401", status_code=401)

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert result["gateway"]["status"] == "healthy"
    for key in ("server", "logging", "batch", "org_sections_enforced"):
        assert key not in result


@pytest.mark.integration
def test_config_when_the_gateway_is_unreachable(auth_client, gateway):
    gateway.health_error = GatewayClientError("Connection refused")

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    assert result["gateway"]["status"] == "unreachable"
    assert "get_config" not in gateway.calls
    for key in ("server", "logging", "batch", "org_sections_enforced"):
        assert key not in result


@pytest.mark.integration
def test_config_ignores_an_answer_that_is_not_the_contract(auth_client, gateway):
    gateway.config = {
        "server": "0.0.0.0:8080",
        "logging": None,
        "batch": ["memory"],
        "org_sections_enforced": "budgets",
    }

    result = _result(auth_client.get(f"{GATEWAY}/config/"))

    for key in ("server", "logging", "batch", "org_sections_enforced"):
        assert key not in result


# --- health check: its time recorded, its re-push only for admins ----------------


@pytest.mark.integration
def test_health_check_records_its_time(auth_client, gateway, repush):
    before = timezone.now()

    checked = _result(auth_client.post(f"{GATEWAY}/health_check/", {}, format="json"))

    assert checked["status"] == "healthy"
    stamp = datetime.datetime.fromisoformat(checked["last_health_check"])
    assert before - datetime.timedelta(seconds=1) <= stamp <= timezone.now()
    assert checked["last_health_check_status"] == "healthy"

    listed = _result(auth_client.get("/agentcc/gateways/"))[0]
    one = _result(auth_client.get(f"{GATEWAY}/"))
    config = _result(auth_client.get(f"{GATEWAY}/config/"))
    for body in (listed, one, config):
        assert body["last_health_check"] == checked["last_health_check"]
        assert body["last_health_check_status"] == "healthy"


@pytest.mark.integration
def test_no_check_yet_reports_no_time(auth_client, gateway):
    listed = _result(auth_client.get("/agentcc/gateways/"))[0]
    config = _result(auth_client.get(f"{GATEWAY}/config/"))

    for body in (listed, config):
        assert body.get("last_health_check") is None


@pytest.mark.integration
def test_a_failed_check_records_its_time_and_outcome(auth_client, gateway, repush):
    gateway.health_error = GatewayClientError("Connection refused")

    failed = auth_client.post(f"{GATEWAY}/health_check/", {}, format="json")

    assert failed.status_code == 400
    assert failed.json()["details"]["status"] == ["unreachable"]
    gateway.health_error = None
    listed = _result(auth_client.get("/agentcc/gateways/"))[0]
    assert listed["last_health_check"]
    assert listed["last_health_check_status"] == "unreachable"
    repush.assert_not_called()


@pytest.mark.integration
def test_member_check_health_pushes_no_org_config(client_with_level, gateway, repush):
    member = client_with_level(Level.MEMBER)

    checked = _result(member.post(f"{GATEWAY}/health_check/", {}, format="json"))

    assert checked["status"] == "healthy"
    assert checked["last_health_check"]
    assert checked["org_configs_pushed"] is False
    repush.assert_not_called()
    # The member's click leaves the throttle unset: an admin's next check
    # still re-pushes.
    assert cache.get(REPUSH_THROTTLE_KEY) is None


@pytest.mark.integration
def test_viewer_check_health_works_and_pushes_nothing(
    client_with_level, gateway, repush
):
    viewer = client_with_level(Level.VIEWER)

    checked = _result(viewer.post(f"{GATEWAY}/health_check/", {}, format="json"))

    assert checked["status"] == "healthy"
    assert checked["org_configs_pushed"] is False
    repush.assert_not_called()


@pytest.mark.integration
def test_admin_check_health_repushes_once_per_window(
    client_with_level, gateway, repush
):
    member = client_with_level(Level.MEMBER)
    admin = client_with_level(Level.ADMIN)

    _result(member.post(f"{GATEWAY}/health_check/", {}, format="json"))
    first = _result(admin.post(f"{GATEWAY}/health_check/", {}, format="json"))
    second = _result(admin.post(f"{GATEWAY}/health_check/", {}, format="json"))

    assert repush.call_count == 1
    assert first["org_configs_pushed"] is True
    assert second["org_configs_pushed"] is False


@pytest.mark.integration
def test_owner_check_health_repushes(auth_client, gateway, repush):
    checked = _result(auth_client.post(f"{GATEWAY}/health_check/", {}, format="json"))

    repush.assert_called_once_with()
    assert checked["org_configs_pushed"] is True


@pytest.mark.integration
def test_last_health_check_is_kept_per_org(auth_client, gateway, repush, clients):
    _, other_owner, other_ws = owner_of_new_org("Other Org")
    other = clients(other_owner, other_ws)

    _result(auth_client.post(f"{GATEWAY}/health_check/", {}, format="json"))
    listed = _result(other.get("/agentcc/gateways/"))[0]

    assert listed.get("last_health_check") is None
