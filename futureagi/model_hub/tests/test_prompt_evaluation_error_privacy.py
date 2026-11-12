"""Fixed public errors for evaluation configuration and upload response paths."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import OperationalError
from django.db.models import Q
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

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
from model_hub.utils.workspace_scope import explicit_workspace_filter
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


def test_get_evaluation_configs_error_is_private(
    call_prompt_handler, prompt_template, workspace
):
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

    lookup.assert_called_once_with(
        Q(eval_template__owner=OwnerChoices.SYSTEM.value)
        | (
            Q(
                eval_template__owner=OwnerChoices.USER.value,
                eval_template__organization=prompt_template.organization,
            )
            & explicit_workspace_filter(workspace, "eval_template__workspace")
        ),
        prompt_template=prompt_template,
        deleted=False,
    )
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


@pytest.mark.parametrize(
    "error_type,expected_status",
    [
        (OperationalError, 400),
        (RuntimeError, 400),
        (prompt_views.PromptFolder.DoesNotExist, 404),
    ],
    ids=["database-error", "unexpected-error", "missing-folder"],
)
def test_folder_lookup_error_categories_remain_private(
    call_prompt_handler, error_type, expected_status
):
    """Missing is 404; infrastructure failures keep the original private 400."""
    error = error_type(SENTINEL)
    with (
        patch.object(
            prompt_views.PromptFolder.no_workspace_objects, "get", side_effect=error
        ) as lookup,
        patch.object(prompt_views.PromptTemplate.objects, "create") as create,
        patch.object(prompt_views.logger, "exception") as log_exception,
    ):
        response = call_prompt_handler(
            "create_draft",
            {"name": "Private prompt", "prompt_folder": str(uuid4())},
        )

    lookup.assert_called_once()
    create.assert_not_called()
    expected = (
        GM.not_found("Prompt folder not found")
        if expected_status == 404
        else GM.bad_request("Prompt folder not found")
    )
    _assert_private(
        response,
        expected,
        error,
        log_exception,
        "Error retrieving prompt folder for draft",
    )


@pytest.fixture
def playground_tenant_scope(
    urlconf_loaded_without_workspace_context, user, organization, workspace
):
    """Exercise real API-key authentication, membership and URL dispatch."""
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import OrgApiKey
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization
    ).update(role=OrganizationRoles.MEMBER, level=Level.MEMBER, is_active=True)
    user.organization_role = OrganizationRoles.MEMBER
    user.save(update_fields=["organization_role"])
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    other_workspace = Workspace.no_workspace_objects.create(
        organization=organization,
        name="TI-01 other workspace",
        is_active=True,
        is_default=False,
        created_by=user,
    )
    foreign_org = Organization.objects.create(name="TI-01 foreign organization")
    foreign_workspace = Workspace.no_workspace_objects.create(
        organization=foreign_org,
        name="TI-01 foreign workspace",
        is_active=True,
        is_default=False,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace__in=[other_workspace, foreign_workspace]
    ).delete()
    OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=foreign_org
    ).delete()
    assert user.can_access_workspace(workspace)
    assert not user.can_access_workspace(other_workspace)
    assert not user.can_access_workspace(foreign_workspace)
    # synthetic test value
    public_key = f"FAKE-ti01-public-{user.pk}"
    # synthetic test value
    secret_key = "FAKE-ti01-secret-abcdefghijklmnopqrstuvwxyz"
    key = OrgApiKey.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        name="TI-01 synthetic key",
        type="user",
        enabled=True,
        api_key=public_key,
        secret_key=secret_key,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=public_key, HTTP_X_SECRET_KEY=secret_key)

    def make_template(boundary="own", *, eval_type="code", owner="user"):
        space = {
            "own": workspace,
            "workspace": other_workspace,
            "organization": foreign_workspace,
        }[boundary]
        return prompt_views.EvalTemplate.no_workspace_objects.create(
            name=f"ti-01-{boundary}-{eval_type}",
            organization=space.organization,
            workspace=space,
            owner=owner,
            eval_type=eval_type,
            config={
                "eval_type_id": "ti01_synthetic_function",
                "function_eval": eval_type == "code",
                "required_keys": ["input"],
                "optional_keys": [],
                "output": "Pass/Fail",
                "config": {},
            },
        )

    try:
        yield SimpleNamespace(
            user=user,
            organization=organization,
            workspace=workspace,
            client=client,
            key=key,
            make_template=make_template,
        )
    finally:
        clear_workspace_context()


@pytest.fixture
def off_cloud_without_turing(monkeypatch):
    """Set deployment inputs, not the real capability or entitlement decision."""
    from tfc import ee_gates
    from tfc.capabilities import service
    from tfc.licensing.types import DeploymentFlavor, DeploymentLocation

    monkeypatch.setattr(service, "_configured", True)
    monkeypatch.setattr(service, "_deployment_flavor", DeploymentFlavor.OSS)
    monkeypatch.setattr(service, "_deployment_location", DeploymentLocation.SELF_HOSTED)
    monkeypatch.setattr(service, "_license_resolver", None)
    monkeypatch.setattr(service, "_cloud_plan_resolver", None)
    assert not service.check("turing_models").allowed
    assert ee_gates.turing_oss_gate_response("turing_large").status_code == 402


def _playground_with_default_model(scope, template_id):
    from accounts.authentication import APIKeyAuthentication
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    try:
        # Omit model: the real serializer must supply turing_large, not None.
        response = scope.client.post(
            "/model-hub/eval-playground/",
            {"template_id": str(template_id), "mapping": {"input": "synthetic"}},
            format="json",
            HTTP_X_WORKSPACE_ID=str(scope.workspace.pk),
            HTTP_X_ORGANIZATION_ID=str(scope.organization.pk),
        )
        request = response.renderer_context["request"]
        assert type(request.successful_authenticator) is APIKeyAuthentication
        assert request.org_api_key.pk == scope.key.pk
        assert request.user.pk == scope.user.pk
        assert request.organization.pk == scope.organization.pk
        assert request.workspace.pk == scope.workspace.pk
        assert request.validated_data["model"] == "turing_large"
        return response
    finally:
        clear_workspace_context()


@pytest.mark.parametrize("boundary", ["workspace", "organization"])
@pytest.mark.parametrize("eval_type", ["code", "llm"])
def test_playground_default_model_hides_foreign_template_before_entitlement(
    playground_tenant_scope, off_cloud_without_turing, boundary, eval_type
):
    from model_hub.views import separate_evals

    scope = playground_tenant_scope
    foreign = scope.make_template(boundary, eval_type=eval_type)
    with patch.object(separate_evals, "run_eval_func", autospec=True) as execute:
        missing = _playground_with_default_model(scope, uuid4())
        denied = _playground_with_default_model(scope, foreign.pk)

    execute.assert_not_called()
    assert missing.status_code == denied.status_code == 400
    assert denied.data == missing.data
    assert foreign.name not in str(denied.data)


@pytest.mark.parametrize("owner", [OwnerChoices.USER.value, OwnerChoices.SYSTEM.value])
def test_playground_default_model_keeps_accessible_code_exception(
    playground_tenant_scope, off_cloud_without_turing, owner
):
    from model_hub.views import separate_evals

    scope = playground_tenant_scope
    template = scope.make_template(owner=owner)
    with patch.object(
        separate_evals,
        "run_eval_func",
        autospec=True,
        return_value={"score": 1, "reason": "synthetic executor boundary"},
    ) as execute:
        response = _playground_with_default_model(scope, template.pk)

    assert response.status_code == 200, response.data
    execute.assert_called_once()
    args, kwargs = execute.call_args
    assert args[2].pk == template.pk
    assert args[3].pk == scope.organization.pk
    assert kwargs["workspace"].pk == scope.workspace.pk
    assert kwargs["model"] == "turing_large"


def test_playground_default_model_still_denies_accessible_llm_without_turing(
    playground_tenant_scope, off_cloud_without_turing
):
    from model_hub.views import separate_evals
    from tfc import ee_gates

    scope = playground_tenant_scope
    template = scope.make_template(eval_type="llm")
    with patch.object(separate_evals, "run_eval_func", autospec=True) as execute:
        response = _playground_with_default_model(scope, template.pk)

    execute.assert_not_called()
    expected = ee_gates.turing_oss_gate_response("turing_large")
    assert response.status_code == expected.status_code == 402
    assert response.data == expected.data
