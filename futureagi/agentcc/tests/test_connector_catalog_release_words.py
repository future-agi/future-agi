"""A connector the gateway does not serve in this release says so in words
that promise no later date: "in this release", never "yet". Pure: the plans
are built from the record alone, with no database or catalogue file."""

import pytest

from agentcc.services import connector_catalog as catalog

ACTIVE = {"lifecycle": {"status": "active"}}


@pytest.mark.parametrize(
    "kind,words",
    [
        (
            "rest",
            "a rest provider is served by FutureAGI's connector runtime, which "
            "this gateway does not run in this release",
        ),
        (
            "partner",
            "a partner provider needs the partner platform's per-person "
            "identity, which the gateway does not support in this release",
        ),
    ],
)
def test_a_provider_kind_the_gateway_does_not_serve_says_in_this_release(kind, words):
    plan = catalog._plan(ACTIVE, {**ACTIVE, "kind": kind})
    assert plan.code == catalog.KIND_NOT_SERVED
    assert plan.message == words


def test_per_person_accounts_say_they_are_not_resolved_in_this_release():
    assert catalog._PER_USER_MESSAGE == (
        "each person's own account needs per-user credentials, which the "
        "gateway does not resolve in this release; only agent mode (one "
        "shared account) works"
    )


def test_oauth_the_gateway_does_not_serve_says_in_this_release():
    plan = catalog._credential_plan({"kind": "oauth2"})
    assert plan.code == catalog.OAUTH_NOT_BUILT
    assert plan.message == (
        "connecting over OAuth is not available in this release; only an API "
        "key or no credential can be connected"
    )


@pytest.mark.parametrize("kind", ["basic", "custom_header"])
def test_a_credential_the_gateway_cannot_send_says_in_this_release(kind):
    plan = catalog._credential_plan({"kind": kind})
    assert plan.code == catalog.AUTH_NOT_SUPPORTED
    assert plan.message == (
        f"the gateway cannot send a {kind} credential in this release"
    )


def test_no_reason_for_an_unserved_provider_promises_a_later_date():
    words = [*catalog._KIND_REASONS.values(), catalog._PER_USER_MESSAGE]
    assert [w for w in words if " yet" in w] == []
