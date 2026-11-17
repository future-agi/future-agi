"""A guardrails save made on stored MCP guardrails that changed since they
were read is refused, not written over the other save.

update-mcp-guardrails replaces mcp.guardrails whole, so a save made on the
settings one admin opened would silently overwrite a save another admin made
since. The save names the stored guardrails it was made on (``base``, as the
config read returned them; omitted or null when nothing was stored). Under the
org lock the write holds, the server compares that by value with what is
stored now: when they differ it answers 409 mcp_guardrails_changed and writes
and pushes nothing, so the other save is kept. A save made on the current
settings, and an organization's first save, are written as before.

Nothing here reaches a network host: the config read's gateway client and the
push are fakes.
"""

import copy
import json
from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.tests.r7_console_a_support import (
    GATEWAY,
    K8_GATEWAY_CONFIG,
    ClientFactory,
    FakeGateway,
    user_with_level,
)
from tfc.constants.levels import Level

SAVE = f"{GATEWAY}/update-mcp-guardrails/"
CHANGED = "mcp_guardrails_changed"
MESSAGE = (
    "These MCP guardrail settings changed after you opened them, so nothing "
    "was saved. Reload them and save again."
)

# What admin A opens, what admin B saves since, and what A then saves.
OPENED = {
    "enabled": True,
    "blocked_tools": ["shell"],
    "tool_rate_limits": {"crm__search": 10},
    "validate_inputs": True,
}
THEIRS = {**OPENED, "blocked_tools": ["shell", "browser-b"]}
MINE = {**OPENED, "enabled": False, "blocked_tools": ["shell", "write-a"]}


@pytest.fixture
def push():
    with (
        patch(
            "agentcc.views.gateway.get_gateway_client",
            return_value=FakeGateway(config=copy.deepcopy(K8_GATEWAY_CONFIG)),
        ),
        patch("agentcc.views.gateway.push_org_config", return_value=True) as push,
    ):
        yield push


@pytest.fixture
def admin_b(user, organization):
    """A second admin of the same organization (``auth_client`` is A)."""
    clients = ClientFactory()
    other, workspace = user_with_level(organization, Level.ADMIN)
    yield clients(other, workspace)
    clients.close()


def _seed(org, mcp):
    AgentccOrgConfig.no_workspace_objects.create(
        organization=org, version=1, is_active=True, mcp=copy.deepcopy(mcp)
    )


def _read(client):
    """The stored MCP guardrails as the console reads them (GET config)."""
    response = client.get(f"{GATEWAY}/config/")
    assert response.status_code == 200, response.json()
    return (response.json()["result"].get("mcp") or {}).get("guardrails")


def _save(client, config, **base):
    """A save as the console sends it: ``base`` only when one is given."""
    return client.post(SAVE, {"config": config, **base}, format="json")


def _stored(org):
    active = AgentccOrgConfig.no_workspace_objects.get(
        organization=org, is_active=True, deleted=False
    )
    return (active.mcp or {}).get("guardrails")


def _versions(org):
    return AgentccOrgConfig.no_workspace_objects.filter(organization=org).count()


def _assert_refused(response):
    body = response.json()
    assert response.status_code == 409, body
    assert body["status"] is False
    assert body["code"] == CHANGED
    assert body["message"] == MESSAGE
    # Neither save's settings come back in the refusal.
    text = json.dumps(body)
    assert "browser-b" not in text
    assert "write-a" not in text


@pytest.mark.integration
@pytest.mark.api
class TestStaleGuardrailsSave:
    def test_the_second_of_two_first_saves_is_refused_not_written_over_the_first(
        self, auth_client, admin_b, user, push
    ):
        """Nothing is stored when both admins open the settings, so neither
        save names a base (the console leaves it out). B saves first; A's
        save, made on nothing stored, must not overwrite B's."""
        org = user.organization
        _seed(org, {})
        assert _read(auth_client) is None
        assert _read(admin_b) is None

        theirs = _save(admin_b, THEIRS)
        assert theirs.status_code == 200, theirs.json()
        pushed = push.call_count

        _assert_refused(_save(auth_client, MINE))
        assert _stored(org) == THEIRS
        assert _versions(org) == 2
        assert push.call_count == pushed

    @pytest.mark.parametrize("path", ["update-mcp-guardrails", "update-config"])
    def test_a_save_made_on_settings_another_admin_changed_is_refused(
        self, auth_client, admin_b, user, push, path
    ):
        org = user.organization
        _seed(org, {"guardrails": OPENED})
        opened = _read(auth_client)
        assert opened == OPENED

        if path == "update-config":
            # The other way an admin changes them: a merge patch of mcp.
            theirs = admin_b.post(
                f"{GATEWAY}/update-config/",
                {"mcp": {"guardrails": {"blocked_tools": THEIRS["blocked_tools"]}}},
                format="json",
            )
        else:
            theirs = _save(admin_b, THEIRS, base=_read(admin_b))
        assert theirs.status_code == 200, theirs.json()
        assert _stored(org) == THEIRS
        pushed = push.call_count

        _assert_refused(_save(auth_client, MINE, base=opened))
        assert _stored(org) == THEIRS
        assert _versions(org) == 2
        assert push.call_count == pushed

    @pytest.mark.parametrize("base", [{}, {"base": None}], ids=["no-base", "null-base"])
    def test_a_save_that_names_no_base_over_stored_settings_is_refused(
        self, auth_client, user, push, base
    ):
        """No base, or null, means the save was made on nothing stored (an
        older page's save sends none). Over stored settings it is refused
        like any other stale save."""
        org = user.organization
        _seed(org, {"guardrails": OPENED})

        _assert_refused(_save(auth_client, MINE, **base))
        assert _stored(org) == OPENED
        assert _versions(org) == 1
        push.assert_not_called()

    def test_a_save_made_on_the_current_settings_is_written_as_today(
        self, auth_client, user, push
    ):
        org = user.organization
        _seed(org, {"guardrails": OPENED})

        response = _save(auth_client, MINE, base=_read(auth_client))
        assert response.status_code == 200, response.json()
        assert response.json()["result"] == {
            "action": "updated",
            "version": 2,
            "gateway_synced": True,
        }
        assert _stored(org) == MINE
        push.assert_called_once()

    @pytest.mark.parametrize(
        "base",
        [{}, {"base": None}, {"base": {}}],
        ids=["no-base", "null-base", "empty-base"],
    )
    @pytest.mark.parametrize(
        "mcp",
        [None, {}, {"guardrails": {}}],
        ids=["no-config", "nothing-stored", "empty-stored"],
    )
    def test_an_organizations_first_save_is_written(
        self, auth_client, user, push, mcp, base
    ):
        org = user.organization
        if mcp is not None:
            _seed(org, mcp)

        response = _save(auth_client, MINE, **base)
        assert response.status_code == 200, response.json()
        assert _stored(org) == MINE
        assert _versions(org) == (1 if mcp is None else 2)
        push.assert_called_once()
