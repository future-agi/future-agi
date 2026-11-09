"""PAGE-CAP-DSX-R2: prompt handlers log exceptions without returning their text."""

from unittest.mock import patch

import pytest
from django.db import OperationalError
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.run_prompt import PromptTemplate
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
def call_prompt_handler(organization, workspace, user):
    """Dispatch through DRF with the real workspace and authenticated user."""

    def call(action, data=None, *, method="post", pk=None, execution=False):
        request = getattr(APIRequestFactory(), method)(
            "/model-hub/prompt-templates/", data or {}, format="json"
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
        view_class = (
            prompt_views.PromptExecutionViewSet
            if execution
            else prompt_views.PromptTemplateViewSet
        )
        try:
            return view_class.as_view({method: action})(
                request, **({"pk": str(pk)} if pk is not None else {})
            )
        finally:
            clear_workspace_context()

    return call


def _assert_private(response, expected, error, log_exception, log_prefix):
    assert response.status_code == expected.status_code, response.data
    body = response.render().content.decode()
    assert SENTINEL not in body
    assert str(error) not in body
    for fragment in str(error).split():
        assert fragment not in body
    assert response.data == expected.data
    log_exception.assert_called_once_with(f"{log_prefix}: {error}")


def test_get_template_by_name_error_is_private(call_prompt_handler):
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptTemplateViewSet, "get_queryset", side_effect=error
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "get_template_by_name", {"name": "Private prompt"}, method="get"
        )

    lookup.assert_called_once_with()
    _assert_private(
        response,
        GM.bad_request("Failed to retrieve template"),
        error,
        log_exception,
        "Error retrieving prompt template",
    )


def test_create_draft_folder_error_is_private(call_prompt_handler):
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptFolder.no_workspace_objects, "get", side_effect=error
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "create_draft",
            {"name": "Private prompt", "prompt_folder": "unavailable-folder"},
        )

    lookup.assert_called_once()
    _assert_private(
        response,
        GM.bad_request("Prompt folder not found"),
        error,
        log_exception,
        "Error retrieving prompt folder for draft",
    )


def test_create_draft_base_template_error_is_private(call_prompt_handler):
    # The sibling lookup in this same handler must not retain the same leak.
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptBaseTemplate.no_workspace_objects,
            "get",
            side_effect=error,
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "create_draft",
            {"name": "Private prompt", "prompt_base_template": "unavailable-base"},
        )

    lookup.assert_called_once()
    _assert_private(
        response,
        GM.bad_request("Prompt base template not found"),
        error,
        log_exception,
        "Error retrieving prompt base template for draft",
    )


def test_create_draft_creation_error_is_private(call_prompt_handler):
    error = OperationalError(SENTINEL)
    with (
        patch.object(PromptTemplate.objects, "create", side_effect=error) as create,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "create_draft", {"name": "Private prompt", "prompt_config": [{}]}
        )

    create.assert_called_once()
    _assert_private(
        response,
        GM.bad_request("Failed to create draft"),
        error,
        log_exception,
        "Error in creation of draft",
    )


def test_run_evals_on_multiple_versions_error_is_private(
    call_prompt_handler, prompt_template
):
    error = OperationalError(SENTINEL)
    with (
        patch.object(PromptTemplate.objects, "get", side_effect=error) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
        patch.object(prompt_views, "submit_with_retry") as submit,
    ):
        response = call_prompt_handler(
            "run_evals_on_multiple_versions", pk=prompt_template.id
        )

    lookup.assert_called_once()
    submit.assert_not_called()
    _assert_private(
        response,
        GM.bad_request("Failed to start evaluation job"),
        error,
        log_exception,
        "Error starting evaluation job",
    )


def test_bulk_delete_error_is_private(call_prompt_handler, prompt_template):
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptTemplateViewSet, "perform_destroy", side_effect=error
        ) as destroy,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "bulk_delete", {"ids": [str(prompt_template.id)]}
        )

    destroy.assert_called_once_with(prompt_template)
    _assert_private(
        response,
        GM.internal_server_error_response(),
        error,
        log_exception,
        "Error in bulk_delete method",
    )


def test_generate_variables_error_is_private(call_prompt_handler):
    error = OperationalError(SENTINEL)
    with (
        patch.object(prompt_views, "SyntheticDataAgent") as agent_class,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        generate = agent_class.return_value.generate_and_validate
        generate.side_effect = error
        response = call_prompt_handler(
            "generate_variables",
            {"prompt_name": "Private prompt", "variable_names": ["topic"]},
        )

    agent_class.assert_called_once_with()
    generate.assert_called_once_with(
        {
            "prompt_name": "Private prompt",
            "variable_names": ["topic"],
            "batch_size": 1,
            "generation_type": "prompt",
        }
    )
    _assert_private(
        response,
        GM.bad_request("Failed to generate variable values"),
        error,
        log_exception,
        "Error in generating variable values",
    )


def test_save_name_error_is_private(call_prompt_handler, prompt_template):
    error = OperationalError(SENTINEL)
    with (
        patch.object(PromptTemplate, "save", side_effect=error) as save,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "save_name", {"name": "Renamed prompt"}, pk=prompt_template.id
        )

    save.assert_called_once_with()
    _assert_private(
        response,
        GM.bad_request("Failed to save template name"),
        error,
        log_exception,
        "Error in saving template name",
    )


def test_save_prompt_folder_error_is_private(call_prompt_handler, prompt_template):
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptFolder.no_workspace_objects, "filter", side_effect=error
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "save_prompt_folder",
            {"prompt_folder_id": "unavailable-folder"},
            pk=prompt_template.id,
        )

    lookup.assert_called_once()
    _assert_private(
        response,
        GM.bad_request("Failed to save prompt folder"),
        error,
        log_exception,
        "Error in saving prompt folder",
    )


@pytest.mark.parametrize("combined", [False, True], ids=["templates", "combined"])
def test_prompt_execution_list_error_is_private(call_prompt_handler, combined):
    error = OperationalError(SENTINEL)
    method = "_get_combined_templates_and_folders" if combined else "get_queryset"
    with (
        patch.object(
            prompt_views.PromptExecutionViewSet, method, side_effect=error
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "list", {"send_all": str(combined).lower()}, method="get", execution=True
        )

    lookup.assert_called_once_with()
    _assert_private(
        response,
        GM.bad_request("Failed to retrieve templates"),
        error,
        log_exception,
        "Error in list view",
    )


def test_get_template_by_name_success_is_unchanged(call_prompt_handler, prompt_version):
    template = prompt_version.original_template
    with patch.object(prompt_views.logger, "exception") as log_exception:
        response = call_prompt_handler(
            "get_template_by_name",
            {"name": template.name, "version": prompt_version.template_version},
            method="get",
        )

    assert response.status_code == 200, response.data
    assert response.data["id"] == str(template.id)
    assert response.data["name"] == template.name
    assert response.data["version"] == prompt_version.template_version
    assert response.data["prompt_config"] == [prompt_version.prompt_config_snapshot]
    log_exception.assert_not_called()


def test_save_name_success_is_unchanged(call_prompt_handler, prompt_template):
    with patch.object(prompt_views.logger, "exception") as log_exception:
        response = call_prompt_handler(
            "save_name", {"name": "Renamed prompt"}, pk=prompt_template.id
        )

    assert response.status_code == 200, response.data
    prompt_template.refresh_from_db()
    assert prompt_template.name == "Renamed prompt"
    expected = GM.success_response(
        prompt_views.PromptTemplateSerializer(prompt_template).data
    )
    assert response.data == expected.data
    log_exception.assert_not_called()
