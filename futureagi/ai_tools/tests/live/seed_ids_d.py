"""Packet D seed ids for ai_tools/tests/verify_bridges.py.

SEED_IDS maps tool name -> bare id (passed as the tool's pk_field) or a
full params dict (pk + path_kwargs), consumed for DETAIL-GET tools whose
ids can't be harvested via ``binding.id_source`` or sibling-list pairing.

Packet D ships NO detail-GET bridges that need seeding: every new GET is
a detail=False @action keyed by explicit query params (project_id /
trace_id / eval_task_id ...), and the two id-keyed widget actions
(execute_widget_query / duplicate_dashboard_widget) are detail POSTs,
which the read sweep deliberately skips — they're covered by
live/writes_d.py instead.

NOTE for the harness owner (Packet A): the read sweep calls non-detail
GET tools with ``{}``, so Packet D's required-query-param GETs (e.g.
get_voice_call_detail, get_trace_eval_names, list_span_attribute_keys)
will report ERR-on-empty-args rather than real data. Extending SEED_IDS
lookup to list-style tools (full params dicts) would close that gap —
the entries below are shaped so they'd work as-is if/when that lands.
"""

from __future__ import annotations

SEED_IDS: dict = {}


def _build() -> None:
    """Pre-shape full-params seeds for the harness's future list-seed
    support. Harvested from the live DB at import time (post django.setup),
    scoped to the harness user's org/default workspace (rows from other
    tenants would 404 through the bridge); silently skipped when the
    account has no data."""
    from django.db.models import Q

    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project
    from tracer.models.trace import Trace

    user = (
        User.objects.select_related("organization")
        .filter(email="kartik.nvj@futureagi.com")
        .first()
    )
    if user is None:
        return
    org = user.organization
    ws = (
        Workspace.objects.filter(
            organization=org, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=org).first()
    )

    project = (
        Project.no_workspace_objects.filter(
            Q(workspace=ws) | Q(workspace__isnull=True),
            organization=org,
            deleted=False,
            trace_type="observe",
        )
        .order_by("-created_at")
        .first()
    )
    if project is not None:
        pid = str(project.id)
        SEED_IDS.setdefault("get_trace_eval_names", {"project_id": pid})
        SEED_IDS.setdefault("list_span_attribute_keys", {"project_id": pid})
        SEED_IDS.setdefault("get_project_graph_data", {"project_id": pid})
        SEED_IDS.setdefault("get_agent_graph", {"project_id": pid})
        SEED_IDS.setdefault("list_voice_calls", {"project_id": pid})
        SEED_IDS.setdefault(
            "get_span_attributes_list", {"filters": {"project_id": pid}}
        )
        SEED_IDS.setdefault(
            "get_span_eval_attributes", {"filters": {"project_id": pid}}
        )

    trace = (
        Trace.objects.filter(
            Q(project__workspace=ws) | Q(project__workspace__isnull=True),
            project__organization=org,
            deleted=False,
        )
        .order_by("-created_at")
        .first()
    )
    if trace is not None:
        SEED_IDS.setdefault("list_root_spans", {"trace_ids": [str(trace.id)]})


try:
    _build()
except Exception as _e:  # never break the verify_bridges run
    print(f"[seed_ids_d] seed construction failed: {_e}")
