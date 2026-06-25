"""Live multi-tenant tests for ground-truth scope.

Hits the real Django test database. No ORM mocking. Covers both
SYSTEM and USER eval templates and the partial unique constraint.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, transaction

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalGroundTruth, EvalTemplate
from model_hub.services.ground_truth_service import GroundTruthService
from tfc.constants.roles import OrganizationRoles


def _make_org_with_workspace(label: str) -> tuple[Organization, Workspace, User]:
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"{label} {suffix}")
    user = User.objects.create_user(
        email=f"{label.lower().replace(' ', '-')}-{suffix}@futureagi.com",
        password="testpass-1234",
        name=f"{label} User",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.objects.create(
        name=f"{label} Workspace {suffix}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    return org, workspace, user


def _make_user_template(org: Organization, workspace: Workspace) -> EvalTemplate:
    return EvalTemplate.no_workspace_objects.create(
        name=f"user-tmpl-{uuid.uuid4().hex[:6]}",
        organization=org,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "required_keys": ["input"]},
        criteria="Check {{input}}",
        visible_ui=True,
    )


def _make_system_template() -> EvalTemplate:
    return EvalTemplate.no_workspace_objects.create(
        name=f"system-tmpl-{uuid.uuid4().hex[:6]}",
        organization=None,
        workspace=None,
        owner=OwnerChoices.SYSTEM.value,
        config={"output": "Pass/Fail", "required_keys": ["input"]},
        criteria="Check {{input}}",
        visible_ui=True,
    )


def _make_gt(
    *,
    template: EvalTemplate,
    org: Organization,
    workspace: Workspace,
    is_active: bool = False,
    enabled: bool = True,
    embedding_status: str = EvalGroundTruth.EmbeddingStatus.COMPLETED,
) -> EvalGroundTruth:
    return EvalGroundTruth.objects.create(
        eval_template=template,
        name=f"gt-{uuid.uuid4().hex[:6]}",
        file_name="gt.csv",
        columns=["input", "expected"],
        data=[{"input": "hello", "expected": "world"}],
        row_count=1,
        embedding_status=embedding_status,
        organization=org,
        workspace=workspace,
        is_active=is_active,
        enabled=enabled,
        variable_mapping={"input": "input"},
        role_mapping={"output": "expected"},
    )


@pytest.mark.django_db
def test_system_template_isolates_gt_between_two_orgs():
    template = _make_system_template()
    org_a, ws_a, _ = _make_org_with_workspace("Tenant A")
    org_b, ws_b, _ = _make_org_with_workspace("Tenant B")

    gt_a = _make_gt(template=template, org=org_a, workspace=ws_a, is_active=True)

    found_for_a = GroundTruthService.load_active_gt(
        eval_template=template, organization_id=org_a.id, workspace_id=ws_a.id,
    )
    found_for_b = GroundTruthService.load_active_gt(
        eval_template=template, organization_id=org_b.id, workspace_id=ws_b.id,
    )

    assert found_for_a is not None and found_for_a.id == gt_a.id
    assert found_for_b is None


@pytest.mark.django_db
def test_user_template_other_org_lookup_returns_none(organization, workspace):
    template = _make_user_template(organization, workspace)
    _make_gt(template=template, org=organization, workspace=workspace, is_active=True)
    other_org, other_ws, _ = _make_org_with_workspace("Outsider")

    found = GroundTruthService.load_active_gt(
        eval_template=template,
        organization_id=other_org.id,
        workspace_id=other_ws.id,
    )

    assert found is None


@pytest.mark.django_db
def test_update_setup_clears_sibling_is_active_in_db():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Sibling")

    older = _make_gt(template=template, org=org, workspace=workspace, is_active=True)
    newer = _make_gt(template=template, org=org, workspace=workspace, is_active=False)

    result = GroundTruthService.update_setup(
        gt=newer,
        eval_template=template,
        variable_mapping={"input": "input"},
        role_mapping={"output": "expected"},
        max_examples=5,
        enabled=True,
    )
    assert not hasattr(result, "code"), getattr(result, "message", "")

    older.refresh_from_db()
    newer.refresh_from_db()
    assert older.is_active is False
    assert newer.is_active is True
    assert newer.max_examples == 5


@pytest.mark.django_db
def test_update_setup_does_not_mutate_system_template_config():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Config")
    gt = _make_gt(template=template, org=org, workspace=workspace)

    GroundTruthService.update_setup(
        gt=gt,
        eval_template=template,
        variable_mapping={"input": "input"},
        role_mapping={"output": "expected"},
        max_examples=3,
        enabled=True,
    )

    template.refresh_from_db()
    assert "ground_truth" not in (template.config or {})


@pytest.mark.django_db
def test_enabled_false_is_treated_as_unwired():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Disabled")
    _make_gt(
        template=template, org=org, workspace=workspace,
        is_active=True, enabled=False,
    )

    found = GroundTruthService.load_active_gt(
        eval_template=template, organization_id=org.id, workspace_id=workspace.id,
    )

    assert found is None


@pytest.mark.django_db
def test_soft_deleted_active_row_drops_out_of_lookup():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Delete")
    gt = _make_gt(template=template, org=org, workspace=workspace, is_active=True)

    gt.deleted = True
    gt.is_active = False
    gt.save(update_fields=["deleted", "is_active", "updated_at"])

    found = GroundTruthService.load_active_gt(
        eval_template=template, organization_id=org.id, workspace_id=workspace.id,
    )

    assert found is None


@pytest.mark.django_db
def test_partial_unique_constraint_rejects_two_active_rows_for_same_tenant():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Unique")

    _make_gt(template=template, org=org, workspace=workspace, is_active=True)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _make_gt(
                template=template, org=org, workspace=workspace, is_active=True,
            )


@pytest.mark.django_db
def test_partial_unique_constraint_rejects_two_active_rows_when_workspace_is_null():
    template = _make_system_template()
    org, _, _ = _make_org_with_workspace("Tenant NullWS")

    EvalGroundTruth.objects.create(
        eval_template=template,
        name=f"gt-{uuid.uuid4().hex[:6]}",
        file_name="gt.csv",
        columns=["input", "expected"],
        data=[{"input": "hello", "expected": "world"}],
        row_count=1,
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
        organization=org,
        workspace=None,
        is_active=True,
        enabled=True,
        variable_mapping={"input": "input"},
        role_mapping={"output": "expected"},
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            EvalGroundTruth.objects.create(
                eval_template=template,
                name=f"gt-{uuid.uuid4().hex[:6]}",
                file_name="gt.csv",
                columns=["input", "expected"],
                data=[{"input": "hello", "expected": "world"}],
                row_count=1,
                embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
                organization=org,
                workspace=None,
                is_active=True,
                enabled=True,
                variable_mapping={"input": "input"},
                role_mapping={"output": "expected"},
            )


@pytest.mark.django_db
def test_partial_unique_constraint_allows_two_inactive_rows_for_same_tenant():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Inactive")

    _make_gt(template=template, org=org, workspace=workspace, is_active=False)
    _make_gt(template=template, org=org, workspace=workspace, is_active=False)
    # No IntegrityError - the partial index condition only enforces uniqueness
    # for is_active=True rows.


@pytest.mark.django_db
def test_partial_unique_constraint_allows_active_rows_in_different_tenants():
    template = _make_system_template()
    org_a, ws_a, _ = _make_org_with_workspace("Tenant Multi A")
    org_b, ws_b, _ = _make_org_with_workspace("Tenant Multi B")

    _make_gt(template=template, org=org_a, workspace=ws_a, is_active=True)
    _make_gt(template=template, org=org_b, workspace=ws_b, is_active=True)
    # No IntegrityError - the constraint is scoped per (template, org, workspace).


@pytest.mark.django_db
def test_load_active_gt_picks_only_the_active_row_among_siblings():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Sibling Read")

    _make_gt(template=template, org=org, workspace=workspace, is_active=False)
    active = _make_gt(template=template, org=org, workspace=workspace, is_active=True)
    _make_gt(template=template, org=org, workspace=workspace, is_active=False)

    found = GroundTruthService.load_active_gt(
        eval_template=template, organization_id=org.id, workspace_id=workspace.id,
    )

    assert found is not None
    assert found.id == active.id


@pytest.mark.django_db
def test_user_template_active_gt_in_owner_org_resolves(organization, workspace):
    template = _make_user_template(organization, workspace)
    gt = _make_gt(
        template=template, org=organization, workspace=workspace, is_active=True,
    )

    found = GroundTruthService.load_active_gt(
        eval_template=template,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )

    assert found is not None and found.id == gt.id


@pytest.mark.django_db
def test_same_org_two_workspaces_have_independent_active_gt():
    """Workspace scope is enforced - two workspaces in the same org each
    pick up only their own active GT row."""
    org, ws_a, ws_a_user = _make_org_with_workspace("Tenant Multi WS")
    ws_b = Workspace.objects.create(
        name=f"WS B {uuid.uuid4().hex[:6]}",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=ws_a_user,
    )
    template = _make_system_template()

    gt_a = _make_gt(template=template, org=org, workspace=ws_a, is_active=True)
    gt_b = _make_gt(template=template, org=org, workspace=ws_b, is_active=True)

    found_a = GroundTruthService.load_active_gt(
        eval_template=template, organization_id=org.id, workspace_id=ws_a.id,
    )
    found_b = GroundTruthService.load_active_gt(
        eval_template=template, organization_id=org.id, workspace_id=ws_b.id,
    )

    assert found_a is not None and found_a.id == gt_a.id
    assert found_b is not None and found_b.id == gt_b.id


@pytest.mark.django_db
def test_update_setup_against_user_template_works_for_owner(organization, workspace):
    template = _make_user_template(organization, workspace)
    gt = _make_gt(template=template, org=organization, workspace=workspace)

    result = GroundTruthService.update_setup(
        gt=gt,
        eval_template=template,
        variable_mapping={"input": "input"},
        role_mapping={"output": "expected"},
        max_examples=7,
        enabled=True,
    )

    assert not hasattr(result, "code"), getattr(result, "message", "")
    gt.refresh_from_db()
    assert gt.is_active is True
    assert gt.enabled is True
    assert gt.max_examples == 7


@pytest.mark.django_db
def test_update_setup_rejects_empty_output_role_mapping():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Validation")
    gt = _make_gt(template=template, org=org, workspace=workspace)

    result = GroundTruthService.update_setup(
        gt=gt,
        eval_template=template,
        variable_mapping={"input": "input"},
        role_mapping={"output": ""},
        max_examples=3,
        enabled=True,
    )

    assert hasattr(result, "code")
    assert result.code == "EXPECTED_OUTPUT_REQUIRED"


@pytest.mark.django_db
def test_update_setup_rejects_max_examples_out_of_range():
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Range")
    gt = _make_gt(template=template, org=org, workspace=workspace)

    result = GroundTruthService.update_setup(
        gt=gt,
        eval_template=template,
        variable_mapping={"input": "input"},
        role_mapping={"output": "expected"},
        max_examples=99,
        enabled=True,
    )

    assert hasattr(result, "code")
    assert result.code == "INVALID_MAX_EXAMPLES"


@pytest.mark.django_db
def test_update_setup_resets_embedding_status_when_variable_mapping_changes():
    """Changing the embed source columns invalidates existing vectors -
    the row goes back to pending so the user has to re-embed."""
    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Stale")
    gt = _make_gt(template=template, org=org, workspace=workspace)
    gt.embedded_row_count = 1
    gt.embedding_status = EvalGroundTruth.EmbeddingStatus.COMPLETED
    gt.save()

    result = GroundTruthService.update_setup(
        gt=gt,
        eval_template=template,
        variable_mapping={"input": "expected"},
        role_mapping={"output": "expected"},
        max_examples=3,
        enabled=True,
    )
    assert not hasattr(result, "code"), getattr(result, "message", "")
    assert result["embeddings_stale"] is True
    gt.refresh_from_db()
    assert gt.embedding_status == EvalGroundTruth.EmbeddingStatus.PENDING


@pytest.mark.django_db
def test_default_manager_hides_soft_deleted_rows_from_list():
    from django.utils import timezone

    template = _make_system_template()
    org, workspace, _ = _make_org_with_workspace("Tenant Soft Delete List")
    live = _make_gt(template=template, org=org, workspace=workspace)
    gone = _make_gt(template=template, org=org, workspace=workspace)
    gone.deleted = True
    gone.deleted_at = timezone.now()
    gone.save()

    visible = list(
        EvalGroundTruth.objects.filter(eval_template=template).values_list(
            "id", flat=True
        )
    )

    assert live.id in visible
    assert gone.id not in visible


@pytest.mark.django_db
def test_inject_context_injects_gt_blocks_only_for_owning_tenant(monkeypatch):
    """End-to-end check: a real Django GT row for tenant A drives the
    block injection, and tenant B sees a clean mapped dict. The CH
    retrieve is stubbed so the test stays in process - the path under
    test is the per-tenant dispatch, not the vector store."""
    template = _make_system_template()
    org_a, ws_a, _ = _make_org_with_workspace("Inject A")
    org_b, ws_b, _ = _make_org_with_workspace("Inject B")
    _make_gt(template=template, org=org_a, workspace=ws_a, is_active=True)

    retrieved_for_a = [{"input": "hello", "expected": "world"}]

    def fake_retrieve(*, gt, inputs, max_results):
        # Only ever called when load_active_gt found a row for the caller.
        assert gt.organization_id == org_a.id
        return retrieved_for_a, {}

    monkeypatch.setattr(
        "model_hub.services.ground_truth_service."
        "GroundTruthService.retrieve_few_shot",
        fake_retrieve,
    )

    mapped_for_a = {"input": "hello"}
    GroundTruthService.inject_context(
        mapped_for_a, template,
        organization_id=org_a.id, workspace_id=ws_a.id,
    )

    mapped_for_b = {"input": "hello"}
    GroundTruthService.inject_context(
        mapped_for_b, template,
        organization_id=org_b.id, workspace_id=ws_b.id,
    )

    assert "ground_truth_blocks" in mapped_for_a
    assert mapped_for_a["ground_truth_blocks"]
    assert "ground_truth_blocks" not in mapped_for_b


@pytest.mark.e2e
@pytest.mark.django_db
def test_setup_endpoint_persists_to_row_not_to_template_config(
    auth_client, organization, workspace,
):
    """HTTP-level proof: PUT /setup/ writes the runtime knobs onto the
    EvalGroundTruth row, and leaves EvalTemplate.config untouched."""
    template = _make_user_template(organization, workspace)
    gt = _make_gt(
        template=template, org=organization, workspace=workspace,
        embedding_status=EvalGroundTruth.EmbeddingStatus.PENDING,
    )

    response = auth_client.put(
        f"/model-hub/ground-truth/{gt.id}/setup/",
        {
            "variable_mapping": {"input": "input"},
            "role_mapping": {"output": "expected"},
            "max_examples": 4,
            "enabled": True,
        },
        format="json",
    )

    assert response.status_code == 200, response.data
    template.refresh_from_db()
    gt.refresh_from_db()
    assert "ground_truth" not in (template.config or {})
    assert gt.is_active is True
    assert gt.enabled is True
    assert gt.max_examples == 4
    assert gt.variable_mapping == {"input": "input"}
    assert response.data["result"]["config"]["max_examples"] == 4
    assert response.data["result"]["config"]["enabled"] is True
