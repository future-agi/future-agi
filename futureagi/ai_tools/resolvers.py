"""Entity resolvers for AI tools.

LLMs work better with names than UUIDs. These resolvers accept either
a name or UUID and return the resolved object, so tools can accept
human-readable identifiers while still working with database IDs internally.

Research shows UUIDs cost ~24 tokens each and cause ~50% error rates
in LLM tool calling. Names are 2-3 tokens and far more natural.
"""

import uuid
from typing import Optional

import structlog

# Module-scope imports so unit tests can patch these symbols
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.run_prompt import PromptTemplate
from tracer.models.project import Project

logger = structlog.get_logger(__name__)


def is_uuid(value: str) -> bool:
    """Check if a string looks like a UUID."""
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError):
        return False


def resolve_dataset(identifier: str, organization, workspace=None):
    """Resolve a dataset by name or UUID.

    Returns (dataset, error_message). If resolved, error_message is None.
    If not found, dataset is None and error_message explains what happened.
    """
    if not identifier:
        return None, "Dataset identifier is required."

    # Try UUID first
    if is_uuid(identifier):
        try:
            ds = Dataset.objects.get(
                id=identifier, organization=organization, deleted=False
            )
            return ds, None
        except Dataset.DoesNotExist:
            return None, f"Dataset with ID `{identifier}` not found."

    # Try exact name match
    matches = Dataset.objects.filter(
        name__iexact=identifier.strip(),
        organization=organization,
        deleted=False,
    )
    if workspace:
        matches = matches.filter(workspace=workspace)

    if matches.count() == 1:
        return matches.first(), None
    elif matches.count() > 1:
        names = [f"`{d.name}` (ID: {d.id})" for d in matches[:5]]
        return None, (
            f"Multiple datasets match '{identifier}': {', '.join(names)}. "
            f"Please specify the exact ID."
        )

    # Try fuzzy match
    fuzzy = Dataset.objects.filter(
        name__icontains=identifier.strip(),
        organization=organization,
        deleted=False,
    )[:5]
    if fuzzy.exists():
        suggestions = [f"`{d.name}` (ID: {d.id})" for d in fuzzy]
        return None, (
            f"No exact match for '{identifier}'. Did you mean: {', '.join(suggestions)}?"
        )

    return None, f"No dataset found matching '{identifier}'."


def resolve_project(identifier: str, organization, workspace=None):
    """Resolve a tracing project by name or UUID."""
    if not identifier:
        return None, "Project identifier is required."

    if is_uuid(identifier):
        try:
            return (
                Project.objects.get(
                    id=identifier, organization=organization, deleted=False
                ),
                None,
            )
        except Project.DoesNotExist:
            return None, f"Project with ID `{identifier}` not found."

    matches = Project.objects.filter(
        name__iexact=identifier.strip(),
        organization=organization,
        deleted=False,
    )
    if workspace:
        matches = matches.filter(workspace=workspace)

    if matches.count() == 1:
        return matches.first(), None
    elif matches.count() > 1:
        names = [f"`{p.name}` (ID: {p.id})" for p in matches[:5]]
        return None, f"Multiple projects match '{identifier}': {', '.join(names)}."

    return None, f"No project found matching '{identifier}'."


def resolve_eval_template(identifier: str, organization, workspace=None):
    """Resolve an eval template by name or UUID.

    Looks up user-owned templates within the org (workspace-filtered via
    the default manager), then falls back to system templates (global,
    organization=NULL) which are accessible to all orgs.
    """
    if not identifier:
        return None, "Eval template identifier is required."

    # 1. Try org-scoped lookup (respects workspace filtering)
    if is_uuid(identifier):
        try:
            return (
                EvalTemplate.objects.get(
                    id=identifier, organization=organization, deleted=False
                ),
                None,
            )
        except EvalTemplate.DoesNotExist:
            pass

        # 2. Fall back to system templates (org=NULL, no workspace)
        try:
            return (
                EvalTemplate.no_workspace_objects.get(
                    id=identifier, owner="system", deleted=False
                ),
                None,
            )
        except EvalTemplate.DoesNotExist:
            return None, f"Eval template with ID `{identifier}` not found."

    # Name-based lookup: try org-scoped first
    matches = EvalTemplate.objects.filter(
        name__iexact=identifier.strip(),
        organization=organization,
        deleted=False,
    )
    if matches.count() == 1:
        return matches.first(), None
    elif matches.count() > 1:
        names = [f"`{t.name}` (ID: {t.id})" for t in matches[:5]]
        return None, f"Multiple templates match '{identifier}': {', '.join(names)}."

    # Fall back to system templates by name
    sys_matches = EvalTemplate.no_workspace_objects.filter(
        name__iexact=identifier.strip(),
        owner="system",
        deleted=False,
    )
    if sys_matches.count() == 1:
        return sys_matches.first(), None
    elif sys_matches.count() > 1:
        names = [f"`{t.name}` (ID: {t.id})" for t in sys_matches[:5]]
        return None, f"Multiple system templates match '{identifier}': {', '.join(names)}."

    # Try fuzzy across both org + system
    from django.db.models import Q

    fuzzy = EvalTemplate.no_workspace_objects.filter(
        Q(organization=organization) | Q(owner="system"),
        name__icontains=identifier.strip(),
        deleted=False,
    )[:5]
    if fuzzy.exists():
        suggestions = [f"`{t.name}` (ID: {t.id})" for t in fuzzy]
        return (
            None,
            f"No exact match for '{identifier}'. Did you mean: {', '.join(suggestions)}?",
        )

    return None, f"No eval template found matching '{identifier}'."


def resolve_experiment(identifier: str, organization, workspace=None):
    """Resolve an experiment by name or UUID."""
    if not identifier:
        return None, "Experiment identifier is required."

    if is_uuid(identifier):
        try:
            return (
                ExperimentsTable.objects.get(
                    id=identifier, organization=organization, deleted=False
                ),
                None,
            )
        except ExperimentsTable.DoesNotExist:
            return None, f"Experiment with ID `{identifier}` not found."

    matches = ExperimentsTable.objects.filter(
        name__iexact=identifier.strip(),
        organization=organization,
        deleted=False,
    )
    if matches.count() == 1:
        return matches.first(), None

    return None, f"No experiment found matching '{identifier}'."


def resolve_prompt_template(identifier: str, organization, workspace=None):
    """Resolve a prompt template by name or UUID."""
    if not identifier:
        return None, "Prompt template identifier is required."

    if is_uuid(identifier):
        try:
            return (
                PromptTemplate.objects.get(
                    id=identifier, organization=organization, deleted=False
                ),
                None,
            )
        except PromptTemplate.DoesNotExist:
            return None, f"Prompt template with ID `{identifier}` not found."

    matches = PromptTemplate.objects.filter(
        name__iexact=identifier.strip(),
        organization=organization,
        deleted=False,
    )
    if workspace:
        matches = matches.filter(workspace=workspace)

    if matches.count() == 1:
        return matches.first(), None
    elif matches.count() > 1:
        names = [f"`{t.name}` (ID: {t.id})" for t in matches[:5]]
        return None, f"Multiple templates match '{identifier}': {', '.join(names)}."

    return None, f"No prompt template found matching '{identifier}'."
