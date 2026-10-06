from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce

from agent_playground.models.choices import GraphVersionStatus, PortDirection
from agent_playground.models.edge import Edge
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.port import Port


def get_exposed_ports_for_versions(version_ids: list) -> dict:
    """
    Get exposed (unconnected) ports for a list of graph version IDs.

    Exposed ports are:
    - Input ports with no incoming edges (entry points)
    - Output ports with no outgoing edges (exit points)

    Returns a dict mapping version_id -> list of port dicts.

    Executes exactly 2 queries:
    1. All ports for the given versions
    2. All edges for the given versions (to determine connected ports)
    """
    if not version_ids:
        return {}

    all_ports = list(
        Port.no_workspace_objects.filter(node__graph_version_id__in=version_ids)
        .select_related("node")
        .order_by("node__name", "key")
    )

    if not all_ports:
        return {}

    connected_input_port_ids = set()
    connected_output_port_ids = set()
    for source_id, target_id in Edge.no_workspace_objects.filter(
        graph_version_id__in=version_ids
    ).values_list("source_port_id", "target_port_id"):
        connected_output_port_ids.add(source_id)
        connected_input_port_ids.add(target_id)

    result: dict = {}
    for port in all_ports:
        is_unconnected_input = (
            port.direction == PortDirection.INPUT
            and port.id not in connected_input_port_ids
        )
        is_unconnected_output = (
            port.direction == PortDirection.OUTPUT
            and port.id not in connected_output_port_ids
        )

        if is_unconnected_input or is_unconnected_output:
            version_id = port.node.graph_version_id
            bucket = result.setdefault(version_id, {"seen": set(), "ports": []})
            if port.display_name not in bucket["seen"]:
                bucket["seen"].add(port.display_name)
                bucket["ports"].append(
                    {
                        "id": port.id,
                        "display_name": port.display_name,
                        "direction": port.direction,
                        "data_schema": port.data_schema,
                        "required": port.required,
                        "default_value": port.default_value,
                        "metadata": port.metadata,
                    }
                )

    return {vid: data["ports"] for vid, data in result.items()}


def get_global_variable_names_for_versions(version_ids: list) -> dict:
    """Get global variable names (unconnected input port display_names) per version. 2 queries."""
    exposed = get_exposed_ports_for_versions(version_ids)
    return {
        vid: [p["display_name"] for p in ports if p["direction"] == PortDirection.INPUT]
        for vid, ports in exposed.items()
    }


def annotate_graph_list_fields(queryset):
    """Annotate a Graph queryset with latest version fields for GraphListSerializer."""
    latest_version_sq = GraphVersion.no_workspace_objects.filter(
        graph=OuterRef("pk"),
    ).order_by("-version_number")

    return queryset.annotate(
        _active_version_id=Subquery(latest_version_sq.values("id")[:1]),
        _active_version_number=Subquery(latest_version_sq.values("version_number")[:1]),
        _node_count=Coalesce(
            Subquery(
                latest_version_sq.annotate(
                    _nc=Count("nodes", filter=Q(nodes__deleted=False))
                ).values("_nc")[:1]
            ),
            Value(0),
        ),
    )


def get_graph_and_version(request, pk, version_id):
    """Look up graph + version, raising DoesNotExist on miss."""
    organization = request.organization
    workspace = request.workspace

    qs = Graph.no_workspace_objects.filter(organization=organization)
    if workspace:
        qs = qs.filter(workspace=workspace)
    graph = qs.get(id=pk)

    version = GraphVersion.no_workspace_objects.get(id=version_id, graph=graph)
    return graph, version


def require_visible_nodes(request, node_ids):
    """Raise Node.DoesNotExist unless every node is in the caller's scope.

    Same org/workspace scope as ``get_graph_and_version``, so another tenant's
    node id answers exactly like a missing one.
    """
    from agent_playground.models.node import Node

    qs = Node.no_workspace_objects.filter(
        id__in=set(node_ids),
        graph_version__graph__organization=request.organization,
    )
    if request.workspace:
        qs = qs.filter(graph_version__graph__workspace=request.workspace)
    if qs.count() != len(set(node_ids)):
        raise Node.DoesNotExist


def require_draft(version):
    """Return True if version is NOT draft, else False."""
    if version.status != GraphVersionStatus.DRAFT:
        return True
    return False
