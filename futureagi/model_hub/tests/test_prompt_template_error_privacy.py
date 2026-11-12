"""B24: prompt reads keep backend exception details out of HTTP responses."""

import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError, OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.exceptions import ErrorDetail, NotFound
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.views import prompt_template as prompt_views
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.utils.general_methods import GeneralMethods

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]

SENTINEL = "db-host.internal:5432 constraint uq_secret_key key=private-value"
GM = GeneralMethods()


@pytest.fixture
def request_prompt(organization, workspace, user):
    """Exercise real scoped lookups and pagination through DRF dispatch."""

    def get(action, pk, query=None):
        suffix = "versions/" if action == "versions" else ""
        request = APIRequestFactory().get(
            f"/model-hub/prompt-templates/{pk}/{suffix}", query or {}
        )
        request.organization = organization
        request.workspace = workspace
        force_authenticate(request, user=user)
        set_workspace_context(
            organization=organization,
            workspace=workspace,
            user=user,
            require_workspace=True,
        )
        try:
            return prompt_views.PromptTemplateViewSet.as_view({"get": action})(
                request, pk=str(pk)
            )
        finally:
            clear_workspace_context()

    return get


def _assert_private_response(response, expected, exception):
    assert response.status_code == expected.status_code, response.data
    body = response.render().content.decode()
    assert str(exception) not in body
    for fragment in str(exception).split():
        assert fragment not in body
    assert response.data == expected.data


