import math
from collections.abc import Callable, Iterable
from typing import Any

try:
    # Optional, used if available (OpenAI models / cl100k_base etc.)
    import tiktoken  # type: ignore
except Exception:  # pragma: no cover
    tiktoken = None  # Fallback to heuristic

try:
    # Prefer existing project utility if available
    try:
        from ee.usage.utils.usage_entries import count_text_tokens as _count_text_tokens
    except ImportError:
        _count_text_tokens = None
except Exception:  # pragma: no cover
    _count_text_tokens = None


def estimate_tokens_text(text: Any, model_name: str | None = None) -> int:
    """Estimate token count for a given text.

    - Uses tiktoken if available (best effort if model_name provided), otherwise
      a simple heuristic ~ 4 chars/token.
    - Accepts non-string and converts via str().
    """
    if text is None:
        return 0
    s = text if isinstance(text, str) else str(text)
    if not s:
        return 0

    # Prefer project-wide counter if available
    if _count_text_tokens is not None:
        try:
            return int(_count_text_tokens(s))
        except Exception:
            pass

    # Try tiktoken when available (fallback)
    if tiktoken is not None:  # pragma: no cover
        try:
            enc = None
            if model_name:
                try:
                    enc = tiktoken.encoding_for_model(model_name)
                except Exception:
                    pass
            if enc is None:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(s))
        except Exception:
            pass

    # Fallback heuristic: ~4 chars/token plus small newline overhead
    # Commonly used rough conversion for English-like text
    length = len(s)
    nl_bonus = s.count("\n") // 2
    return int(math.ceil(length / 4.0)) + nl_bonus


def estimate_tokens_lines(lines: Iterable[str], model_name: str | None = None) -> int:
    """Estimate tokens for multiple lines by summing per-line estimates."""
    total = 0
    for line in lines:
        total += estimate_tokens_text(line, model_name=model_name)
    return total


def estimate_outline_line_tokens(path: str, span_name: str, span_id: str, model_name: str | None = None) -> int:
    """Estimate tokens for one outline line in our summary format."""
    line = f"{path}  {span_name}  [Span ID: {span_id}]"
    return estimate_tokens_text(line, model_name=model_name)


def estimate_span_detail_tokens(
    span_id: str,
    span_name: str,
    span_type: str,
    span_input: Any,
    span_output: Any,
    include_input: bool = True,
    include_output: bool = True,
    head_lines: int = 2,
    tail_lines: int = 2,
    model_name: str | None = None,
) -> int:
    """Estimate tokens for one span detail block in our summary format.

    We count the static headers plus selected head/tail lines from input/output.
    """
    static_headers = [
        f"Span ID: {span_id}",
        f"Span Name: {span_name}",
        f"Span Type: {span_type}",
    ]
    total = estimate_tokens_lines(static_headers, model_name)

    def _select_lines(val: Any) -> list[str]:
        if val is None:
            return []
        s = val if isinstance(val, str) else str(val)
        if not s:
            return []
        lines = s.splitlines() or [s]
        if len(lines) <= head_lines + tail_lines:
            return lines
        head = lines[:head_lines]
        tail = lines[-tail_lines:] if tail_lines > 0 else []
        return head + tail

    if include_input:
        in_lines = _select_lines(span_input)
        if in_lines:
            total += estimate_tokens_text("Input:", model_name)
            total += estimate_tokens_lines(in_lines, model_name)

    if include_output:
        out_lines = _select_lines(span_output)
        if out_lines:
            total += estimate_tokens_text("Output:", model_name)
            total += estimate_tokens_lines(out_lines, model_name)

    # Separator lines (approximate)
    total += estimate_tokens_text("=" * 50, model_name)
    return total


