"""A connector's server joins only its own virtual server, and its catalog
record changes only from Connectors.

A server with a catalog_ref (the first spelling present, even when null; null
or "" is no record) is a connector's. It may be a member, bare or as
``<id>@<account>``, only of ``mcp.virtual_servers.<its id>``: another virtual
server would run its tools without the connector's approvals and denied
tools. Every config write and every activation refuses a write that adds such
a member elsewhere (a new pair, or a listed server that gains a record), and
one that changes or drops a stored record or deletes the server, the section
emptied included. Only the connector remove and remove-mcp-server delete one.
A pair stored before stays: an unrelated save beside it passes.

Every shape is answered against one stored active version: the connector
acme-docs with its own virtual server, the plain servers wiki and notes, and
"team" listing wiki. On a refusal nothing is saved, pushed, activated or
revoked.
"""

import copy
from unittest.mock import patch

import pytest

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers import org_config as org_config_rules
from agentcc.services import org_config_writer

UPDATE_CONFIG = "/agentcc/gateways/default/update-config/"
UPDATE_SERVER = "/agentcc/gateways/default/update-mcp-server/"
REMOVE_SERVER = "/agentcc/gateways/default/remove-mcp-server/"
ORG_CONFIGS = "/agentcc/org-configs/"
SAVE_PUSH = "agentcc.views.org_config.AgentccOrgConfigViewSet._push_config_to_gateway"
GATEWAY_PUSH = "agentcc.views.gateway.push_org_config"
REVOKE = "agentcc.services.org_config_writer._revoke_server_clients_left_behind"

MEMBERSHIP = "connector_server_not_own_virtual_server"
LOCKED = "connector_catalog_ref_locked"

ACME_URL = "https://mcp.acme-docs.example.com/mcp"
WIKI_URL = "https://wiki.example.com/mcp"
NOTES_URL = "https://notes.example.com/mcp"
ACME_REF = "acme-docs/acme-mcp@1.2.0"
WIKI_REF = "wiki-app/wiki-mcp@1.0.0"
NOTES_REF = "notes-app/notes-mcp@2.0.0"
ACME_VS = "vs_01JBCDEFGHJKMNPQRSTVWXYZ01"
TEAM_VS = "vs_01JBCDEFGHJKMNPQRSTVWXYZ02"


def _own_only(server):
    return (
        f"{server} is a connector, so only its own virtual server ({server}) can "
        "include it. Its approvals and denied tools apply only there."
    )


def _record_locked(server):
    return (
        f"{server} is a connector, so its catalog record cannot be changed or "
        "removed here. Remove the connector from Connectors instead."
    )


def _members_refused(slug, server):
    return (MEMBERSHIP, f"mcp.virtual_servers.{slug}.members", _own_only(server))


def _ref_locked(server):
    return (LOCKED, f"mcp.servers.{server}.catalog_ref", _record_locked(server))


def _stored_mcp(team=("wiki",)):
    return {
        "enabled": True,
        "servers": {
            "acme-docs": {
                "url": ACME_URL,
                "transport": "http",
                "catalog_ref": ACME_REF,
            },
            "wiki": {"url": WIKI_URL, "transport": "http"},
            "notes": {"url": NOTES_URL, "transport": "http"},
        },
        "virtual_servers": {
            "acme-docs": {
                "id": ACME_VS,
                "title": "Acme Docs",
                "members": ["acme-docs"],
            },
            "team": {"id": TEAM_VS, "title": "Team", "members": list(team)},
        },
    }


def _seed(organization, team=("wiki",)):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=organization, version=1, is_active=True, mcp=_stored_mcp(team)
    )


def _active(organization):
    return AgentccOrgConfig.no_workspace_objects.get(
        organization=organization, is_active=True, deleted=False
    )


def _versions(organization):
    return AgentccOrgConfig._base_manager.filter(organization=organization).count()


# --- The shapes ----------------------------------------------------------------


def _team_adds(member):
    def edit(mcp):
        mcp["virtual_servers"]["team"]["members"].append(member)

    return edit


def _own_adds(member):
    def edit(mcp):
        mcp["virtual_servers"]["acme-docs"]["members"].append(member)

    return edit


def _record(server, value):
    def edit(mcp):
        mcp["servers"][server]["catalog_ref"] = value

    return edit


def _null_record_before_the_other_spelling(mcp):
    mcp["servers"]["acme-docs"]["catalog_ref"] = None
    mcp["servers"]["acme-docs"]["catalogRef"] = ACME_REF


def _record_dropped(mcp):
    del mcp["servers"]["acme-docs"]["catalog_ref"]


