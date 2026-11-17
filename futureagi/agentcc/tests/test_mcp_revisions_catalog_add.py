"""An app added from the connector catalog is pinned to the tools it was added
with (CN-ADD + M20/M21): the live listing taken at add is the baseline.

Runs the real add path (the catalog view, the add service, the org config
writer and the push payload) on test_connector_catalog's fixture bundle and
faked probe, imported from there so both files add apps the same way.
"""

import pytest

from agentcc.tests.test_connector_catalog import (  # noqa: F401 (fixtures)
    _active,
    _add,
    _key,
    _ok,
    _versions,
    bundle,
    drift_free,
    probe,
    probe_budget,
    pushes,
)

ACME_HOST = "mcp.acme-docs.example.com"


def _with_revisions(request):
    """The acme listing as a gateway with revisions reports it: each tool
    with its own revision."""
    listed = request.getfixturevalue("probe").listings[ACME_HOST]
    for i, tool in enumerate(listed):
        tool["revision"] = f"{i + 1:064x}"
    return listed


def _add_acme(client):
    return _ok(
        client.post(
            _add("acme-docs"),
            {"provider": "acme-mcp", "mode": "agent", **_key()},
            format="json",
        ),
        201,
    )["result"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.usefixtures("drift_free", "bundle", "probe", "pushes")
class TestCatalogAddPinsItsListing:
    def test_add_pins_each_tool_to_the_revision_it_was_listed_at(
        self, auth_client, user, request
    ):
        org = user.organization
        listed = _with_revisions(request)
        before = _versions(org)

        result = _add_acme(auth_client)

        expected = {f"acme-docs/{tool['name']}": tool["revision"] for tool in listed}
        active = _active(org)
        # In the same version as the server: from the moment the gateway has
        # the server, it holds any tool that differs from this listing.
        assert _versions(org) == before + 1 and result["version"] == active.version
        assert active.mcp["pinning"]["pins"] == expected
        sent = request.getfixturevalue("pushes")
        assert sent[-1][1]["mcp"]["pinning"]["pins"] == expected

    def test_readd_replaces_the_pins_a_removed_server_left(
        self, auth_client, user, request
    ):
        from agentcc.models.org_config import AgentccOrgConfig

        org = user.organization
        AgentccOrgConfig.no_workspace_objects.create(
            organization=org,
            version=1,
            is_active=True,
            mcp={
                "pinning": {
                    "pins": {
                        "acme-docs/search_docs": "f" * 64,
                        "acme-docs/gone": "e" * 64,
                    }
                }
            },
        )
        listed = _with_revisions(request)

        _add_acme(auth_client)

        assert _active(org).mcp["pinning"]["pins"] == {
            f"acme-docs/{tool['name']}": tool["revision"] for tool in listed
        }

    def test_listing_without_revisions_pins_nothing(self, auth_client, user, request):
        # A gateway built before revisions: the gateway pins the server's
        # first listing itself instead (auto_baseline).
        org = user.organization
        _add_acme(auth_client)
        assert "pinning" not in _active(org).mcp
