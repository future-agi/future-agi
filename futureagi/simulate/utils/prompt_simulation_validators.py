"""
Shared validation utilities for prompt simulation operations.

Used by:
- simulate/serializers/run_test.py (CreatePromptSimulationSerializer)
- simulate/views/prompt_simulation.py (PATCH, Execute views)
- ai_tools/tools/prompts/ (create, update, execute tools)
"""

import uuid

import structlog
from django.core.exceptions import ValidationError

logger = structlog.get_logger(__name__)


def resolve_prompt_version(version_id_str, template_id):
    """
    Resolve a prompt version identifier to a PromptVersion instance.

    Accepts either a UUID or a version string (e.g. 'v1').
    Returns the PromptVersion instance or raises ValidationError.
    """
    from model_hub.models.run_prompt import PromptVersion

    prompt_version = None

    # Strategy 1: Try as UUID
    try:
        version_uuid = uuid.UUID(str(version_id_str))
        prompt_version = PromptVersion.objects.filter(
            id=version_uuid, original_template_id=template_id, deleted=False
        ).first()
    except (ValueError, AttributeError):
        pass

    # Strategy 2: Try as template_version string (e.g. 'v1')
    if not prompt_version:
        prompt_version = PromptVersion.objects.filter(
            template_version=str(version_id_str),
            original_template_id=template_id,
            deleted=False,
        ).first()

    if not prompt_version:
        raise ValidationError(
            f"Prompt version '{version_id_str}' not found for template '{template_id}'."
        )

    return prompt_version


def validate_scenarios_in_org(scenario_ids, organization):
    """
    Validate that all scenario IDs exist in the given organization.

    Returns the queryset of valid scenarios.
    Raises ValidationError with list of missing IDs if any are not found.
    """
    from simulate.models import Scenarios

    scenarios = Scenarios.objects.filter(
        id__in=scenario_ids, organization=organization, deleted=False
    )
    existing_ids = {str(s.id) for s in scenarios}
    missing_ids = [str(sid) for sid in scenario_ids if str(sid) not in existing_ids]

    if missing_ids:
        raise ValidationError(f"Scenarios not found: {', '.join(missing_ids)}")

    return scenarios
