import uuid

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices, StatusType
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import (
    PromptEvalConfig,
    PromptTemplate,
    PromptVersion,
)


def _prompt_config(text="Hello {{name}}"):
    return {
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": "Be concise."}],
            },
            {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        ],
        "configuration": {
            "model": "gpt-4o-mini",
            "model_detail": {"type": "chat"},
            "template_format": "mustache",
        },
    }


def _create_prompt_template(organization, workspace, user, name, folder=None):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        created_by=user,
        prompt_folder=folder,
        variable_names={"name": ["Ada"]},
    )
    version = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot=_prompt_config(),
        variable_names={"name": ["Ada"]},
        is_draft=True,
    )
    return template, version


def _create_prompt_version(
    template,
    template_version,
    *,
    text=None,
    is_default=False,
    is_draft=True,
):
    return PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version=template_version,
        prompt_config_snapshot=_prompt_config(text or f"Hello from {template_version}"),
        variable_names={"name": ["Ada"]},
        is_default=is_default,
        is_draft=is_draft,
    )


def _create_eval_template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name="prompt_eval_config_contract",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        output_type_normalized="pass_fail",
        config={
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["text"],
            "custom_eval": True,
        },
    )


@pytest.mark.django_db
def test_prompt_bulk_delete_stamps_deleted_at_on_template_and_versions(
    auth_client, organization, workspace, user
):
    template, version = _create_prompt_template(
        organization, workspace, user, "Prompt bulk delete contract"
    )

    response = auth_client.post(
        "/model-hub/prompt-templates/bulk-delete/",
        {"ids": [str(template.id)]},
        format="json",
    )

    assert response.status_code == 200
    template.refresh_from_db()
    version.refresh_from_db()
    assert template.deleted is True
    assert template.deleted_at is not None
    assert version.deleted is True
    assert version.deleted_at is not None