def _record_under_the_other_spelling(mcp):
    del mcp["servers"]["acme-docs"]["catalog_ref"]
    mcp["servers"]["acme-docs"]["catalogRef"] = ACME_REF


def _server_deleted(mcp):
    del mcp["servers"]["acme-docs"]


def _servers_deleted(mcp):
    del mcp["servers"]


def _section_emptied(mcp):
    mcp.clear()


def _team_retitled(mcp):
    mcp["virtual_servers"]["team"]["title"] = "Team (EU)"


# Each shape as a whole mcp section (POST org-configs and activation): the
# stored "team", the edit, and the answer (code, param, message), or None
# for a save that passes.
SHAPES = {
    "another-virtual-server-adds-the-connector": (
        ("wiki",),
        _team_adds("acme-docs"),
        _members_refused("team", "acme-docs"),
    ),
    "another-virtual-server-adds-an-account-of-it": (
        ("wiki",),
        _team_adds("acme-docs@work"),
        _members_refused("team", "acme-docs"),
    ),
    "a-listed-server-gains-a-catalog-record": (
        ("wiki",),
        _record("wiki", WIKI_REF),
        _members_refused("team", "wiki"),
    ),
    "the-record-dropped": (("wiki",), _record_dropped, _ref_locked("acme-docs")),
    "a-null-record-before-the-same-one-under-the-other-spelling": (
        ("wiki",),
        _null_record_before_the_other_spelling,
        _ref_locked("acme-docs"),
    ),
    "the-record-changed": (
        ("wiki",),
        _record("acme-docs", "acme-docs/acme-mcp@9.0.0"),
        _ref_locked("acme-docs"),
    ),
    "the-server-deleted": (("wiki",), _server_deleted, _ref_locked("acme-docs")),
    "every-server-deleted": (("wiki",), _servers_deleted, _ref_locked("acme-docs")),
    "the-section-emptied": (("wiki",), _section_emptied, _ref_locked("acme-docs")),
    "the-same-record-under-the-other-spelling": (
        ("wiki",),
        _record_under_the_other_spelling,
        None,
    ),
    "a-record-added-to-a-server-no-other-virtual-server-lists": (
        ("wiki",),
        _record("notes", NOTES_REF),
        None,
    ),
    "an-unrelated-change-beside-a-stored-foreign-member": (
        ("wiki", "acme-docs"),
        _team_retitled,
        None,
    ),
    "the-own-virtual-server-adds-an-account": (
        ("wiki",),
        _own_adds("acme-docs@work"),
        None,
    ),
}

# The same shapes as update-config merge patches. A null deletes a key there,
# so the null-first record arrives as the same record under the other
# spelling, which passes.
PATCHES = {
    "another-virtual-server-adds-the-connector": (
        ("wiki",),
        {"virtual_servers": {"team": {"members": ["wiki", "acme-docs"]}}},
        _members_refused("team", "acme-docs"),
    ),
    "another-virtual-server-adds-an-account-of-it": (
        ("wiki",),
        {"virtual_servers": {"team": {"members": ["wiki", "acme-docs@work"]}}},
        _members_refused("team", "acme-docs"),
    ),
    "a-listed-server-gains-a-catalog-record": (
        ("wiki",),
        {"servers": {"wiki": {"catalog_ref": WIKI_REF}}},
        _members_refused("team", "wiki"),
    ),
    "the-record-dropped": (
        ("wiki",),
        {"servers": {"acme-docs": {"catalog_ref": None}}},
        _ref_locked("acme-docs"),
    ),
    "the-record-changed": (
        ("wiki",),
        {"servers": {"acme-docs": {"catalog_ref": "acme-docs/acme-mcp@9.0.0"}}},
        _ref_locked("acme-docs"),
    ),
    "the-server-deleted": (
        ("wiki",),
        {"servers": {"acme-docs": None}},
        _ref_locked("acme-docs"),
    ),
    "every-server-deleted": (
        ("wiki",),
        {"servers": None},
        _ref_locked("acme-docs"),
    ),
    "every-child-nulled": (
        ("wiki",),
        {"enabled": None, "servers": None, "virtual_servers": None},
        _ref_locked("acme-docs"),
    ),
    "a-null-record-beside-the-same-one-under-the-other-spelling": (
        ("wiki",),
        {"servers": {"acme-docs": {"catalog_ref": None, "catalogRef": ACME_REF}}},
        None,
    ),
    "a-record-added-to-a-server-no-other-virtual-server-lists": (
        ("wiki",),
        {"servers": {"notes": {"catalog_ref": NOTES_REF}}},
        None,
    ),
    "an-unrelated-change-beside-a-stored-foreign-member": (
        ("wiki", "acme-docs"),
        {"virtual_servers": {"team": {"title": "Team (EU)"}}},
        None,
    ),
    "the-own-virtual-server-adds-an-account": (
        ("wiki",),
        {
            "virtual_servers": {
                "acme-docs": {"members": ["acme-docs", "acme-docs@work"]}
            }
        },
        None,
    ),
}


