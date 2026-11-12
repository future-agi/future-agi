"""The Hub writes that need a part that may not work (omega_rollout.readiness).

- A submission needs the checker, so with none configured an upload is refused instead of waiting
  forever on "checks start in a moment". That is a configuration error: intake answers 503
  OMEGA_HUB_CHECKER_NOT_CONFIGURED, saying what is missing and storing nothing, never the 404 of a
  dark product.
- A Hub token's only use is `omega hub login`, so none is made before the released CLI has that
  command: refused with the 404 a dark product gets, and the refusal is logged."""

from __future__ import annotations

import pytest

from omega_hub.models import CheckJob, Submission
from omega_hub.tests.helpers import entry_files, submit, token_client
from omega_rollout import enforce
from omega_rollout import products as p
from omega_rollout.tests.test_refusal_logging import Recorder

pytestmark = pytest.mark.django_db
TOKENS = "/omega-hub/v1/me/tokens"


def refused_as_dark(response) -> bool:
    return (
        response.status_code == 404
        and response.json()["error"]["code"] == p.REASON_NOT_ADMITTED
    )


def test_a_submission_says_what_is_missing_while_no_checker_is_configured(
    world, hub_settings, monkeypatch
):
    log = Recorder()
    monkeypatch.setattr(enforce, "logger", log)
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = ""
    files = entry_files("first-agent")
    for client in (token_client(world.token_c), world.c):
        response = submit(client, "c-test", files, name="first-agent", version="0.1.0")
        assert response.status_code == 503, response.content
        error = response.json()["error"]
        assert error["code"] == "OMEGA_HUB_CHECKER_NOT_CONFIGURED"
        assert (
            "Nothing was stored" in error["message"]
            and "OMEGA_HUB_CHECKER_TOKEN" in error["fix"]
        )
    # Not a dark product: no rollout refusal is logged, and nothing was stored.
    assert log.refusals() == []
    assert Submission.objects.count() == 0 and CheckJob.objects.count() == 0
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = "test-checker-token-0123456789"
    created = submit(
        token_client(world.token_c),
        "c-test",
        files,
        name="first-agent",
        version="0.1.0",
    )
    assert created.status_code == 201, created.content


@pytest.mark.parametrize("profiles", ["", "sandbox", "hub-extra", "ml,sandbox"])
def test_standalone_without_hub_profile_is_not_ready_and_refuses_uploads(
    world, settings, profiles
):
    from omega_rollout.readiness import HUB_PUBLISH, part_ready

    settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    # Includes installs whose secret was generated earlier, not just the old placeholder.
    settings.OMEGA_HUB_CHECKER_TOKEN = "test-generated-token-0123456789"
    assert not part_ready(HUB_PUBLISH)
    response = submit(
        world.c,
        "c-test",
        entry_files("first-agent"),
        name="first-agent",
        version="1.0.0",
    )
    assert response.status_code == 503, response.content
    assert response.json()["error"]["code"] == "OMEGA_HUB_CHECKER_NOT_CONFIGURED"
    assert not Submission.objects.exists() and not CheckJob.objects.exists()


@pytest.mark.parametrize("profiles", ["hub", "sandbox,hub", "*"])
def test_standalone_with_checker_profile_and_token_can_publish(
    world, settings, profiles
):
    from omega_rollout.readiness import HUB_PUBLISH, part_ready

    settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    assert part_ready(HUB_PUBLISH)
    response = submit(
        world.c,
        "c-test",
        entry_files("first-agent"),
        name="first-agent",
        version="1.0.0",
    )
    assert response.status_code == 201, response.content


@pytest.mark.parametrize(
    "token", ["", "   ", "local-dev-only-hub-checker-token-replace-me"]
)
def test_enabling_the_profile_without_a_real_token_is_not_ready(world, settings, token):
    from omega_rollout.readiness import HUB_PUBLISH, part_ready

    settings.OMEGA_HUB_COMPOSE_PROFILES = "hub"
    settings.OMEGA_HUB_CHECKER_TOKEN = token
    assert not part_ready(HUB_PUBLISH)
    response = submit(
        world.c,
        "c-test",
        entry_files("first-agent"),
        name="first-agent",
        version="1.0.0",
    )
    assert response.status_code == 503, response.content
    assert not Submission.objects.exists() and not CheckJob.objects.exists()


def test_a_hub_token_is_refused_until_the_hub_cli_is_released(world, hub_settings):
    hub_settings.OMEGA_RELEASED_PARTS = "studio_cli,deploy_cli"
    body = {"publisher": "c-test", "name": "laptop", "days": 30}
    assert refused_as_dark(world.c.post(TOKENS, body, format="json"))
    # Revoking and listing keep working: they start nothing.
    assert world.c.get(TOKENS).status_code == 200
    revoked = world.c.delete(f"{TOKENS}/{world.token_c_obj.pk}")
    assert revoked.status_code == 200, revoked.content
    hub_settings.OMEGA_RELEASED_PARTS = "hub_cli"
    made = world.c.post(TOKENS, body, format="json")
    assert made.status_code == 201, made.content
