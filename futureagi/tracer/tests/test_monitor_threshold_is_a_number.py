"""Saving an alert refuses a threshold that is not a number.

Python's JSON parser reads 1e309 as infinity, and a float field reads the text
"1e309", "Infinity", "-Infinity" or "NaN" as infinity or not-a-number. No alert
can be judged by such a level. Creating or updating an alert, on
/tracer/user-alerts/ and on /tracer/alerts/, answers 400 with one sentence on
that field and saves nothing. The bare tokens Infinity, -Infinity and NaN are
not JSON, so the body is refused before any field is read.

A level saved as infinity or not-a-number before this answers as null with its
words. An update that leaves that level out keeps it as saved.
"""

import json
import math

import pytest
from rest_framework import status

from tracer.models.monitor import UserAlertMonitor

pytestmark = pytest.mark.django_db

SENTENCE = "Enter a number for the threshold."
CRITICAL = "critical_threshold_value"
WARNING = "warning_threshold_value"
NAME = "Errors up"
RENAMED = "Errors up, renamed"
INFINITY = float("inf")
NOT_A_NUMBER = float("nan")

# A threshold that is not a number, as the JSON body writes it.
NOT_NUMBERS = [
    pytest.param("1e309", id="number-1e309"),
    pytest.param("-1e309", id="number-minus-1e309"),
    pytest.param('"1e309"', id="text-1e309"),
    pytest.param('"Infinity"', id="text-infinity"),
    pytest.param('"-Infinity"', id="text-minus-infinity"),
    pytest.param('"NaN"', id="text-nan"),
]
# Tokens Python's json module would read, which strict JSON does not have.
NOT_JSON = [
    pytest.param("Infinity", id="infinity"),
    pytest.param("-Infinity", id="minus-infinity"),
    pytest.param("NaN", id="nan"),
]
LEVELS = [pytest.param(CRITICAL, id="critical"), pytest.param(WARNING, id="warning")]

# Where an alert is created, and what the body adds there.
CREATE = [
    pytest.param("/tracer/user-alerts/", {}, id="user-alerts"),
    pytest.param("/tracer/alerts/", {"kind": "metric"}, id="alerts"),
]
# Where an alert is updated.
UPDATE = [
    pytest.param("/tracer/user-alerts/{id}/", id="user-alerts"),
    pytest.param("/tracer/alerts/metric/{id}/", id="alerts"),
]
# A large finite threshold still saves: what each create route answers.
SAVED = [
    pytest.param("/tracer/user-alerts/", {}, status.HTTP_200_OK, id="user-alerts"),
    pytest.param(
        "/tracer/alerts/", {"kind": "metric"}, status.HTTP_201_CREATED, id="alerts"
    ),
]
# Rows the API could save before this change: the saved levels and direction,
# the level saved with no number and its words, and what the edit form sends
# without that level.
KEPT = [
    pytest.param(
        {CRITICAL: INFINITY, WARNING: 95.0, "operator": "greater_than"},
        CRITICAL,
        "Infinity",
        {WARNING: 90.0},
        id="infinite-critical",
    ),
    pytest.param(
        {CRITICAL: NOT_A_NUMBER, WARNING: None, "operator": "greater_than"},
        CRITICAL,
        "Not a number",
        {"alert_frequency": 30},
        id="not-a-number-critical",
    ),
    pytest.param(
        {CRITICAL: 5.0, WARNING: INFINITY, "operator": "less_than"},
        WARNING,
        "Infinity",
        {CRITICAL: 4.0},
        id="infinite-warning",
    ),
]


def _uncapped(*args, **kwargs):
    return None


@pytest.fixture
def member_client(auth_client, monkeypatch):
    """A signed-in member of the alert's organization and workspace, with the
    plan's alert count out of the way. A failing answer comes back as the
    status a browser gets, not as the error behind it."""
    monkeypatch.setattr("tfc.ee_gating.check_ee_can_create", _uncapped)
    monkeypatch.setattr("tracer.views.alerts.check_ee_can_create", _uncapped)
    auth_client.raise_request_exception = False
    return auth_client


@pytest.fixture
def make_alert(organization, workspace, observe_project):
    def make(critical, warning=None, operator="greater_than"):
        return UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name=NAME,
            metric_type="count_of_errors",
            threshold_operator=operator,
            threshold_type="static",
            critical_threshold_value=critical,
            warning_threshold_value=warning,
            alert_frequency=60,
        )

    return make


def _new_alert(project, **extra):
    return {
        "project": str(project.id),
        "name": NAME,
        "metric_type": "count_of_errors",
        "threshold_type": "static",
        "threshold_operator": "greater_than",
        CRITICAL: 10,
        WARNING: 5,
        "alert_frequency": 60,
        "notification_emails": ["alerts@example.test"],
        **extra,
    }


def _body(fields, level, raw):
    """``fields`` as JSON text, with ``level`` written as the JSON text ``raw``."""
    others = {name: value for name, value in fields.items() if name != level}
    return json.dumps(others)[:-1] + f', "{level}": {raw}}}'


