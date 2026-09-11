import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
try:
    _project_root = Path(__file__).resolve().parents[3]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))
except Exception:
    pass

# Django settings
os.environ["DJANGO_SETTINGS_MODULE"] = os.environ.get(
    "DJANGO_SETTINGS_MODULE", "tfc.settings.settings"
)
import django  # type: ignore

django.setup()

from ee.agenthub.traceerroragent.token_utils import (
    BudgetedSubtreeSplitter,
    estimate_outline_line_tokens,
    estimate_span_detail_tokens,
)
from tracer.models.observation_span import ObservationSpan  # type: ignore
from tracer.models.trace import Trace  # type: ignore


def build_tree_for_trace(trace_id: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    spans = list(
        ObservationSpan.objects.filter(trace_id=trace_id).order_by("start_time", "created_at")
    )
    id_to_node: dict[str, dict[str, Any]] = {}
    for s in spans:
        id_to_node[str(s.id)] = {"span": s, "children": []}

    roots: list[dict[str, Any]] = []
    for s in spans:
        sid = str(s.id)
        if s.parent_span_id and str(s.parent_span_id) in id_to_node:
            id_to_node[str(s.parent_span_id)]["children"].append(id_to_node[sid])
        else:
            roots.append(id_to_node[sid])

    # Sort children by time
    def sort_children(n: dict[str, Any]):
        try:
            n["children"].sort(
                key=lambda c: (
                    getattr(c["span"], "start_time", None)
                    or getattr(c["span"], "created_at", None)
                )
            )
        except Exception:
            pass
        for ch in n["children"]:
            sort_children(ch)

    for r in roots:
        sort_children(r)

    return roots, id_to_node


def compute_paths(roots: list[dict[str, Any]]) -> dict[str, str]:
    path_map: dict[str, str] = {}

    def walk(n: dict[str, Any], path: list[int]):
        sid = str(getattr(n["span"], "id", ""))
        path_map[sid] = ".".join(str(p) for p in path)
        for i, ch in enumerate(n.get("children", []) or [], start=1):
            walk(ch, path + [i])

    for i, r in enumerate(roots, start=1):
        walk(r, [i])
    return path_map


def estimate_full_tokens(roots: list[dict[str, Any]]) -> int:
    total = 0

    def walk(n: dict[str, Any], path: list[int]):
        nonlocal total
        s = n["span"]
        sid = str(getattr(s, "id", ""))
        sname = getattr(s, "name", "") or ""
        stype = getattr(s, "observation_type", "") or ""
        sin = getattr(s, "input", "") or ""
        sout = getattr(s, "output", "") or ""
        path_str = ".".join(str(p) for p in path)
        total += estimate_outline_line_tokens(path_str, sname, sid)
        total += estimate_span_detail_tokens(
            span_id=sid,
            span_name=sname,
            span_type=stype,
            span_input=sin,
            span_output=sout,
            head_lines=2,
            tail_lines=2,
        )
        for i, ch in enumerate(n.get("children", []) or [], start=1):
            walk(ch, path + [i])

    for i, r in enumerate(roots, start=1):
        walk(r, [i])
    return total


def main():
    project_id = "69d76a61-07e8-4cbe-8696-76dc4c8eac3f"
    trace_id = None
    # trace_id = "911e853f-02d0-3e97-6dbf-0c16f653ab57"
    token_budget = 10000

    if not trace_id:
        if not project_id:
            raise RuntimeError("Set TRACE_ID or PROJECT_ID in env")
        # pick the latest trace for the project
        t = (
            Trace.objects.filter(project_id=project_id)
            .order_by("-created_at")
            .values_list("id", flat=True)
            .first()
        )
        if not t:
            raise RuntimeError("No traces found for project")
        trace_id = str(t)

    roots, id_to_node = build_tree_for_trace(trace_id)
    path_map = compute_paths(roots)
    total_tokens = estimate_full_tokens(roots)

    splitter = BudgetedSubtreeSplitter(token_budget=token_budget)
    batches = splitter.split(roots)

    print(json.dumps({
        "trace_id": trace_id,
        "token_budget": token_budget,
        "estimated_full_tokens": total_tokens,
        "num_spans": len(id_to_node),
        "num_batches": len(batches),
    }, indent=2))

    for i, batch in enumerate(batches, start=1):
        print(f"\n=== BATCH {i}/{len(batches)} ===")
        print(f"spans_in_batch={len(batch)}")
        batch_tokens = 0
        for sid in batch:
            node = id_to_node.get(sid)
            if not node:
                continue
            s = node["span"]
            sname = getattr(s, "name", "") or ""
            path = path_map.get(sid, "?")
            print(f"{path}  {sname}  [Span ID: {sid}]")
            stype = getattr(s, "observation_type", "") or ""
            sin = getattr(s, "input", "") or ""
            sout = getattr(s, "output", "") or ""
            batch_tokens += estimate_outline_line_tokens(path, sname, sid)
            batch_tokens += estimate_span_detail_tokens(
                span_id=sid,
                span_name=sname,
                span_type=stype,
                span_input=sin,
                span_output=sout,
                head_lines=2,
                tail_lines=2,
            )
        print(f"estimated_batch_tokens={batch_tokens}")


if __name__ == "__main__":
    main()


