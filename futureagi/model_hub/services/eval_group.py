import json

from django.db.models import Q
from django.utils import timezone

from model_hub.models.choices import ModelChoices, OwnerChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.eval_groups import EvalGroup, History
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
from model_hub.schema.eval_group import PageType
from model_hub.serializers.eval_group import EvalGroupSerializer
from model_hub.utils.function_eval_params import (
    get_function_params_schema,
    normalize_eval_runtime_config,
    params_with_defaults_for_response,
)
from model_hub.utils.workspace_scope import (
    explicit_workspace_filter,
    knowledge_base_id_for,
)
from model_hub.views.utils.utils import (
    fetch_required_keys_for_eval_template,
    fetch_specific_mapping_for_specific_eval_template,
)
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.serializers.run_test import SimulateEvalConfigSimpleSerializer
from tfc.middleware.workspace_context import get_current_organization
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer

# =============================================================================
# Helper Functions
# =============================================================================


def _get_eval_templates_for_group(eval_group, deselected_evals=None):
    """Get filtered eval templates for an eval group."""
    template_ids = list(
        eval_group.eval_templates.through.objects.filter(
            evalgroup_id=eval_group.id
        ).values_list("evaltemplate_id", flat=True)
    )
    templates = filter_eval_templates_for_group_scope(
        EvalTemplate.no_workspace_objects.filter(id__in=template_ids),
        eval_group.organization,
        eval_group.workspace,
    )
    if deselected_evals:
        templates = templates.exclude(id__in=deselected_evals)
    return templates


def _generate_config_name(eval_template, eval_group, existing_count):
    """Generate versioned config name."""
    base_name = f"{eval_template.name}_{eval_group.name}"
    if existing_count > 0:
        return f"{base_name}_v{existing_count + 1}"
    return base_name


def _filter_global_params_for_template(eval_template, global_params):
    if not isinstance(global_params, dict) or not global_params:
        return {}

    schema = get_function_params_schema(eval_template.config)
    if not schema:
        return {}

    return {key: value for key, value in global_params.items() if key in schema}


def _accessible_eval_template_filter(organization, workspace):
    user_filter = Q(owner=OwnerChoices.USER.value, organization=organization)
    if workspace:
        user_filter &= Q(workspace=workspace) | Q(workspace__isnull=True)
    return Q(owner=OwnerChoices.SYSTEM.value) | user_filter


def filter_eval_templates_for_group_scope(queryset, organization, workspace):
    # External templates (SRV-5) are scored by the caller and never executed,
    # so they are never added to a group or applied from one.
    from model_hub.models.evals_metric import EXTERNAL_EVAL_TYPE

    return queryset.filter(
        _accessible_eval_template_filter(organization, workspace)
    ).exclude(eval_type=EXTERNAL_EVAL_TYPE)


# Short names older templates keep for the Turing models, read the way the
# evaluation detail reads them (views/separate_evals.py).
_SHORT_TURING_NAMES = {
    "small": ModelChoices.TURING_SMALL.value,
    "large": ModelChoices.TURING_LARGE.value,
    "flash": ModelChoices.TURING_FLASH.value,
}


def _model_name(value):
    if not isinstance(value, str) or not value.strip():
        return None
    name = value.strip()
    return _SHORT_TURING_NAMES.get(name, name)


def template_model(eval_template):
    """The model an evaluation's own template is configured with, or None.

    Read in the order Add evaluation's picker reads it: the template's config
    (its default version's when the template keeps none), then the
    template's model, then its default version's model.
    """
    version = EvalTemplateVersion.objects.get_default(eval_template)
    config = eval_template.config or (version.config_snapshot if version else None)
    candidates = (
        config.get("model") if isinstance(config, dict) else None,
        eval_template.model,
        version.model if version else None,
    )
    for candidate in candidates:
        name = _model_name(candidate)
        if name:
            return name
    return None