@pytest.mark.django_db
def test_prompt_folder_delete_stamps_deleted_at_and_cascades_prompt_versions(
    auth_client, organization, workspace, user
):
    folder = PromptFolder.no_workspace_objects.create(
        name="Prompt folder delete contract",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    template, version = _create_prompt_template(
        organization, workspace, user, "Prompt folder cascade contract", folder
    )

    response = auth_client.delete(f"/model-hub/prompt-folders/{folder.id}/")

    assert response.status_code == 200
    folder.refresh_from_db()
    template.refresh_from_db()
    version.refresh_from_db()
    assert folder.deleted is True
    assert folder.deleted_at is not None
    assert template.deleted is True
    assert template.deleted_at is not None
    assert version.deleted is True
    assert version.deleted_at is not None


@pytest.mark.django_db
def test_run_evals_rejects_eval_config_from_another_prompt(
    auth_client, organization, workspace, user
):
    target_template, target_version = _create_prompt_template(
        organization, workspace, user, "Prompt eval run target"
    )
    other_template, _ = _create_prompt_template(
        organization, workspace, user, "Prompt eval run other"
    )
    eval_template = _create_eval_template(organization, workspace)
    other_config = PromptEvalConfig.no_workspace_objects.create(
        name="Other prompt eval config",
        prompt_template=other_template,
        eval_template=eval_template,
        user=user,
        mapping={"text": "text"},
    )

    url = (
        f"/model-hub/prompt-templates/{target_template.id}/"
        "run-evals-on-multiple-versions/"
    )
    response = auth_client.post(
        url,
        {
            "version_to_run": ["v1"],
            "prompt_eval_config_ids": [str(other_config.id)],
        },
        format="json",
    )

    assert response.status_code == 400
    assert "do not exist for this prompt template" in str(response.data)
    target_version.refresh_from_db()
    assert target_version.evaluation_results in ({}, None)


@pytest.mark.django_db
def test_run_evals_run_index_zero_marks_single_result_and_submits_scoped_job(
    auth_client, organization, workspace, user, monkeypatch
):
    template, version_v1 = _create_prompt_template(
        organization, workspace, user, "Prompt eval run indexed target"
    )
    version_v2 = _create_prompt_version(
        template,
        "v2",
        text="Second indexed eval {{name}}",
    )
    eval_template = _create_eval_template(organization, workspace)
    eval_config = PromptEvalConfig.no_workspace_objects.create(
        name="Indexed prompt eval config",
        prompt_template=template,
        eval_template=eval_template,
        user=user,
        mapping={"text": "name"},
    )
    initial_results = {
        str(eval_config.id): {
            "name": eval_config.name,
            "average_score": None,
            "results": [
                {"status": StatusType.COMPLETED.value},
                {"status": StatusType.COMPLETED.value},
            ],
        }
    }
    for version in (version_v1, version_v2):
        version.variable_names = {"name": ["Ada", "Grace"]}
        version.evaluation_results = initial_results
        version.save(update_fields=["variable_names", "evaluation_results"])

    tracked_counts = []
    submitted = {}

    def fake_track_running_eval_count(
        prompt_config_eval_id, start=False, operation="set", num=None
    ):
        tracked_counts.append(
            {
                "prompt_config_eval_id": prompt_config_eval_id,
                "start": start,
                "operation": operation,
                "num": num,
            }
        )

    def fake_submit_with_retry(
        _executor,
        func,
        template_arg,
        executions_arg,
        prompt_eval_config_ids,
        run_index,
        **kwargs,
    ):
        submitted["func_name"] = func.__name__
        submitted["template_id"] = template_arg.id
        submitted["execution_versions"] = sorted(
            execution.template_version for execution in executions_arg
        )
        submitted["prompt_eval_config_ids"] = list(prompt_eval_config_ids)
        submitted["run_index"] = run_index
        submitted["user_id"] = kwargs["user_id"]

    monkeypatch.setattr(
        "model_hub.views.prompt_template.track_running_eval_count",
        fake_track_running_eval_count,
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.submit_with_retry",
        fake_submit_with_retry,
    )

    response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/run-evals-on-multiple-versions/",
        {
            "version_to_run": ["v1", "v2"],
            "prompt_eval_config_ids": [str(eval_config.id)],
            "run_index": 0,
        },
        format="json",
    )

    assert response.status_code == 200
    assert submitted == {
        "func_name": "run_evals_task",
        "template_id": template.id,
        "execution_versions": ["v1", "v2"],
        "prompt_eval_config_ids": [str(eval_config.id)],
        "run_index": 0,
        "user_id": str(user.id),
    }
    assert tracked_counts == [
        {
            "prompt_config_eval_id": str(eval_config.id),
            "start": True,
            "operation": "set",
            "num": 1,
        },
        {
            "prompt_config_eval_id": str(eval_config.id),
            "start": True,
            "operation": "set",
            "num": 1,
        },
    ]
    for version in (version_v1, version_v2):
        version.refresh_from_db()
        results = version.evaluation_results[str(eval_config.id)]["results"]
        assert results[0]["status"] == StatusType.RUNNING.value
        assert results[1]["status"] == StatusType.COMPLETED.value


@pytest.mark.django_db
def test_prompt_template_partial_update_preserves_scope_and_validates_folder(
    auth_client, organization, workspace, user
):
    active_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt template PATCH active folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    other_workspace = Workspace.objects.create(
        name="Prompt template PATCH other workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt template PATCH hidden folder",
        organization=organization,
        workspace=other_workspace,
        created_by=user,
    )
    other_org = Organization.objects.create(name="Prompt template PATCH other org")
    other_user = User.objects.create_user(
        email=f"prompt-template-patch-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Prompt Template Patch Owner",
        organization=organization,
    )
    template, _ = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt template PATCH contract",
        active_folder,
    )

    response = auth_client.patch(
        f"/model-hub/prompt-templates/{template.id}/",
        {
            "description": "Updated through partial patch",
            "variable_names": {"customer": ["Ada"]},
            "prompt_folder": str(active_folder.id),
            "organization": str(other_org.id),
            "created_by": str(other_user.id),
        },
        format="json",
    )

    assert response.status_code == 200
    template.refresh_from_db()
    assert template.description == "Updated through partial patch"
    assert template.variable_names == {"customer": ["Ada"]}
    assert template.prompt_folder_id == active_folder.id
    assert template.organization_id == organization.id
    assert template.workspace_id == workspace.id
    assert template.created_by_id == user.id

    hidden_folder_response = auth_client.patch(
        f"/model-hub/prompt-templates/{template.id}/",
        {"prompt_folder": str(other_folder.id)},
        format="json",
    )

    assert hidden_folder_response.status_code == 400
    template.refresh_from_db()
    assert template.prompt_folder_id == active_folder.id


