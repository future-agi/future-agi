"""An experiment's inline prompts, from before experiments kept a snapshot of
their rows and saved prompt configs.

Such an experiment keeps each prompt only in its own prompt_config: a name,
messages naming dataset columns by id ({{<column id>}}), a list of models,
a configuration and per-model params. Its detail read shows them
(inline_prompt_entries), and a copy (Duplicate & edit) runs them through a
saved prompt made when the copy is saved (saved_prompt_for). The saved prompt
is built by get_or_create_prompt_template, the same function the
migrate_experiment_prompts command converts such experiments with. Only LLM
prompts are shown and copied; the type is read as the conversion reads it
(detect_experiment_type).
"""

import contextlib
import copy
import uuid

from model_hub.models.develop_dataset import Column
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.services.prompt_label import events_suppressed
from model_hub.views.utils.utils import update_column_id


def extract_model_info(model_spec):
    """Extract model name, display name, and run_prompt_config from model spec."""
    if isinstance(model_spec, str):
        return model_spec, None, None
    elif isinstance(model_spec, dict):
        return (
            model_spec.get("name", ""),
            model_spec.get("display_name"),
            model_spec.get("config"),
        )
    return str(model_spec), None, None


def detect_experiment_type(prompt_config):
    """The experiment type its inline prompts name: tts, stt or image when
    any prompt says so (model_type), else llm. The conversion command reads
    an experiment's type with this rule."""
    if not isinstance(prompt_config, list):
        return "llm"
    for entry in prompt_config:
        if not isinstance(entry, dict):
            continue
        model_type = entry.get("model_type", "llm")
        if model_type in ("tts", "stt", "image"):
            return model_type
    return "llm"


def inline_experiment_type(experiment):
    """An experiment's type: the one it stores when that is not llm (the
    older create stored none), else the one its inline prompts name."""
    stored = getattr(experiment, "experiment_type", None) or "llm"
    if stored != "llm":
        return stored
    return detect_experiment_type(experiment.prompt_config)


def has_inline_prompts_only(experiment):
    """True for an experiment from before snapshots: no snapshot and no saved
    prompt or agent config, its prompts only in its inline prompt_config."""
    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentPromptConfig,
    )

    if experiment.snapshot_dataset_id:
        return False
    if ExperimentPromptConfig.objects.filter(
        experiment_dataset__experiment=experiment
    ).exists():
        return False
    return not ExperimentAgentConfig.objects.filter(
        experiment_dataset__experiment=experiment
    ).exists()


def inline_prompt_entries(experiment):
    """One entry per inline prompt and model, in order, for an LLM
    experiment from before snapshots; [] for any other experiment (a voice or
    image one too: a copy runs LLM prompts only). Its messages name the
    dataset's columns as a saved prompt does ({{<column name>}}): what the
    saved prompt made for a copy holds. Nothing is written."""
    prompt_config = experiment.prompt_config
    if not isinstance(prompt_config, list) or not has_inline_prompts_only(experiment):
        return []
    if inline_experiment_type(experiment) != "llm":
        return []
    names = column_names(experiment.dataset)
    entries = []
    for index, prompt in enumerate(prompt_config):
        if not isinstance(prompt, dict):
            continue
        for model_spec in prompt.get("model") or []:
            entries.append(_inline_entry(prompt, index, model_spec, names))
    return entries


def _inline_entry(prompt, index, model_spec, names):
    """The stored prompt ``index`` with one model: its name, settings and
    messages as stored, its params those stored for that model."""
    model_name, display_name, _config = extract_model_info(model_spec)
    return {
        "name": prompt.get("name", "unnamed"),
        "prompt_index": index,
        "model": model_name,
        "model_display_name": display_name,
        "model_params": (prompt.get("model_params") or {}).get(model_name, {}),
        "configuration": prompt.get("configuration") or {},
        "output_format": prompt.get(
            "outputFormat", prompt.get("output_format", "string")
        ),
        "messages": with_column_names(prompt.get("messages"), names),
    }


def column_names(dataset):
    """Each column id of the dataset (str) to its name."""
    return {
        str(column.id): column.name
        for column in Column.objects.filter(dataset=dataset, deleted=False)
    }


def with_column_names(messages, names):
    """The messages with each {{<column id>}} read as {{<column name>}}, as
    the conversion names a saved prompt's variables."""
    if not messages or not names:
        return messages or []
    return [update_column_id(copy.deepcopy(message), names) for message in messages]


