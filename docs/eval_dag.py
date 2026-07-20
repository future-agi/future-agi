"""
Composite eval dependency DAG analyser.

Generates SVG diagrams showing:
  - The dependency graph between EvalTemplates via CompositeEvalChild
  - Topological levels (determines trigger order)
  - Critical path (longest chain = bottleneck for latency)
  - Cycle detection (a cycle would make auto-eval unschedulable)

Usage (from futureagi/ with Django configured):
    python ../docs/eval_dag.py --org <org_id> [--root <template_id>]

Usage (standalone, from repo root, with synthetic data for diagrams):
    python docs/eval_dag.py --demo

SVGs are written to docs/diagrams/.
"""

import argparse
import os
import sys
from collections import defaultdict, deque

# ---------------------------------------------------------------------------
# Graph primitives — pure functions, Z3-verifiable
# ---------------------------------------------------------------------------

def build_adjacency(edges):
    """
    edges: list of (parent_id, child_id, weight) tuples
    Returns: {parent_id: [(child_id, weight)]}
    """
    adj = defaultdict(list)
    for parent, child, weight in edges:
        adj[parent].append((child, weight))
    return dict(adj)


def detect_cycles(adj, nodes):
    """
    DFS-based cycle detection.
    Returns: list of cycles as lists of node IDs, or [] if DAG is sound.

    Invariant (Z3-verifiable): if detect_cycles returns [], then for all
    paths p in adj, p visits no node twice.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    parent = {}
    cycles = []

    def dfs(u):
        color[u] = GRAY
        for v, _ in adj.get(u, []):
            if color[v] == GRAY:
                # Reconstruct cycle
                cycle = [v]
                cur = u
                while cur != v:
                    cycle.append(cur)
                    cur = parent.get(cur, cur)
                cycle.reverse()
                cycles.append(cycle)
            elif color[v] == WHITE:
                parent[v] = u
                dfs(v)
        color[u] = BLACK

    for node in nodes:
        if color[node] == WHITE:
            dfs(node)
    return cycles


def topological_sort(adj, nodes):
    """
    Kahn's algorithm (BFS-based).
    Returns: list of node IDs in topological order, or raises ValueError on cycle.

    Property: for every edge (u, v) in adj, u appears before v in the result.
    """
    in_degree = {n: 0 for n in nodes}
    for u in nodes:
        for v, _ in adj.get(u, []):
            in_degree[v] = in_degree.get(v, 0) + 1

    queue = deque(n for n in nodes if in_degree[n] == 0)
    order = []
    while queue:
        u = queue.popleft()
        order.append(u)
        for v, _ in adj.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    if len(order) != len(nodes):
        raise ValueError("Cycle detected — graph is not a DAG")
    return order


def critical_path(adj, nodes, topo_order, node_cost):
    """
    Longest path in the DAG (by summed node_cost).
    Returns: (max_latency, path_as_list_of_node_ids)

    This is the minimum latency bound for auto-eval trigger completion:
    no matter how many parallel workers you add, you wait at least
    critical_path_cost for a composite eval to finish.
    """
    dist = {n: node_cost.get(n, 1) for n in nodes}
    pred = {n: None for n in nodes}

    for u in topo_order:
        for v, _ in adj.get(u, []):
            candidate = dist[u] + node_cost.get(v, 1)
            if candidate > dist[v]:
                dist[v] = candidate
                pred[v] = u

    # Find sink with max distance
    sink = max(nodes, key=lambda n: dist[n])
    path = []
    cur = sink
    while cur is not None:
        path.append(cur)
        cur = pred[cur]
    path.reverse()
    return dist[sink], path


def assign_levels(adj, nodes, topo_order):
    """Assign each node to a topological level (0 = sources)."""
    level = {n: 0 for n in nodes}
    for u in topo_order:
        for v, _ in adj.get(u, []):
            level[v] = max(level[v], level[u] + 1)
    return level


# ---------------------------------------------------------------------------
# SVG generation via graphviz DOT
# ---------------------------------------------------------------------------

def render_dag_svg(nodes, adj, levels, critical, node_labels, out_path):
    """
    Render the DAG as an SVG using graphviz dot.
    - Nodes coloured by topological level (blue gradient)
    - Critical path edges highlighted in red
    - Edge labels show weights
    """
    try:
        import graphviz
    except ImportError:
        print("pip install graphviz  (and brew install graphviz on macOS)")
        return None

    critical_path_set = set(zip(critical[1], critical[1][1:]))

    dot = graphviz.Digraph(
        name="EvalDependencyDAG",
        graph_attr={
            "rankdir": "LR",
            "bgcolor": "#1a1a2e",
            "fontname": "Helvetica",
            "splines": "polyline",
            "nodesep": "0.6",
            "ranksep": "0.8",
        },
        node_attr={
            "shape": "box",
            "style": "filled,rounded",
            "fontname": "Helvetica",
            "fontsize": "12",
            "fontcolor": "white",
            "penwidth": "0",
        },
        edge_attr={
            "fontname": "Helvetica",
            "fontsize": "10",
            "fontcolor": "#aaaacc",
            "color": "#4444aa",
            "penwidth": "1.5",
        },
    )

    # Colour palette: deeper blue = higher level (closer to output)
    level_colours = [
        "#16213e", "#0f3460", "#1a4a7a", "#1e5f8e",
        "#2274a5", "#2589bd", "#28a0d5",
    ]
    max_level = max(levels.values(), default=0)

    for n in nodes:
        lvl = levels[n]
        colour = level_colours[min(lvl, len(level_colours) - 1)]
        is_critical = n in critical[1]
        dot.node(
            str(n),
            label=node_labels.get(n, str(n)),
            fillcolor=colour,
            color="#ff6b6b" if is_critical else colour,
            penwidth="2" if is_critical else "0",
        )

    for u in nodes:
        for v, weight in adj.get(u, []):
            is_crit = (u, v) in critical_path_set
            dot.edge(
                str(u), str(v),
                label=f"w={weight:.1f}",
                color="#ff6b6b" if is_crit else "#4444aa",
                penwidth="3" if is_crit else "1.5",
            )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    dot.render(out_path, format="svg", cleanup=True)
    return out_path + ".svg"


# ---------------------------------------------------------------------------
# Demo data — synthetic composite eval graph
# ---------------------------------------------------------------------------

DEMO_NODES = [
    "toxicity",
    "relevance",
    "hallucination",
    "fluency",
    "composite_safety",    # toxicity + hallucination → safety score
    "composite_quality",   # relevance + fluency → quality score
    "composite_final",     # safety + quality → final
]

DEMO_EDGES = [
    # (parent, child, weight)  — parent must complete before child starts
    ("toxicity",         "composite_safety",  0.5),
    ("hallucination",    "composite_safety",  0.5),
    ("relevance",        "composite_quality", 0.6),
    ("fluency",          "composite_quality", 0.4),
    ("composite_safety", "composite_final",   0.5),
    ("composite_quality","composite_final",   0.5),
]

DEMO_COSTS = {
    "toxicity":           2,
    "relevance":          3,
    "hallucination":      4,
    "fluency":            1,
    "composite_safety":   1,
    "composite_quality":  1,
    "composite_final":    1,
}


def run_demo(out_dir="docs/diagrams"):
    adj = build_adjacency(DEMO_EDGES)
    nodes = DEMO_NODES

    cycles = detect_cycles(adj, nodes)
    if cycles:
        print(f"CYCLE DETECTED: {cycles}")
        sys.exit(1)
    print("✓ DAG is acyclic")

    topo = topological_sort(adj, nodes)
    print(f"Topological trigger order: {' → '.join(topo)}")

    levels = assign_levels(adj, nodes, topo)
    print(f"Levels: {levels}")

    cost, path = critical_path(adj, nodes, topo, DEMO_COSTS)
    print(f"Critical path (cost={cost}): {' → '.join(path)}")

    out_path = os.path.join(out_dir, "eval_dependency_dag")
    svg = render_dag_svg(nodes, adj, levels, (cost, path), {n: n for n in nodes}, out_path)
    if svg:
        print(f"SVG written to: {svg}")
    return svg


# ---------------------------------------------------------------------------
# Django mode — load real data
# ---------------------------------------------------------------------------

def run_django(org_id, root_id=None, out_dir="docs/diagrams"):
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
    django.setup()

    from model_hub.models.evals_metric import EvalTemplate, CompositeEvalChild

    qs = CompositeEvalChild.objects.filter(
        parent__organization_id=org_id,
        deleted=False,
    ).select_related("parent", "child")

    if root_id:
        qs = qs.filter(parent_id=root_id)

    edges = [(str(c.parent_id), str(c.child_id), c.weight) for c in qs]
    nodes = list({e[0] for e in edges} | {e[1] for e in edges})
    labels = {}
    for c in qs:
        labels[str(c.parent_id)] = c.parent.name
        labels[str(c.child_id)] = c.child.name

    adj = build_adjacency(edges)

    cycles = detect_cycles(adj, nodes)
    if cycles:
        print(f"⚠ CYCLE DETECTED in org {org_id}: {cycles}")

    topo = topological_sort(adj, nodes)
    levels = assign_levels(adj, nodes, topo)
    costs = {n: 1 for n in nodes}
    cp_cost, cp_path = critical_path(adj, nodes, topo, costs)

    out_path = os.path.join(out_dir, f"eval_dag_{org_id[:8]}")
    svg = render_dag_svg(nodes, adj, levels, (cp_cost, cp_path), labels, out_path)
    if svg:
        print(f"SVG: {svg}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--org", default=None)
    parser.add_argument("--root", default=None)
    parser.add_argument("--out", default="docs/diagrams")
    args = parser.parse_args()

    if args.demo:
        run_demo(args.out)
    elif args.org:
        run_django(args.org, args.root, args.out)
    else:
        parser.print_help()