def compute_subtree_costs(
    roots: list[Any],
    get_children: Callable[[Any], Iterable[Any]],
    node_cost: Callable[[Any], int],
) -> tuple[dict[Any, int], dict[Any, int]]:
    """Compute per-node and per-subtree costs.

    Returns a tuple: (node_cost_map, subtree_cost_map).
    Keys are node objects themselves (must be hashable) or use ids outside if needed.
    """
    node_cost_map: dict[Any, int] = {}
    subtree_cost_map: dict[Any, int] = {}

    def dfs(node: Any) -> int:
        c = node_cost_map.get(node)
        if c is None:
            c = node_cost(node)
            node_cost_map[node] = c
        total = c
        for child in get_children(node) or []:
            total += dfs(child)
        subtree_cost_map[node] = total
        return total

    for r in roots:
        dfs(r)

    return node_cost_map, subtree_cost_map


def max_depth_fit(
    root: Any,
    get_children: Callable[[Any], Iterable[Any]],
    node_cost: Callable[[Any], int],
    depth_budget_tokens: int,
) -> int:
    """Find the maximum depth k such that the total tokens for all nodes with depth ≤ k fit the budget.

    This ignores outline/evidence reserves; compute those separately and pass the working budget here.
    """
    # BFS by depth accumulating cost
    from collections import deque

    q = deque([(root, 0)])
    total = 0
    max_k = -1
    current_depth = 0
    layer_nodes: list[Any] = []

    while q:
        node, d = q.popleft()
        if d != current_depth:
            # finalize previous layer
            layer_cost = sum(node_cost(n) for n in layer_nodes)
            if total + layer_cost <= depth_budget_tokens:
                total += layer_cost
                max_k = current_depth
                layer_nodes = [node]
                current_depth = d
            else:
                break
        else:
            layer_nodes.append(node)

        for c in get_children(node) or []:
            q.append((c, d + 1))

    # Try to include the last accumulated layer if possible
    if layer_nodes and current_depth == max_k + 1:
        layer_cost = sum(node_cost(n) for n in layer_nodes)
        if total + layer_cost <= depth_budget_tokens:
            max_k = current_depth

    return max_k


