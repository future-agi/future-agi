"""
Business logic for granular Node / Port / NodeConnection CRUD operations.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from django.core.exceptions import ValidationError
from django.utils import timezone

from agent_playground.models.choices import NodeType, PortDirection, PortMode
from agent_playground.models.edge import Edge
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.node_template import NodeTemplate
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode
from agent_playground.services.engine.utils.json_path import parse_variable
from agent_playground.utils.graph import get_exposed_ports_for_versions
from model_hub.models.run_prompt import PromptTemplate, PromptVersion

logger = structlog.get_logger(__name__)

_VARIABLE_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")


def _default_prompt_data() -> dict[str, Any]:
    """Default prompt data for LLM nodes created without explicit prompt data.

    Returns:
        Dict with empty messages array and "text" response format (plain text).
        This creates a basic LLM node that accepts no input variables and
        outputs plain text.
    """
    return {"messages": [], "response_format": "text"}


def _auto_create_edges_for_node(
    node: Node, input_mappings: list[dict[str, str | None]] | None = None
) -> None:
    """
    Auto-create edges for a node based on its input ports and existing NodeConnections.

    If input_mappings is provided (for subgraph nodes), use explicit mappings.
    Otherwise, use display_name matching logic.

    For each NodeConnection where this node is the target:
    - Iterate through the node's input ports
    - Parse the input port display_name to check if it's dot-notation (e.g., "node_3.llm_output")
    - If dot-notation and the source node name matches the NodeConnection's source node:
      - Find the output port on the source node with matching display_name
      - Create an Edge if both ports exist and no edge exists yet
    - If simple variable (e.g., "question"), try to match by display_name

    This mirrors the logic in version_content.py:_auto_create_edges() but simplified
    for single-node context.

    Args:
        node: The target node for which to create edges.
        input_mappings: Optional list of {key, value} dicts mapping input port display_name to source reference.
    """
    # If explicit input_mappings provided, use them instead
    if input_mappings:
        _create_edges_from_input_mappings(node, input_mappings)
        return
    version = node.graph_version

    # Find all NodeConnections where this node is the target
    node_connections = NodeConnection.no_workspace_objects.filter(
        graph_version=version, target_node=node
    )

    for nc in node_connections:
        # Get output ports from source node
        output_ports_by_name = {
            p.display_name: p
            for p in Port.no_workspace_objects.filter(
                node=nc.source_node, direction=PortDirection.OUTPUT
            )
        }

        # Get input ports from target node (this node)
        input_ports = list(
            Port.no_workspace_objects.filter(node=node, direction=PortDirection.INPUT)
        )

        for in_port in input_ports:
            # Check if edge already exists for this input port
            existing_edge = Edge.no_workspace_objects.filter(
                target_port=in_port
            ).first()
            if existing_edge:
                continue  # Skip if edge already exists (fan-in prevention)

            # Parse the input port display_name
            parsed = parse_variable(in_port.display_name)

            if parsed.is_dot_notation:
                # Dot-notation: match source node by name, then output port
                if nc.source_node.name == parsed.parent_node_name:
                    out_port = output_ports_by_name.get(parsed.output_port_name)
                    if out_port:
                        try:
                            Edge(
                                graph_version=version,
                                source_port=out_port,
                                target_port=in_port,
                            ).save()
                            logger.info(
                                "auto_create_edge",
                                source_node=nc.source_node.name,
                                source_port=out_port.display_name,
                                target_node=node.name,
                                target_port=in_port.display_name,
                            )
                        except Exception as e:
                            logger.warning(
                                "failed_to_create_edge",
                                source_node=nc.source_node.name,
                                source_port=out_port.display_name,
                                target_node=node.name,
                                target_port=in_port.display_name,
                                error=str(e),
                            )
            else:
                # Simple variable: match by display_name
                out_port = output_ports_by_name.get(in_port.display_name)
                if out_port:
                    try:
                        Edge(
                            graph_version=version,
                            source_port=out_port,
                            target_port=in_port,
                        ).save()
                        logger.info(
                            "auto_create_edge",
                            source_node=nc.source_node.name,
                            source_port=out_port.display_name,
                            target_node=node.name,
                            target_port=in_port.display_name,
                        )
                    except Exception as e:
                        logger.warning(
                            "failed_to_create_edge",
                            source_node=nc.source_node.name,
                            source_port=out_port.display_name,
                            target_node=node.name,
                            target_port=in_port.display_name,
                            error=str(e),
                        )


def create_node(
    version: GraphVersion,
    data: dict[str, Any],
    user: Any,
    organization: Any,
    workspace: Any,
) -> tuple[Node, NodeConnection | None]:
    """
    Create a node within a draft graph version.

    Returns (node, node_connection_or_None).
    """
    node_type = data["type"]
    node_id = data["id"]
    name = data["name"]
    position = data.get("position", {})
    source_node_id = data.get("source_node_id")
    prompt_data = data.get("prompt_template")
    ports_data = data.get("ports", [])
    config = data.get("config") or {}

    node_template = None
    ref_graph_version = None

    if node_type == NodeType.ATOMIC:
        node_template = _resolve_node_template(data["node_template_id"])
    elif node_type == NodeType.SUBGRAPH:
        ref_gv_id = data.get("ref_graph_version_id")
        if ref_gv_id:
            ref_graph_version = _resolve_ref_graph_version(ref_gv_id)

    # Create Node
    node = Node(
        id=node_id,
        graph_version=version,
        node_template=node_template,
        ref_graph_version=ref_graph_version,
        type=node_type,
        name=name,
        config=config,
        position=position,
    )
    node.save(skip_validation=True)

    # Handle LLM prompt nodes (DYNAMIC input_mode)
    if node_template and node_template.input_mode == PortMode.DYNAMIC:
        # LLM-type node — always create PT/PTV/PTN
        effective_prompt = prompt_data or _default_prompt_data()
        pt, pv = _resolve_or_create_pt_ptv(
            effective_prompt, name, user, organization, workspace
        )
        PromptTemplateNode.no_workspace_objects.create(
            node=node,
            prompt_template=pt,
            prompt_version=pv,
        )
        # Auto-create input ports from messages variables (only if prompt provided)
        if prompt_data:
            _create_input_ports_from_prompt(node, prompt_data)
        # Use FE-supplied output ports if provided, otherwise auto-create default
        if ports_data:
            _create_ports_from_fe_array(node, ports_data)
        else:
            _create_default_output_port_from_prompt(node, effective_prompt)

    # Handle subgraph nodes — auto-create input ports from exposed ports or mappings
    elif node_type == NodeType.SUBGRAPH and ref_graph_version:
        input_mappings = data.get("input_mappings", [])

        # Check if explicit input_mappings provided
        if input_mappings:
            _create_subgraph_input_ports_from_mappings(node, input_mappings)
        else:
            # Backwards compatible: auto-create from exposed ports
            _create_subgraph_input_ports(node, ref_graph_version)

        # Use FE-supplied ports if provided, otherwise auto-create output ports
        if ports_data:
            _validate_subgraph_fe_ports(ports_data, ref_graph_version)
            _create_ports_from_fe_array(node, ports_data)
        else:
            _create_subgraph_output_ports(node, ref_graph_version)

    # Handle explicit FE ports (used for non-LLM atomic nodes or when FE sends ports)
    elif ports_data:
        _create_ports_from_fe_array(node, ports_data)

    # Create NodeConnection if source_node_id provided
    nc = None
    if source_node_id:
        source_node = Node.no_workspace_objects.get(
            id=source_node_id, graph_version=version
        )
        nc = NodeConnection(
            graph_version=version,
            source_node=source_node,
            target_node=node,
        )
        nc.save()  # runs clean() — validates no self-connection, same version

    # Auto-create edges based on input ports and existing NodeConnections
    input_mappings = (
        data.get("input_mappings") if node_type == NodeType.SUBGRAPH else None
    )
    _auto_create_edges_for_node(node, input_mappings)

    return node, nc


def update_node(
    node: Node,
    data: dict[str, Any],
    user: Any,
    organization: Any,
    workspace: Any,
) -> Node:
    """
    Partially update a node (name, position, prompt_template).
    """
    if "name" in data:
        node.name = data["name"]
    if "position" in data:
        node.position = data["position"]
    if "config" in data:
        node.config = data["config"] or {}

    # Handle ref_graph_version_id update (subgraph nodes)
    if "ref_graph_version_id" in data:
        ref_gv_id = data["ref_graph_version_id"]
        if ref_gv_id:
            node.ref_graph_version = _resolve_ref_graph_version(ref_gv_id)
        else:
            node.ref_graph_version = None

    node.save(skip_validation=True)

    # Handle input_mappings update (subgraph nodes only)
    if "input_mappings" in data and node.type == NodeType.SUBGRAPH:
        _update_subgraph_input_mappings(node, data["input_mappings"])

    # Handle ports update (output ports only)
    if "ports" in data:
        _replace_output_ports(node, data["ports"])

    # Sync name to linked PromptTemplate (if any)
    if "name" in data:
        ptn = (
            PromptTemplateNode.no_workspace_objects.filter(node=node)
            .select_related("prompt_template")
            .first()
        )
        if ptn:
            ptn.prompt_template.name = data["name"]
            ptn.prompt_template.save()

    prompt_data = data.get("prompt_template")
    if prompt_data is not None:
        _handle_ptv_lifecycle_on_update(
            node, prompt_data, user, organization, workspace
        )

    node.refresh_from_db()
    return node


def cascade_soft_delete_node(node: Node) -> None:
    """
    Soft-delete a node and all related objects.

    Order: edges on ports → node connections → ports → prompt_template_node → node.
    """
    now = timezone.now()
    port_ids = list(
        Port.no_workspace_objects.filter(node=node).values_list("id", flat=True)
    )

    # 1. Edges on this node's ports
    Edge.no_workspace_objects.filter(source_port_id__in=port_ids).update(
        deleted=True, deleted_at=now
    )
    Edge.no_workspace_objects.filter(target_port_id__in=port_ids).update(
        deleted=True, deleted_at=now
    )

    # 2. NodeConnections where this node is source or target
    NodeConnection.no_workspace_objects.filter(source_node=node).update(
        deleted=True, deleted_at=now
    )
    NodeConnection.no_workspace_objects.filter(target_node=node).update(
        deleted=True, deleted_at=now
    )

    # 3. Ports
    Port.no_workspace_objects.filter(node=node).update(deleted=True, deleted_at=now)

    # 4. PromptTemplateNode (if exists)
    PromptTemplateNode.no_workspace_objects.filter(node=node).update(
        deleted=True, deleted_at=now
    )

    # 5. Node itself
    node.deleted = True
    node.deleted_at = now
    node.save(skip_validation=True)


def cascade_soft_delete_node_connection(nc: NodeConnection) -> None:
    """
    Soft-delete a node connection and all edges between the two nodes' ports.
    """
    now = timezone.now()

    source_port_ids = set(
        Port.no_workspace_objects.filter(node=nc.source_node).values_list(
            "id", flat=True
        )
    )
    target_port_ids = set(
        Port.no_workspace_objects.filter(node=nc.target_node).values_list(
            "id", flat=True
        )
    )

    # Delete edges going source→target
    Edge.no_workspace_objects.filter(
        source_port_id__in=source_port_ids,
        target_port_id__in=target_port_ids,
    ).update(deleted=True, deleted_at=now)

    # Delete edges going target→source (reverse direction)
    Edge.no_workspace_objects.filter(
        source_port_id__in=target_port_ids,
        target_port_id__in=source_port_ids,
    ).update(deleted=True, deleted_at=now)

    nc.deleted = True
    nc.deleted_at = now
    nc.save()


def _resolve_node_template(node_template_id: UUID) -> NodeTemplate:
    return NodeTemplate.no_workspace_objects.get(id=node_template_id)


def _resolve_ref_graph_version(ref_graph_version_id: UUID) -> GraphVersion:
    return GraphVersion.no_workspace_objects.get(id=ref_graph_version_id)


def _resolve_or_create_pt_ptv(
    prompt_data: dict[str, Any],
    node_name: str,
    user: Any,
    organization: Any,
    workspace: Any,
) -> tuple[PromptTemplate, PromptVersion]:
    """
    Resolve or create PromptTemplate + PromptVersion based on provided IDs.

    Lifecycle table:
    - No PT, No PV → create new PT + draft PTV
    - PT, No PV → create new draft PTV under PT
    - PT + PV (draft) → update draft PTV in-place
    - PT + PV (committed) → create new draft PTV under PT
    """
    pt_id = prompt_data.get("prompt_template_id")
    pv_id = prompt_data.get("prompt_version_id")
    snapshot = _build_prompt_config_snapshot(prompt_data)

    # Build variable_names dict from messages (model_hub convention)
    messages = prompt_data.get("messages", [])
    _tf = prompt_data.get("template_format") or prompt_data.get(
        "configuration", {}
    ).get("template_format")
    var_names = _extract_variables(messages, template_format=_tf)
    var_names_dict = prompt_data.get("variable_names") or {v: [] for v in var_names}
    pv_metadata = prompt_data.get("metadata") or {}

    if not pt_id:
        # Create new PT + new draft PTV
        pt = PromptTemplate.no_workspace_objects.create(
            name=node_name,
            organization=organization,
            workspace=workspace,
            created_by=user,
            variable_names=list(var_names_dict.keys()),
        )
        if user is not None:
            pt.collaborators.add(user)
        version_str = _get_next_template_version(pt)
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version=version_str,
            prompt_config_snapshot=snapshot,
            variable_names=var_names_dict,
            metadata=pv_metadata,
            is_draft=True,
        )
        return pt, pv

    pt = PromptTemplate.no_workspace_objects.get(id=pt_id)

    if not pv_id:
        # Create new draft PTV under existing PT
        pt.variable_names = list(var_names_dict.keys())
        pt.save()
        if user is not None:
            pt.collaborators.add(user)
        version_str = _get_next_template_version(pt)
        pv = PromptVersion.no_workspace_objects.create(
            original_template=pt,
            template_version=version_str,
            prompt_config_snapshot=snapshot,
            variable_names=var_names_dict,
            metadata=pv_metadata,
            is_draft=True,
        )
        return pt, pv

    pv = PromptVersion.no_workspace_objects.get(id=pv_id)

    if pv.is_draft:
        # Update draft PTV in-place
        pv.prompt_config_snapshot = snapshot
        pv.variable_names = var_names_dict
        pv.metadata = pv_metadata
        pv.save()
        pt.variable_names = list(var_names_dict.keys())
        pt.save()
        return pt, pv

    # PV is committed — create new draft PTV
    pt.variable_names = list(var_names_dict.keys())
    pt.save()
    version_str = _get_next_template_version(pt)
    pv = PromptVersion.no_workspace_objects.create(
        original_template=pt,
        template_version=version_str,
        prompt_config_snapshot=snapshot,
        variable_names=var_names_dict,
        metadata=pv_metadata,
        is_draft=True,
    )
    return pt, pv


def _handle_ptv_lifecycle_on_update(
    node: Node,
    prompt_data: dict[str, Any],
    user: Any,
    organization: Any,
    workspace: Any,
) -> None:
    """
    Handle PTV lifecycle during node PATCH.

    save_prompt_version behavior:
    - false + draft PTV → update in-place
    - false + committed PTV → create new draft PTV
    - true + draft PTV → commit (is_draft=False)
    - true + committed PTV → create new committed PTV
    """
    save_version = prompt_data.get("save_prompt_version", False)
    snapshot = _build_prompt_config_snapshot(prompt_data)

    # Build variable_names and metadata from prompt_data
    messages = prompt_data.get("messages", [])
    _tf = snapshot.get("configuration", {}).get("template_format")
    var_names = _extract_variables(messages, template_format=_tf)
    var_names_dict = prompt_data.get("variable_names") or {v: [] for v in var_names}
    pv_metadata = prompt_data.get("metadata") or {}
    commit_message = prompt_data.get("commit_message") or ""

    ptn = (
        PromptTemplateNode.no_workspace_objects.filter(node=node)
        .select_related("prompt_template", "prompt_version")
        .first()
    )

    pt_id = prompt_data.get("prompt_template_id")
    pv_id = prompt_data.get("prompt_version_id")

    if ptn is None:
        # Node didn't have a PTN before — create from scratch
        pt, pv = _resolve_or_create_pt_ptv(
            prompt_data, node.name, user, organization, workspace
        )
        PromptTemplateNode.no_workspace_objects.create(
            node=node, prompt_template=pt, prompt_version=pv
        )
    else:
        pt = (
            PromptTemplate.no_workspace_objects.get(id=pt_id)
            if pt_id
            else ptn.prompt_template
        )
        pv = (
            PromptVersion.no_workspace_objects.get(id=pv_id)
            if pv_id
            else ptn.prompt_version
        )

        if not save_version:
            if pv.is_draft:
                # Update draft PTV in-place
                pv.prompt_config_snapshot = snapshot
                pv.variable_names = var_names_dict
                pv.metadata = pv_metadata
                pv.save()
            else:
                # Create new draft PTV
                version_str = _get_next_template_version(pt)
                pv = PromptVersion.no_workspace_objects.create(
                    original_template=pt,
                    template_version=version_str,
                    prompt_config_snapshot=snapshot,
                    variable_names=var_names_dict,
                    metadata=pv_metadata,
                    is_draft=True,
                )
        else:
            if pv.is_draft:
                # Commit the draft
                pv.prompt_config_snapshot = snapshot
                pv.variable_names = var_names_dict
                pv.metadata = pv_metadata
                pv.commit_message = commit_message
                pv.is_draft = False
                pv.save()
            else:
                # Create new committed PTV
                version_str = _get_next_template_version(pt)
                pv = PromptVersion.no_workspace_objects.create(
                    original_template=pt,
                    template_version=version_str,
                    prompt_config_snapshot=snapshot,
                    variable_names=var_names_dict,
                    metadata=pv_metadata,
                    commit_message=commit_message,
                    is_draft=False,
                )

        # Sync variable_names to parent PromptTemplate
        pt.variable_names = list(var_names_dict.keys())
        pt.save()

        # Update PTN link if PT/PV changed
        if ptn.prompt_template_id != pt.id or ptn.prompt_version_id != pv.id:
            ptn.prompt_template = pt
            ptn.prompt_version = pv
            ptn.save()

    # Reconcile ports based on new messages
    messages = prompt_data.get("messages")
    if messages is not None:
        _reconcile_prompt_ports(node, prompt_data)
        # Auto-create edges for new input ports
        _auto_create_edges_for_node(node)


def _reconcile_prompt_ports(node: Node, prompt_data: dict[str, Any]) -> None:
    """
    Reconcile input ports against {{variables}} in messages,
    and update output port data_schema if response_format changed.

    Uses set operations for efficient diff computation (O(n) instead of O(n²)).
    """
    now = timezone.now()
    messages = prompt_data.get("messages", [])
    _tf = prompt_data.get("template_format") or prompt_data.get(
        "configuration", {}
    ).get("template_format")
    new_vars = _extract_variables(messages, template_format=_tf)

    existing_input_ports = list(
        Port.no_workspace_objects.filter(node=node, direction=PortDirection.INPUT)
    )
    existing_names = {p.display_name: p for p in existing_input_ports}

    # Use set operations to find differences efficiently
    new_vars_set = set(new_vars)
    existing_vars_set = set(existing_names.keys())

    to_add = new_vars_set - existing_vars_set
    to_remove = existing_vars_set - new_vars_set

    # Create ports for new variables
    # We are not adding edges here since there is no concept of referencing data from other nodes in Prompt Playground
    for var in to_add:
        Port(
            node=node,
            key="custom",  # Always use "custom" for dynamic prompt variables
            display_name=var,
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)

    # Soft-delete ports for removed variables + cascade edges
    for name in to_remove:
        port = existing_names[name]
        # Cascade edges on this port
        Edge.no_workspace_objects.filter(source_port=port).update(
            deleted=True, deleted_at=now
        )
        Edge.no_workspace_objects.filter(target_port=port).update(
            deleted=True, deleted_at=now
        )
        port.deleted = True
        port.deleted_at = now
        port.save(skip_validation=True)

    # Update output port data_schema if response_format provided
    response_format = prompt_data.get("response_format")
    if response_format is not None:
        # No normalization needed - expect dict format directly
        output_port = Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.OUTPUT, display_name="response"
        ).first()
        if output_port:
            output_port.data_schema = _output_data_schema(
                response_format, prompt_data.get("response_schema")
            )
            output_port.save(skip_validation=True)


def _create_ports_from_prompt(node: Node, prompt_data: dict[str, Any]) -> None:
    """Create ports for an LLM prompt node from its messages."""
    messages = prompt_data.get("messages", [])
    _tf = prompt_data.get("template_format") or prompt_data.get(
        "configuration", {}
    ).get("template_format")
    variables = _extract_variables(messages, template_format=_tf)

    # Input ports for each variable
    for var in variables:
        Port(
            node=node,
            key="custom",  # Always use "custom" for dynamic prompt variables
            display_name=var,
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)

    # Output port
    response_format = prompt_data.get("response_format", "text")
    response_schema = prompt_data.get("response_schema")
    Port(
        node=node,
        key=_get_port_key_for_template(node, PortDirection.OUTPUT, "response"),
        display_name="response",
        direction=PortDirection.OUTPUT,
        data_schema=_output_data_schema(response_format, response_schema),
    ).save(skip_validation=True)


def _create_input_ports_from_prompt(node: Node, prompt_data: dict[str, Any]) -> None:
    """Create input ports for an LLM prompt node from variables in messages."""
    messages = prompt_data.get("messages", [])
    _tf = prompt_data.get("template_format") or prompt_data.get(
        "configuration", {}
    ).get("template_format")
    variables = _extract_variables(messages, template_format=_tf)

    for var in variables:
        Port(
            node=node,
            key="custom",  # Always use "custom" for dynamic prompt variables
            display_name=var,
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)


def _create_default_output_port_from_prompt(
    node: Node, prompt_data: dict[str, Any]
) -> None:
    """Fallback: create the default 'response' output port for an LLM node."""
    response_format = prompt_data.get("response_format", "text")
    response_schema = prompt_data.get("response_schema")
    Port(
        node=node,
        key=_get_port_key_for_template(node, PortDirection.OUTPUT, "response"),
        display_name="response",
        direction=PortDirection.OUTPUT,
        data_schema=_output_data_schema(response_format, response_schema),
    ).save(skip_validation=True)


def _create_ports_from_fe_array(node: Node, ports_data: list[dict]) -> None:
    """Create ports using FE-provided IDs."""
    for pd in ports_data:
        Port(
            id=pd["id"],
            node=node,
            key=pd["key"],
            display_name=pd["display_name"],
            direction=pd["direction"],
            data_schema=pd.get("data_schema", {}),
            ref_port_id=pd.get("ref_port_id"),
        ).save(skip_validation=True)


def _create_subgraph_ports(node: Node, ref_graph_version: GraphVersion) -> None:
    """Auto-create ports for a subgraph node from exposed ports."""
    exposed_map = get_exposed_ports_for_versions([ref_graph_version.id])
    exposed_ports = exposed_map.get(ref_graph_version.id, [])

    seen_names: set[str] = set()
    for ep in exposed_ports:
        display_name = ep["display_name"]
        # Handle duplicates
        base_name = display_name
        counter = 1
        while display_name in seen_names:
            display_name = f"{base_name}_{counter}"
            counter += 1
        seen_names.add(display_name)

        Port(
            node=node,
            key="custom",
            display_name=display_name,
            direction=ep["direction"],
            data_schema=ep.get("data_schema", {}),
            ref_port_id=ep.get("id"),
        ).save(skip_validation=True)


def _dedupe_name(name: str, seen: set[str]) -> str:
    """Return a unique display name, appending _1, _2, etc. if needed."""
    base, counter, result = name, 1, name
    while result in seen:
        result = f"{base}_{counter}"
        counter += 1
    seen.add(result)
    return result


def _create_subgraph_input_ports(node: Node, ref_graph_version: GraphVersion) -> None:
    """Auto-create input ports for a subgraph node from exposed input ports."""
    exposed_map = get_exposed_ports_for_versions([ref_graph_version.id])
    exposed_ports = exposed_map.get(ref_graph_version.id, [])

    seen_names: set[str] = set()
    for ep in exposed_ports:
        if ep["direction"] != PortDirection.INPUT:
            continue
        display_name = _dedupe_name(ep["display_name"], seen_names)
        Port(
            node=node,
            key="custom",
            display_name=display_name,
            direction=PortDirection.INPUT,
            data_schema=ep.get("data_schema", {}),
            ref_port_id=ep.get("id"),
        ).save(skip_validation=True)


def _create_subgraph_output_ports(node: Node, ref_graph_version: GraphVersion) -> None:
    """Fallback: auto-create output ports for a subgraph node from exposed output ports."""
    exposed_map = get_exposed_ports_for_versions([ref_graph_version.id])
    exposed_ports = exposed_map.get(ref_graph_version.id, [])

    seen_names: set[str] = set()
    for ep in exposed_ports:
        if ep["direction"] != PortDirection.OUTPUT:
            continue
        display_name = _dedupe_name(ep["display_name"], seen_names)
        Port(
            node=node,
            key="custom",
            display_name=display_name,
            direction=PortDirection.OUTPUT,
            data_schema=ep.get("data_schema", {}),
            ref_port_id=ep.get("id"),
        ).save(skip_validation=True)


def _validate_subgraph_fe_ports(
    ports_data: list[dict], ref_graph_version: GraphVersion
) -> None:
    """Validate that FE-sent ports have valid ref_port_ids matching exposed ports."""
    exposed_map = get_exposed_ports_for_versions([ref_graph_version.id])
    exposed_ports = exposed_map.get(ref_graph_version.id, [])
    exposed_port_ids = {ep["id"] for ep in exposed_ports}

    for pd in ports_data:
        ref_port_id = pd.get("ref_port_id")
        if ref_port_id and ref_port_id not in exposed_port_ids:
            raise ValidationError(
                f"ref_port_id '{ref_port_id}' does not match any exposed "
                f"port in the referenced graph version."
            )


def _create_subgraph_input_ports_from_mappings(
    node: Node, input_mappings: list[dict[str, str | None]]
) -> None:
    """Create input ports for subgraph node from input_mappings list.

    Args:
        node: The subgraph node to create ports for.
        input_mappings: List of mappings with 'key' (port name) and 'value' (source ref or null).
    """
    for mapping in input_mappings:
        display_name = mapping["key"]
        Port(
            node=node,
            key="custom",
            display_name=display_name,
            direction=PortDirection.INPUT,
            data_schema={"type": "string"},
        ).save(skip_validation=True)


def _create_edges_from_input_mappings(
    node: Node, input_mappings: list[dict[str, str | None]]
) -> None:
    """Create edges based on explicit input_mappings.

    For each mapping:
    - Parse "NodeName.port_display_name" format
    - Find source node and port by name
    - Create edge from source port to target input port
    - Log warnings for invalid/missing references (don't fail)

    Args:
        node: The target node for which to create edges.
        input_mappings: List of mappings with 'key' (port name) and 'value' (source ref or null).
    """
    version = node.graph_version

    # Get all nodes in this version for lookup by name
    nodes_by_name = {
        n.name: n for n in Node.no_workspace_objects.filter(graph_version=version)
    }

    # Get target input ports by display_name
    target_input_ports = {
        p.display_name: p
        for p in Port.no_workspace_objects.filter(
            node=node, direction=PortDirection.INPUT
        )
    }

    for mapping in input_mappings:
        input_display_name = mapping["key"]
        source_ref = mapping["value"]

        if source_ref is None:
            # Globally exposed input, no edge
            logger.debug(
                "input_mapping_null_value",
                node=node.name,
                input_port=input_display_name,
                message="Globally exposed input, no edge created",
            )
            continue

        # Parse "NodeName.port_display_name"
        parts = source_ref.split(".", 1)
        if len(parts) != 2:
            logger.warning(
                "input_mapping_invalid_format",
                node=node.name,
                input_port=input_display_name,
                source_ref=source_ref,
                message="Expected format 'NodeName.port_display_name'",
            )
            continue

        source_node_name, source_port_name = parts

        # Find source node by name
        source_node = nodes_by_name.get(source_node_name)
        if not source_node:
            logger.warning(
                "input_mapping_source_node_not_found",
                node=node.name,
                input_port=input_display_name,
                source_node_name=source_node_name,
            )
            continue

        # Find source port by display_name
        source_port = Port.no_workspace_objects.filter(
            node=source_node,
            direction=PortDirection.OUTPUT,
            display_name=source_port_name,
        ).first()

        if not source_port:
            logger.warning(
                "input_mapping_source_port_not_found",
                node=node.name,
                input_port=input_display_name,
                source_node=source_node_name,
                source_port=source_port_name,
            )
            continue

        # Find target port
        target_port = target_input_ports.get(input_display_name)
        if not target_port:
            logger.warning(
                "input_mapping_target_port_not_found",
                node=node.name,
                input_port=input_display_name,
            )
            continue

        # Verify a NodeConnection exists (edges require an existing NC)
        nc_exists = NodeConnection.no_workspace_objects.filter(
            graph_version=version,
            source_node=source_node,
            target_node=node,
        ).exists()
        if not nc_exists:
            logger.warning(
                "input_mapping_no_node_connection",
                source_node=source_node_name,
                target_node=node.name,
                message="No NodeConnection exists; skipping edge creation",
            )
            continue

        # Create edge
        try:
            Edge(
                graph_version=version,
                source_port=source_port,
                target_port=target_port,
            ).save()
            logger.info(
                "input_mapping_edge_created",
                source_node=source_node_name,
                source_port=source_port_name,
                target_node=node.name,
                target_port=input_display_name,
            )
        except Exception as e:
            logger.warning(
                "input_mapping_edge_creation_failed",
                source_node=source_node_name,
                source_port=source_port_name,
                target_node=node.name,
                target_port=input_display_name,
                error=str(e),
            )


def _update_subgraph_input_mappings(
    node: Node, input_mappings: list[dict[str, str | None]]
) -> None:
    """Update subgraph input ports/edges using REPLACE strategy.

    Steps:
    1. Soft-delete all existing input ports (cascades to edges)
    2. Create new input ports from mapping keys
    3. Create new edges from mapping values

    Args:
        node: The subgraph node to update.
        input_mappings: New input mappings list of {key, value} dicts.
    """
    from django.db.models import Q

    now = timezone.now()

    # Get existing input ports
    existing_input_ports = Port.no_workspace_objects.filter(
        node=node, direction=PortDirection.INPUT
    )

    # Cascade-delete edges connected to input ports
    for port in existing_input_ports:
        Edge.no_workspace_objects.filter(
            Q(source_port=port) | Q(target_port=port)
        ).update(deleted=True, deleted_at=now)

    # Soft-delete input ports
    existing_input_ports.update(deleted=True, deleted_at=now)

    # Create new ports and edges from mappings
    _create_subgraph_input_ports_from_mappings(node, input_mappings)
    _create_edges_from_input_mappings(node, input_mappings)


def _replace_output_ports(node: Node, ports_data: list[dict]) -> None:
    """
    Replace ONLY output ports on a node, preserving input ports.

    This operation:
    1. Soft-deletes existing OUTPUT ports only
    2. Cascade soft-deletes edges connected to those output ports
    3. Creates new output ports from ports_data
    4. Leaves INPUT ports completely untouched

    Input ports are managed separately:
    - For subgraph nodes: via input_mappings
    - For LLM/atomic nodes: auto-generated from prompt template variables

    Output ports are configured explicitly:
    - User selects which outputs to expose from subgraph
    - User configures response format for LLM nodes

    Args:
        node: The node whose output ports should be replaced
        ports_data: List of new OUTPUT port definitions (validated by PortCreateSerializer)
                   Expected to contain only direction="output" ports
    """
    now = timezone.now()

    # Step 1: Get existing OUTPUT ports only (preserve input ports)
    existing_output_ports = Port.no_workspace_objects.filter(
        node=node, direction=PortDirection.OUTPUT
    )
    existing_output_port_ids = list(existing_output_ports.values_list("id", flat=True))

    # Step 2: Cascade soft-delete edges connected to output ports
    # Only delete edges where the OUTPUT port is involved (source or target)
    Edge.no_workspace_objects.filter(
        source_port_id__in=existing_output_port_ids
    ).update(deleted=True, deleted_at=now)
    Edge.no_workspace_objects.filter(
        target_port_id__in=existing_output_port_ids
    ).update(deleted=True, deleted_at=now)

    # Step 3: Soft-delete existing OUTPUT ports (input ports preserved)
    existing_output_ports.update(deleted=True, deleted_at=now)

    # Step 4: Create new output ports from FE-provided data
    # Note: Frontend should only send output ports in this array
    _create_ports_from_fe_array(node, ports_data)


def _extract_variables(
    messages: list[dict], template_format: str | None = None
) -> list[str]:
    """Extract unique variable names from message contents, preserving order.

    When template_format is "jinja" or "jinja2", uses Jinja2 AST analysis to
    correctly identify input variables (excluding loop/set scoped vars).
    Otherwise falls back to {{variable}} regex matching.

    Handles new message format where content is an array of content items:
    [{
        "id": "msg-0",
        "role": "user",
        "content": [
            {"type": "text", "text": "What is {{city}}?"}
        ]
    }]
    """
    use_jinja = template_format in ("jinja", "jinja2")

    if use_jinja:
        from model_hub.utils.jinja_variables import extract_jinja_variables

        # Collect all text from messages, then do a single AST parse
        all_text = []
        for msg in messages:
            content = msg.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        all_text.append(item.get("text", ""))
            elif isinstance(content, str):
                all_text.append(content)
        return extract_jinja_variables("\n".join(all_text))

    # Default: mustache {{ }} regex
    seen: set[str] = set()
    result: list[str] = []
    for msg in messages:
        content = msg.get("content", [])

        # Handle content array (new format)
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    for match in _VARIABLE_RE.finditer(text):
                        var = match.group(1)
                        if var not in seen:
                            seen.add(var)
                            result.append(var)
        # Legacy: Handle string content (should not happen with new serializer)
        elif isinstance(content, str):
            for match in _VARIABLE_RE.finditer(content):
                var = match.group(1)
                if var not in seen:
                    seen.add(var)
                    result.append(var)

    return result


def _get_port_key_for_template(
    node: Node, direction: str, fallback_key: str = "custom"
) -> str:
    """
    Get the appropriate port key based on the node's template port mode.

    Returns:
    - DYNAMIC mode: fallback_key (usually "custom")
    - STRICT mode: First key from template definition
    - EXTENSIBLE mode: First key from template definition or fallback_key
    """
    if node.type != NodeType.ATOMIC or not node.node_template:
        return fallback_key

    template = node.node_template
    if direction == PortDirection.INPUT:
        mode = template.input_mode
        definition = template.input_definition
    else:
        mode = template.output_mode
        definition = template.output_definition

    if mode == PortMode.DYNAMIC:
        return fallback_key

    # STRICT or EXTENSIBLE: use template-defined key
    if definition and len(definition) > 0:
        return definition[0]["key"]

    return fallback_key


def _build_prompt_config_snapshot(prompt_data: dict[str, Any]) -> dict[str, Any]:
    """Build the prompt_config_snapshot dict stored in PromptVersion.

    Matches the structure used by model_hub prompt template APIs:
    model config fields are nested under a ``configuration`` key.
    """
    configuration: dict[str, Any] = {}

    _CONFIG_KEYS = (
        "model",
        "temperature",
        "max_tokens",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "response_format",
        "response_schema",
        "output_format",
        "tools",
        "tool_choice",
        "model_detail",
        "template_format",
    )
    for key in _CONFIG_KEYS:
        value = prompt_data.get(key)
        if value is not None:
            configuration[key] = value

    # Ensure response_format always has a default inside configuration
    configuration.setdefault("response_format", "text")

    # Build variable_names dict for the snapshot (model_hub convention)
    messages = prompt_data.get("messages", [])
    _tf = configuration.get("template_format")
    var_names = _extract_variables(messages, template_format=_tf)
    var_names_dict = prompt_data.get("variable_names") or {v: [] for v in var_names}

    return {
        "messages": messages,
        "configuration": configuration,
        "response_format": configuration.get("response_format", "text"),
        "response_schema": configuration.get("response_schema"),
        "variable_names": var_names_dict,
    }


def _output_data_schema(
    response_format: str, response_schema: dict | None = None
) -> dict:
    """Return the output port data_schema based on response_format.

    Args:
        response_format: LLM output format. Options:
            - "text": Plain text (default) → {"type": "string"}
            - "json": Free-form JSON → {"type": "object"}
            - "json_schema": Structured JSON with schema → returns response_schema
            - UUID string: Saved schema reference (handled at runtime)
        response_schema: JSON Schema (Draft 7) for structured outputs.
            Required when response_format="json_schema".

    Returns:
        JSON schema describing the output port's data type.
    """
    if response_format == "json_schema" and response_schema:
        return response_schema
    if response_format == "json":
        return {"type": "object"}
    return {"type": "string"}


def _get_next_template_version(pt: PromptTemplate) -> str:
    """Return the next version string like 'v1', 'v2', etc."""
    count = PromptVersion.no_workspace_objects.filter(original_template=pt).count()
    return f"v{count + 1}"
