"""Validate native graph nodes before an experiment snapshot is created."""

from agent_playground.models import Node
from agent_playground.services.engine.runners.deterministic_text import validate_config
from tfc.utils.tenant_scope import scope_to_caller


def validate_nodes(request, versions):
    """Inspect only previously authorized graph versions in the caller's scope."""
    version_ids = [version.pk for version in versions]
    if not version_ids:
        return
    nodes = scope_to_caller(
        Node.no_workspace_objects.filter(
            graph_version_id__in=version_ids,
            node_template__name="deterministic_text",
            deleted=False,
        ),
        request,
        organization_field="graph_version__graph__organization",
        workspace_field="graph_version__graph__workspace",
    )
    for config in nodes.values_list("config", flat=True):
        validate_config(config)