#: The model a group member would be stored with can't run on this
#: deployment. ``model`` is the model as the Apply dialog names an added
#: member's (model_display_name).
MODEL_CANT_RUN = "its model, {model}, can't run on this deployment."
#: Why a group member is left out; ``change`` names the one place with a
#: control for that model.
GROUP_MEMBER_MODEL_REASON = MODEL_CANT_RUN + " {change}, then apply again."
#: The model came from the evaluation: its own template's model, or Add
#: evaluation's default when it names none. The evaluation's page sets it.
CHANGE_EVALUATION_MODEL = "Change the evaluation's model"
#: The model came with the request: the Language Model field of the
#: Evaluations drawer where the group is applied.
CHANGE_GROUP_MODEL = "Change the group's model"
#: A built-in evaluation (its page reads "Read-only (system eval)") with no
#: model sent: its own model can't be changed on its page. The reason alone;
#: each place adds the action it offers, by the refusal's code.
BUILT_IN_MEMBER_REASON = "it uses {model}, which can't run here."
#: After Apply, a member that was left out (the toast has no row, so it names
#: the evaluation).
GROUP_MEMBER_NOT_ADDED = "{evaluation} was not added: {reason}"
#: Before Apply, under the member's row in the Apply dialog.
GROUP_MEMBER_WONT_BE_ADDED = "Won't be added: {reason}"

#: What a refusal is, sent beside its sentence so a place tells a built-in
#: evaluation's refusal apart by this code, never by the sentence's words.
REFUSAL_BUILT_IN = "built_in"
#: Any other refusal: the group's model or the evaluation's model to change.
REFUSAL_MODEL = "model"

# The Turing models by the names the Apply dialog gives an added member (the
# model selector's, frontend evaluatorModels.js); any other model, Protect,
# Protect Flash and Turing Large XL among them, by its id.
_MODEL_DISPLAY_NAMES = {
    ModelChoices.TURING_LARGE.value: "Turing Large",
    ModelChoices.TURING_SMALL.value: "Turing Small",
    ModelChoices.TURING_FLASH.value: "Turing Flash",
}


def model_display_name(model):
    return _MODEL_DISPLAY_NAMES.get(model, model)


class GroupMembersRefused(ValueError):
    """Every member of a group apply was refused. The message joins each
    member's sentence; ``refused`` lists them, each with its code."""

    def __init__(self, refused):
        super().__init__(" ".join(refusal["reason"] for refusal in refused))
        self.refused = refused


def group_member_model(eval_template, sent_model=None):
    """The model a group member is applied with: ``(model, None, None)``, or
    ``(None, reason, code)`` when the member is refused, ``reason`` being the
    clause the two sentences share and ``code`` what kind of refusal it is.

    The model is the one the caller sends; without one, the template's own
    model; without that, Add evaluation's default. Whichever it is, the model
    that would be stored is checked by the rule Add evaluation applies to the
    model it receives, in its order: the Jev check, then the Turing check
    (ee_model_gate_for_template). Where that rule refuses it, the member is
    refused; no other model is put in its place.

    The reason, in this order: a model the request sent, the group's model to
    change; a built-in evaluation, that it uses a model which can't run here;
    anything else, a blank model included, the evaluation's model to change.
    """
    from tfc.ee_gates import ee_model_gate_for_template

    model = (
        sent_model or template_model(eval_template) or ModelChoices.TURING_LARGE.value
    )
    refusal = ee_model_gate_for_template(model, eval_type=eval_template.eval_type)
    if refusal is None:
        return model, None, None
    name = model_display_name(model)
    if sent_model:
        reason = GROUP_MEMBER_MODEL_REASON.format(model=name, change=CHANGE_GROUP_MODEL)
        return None, reason, REFUSAL_MODEL
    if eval_template.owner == OwnerChoices.SYSTEM.value:
        return None, BUILT_IN_MEMBER_REASON.format(model=name), REFUSAL_BUILT_IN
    reason = GROUP_MEMBER_MODEL_REASON.format(
        model=name, change=CHANGE_EVALUATION_MODEL
    )
    return None, reason, REFUSAL_MODEL


