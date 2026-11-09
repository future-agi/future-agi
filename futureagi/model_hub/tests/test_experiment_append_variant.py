"""Append a prompt variant without rewriting any existing experiment results."""

from copy import deepcopy
from unittest.mock import ANY, patch

import pytest

from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentPromptConfig
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy

pytestmark = pytest.mark.django_db
tenants = tenancy.tenants
START = "tfc.temporal.experiments.start_experiment_v2_workflow"


def prompt(actor, name="original", *, draft=False):
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
        organization=actor["org"],
        workspace=actor["workspace"],
        created_by=actor["user"],
    )
    version = PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        is_draft=draft,
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": name}],
            "configuration": {"temperature": 0, "max_tokens": 64},
        },
    )
    return template, version


def entry(template, version, model="gpt-4o-mini"):
    return {
        "name": "New variant",
        "prompt_id": str(template.id),
        "prompt_version": str(version.id),
        "model": model,
        "model_params": {"temperature": 0, "max_tokens": 64},
        "configuration": {},
        "output_format": "string",
    }


def prepare(actor):
    experiment = actor["exp"]["experiment"]
    edt = ExperimentDatasetTable.no_workspace_objects.get(experiment=experiment)
    template, version = prompt(actor)
    config = ExperimentPromptConfig.no_workspace_objects.create(
        experiment_dataset=edt,
        prompt_template=template,
        prompt_version=version,
        name="Original variant",
        model="gpt-4o-mini",
        model_params={"temperature": 0, "max_tokens": 64},
    )
    url = f"/model-hub/experiments/v2/{experiment.id}/"
    return experiment, config, template, version, url


def snapshot(experiment):
    return {
        "configs": list(
            ExperimentPromptConfig.no_workspace_objects.filter(
                experiment_dataset__experiment=experiment
            )
            .order_by("id")
            .values()
        ),
        "datasets": list(
            ExperimentDatasetTable.no_workspace_objects.filter(experiment=experiment)
            .order_by("id")
            .values()
        ),
        "columns": list(
            Column.no_workspace_objects.filter(dataset=experiment.snapshot_dataset)
            .order_by("id")
            .values()
        ),
        "cells": list(
            Cell.no_workspace_objects.filter(dataset=experiment.snapshot_dataset)
            .order_by("id")
            .values()
        ),
    }


@pytest.mark.parametrize("changed", ["model", "prompt"])
def test_append_variant_preserves_original_and_dispatches_only_new_config(
    tenants, changed
):
    actor = tenants["a"]
    experiment, old, template, version, url = prepare(actor)
    before = snapshot(experiment)
    if changed == "prompt":
        template, version = prompt(actor, "changed prompt")
    new_entry = entry(template, version, "gpt-4o" if changed == "model" else old.model)
    with patch(START, return_value="new-variant-workflow") as start:
        response = actor["client"].put(
            url, {"append_only": True, "prompt_config": [new_entry]}, format="json"
        )
    assert response.status_code == 200, response.content
    after = snapshot(experiment)
    for kind in ("configs", "datasets", "columns", "cells"):
        persisted = {row["id"]: row for row in after[kind]}
        assert all(persisted[row["id"]] == row for row in before[kind]), kind
    assert len(after["configs"]) == len(before["configs"]) + 1
    child = (
        ExperimentPromptConfig.no_workspace_objects.filter(
            experiment_dataset__experiment=experiment
        )
        .exclude(id=old.id)
        .get()
    )
    assert child.prompt_version_id == version.id
    assert child.model == new_entry["model"]
    assert child.order > old.order
    assert child.experiment_dataset_id != old.experiment_dataset_id
    assert child.experiment_dataset.columns.filter(deleted=False).count() == 1
    start.assert_called_once_with(
        experiment_id=str(experiment.id),
        admission_id=ANY,
        rerun_prompt_config_ids=[str(child.id)],
        rerun_agent_config_ids=[],
        rerun_eval_template_ids=[],
        column_changed=False,
    )
    body = actor["client"].get(url).json()["result"]
    assert {item["id"] for item in body["prompt_configs"]} == {
        str(old.id),
        str(child.id),
    }


