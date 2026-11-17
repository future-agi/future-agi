"""Picker-lock regression: inspect the persisted pin, not just onSave's UUID."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]


@pytest.fixture
def pinned_binding(user, organization, workspace):
    """V1 has the saved drawer config; a different V2 is now the default."""
    prompt = "Judge {{output}}"
    definition = {
        "model": "fixture-model",
        "rule_prompt": prompt,
        "messages": [{"role": "system", "content": prompt}],
        "required_keys": ["output"],
        "output": "score",
        "pass_threshold": 0.5,
        "template_format": "mustache",
        "multi_choice": False,
    }
    # EvaluationDrawer forwards these separately from the template definition.
    run_config = {
        "model": "fixture-model",
        "agent_mode": "agent",
        "check_internet": False,
        "summary": {"type": "concise"},
        "tools": [],
        "knowledge_bases": [],
        "data_injection": {"variables_only": True},
        "error_localizer_enabled": False,
        "pass_threshold": 0.5,
        "multi_choice": False,
    }
    template = EvalTemplate.no_workspace_objects.create(
        name="picker-pin-template",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="llm",
        config=deepcopy(definition),
        criteria=prompt,
        model="fixture-model",
        pass_threshold=0.5,
        output_type_normalized="percentage",
    )
    # The service flattens config + run_config into this snapshot. In particular,
    # it predates the picker's derived version_pin metadata.
    snapshot = {**deepcopy(definition), **deepcopy(run_config)}
    v1 = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=snapshot,
        criteria=prompt,
        model=template.model,
        user=user,
        organization=organization,
        workspace=workspace,
    )
    v2 = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot={**deepcopy(snapshot), "rule_prompt": "New {{output}}"},
        criteria="New {{output}}",
        model=template.model,
        user=user,
        organization=organization,
        workspace=workspace,
    )
    dataset = Dataset.no_workspace_objects.create(
        name="picker-pin-dataset",
        organization=organization,
        workspace=workspace,
    )
    metric = UserEvalMetric.objects.create(
        name="picker-pin-binding",
        dataset=dataset,
        template=template,
        organization=organization,
        workspace=workspace,
        model=template.model,
        pinned_version=v1,
        config={
            "mapping": {"output": "output-column"},
            "config": deepcopy(definition),
            "run_config": deepcopy(run_config),
        },
    )
    return SimpleNamespace(metric=metric, v1=v1, v2=v2, snapshot=snapshot)


def _drawer_payload(binding, threshold=0.5):
    metric = binding.metric
    payload = {
        "template_id": str(metric.template_id),
        "name": metric.name,
        "model": metric.model,
        "pinned_version_id": str(binding.v1.id),
        "run": False,
        "error_localizer": False,
        "config": deepcopy(metric.config),
    }
    # These are emitted even when the user simply reopens and clicks Update.
    payload["config"]["config"]["version_pin"] = "v1"
    payload["config"]["config"]["pass_threshold"] = threshold
    payload["config"]["run_config"]["pass_threshold"] = threshold
    return payload


def _update(binding, payload, user, organization, workspace):
    # Bypass middleware, not the serializer/view/service/version manager/save.
    # run=False ensures the regression never asks an evaluator to execute.
    from model_hub.views.develop_dataset import EditAndRunUserEvalView

    metric = binding.metric
    request = APIRequestFactory().post(
        f"/model-hub/develops/{metric.dataset_id}/edit_and_run_user_eval/{metric.id}/",
        payload,
        format="json",
    )
    force_authenticate(request, user=user)
    request.organization = organization
    request.workspace = workspace
    response = EditAndRunUserEvalView.as_view()(
        request,
        dataset_id=metric.dataset_id,
        eval_id=metric.id,
    )
    assert response.status_code == 200, response.data
    metric.refresh_from_db()


@pytest.mark.parametrize("snapshot_has_pin", [False, True])
@pytest.mark.parametrize("threshold", [0.5, 0.75])
def test_update_preserves_binding_config_and_old_snapshot(
    pinned_binding,
    user,
    organization,
    workspace,
    snapshot_has_pin,
    threshold,
):
    """D1 may repin on save; binding values and immutable history must survive."""
    binding = pinned_binding
    if snapshot_has_pin:
        binding.v1.config_snapshot["version_pin"] = "v1"
        binding.v1.save(update_fields=["config_snapshot"])
    original_snapshot = deepcopy(binding.v1.config_snapshot)
    payload = _drawer_payload(binding, threshold)

    _update(binding, payload, user, organization, workspace)

    assert binding.metric.config["config"]["version_pin"] == "v1"
    assert binding.metric.config["run_config"]["pass_threshold"] == threshold
    assert binding.metric.config["config"]["pass_threshold"] == threshold
    binding.v1.refresh_from_db()
    binding.v2.refresh_from_db()
    assert binding.v1.config_snapshot == original_snapshot
    assert binding.v1.pass_threshold == 0.5


@pytest.mark.parametrize("case_index", [0, 1])
def test_picker_serialized_edit_reaches_saved_snapshot(
    pinned_binding, user, organization, workspace, case_index
):
    """The FE asserts exact equality with this request, not a second serializer."""
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "frontend/src/sections/common/EvalPicker/__tests__/bindingSnapshot.fixture.json"
    )
    fixture = json.loads(fixture_path.read_text())
    case = fixture["cases"][case_index]
    binding = pinned_binding
    binding.v1.config_snapshot = deepcopy(fixture["base"])
    binding.v1.save(update_fields=["config_snapshot"])
    old_snapshot = deepcopy(binding.v1.config_snapshot)
    binding.metric.config = {
        "mapping": {"output": "output-column"},
        "config": {},
        "run_config": deepcopy(case["storedRunConfig"]),
    }
    binding.metric.save(update_fields=["config"])
    payload = {
        "template_id": str(binding.metric.template_id),
        "name": binding.metric.name,
        "model": "fixture-model",
        "error_localizer": False,
        "run": False,
        "config": deepcopy(case["requestConfig"]),
    }

    _update(binding, payload, user, organization, workspace)

    assert binding.metric.pinned_version_id != binding.v1.id
    # The wire contract is exact, but the view also derives prompt variables
    # and defaults reason_column before storing the binding. Assert the entire
    # normalized row, without dropping either server-derived field.
    expected_config = deepcopy(case["requestConfig"])
    expected_config["config"]["required_keys"] = ["output"]
    expected_config["reason_column"] = True
    assert binding.metric.config == expected_config
    saved = binding.metric.pinned_version
    assert saved.config_snapshot[case["field"]] == case["value"]
    assert (
        saved.config_snapshot["messages"] == case["requestConfig"]["config"]["messages"]
    )
    # With no eval_type_id, config_to_prompt_messages uses its criteria-shaped
    # migration format rather than copying config.messages. Keep both exact
    # contracts: the edited turns in config_snapshot and this derived field.
    assert saved.prompt_messages == [
        {
            "role": "system",
            "name": "criteria",
            "content": "Judge {{output}}\n\n{{output}}",
        }
    ]
    assert saved.config_snapshot["required_keys"] == ["output"]
    assert (
        saved.config_snapshot["few_shot_examples"]
        == case["requestConfig"]["config"]["few_shot_examples"]
    )
    for key, value in case["requestConfig"]["run_config"].items():
        assert saved.config_snapshot[key] == value
    binding.v1.refresh_from_db()
    assert binding.v1.config_snapshot == old_snapshot


def test_sparse_historical_runtime_edit_does_not_inherit_live_definition(
    pinned_binding, user, organization, workspace
):
    """An implicit new version derives from the pin, never today's template."""
    binding = pinned_binding
    metric = binding.metric
    binding.v1.config_snapshot["data_injection"] = {"full_row": True}
    binding.v1.config_snapshot["messages"] = [
        {"role": "system", "content": binding.v1.criteria},
        {"role": "user", "content": "Evaluate {{output}} using the historical rules"},
    ]
    binding.v1.save(update_fields=["config_snapshot"])
    old_snapshot = deepcopy(binding.v1.config_snapshot)
    template = metric.template
    template.config = {
        **template.config,
        "rule_prompt": "Live {{output}}",
        "data_injection": {"variables_only": True},
        "live_only": True,
    }
    template.criteria = "Live {{output}}"
    template.save(update_fields=["config", "criteria"])
    metric.config = {"mapping": {"output": "output-column"}, "config": {}}
    metric.save(update_fields=["config"])
    payload = {
        "template_id": str(metric.template_id),
        "name": metric.name,
        "model": metric.model,
        "run": False,
        "error_localizer": False,
        "config": {
            **metric.config,
            "run_config": {"check_internet": True},
        },
    }

    _update(binding, payload, user, organization, workspace)

    assert metric.pinned_version_id != binding.v1.id
    snapshot = metric.pinned_version.config_snapshot
    assert snapshot["rule_prompt"] == old_snapshot["rule_prompt"]
    assert snapshot["data_injection"] == {"full_row": True}
    assert snapshot["messages"] == old_snapshot["messages"]
    assert snapshot["check_internet"] is True
    assert "live_only" not in snapshot
    assert metric.pinned_version.criteria == binding.v1.criteria
    binding.v1.refresh_from_db()
    assert binding.v1.config_snapshot == old_snapshot