def group_member_preview(eval_template):
    """What the group read says per member, before Apply sends no model:
    ``(model, None, None)``, or ``(None, "Won't be added: ...", code)``."""
    model, reason, code = group_member_model(eval_template)
    if reason is None:
        return model, None, None
    return None, GROUP_MEMBER_WONT_BE_ADDED.format(reason=reason), code


def _members_with_models(eval_templates, filters, refused):
    """Each member to apply, with its model.

    A refused member is left out and recorded in ``refused`` with its code.
    When every member is refused nothing is applied, and GroupMembersRefused
    is raised with each of them.
    """
    members, refusals = [], []
    for eval_template in eval_templates:
        model, reason, code = group_member_model(eval_template, filters.get("model"))
        if reason is None:
            members.append((eval_template, model))
            continue
        refusals.append(
            {
                "eval_template_id": str(eval_template.id),
                "name": eval_template.name,
                "reason": GROUP_MEMBER_NOT_ADDED.format(
                    evaluation=eval_template.name, reason=reason
                ),
                "code": code,
            }
        )
    if refused is not None:
        refused.extend(refusals)
    if refusals and not members:
        # Every member is named, each with why.
        raise GroupMembersRefused(refusals)
    return members


def get_accessible_eval_templates_by_ids(eval_template_ids, organization, workspace):
    requested_ids = {str(template_id) for template_id in eval_template_ids or []}
    templates = filter_eval_templates_for_group_scope(
        EvalTemplate.no_workspace_objects.filter(id__in=list(requested_ids)),
        organization,
        workspace,
    )
    found_ids = {str(template.id) for template in templates}
    if found_ids != requested_ids:
        raise ValueError(
            "One or more eval templates are unavailable for this workspace."
        )
    return templates


# =============================================================================
# Core Functions
# =============================================================================


def create_eval_group(name, description, eval_template_ids, user, workspace):
    if eval_template_ids is None:
        raise ValueError("eval_template_ids cannot be None")

    _org = get_current_organization() or user.organization
    eval_templates = get_accessible_eval_templates_by_ids(
        eval_template_ids,
        _org,
        workspace,
    )

    eval_group = EvalGroup.objects.create(
        name=name,
        description=description,
        organization=_org,
        created_by=user,
        workspace=workspace,
    )

    serialized_group_data = EvalGroupSerializer(eval_group).data

    history_records = []

    # Add templates to the many-to-many relationship
    eval_group.eval_templates.set(eval_templates)
    evals_with_model = []

    # Create history records for ADD actions
    for eval_template in eval_templates:
        history_record = History(
            source_id=str(eval_group.id),
            source_type="EVAL_GROUP",
            action="ADD",
            action_by=user,
            organization=_org,
            workspace=eval_group.workspace,
            reference_id=str(eval_template.id),
        )
        history_records.append(history_record)

        tags = eval_template.eval_tags
        if tags and len(tags) > 0 and "FUTURE_EVALS" in tags:
            evals_with_model.append(eval_template.name)

    History.objects.bulk_create(history_records)

    required_keys = fetch_required_keys_for_eval_template(eval_templates)

    return {
        **serialized_group_data,
        "required_keys": required_keys,
        "evals_with_model": evals_with_model,
    }


