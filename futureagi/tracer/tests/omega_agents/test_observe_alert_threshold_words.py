"""A Console alert whose saved threshold is not a finite number still answers.

The Console agent and deployment routes list the two alerts linked to the
Observe project. PostgreSQL float columns hold Infinity and NaN, which strict
JSON has no token for, and a linked alert's threshold is whatever is saved.
Such a level is sent as null with its words in ``<level>ThresholdLabel``:
"Infinity", "-Infinity" or "Not a number", the words the alert answers use. A
finite threshold answers exactly as before, with no label.
"""

import json
import math

import pytest
from rest_framework import status

from omega_rollout import products as rollout_products
from omega_rollout import readiness
from omega_rollout.testing import admit_for_tests, declare_released
from tracer.models.monitor import UserAlertMonitor
from tracer.tests.omega_agents.conftest import ensure, new_agent_id
from tracer.tests.omega_deployments.conftest import ensure_url, new_deployment_id

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

INFINITY = float("inf")
NOT_A_NUMBER = float("nan")
DEPLOYMENT_NAME = "support-agent"

# A saved threshold, and the words the answer gives for it (None: a finite
# number, sent as it is).
THRESHOLDS = [
    pytest.param(INFINITY, "Infinity", id="infinity"),
    pytest.param(-INFINITY, "-Infinity", id="minus-infinity"),
    pytest.param(NOT_A_NUMBER, "Not a number", id="not-a-number"),
    pytest.param(150.0, None, id="finite"),
]


@pytest.fixture(autouse=True)
def deployments_admitted(organization, settings, agent_observe_released):
    """The deployment route has its own walls; this directory opens the Console
    only. Its tests run as hosted Future AGI, where Deployments also needs its
    release part declared, so this declares it beside the agent route's own."""
    admit_for_tests(organization, products=[rollout_products.DEPLOYMENTS])
    declare_released(settings, [readiness.AGENT_OBSERVE, readiness.HOSTED_DEPLOYMENTS])


@pytest.fixture
def member_client(auth_client):
    """A signed-in member of the organization and workspace. A failing answer
    comes back as the 500 a browser gets, not as the error behind it."""
    auth_client.raise_request_exception = False
    return auth_client


def _asker(client, route):
    """Asks the route about one new Console agent or deployment, the same one
    each time. The first ask links it and sets up its two default alerts."""
    if route == "agent":
        agent_id = new_agent_id()
        return lambda: ensure(client, agent_id)
    deployment_id = new_deployment_id()
    return lambda: client.post(
        ensure_url(deployment_id), {"name": DEPLOYMENT_NAME}, format="json"
    )


def _refuse_constant(token):
    raise AssertionError(f"the answer is not strict JSON: it holds {token}")


def _result(response):
    """The answer's result read as strict JSON: a NaN, Infinity or -Infinity
    token fails."""
    assert response.status_code == status.HTTP_200_OK, response.content[:200]
    body = json.loads(response.content, parse_constant=_refuse_constant)
    assert body["status"] is True
    return body["result"]


def _by_kind(result):
    return {alert["kind"]: alert for alert in result["alerts"]}


def _without_alerts(result):
    return {key: value for key, value in result.items() if key != "alerts"}


def _store(monitor_id, level, value):
    """Writes the level's threshold straight to the alert's row."""
    UserAlertMonitor._base_manager.filter(pk=monitor_id).update(
        **{f"{level}_threshold_value": value}
    )


def _same(saved, stored):
    return stored == saved or (math.isnan(saved) and math.isnan(stored))


@pytest.mark.parametrize("value,words", THRESHOLDS)
@pytest.mark.parametrize("level", ["critical", "warning"])
@pytest.mark.parametrize("route", ["agent", "deployment"])
def test_a_linked_alert_says_a_threshold_with_no_number_in_words(
    member_client, route, level, value, words
):
    ask = _asker(member_client, route)
    before = _result(ask())
    errors = _by_kind(before)["errors_spike"]
    _store(errors["id"], level, value)

    after = _result(ask())

    key = f"{level}Threshold"
    if words is None:
        expected = {**errors, key: value}
    else:
        expected = {**errors, key: None, f"{key}Label": words}
    # Only that level changes; the other alert and the rest are as before.
    assert _by_kind(after) == {**_by_kind(before), "errors_spike": expected}
    assert [alert["kind"] for alert in after["alerts"]] == [
        "errors_spike",
        "daily_tokens",
    ]
    assert _without_alerts(after) == _without_alerts(before)
    if words is None:
        # A finite level: the same keys in the same order, and no label.
        assert list(_by_kind(after)["errors_spike"]) == list(errors)
    # Answering never changes what is saved.
    stored = UserAlertMonitor._base_manager.get(pk=errors["id"])
    assert _same(value, getattr(stored, f"{level}_threshold_value"))


@pytest.mark.parametrize("route", ["agent", "deployment"])
def test_both_levels_of_a_linked_alert_can_be_in_words(member_client, route):
    ask = _asker(member_client, route)
    errors = _by_kind(_result(ask()))["errors_spike"]
    _store(errors["id"], "critical", NOT_A_NUMBER)
    _store(errors["id"], "warning", INFINITY)

    alert = _by_kind(_result(ask()))["errors_spike"]

    assert alert == {
        **errors,
        "criticalThreshold": None,
        "criticalThresholdLabel": "Not a number",
        "warningThreshold": None,
        "warningThresholdLabel": "Infinity",
    }
