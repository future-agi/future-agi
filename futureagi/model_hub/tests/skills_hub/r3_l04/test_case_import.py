"""Real PostgreSQL exact Dataset read, source authorization and suite CAS."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.models import HubSkillContextOperation, HubSkillTestSuite
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.skills_hub_r3 import HubSkillIntakePlan
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.case_import import (
    accept_dataset_case,
    preview_dataset_case,
    purge_expired_case_previews,
    read_dataset_source,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def principal(user, organization, workspace):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture
def source(organization, workspace):
    dataset = Dataset.no_workspace_objects.create(
        organization=organization, workspace=workspace, name="Synthetic requests"
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    prompt = Column.no_workspace_objects.create(
        dataset=dataset, name="Supplied request", data_type="text", source="OTHERS"
    )
    fixture = Column.no_workspace_objects.create(
        dataset=dataset, name="Supplied policy", data_type="text", source="OTHERS"
    )
    Cell.no_workspace_objects.create(
        dataset=dataset,
        row=row,
        column=prompt,
        value="A synthetic customer requests a refund.",
    )
    Cell.no_workspace_objects.create(
        dataset=dataset,
        row=row,
        column=fixture,
        value="Approved synthetic policy; exclude chargebacks.",
    )
    return dataset, row, prompt, fixture


@pytest.fixture
def preview(principal, hub_skill, source):
    dataset, row, prompt, fixture = source
    return preview_dataset_case(
        principal=principal,
        resource_id=hub_skill.id,
        kind="skill",
        dataset_id=dataset.id,
        row_id=row.id,
        prompt_column_id=prompt.id,
        fixture_column_ids=[fixture.id],
    )


def acceptance(preview):
    case = {
        **preview["case"],
        "reviewed": True,
        "checks": [
            {
                "id": str(uuid4()),
                "kind": "contains",
                "critical": True,
                "config": {"text": "refund", "case_sensitive": False},
            }
        ],
    }
    return {
        "plan_id": preview["plan_id"],
        "source_fingerprint": preview["source_fingerprint"],
        "suite_revision": 0,
        "case": case,
        "acknowledge_workspace_sharing": True,
        "idempotency_key": uuid4(),
    }


def test_reviewed_redaction_retains_lineage_without_output_as_ground_truth(
    principal, hub_skill, preview
):
    assert preview["case"]["checks"] == []
    assert preview["case"]["reviewed"] is False
    request = acceptance(preview)
    request["case"]["prompt"] = "Redacted synthetic request"
    request["case"]["fixtures"] = []
    result = accept_dataset_case(
        principal=principal, resource_id=hub_skill.id, kind="skill", **request
    )
    saved = HubSkillTestSuite.objects.get(skill=hub_skill)
    assert saved.cases[0]["prompt"] == "Redacted synthetic request"
    assert saved.cases[0]["source"]["revision"] == preview["source"]["revision"]
    assert saved.cases[0]["reviewed_by"]["id"] == str(principal.user_id)
    assert result["suite_revision"] == 1
    assert HubSkillIntakePlan.objects.get(pk=preview["plan_id"]).payload is None
    # An unknown response retry returns the original receipt, not another suite write.
    assert (
        accept_dataset_case(
            principal=principal, resource_id=hub_skill.id, kind="skill", **request
        )
        == result
    )
    saved.refresh_from_db()
    assert saved.revision == 1
    assert HubSkillContextOperation.objects.count() == 1


def test_source_mutation_invalidates_retained_preview(
    principal, hub_skill, source, preview
):
    Cell.no_workspace_objects.filter(row=source[1], column=source[2]).update(
        value="Changed source without normal save hook"
    )
    with pytest.raises(SkillsHubError) as error:
        accept_dataset_case(
            principal=principal,
            resource_id=hub_skill.id,
            kind="skill",
            **acceptance(preview),
        )
    assert error.value.code == "source_conflict"
    assert not HubSkillTestSuite.objects.filter(skill=hub_skill).exists()


def test_source_and_destination_scope_are_checked_independently(
    principal, hub_skill, source, other_workspace
):
    dataset, row, prompt, fixture = source
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(workspace=other_workspace)
    with pytest.raises(SkillsHubError) as error:
        preview_dataset_case(
            principal=principal,
            resource_id=hub_skill.id,
            kind="skill",
            dataset_id=dataset.id,
            row_id=row.id,
            prompt_column_id=prompt.id,
            fixture_column_ids=[fixture.id],
        )
    assert error.value.code == "resource_not_found"
    assert not HubSkillIntakePlan.objects.exists()


@pytest.mark.parametrize(
    "change,code",
    [
        ("expiry", "capture_expired"),
        ("disclosure", "capture_unsafe"),
        ("review", "invalid_test_config"),
        ("suite", "test_suite_conflict"),
        ("identity", "invalid_test_config"),
    ],
)
def test_acceptance_negative_contract(principal, hub_skill, preview, change, code):
    request = acceptance(preview)
    if change == "expiry":
        HubSkillIntakePlan.objects.filter(pk=preview["plan_id"]).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif change == "disclosure":
        request["acknowledge_workspace_sharing"] = False
    elif change == "review":
        request["case"]["reviewed"] = False
    elif change == "suite":
        request["suite_revision"] = 1
    else:
        request["case"]["source_group_id"] = "forged"
    with pytest.raises(SkillsHubError) as error:
        accept_dataset_case(
            principal=principal, resource_id=hub_skill.id, kind="skill", **request
        )
    assert error.value.code == code
    assert not HubSkillTestSuite.objects.filter(skill=hub_skill).exists()


def test_system_key_never_inherits_a_private_owner(principal, hub_skill, source):
    dataset, row, prompt, fixture = source
    with pytest.raises(SkillsHubError) as error:
        preview_dataset_case(
            principal=replace(principal, kind="system_key", api_key_id=uuid4()),
            resource_id=hub_skill.id,
            kind="skill",
            dataset_id=dataset.id,
            row_id=row.id,
            prompt_column_id=prompt.id,
            fixture_column_ids=[],
        )
    assert error.value.code == "principal_not_supported"


def test_generated_reasoning_and_binary_columns_are_not_imported(principal, source):
    dataset, row, prompt, fixture = source
    for values in (
        {"source": "evaluation_reason"},
        {"source": "OTHERS", "data_type": "image"},
    ):
        Column.no_workspace_objects.filter(pk=prompt.pk).update(**values)
        with pytest.raises(SkillsHubError) as error:
            read_dataset_source(
                principal=principal,
                dataset_id=dataset.id,
                row_id=row.id,
                prompt_column_id=prompt.id,
                fixture_column_ids=[],
            )
        assert error.value.code == "capture_unsafe"


def test_review_does_not_waive_credential_redaction(principal, hub_skill, preview):
    request = acceptance(preview)
    request["case"][
        "prompt"
    ] = "-----BEGIN PRIVATE KEY-----\\nsynthetic-placeholder\\n-----END PRIVATE KEY-----"
    with pytest.raises(SkillsHubError) as error:
        accept_dataset_case(
            principal=principal, resource_id=hub_skill.id, kind="skill", **request
        )
    assert error.value.code == "capture_unsafe"
    assert "synthetic-placeholder" not in str(error.value)
    assert not HubSkillTestSuite.objects.filter(skill=hub_skill).exists()


def test_preview_retention_removes_private_text_in_bounded_batches(preview):
    assert purge_expired_case_previews() == 0
    HubSkillIntakePlan.objects.filter(pk=preview["plan_id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert purge_expired_case_previews(batch_size=1) == 1
    plan = HubSkillIntakePlan.objects.get(pk=preview["plan_id"])
    assert plan.payload is None
    assert plan.fingerprint == preview["source_fingerprint"]
    assert purge_expired_case_previews() == 0
    with pytest.raises(ValueError):
        purge_expired_case_previews(batch_size=101)


def test_replay_conflict_does_not_rewrite_later_case(principal, hub_skill, preview):
    request = acceptance(preview)
    accept_dataset_case(
        principal=principal, resource_id=hub_skill.id, kind="skill", **request
    )
    request["case"]["prompt"] += "different"
    with pytest.raises(SkillsHubError) as error:
        accept_dataset_case(
            principal=principal, resource_id=hub_skill.id, kind="skill", **request
        )
    assert error.value.code == "idempotency_conflict"