def edit_eval_list_manager(
    eval_group_id, added_template_ids, deleted_template_ids, user, workspace=None
):
    _org = get_current_organization() or user.organization

    # Get the eval group
    try:
        eval_group_query = EvalGroup.objects.filter(id=eval_group_id, organization=_org)
        if workspace:
            eval_group_query = eval_group_query.filter(workspace=workspace)
        eval_group = eval_group_query.get()
    except EvalGroup.DoesNotExist as e:
        raise Exception("Eval group does not exist") from e

    template_ids = list(
        eval_group.eval_templates.through.objects.filter(
            evalgroup_id=eval_group.id
        ).values_list("evaltemplate_id", flat=True)
    )

    # Get current template IDs
    current_template_ids = {str(template_id) for template_id in template_ids}

    history_records = []

    # Handle adding templates
    if (
        added_template_ids
        and isinstance(added_template_ids, list)
        and len(added_template_ids) > 0
    ):
        added_templates = []

        for template_id in added_template_ids:
            template_id_str = str(template_id)

            # Only add if not already present
            if template_id_str not in current_template_ids:
                added_templates.append(template_id)

                # Create history record for ADD action
                history_record = History(
                    source_id=str(eval_group_id),
                    source_type="EVAL_GROUP",
                    action="ADD",
                    action_by=user,
                    organization=_org,
                    workspace=eval_group.workspace,
                    reference_id=str(template_id),
                )
                history_records.append(history_record)

        if added_templates:
            # Get the template objects and add them to the many-to-many relationship
            templates_to_add = get_accessible_eval_templates_by_ids(
                added_templates,
                _org,
                eval_group.workspace,
            )
            eval_group.eval_templates.add(*templates_to_add)

    # Handle deleting templates
    if (
        deleted_template_ids
        and isinstance(deleted_template_ids, list)
        and len(deleted_template_ids) > 0
    ):
        updated_deleted_list = []
        for template_id in deleted_template_ids:
            template_id_str = str(template_id)

            if template_id_str not in current_template_ids:
                raise Exception("Template to be deleted not found in eval group")

            updated_deleted_list.append(template_id)

            # Create history record for DELETE action
            history_record = History(
                source_id=str(eval_group_id),
                source_type="EVAL_GROUP",
                action="DELETE",
                action_by=user,
                organization=_org,
                workspace=eval_group.workspace,
                reference_id=str(template_id),
            )
            history_records.append(history_record)

        if updated_deleted_list:
            deleted_count = eval_group.eval_templates.through.objects.filter(
                evalgroup_id=eval_group.id, evaltemplate_id__in=updated_deleted_list
            ).delete()[0]

            if deleted_count == 0:
                raise Exception(
                    f"No relationship found between eval group and template {template_id}"
                )

    # Bulk create all history records
    if history_records:
        History.objects.bulk_create(history_records)

    # Update the eval_group's updated_at timestamp and save
    eval_group.updated_at = timezone.now()
    eval_group.save(update_fields=["updated_at"])

    return


# =============================================================================
# Handler Registry
# =============================================================================

EVAL_GROUP_HANDLERS = {
    PageType.EVAL_TASK: "apply_eval_group_to_observation_span",
    PageType.PROMPT: "apply_eval_group_to_prompt_template",
    PageType.DATASET: "apply_eval_group_to_dataset",
    PageType.SIMULATE: "apply_eval_group_to_simulate",
    PageType.EXPERIMENT: "apply_eval_group_to_experiment",
}


def apply_eval_group(
    eval_group,
    filters,
    mapping,
    page_id: PageType,
    user,
    workspace,
    deselected_evals,
    params=None,
    refused=None,
):
    """Apply an eval group based on the page_id using handler registry.

    Members refused for want of a model are added to ``refused``.
    """
    handler_name = EVAL_GROUP_HANDLERS.get(PageType(page_id))
    if not handler_name:
        raise ValueError(f"Unknown page_id: {page_id}. Valid: {list(PageType)}")

    handler = globals()[handler_name]
    return handler(
        eval_group,
        filters,
        mapping,
        user,
        deselected_evals,
        workspace,
        params or {},
        refused=refused,
    )