@pytest.mark.parametrize("snapshot_has_pin", [False, True])
class TestPickerDefinitionEdits:
    def test_definition_edit_still_creates_database_version(
        self,
        pinned_binding,
        user,
        organization,
        workspace,
        snapshot_has_pin,
    ):
        """Picker edits omit selection, regardless of stored pin metadata."""
        binding = pinned_binding
        if snapshot_has_pin:
            binding.v1.config_snapshot["version_pin"] = "v1"
            binding.v1.save(update_fields=["config_snapshot"])
        original_snapshot = deepcopy(binding.v1.config_snapshot)
        payload = _drawer_payload(binding, 0.75)
        payload["config"]["config"]["rule_prompt"] = "Edited {{output}}"
        payload["config"]["config"]["messages"] = [
            {"role": "system", "content": "Edited {{output}}"},
        ]
        # A present pinned_version_id is selection-only at this endpoint. The real
        # picker omits it for definition edits; config.version_pin is just metadata.
        del payload["pinned_version_id"]

        _update(binding, payload, user, organization, workspace)

        assert binding.metric.pinned_version_id != binding.v1.id
        assert binding.metric.pinned_version.version_number == 3
        assert (
            binding.metric.pinned_version.config_snapshot["rule_prompt"]
            == "Edited {{output}}"
        )
        assert binding.metric.config["config"]["rule_prompt"] == "Edited {{output}}"
        assert binding.metric.config["run_config"]["pass_threshold"] == 0.75
        assert (
            EvalTemplateVersion.objects.filter(
                eval_template=binding.metric.template
            ).count()
            == 3
        )
        binding.v1.refresh_from_db()
        assert binding.v1.config_snapshot == original_snapshot


