"""Append one prompt variant while retaining all previous configs and cells."""

from model_hub.serializers.experiments import ExperimentDetailV2Serializer
from model_hub.services.experiment_submit import (
    Submission,
    matching_submission,
    stage_submission,
    submit_transaction,
)


def validate_append_payload(data):
    if not data.get("append_only"):
        return
    if set(data) != {"append_only", "prompt_config"}:
        raise ValueError("Append a variant separately from other experiment edits.")
    entries = data["prompt_config"]
    if len(entries) != 1 or entries[0].get("id"):
        raise ValueError(
            "Append exactly one new variant without an existing config id."
        )
    entry = entries[0]
    if entry.get("agent_id") or entry.get("agent_version"):
        raise ValueError("Only prompt variants support re-run with changes.")
    if not entry.get("prompt_id") or not entry.get("prompt_version"):
        raise ValueError("Select a published prompt and its version.")
    model = entry.get("model")
    name = model.get("name") if isinstance(model, dict) else model
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Select a model for the new variant.")


@submit_transaction
def append_variant_response(view, request, experiment_id, data):
    from model_hub.views.experiments import (
        _build_and_save_v2_column_order,
        _diff_and_update_configs,
        _precreate_eval_columns_for_configs,
        _request_prompt_versions,
        _scoped_experiment_queryset,
    )

    experiment = (
        _scoped_experiment_queryset(request)
        .select_for_update(of=("self",))
        .get(id=experiment_id)
    )
    if experiment.experiment_type != "llm" or not experiment.snapshot_dataset_id:
        raise ValueError("Re-run with changes requires a prompt experiment snapshot.")
    entry = data["prompt_config"][0]
    version = (
        _request_prompt_versions(request).filter(id=entry["prompt_version"]).first()
    )
    if (
        not version
        or str(version.original_template_id) != str(entry["prompt_id"])
        or version.is_draft
    ):
        raise ValueError(
            "Select an accessible published version of the selected prompt."
        )
    if not matching_submission(experiment, data, "append_variant"):
        prompt_ids, agent_ids = _diff_and_update_configs(
            experiment, [entry], request=request, append_only=True
        )
        _precreate_eval_columns_for_configs(experiment, prompt_ids, agent_ids)
        _build_and_save_v2_column_order(experiment, experiment.snapshot_dataset)
        stage_submission(
            experiment,
            data,
            "append_variant",
            rerun_prompt_config_ids=prompt_ids,
            rerun_agent_config_ids=[],
            rerun_eval_template_ids=[],
            column_changed=False,
        )
    serializer = ExperimentDetailV2Serializer(
        experiment, context={"organization_id": str(experiment.dataset.organization_id)}
    )
    return Submission(str(experiment.id), view._gm.success_response(serializer.data))