def apply_eval_group_to_observation_span(
    eval_group,
    filters,
    mapping,
    user,
    deselected_evals,
    workspace,
    params,
    refused=None,
):
    _org = get_current_organization() or user.organization

    project_id = filters.get("project_id", None)
    if not project_id:
        raise ValueError("Project id is required")

    try:
        project = Project.objects.get(id=project_id, organization=_org)
    except Project.DoesNotExist as e:
        raise ValueError("Project not found") from e

    eval_templates = _get_eval_templates_for_group(eval_group, deselected_evals)

    kb_id = filters.get("kb_id", None)
    error_localizer = filters.get("error_localizer", False)
    members = _members_with_models(eval_templates, filters, refused)

    # Pre-fetch existing configs for deduplication by template_id + mapping
    # + model
    existing_configs = CustomEvalConfig.objects.filter(
        eval_group=eval_group,
        kb_id__id=kb_id,
        model__in={model for _eval_template, model in members},
        error_localizer=error_localizer,
        project=project,
    ).select_related("eval_template")

    existing_configs_lookup = {
        (
            cfg.eval_template_id,
            json.dumps(cfg.mapping, sort_keys=True) if cfg.mapping else "",
            cfg.model,
        ): cfg
        for cfg in existing_configs
    }

    # Count existing configs per template for versioning
    naming_configs = CustomEvalConfig.objects.filter(
        eval_group=eval_group, eval_template__in=eval_templates, project=project
    ).values_list("eval_template_id", "name")

    template_names_count = {}
    for template_id, _name in naming_configs:
        template_names_count[template_id] = template_names_count.get(template_id, 0) + 1

    existing_configs_list = []
    custom_eval_configs = []

    for eval_template, model in members:
        parsed_mapping = fetch_specific_mapping_for_specific_eval_template(
            mapping, eval_template
        )
        mapping_key = (
            json.dumps(parsed_mapping, sort_keys=True) if parsed_mapping else ""
        )
        lookup_key = (eval_template.id, mapping_key, model)

        if lookup_key in existing_configs_lookup:
            existing_configs_list.append(existing_configs_lookup[lookup_key])
        else:
            config = {}
            if eval_template.name == "tone":
                config["choices"] = eval_template.choices
            config = normalize_eval_runtime_config(
                eval_template.config,
                {
                    **config,
                    "params": _filter_global_params_for_template(eval_template, params),
                },
            )

            name = _generate_config_name(
                eval_template, eval_group, template_names_count.get(eval_template.id, 0)
            )

            custom_eval_configs.append(
                CustomEvalConfig(
                    eval_template=eval_template,
                    mapping=parsed_mapping,
                    kb_id_id=kb_id,
                    model=model,
                    error_localizer=error_localizer,
                    name=name,
                    config=config,
                    project_id=project_id,
                    eval_group=eval_group,
                )
            )

    newly_created_configs = []
    if custom_eval_configs:
        newly_created_configs = CustomEvalConfig.objects.bulk_create(
            custom_eval_configs
        )

    final_list = existing_configs_list + list(newly_created_configs)
    return CustomEvalConfigSerializer(final_list, many=True).data


