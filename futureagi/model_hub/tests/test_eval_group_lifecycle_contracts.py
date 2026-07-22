import pytest
from rest_framework import status

from accounts.models import Organization
from model_hub.models.choices import OwnerChoices
from model_hub.models.eval_groups import EvalGroup
from model_hub.models.evals_metric import EvalTemplate


def _create_eval_template(organization, workspace=None, name="eval-group-template"):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={
            "output": "Pass/Fail",
            "eval_type_id": "CustomPromptEvaluator",
            "required_keys": ["response"],
        },
        eval_tags=["llm"],
        criteria="Check {{response}}.",
        model="turing_large",
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _create_eval_group(auth_client, name, template_ids):
    return auth_client.post(
        "/model-hub/eval-groups/",
        {
            "name": name,
            "description": "Eval group lifecycle contract.",
            "eval_template_ids": [str(template_id) for template_id in template_ids],
        },
        format="json",
    )


@pytest.mark.django_db
def test_eval_group_create_rejects_cross_org_template_id(auth_client, user, workspace):
    other_org = Organization.objects.create(name="Other Eval Group Org")
    other_template = _create_eval_template(
        other_org, name="eval-group-other-org-template"
    )

    response = _create_eval_group(
        auth_client,
        "cross_org_template_group",
        [other_template.id],
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not EvalGroup.all_objects.filter(
        name="cross_org_template_group",
        organization=user.organization,
        workspace=workspace,
    ).exists()


@pytest.mark.django_db
def test_eval_group_create_rejects_unknown_template_id(auth_client, user, workspace):
    response = _create_eval_group(
        auth_client,
        "unknown_template_group",
        ["00000000-0000-0000-0000-000000000000"],
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not EvalGroup.all_objects.filter(
        name="unknown_template_group",
        organization=user.organization,
        workspace=workspace,
    ).exists()


@pytest.mark.django_db
def test_eval_group_edit_rejects_cross_org_template_id(auth_client, user, workspace):
    template = _create_eval_template(
        user.organization,
        workspace,
        name="eval-group-edit-owned-template",
    )
    group_response = _create_eval_group(
        auth_client,
        "edit_cross_org_template_group",
        [template.id],
    )
    assert group_response.status_code == status.HTTP_200_OK
    group_id = group_response.json()["result"]["id"]

    other_org = Organization.objects.create(name="Other Eval Group Edit Org")
    other_template = _create_eval_template(
        other_org, name="eval-group-edit-other-org-template"
    )

    response = auth_client.post(
        "/model-hub/eval-groups/edit-eval-list/",
        {
            "eval_group_id": group_id,
            "added_template_ids": [str(other_template.id)],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    group = EvalGroup.objects.get(id=group_id)
    assert list(group.eval_templates.values_list("id", flat=True)) == [template.id]


@pytest.mark.django_db
def test_eval_group_delete_sets_deleted_at_and_clears_relationships(
    auth_client, user, workspace
):
    template = _create_eval_template(
        user.organization,
        workspace,
        name="eval-group-delete-template",
    )
    group_response = _create_eval_group(
        auth_client,
        "delete_contract_group",
        [template.id],
    )
    assert group_response.status_code == status.HTTP_200_OK
    group_id = group_response.json()["result"]["id"]

    response = auth_client.delete(f"/model-hub/eval-groups/{group_id}/")

    assert response.status_code == status.HTTP_200_OK
    deleted_group = EvalGroup.all_objects.get(id=group_id)
    assert deleted_group.deleted is True
    assert deleted_group.deleted_at is not None
    assert deleted_group.eval_templates.through.objects.filter(
        evalgroup_id=group_id
    ).count() == 0
