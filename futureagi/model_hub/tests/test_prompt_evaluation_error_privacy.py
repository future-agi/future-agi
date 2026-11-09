"""Fixed public errors for evaluation configuration and upload response paths."""

from unittest.mock import patch

import pytest
from django.db import OperationalError
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import OwnerChoices
from model_hub.tests.network_isolation import (
    no_external_lookups as no_external_lookups,
)
from model_hub.tests.test_prompt_handler_error_privacy import (
    GM,
    SENTINEL,
    _assert_private,
)
from model_hub.tests.test_prompt_handler_error_privacy import (
    call_prompt_handler as _call_prompt_handler,
)
from model_hub.views import prompt_template as prompt_views

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]
call_prompt_handler = _call_prompt_handler


@pytest.fixture
def evaluation_template(organization):
    return prompt_views.EvalTemplate.no_workspace_objects.create(
        name="Privacy evaluation",
        organization=organization,
        owner=OwnerChoices.SYSTEM.value,
        config={
            "eval_type_id": "RecallAtK",
            "function_eval": True,
            "required_keys": ["hypothesis", "reference"],
            "output": "score",
        },
    )


def test_get_evaluation_configs_error_is_private(call_prompt_handler, prompt_template):
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptEvalConfig.objects, "filter", side_effect=error
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "get_evaluation_configs", method="get", pk=prompt_template.id
        )

    lookup.assert_called_once_with(prompt_template=prompt_template, deleted=False)
    _assert_private(
        response,
        GM.bad_request("Failed to fetch evaluation configurations"),
        error,
        log_exception,
        "Error fetching evaluation configs",
    )


def test_update_evaluation_configs_error_is_private(
    call_prompt_handler, prompt_template, evaluation_template
):
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.EvalTemplate.no_workspace_objects, "get", side_effect=error
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "update_evaluation_configs",
            {"id": str(evaluation_template.id), "name": "Privacy evaluation"},
            pk=prompt_template.id,
        )

    lookup.assert_called_once()
    _assert_private(
        response,
        GM.bad_request("Failed to update evaluation configurations"),
        error,
        log_exception,
        "Error updating evaluation configs",
    )


def test_update_evaluation_configs_start_error_is_private(
    call_prompt_handler, prompt_version, evaluation_template
):
    error = OperationalError(SENTINEL)
    template = prompt_version.original_template
    with (
        patch.object(
            prompt_views._PROMPT_TEMPLATE_EXECUTOR, "submit", side_effect=error
        ) as submit,
        patch.object(prompt_views, "track_running_eval_count"),
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "update_evaluation_configs",
            {
                "id": str(evaluation_template.id),
                "name": "Privacy evaluation",
                "is_run": True,
                "version_to_run": [prompt_version.template_version],
            },
            pk=template.id,
        )

    submit.assert_called_once()
    saved = prompt_views.PromptEvalConfig.no_workspace_objects.get(
        prompt_template=template, name="Privacy evaluation"
    )
    _assert_private(
        response,
        GM.success_response(
            {
                "message": "Evaluation configuration updated successfully but failed to start evaluation",
                "prompt_eval_config_id": str(saved.id),
                "error": "Failed to start evaluation",
            }
        ),
        error,
        log_exception,
        "Error starting evaluation after config creation",
    )


def test_delete_evaluation_config_error_is_private(
    call_prompt_handler, prompt_template
):
    error = OperationalError(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptTemplate.objects, "get", side_effect=error
        ) as lookup,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "delete_evaluation_config", method="delete", pk=prompt_template.id
        )

    lookup.assert_called_once()
    _assert_private(
        response,
        GM.bad_request("Failed to delete evaluation configuration"),
        error,
        log_exception,
        "Error deleting evaluation config",
    )


@pytest.fixture
def call_upload(user, organization, workspace):
    def call():
        request = APIRequestFactory().post(
            "/model-hub/upload-file/",
            {"type": "image", "links": ["https://example.test/image.png"]},
            format="json",
        )
        request.organization = organization
        request.workspace = workspace
        force_authenticate(request, user=user)
        return prompt_views.UploadFileView.as_view()(request)

    return call


def test_upload_item_error_is_private(call_upload, organization):
    error = OperationalError(SENTINEL)
    with (
        patch.object(prompt_views, "upload_image_to_s3", side_effect=error) as upload,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_upload()

    upload.assert_called_once_with(
        "https://example.test/image.png",
        bucket_name="fi-customer-data-dev",
        org_id=str(organization.id),
    )
    _assert_private(
        response,
        GM.success_response(
            [{"url": None, "error": "Failed to upload file.", "file_name": None}]
        ),
        error,
        log_exception,
        "Error uploading file",
    )


def test_evaluation_config_success_is_unchanged(
    call_prompt_handler, prompt_template, evaluation_template
):
    with (
        patch.object(prompt_views.logger, "exception") as log_exception,
        patch.object(prompt_views._PROMPT_TEMPLATE_EXECUTOR, "submit") as submit,
    ):
        response = call_prompt_handler(
            "update_evaluation_configs",
            {"id": str(evaluation_template.id), "name": "Privacy evaluation"},
            pk=prompt_template.id,
        )
        saved = prompt_views.PromptEvalConfig.no_workspace_objects.get(
            prompt_template=prompt_template, name="Privacy evaluation"
        )
        listing = call_prompt_handler(
            "get_evaluation_configs", method="get", pk=prompt_template.id
        )

    assert response.status_code == 200, response.data
    assert (
        response.data
        == GM.success_response(
            {
                "message": "Evaluation configuration updated successfully",
                "prompt_eval_config_id": str(saved.id),
            }
        ).data
    )
    assert listing.status_code == 200, listing.data
    configs = listing.data["result"]["evaluation_configs"]
    assert len(configs) == 1
    assert configs[0]["id"] == str(saved.id)
    assert configs[0]["name"] == "Privacy evaluation"
    submit.assert_not_called()
    log_exception.assert_not_called()


def test_upload_success_is_unchanged(call_upload):
    url = "https://example.test/stored-image.png"
    with (
        patch.object(prompt_views, "upload_image_to_s3", return_value=url) as upload,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_upload()

    assert response.status_code == 200, response.data
    assert (
        response.data
        == GM.success_response([{"url": url, "file_name": "image.png"}]).data
    )
    upload.assert_called_once()
    log_exception.assert_not_called()