def apply_eval_group_to_prompt_template(
    eval_group,
    filters,
    mapping,
    user,
    deselected_evals,
    workspace,
    params,
    refused=None,
):
    # A prompt's evaluation config keeps no model of its own, so no member
    # of the group is refused here.
    _org = get_current_organization() or user.organization

    prompt_template_id = filters.get("prompt_template_id", None)
    if not prompt_template_id:
        raise Exception("Prompt template id is required")

    try:
        prompt_template = PromptTemplate.objects.get(
            id=prompt_template_id, organization=_org
        )
    except PromptTemplate.DoesNotExist as e:
        raise Exception("Prompt template not found") from e

    eval_templates = _get_eval_templates_for_group(eval_group, deselected_evals)

    kb_id = filters.get("kb_id", None)
    error_localizer = filters.get("error_localizer", False)

    existing_configs = PromptEvalConfig.objects.filter(
        prompt_template=prompt_template, deleted=False, eval_group=eval_group
    ).select_related("eval_template", "eval_group")

    config_name_to_config_map = {config.name: config for config in existing_configs}

    response = []
    new_prompt_configs = []

    for eval_template in eval_templates:
        required_mapping = fetch_specific_mapping_for_specific_eval_template(
            mapping, eval_template
        )
        # Note: Prompt template uses simple name without versioning (updates existing)
        name = _generate_config_name(eval_template, eval_group, 0)

        if name in config_name_to_config_map:
            config = config_name_to_config_map[name]
            existing_params = (config.config or {}).get("params", {})
            template_params = _filter_global_params_for_template(eval_template, params)
            final_params = template_params if template_params else existing_params
            normalized = normalize_eval_runtime_config(
                eval_template.config, {"params": final_params}
            )
            config.mapping = required_mapping
            config.config = normalized
            config.save(update_fields=["mapping", "config"])
            response.append(config)
        else:
            new_prompt_configs.append(
                PromptEvalConfig(
                    eval_template=eval_template,
                    prompt_template=prompt_template,
                    mapping=required_mapping,
                    config=normalize_eval_runtime_config(
                        eval_template.config,
                        {
                            "params": _filter_global_params_for_template(
                                eval_template, params
                            )
                        },
                    ),
                    kb_id=kb_id,
                    error_localizer=error_localizer,
                    eval_group=eval_group,
                    user=user,
                    name=name,
                )
            )

    if new_prompt_configs:
        newly_created_configs = PromptEvalConfig.objects.bulk_create(new_prompt_configs)
        response.extend(newly_created_configs)

    result = []
    for config in response:
        function_params_schema, params = ({}, {})
        if config.eval_template:
            function_params_schema, params = params_with_defaults_for_response(
                config.eval_template.config,
                config.config,
            )

        result.append(
            {
                "id": str(config.id),
                "eval_template_id": (
                    str(config.eval_template.id) if config.eval_template else None
                ),
                "name": config.name,
                "mapping": config.mapping,
                "config": config.eval_template.config if config.eval_template else None,
                "params": params,
                "function_params_schema": function_params_schema,
                "eval_required_keys": config.eval_template.config.get(
                    "required_keys", []
                ),
                "updated_at": (
                    config.updated_at.isoformat() if config.updated_at else None
                ),
                "eval_group": eval_group.name if eval_group else None,
            }
        )
    return result


def apply_eval_group_to_dataset(
    eval_group,
    filters,
    mapping,
    user,
    deselected_evals,
    workspace,
    params,
    refused=None,
):
    dataset_id = filters.get("dataset_id", None)
    if not dataset_id:
        raise Exception("Dataset id is required")

    organization = get_current_organization() or user.organization
    try:
        dataset = Dataset.no_workspace_objects.filter(
            explicit_workspace_filter(workspace),
            organization=organization,
            deleted=False,
        ).get(id=dataset_id)
    except Dataset.DoesNotExist as e:
        raise ValueError("Dataset not found") from e
    if (
        filters.get("kb_id")
        and knowledge_base_id_for(organization, workspace, filters["kb_id"]) is None
    ):
        raise ValueError("Knowledge base not found")

    eval_templates = _get_eval_templates_for_group(eval_group, deselected_evals)

    # Count existing configs per template for versioning
    current_metrics = UserEvalMetric.objects.filter(
        dataset=dataset, eval_group=eval_group, deleted=False
    )
    template_names_count = {}
    for metric in current_metrics:
        tid = str(metric.template.id)
        template_names_count[tid] = template_names_count.get(tid, 0) + 1

    kb_id = filters.get("kb_id", None)
    error_localizer = filters.get("error_localizer", False)
    members = _members_with_models(eval_templates, filters, refused)

    user_eval_metrics = []
    for eval_template, model in members:
        parsed_mapping = fetch_specific_mapping_for_specific_eval_template(
            mapping, eval_template
        )
        name = _generate_config_name(
            eval_template,
            eval_group,
            template_names_count.get(str(eval_template.id), 0),
        )

        user_eval_metrics.append(
            UserEvalMetric(
                name=name,
                organization=dataset.organization,
                dataset=dataset,
                template=eval_template,
                config=normalize_eval_runtime_config(
                    eval_template.config,
                    {
                        "mapping": parsed_mapping,
                        "reason_column": True,
                        "config": {},
                        "params": _filter_global_params_for_template(
                            eval_template, params
                        ),
                    },
                ),
                status=StatusType.INACTIVE.value,
                user=user,
                model=model,
                error_localizer=error_localizer,
                kb_id=kb_id,
                workspace=workspace,
                eval_group=eval_group,
            )
        )

    UserEvalMetric.objects.bulk_create(user_eval_metrics)
    return