@pytest.mark.parametrize(
    "case",
    [
        "foreign",
        "mismatch",
        "draft",
        "missing_pair",
        "existing_id",
        "empty",
        "multiple",
        "mixed_update",
    ],
)
def test_invalid_append_rejected_before_any_write_or_dispatch(tenants, case):
    actor = tenants["a"]
    experiment, old, template, version, url = prepare(actor)
    if case == "foreign":
        template, version = prompt(tenants["b"], "foreign prompt")
    elif case == "mismatch":
        _, version = prompt(actor, "different prompt")
    elif case == "draft":
        template, version = prompt(actor, "draft prompt", draft=True)
    body = {"append_only": True, "prompt_config": [entry(template, version)]}
    if case == "missing_pair":
        del body["prompt_config"][0]["prompt_id"]
    elif case == "existing_id":
        body["prompt_config"][0]["id"] = str(old.id)
    elif case == "empty":
        body["prompt_config"] = []
    elif case == "multiple":
        body["prompt_config"] *= 2
    elif case == "mixed_update":
        body["column_id"] = None
    before = snapshot(experiment)
    with patch(START) as start:
        response = actor["client"].put(url, body, format="json")
    assert response.status_code == 400, response.content
    assert snapshot(experiment) == before
    start.assert_not_called()


def test_foreign_experiment_cannot_append_and_owner_has_positive_control(tenants):
    actor = tenants["a"]
    experiment, _, template, version, url = prepare(actor)
    before = snapshot(experiment)
    body = {"append_only": True, "prompt_config": [entry(template, version, "gpt-4o")]}
    with patch(START, return_value="new-variant-workflow") as start:
        denied = tenants["b"]["client"].put(url, deepcopy(body), format="json")
        assert denied.status_code == 404, denied.content
        assert snapshot(experiment) == before
        start.assert_not_called()
        accepted = actor["client"].put(url, body, format="json")
        assert accepted.status_code == 200, accepted.content
        start.assert_called_once()


def test_ambiguous_dispatch_retains_intent_and_retry_never_duplicates_variant(tenants):
    actor = tenants["a"]
    experiment, _, template, version, url = prepare(actor)
    before = snapshot(experiment)
    body = {"append_only": True, "prompt_config": [entry(template, version, "gpt-4o")]}
    with patch(START, side_effect=RuntimeError("workflow unavailable")) as failed:
        response = actor["client"].put(url, body, format="json")
    assert response.status_code == 503, response.content
    assert response.json()["code"] == "experiment_admission_unconfirmed"
    pending = snapshot(experiment)
    assert len(pending["configs"]) == len(before["configs"]) + 1
    for kind in before:
        persisted = {row["id"]: row for row in pending[kind]}
        assert all(persisted[row["id"]] == row for row in before[kind]), kind
    with patch(START, return_value="recovered-workflow") as resumed:
        response = actor["client"].put(url, body, format="json")
        assert response.status_code == 200, response.content
        assert snapshot(experiment) == pending
        assert resumed.call_args == failed.call_args
        again = actor["client"].put(url, body, format="json")
        assert again.status_code == 200, again.content
        assert snapshot(experiment) == pending
        resumed.assert_called_once()


@pytest.mark.parametrize("target", ["prompt", "experiment"])
def test_append_rejects_other_workspace_in_the_same_organization(tenants, target):
    from accounts.models.workspace import Workspace

    actor = tenants["a"]
    experiment, _, template, version, url = prepare(actor)
    other_workspace = Workspace.objects.create(
        name="Other experiment workspace",
        organization=actor["org"],
        created_by=actor["user"],
        is_default=False,
    )
    valid = {"append_only": True, "prompt_config": [entry(template, version, "gpt-4o")]}
    body = deepcopy(valid)
    victim = experiment
    denied_url = url
    expected = 400
    if target == "prompt":
        other_template, other_version = prompt({**actor, "workspace": other_workspace})
        body["prompt_config"] = [entry(other_template, other_version, "gpt-4o")]
    else:
        other = tenancy._make_experiment(
            actor["org"], other_workspace, actor["user"], "other-workspace"
        )
        victim = other["experiment"]
        denied_url = f"/model-hub/experiments/v2/{victim.id}/"
        expected = 404
    before = snapshot(victim)
    with patch(START, return_value="new-variant-workflow") as start:
        denied = actor["client"].put(denied_url, body, format="json")
        assert denied.status_code == expected, denied.content
        assert snapshot(victim) == before
        start.assert_not_called()
        accepted = actor["client"].put(url, valid, format="json")
        assert accepted.status_code == 200, accepted.content
        start.assert_called_once()
