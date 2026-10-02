import json

from django.db.models import Q
from django.utils import timezone

from model_hub.models.choices import OwnerChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.eval_groups import EvalGroup, History
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
from model_hub.schema.eval_group import PageType
from model_hub.serializers.eval_group import EvalGroupSerializer
from model_hub.utils.function_eval_params import (
    get_function_params_schema,
    normalize_eval_runtime_config,
    params_with_defaults_for_response,
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
    return queryset.filter(_accessible_eval_template_filter(organization, workspace))


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
            workspace=workspace,
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
):
    """Apply an eval group based on the page_id using handler registry."""
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
    )


def apply_eval_group_to_observation_span(
    eval_group, filters, mapping, user, deselected_evals, workspace, params
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
    model = filters.get("model", None)
    error_localizer = filters.get("error_localizer", False)

    # Pre-fetch existing configs for deduplication by template_id + mapping
    existing_configs = CustomEvalConfig.objects.filter(
        eval_group=eval_group,
        kb_id__id=kb_id,
        model=model,
        error_localizer=error_localizer,
        project=project,
    ).select_related("eval_template")

    existing_configs_lookup = {
        (
            cfg.eval_template_id,
            json.dumps(cfg.mapping, sort_keys=True) if cfg.mapping else "",
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

    for eval_template in eval_templates:
        parsed_mapping = fetch_specific_mapping_for_specific_eval_template(
            mapping, eval_template
        )
        mapping_key = (
            json.dumps(parsed_mapping, sort_keys=True) if parsed_mapping else ""
        )
        lookup_key = (eval_template.id, mapping_key)

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
    eval_group, filters, mapping, user, deselected_evals, workspace, params
):
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
    eval_group, filters, mapping, user, deselected_evals, workspace, params
):
    dataset_id = filters.get("dataset_id", None)
    if not dataset_id:
        raise Exception("Dataset id is required")

    try:
        dataset = Dataset.objects.get(id=dataset_id)
    except Dataset.DoesNotExist as e:
        raise Exception("Dataset not found") from e

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
    model = filters.get("model", None)
    error_localizer = filters.get("error_localizer", False)

    user_eval_metrics = []
    for eval_template in eval_templates:
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
    eval_group, filters, mapping, user, deselected_evals, workspace, params
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
    model = filters.get("model", None)
    error_localizer = filters.get("error_localizer", False)
    simulate_config_filters = filters.get("filters", [])

    simulate_eval_metrics = []
    for eval_template in eval_templates:
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
    eval_group, filters, mapping, user, deselected_evals, workspace, params
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

    eval_templates = _get_eval_templates_for_group(eval_group, deselected_evals)

    # Count existing configs per template for versioning
    current_metrics = UserEvalMetric.objects.filter(
        dataset=experiment.dataset,
        eval_group=eval_group,
        source_id=experiment.id,
        deleted=False,
    )
    template_names_count = {}
    for metric in current_metrics:
        tid = str(metric.template.id)
        template_names_count[tid] = template_names_count.get(tid, 0) + 1

    kb_id = filters.get("kb_id", None)
    model = filters.get("model", None)
    error_localizer = filters.get("error_localizer", False)

    user_eval_metrics = []
    for eval_template in eval_templates:
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
                organization=experiment.dataset.organization,
                dataset=experiment.dataset,
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
    return
