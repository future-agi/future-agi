"""Fixtures for the Hub tests: storage in a temp folder, the checker secret, a maintainer
organisation with maintainers A and B, the Official publisher, and a community user C."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission

from omega_hub.models import Publisher
from omega_hub.services import tokens
from omega_hub.tests.helpers import (
    CHECKER_TOKEN,
    clear_caches,
    make_person,
    session_client,
)
from tfc.constants.levels import Level

# Production clears the per-request workspace context in this middleware; tfc.settings.test drops
# it, which lets one test request's workspace filter the next one's queries (an org API key lookup
# then fails as "invalid"). The Hub tests run with it, as the platform does.
WORKSPACE_CONTEXT_MIDDLEWARE = (
    "tfc.middleware.workspace_context.WorkspaceContextMiddleware"
)


@pytest.fixture(autouse=True)
def hub_settings(settings, tmp_path):
    if WORKSPACE_CONTEXT_MIDDLEWARE not in settings.MIDDLEWARE:
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, WORKSPACE_CONTEXT_MIDDLEWARE]
    settings.OMEGA_HUB_STORAGE = "filesystem"
    settings.OMEGA_HUB_STORAGE_DIR = str(tmp_path / "hub-store")
    settings.OMEGA_HUB_CHECKER_TOKEN = CHECKER_TOKEN
    # The stand-in checker is configured by this fixture, not the host's Compose profile.
    # Profile-gate regressions explicitly override this value.
    settings.OMEGA_HUB_COMPOSE_PROFILES = "hub"
    settings.OMEGA_HUB_METRICS_TOKEN = "metrics-test-token"
    settings.OMEGA_HUB_PUBLIC_API_URL = "http://testserver/omega-hub/v1"
    clear_caches()
    yield settings
    clear_caches()


@pytest.fixture(autouse=True)
def hub_rollout_open(request, settings):
    """The Hub ships dark (READINESS 3.13). Its own suite tests the Hub, so it runs with the Hub open
    to everyone (S3, 100%) and every part released (Hub tokens need the hub CLI,
    omega_rollout.readiness); omega_rollout/tests/test_hub_gate.py, test_readiness.py and
    test_readiness_gates.py test the gates themselves."""
    from omega_rollout.testing import declare_released

    declare_released(settings)
    names = request.fixturenames
    if (
        not {"db", "transactional_db"} & set(names)
        and request.node.get_closest_marker("django_db") is None
    ):
        yield
        return
    request.getfixturevalue("transactional_db" if "transactional_db" in names else "db")
    from omega_rollout.models import ProductRollout

    ProductRollout.objects.update_or_create(
        product="hub", defaults={"stage": "S3", "percentage": 100}
    )
    yield


class World:
    pass


def grant_maintainer(user):
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="omega_hub", codename="hub_maintainer"
        )
    )


@pytest.fixture
def world(db, hub_settings):
    w = World()
    w.maint_a = make_person("maint-a", org_level=Level.ADMIN)
    w.maint_org = w.maint_a.organization
    w.maint_b = make_person("maint-b", org_level=Level.ADMIN, organization=w.maint_org)
    hub_settings.OMEGA_HUB_MAINTAINER_ORG_ID = str(w.maint_org.id)
    grant_maintainer(w.maint_a.user)
    grant_maintainer(w.maint_b.user)
    w.official = Publisher.objects.create(
        handle="future-agi",
        kind="organization",
        owner_org=w.maint_org,
        display_name="Future AGI",
        tier=Publisher.Tier.OFFICIAL,
    )
    w.token_a_obj, w.token_a = tokens.create_token(w.maint_a.user, w.official, "seed")
    w.carol = make_person("carol")
    w.c_pub = Publisher.objects.create(
        handle="c-test", kind="user", owner_user=w.carol.user, display_name="Carol"
    )
    w.token_c_obj, w.token_c = tokens.create_token(w.carol.user, w.c_pub, "cli")
    w.a = session_client(w.maint_a)
    w.b = session_client(w.maint_b)
    w.c = session_client(w.carol)
    return w