def _shaped(team, edit):
    mcp = _stored_mcp(team)
    edit(mcp)
    return mcp


@pytest.fixture(autouse=True)
def _switches_on(settings):
    """The catalog, the credential vault and virtual servers are on, so only
    the rules under test answer."""
    settings.AGENTCC_MCP_CATALOG_ENABLED = True
    settings.AGENTCC_MCP_CREDENTIAL_VAULT_ENABLED = True
    settings.AGENTCC_MCP_VIRTUAL_SERVERS_ENABLED = True


@pytest.fixture
def pushes():
    """The two push seams the config writes here go through."""
    with (
        patch(SAVE_PUSH, return_value=True) as saved,
        patch(GATEWAY_PUSH, return_value=True) as gateway,
    ):
        yield saved, gateway


@pytest.fixture
def revokes():
    """The writer's revocation of what a write leaves behind, still run."""
    with patch(
        REVOKE, wraps=org_config_writer._revoke_server_clients_left_behind
    ) as spy:
        yield spy


def _assert_refused(response, refused, organization, stored, pushes, revokes):
    body = response.json()
    assert response.status_code == 400, body
    assert (body["code"], body.get("param"), body["message"]) == refused
    assert _versions(organization) == 1
    assert _active(organization).pk == stored.pk
    assert _active(organization).mcp == stored.mcp
    assert not any(push.called for push in pushes)
    revokes.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestEveryWritePathAnswersTheSame:
    @pytest.mark.parametrize("name", PATCHES)
    def test_update_config(self, auth_client, organization, pushes, revokes, name):
        team, mcp_patch, refused = PATCHES[name]
        stored = _seed(organization, team)

        response = auth_client.post(UPDATE_CONFIG, {"mcp": mcp_patch}, format="json")

        if refused is None:
            assert response.status_code == 200, response.json()
            assert _versions(organization) == 2
        else:
            _assert_refused(response, refused, organization, stored, pushes, revokes)

    @pytest.mark.parametrize("name", SHAPES)
    def test_post_org_configs(self, auth_client, organization, pushes, revokes, name):
        team, edit, refused = SHAPES[name]
        stored = _seed(organization, team)

        response = auth_client.post(
            ORG_CONFIGS, {"mcp": _shaped(team, edit)}, format="json"
        )

        if refused is None:
            assert response.status_code == 200, response.json()
            assert _versions(organization) == 2
        else:
            _assert_refused(response, refused, organization, stored, pushes, revokes)

    @pytest.mark.parametrize("cleared", [None, {}])
    def test_post_org_configs_clearing_the_section(
        self, auth_client, organization, pushes, revokes, cleared
    ):
        stored = _seed(organization)

        response = auth_client.post(ORG_CONFIGS, {"mcp": cleared}, format="json")

        _assert_refused(
            response,
            _ref_locked("acme-docs"),
            organization,
            stored,
            pushes,
            revokes,
        )

    @pytest.mark.parametrize("name", SHAPES)
    def test_activation_answers_as_the_save_does(
        self, auth_client, organization, pushes, revokes, name
    ):
        """A version stored before these rules is activated with the answer
        POST org-configs gives the same section."""
        team, edit, refused = SHAPES[name]
        stored = _seed(organization, team)
        version = AgentccOrgConfig.no_workspace_objects.create(
            organization=organization,
            version=2,
            is_active=False,
            mcp=_shaped(team, edit),
        )

        response = auth_client.post(
            f"{ORG_CONFIGS}{version.pk}/activate/", {}, format="json"
        )

        if refused is None:
            assert response.status_code == 200, response.json()
            assert _active(organization).pk == version.pk
            return
        body = response.json()
        assert response.status_code == 400, body
        assert (body["code"], body.get("param"), body["message"]) == refused
        assert _active(organization).pk == stored.pk
        version.refresh_from_db()
        assert not version.is_active
        assert not any(push.called for push in pushes)
        revokes.assert_not_called()

    @pytest.mark.parametrize(
        "server_id, config, refused",
        [
            (
                "wiki",
                {"url": WIKI_URL, "transport": "http", "catalog_ref": WIKI_REF},
                _members_refused("team", "wiki"),
            ),
            (
                "acme-docs",
                {"url": ACME_URL, "transport": "http"},
                _ref_locked("acme-docs"),
            ),
            (
                "acme-docs",
                {
                    "url": ACME_URL,
                    "transport": "http",
                    "catalog_ref": "acme-docs/acme-mcp@9.0.0",
                },
                _ref_locked("acme-docs"),
            ),
        ],
        ids=[
            "a-listed-server-gains-a-catalog-record",
            "the-record-dropped",
            "the-record-changed",
        ],
    )
    def test_update_mcp_server(
        self, auth_client, organization, pushes, revokes, server_id, config, refused
    ):
        stored = _seed(organization)

        response = auth_client.post(
            UPDATE_SERVER, {"server_id": server_id, "config": config}, format="json"
        )

        _assert_refused(response, refused, organization, stored, pushes, revokes)

    def test_update_mcp_server_sending_the_record_unchanged_saves(
        self, auth_client, organization, pushes
    ):
        _seed(organization)

        response = auth_client.post(
            UPDATE_SERVER,
            {
                "server_id": "acme-docs",
                "config": {
                    "url": ACME_URL,
                    "transport": "http",
                    "timeout_ms": 15000,
                    "catalog_ref": ACME_REF,
                },
            },
            format="json",
        )

        assert response.status_code == 200, response.json()
        server = _active(organization).mcp["servers"]["acme-docs"]
        assert (server["catalog_ref"], server["timeout_ms"]) == (ACME_REF, 15000)

    def test_update_config_with_a_null_section_is_refused_by_its_contract(
        self, auth_client, organization, pushes
    ):
        stored = _seed(organization)

        response = auth_client.post(UPDATE_CONFIG, {"mcp": None}, format="json")

        assert response.status_code == 400, response.json()
        assert _versions(organization) == 1
        assert _active(organization).mcp == stored.mcp

    def test_dropping_the_record_to_add_it_elsewhere_fails_at_the_first_write(
        self, auth_client, organization, pushes, revokes
    ):
        stored = _seed(organization)

        untagged = auth_client.post(
            UPDATE_CONFIG,
            {"mcp": {"servers": {"acme-docs": {"catalog_ref": None}}}},
            format="json",
        )
        _assert_refused(
            untagged,
            _ref_locked("acme-docs"),
            organization,
            stored,
            pushes,
            revokes,
        )

        team_adds = PATCHES["another-virtual-server-adds-the-connector"][1]
        added = auth_client.post(UPDATE_CONFIG, {"mcp": team_adds}, format="json")
        _assert_refused(
            added,
            _members_refused("team", "acme-docs"),
            organization,
            stored,
            pushes,
            revokes,
        )