@pytest.mark.parametrize(
    "error_type", [IntegrityError, OperationalError], ids=["integrity", "operational"]
)
def test_retrieve_version_creation_error_is_private(
    request_prompt, prompt_template, error_type
):
    assert not PromptVersion.objects.filter(original_template=prompt_template).exists()
    error = error_type(SENTINEL)
    with (
        patch.object(PromptVersion.objects, "create", side_effect=error) as create,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = request_prompt("retrieve", prompt_template.id)

    create.assert_called_once()
    assert create.call_args.kwargs["original_template"] == prompt_template
    assert create.call_args.kwargs["is_draft"] is True
    log_exception.assert_called_once_with(f"Error retrieving prompt template: {error}")
    _assert_private_response(
        response, GM.bad_request("Failed to retrieve template"), error
    )


def test_retrieve_cache_error_is_private(request_prompt, prompt_version):
    error = RedisConnectionError(SENTINEL)
    with (
        patch.object(prompt_views.cache, "get", side_effect=error) as cache_get,
        patch.object(PromptVersion.objects, "create") as create,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = request_prompt("retrieve", prompt_version.original_template_id)

    create.assert_not_called()
    cache_get.assert_called_once_with(
        f"prompt_template_{prompt_version.original_template_id}_v1_run_prompt_0"
    )
    log_exception.assert_called_once_with(f"Error retrieving prompt template: {error}")
    _assert_private_response(
        response, GM.bad_request("Failed to retrieve template"), error
    )


# B24-R2 restores the declared integer >= 1 runtime request contract:
# abc/0/-1 are validation errors, not the paginator's out-of-range 404.
@pytest.mark.parametrize("page", ["999999"])
def test_versions_invalid_page_is_fixed_not_found(request_prompt, prompt_version, page):
    response = request_prompt(
        "versions", prompt_version.original_template_id, {"page": page}
    )

    assert response.status_code == 404, response.data
    assert response.data == GM.not_found("Invalid page.").data
    body = response.render().content.decode()
    assert "That page contains no results" not in body
    assert "That page number is not an integer" not in body
    assert "That page number is less than 1" not in body


@pytest.mark.parametrize(
    "page, message",
    [
        ("abc", "A valid integer is required."),
        ("0", "Ensure this value is greater than or equal to 1."),
        ("-1", "Ensure this value is greater than or equal to 1."),
        ("last", "A valid integer is required."),
    ],
    ids=["abc", "zero", "negative", "last"],
)
def test_versions_malformed_page_keeps_declared_validation_error(
    request_prompt, prompt_version, page, message
):
    with patch.object(
        prompt_views.PromptTemplateViewSet, "paginate_queryset"
    ) as paginate:
        response = request_prompt(
            "versions", prompt_version.original_template_id, {"page": page}
        )

    assert response.status_code == 400, response.data
    code = "min_value" if page in {"0", "-1"} else "invalid"
    expected = GM.bad_request({"page": [ErrorDetail(message, code=code)]})
    assert response.data == expected.data
    paginate.assert_not_called()


def test_versions_paginator_not_found_detail_is_private(
    request_prompt, prompt_template
):
    error = NotFound(SENTINEL)
    with patch.object(
        prompt_views.PromptTemplateViewSet, "paginate_queryset", side_effect=error
    ) as paginate:
        response = request_prompt("versions", prompt_template.id)

    paginate.assert_called_once()
    _assert_private_response(response, GM.not_found("Invalid page."), error)


@pytest.mark.parametrize("failure_point", ["query", "pagination"])
def test_versions_internal_error_is_private(
    request_prompt, prompt_template, failure_point
):
    error = OperationalError(SENTINEL)
    target, method = (
        (PromptVersion.objects, "filter")
        if failure_point == "query"
        else (prompt_views.PromptTemplateViewSet, "paginate_queryset")
    )
    with (
        patch.object(target, method, side_effect=error) as fail,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = request_prompt("versions", prompt_template.id)

    fail.assert_called_once()
    log_exception.assert_called_once_with(f"Error in versions method: {error}")
    _assert_private_response(response, GM.internal_server_error_response(), error)


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize(
    "action, expected_status", [("retrieve", 400), ("versions", 404)]
)
def test_missing_and_foreign_templates_are_indistinguishable(
    request_prompt, organization, user, scope, action, expected_status
):
    foreign_org = (
        Organization.objects.create(name="B24 foreign organization")
        if scope == "organization"
        else organization
    )
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="B24 foreign workspace",
        organization=foreign_org,
        created_by=user,
        is_default=False,
    )
    foreign = PromptTemplate.no_workspace_objects.create(
        name="B24 inaccessible template",
        organization=foreign_org,
        workspace=foreign_workspace,
        created_by=user,
    )
    missing_id = uuid.uuid4()
    assert not PromptTemplate.no_workspace_objects.filter(pk=missing_id).exists()
    assert PromptTemplate.no_workspace_objects.filter(pk=foreign.pk).exists()

    with (
        patch.object(PromptVersion.objects, "create") as create,
        patch.object(prompt_views.cache, "get") as cache_get,
    ):
        missing_response = request_prompt(action, missing_id)
        foreign_response = request_prompt(action, foreign.id)

    assert (
        missing_response.status_code == foreign_response.status_code == expected_status
    )
    assert missing_response.data == foreign_response.data
    assert missing_response.render().content == foreign_response.render().content
    if action == "versions":
        assert missing_response.data == GM.not_found("Prompt template not found").data
    create.assert_not_called()
    cache_get.assert_not_called()


def test_retrieve_success_still_returns_cached_output(request_prompt, prompt_version):
    with patch.object(
        prompt_views.cache, "get", return_value={"response": {"text": "cached output"}}
    ) as cache_get:
        response = request_prompt("retrieve", prompt_version.original_template_id)

    assert response.status_code == 200, response.data
    assert response.data["id"] == str(prompt_version.original_template_id)
    assert response.data["version"] == "v1"
    assert response.data["output"] == [{"text": "cached output"}]
    cache_get.assert_called_once()


def test_versions_success_still_returns_a_page(request_prompt, prompt_version):
    response = request_prompt(
        "versions", prompt_version.original_template_id, {"page": "1", "limit": "1"}
    )

    assert response.status_code == 200, response.data
    assert response.data["count"] == 1
    assert response.data["current_page"] == 1
    assert [row["id"] for row in response.data["results"]] == [str(prompt_version.id)]


def test_versions_invalid_limit_keeps_validation_error(request_prompt, prompt_template):
    response = request_prompt("versions", prompt_template.id, {"limit": "abc"})

    assert response.status_code == 400, response.data
    assert "limit" in response.data["details"]