class BudgetedSubtreeSplitter:
    """Depth-based budget splitter for span trees.

    Works on trees built as nodes: {"span": span, "children": [nodes...]}
    Returns batches as lists of included span_ids.
    """

    def __init__(
        self,
        token_budget: int,
        model_name: str | None = None,
        head_lines: int = 2,
        tail_lines: int = 2,
        reserve_outline: int = 0,
        reserve_evidence: int = 0,
    ) -> None:
        self.token_budget = max(0, int(token_budget))
        self.model_name = model_name
        self.head_lines = head_lines
        self.tail_lines = tail_lines
        self.reserve_outline = max(0, int(reserve_outline))
        self.reserve_evidence = max(0, int(reserve_evidence))

    # ---- Public API ----
    def split(self, root_nodes: list[dict[str, Any]]) -> list[list[str]]:
        if self.token_budget <= 0 or not root_nodes:
            # No budget given; one batch with all spans
            return [self._collect_all_span_ids(root_nodes)]

        # Precompute per-node costs and subtree costs (keyed by node id)
        node_cost: dict[int, int] = {}
        subtree_cost: dict[int, int] = {}
        self._compute_costs(root_nodes, node_cost, subtree_cost)

        working_budget = max(0, self.token_budget - self.reserve_outline - self.reserve_evidence)
        total_cost = sum(subtree_cost.get(self._k(r), 0) for r in root_nodes)
        if total_cost <= working_budget:
            return [self._collect_all_span_ids(root_nodes)]

        # Depth-based batching for each root, concatenated
        batches: list[list[str]] = []
        for r in root_nodes:
            batches.extend(self._split_node(r, working_budget, node_cost, subtree_cost))
        return batches

    # ---- Internals ----
    def _split_node(
        self,
        node: dict[str, Any],
        budget: int,
        node_cost: dict[int, int],
        subtree_cost: dict[int, int],
    ) -> list[list[str]]:
        # If whole subtree fits, return one batch with all span_ids in subtree
        if subtree_cost.get(self._k(node), 0) <= budget:
            return [self._collect_all_span_ids([node])]

        # Choose largest depth k s.t. nodes with depth <= k fit in budget
        layers: list[list[dict[str, Any]]] = self._layers(node)
        included: list[dict[str, Any]] = []
        used = 0
        k = -1
        for depth, layer_nodes in enumerate(layers):
            layer_cost = sum(node_cost.get(self._k(n), 0) for n in layer_nodes)
            if used + layer_cost <= budget:
                included.extend(layer_nodes)
                used += layer_cost
                k = depth
            else:
                break

        # If nothing fits at this node, fall back to a minimal batch with just this node
        if k < 0:
            return [[self._span_id(node)]]

        batches: list[list[str]] = []
        if included:
            batches.append([self._span_id(n) for n in included])

        # Frontier: nodes at depth k+1
        frontier: list[dict[str, Any]] = []
        if k + 1 < len(layers):
            frontier = layers[k + 1]

        # Pack multiple frontier subtrees per batch up to budget
        group_ids: list[str] = []
        group_cost = 0
        for child in frontier:
            ch_cost = subtree_cost.get(self._k(child), 0)
            if ch_cost <= budget:
                if group_cost + ch_cost <= budget:
                    # add child's subtree to current group
                    group_ids.extend(self._collect_all_span_ids([child]))
                    group_cost += ch_cost
                else:
                    # flush current group and start a new one with this child
                    if group_ids:
                        batches.append(group_ids)
                    group_ids = self._collect_all_span_ids([child])
                    group_cost = ch_cost
            else:
                # Oversized subtree: flush current group, then recurse
                if group_ids:
                    batches.append(group_ids)
                    group_ids = []
                    group_cost = 0
                child_batches = self._split_node(child, budget, node_cost, subtree_cost)
                batches.extend(child_batches)

        if group_ids:
            batches.append(group_ids)

        return batches

    def _compute_costs(
        self,
        roots: list[dict[str, Any]],
        node_cost: dict[int, int],
        subtree_cost: dict[int, int],
    ) -> None:
        def cost(n: dict[str, Any]) -> int:
            key = self._k(n)
            c = node_cost.get(key)
            if c is not None:
                return c
            span = n.get("span")
            span_id = str(getattr(span, "id", ""))
            span_name = str(getattr(span, "name", "") or "")
            span_type = str(getattr(span, "observation_type", "") or "")
            span_input = getattr(span, "input", "") or ""
            span_output = getattr(span, "output", "") or ""
            c = estimate_span_detail_tokens(
                span_id=span_id,
                span_name=span_name,
                span_type=span_type,
                span_input=span_input,
                span_output=span_output,
                head_lines=self.head_lines,
                tail_lines=self.tail_lines,
                model_name=self.model_name,
            )
            node_cost[key] = c
            return c

        def dfs(n: dict[str, Any]) -> int:
            total = cost(n)
            for ch in n.get("children", []) or []:
                total += dfs(ch)
            subtree_cost[self._k(n)] = total
            return total

        for r in roots:
            dfs(r)

    def _layers(self, root: dict[str, Any]) -> list[list[dict[str, Any]]]:
        from collections import deque
        q = deque([(root, 0)])
        layers: list[list[dict[str, Any]]] = []
        while q:
            n, d = q.popleft()
            if d >= len(layers):
                layers.append([])
            layers[d].append(n)
            for ch in n.get("children", []) or []:
                q.append((ch, d + 1))
        return layers

    def _collect_nodes(self, node: dict[str, Any], out: list[dict[str, Any]]) -> None:
        out.append(node)
        for ch in node.get("children", []) or []:
            self._collect_nodes(ch, out)

    def _span_id(self, node: dict[str, Any]) -> str:
        return str(getattr(node.get("span"), "id", ""))

    def _collect_all_span_ids(self, roots: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        def walk(n: dict[str, Any]):
            ids.append(self._span_id(n))
            for ch in n.get("children", []) or []:
                walk(ch)
        for r in roots:
            walk(r)
        return ids

    def _k(self, node: dict[str, Any]) -> int:
        # Use object identity as key (node dicts are unhashable)
        return id(node)


