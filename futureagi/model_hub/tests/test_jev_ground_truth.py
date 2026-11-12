"""W10-EVALS-L02: Jev cannot enable or inherit ground-truth calibration."""

from unittest.mock import patch

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalGroundTruth, EvalTemplate, EvalTemplateVersion
from model_hub.utils.jev_templates import validate_jev_binding

pytestmark = [pytest.mark.requires_ee, pytest.mark.django_db]


@pytest.fixture
def grounded_template(organization, workspace):
    template = EvalTemplate.no_workspace_objects.create(
        name="jev-ground-truth-regression",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        model="turing_large",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "model": "turing_large",
            "output": "Pass/Fail",
            "rule_prompt": "Judge {{answer}}",
            "required_keys": ["answer"],
        },
        criteria="Judge {{answer}}",
        visible_ui=True,
    )
    gt = EvalGroundTruth.objects.create(
        eval_template=template,
        organization=organization,
        workspace=workspace,
        name="Reference answers",
        columns=["answer", "expected"],
        data=[{"answer": "calibration sentinel", "expected": "Pass"}],
        row_count=1,
        variable_mapping={"answer": "answer"},
        role_mapping={"output": "expected"},
        is_active=True,
        enabled=True,
        embedding_status=EvalGroundTruth.EmbeddingStatus.COMPLETED,
    )
    return template, gt


@pytest.mark.parametrize("enabled", [True, None, False])
def test_jev_setup_rejects_enabling_but_allows_disabling(
    auth_client, grounded_template, enabled
):
    template, gt = grounded_template
    template.model = template.config["model"] = "jev-latest"
    template.save(update_fields=["model", "config"])
    gt.enabled = False
    gt.save(update_fields=["enabled"])
    payload = {
        "variable_mapping": {"answer": "answer"},
        "role_mapping": {"output": "expected"},
        "max_examples": 3,
    }
    if enabled is not None:
        payload["enabled"] = enabled
    response = auth_client.put(
        f"/model-hub/ground-truth/{gt.id}/setup/", payload, format="json"
    )
    assert response.status_code == (200 if enabled is False else 400), response.data
    if enabled is not False:
        assert "Ground truth is not supported with Jev" in str(response.data)
    gt.refresh_from_db()
    assert gt.enabled is False
    assert gt.data == [{"answer": "calibration sentinel", "expected": "Pass"}]


@pytest.mark.parametrize("version", [False, True])
def test_switch_to_jev_rejects_enabled_ground_truth_without_writes(
    auth_client, grounded_template, version
):
    template, gt = grounded_template
    versions_before = EvalTemplateVersion.objects.filter(eval_template=template).count()
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        if version:
            response = auth_client.post(
                f"/model-hub/eval-templates/{template.id}/versions/create/",
                {"model": "jev-latest"},
                format="json",
            )
        else:
            response = auth_client.put(
                f"/model-hub/eval-templates/{template.id}/update/",
                {"model": "jev-latest"},
                format="json",
            )
    assert response.status_code == 400, response.data
    assert "JEV_FEW_SHOT_UNSUPPORTED" in str(response.data)
    assert "Ground truth is not supported with Jev" in str(response.data)
    template.refresh_from_db()
    gt.refresh_from_db()
    assert template.model == "turing_large"
    assert gt.enabled is True
    assert EvalTemplateVersion.objects.filter(eval_template=template).count() == versions_before


@pytest.mark.parametrize("embedding_status", ["pending", "completed", "failed"])
def test_run_override_rejects_enabled_ground_truth_before_retrieval(
    grounded_template, embedding_status
):
    template, gt = grounded_template
    gt.embedding_status = embedding_status
    gt.save(update_fields=["embedding_status"])
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        response = validate_jev_binding(
            template, model="turing_large", runtime_config={"run_config": {"model": "jev-latest"}}
        )
    assert response is not None
    assert response.status_code == 400
    assert "JEV_FEW_SHOT_UNSUPPORTED" in str(response.data)


@pytest.mark.parametrize("inactive_field", ["enabled", "is_active", "deleted"])
def test_inactive_ground_truth_does_not_block_jev(grounded_template, inactive_field):
    template, gt = grounded_template
    setattr(gt, inactive_field, inactive_field == "deleted")
    gt.save(update_fields=[inactive_field])
    with patch("tfc.ee_gates._jev_denied_off_cloud", return_value=False):
        assert validate_jev_binding(template, model="jev-latest") is None


def test_non_jev_still_accepts_enabled_ground_truth(grounded_template):
    template, _gt = grounded_template
    assert validate_jev_binding(template, model="turing_large") is None