def get_or_create_prompt_template(
    exp,
    config_name,
    messages,
    configuration=None,
    output_format="string",
    model_name="",
    display_name=None,
    model_params=None,
    *,
    suppress_events=None,
):
    """Create or find PromptTemplate + PromptVersion for an LLM experiment config.

    A saved prompt is found, and its v1 taken, only when it has this name, is
    in the experiment's dataset's workspace and its committed v1 holds these
    messages: never one in another workspace, a later edit or a draft.
    Otherwise a new one is made, even when one of the same name exists.

    A new version publishes prompt.version.created, as any committed version
    does, unless ``suppress_events`` names why not (the conversion command: a
    data migration, not a person's commit; webhooks DESIGN.md section 14.3).
    """
    template_name = f"[Migrated] {exp.name} - {config_name} - {model_name}"

    # Get org/workspace from the experiment's dataset
    org = getattr(exp.dataset, "organization", None)
    workspace = (
        getattr(exp.dataset, "workspace", None)
        if hasattr(exp.dataset, "workspace")
        else None
    )

    found = _saved_v1(template_name, org, workspace, messages)
    if found is not None:
        return found

    # Create new template
    template = PromptTemplate.all_objects.create(
        id=uuid.uuid4(),
        name=template_name,
        organization=org,
        workspace=workspace,
    )

    # Build prompt_config_snapshot as a dict with messages, placeholders,
    # and configuration — matching the format used by the prompt template UI
    config = configuration or {}
    params = model_params or {}
    snapshot = {
        "messages": messages,
        "placeholders": [],
        "configuration": {
            "model": model_name,
            "tools": config.get("tools", []),
            "modelType": "all",
            "toolChoice": config.get("tool_choice", ""),
            "modelDetail": {
                "logoUrl": params.get("logo_url", ""),
                "providers": params.get("providers", ""),
                "modelName": display_name or model_name,
                "isAvailable": True,
            },
            "outputFormat": output_format,
        },
    }

    # Create version with full snapshot dict. Its webhook event is suppressed
    # only when the caller names why (suppress_events).
    publishing = (
        events_suppressed(suppress_events)
        if suppress_events
        else contextlib.nullcontext()
    )
    with publishing:
        version = PromptVersion.all_objects.create(
            id=uuid.uuid4(),
            original_template=template,
            prompt_config_snapshot=snapshot,
            template_version="v1",
            is_default=True,
        )

    return template, version


def _saved_v1(template_name, organization, workspace, messages):
    """(template, v1) of a live template of this name in this workspace whose
    committed v1 holds ``messages`` (the earliest such), else None."""
    versions = (
        PromptVersion.all_objects.select_related("original_template")
        .filter(
            original_template__name=template_name,
            original_template__organization=organization,
            original_template__workspace=workspace,
            original_template__deleted=False,
            template_version="v1",
            is_draft=False,
            deleted=False,
        )
        .order_by("original_template__created_at", "original_template_id")
    )
    for version in versions:
        if _messages_of(version.prompt_config_snapshot) == messages:
            return version.original_template, version
    return None


def _messages_of(snapshot):
    """A version snapshot's messages (a dict, or a one-item list of one)."""
    if isinstance(snapshot, list):
        snapshot = snapshot[0] if snapshot else {}
    return (snapshot or {}).get("messages") if isinstance(snapshot, dict) else None


class InlinePromptNotFound(ValueError):
    """The prompt and model a copy names are not one of the experiment's
    inline prompts."""


def saved_prompt_for(experiment, prompt_index, model_spec):
    """The saved prompt (PromptTemplate, PromptVersion) that runs the
    experiment's inline prompt ``prompt_index`` with the copy's model
    (``model_spec``: one the prompt ran with, or one added to or swapped
    into the copy): made by get_or_create_prompt_template, or found when it
    was made before. The prompt is found by its index alone; its name,
    messages and settings are read from the stored experiment, never from a
    request. A person's own copy: a new version publishes
    prompt.version.created."""
    model, _display_name, _config = extract_model_info(model_spec)
    entries = [
        entry
        for entry in inline_prompt_entries(experiment)
        if entry["prompt_index"] == prompt_index
    ]
    if not entries or not model:
        raise InlinePromptNotFound("The prompt to copy was not found.")
    entry = next((entry for entry in entries if entry["model"] == model), None)
    if entry is None:
        # A model the prompt did not run with: the same stored prompt, for it.
        entry = _inline_entry(
            experiment.prompt_config[prompt_index],
            prompt_index,
            model_spec,
            column_names(experiment.dataset),
        )
    return get_or_create_prompt_template(
        experiment,
        entry["name"],
        entry["messages"],
        entry["configuration"],
        entry["output_format"],
        entry["model"],
        entry["model_display_name"],
        entry["model_params"],
    )
