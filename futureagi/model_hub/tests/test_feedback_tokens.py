"""Feedback capabilities: authenticated minting, anonymous write-only submission."""

import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core import signing
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from model_hub.models.ai_model import AIModel
from model_hub.models.score import Score
from tracer.models.project import Project

MINT_URL = "/model-hub/scores/feedback-token/"
SUBMIT_URL = "/model-hub/scores/feedback/"

pytestmark = pytest.mark.django_db


@pytest.fixture
def feedback_source(organization, workspace, monkeypatch):
    project = Project.objects.create(
        name="Feedback test project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    source = SimpleNamespace(
        id="test-feedback-span",
        pk="test-feedback-span",
        trace_id=str(uuid.uuid4()),
        project_id=str(project.id),
        org_id=str(organization.id),
        parent_span_id="",
        observation_type="agent",
        status="OK",
    )
    reader = MagicMock()
    reader.__enter__.return_value = reader
    reader.__exit__.return_value = False
    reader.get.side_effect = lambda sid, **kwargs: source if sid == source.id else None
    reader.roots_by_trace_ids.side_effect = (
        lambda tids, **kwargs: [source] if source.trace_id in tids else []
    )
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        MagicMock(side_effect=AssertionError("Unexpected external HTTP")),
    )
    return source


def mint(auth_client, source, source_type="span", **kwargs):
    response = auth_client.post(
        MINT_URL,
        {
            "source_type": source_type,
            "source_id": source.trace_id if source_type == "trace" else source.id,
            "key": "test-feedback-quality",
            **kwargs,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    result = response.data["result"]
    assert result["url_path"] == SUBMIT_URL
    assert parse_datetime(result["expires_at"]) is not None
    return result["token"]


def test_mint_requires_auth(feedback_source):
    response = APIClient().post(
        MINT_URL,
        {"source_type": "span", "source_id": feedback_source.id, "key": "test-key"},
        format="json",
    )
    assert response.status_code in (401, 403)
    assert not Score.no_workspace_objects.exists()


def test_mint_refuses_other_organization(auth_client, feedback_source):
    foreign_org = Organization.objects.create(name="Other feedback test organization")
    foreign_project = Project.no_workspace_objects.create(
        name="Other feedback test project",
        organization=foreign_org,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    feedback_source.project_id = str(foreign_project.id)
    feedback_source.org_id = str(foreign_org.id)
    response = auth_client.post(
        MINT_URL,
        {"source_type": "span", "source_id": feedback_source.id, "key": "test-key"},
        format="json",
    )
    assert response.status_code == 400
    assert not Score.no_workspace_objects.exists()


@pytest.mark.parametrize("source_type", ["trace", "span"])
def test_public_submission_creates_bound_score(auth_client, feedback_source, source_type):
    token = mint(auth_client, feedback_source, source_type)
    response = APIClient(enforce_csrf_checks=True).post(
        SUBMIT_URL, {"token": token, "score": 0, "comment": "Test feedback"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert response.data == {"status": True, "result": {"accepted": True}}
    score = Score.no_workspace_objects.get()
    assert score.source_type == ("trace" if source_type == "trace" else "observation_span")
    assert str(score.get_source_id()) == (
        feedback_source.trace_id if source_type == "trace" else feedback_source.id
    )
    assert str(score.trace_id) == feedback_source.trace_id
    assert str(score.tracer_project_id) == feedback_source.project_id
    assert score.label.name == "test-feedback-quality"
    assert score.value == {"value": 0}
    assert score.notes == "Test feedback"
    assert score.annotator_id is None
    assert score.score_source == "end_user"
    assert score.queue_item_id is not None


def test_public_value_and_score_are_preserved(auth_client, feedback_source):
    token = mint(auth_client, feedback_source)
    response = APIClient().post(
        SUBMIT_URL, {"token": token, "score": 1, "value": "up"}, format="json"
    )
    assert response.status_code == 200, response.content
    assert Score.no_workspace_objects.get().value == {"score": 1, "value": "up"}


def test_separate_tokens_collect_separate_feedback(auth_client, feedback_source):
    tokens = [mint(auth_client, feedback_source) for _ in range(2)]
    for token in tokens:
        response = APIClient().post(
            SUBMIT_URL, {"token": token, "value": {"rating": 4}}, format="json"
        )
        assert response.status_code == 200, response.content
    assert Score.no_workspace_objects.count() == 2


def test_tampered_token_is_rejected(auth_client, feedback_source):
    token = mint(auth_client, feedback_source)
    response = APIClient().post(
        SUBMIT_URL, {"token": token + "tampered", "score": 1}, format="json"
    )
    assert response.status_code in (400, 401)
    assert not Score.no_workspace_objects.exists()


def test_wrong_signing_key_is_rejected(auth_client, feedback_source, settings):
    token = mint(auth_client, feedback_source)
    settings.SECRET_KEY = "test-only-different-signing-key"
    settings.SECRET_KEY_FALLBACKS = []
    response = APIClient().post(
        SUBMIT_URL, {"token": token, "score": 1}, format="json"
    )
    assert response.status_code in (400, 401)
    assert not Score.no_workspace_objects.exists()


def test_expired_token_is_rejected(auth_client, feedback_source, monkeypatch):
    token = mint(auth_client, feedback_source, expires_in=1)
    future = time.time() + 2
    monkeypatch.setattr(signing.time, "time", lambda: future)
    response = APIClient().post(
        SUBMIT_URL, {"token": token, "score": 1}, format="json"
    )
    assert response.status_code in (400, 401)
    assert not Score.no_workspace_objects.exists()


def test_missing_score_is_rejected(auth_client, feedback_source):
    token = mint(auth_client, feedback_source)
    response = APIClient().post(
        SUBMIT_URL, {"token": token, "comment": "Test comment only"}, format="json"
    )
    assert response.status_code == 400
    assert not Score.no_workspace_objects.exists()


def test_feedback_key_cannot_be_overridden(auth_client, feedback_source):
    token = mint(auth_client, feedback_source)
    response = APIClient().post(
        SUBMIT_URL, {"token": token, "score": 1, "key": "other-test-key"}, format="json"
    )
    assert response.status_code == 400
    assert not Score.no_workspace_objects.exists()


def test_token_grants_no_read_access(auth_client, feedback_source):
    token = mint(auth_client, feedback_source)
    client = APIClient()
    assert client.get(SUBMIT_URL, {"token": token}).status_code == 405
    assert client.get("/model-hub/scores/", {"token": token}).status_code in (401, 403)


@pytest.mark.parametrize("expires_in", [0, -1, 604801])
def test_mint_rejects_invalid_lifetime(auth_client, feedback_source, expires_in):
    response = auth_client.post(
        MINT_URL,
        {
            "source_type": "span",
            "source_id": feedback_source.id,
            "key": "test-key",
            "expires_in": expires_in,
        },
        format="json",
    )
    assert response.status_code == 400


def test_token_is_single_use(auth_client, feedback_source):
    token = mint(auth_client, feedback_source)
    client = APIClient()
    first = client.post(SUBMIT_URL, {"token": token, "score": 1}, format="json")
    assert first.status_code == 200, first.content
    second = client.post(SUBMIT_URL, {"token": token, "score": 0}, format="json")
    assert second.status_code == 400
    assert Score.no_workspace_objects.count() == 1
    assert Score.no_workspace_objects.get().value == {"value": 1}