def apply_eval_group_to_simulate(
    eval_group,
    filters,
    mapping,
    user,
    deselected_evals,
    workspace,
    params,
    refused=None,
):
    _org = get_current_organization() or user.organization

    simulate_id = filters.get("simulate_id", None)
    if not simulate_id:
        raise Exception("Simulate id is required")

    try:
        run_test = RunTest.objects.get(id=simulate_id, organization=_org)
    except RunTest.DoesNotExist as e:
        raise Exception("Run test not found") from e

    eval_templates = _get_eval_templates_for_group(eval_group, deselected_evals)

    # Count existing configs per template for versioning
    current_configs = SimulateEvalConfig.objects.filter(
        run_test=run_test, eval_group=eval_group, deleted=False
    )
    template_names_count = {}
    for cfg in current_configs:
        tid = str(cfg.eval_template.id)
        template_names_count[tid] = template_names_count.get(tid, 0) + 1

    kb_id = filters.get("kb_id", None)
    error_localizer = filters.get("error_localizer", False)
    simulate_config_filters = filters.get("filters", [])
    members = _members_with_models(eval_templates, filters, refused)

    simulate_eval_metrics = []
    for eval_template, model in members:
        parsed_mapping = fetch_specific_mapping_for_specific_eval_template(
            mapping, eval_template
        )
        name = _generate_config_name(
            eval_template,
            eval_group,
            template_names_count.get(str(eval_template.id), 0),
        )

        simulate_eval_metrics.append(
            SimulateEvalConfig(
                name=name,
                run_test=run_test,
                eval_template=eval_template,
                config=normalize_eval_runtime_config(
                    eval_template.config,
                    {
                        "mapping": parsed_mapping,
                        "reason_column": True,
                        "config": {},
                        "params": _filter_global_params_for_template(
                            eval_template, params
                        ),
                    },
                ),
                model=model,
                error_localizer=error_localizer,
                kb_id_id=kb_id,
                eval_group=eval_group,
                filters=simulate_config_filters,
                mapping=parsed_mapping,
            )
        )

    created_configs = SimulateEvalConfig.objects.bulk_create(simulate_eval_metrics)
    return SimulateEvalConfigSimpleSerializer(created_configs, many=True).data


