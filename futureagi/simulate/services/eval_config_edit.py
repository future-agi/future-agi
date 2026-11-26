"""Edit one eval config of a run test in place.

The run-test page's ``eval-configs/{id}/update/`` and the environments API's
``PATCH evaluations/{id}/`` both go through here, so a change is checked and
applied one way. Nothing here grades anything; each caller words its own
answer to a refusal.
"""

from django.db.models import Q

from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from model_hub.utils.function_eval_params import normalize_eval_runtime_config
from simulate.models import SimulateEvalConfig


class EvalConfigEditRefused(Exception):
    """The change can't be applied as asked. Nothing was saved."""


def has_own_mapping(eval_config) -> bool:
    """Whether the config maps inputs of its own.

    A row the harness fills keeps an empty mapping on purpose: later harness
    runs write into it by its fixed id. A mapping that is not a dict reads as
    empty, the way ``regrade_mapping`` reads it.
    """
    mapping = eval_config.mapping
    return isinstance(mapping, dict) and bool(mapping)


def visible_eval_template_query(user_organization, workspace):
    """Templates available from the active workspace plus global system templates."""
    template_query = Q(organization__isnull=True)
    if workspace is None:
        return template_query | Q(organization=user_organization)

    workspace_query = Q(organization=user_organization, workspace=workspace)
    if getattr(workspace, "is_default", False):
        workspace_query |= Q(
            organization=user_organization,
            workspace__is_default=True,
            workspace__organization_id=user_organization.id,
        ) | Q(organization=user_organization, workspace__isnull=True)

    return template_query | workspace_query


def update_eval_config(
    eval_config, validated, *, organization, workspace, before_save=None
):
    """Apply the keys present in ``validated`` to ``eval_config`` and save it.

    Only the keys present are applied, so a missing key leaves its field as
    it is. ``before_save``, when given, is called with the changed but unsaved
    config and may raise ``EvalConfigEditRefused`` to keep it unsaved.
    """
    # Resolve new template if provided so config normalization uses the
    # right template schema.
    new_template = None
    if "template_id" in validated:
        template_id = validated.get("template_id")
        try:
            new_template = EvalTemplate.no_workspace_objects.get(
                visible_eval_template_query(organization, workspace),
                id=template_id,
            )
        except EvalTemplate.DoesNotExist as missing:
            raise EvalConfigEditRefused("Evaluation template not found") from missing

    # Update config if provided (similar to EditAndRunUserEvalView)
    new_config = validated.get("config")
    if new_config:
        template_config = (
            new_template.config if new_template else eval_config.eval_template.config
        )
        try:
            eval_config.config = normalize_eval_runtime_config(
                template_config, new_config
            )
        except ValueError as e:
            raise EvalConfigEditRefused(str(e)) from e
    elif new_template:
        # Template changed without new config: re-normalize existing config
        # against the new template's schema so it stays valid after the switch.
        try:
            eval_config.config = normalize_eval_runtime_config(
                new_template.config, eval_config.config
            )
        except ValueError as e:
            raise EvalConfigEditRefused(
                f"Cannot switch template: existing config is incompatible with new template. {str(e)}"
            ) from e

    # Update mapping if provided at top level
    if "mapping" in validated:
        eval_config.mapping = validated.get("mapping")

    # Update filters if provided
    if "filters" in validated:
        eval_config.filters = validated.get("filters") or []

    # Update other fields if provided
    if "name" in validated:
        new_name = validated.get("name")
        if (
            SimulateEvalConfig.objects.filter(
                run_test_id=eval_config.run_test_id,
                name=new_name,
                deleted=False,
            )
            .exclude(id=eval_config.id)
            .exists()
        ):
            raise EvalConfigEditRefused(
                f"An evaluation config with the name '{new_name}' already exists in this run test. Please use a different name."
            )
        eval_config.name = new_name
    if "model" in validated:
        eval_config.model = validated.get("model")
    if "error_localizer" in validated:
        eval_config.error_localizer = validated.get("error_localizer")
    if "kb_id" in validated:
        kb_id = validated.get("kb_id")
        if kb_id:
            try:
                eval_config.kb_id = KnowledgeBaseFile.objects.get(
                    id=kb_id, organization=organization
                )
            except KnowledgeBaseFile.DoesNotExist as missing:
                raise EvalConfigEditRefused("Knowledge base not found") from missing
        else:
            eval_config.kb_id = None

    # Re-validate mapping against the new template's input variables.
    # When template switches without an explicit mapping, the old
    # mapping keys can diverge from what the new template expects.
    if new_template and "mapping" not in validated and eval_config.mapping:
        template_config = new_template.config or {}
        required_keys = template_config.get("required_keys", []) or []
        optional_keys = template_config.get("optional_keys", []) or []
        valid_keys = set(required_keys) | set(optional_keys)
        invalid_keys = set(eval_config.mapping.keys()) - valid_keys
        if invalid_keys:
            raise EvalConfigEditRefused(
                f"Keys {sorted(invalid_keys)} are not valid input variables for the selected template. Valid keys: {sorted(valid_keys)}"
            )

    # Re-validate kb_id: clear it on template switch when not
    # explicitly provided, since the new template may not be
    # compatible with the old knowledge base.
    if new_template and "kb_id" not in validated:
        eval_config.kb_id = None

    # Switch template after config normalization so the existing config
    # is validated against the new template's schema.
    if new_template:
        eval_config.eval_template = new_template

    if before_save is not None:
        before_save(eval_config)

    # Save the eval config
    eval_config.save()
    return eval_config
