import re

import structlog

logger = structlog.get_logger(__name__)

EVAL_NAME_REGEX = re.compile(r"^[a-z0-9_-]+$")
TEMPLATE_VARIABLE_REGEX = re.compile(r"\{\{[a-zA-Z0-9_]+\}\}")


def validate_eval_name(name: str) -> str:
    """Validate eval name format: lowercase alphanumeric with hyphens/underscores only.

    Matches the frontend Zod schema regex /^[a-z0-9_-]+$/ and the
    CustomEvalTemplateCreateSerializer.validate() checks.

    Returns the cleaned name or raises ValueError.
    """
    cleaned = name.strip()

    if not cleaned:
        raise ValueError("Name cannot be empty.")

    if not EVAL_NAME_REGEX.match(cleaned):
        raise ValueError(
            "Name can only contain lowercase letters, numbers, "
            "hyphens (-), or underscores (_). No spaces allowed."
        )

    if cleaned.startswith("-") or cleaned.startswith("_"):
        raise ValueError("Name cannot start with hyphens (-) or underscores (_).")

    if cleaned.endswith("-") or cleaned.endswith("_"):
        raise ValueError("Name cannot end with hyphens (-) or underscores (_).")

    if "_-" in cleaned or "-_" in cleaned:
        raise ValueError("Name cannot contain consecutive separators (_- or -_).")

    return cleaned


def validate_criteria_has_variables(criteria: str, template_type: str) -> None:
    """Validate that criteria contains at least one {{variable}} when template_type != Function.

    Matches the frontend Zod schema that requires criteria with variables
    for non-Function template types.
    """
    if template_type and template_type.lower() == "function":
        return

    if not criteria or not criteria.strip():
        raise ValueError(
            "Criteria is required and must contain at least one template variable "
            "using double curly braces (e.g. {{variable_name}})."
        )

    if not TEMPLATE_VARIABLE_REGEX.search(criteria):
        raise ValueError(
            "Criteria must contain at least one template variable "
            "using double curly braces (e.g. {{variable_name}}). "
            "These variables are replaced with actual values at runtime."
        )


def validate_choices_for_output_type(
    output_type: str, choices: dict | list | None
) -> None:
    """Validate that choices is a non-empty dict when output_type is 'choices'.

    Matches the frontend Zod schema: choices array must have min 1 item.
    """
    if output_type != "choices":
        return

    if not choices or not isinstance(choices, dict) or len(choices) == 0:
        raise ValueError(
            "Choices must be provided as a non-empty dict when output_type is 'choices'."
        )


def validate_length_between_config(config: dict | None) -> None:
    """Validate that minLength <= maxLength in LengthBetween eval config.

    Matches the frontend Zod validation for LengthBetween eval type.
    """
    if not config or not isinstance(config, dict):
        return

    inner_config = config.get("config", {})
    if not isinstance(inner_config, dict):
        return

    min_length = inner_config.get("minLength")
    max_length = inner_config.get("maxLength")

    if min_length is not None and max_length is not None:
        try:
            if float(min_length) > float(max_length):
                raise ValueError("Min length cannot be greater than max length.")
        except (TypeError, ValueError) as e:
            if "Min length" in str(e):
                raise
            # Non-numeric values — skip validation


def validate_required_key_mapping(mapping: dict, required_keys: list[str]) -> list[str]:
    """Check that all required template keys are present in the mapping.

    Matches the frontend validateRequiredColumnMapping() logic.
    Returns a list of missing keys (empty if all present).
    """
    if not required_keys:
        return []
    return [k for k in required_keys if k not in mapping or not mapping[k]]


def get_required_mapping_keys_for_template(template) -> list[str]:
    """Return required input keys for single and composite eval bindings."""
    if getattr(template, "template_type", "single") != "composite":
        config = getattr(template, "config", None) or {}
        if not isinstance(config, dict):
            return []
        return list(config.get("required_keys") or config.get("requiredKeys") or [])

    from model_hub.models.evals_metric import CompositeEvalChild

    keys = []
    seen = set()
    child_links = (
        CompositeEvalChild.objects.filter(parent=template, deleted=False)
        .select_related("child")
        .order_by("order")
    )
    for link in child_links:
        config = getattr(link.child, "config", None) or {}
        if not isinstance(config, dict):
            continue
        for key in config.get("required_keys") or config.get("requiredKeys") or []:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def validate_eval_template_org_access(template_id, organization):
    """Look up an eval template with organization scoping.

    Allows templates owned by the organization OR system templates (org=null).
    This is the correct pattern used in test_eval_template and run_evaluation tools.

    Returns the EvalTemplate instance or raises DoesNotExist.
    """
    from django.db.models import Q

    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.no_workspace_objects.get(
        Q(organization=organization) | Q(organization__isnull=True),
        id=template_id,
        deleted=False,
    )