@pytest.mark.django_db
def test_prompt_template_full_update_preserves_scope_and_validates_folder(
    auth_client, organization, workspace, user
):
    active_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt template PUT active folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    replacement_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt template PUT replacement folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    other_workspace = Workspace.objects.create(
        name="Prompt template PUT other workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt template PUT hidden folder",
        organization=organization,
        workspace=other_workspace,
        created_by=user,
    )
    other_org = Organization.objects.create(name="Prompt template PUT other org")
    other_user = User.objects.create_user(
        email=f"prompt-template-put-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Prompt Template Put Owner",
        organization=organization,
    )
    template, _ = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt template PUT contract",
        active_folder,
    )

    response = auth_client.put(
        f"/model-hub/prompt-templates/{template.id}/",
        {
            "name": "Prompt template PUT contract updated",
            "description": "Updated through full put",
            "variable_names": {"customer": ["Ada", "Grace"]},
            "prompt_folder": str(replacement_folder.id),
            "organization": str(other_org.id),
            "created_by": str(other_user.id),
        },
        format="json",
    )

    assert response.status_code == 200
    template.refresh_from_db()
    assert template.name == "Prompt template PUT contract updated"
    assert template.description == "Updated through full put"
    assert template.variable_names == {"customer": ["Ada", "Grace"]}
    assert template.prompt_folder_id == replacement_folder.id
    assert template.organization_id == organization.id
    assert template.workspace_id == workspace.id
    assert template.created_by_id == user.id

    hidden_folder_response = auth_client.put(
        f"/model-hub/prompt-templates/{template.id}/",
        {
            "name": "Prompt template PUT hidden folder attempt",
            "description": "Should not persist",
            "variable_names": {"customer": ["Hidden"]},
            "prompt_folder": str(other_folder.id),
        },
        format="json",
    )

    assert hidden_folder_response.status_code == 400
    template.refresh_from_db()
    assert template.name == "Prompt template PUT contract updated"
    assert template.prompt_folder_id == replacement_folder.id


@pytest.mark.django_db
def test_prompt_template_save_name_scopes_duplicate_checks_to_workspace(
    auth_client, organization, workspace, user
):
    other_workspace = Workspace.objects.create(
        name="Prompt save-name other workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    template, _ = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt save-name contract",
    )
    _same_workspace_duplicate, _ = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt save-name duplicate",
    )
    _other_workspace_duplicate, _ = _create_prompt_template(
        organization,
        other_workspace,
        user,
        "Prompt save-name other workspace duplicate",
    )

    duplicate_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/save-name/",
        {"name": "Prompt save-name duplicate"},
        format="json",
    )

    assert duplicate_response.status_code == 400
    template.refresh_from_db()
    assert template.name == "Prompt save-name contract"

    other_workspace_name_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/save-name/",
        {"name": "Prompt save-name other workspace duplicate"},
        format="json",
    )

    assert other_workspace_name_response.status_code == 200
    template.refresh_from_db()
    assert template.name == "Prompt save-name other workspace duplicate"
    assert template.organization_id == organization.id
    assert template.workspace_id == workspace.id
    assert template.created_by_id == user.id