@pytest.mark.parametrize("echo_pin", [False, True])
def test_definition_edit_respects_explicit_pin(
    pinned_binding,
    user,
    organization,
    workspace,
    echo_pin,
):
    """Edits persist, but only omission of an explicit pin creates a version."""
    binding = pinned_binding
    versions = EvalTemplateVersion.objects.filter(
        eval_template=binding.metric.template
    ).order_by("version_number")
    original_versions = list(versions.values())
    payload = _drawer_payload(binding, 0.75)
    payload["config"]["config"]["rule_prompt"] = "Edited {{output}}"
    payload["config"]["config"]["messages"] = [
        {"role": "system", "content": "Edited {{output}}"},
    ]
    if not echo_pin:
        # The dataset picker omits versionId when the definition was edited.
        del payload["pinned_version_id"]

    _update(binding, payload, user, organization, workspace)

    if echo_pin:
        # C7 treats even the current UUID as an explicit saved-version selection.
        assert binding.metric.pinned_version_id == binding.v1.id
        assert list(versions.values()) == original_versions
    else:
        assert binding.metric.pinned_version_id != binding.v1.id
        assert binding.metric.pinned_version.version_number == 3
        assert (
            binding.metric.pinned_version.config_snapshot["rule_prompt"]
            == "Edited {{output}}"
        )
    assert versions.count() == (2 if echo_pin else 3)
    assert binding.metric.config["config"]["rule_prompt"] == "Edited {{output}}"
    assert binding.metric.config["config"]["messages"] == [
        {"role": "system", "content": "Edited {{output}}"},
    ]
    assert binding.metric.config["config"]["pass_threshold"] == 0.75
    assert binding.metric.config["run_config"]["pass_threshold"] == 0.75
    binding.v1.refresh_from_db()
    assert binding.v1.config_snapshot == binding.snapshot