def _post(client, path, text):
    return client.post(path, data=text, content_type="application/json")


def _patch(client, path, text):
    return client.patch(path, data=text, content_type="application/json")


def _assert_refused(response, level):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content[:300]
    body = response.json()
    assert body.get("details") == {level: [SENTENCE]}
    assert body.get("attr") == level


def _refuse_constant(token):
    raise AssertionError(f"the answer is not strict JSON: it holds {token}")


def _strict_json(response):
    """The body as strict JSON: a NaN, Infinity or -Infinity token fails."""
    return json.loads(response.content, parse_constant=_refuse_constant)


def _same(saved, stored):
    if stored is None:
        return False
    return stored == saved or (math.isnan(saved) and math.isnan(stored))


@pytest.mark.parametrize("path,extra", CREATE)
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("raw", NOT_NUMBERS)
def test_creating_an_alert_refuses_a_threshold_that_is_not_a_number(
    member_client, observe_project, path, extra, level, raw
):
    text = _body(_new_alert(observe_project, **extra), level, raw)

    response = _post(member_client, path, text)

    _assert_refused(response, level)
    assert not UserAlertMonitor._base_manager.filter(name=NAME).exists()


@pytest.mark.parametrize("path", UPDATE)
@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("raw", NOT_NUMBERS)
def test_updating_an_alert_refuses_a_threshold_that_is_not_a_number(
    member_client, make_alert, path, level, raw
):
    alert = make_alert(10.0, 5.0)
    text = _body({"name": RENAMED}, level, raw)

    response = _patch(member_client, path.format(id=alert.id), text)

    _assert_refused(response, level)
    stored = UserAlertMonitor._base_manager.get(pk=alert.pk)
    assert stored.name == NAME
    assert stored.critical_threshold_value == 10.0
    assert stored.warning_threshold_value == 5.0


@pytest.mark.parametrize("path,extra", CREATE)
@pytest.mark.parametrize("raw", NOT_JSON)
def test_a_body_with_a_bare_infinity_or_nan_token_is_refused_as_not_json(
    member_client, observe_project, path, extra, raw
):
    text = _body(_new_alert(observe_project, **extra), CRITICAL, raw)

    response = _post(member_client, path, text)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content[:300]
    assert response.json()["code"] == "parse_error"
    assert not UserAlertMonitor._base_manager.filter(name=NAME).exists()


@pytest.mark.parametrize("path,extra,answer", SAVED)
def test_a_large_finite_threshold_still_saves(
    member_client, observe_project, path, extra, answer
):
    text = _body(_new_alert(observe_project, **extra), CRITICAL, "1e308")

    response = _post(member_client, path, text)

    assert response.status_code == answer, response.content[:300]
    stored = UserAlertMonitor._base_manager.get(name=NAME)
    assert stored.critical_threshold_value == 1e308


def test_a_negative_threshold_is_still_refused_by_the_minimum(
    member_client, observe_project
):
    text = _body(_new_alert(observe_project), CRITICAL, "-1")

    response = _post(member_client, "/tracer/user-alerts/", text)

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content[:300]
    assert response.json()["details"] == {
        CRITICAL: ["Ensure this value is greater than or equal to 0.0."]
    }
    assert not UserAlertMonitor._base_manager.filter(name=NAME).exists()


@pytest.mark.parametrize("path", UPDATE)
@pytest.mark.parametrize("row,level,words,change", KEPT)
def test_an_update_that_leaves_a_saved_level_out_keeps_it(
    member_client, make_alert, path, row, level, words, change
):
    alert = make_alert(row[CRITICAL], row[WARNING], row["operator"])
    body = {
        "name": RENAMED,
        "metric_type": "count_of_errors",
        "threshold_type": "static",
        "threshold_operator": row["operator"],
        **change,
    }

    response = member_client.patch(path.format(id=alert.id), body, format="json")

    assert response.status_code == status.HTTP_200_OK, response.content[:300]
    answer = _strict_json(response)["result"]
    assert answer[level] is None
    assert answer[level.replace("_value", "_label")] == words
    stored = UserAlertMonitor._base_manager.get(pk=alert.pk)
    assert _same(row[level], getattr(stored, level))
    assert stored.name == RENAMED
    for name, value in change.items():
        assert getattr(stored, name) == value


def test_an_update_that_sends_null_for_a_saved_infinite_critical_keeps_it(
    member_client, make_alert
):
    alert = make_alert(INFINITY, 95.0)

    response = member_client.patch(
        f"/tracer/user-alerts/{alert.id}/", {CRITICAL: None}, format="json"
    )

    # Null does not mean "as saved": the alert still needs a critical level, so
    # the edit form leaves a kept level out instead of sending null.
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content[:300]
    stored = UserAlertMonitor._base_manager.get(pk=alert.pk)
    assert math.isinf(stored.critical_threshold_value)
