"""The evaluation templates an SDK request can name, and the one a name means.

The SDK's request check (ConfigureEvaluationsSerializer), its list of
evaluations and its run and configure endpoints all use these, so they agree on
which templates a caller sees and on which one a name picks.
"""

from django.db.models import Q

from model_hub.models.evals_metric import EvalTemplate
from tfc.utils.tenant_scope import workspace_scope_q


def sdk_templates_q(organization, workspace):
    """The built-ins, and ``organization``'s own templates in ``workspace``.

    The workspace rule is the shared manager's (workspace_scope_q): a default
    workspace also covers the organization's templates saved with no workspace,
    as from before workspaces existed; any other workspace covers only itself.
    With no workspace, only the organization's templates with no workspace.
    """
    if workspace is None:
        own = Q(organization=organization, workspace__isnull=True)
    else:
        own = Q(organization=organization) & workspace_scope_q(workspace)
    return own | Q(organization__isnull=True)


def _precedence(template, workspace):
    """Sort key among templates that share a name: a built-in first, then the
    caller's own in its workspace, then its own with no workspace (or in another
    default workspace of the organization); the earliest saved first in each."""
    if template.organization_id is None:
        rank = 0
    elif workspace is not None and template.workspace_id == getattr(
        workspace, "pk", None
    ):
        rank = 1
    else:
        rank = 2
    return (rank, template.created_at, str(template.pk))


def find_sdk_template(name, organization, workspace):
    """The template ``name`` means for a caller in ``organization`` and ``workspace``.

    Raises EvalTemplate.DoesNotExist when no template the caller sees has that
    name. Saving now refuses a customer template named like a built-in, or like
    another of the organization's, but older rows can share a name: then
    _precedence decides, so every SDK endpoint picks the same one.
    """
    lookup = Q(name=name) & sdk_templates_q(organization, workspace)
    try:
        return EvalTemplate.no_workspace_objects.get(lookup)
    except EvalTemplate.MultipleObjectsReturned:
        rows = EvalTemplate.no_workspace_objects.filter(lookup)
        return min(rows, key=lambda template: _precedence(template, workspace))


def find_cicd_template(name, organization, workspace):
    """The template ``name`` means for an SDK CI/CD evaluation run.

    CI/CD has always matched a built-in or any of ``organization``'s templates,
    whatever their workspace, and still does. When rows share a name, the same
    order as find_sdk_template decides: a built-in, then the caller's template
    in ``workspace``, then its other templates; the earliest saved first in each.
    Raises EvalTemplate.DoesNotExist when nothing matches.
    """
    lookup = Q(name=name) & (
        Q(organization=organization) | Q(organization__isnull=True)
    )
    try:
        return EvalTemplate.no_workspace_objects.get(lookup)
    except EvalTemplate.MultipleObjectsReturned:
        rows = EvalTemplate.no_workspace_objects.filter(lookup)
        return min(rows, key=lambda template: _precedence(template, workspace))