@pytest.mark.django_db
def test_prompt_template_save_prompt_folder_rejects_other_workspace_folder(
    auth_client, organization, workspace, user
):
    active_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt save-folder active folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    replacement_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt save-folder replacement folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    other_workspace = Workspace.objects.create(
        name="Prompt save-folder other workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt save-folder hidden folder",
        organization=organization,
        workspace=other_workspace,
        created_by=user,
    )
    template, _ = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt save-folder contract",
        active_folder,
    )

    move_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/save-prompt-folder/",
        {"prompt_folder_id": str(replacement_folder.id)},
        format="json",
    )

    assert move_response.status_code == 200
    template.refresh_from_db()
    assert template.prompt_folder_id == replacement_folder.id

    hidden_folder_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/save-prompt-folder/",
        {"prompt_folder_id": str(other_folder.id)},
        format="json",
    )

    assert hidden_folder_response.status_code == 400
    template.refresh_from_db()
    assert template.prompt_folder_id == replacement_folder.id
    assert template.organization_id == organization.id
    assert template.workspace_id == workspace.id
    assert template.created_by_id == user.id


@pytest.mark.django_db
def test_prompt_template_stop_streaming_accepts_session_uuid_and_scopes_template(
    auth_client, organization, workspace, user, monkeypatch
):
    template, _ = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt stop-streaming active contract",
    )
    other_workspace = Workspace.objects.create(
        name="Prompt stop-streaming other workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    hidden_template, _ = _create_prompt_template(
        organization,
        other_workspace,
        user,
        "Prompt stop-streaming hidden contract",
    )
    session_uuid = str(uuid.uuid4())
    calls = []

    class FakeWebSocketManager:
        def handle_stop_streaming_request(self, template_id, versions, session_uuids):
            calls.append(
                {
                    "template_id": template_id,
                    "versions": versions,
                    "session_uuids": session_uuids,
                }
            )
            return {"status": "success", "message": "Stop requested"}

    def fake_get_websocket_manager(organization_id):
        calls.append({"organization_id": str(organization_id)})
        return FakeWebSocketManager()

    monkeypatch.setattr(
        "model_hub.views.prompt_template.get_websocket_manager",
        fake_get_websocket_manager,
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{template.id}/stop-streaming/",
        {"session_uuid": [session_uuid]},
    )

    assert response.status_code == 200
    assert response.json()["result"] == "Stop requested"
    assert calls[0]["organization_id"] == str(organization.id)
    assert calls[1] == {
        "template_id": str(template.id),
        "versions": [],
        "session_uuids": [session_uuid],
    }

    hidden_response = auth_client.get(
        f"/model-hub/prompt-templates/{hidden_template.id}/stop-streaming/",
        {"session_uuid": [str(uuid.uuid4())]},
    )

    assert hidden_response.status_code == 404
    assert len(calls) == 2


@pytest.mark.django_db
def test_prompt_template_stop_streaming_validates_versions_before_manager(
    auth_client, organization, workspace, user, monkeypatch
):
    template, _ = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt stop-streaming validation contract",
    )
    calls = []

    def fake_get_websocket_manager(organization_id):
        calls.append(str(organization_id))
        raise AssertionError("websocket manager should not be created")

    monkeypatch.setattr(
        "model_hub.views.prompt_template.get_websocket_manager",
        fake_get_websocket_manager,
    )

    too_many_versions = auth_client.get(
        f"/model-hub/prompt-templates/{template.id}/stop-streaming/",
        {"version": ["v1", "v2", "v3", "v4"]},
    )
    invalid_version = auth_client.get(
        f"/model-hub/prompt-templates/{template.id}/stop-streaming/",
        {"version": ["draft"]},
    )

    assert too_many_versions.status_code == 400
    assert invalid_version.status_code == 400
    assert calls == []


