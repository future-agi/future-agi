"""The checker token counts only with the hub profile.

A production install that does not enable the Hub either leaves OMEGA_HUB_CHECKER_TOKEN
empty or keeps the token deploy/setup.sh generated for it (conf.checker_token: a marker
without `hub` or `*` turns any token off). A checker request is then refused with 503
OMEGA_HUB_CHECKER_DISABLED, whether its token header is empty, missing or even the
configured token itself (auth.CheckerAuthentication), and an upload is refused before
anything is stored.

A production Hub install sets COMPOSE_PROFILES=hub, which the backend sees as its marker:
the same filled token then turns the Hub on. conf.checker_token() returns it, the checker
is authenticated with it (and refused without it), and an upload is accepted.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from omega_hub import conf
from omega_hub.models import CheckJob, Submission
from omega_hub.tests.helpers import entry_files, submit, token_client

pytestmark = pytest.mark.django_db
CLAIM = "/omega-hub/internal/checker/claim"
# OMEGA_HUB_COMPOSE_PROFILES values that do not enable the hub profile, and values that do
# (what the backend sees with COMPOSE_PROFILES=hub, alone or with other profiles).
WITHOUT_HUB = ["", "sandbox", "ml,sandbox", "hub-extra"]
WITH_HUB = ["hub", "full,hub"]
# A made-up checker token, as deploy/setup.sh generates one for every new install.
FILLED_TOKEN = "made-up-generated-checker-token-0123456789abcdef"
# The checker token: empty (no Hub settings), or filled (a generated token kept with the Hub off).
TOKENS = ["", FILLED_TOKEN]


def checker(header: str | None) -> APIClient:
    """A checker client sending `header` as its token, or no token header at all (None)."""
    client = APIClient()
    if header is not None:
        client.credentials(
            HTTP_X_OMEGA_HUB_CHECKER_TOKEN=header, HTTP_X_OMEGA_HUB_CHECKER_ID="test-checker"
        )
    return client


@pytest.mark.parametrize("header", [None, "", FILLED_TOKEN])
@pytest.mark.parametrize("token", TOKENS)
@pytest.mark.parametrize("profiles", WITHOUT_HUB)
def test_a_checker_request_is_refused_as_disabled(world, hub_settings, profiles, token, header):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = token
    response = checker(header).post(CLAIM)
    assert response.status_code == 503, response.content
    assert response.json()["error"]["code"] == "OMEGA_HUB_CHECKER_DISABLED"


@pytest.mark.parametrize("token", TOKENS)
@pytest.mark.parametrize("profiles", WITHOUT_HUB)
def test_an_upload_is_refused_and_nothing_is_stored(world, hub_settings, profiles, token):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = token
    files = entry_files("first-agent")
    for client in (token_client(world.token_c), world.c):
        response = submit(client, "c-test", files, name="first-agent", version="0.1.0")
        assert response.status_code == 503, response.content
        assert response.json()["error"]["code"] == "OMEGA_HUB_CHECKER_NOT_CONFIGURED"
    assert not Submission.objects.exists() and not CheckJob.objects.exists()


@pytest.mark.parametrize("profiles", WITH_HUB)
def test_with_the_hub_profile_the_filled_token_turns_the_hub_on(world, hub_settings, profiles):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = FILLED_TOKEN
    assert conf.checker_token() == FILLED_TOKEN
    # Nothing is queued yet: an authenticated claim finds no work (204).
    claimed = checker(FILLED_TOKEN).post(CLAIM)
    assert claimed.status_code == 204, claimed.content
    for header in (None, "", "another-made-up-token"):
        refused = checker(header).post(CLAIM)
        assert refused.status_code == 401, refused.content
        assert refused.json()["error"]["code"] == "OMEGA_HUB_CHECKER_AUTH"
    created = submit(
        token_client(world.token_c), "c-test", entry_files("first-agent"),
        name="first-agent", version="0.1.0",
    )
    assert created.status_code == 201, created.content
    assert Submission.objects.filter(pk=created.json()["id"]).exists()
