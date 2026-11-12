"""Cloud-only edit regressions after a worker persists effective version params."""

from unittest.mock import patch

import pytest

from model_hub.models.run_prompt import PromptVersion
from model_hub.tests.test_experiment_version_params import (
    body,
    create_run,
    provider_payloads,
    setup_run,
)
from model_hub.tests.test_experiment_version_params import (
    version_world as _version_world,
)

version_world = _version_world
pytestmark = pytest.mark.django_db(transaction=True)


def update_run(auth_client, record, entry):
    with patch(
        "tfc.temporal.experiments.start_experiment_v2_workflow",
        return_value="params-edit-test",
    ) as start:
        response = auth_client.put(
            f"/model-hub/experiments/v2/{record.experiment_dataset.experiment_id}/",
            {"prompt_config": [entry]},
            format="json",
        )
    assert response.status_code == 200, response.content
    record.refresh_from_db()
    return start


@pytest.mark.parametrize("echo", ["original", "effective", "dsx"])
@pytest.mark.parametrize("saved_tools", [False, True])
def test_unchanged_edit_after_worker_keeps_run(
    auth_client, version_world, echo, saved_tools
):
    version = version_world[2]
    version.prompt_config_snapshot["configuration"]["top_p"] = 1
    if saved_tools:
        from model_hub.tests.test_experiment_version_params import SAVED_TOOL

        version.prompt_config_snapshot["configuration"].update(
            tools=[SAVED_TOOL], tool_choice="required"
        )
    version.save(update_fields=["prompt_config_snapshot"])
    request = body(version_world, {}, {"toolChoice": "auto", "tools": []})
    record = create_run(auth_client, request)
    setup_run(record)
    entry = {**request["prompt_config"][0], "id": str(record.id)}
    if echo in ("effective", "dsx"):
        # Echo the actual detail response's editable fields, not a facsimile.
        detail = auth_client.get(
            f"/model-hub/experiments/v2/{record.experiment_dataset.experiment_id}/"
        )
        assert detail.status_code == 200, detail.content
        stored = detail.json()["result"]["prompt_configs"][0]
        assert stored["id"] == str(record.id)
        entry["model_params"] = stored["model_params"]
        if echo == "effective":
            entry["configuration"] = stored["configuration"]
    start = update_run(auth_client, record, entry)
    start.assert_not_called()
    assert record.model_params["temperature"] == 0
    assert record.model_params["max_tokens"] == 64


@pytest.mark.parametrize("include_model", [False, True])
@pytest.mark.parametrize("override", [None, {"temperature": 0.7, "maxTokens": 256}])
def test_new_version_uses_new_defaults_not_previous_effective_params(
    auth_client, version_world, include_model, override
):
    request = body(version_world, {}, {"toolChoice": "auto", "tools": []})
    record = create_run(auth_client, request)
    setup_run(record)
    version = PromptVersion.no_workspace_objects.create(
        original_template=version_world[1],
        template_version="v2",
        is_draft=False,
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "new saved version"}],
            "configuration": {"temperature": 0.2, "max_tokens": 128},
        },
    )
    entry = {
        "id": str(record.id),
        "prompt_id": str(version_world[1].id),
        "prompt_version": str(version.id),
        "configuration": {"toolChoice": "auto", "tools": []},
    }
    if include_model:
        entry["model"] = record.model
    if override is not None:
        entry["model_params"] = override
    if not include_model:
        # The API has always required model, even for existing IDs. Do not
        # broaden that contract to fix an unreachable internal fallback.
        with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
            response = auth_client.put(
                f"/model-hub/experiments/v2/{record.experiment_dataset.experiment_id}/",
                {"prompt_config": [entry]},
                format="json",
            )
        assert response.status_code == 400
        assert "Prompt entries require a model" in str(response.json())
        start.assert_not_called()
        record.refresh_from_db()
        assert record.prompt_version_id == version_world[2].id
        return
    start = update_run(auth_client, record, entry)
    assert start.call_count == 1
    assert start.call_args.kwargs["rerun_prompt_config_ids"] == [str(record.id)]
    received = provider_payloads(record, setup_run(record))
    expected = {"temperature": 0.2, "max_tokens": 128}
    if override:
        expected = {"temperature": 0.7, "max_tokens": 256}
    assert [{key: payload.get(key) for key in expected} for payload in received] == [
        expected
    ]


def test_real_parameter_edit_still_reruns(auth_client, version_world):
    request = body(version_world, {})
    record = create_run(auth_client, request)
    setup_run(record)
    entry = {
        **request["prompt_config"][0],
        "id": str(record.id),
        "model_params": {"temperature": 0.9, "maxTokens": 96},
    }
    start = update_run(auth_client, record, entry)
    assert start.call_count == 1
    assert start.call_args.kwargs["rerun_prompt_config_ids"] == [str(record.id)]
    received = provider_payloads(record, setup_run(record))
    assert received[0]["temperature"] == 0.9
    assert received[0]["max_tokens"] == 96