@pytest.mark.django_db
def test_prompt_template_create_stamps_request_scope_and_validates_folder(
    auth_client, organization, workspace, user
):
    active_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt template create active folder",
        organization=organization,
        workspace=workspace,
        created_by=user,
    )
    other_workspace = Workspace.objects.create(
        name="Prompt template create other workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_folder = PromptFolder.no_workspace_objects.create(
        name="Prompt template create hidden folder",
        organization=organization,
        workspace=other_workspace,
        created_by=user,
    )
    other_org = Organization.objects.create(name="Prompt template create other org")
    other_user = User.objects.create_user(
        email=f"prompt-template-create-owner-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Prompt Template Create Owner",
        organization=organization,
    )

    response = auth_client.post(
        "/model-hub/prompt-templates/",
        {
            "name": "Prompt template REST create contract",
            "description": "Created through the REST prompt-template endpoint",
            "variable_names": {"customer": ["Ada"]},
            "placeholders": {"customer": "Ada"},
            "prompt_folder": str(active_folder.id),
            "organization": str(other_org.id),
            "created_by": str(other_user.id),
        },
        format="json",
    )

    assert response.status_code == 201
    template = PromptTemplate.all_objects.get(id=response.data["id"])
    assert template.organization_id == organization.id
    assert template.workspace_id == workspace.id
    assert template.created_by_id == user.id
    assert template.prompt_folder_id == active_folder.id
    assert template.variable_names == {"customer": ["Ada"]}
    assert template.placeholders == {"customer": "Ada"}

    hidden_folder_response = auth_client.post(
        "/model-hub/prompt-templates/",
        {
            "name": "Prompt template REST create hidden folder",
            "prompt_folder": str(other_folder.id),
        },
        format="json",
    )

    assert hidden_folder_response.status_code == 400
    assert not PromptTemplate.all_objects.filter(
        name="Prompt template REST create hidden folder"
    ).exists()


@pytest.mark.django_db
def test_prompt_template_delete_scopes_and_soft_deletes_versions(
    auth_client, organization, workspace, user
):
    template, version = _create_prompt_template(
        organization,
        workspace,
        user,
        "Prompt template REST delete contract",
    )
    other_workspace = Workspace.objects.create(
        name="Prompt template delete other workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    hidden_template, hidden_version = _create_prompt_template(
        organization,
        other_workspace,
        user,
        "Prompt template REST delete hidden",
    )

    hidden_response = auth_client.delete(
        f"/model-hub/prompt-templates/{hidden_template.id}/"
    )

    assert hidden_response.status_code == 404
    hidden_template.refresh_from_db()
    hidden_version.refresh_from_db()
    assert hidden_template.deleted is False
    assert hidden_version.deleted is False

    response = auth_client.delete(f"/model-hub/prompt-templates/{template.id}/")

    assert response.status_code == 204
    template.refresh_from_db()
    version.refresh_from_db()
    assert template.deleted is True
    assert template.deleted_at is not None
    assert version.deleted is True
    assert version.deleted_at is not None


@pytest.mark.django_db
def test_prompt_assistant_helpers_validate_required_fields_before_agent(
    auth_client, monkeypatch
):
    def fail_agent(*_args, **_kwargs):
        raise AssertionError("assistant agent should not be constructed")

    monkeypatch.setattr("model_hub.views.prompt_template.PromptGenerator", fail_agent)
    monkeypatch.setattr(
        "model_hub.views.prompt_template.PromptSuggestionGenerator", fail_agent
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.SyntheticDataAgent", fail_agent
    )
    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct",
        lambda self, **_kwargs: None,
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.submit_with_retry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("assistant task should not be submitted")
        ),
    )
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *_args, **_kw: None)

    generate_response = auth_client.post(
        "/model-hub/prompt-templates/generate-prompt/",
        {},
        format="json",
    )
    improve_response = auth_client.post(
        "/model-hub/prompt-templates/improve-prompt/",
        {"improvement_requirements": "Make it shorter."},
        format="json",
    )
    analyze_response = auth_client.post(
        "/model-hub/prompt-templates/analyze-prompt/",
        {"prompt": "Hello {{customer}}"},
        format="json",
    )
    variables_response = auth_client.post(
        "/model-hub/prompt-templates/generate-variables/",
        {"prompt_name": "Greeting"},
        format="json",
    )

    assert generate_response.status_code == 400
    assert improve_response.status_code == 400
    assert analyze_response.status_code == 400
    assert variables_response.status_code == 400


