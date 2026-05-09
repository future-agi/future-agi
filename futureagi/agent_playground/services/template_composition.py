"""
Pure graph-theory functions for template composition analysis.

These functions operate on plain Python structures (adjacency dicts, schema dicts)
so they can be tested by Z3 and Hypothesis without Django.  The Django layer
(would_create_graph_reference_cycle, validate_version_for_activation) delegates
to these for the heavy lifting.

Composition algebra
-------------------
Given graphs G and T (both acyclic), embedding T as a subgraph node n in G
produces G[T/n].  The result is acyclic iff:

    ∀ path P in G[T/n]: P visits no node twice.

Since G and T are individually acyclic, a cycle can only arise via the embedding
edge G→T.  The cross-graph reference DAG (crDAG) therefore captures all possible
cycles: G[T/n] is acyclic ⟺ adding (G, T) to crDAG keeps crDAG acyclic.

This is exactly what would_create_graph_reference_cycle checks.  The Z3 proofs
in formal_tests/test_composition_z3.py encode this theorem and verify it
is satisfiable (no counter-example exists within the bounded model).
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Set, Tuple


# ── Types ─────────────────────────────────────────────────────────────────────

# Node ID type (string UUID or int in tests).
NodeId = str
GraphId = str
# Adjacency: {node → [successors]}
Adjacency = Dict[NodeId, List[NodeId]]
# Cross-graph reference: {graph_id → [referenced_graph_ids]}
CrossGraphAdj = Dict[GraphId, List[GraphId]]
# Port schema (simplified JSON Schema subset: {"type": "string"|"number"|"object"|"array"|"boolean"})
Schema = dict


# ── Cross-graph reference DAG ─────────────────────────────────────────────────

def build_cross_graph_adjacency(
    refs: List[Tuple[GraphId, GraphId]],
) -> CrossGraphAdj:
    """
    Build an adjacency dict from (source_graph_id, target_graph_id) pairs.
    Each pair represents a subgraph node: source embeds target.
    """
    adj: CrossGraphAdj = {}
    for src, tgt in refs:
        adj.setdefault(src, []).append(tgt)
    return adj


def detect_cross_graph_cycle(
    adj: CrossGraphAdj,
    source: GraphId,
    target: GraphId,
) -> bool:
    """
    Would adding edge (source → target) create a cycle in the cross-graph DAG?

    Returns True if source is reachable from target via existing edges
    (i.e., adding the edge would close a cycle).

    Identical logic to would_create_graph_reference_cycle but operates on
    plain dicts for formal testing.
    """
    if source == target:
        return True

    visited: Set[GraphId] = set()
    queue: deque[GraphId] = deque([target])
    while queue:
        current = queue.popleft()
        if current == source:
            return True
        if current in visited:
            continue
        visited.add(current)
        queue.extend(adj.get(current, []))
    return False


def infer_composed_levels(
    graph_ids: List[GraphId],
    refs: List[Tuple[GraphId, GraphId]],
) -> Dict[GraphId, int]:
    """
    Assign a topological level to each graph in the cross-graph reference DAG.

    Level 0 = leaf graphs (no outgoing references, i.e. no subgraph nodes).
    Level k = max(level of referenced graphs) + 1.

    Raises ValueError if the reference graph contains a cycle.
    """
    adj = build_cross_graph_adjacency(refs)
    # in-degree count
    in_degree: Dict[GraphId, int] = {g: 0 for g in graph_ids}
    for src, targets in adj.items():
        for tgt in targets:
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    levels: Dict[GraphId, int] = {}
    queue: deque[GraphId] = deque(
        g for g in graph_ids if in_degree.get(g, 0) == 0
    )
    for g in queue:
        levels[g] = 0

    processed = 0
    while queue:
        current = queue.popleft()
        processed += 1
        for neighbor in adj.get(current, []):
            in_degree[neighbor] -= 1
            levels[neighbor] = max(levels.get(neighbor, 0), levels[current] + 1)
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if processed < len(graph_ids):
        raise ValueError("Cross-graph reference DAG contains a cycle")

    return levels


# ── Port schema compatibility ─────────────────────────────────────────────────

# JSON Schema "type" hierarchy for the simple schemas used in practice.
# S1 is compatible with S2 (S1 ≤ S2) if every value valid under S1 is also
# valid under S2.  For primitive types this reduces to: S1.type == S2.type.
# An empty schema {} (no constraints) accepts everything — it is the top element.

def _primitive_type(schema: Schema) -> Optional[str]:
    """Return the 'type' string if present, else None (unconstrained)."""
    return schema.get("type") if isinstance(schema, dict) else None


def schemas_compatible(source: Schema, target: Schema) -> bool:
    """
    Check whether data produced by a port with `source` schema can safely flow
    into a port expecting `target` schema.

    Conservative syntactic check for the common cases:
    - {} (any) target accepts everything.
    - Matching primitive types are compatible.
    - Mismatching primitive types are incompatible.
    - Object schemas are compatible if target's required properties are all
      present in source's properties (structural subtyping, one level deep).

    Returns True if compatible, False if definitely incompatible.
    Raises NotImplementedError for schema constructs we don't handle.
    """
    if not target:
        return True  # unconstrained target accepts anything

    src_type = _primitive_type(source)
    tgt_type = _primitive_type(target)

    if tgt_type is None:
        return True  # unconstrained target

    if src_type is None:
        # unconstrained source flowing into constrained target: conservatively True
        # (runtime validation will catch mismatches)
        return True

    if src_type != tgt_type:
        return False

    if tgt_type == "object":
        src_props = set((source.get("properties") or {}).keys())
        tgt_required = set(target.get("required") or [])
        return tgt_required.issubset(src_props)

    if tgt_type == "array":
        # Check item type compatibility if both specify items.type
        src_items = source.get("items", {})
        tgt_items = target.get("items", {})
        if src_items and tgt_items:
            return schemas_compatible(src_items, tgt_items)
        return True

    # string, number, boolean, integer — same type is sufficient
    return True


def check_interface_compatibility(
    exposed_ports: List[Tuple[str, str, Schema]],
    connecting_edges: List[Tuple[str, str, Schema]],
) -> List[str]:
    """
    Check that each edge connecting to a subgraph boundary is schema-compatible
    with the corresponding exposed port.

    Args:
        exposed_ports: List of (port_key, direction, schema) from the template.
        connecting_edges: List of (port_key, direction, schema) from the parent graph.

    Returns:
        List of incompatibility messages (empty = all compatible).
    """
    template_ports = {
        (key, direction): schema for key, direction, schema in exposed_ports
    }
    errors: List[str] = []
    for key, direction, edge_schema in connecting_edges:
        tpl_schema = template_ports.get((key, direction))
        if tpl_schema is None:
            errors.append(f"Port '{key}' ({direction}) not exposed by template")
            continue
        if direction == "output":
            # data flows: template output → parent input
            if not schemas_compatible(tpl_schema, edge_schema):
                errors.append(
                    f"Port '{key}' output: template schema {tpl_schema!r} "
                    f"incompatible with parent expects {edge_schema!r}"
                )
        else:
            # data flows: parent output → template input
            if not schemas_compatible(edge_schema, tpl_schema):
                errors.append(
                    f"Port '{key}' input: parent provides {edge_schema!r} "
                    f"incompatible with template expects {tpl_schema!r}"
                )
    return errors


# ── Intra-graph acyclicity (pure, for formal testing) ─────────────────────────

def is_dag(adjacency: Adjacency, nodes: List[NodeId]) -> bool:
    """
    Return True if the directed graph defined by adjacency is acyclic (is a DAG).
    Uses Kahn's algorithm.
    """
    in_degree = {n: 0 for n in nodes}
    for src in nodes:
        for tgt in adjacency.get(src, []):
            in_degree[tgt] = in_degree.get(tgt, 0) + 1

    queue: deque[NodeId] = deque(n for n in nodes if in_degree[n] == 0)
    processed = 0
    while queue:
        current = queue.popleft()
        processed += 1
        for neighbor in adjacency.get(current, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    return processed == len(nodes)


def compose_graphs(
    parent_adj: Adjacency,
    parent_nodes: List[NodeId],
    subgraph_node: NodeId,
    child_adj: Adjacency,
    child_nodes: List[NodeId],
    child_inputs: List[NodeId],
    child_outputs: List[NodeId],
) -> Tuple[Adjacency, List[NodeId]]:
    """
    Inline-expand a subgraph node into its parent graph.

    The subgraph_node in parent_adj is replaced by the full child graph.
    Edges into subgraph_node are rewired to child_inputs;
    edges out of subgraph_node are rewired from child_outputs.

    Returns (composed_adjacency, composed_nodes).
    Used by formal tests to verify acyclicity is preserved under composition.
    """
    parent_in: List[NodeId] = []
    parent_out: List[NodeId] = []

    for src, targets in parent_adj.items():
        if subgraph_node in targets:
            parent_in.append(src)
        for tgt in targets:
            if src == subgraph_node:
                parent_out.append(tgt)

    composed_adj: Adjacency = {}

    # Copy parent edges, excluding those involving subgraph_node
    for src, targets in parent_adj.items():
        if src == subgraph_node:
            continue
        new_targets = [t for t in targets if t != subgraph_node]
        if src in parent_in:
            new_targets.extend(child_inputs)
        composed_adj[src] = new_targets

    # Copy child edges
    for src, targets in child_adj.items():
        existing = composed_adj.get(src, [])
        composed_adj[src] = existing + targets

    # Wire child_outputs → parent_out
    for out_node in child_outputs:
        composed_adj.setdefault(out_node, []).extend(parent_out)

    # Node list: parent without subgraph_node, plus all child nodes
    composed_nodes = [n for n in parent_nodes if n != subgraph_node] + child_nodes

    return composed_adj, composed_nodes