@pytest.mark.integration
@pytest.mark.api
class TestOnlyTheRemoversDeleteAConnectorServer:
    def test_remove_mcp_server_deletes_it_and_resets_its_permission(
        self, auth_client, organization, pushes
    ):
        _seed(organization, ("wiki", "acme-docs"))

        removed = auth_client.post(
            REMOVE_SERVER, {"server_id": "acme-docs"}, format="json"
        )

        assert removed.status_code == 200, removed.json()
        mcp = _active(organization).mcp
        assert "acme-docs" not in mcp["servers"]
        assert mcp["virtual_servers"]["team"]["members"] == ["wiki"]
        assert org_config_rules._REMOVED_SERVER_IDS.get() is None

    def test_a_remove_of_no_such_server_resets_its_permission(
        self, auth_client, organization, pushes
    ):
        stored = _seed(organization)

        missing = auth_client.post(
            REMOVE_SERVER, {"server_id": "missing"}, format="json"
        )

        assert missing.status_code == 404, missing.json()
        assert _active(organization).mcp == stored.mcp
        assert org_config_rules._REMOVED_SERVER_IDS.get() is None

    def test_a_write_outside_the_removers_cannot_name_a_server_removed(self):
        with pytest.raises(RuntimeError):
            org_config_rules.allow_server_removal("acme-docs")
        assert org_config_rules._REMOVED_SERVER_IDS.get() is None

    def test_a_write_a_remover_refuses_for_another_reason_resets_its_permission(
        self, organization
    ):
        _seed(organization)

        def updater(config):
            org_config_rules.allow_server_removal("acme-docs")
            mcp = copy.deepcopy(config.mcp)
            del mcp["servers"]["acme-docs"]
            mcp["servers"]["wiki"]["catalog_ref"] = WIKI_REF
            config.mcp = mcp

        with (
            pytest.raises(org_config_writer.MCPConfigRejected) as refused,
            org_config_rules.connector_server_removal(),
        ):
            org_config_writer.write_config_version_with(
                organization, updater, push=lambda _: True
            )

        assert refused.value.code == MEMBERSHIP
        assert refused.value.param == "mcp.virtual_servers.team.members"
        assert org_config_rules._REMOVED_SERVER_IDS.get() is None
        assert _versions(organization) == 1
