"""An alert whose saved threshold is infinite or not a number still answers.

PostgreSQL float columns hold Infinity and NaN, and an alert body such as
1e309, "Infinity" or "NaN" saves them. Strict JSON has no token for either.
Every alert answer sends such a threshold as null with its words in
``<level>_threshold_label``: "Infinity" or "-Infinity" for an infinite
threshold and "Not a number" for NaN. A finite threshold answers exactly as
before, with no label.
"""

import json
import math

import pytest
from rest_framework import status

from tracer.models.monitor import UserAlertMonitor

pytestmark = pytest.mark.django_db

INFINITY = float("inf")
NOT_A_NUMBER = float("nan")
RENAMED = {"name": "Errors up, renamed"}

# The saved critical and warning thresholds, and the words the answer gives for
# the critical level (None: a finite number, sent as it is).
THRESHOLDS = [
    pytest.param(INFINITY, 95.0, "Infinity", id="infinity"),
    pytest.param(-INFINITY, None, "-Infinity", id="minus-infinity"),
    pytest.param(NOT_A_NUMBER, None, "Not a number", id="not-a-number"),
    pytest.param(0.1, 0.05, None, id="finite"),
]


def _refuse_constant(token):
    raise AssertionError(f"the answer is not strict JSON: it holds {token}")


def _strict_json(response):
    """The body as strict JSON: a NaN, Infinity or -Infinity token fails."""
    return json.loads(response.content, parse_constant=_refuse_constant)


def _answer(body):
    return body.get("result", body)


def _single(body, alert):
    return _answer(body)


def _listed(key):
    def pick(body, alert):
        return next(row for row in _answer(body)[key] if row["id"] == str(alert.id))

    return pick


def _same(saved, stored):
    return stored == saved or (math.isnan(saved) and math.isnan(stored))


# Every answer that carries an alert's thresholds: how it is asked for, and
# where the alert sits in it.
ANSWERS = [
    pytest.param("get", "/tracer/user-alerts/{id}/details/", _single, id="details"),
    pytest.param("get", "/tracer/user-alerts/{id}/", _single, id="alert"),
    pytest.param(
        "get",
        "/tracer/user-alerts/?project_id={project}",
        _listed("results"),
        id="alert-list",
    ),
    pytest.param(
        "get", "/tracer/alerts/?kind=metric", _listed("alerts"), id="alerts-list"
    ),
    pytest.param("get", "/tracer/alerts/metric/{id}/", _single, id="alerts-alert"),
    pytest.param("patch", "/tracer/user-alerts/{id}/", _single, id="rename"),
    pytest.param("patch", "/tracer/alerts/metric/{id}/", _single, id="alerts-rename"),
]


@pytest.fixture
def member_client(auth_client):
    """A signed-in member of the alert's organization and workspace. A failing
    answer comes back as the 500 a browser gets, not as the error behind it."""
    auth_client.raise_request_exception = False
    return auth_client


@pytest.fixture
def make_alert(organization, workspace, observe_project):
    def make(name, critical, warning=None, operator="greater_than"):
        return UserAlertMonitor.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            name=name,
            metric_type="count_of_errors",
            threshold_operator=operator,
            threshold_type="static",
            critical_threshold_value=critical,
            warning_threshold_value=warning,
            alert_frequency=60,
        )

    return make


def _details(client, alert):
    response = client.get(f"/tracer/user-alerts/{alert.id}/details/")
    assert response.status_code == status.HTTP_200_OK, response.content[:200]
    return _answer(_strict_json(response))


@pytest.mark.parametrize("method,url,pick", ANSWERS)
@pytest.mark.parametrize("critical,warning,words", THRESHOLDS)
def test_an_alert_answer_says_a_threshold_with_no_number_in_words(
    member_client,
    make_alert,
    observe_project,
    method,
    url,
    pick,
    critical,
    warning,
    words,
):
    alert = make_alert("Errors up", critical, warning)
    path = url.format(id=alert.id, project=observe_project.id)

    if method == "patch":
        response = member_client.patch(path, RENAMED, format="json")
    else:
        response = member_client.get(path)

    assert response.status_code == status.HTTP_200_OK, response.content[:200]
    answer = pick(_strict_json(response), alert)
    if words is None:
        assert answer["critical_threshold_value"] == critical
        assert "critical_threshold_label" not in answer
    else:
        assert answer["critical_threshold_value"] is None
        assert answer["critical_threshold_label"] == words
    assert answer["warning_threshold_value"] == warning
    assert "warning_threshold_label" not in answer
    # Answering never changes what is saved.
    stored = UserAlertMonitor._base_manager.get(pk=alert.pk)
    assert _same(critical, stored.critical_threshold_value)


def test_an_infinite_warning_threshold_is_said_in_words_beside_a_numeric_critical(
    member_client, make_alert
):
    alert = make_alert("Errors down", 5.0, INFINITY, operator="less_than")

    answer = _details(member_client, alert)

    assert answer["critical_threshold_value"] == 5.0
    assert "critical_threshold_label" not in answer
    assert answer["warning_threshold_value"] is None
    assert answer["warning_threshold_label"] == "Infinity"


def test_a_threshold_in_words_adds_only_its_label_to_the_details_answer(
    member_client, make_alert
):
    finite = _details(member_client, make_alert("Errors up", 0.1))
    unbounded = _details(member_client, make_alert("Errors way up", INFINITY))

    assert set(unbounded) == set(finite) | {"critical_threshold_label"}