def apply_eval_group_to_experiment(
    eval_group,
    filters,
    mapping,
    user,
    deselected_evals,
    workspace,
    params,
    refused=None,
):
    _org = get_current_organization() or user.organization
    experiment_id = filters.get("experiment_id", None)
    if not experiment_id:
        raise ValueError("Experiment id is required")

    try:
        experiment = ExperimentsTable.objects.select_related("dataset").get(
            id=experiment_id
        )
    except ExperimentsTable.DoesNotExist as e:
        raise ValueError("Experiment not found") from e

    try:
        dataset = Dataset.objects.get(id=experiment.dataset_id, organization=_org)
    except Dataset.DoesNotExist as e:
        raise ValueError("Experiment not found") from e
    experiment.dataset = dataset
    # As Add evaluation does: an experiment with a snapshot evaluates on it,
    # with the mapping's dataset columns read as their snapshot copies.
    from model_hub.views.experiments import (
        _build_column_mapping_from_snapshot,
        _translate_eval_mapping,
    )

    eval_dataset = experiment.snapshot_dataset or experiment.dataset
    snapshot_columns = _build_column_mapping_from_snapshot(experiment.snapshot_dataset)

    eval_templates = _get_eval_templates_for_group(eval_group, deselected_evals)

    # Count existing configs per template for versioning
    current_metrics = UserEvalMetric.objects.filter(
        dataset_id__in={experiment.dataset_id, eval_dataset.id},
        eval_group=eval_group,
        source_id=experiment.id,
        deleted=False,
    )
    template_names_count = {}
    for metric in current_metrics:
        tid = str(metric.template.id)
        template_names_count[tid] = template_names_count.get(tid, 0) + 1

    kb_id = filters.get("kb_id", None)
    error_localizer = filters.get("error_localizer", False)
    members = _members_with_models(eval_templates, filters, refused)

    user_eval_metrics = []
    for eval_template, model in members:
        parsed_mapping = _translate_eval_mapping(
            fetch_specific_mapping_for_specific_eval_template(mapping, eval_template),
            snapshot_columns,
        )
        name = _generate_config_name(
            eval_template,
            eval_group,
            template_names_count.get(str(eval_template.id), 0),
        )

        user_eval_metrics.append(
            UserEvalMetric(
                name=name,
                organization=experiment.dataset.organization,
                dataset=eval_dataset,
                template=eval_template,
                config=normalize_eval_runtime_config(
                    eval_template.config,
                    {
                        "mapping": parsed_mapping,
                        "config": {},
                        "reason_column": True,
                        "params": _filter_global_params_for_template(
                            eval_template, params
                        ),
                    },
                ),
                status=StatusType.EXPERIMENT_EVALUATION.value,
                user=user,
                model=model,
                error_localizer=error_localizer,
                kb_id=kb_id,
                workspace=workspace,
                eval_group=eval_group,
                source_id=experiment.id,
            )
        )

    created_metrics = UserEvalMetric.objects.bulk_create(user_eval_metrics)
    experiment.user_eval_template_ids.add(*created_metrics)
    if created_metrics and experiment.snapshot_dataset_id:
        run = _run_added_experiment_evaluations(
            experiment, [str(metric.id) for metric in created_metrics]
        )
        return {"run": run}
    return


def _run_added_experiment_evaluations(experiment, metric_ids):
    """Start the experiment's run for evaluations just added, as Add
    evaluation with Run does (model_hub/views/experiments.py).

    Returns "started", "run_going" or "not_started". While a run of the
    experiment is going, starting another would stop it, so the evaluations
    are only added: they stay pending, with no columns yet, until they are
    run (Run evaluations sets up their columns). A run that cannot be
    started also leaves them pending until they are run again."""
    import structlog

    from model_hub.views.experiment_runner import ExperimentRunner
    from model_hub.views.experiments import _build_and_save_v2_column_order

    if experiment.status in (StatusType.RUNNING.value, StatusType.QUEUED.value):
        return "run_going"
    status_before = experiment.status
    experiment.status = StatusType.RUNNING.value
    experiment.save(update_fields=["status"])
    experiment_runner = ExperimentRunner(experiment_id=experiment.id)
    experiment_runner.load_experiment()
    experiment_runner.empty_or_create_evals_column(eval_template_ids=metric_ids)
    # The column setup loads each evaluation, which marks it running; the
    # run marks it again when it starts, so until then it stays pending.
    experiment.user_eval_template_ids.filter(id__in=metric_ids).update(
        status=StatusType.EXPERIMENT_EVALUATION.value
    )
    _build_and_save_v2_column_order(experiment, experiment.snapshot_dataset)
    try:
        from tfc.temporal.experiments import start_experiment_v2_workflow

        start_experiment_v2_workflow(
            experiment_id=str(experiment.id),
            rerun_eval_template_ids=metric_ids,
        )
    except Exception as wf_err:
        structlog.get_logger(__name__).warning(
            "Failed to start eval workflow for eval group",
            experiment_id=str(experiment.id),
            error=str(wf_err),
        )
        # No run started, so the experiment is not running.
        ExperimentsTable.objects.filter(
            id=experiment.id, status=StatusType.RUNNING.value
        ).update(status=status_before)
        return "not_started"
    return "started"
