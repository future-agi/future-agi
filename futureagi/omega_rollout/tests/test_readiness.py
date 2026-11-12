"""Readiness of product parts (HIDE-UNFINISHED-FA): a control that does not work today is hidden.
The server declares each part through `/api/capabilities/` as `omega_part_<part>`, from the release's
`OMEGA_RELEASED_PARTS` (hub_cli, deploy_cli, deployment_studio) or from what it observes itself
(hub_publish: a configured checker). Products are not hidden by readiness: a product decision of
2026-10-19 keeps the Studio tab and the Hub visible, decided by rollout alone."""

from __future__ import annotations

import pytest

from omega_hub.tests.helpers import CHECKER_TOKEN
from omega_rollout import products as p
from omega_rollout import readiness
from omega_rollout.tests.conftest import admit, client_for

pytestmark = pytest.mark.django_db
CAPABILITIES = "/api/capabilities/"
PART_FEATURES = {f"omega_part_{part}" for part in readiness.PARTS}


@pytest.fixture(autouse=True)
def configured_checker_profile(settings):
    # These tests control the token, independently of the host's standalone Compose profile.
    settings.OMEGA_HUB_COMPOSE_PROFILES = "hub"


def features(person, workspace=None):
    response = client_for(person, workspace).get(CAPABILITIES)
    assert response.status_code == 200, response.content
    return response.json()["features"]


def hidden(row) -> bool:
    return row["allowed"] is False and row["reason_code"] == "PRODUCT_NOT_ADMITTED"


class TestReleasedParts:
    def test_a_comma_list_a_list_and_the_environment(self, settings, monkeypatch):
        settings.OMEGA_RELEASED_PARTS = " deploy_cli , HUB_CLI,,"
        assert readiness.released_parts() == {"deploy_cli", "hub_cli"}
        settings.OMEGA_RELEASED_PARTS = ["deployment_studio"]
        assert readiness.released_parts() == {"deployment_studio"}
        del settings.OMEGA_RELEASED_PARTS
        monkeypatch.setenv("OMEGA_RELEASED_PARTS", "deploy_cli")
        assert readiness.released_parts() == {"deploy_cli"}
        monkeypatch.delenv("OMEGA_RELEASED_PARTS")
        assert readiness.released_parts() == frozenset()

    def test_an_unknown_name_is_ignored_and_never_ready(self, settings):
        settings.OMEGA_RELEASED_PARTS = "hub,hub_cli,everything,hub_publish,studio_tab"
        assert readiness.released_parts() == {"hub_cli"}
        for name in ("hub", "everything", "", "console", "studio_tab"):
            assert readiness.part_ready(name) is False

    def test_nothing_is_released_by_default(self, settings, monkeypatch):
        del settings.OMEGA_RELEASED_PARTS
        monkeypatch.delenv("OMEGA_RELEASED_PARTS", raising=False)
        for part in readiness.DECLARED_PARTS:
            assert readiness.part_ready(part) is False

    def test_hub_publish_is_observed_from_the_checker_not_declared(self, settings):
        settings.OMEGA_RELEASED_PARTS = "hub_publish"
        settings.OMEGA_HUB_CHECKER_TOKEN = ""
        assert readiness.part_ready(readiness.HUB_PUBLISH) is False
        settings.OMEGA_HUB_CHECKER_TOKEN = CHECKER_TOKEN
        settings.OMEGA_RELEASED_PARTS = ""
        assert readiness.part_ready(readiness.HUB_PUBLISH) is True


class TestCapabilities:
    def test_each_part_is_reported_and_follows_its_fact(self, tenants, settings):
        settings.OMEGA_RELEASED_PARTS = ""
        settings.OMEGA_HUB_CHECKER_TOKEN = ""
        answer = features(tenants.b)
        assert set(answer) >= PART_FEATURES
        assert all(hidden(answer[feature]) for feature in PART_FEATURES)
        # Every declared part, the parity program's hide switches included (test_hide_switches.py).
        settings.OMEGA_RELEASED_PARTS = ",".join(readiness.DECLARED_PARTS)
        settings.OMEGA_HUB_CHECKER_TOKEN = CHECKER_TOKEN
        answer = features(tenants.b)
        assert all(answer[feature]["allowed"] is True for feature in PART_FEATURES)

    def test_parts_never_hide_or_admit_a_product(self, tenants, settings):
        """The Studio tab and the Hub stay decided by rollout alone."""
        admit(tenants, p.STUDIO_TAB)
        admit(tenants, p.HUB)
        settings.OMEGA_RELEASED_PARTS = ""
        settings.OMEGA_HUB_CHECKER_TOKEN = ""
        a = features(tenants.a)
        assert a["omega_studio_tab"]["allowed"] is True
        assert a["omega_hub"]["allowed"] is True
        settings.OMEGA_RELEASED_PARTS = "hub_cli,deploy_cli,deployment_studio"
        b = features(tenants.b)
        assert hidden(b["omega_studio_tab"]) and hidden(b["omega_hub"])