@pytest.mark.django_db
def test_prompt_assistant_helpers_submit_scoped_payloads(
    auth_client, organization, workspace, user, monkeypatch
):
    submitted = []
    suggestions = []
    generated_variable_payloads = []

    class FakePromptGenerator:
        def generate_prompt(self, payload):
            return payload

        def improve_prompt(self, payload):
            return payload

    class FakePromptSuggestionGenerator:
        def _prompt_suggestion(self, payload):
            suggestions.append(payload)
            return "Use a measurable success criterion."

    class FakeSeries(list):
        def tolist(self):
            return list(self)

    class FakeFrame:
        columns = ["customer", "tone"]

        def __getitem__(self, column):
            return FakeSeries(
                {
                    "customer": ["Ada", "Grace"],
                    "tone": ["warm", "direct"],
                }[column]
            )

    class FakeSyntheticDataAgent:
        def generate_and_validate(self, payload):
            generated_variable_payloads.append(payload)
            return FakeFrame()

    def fake_submit_with_retry(_executor, func, payload, call_log_row=None):
        submitted.append(
            {
                "func_name": getattr(func, "__name__", ""),
                "payload": payload,
                "call_log_row": call_log_row,
            }
        )

    monkeypatch.setattr(
        "tfc.billing.boundary._EeBilling.log_and_deduct",
        lambda self, **_kwargs: None,
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.PromptGenerator", FakePromptGenerator
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.PromptSuggestionGenerator",
        FakePromptSuggestionGenerator,
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.SyntheticDataAgent", FakeSyntheticDataAgent
    )
    monkeypatch.setattr(
        "model_hub.views.prompt_template.submit_with_retry", fake_submit_with_retry
    )
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *_args, **_kw: None)

    generate_response = auth_client.post(
        "/model-hub/prompt-templates/generate-prompt/",
        {"statement": "Write a support triage prompt."},
        format="json",
    )
    improve_response = auth_client.post(
        "/model-hub/prompt-templates/improve-prompt/",
        {
            "existing_prompt": "Hello {{customer}}",
            "improvement_requirements": "Make it more specific.",
        },
        format="json",
    )
    analyze_response = auth_client.post(
        "/model-hub/prompt-templates/analyze-prompt/",
        {
            "prompt": "Hello {{customer}}",
            "explanation": "Needs clearer success criteria.",
            "example": {"customer": "Ada"},
        },
        format="json",
    )
    variables_response = auth_client.post(
        "/model-hub/prompt-templates/generate-variables/",
        {
            "prompt_name": "Greeting",
            "prompt_instructions": ["Greet the customer"],
            "variable_names": ["customer", "tone"],
            "variable_count": 2,
        },
        format="json",
    )

    assert generate_response.status_code == 200
    assert improve_response.status_code == 200
    assert analyze_response.status_code == 200
    assert variables_response.status_code == 200

    assert len(submitted) == 2
    generate_payload = submitted[0]["payload"]
    improve_payload = submitted[1]["payload"]
    assert submitted[0]["func_name"] == "generate_prompt"
    assert generate_payload["description"] == "Write a support triage prompt."
    assert generate_payload["organization_id"] == str(organization.id)
    assert generate_payload["user_id"] == str(user.id)
    assert generate_payload["generation_id"].startswith("generate_")
    assert submitted[1]["func_name"] == "improve_prompt"
    assert improve_payload["original_prompt"] == "Hello {{customer}}"
    assert improve_payload["improvement_suggestions"] == "Make it more specific."
    assert improve_payload["organization_id"] == str(organization.id)
    assert improve_payload["user_id"] == str(user.id)
    assert improve_payload["improve_id"].startswith("improve_")

    assert suggestions == [
        {
            "prompt": "Hello {{customer}}",
            "example": {"customer": "Ada"},
            "feedback": "Needs clearer success criteria.",
        }
    ]
    assert (
        analyze_response.json()["result"]["improvement_suggestions"]
        == "Use a measurable success criterion."
    )
    assert generated_variable_payloads == [
        {
            "prompt_name": "Greeting",
            "variable_names": ["customer", "tone"],
            "batch_size": 2,
            "generation_type": "prompt",
            "prompt_instructions": "['Greet the customer']",
        }
    ]
    assert variables_response.json()["result"]["variables"] == {
        "customer": ["Ada", "Grace"],
        "tone": ["warm", "direct"],
    }


