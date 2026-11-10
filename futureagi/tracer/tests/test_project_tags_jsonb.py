"""Raw JSON tag input must be validated before reaching PostgreSQL jsonb."""

import json
import uuid
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from tracer.models.project import Project

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.integration,
    pytest.mark.api,
]


@pytest.fixture
def tag_endpoint(organization, workspace, user, settings):
    assert connection.vendor == "postgresql"
    settings.DEBUG = False
    settings.DEBUG_PROPAGATE_EXCEPTIONS = False
    project = Project.objects.create(
        name="Synthetic tags project",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
        tags=["original"],
    )
    key = OrgApiKey.objects.create(
        name="Synthetic tags key",
        organization=organization,
        workspace=workspace,
        user=user,
        type="user",
        enabled=True,
        api_key=uuid.uuid4().hex,
        secret_key=uuid.uuid4().hex,
    )
    client = APIClient(raise_request_exception=False)
    client.credentials(
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client, project, f"/tracer/project/{project.id}/tags/"


def persisted_state(project):
    project.refresh_from_db()
    return project.tags, project.updated_at


@pytest.mark.parametrize(
    "raw_body",
    [
        pytest.param(b'{"tags":["\\u0000"]}', id="escaped-nul"),
        pytest.param(b'{"tags":["\\ud800"]}', id="unpaired-high-surrogate"),
        pytest.param(b'{"tags":["\\udc00"]}', id="unpaired-low-surrogate"),
        pytest.param(b'{"tags":[["\\u0000"]]}', id="nested-nul"),
        pytest.param(b'{"tags":[{"\\ud800":"value"}]}', id="surrogate-key"),
    ],
)
def test_database_incompatible_tag_returns_400_without_mutation(tag_endpoint, raw_body):
    client, project, url = tag_endpoint
    # These are syntactically valid JSON bodies, accepted by the real parser.
    assert isinstance(json.loads(raw_body)["tags"], list)
    before = persisted_state(project)

    response = client.patch(url, data=raw_body, content_type="application/json")

    assert persisted_state(project) == before
    assert response.status_code == 400
    assert response["Content-Type"].startswith("application/json")
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "validation_error"
    assert body["code"]
    assert body["detail"] == body["message"] == body["result"]
    assert response.exc_info is None


@pytest.mark.parametrize(
    ("raw_body", "expected"),
    [
        pytest.param(b'{"tags":["release","blue"]}', ["release", "blue"], id="ascii"),
        pytest.param(
            b'{"tags":["caf\\u00e9","\\u65e5\\u672c\\u8a9e","\\ud83d\\ude80","line\\nbreak"]}',
            ["café", "日本語", "\U0001f680", "line\nbreak"],
            id="valid-unicode-and-controls",
        ),
        pytest.param(b'{"tags":["\\\\u0000"]}', [r"\u0000"], id="literal-escape"),
        pytest.param(b'{"tags":[]}', [], id="clear-tags"),
        pytest.param(
            b'{"tags":[{"label":["release",1,true,null]}]}',
            [{"label": ["release", 1, True, None]}],
            id="existing-json-list-values",
        ),
    ],
)
def test_valid_tag_round_trip(tag_endpoint, raw_body, expected):
    client, project, url = tag_endpoint
    before = persisted_state(project)

    response = client.patch(url, data=raw_body, content_type="application/json")

    assert response.status_code == 200
    assert response.json()["result"]["tags"] == expected
    tags, updated_at = persisted_state(project)
    assert tags == expected
    assert updated_at > before[1]
    assert response.exc_info is None


def nested_tag_body(raw_leaf, depth, container):
    opening, closing = (b"[", b"]") if container == "array" else (b'{"label":', b"}")
    return b'{"tags":[' + opening * depth + raw_leaf + closing * depth + b"]}"


@pytest.mark.parametrize("depth", [600, 800])
@pytest.mark.parametrize("container", ["array", "object"])
def test_deep_valid_tag_round_trip(tag_endpoint, depth, container):
    client, project, url = tag_endpoint
    raw_body = nested_tag_body(b'["release",1,true,null]', depth, container)
    # Compare serialized values so the test's equality check does not recurse.
    expected_json = json.dumps(json.loads(raw_body)["tags"])
    before = persisted_state(project)

    response = client.patch(url, data=raw_body, content_type="application/json")

    assert response.status_code == 200
    assert json.dumps(response.json()["result"]["tags"]) == expected_json
    tags, updated_at = persisted_state(project)
    assert json.dumps(tags) == expected_json
    assert updated_at > before[1]
    assert response.exc_info is None


@pytest.mark.parametrize("depth", [600, 800])
@pytest.mark.parametrize("container", ["array", "object"])
@pytest.mark.parametrize(
    "raw_leaf",
    [
        pytest.param(b'"\\u0000"', id="nul-value"),
        pytest.param(b'"\\ud800"', id="high-surrogate-value"),
        pytest.param(b'"\\udc00"', id="low-surrogate-value"),
        pytest.param(b'{"\\u0000":"value"}', id="nul-key"),
        pytest.param(b'{"\\ud800":"value"}', id="high-surrogate-key"),
        pytest.param(b'{"\\udc00":"value"}', id="low-surrogate-key"),
    ],
)
def test_deep_invalid_tag_returns_400_without_mutation(
    tag_endpoint, depth, container, raw_leaf
):
    client, project, url = tag_endpoint
    raw_body = nested_tag_body(raw_leaf, depth, container)
    assert isinstance(json.loads(raw_body)["tags"], list)
    before = persisted_state(project)

    response = client.patch(url, data=raw_body, content_type="application/json")

    assert persisted_state(project) == before
    assert response.status_code == 400
    assert response["Content-Type"].startswith("application/json")
    body = response.json()
    assert body["status"] is False
    assert body["type"] == "validation_error"
    assert body["code"]
    assert body["detail"] == body["message"] == body["result"]
    assert response.exc_info is None


@pytest.mark.parametrize("raw_body", [b"[]", b"{}", b'{"tags":"release"}'])
def test_existing_shape_validation_remains_400(tag_endpoint, raw_body):
    client, project, url = tag_endpoint
    before = persisted_state(project)

    response = client.patch(url, data=raw_body, content_type="application/json")

    assert response.status_code == 400
    assert response.json()["type"] == "validation_error"
    assert persisted_state(project) == before
    assert response.exc_info is None


def test_operational_database_failure_remains_503(tag_endpoint):
    client, project, url = tag_endpoint
    before = persisted_state(project)
    with patch.object(
        Project, "save", side_effect=OperationalError("synthetic DB unavailable")
    ) as save:
        response = client.patch(
            url, data=b'{"tags":["release"]}', content_type="application/json"
        )

    save.assert_called_once()
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert "synthetic DB unavailable" not in response.content.decode()
    assert persisted_state(project) == before
    assert response.exc_info is None
