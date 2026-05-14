from typing import Any
from uuid import UUID

import structlog
from django.core.exceptions import ValidationError
from django.utils import timezone

from agent_playground.models.choices import (
    GraphVersionStatus,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.models.edge import Edge
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_template import NodeTemplate
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode
from agent_playground.services.dataset_bridge import activate_version_and_sync
from agent_playground.services.engine.utils.json_path import parse_variable
from agent_playground.services.node_crud import (
    _create_default_output_port_from_prompt,
    _create_input_ports_from_prompt,
    _default_prompt_data,
    _resolve_or_create_pt_ptv,
)

logger = structlog.get_logger(__name__)


def update_version_content(
    graph,
    version: GraphVersion,
    nodes_data: list[dict[str, Any]],
    new_status: str,
    commit_message: str | None,
    node_connections_data: list[dict[str, Any]] | None = None,
    user: Any = None,
    organization: Any = None,
    workspace: Any = None,
) -> None:
    """
    Update version content with full snapshot replace.

    FE sends nodes + node_connections. Backend auto-creates ports and edges.

    Steps:
    1. Soft-delete all existing edges, node connections, ports, PTNs, and nodes
    2. Create Node objects with ports based on node type (respects FE ports when provided):
       - LLM nodes (DYNAMIC input_mode): input ports from {{variables}}, output ports from FE or default
       - Subgraph nodes: input ports from input_mappings, output ports from FE
       - Nodes with FE ports: use FE ports (fixes bug where FE ports were ignored)
       - Nodes with template but no FE ports: auto-create from template (backward compatible)
    3. Create PromptTemplateNode for LLM nodes
    4. Create NodeConnection objects from temp_ids
    5. Auto-create Edge objects via display_name matching + subgraph input_mappings
    6. If status == "active": run validation and promote

    Args:
        graph: The Graph instance this version belongs to.
        version: The GraphVersion instance to update.
        nodes_data: List of node data dicts with temp_ids.
        new_status: Target status ("draft" or "active").
        commit_message: Optional commit message for the version.
        node_connections_data: List of node connection data dicts with node temp_ids.
        user: Current user (for PromptTemplate creation).
        organization: User's organization.
        workspace: Current workspace.

    Raises:
        ValidationError: If temp_ids cannot be resolved or validation fails.
    """
    if node_connections_data is None:
        node_connections_data = []

    now = timezone.now()
    Edge.no_workspace_objects.filter(graph_version=version).update(
        deleted=True, deleted_at=now
    )
    NodeConnection.no_workspace_objects.filter(graph_version=version).update(
        deleted=True, deleted_at=now
    )
    nodes = Node.no_workspace_objects.filter(graph_version=version)
    Port.no_workspace_objects.filter(node__in=nodes).update(
        deleted=True, deleted_at=now
    )
    PromptTemplateNode.no_workspace_objects.filter(node__in=nodes).update(
        deleted=True, deleted_at=now
    )
    nodes.update(deleted=True, deleted_at=now)

    skip_validation = new_status != GraphVersionStatus.ACTIVE

    node_id_to_node: dict[UUID, Node] = {}
    for node_data in nodes_data:
        node = create_node(version, node_data, skip_validation=skip_validation)
        node_id_to_node[node.id] = node

        prompt_data = node_data.get("prompt_template")
        fe_ports = node_data.get("ports", [])
        input_mappings = node_data.get("input_mappings", [])

        # Port creation logic - mirrors create_node behavior with backward compatibility
        # Priority: LLM nodes → Subgraph nodes → FE ports → Template fallback

        # 1. LLM nodes (DYNAMIC input_mode) - input from prompt, output from FE or default
        if node.node_template and node.node_template.input_mode == PortMode.DYNAMIC:
            # Create input ports from prompt variables
            if prompt_data:
                _create_input_ports_from_prompt(node, prompt_data)

            # Use FE-supplied output ports if provided, otherwise create default output
            if fe_ports:
                for port_data in fe_ports:
                    create_port(node, port_data, skip_validation=skip_validation)
            else:
                effective_prompt = prompt_data or _default_prompt_data()
                _create_default_output_port_from_prompt(node, effective_prompt)

        # 2. Subgraph nodes - input from input_mappings, output from FE
        elif node.type == NodeType.SUBGRAPH and node.ref_graph_version:
            # Create input ports from input_mappings list [{key, value}]
            for mapping in input_mappings:
                Port(
                    node=node,
                    key="custom",
                    display_name=mapping["key"],
                    direction=PortDirection.INPUT,
                    data_schema={"type": "string"},
                ).save(skip_validation=skip_validation)

            # Create output ports from FE data
            for port_data in fe_ports:
                create_port(node, port_data, skip_validation=skip_validation)

        # 3. Use FE ports if provided (fixes the bug - FE ports were being ignored)
        elif fe_ports:
            for port_data in fe_ports:
                create_port(node, port_data, skip_validation=skip_validation)

        # 4. Fallback: template-based auto-creation (preserves backward compatibility)
        elif node.node_template:
            _create_ports_from_template(node, skip_validation=skip_validation)

        # Create PromptTemplateNode for LLM nodes (DYNAMIC input_mode)
        if node.node_template and node.node_template.input_mode == PortMode.DYNAMIC:
            effective_prompt = prompt_data or _default_prompt_data()
            pt, pv = _resolve_or_create_pt_ptv(
                effective_prompt, node.name, user, organization, workspace
            )
            PromptTemplateNode.no_workspace_objects.create(
                node=node,
                prompt_template=pt,
                prompt_version=pv,
            )

    for nc_data in node_connections_data:
        source_node_id = nc_data["source_node_id"]
        target_node_id = nc_data["target_node_id"]

        if source_node_id not in node_id_to_node:
            raise ValidationError(
                f"Source node '{source_node_id}' not found in created nodes"
            )
        if target_node_id not in node_id_to_node:
            raise ValidationError(
                f"Target node '{target_node_id}' not found in created nodes"
            )

        create_node_connection(version, source_node_id, target_node_id)

    # Auto-create edges (display_name matching + subgraph input_mappings)
    _auto_create_edges(version, nodes_data, node_id_to_node)

    if new_status == GraphVersionStatus.ACTIVE:
        activate_version_and_sync(graph, version, commit_message)
    elif commit_message:
        version.commit_message = commit_message
        version.save()


def _create_ports_from_template(node: Node, skip_validation: bool = False) -> None:
    """
    Auto-create ports from the node's template input/output definitions.

    For strict/extensible modes: create ports from definition entries.
    For dynamic mode: no-op (ports come from prompt data or FE).
    """
    template = node.node_template
    if not template:
        return

    if template.input_mode in (PortMode.STRICT, PortMode.EXTENSIBLE):
        for defn in template.input_definition:
            Port(
                node=node,
                key=defn["key"],
                display_name=defn.get("display_name", defn["key"]),
                direction=PortDirection.INPUT,
                data_schema=defn.get("data_schema", {}),
            ).save(skip_validation=skip_validation)

    if template.output_mode in (PortMode.STRICT, PortMode.EXTENSIBLE):
        for defn in template.output_definition:
            Port(
                node=node,
                key=defn["key"],
                display_name=defn.get("display_name", defn["key"]),
                direction=PortDirection.OUTPUT,
                data_schema=defn.get("data_schema", {}),
            ).save(skip_validation=skip_validation)


def _auto_create_edges(
    version: GraphVersion,
    nodes_data: list[dict[str, Any]],
    node_id_to_node: dict[UUID, Node],
) -> None:
    """Auto-create edges via display_name matching + subgraph input_mappings."""

    # Collect subgraph input_mappings keyed by node id
    subgraph_mappings: dict[UUID, list[dict]] = {}
    for node_data in nodes_data:
        mappings = node_data.get("input_mappings", [])
        if mappings:
            node_id = node_data["id"]
            if isinstance(node_id, str):
                node_id = UUID(node_id)
            subgraph_mappings[node_id] = mappings

    connections = NodeConnection.no_workspace_objects.filter(graph_version=version)

    for nc in connections:
        if nc.target_node_id in subgraph_mappings:
            # Subgraph target: use explicit input_mappings
            mappings = subgraph_mappings[nc.target_node_id]
            target_input_ports = {
                p.display_name: p
                for p in Port.no_workspace_objects.filter(
                    node=nc.target_node, direction=PortDirection.INPUT
                )
            }
            for mapping in mappings:
                input_display_name = mapping["key"]
                source_ref = mapping["value"]

                if source_ref is None:
                    continue  # globally exposed, no edge
                # Parse "NodeName.port_display_name"
                parts = source_ref.split(".", 1)
                if len(parts) != 2:
                    continue
                source_node_name, source_port_name = parts
                # Find source node by name in this version
                source_node = Node.no_workspace_objects.filter(
                    graph_version=version, name=source_node_name
                ).first()
                if not source_node:
                    continue
                source_port = Port.no_workspace_objects.filter(
                    node=source_node,
                    direction=PortDirection.OUTPUT,
                    display_name=source_port_name,
                ).first()
                target_port = target_input_ports.get(input_display_name)
                if source_port and target_port:
                    Edge(
                        graph_version=version,
                        source_port=source_port,
                        target_port=target_port,
                    ).save()
        else:
            # Non-subgraph: display_name matching (simple + dot-notation)
            output_ports_by_name = {
                p.display_name: p
                for p in Port.no_workspace_objects.filter(
                    node=nc.source_node, direction=PortDirection.OUTPUT
                )
            }
            input_ports = list(
                Port.no_workspace_objects.filter(
                    node=nc.target_node, direction=PortDirection.INPUT
                )
            )
            for in_port in input_ports:
                parsed = parse_variable(in_port.display_name)
                if parsed.is_dot_notation:
                    # Dot-notation: match source node by name, then output port
                    if nc.source_node.name == parsed.parent_node_name:
                        out_port = output_ports_by_name.get(parsed.output_port_name)
                        if out_port:
                            Edge(
                                graph_version=version,
                                source_port=out_port,
                                target_port=in_port,
                            ).save()
                else:
                    # Simple variable: match by display_name
                    if (
                        nc.target_node.node_template
                        and nc.target_node.node_template.name == "code_execution"
                    ):
                        out_port = (
                            Port.no_workspace_objects.filter(
                                node=nc.source_node, direction=PortDirection.OUTPUT
                            )
                            .order_by("created_at")
                            .first()
                        )
                    else:
                        out_port = output_ports_by_name.get(in_port.display_name)
                    if out_port:
                        Edge(
                            graph_version=version,
                            source_port=out_port,
                            target_port=in_port,
                        ).save()


def _resolve_node_template(node_template_id: str | None) -> NodeTemplate | None:
    """Fetch a NodeTemplate by ID, raising ValidationError if not found."""
    if not node_template_id:
        return None
    try:
        return NodeTemplate.no_workspace_objects.get(id=node_template_id)
    except NodeTemplate.DoesNotExist as exc:
        raise ValidationError(f"Node template '{node_template_id}' not found") from exc


def _resolve_ref_graph_version(ref_graph_version_id: str | None) -> GraphVersion | None:
    """Fetch a GraphVersion by ID, raising ValidationError if not found."""
    if not ref_graph_version_id:
        return None
    try:
        return GraphVersion.no_workspace_objects.get(id=ref_graph_version_id)
    except GraphVersion.DoesNotExist as exc:
        raise ValidationError(
            f"Referenced graph version '{ref_graph_version_id}' not found"
        ) from exc


def create_node(
    version: GraphVersion,
    node_data: dict[str, Any],
    skip_validation: bool = False,
) -> Node:
    """
    Create a node from node_data dictionary.

    Args:
        version: The GraphVersion this node belongs to.
        node_data: Dictionary containing node fields (must include 'id' field).

    Returns:
        The created Node instance.

    Raises:
        ValidationError: If referenced node_template or graph_version not found.
    """
    node_template = _resolve_node_template(node_data.get("node_template_id"))
    ref_graph_version = _resolve_ref_graph_version(
        node_data.get("ref_graph_version_id")
    )

    node = Node(
        id=node_data["id"],  # Use frontend-provided UUID
        graph_version=version,
        type=node_data["type"],
        name=node_data["name"],
        node_template=node_template,
        ref_graph_version=ref_graph_version,
        config=node_data.get("config", {}),
        position=node_data.get("position", {}),
    )

    node.save(skip_validation=skip_validation)
    return node


def _resolve_ref_port(ref_port_id: str | None) -> Port | None:
    """Fetch a Port by ID for ref_port, raising ValidationError if not found."""
    if not ref_port_id:
        return None
    try:
        return Port.no_workspace_objects.get(id=ref_port_id)
    except Port.DoesNotExist as exc:
        raise ValidationError(f"Referenced port '{ref_port_id}' not found") from exc


def create_port(
    node: Node,
    port_data: dict[str, Any],
    skip_validation: bool = False,
) -> Port:
    """
    Create a port from port_data dictionary.

    Args:
        node: The Node this port belongs to.
        port_data: Dictionary containing port fields.
        skip_validation: If True, skip model-level validation (for drafts).

    Returns:
        The created Port instance.
    """
    ref_port = _resolve_ref_port(port_data.get("ref_port_id"))

    port = Port(
        node=node,
        key=port_data["key"],
        display_name=port_data["display_name"],
        direction=port_data["direction"],
        data_schema=port_data.get("data_schema", {}),
        required=port_data.get("required", True),
        default_value=port_data.get("default_value"),
        metadata=port_data.get("metadata", {}),
        ref_port=ref_port,
    )
    port.save(skip_validation=skip_validation)
    return port


def create_node_connection(
    version: GraphVersion,
    source_node_id: UUID,
    target_node_id: UUID,
) -> NodeConnection:
    """
    Create a node connection between two nodes.

    Args:
        version: The GraphVersion this connection belongs to.
        source_node_id: UUID of the source node.
        target_node_id: UUID of the target node.

    Returns:
        The created NodeConnection instance.
    """
    source_node = Node.no_workspace_objects.get(id=source_node_id)
    target_node = Node.no_workspace_objects.get(id=target_node_id)

    node_connection = NodeConnection(
        graph_version=version,
        source_node=source_node,
        target_node=target_node,
    )

    node_connection.save()
    return node_connection


def create_edge(
    version: GraphVersion,
    source_port_id: UUID,
    target_port_id: UUID,
) -> Edge:
    """
    Create an edge between two ports.

    Args:
        version: The GraphVersion this edge belongs to.
        source_port_id: UUID of the source (output) port.
        target_port_id: UUID of the target (input) port.

    Returns:
        The created Edge instance.
    """
    source_port = Port.no_workspace_objects.get(id=source_port_id)
    target_port = Port.no_workspace_objects.get(id=target_port_id)

    edge = Edge(
        graph_version=version,
        source_port=source_port,
        target_port=target_port,
    )

    edge.save()
    return edge