@pytest.mark.django_db
def test_prompt_versions_endpoint_returns_prompt_version_rows(
    auth_client, organization, workspace, user
):
    template, version_v1 = _create_prompt_template(
        organization, workspace, user, "Prompt versions contract"
    )
    version_v2 = _create_prompt_version(
        template,
        "v2",
        text="Second version {{name}}",
    )

    response = auth_client.get(f"/model-hub/prompt-templates/{template.id}/versions/")

    assert response.status_code == 200
    rows = response.json()["results"]
    versions = [row["template_version"] for row in rows]
    assert versions[:2] == ["v2", "v1"]
    row_ids = {row["id"] for row in rows}
    assert str(version_v1.id) in row_ids
    assert str(version_v2.id) in row_ids


@pytest.mark.django_db
def test_prompt_sdk_code_accepts_dict_prompt_config_snapshot(
    auth_client, organization, workspace, user
):
    template, _ = _create_prompt_template(
        organization, workspace, user, "Prompt SDK code snapshot contract"
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{template.id}/get-sdk-code/python/"
    )

    assert response.status_code == 200
    code = response.json()["result"]["python"]
    assert f"/model-hub/prompt-templates/{template.id}/run_template/" in code
    assert "YOUR_API_KEY" in code
    assert "gpt-4o-mini" in code


@pytest.mark.django_db
def test_run_template_prompt_run_submits_organization_id(
    auth_client, organization, workspace, user, monkeypatch
):
    template, version = _create_prompt_template(
        organization, workspace, user, "Prompt run organization id contract"
    )
    submitted = {}

    def fake_submit_with_retry(_executor, _func, *args, **_kwargs):
        submitted["args"] = args

    monkeypatch.setattr(
        "model_hub.views.prompt_template.submit_with_retry",
        fake_submit_with_retry,
    )

    response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/run_template/",
        {
            "name": template.name,
            "version": version.template_version,
            "is_run": "prompt",
            "variable_names": {"name": ["Ada"]},
            "placeholders": {},
            "evaluation_configs": [],
            "prompt_config": [_prompt_config("Say hello to {{name}}")],
        },
        format="json",
    )

    assert response.status_code == 200
    assert str(submitted["args"][2]) == str(organization.id)
    assert submitted["args"][2] != organization


@pytest.mark.django_db
def test_prompt_default_version_is_exclusive_for_set_default_and_commit(
    auth_client, organization, workspace, user
):
    template, version_v1 = _create_prompt_template(
        organization, workspace, user, "Prompt default exclusivity contract"
    )
    version_v1.is_default = True
    version_v1.is_draft = False
    version_v1.save(update_fields=["is_default", "is_draft"])
    version_v2 = _create_prompt_version(
        template,
        "v2",
        text="Default v2 {{name}}",
        is_draft=False,
    )

    set_default_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/set_default/",
        {"version_name": "v2"},
        format="json",
    )

    assert set_default_response.status_code == 200
    version_v1.refresh_from_db()
    version_v2.refresh_from_db()
    assert version_v1.is_default is False
    assert version_v2.is_default is True

    default_lookup = auth_client.get(
        "/model-hub/prompt-templates/get-template-by-name/",
        {"name": template.name},
    )
    assert default_lookup.status_code == 200
    assert default_lookup.json()["version"] == "v2"

    commit_response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/commit/",
        {
            "version_name": "v1",
            "message": "restore v1 default",
            "is_draft": False,
            "set_default": True,
        },
        format="json",
    )

    assert commit_response.status_code == 200
    version_v1.refresh_from_db()
    version_v2.refresh_from_db()
    assert version_v1.is_default is True
    assert version_v2.is_default is False
