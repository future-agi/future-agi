"""Error Feed requests that need a connection's saved credentials.

When those credentials cannot be read, Linear's team list, Linear issue
creation and a Slack alert's channel check answer one sentence instead of
failing, and nothing is sent to Linear or Slack.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.services.feed_alerts import rules
from tracer.views.feed.linear_issue_view import CreateLinearIssueView, LinearTeamsView

SENTENCE = (
    "This connection's saved credentials can't be read. Reconnect it to use it again."
)
_CONNECTIONS = (
    "integrations.models.integration_connection.IntegrationConnection.objects.filter"
)


@pytest.fixture
def unreadable(settings):
    """A connection whose saved credentials were sealed with another key."""
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    sealed = Fernet(Fernet.generate_key()).encrypt(
        json.dumps(
            {"api_key": "lin-placeholder", "bot_token": "xoxb-placeholder"}
        ).encode()
    )
    return SimpleNamespace(encrypted_credentials=sealed)


@pytest.fixture
def _error_feed_entitled(monkeypatch):
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **k: None)


def _answer(view, request, **kwargs):
    """The view's response, or what it raised instead of answering."""
    try:
        return view(request, **kwargs)
    except Exception as exc:
        return exc


def _found(connections, connection):
    lookup = connections.return_value.exclude.return_value.order_by.return_value
    lookup.first.return_value = connection


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_linear_teams_answer_the_sentence(user, organization, unreadable):
    request = APIRequestFactory().get("/tracer/feed/integrations/linear/teams/")
    request.organization = organization
    force_authenticate(request, user=user)

    with (
        patch(_CONNECTIONS) as connections,
        patch(
            "integrations.services.linear_service.LinearService.get_teams"
        ) as get_teams,
    ):
        _found(connections, unreadable)
        response = _answer(LinearTeamsView.as_view(), request)

    assert getattr(response, "status_code", None) == 400
    assert response.data["result"] == SENTENCE
    get_teams.assert_not_called()


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_creating_a_linear_issue_answers_the_sentence(
    user, organization, observe_project, unreadable
):
    cluster = TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="E-UNREADABLE-1",
        error_type="eval",
        title="Tone",
    )
    request = APIRequestFactory().post(
        f"/tracer/feed/issues/{cluster.cluster_id}/create-linear-issue/",
        {"team_id": "team-local-test"},
        format="json",
    )
    request.organization = organization
    force_authenticate(request, user=user)

    with (
        patch(_CONNECTIONS) as connections,
        patch(
            "integrations.services.linear_service.LinearService.create_issue"
        ) as create_issue,
    ):
        _found(connections, unreadable)
        response = _answer(
            CreateLinearIssueView.as_view(), request, cluster_id=cluster.cluster_id
        )

    assert getattr(response, "status_code", None) == 400
    assert response.data["result"] == SENTENCE
    create_issue.assert_not_called()
    cluster.refresh_from_db()
    assert not cluster.external_issue_url


def test_a_slack_alert_channel_check_answers_the_sentence(unreadable):
    with patch(
        "integrations.services.slack_service.SlackService.validate_channel"
    ) as validate_channel:
        try:
            rules._channel(unreadable, "C0123456789")
            raised = None
        except Exception as exc:
            raised = exc

    assert isinstance(raised, rules.FeedAlertError)
    assert str(raised) == SENTENCE
    assert raised.field == "slack_connection_id"
    assert 400 <= raised.status_code < 500
    validate_channel.assert_not_called()
