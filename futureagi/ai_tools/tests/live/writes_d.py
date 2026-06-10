# ruff: noqa: E402
"""Packet D write round-trips for ai_tools/tests/verify_writes.py.

Generalized-dict entries (setup -> call -> fresh-shell ORM assert ->
compensate, PHASES.md:117). This module is imported by verify_writes.py
AFTER django.setup() inside the live container, so ids are harvested from
the live DB at import time; entries whose fixtures don't exist in the
target account are omitted with a printed [writes_d] note instead of
shipping an entry that can only CALL-ERR.

Not covered here (documented gaps, exercised manually):
- pause_eval_task / unpause_eval_task: need an EvalTask currently in
  RUNNING / PAUSED state — not constructible without kicking off a real
  eval run; verified manually in the live sweep.
- submit_span_feedback: needs a span with a logged EvalLogger result for
  a given eval config; data-dependent, exercised in the L1 live suite.
- submit_bulk_annotations / add_span_annotations: need an annotation
  label in the workspace; the add_span_annotations entry below is only
  emitted when one exists.
"""

from __future__ import annotations

ROUNDTRIPS: list = []


def _build() -> None:
    from django.db.models import Q

    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace

    _MARK = "bridge-writecheck-d"

    # The harness context (verify_writes.USER_EMAIL — not imported to avoid
    # a circular import while verify_writes is itself importing this module).
    user = (
        User.objects.select_related("organization")
        .filter(email="kartik.nvj@futureagi.com")
        .first()
    )
    if user is None:
        print("[writes_d] harness user not found — all entries omitted")
        return
    org = user.organization
    ws = (
        Workspace.objects.filter(
            organization=org, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=org).first()
    )
    # Rows must be visible to the harness request (org + default-workspace
    # scoping in the views), otherwise the handlers 404.
    _scope = Q(project__organization=org) & (
        Q(project__workspace=ws) | Q(project__workspace__isnull=True)
    )

    # ------------------------------------------------------------------
    # update_trace_tags — replace tags, assert ORM, restore original.
    # ------------------------------------------------------------------
    trace = (
        Trace.objects.filter(_scope, deleted=False).order_by("-created_at").first()
    )
    if trace is not None:
        _trace_id = str(trace.id)
        _orig_tags = list(trace.tags or [])

        def _assert_trace_tags(ctx, result, _tid=_trace_id):
            t = Trace.objects.get(id=_tid)
            return _MARK in (t.tags or [])

        def _restore_trace_tags(ctx, result, _tid=_trace_id, _tags=_orig_tags):
            t = Trace.objects.get(id=_tid)
            t.tags = _tags
            t.save(update_fields=["tags", "updated_at"])

        ROUNDTRIPS.append(
            {
                "tool": "update_trace_tags",
                "args": {"trace_id": _trace_id, "tags": _orig_tags + [_MARK]},
                "assert_orm": _assert_trace_tags,
                "compensate": _restore_trace_tags,
            }
        )
    else:
        print("[writes_d] no Trace rows — update_trace_tags entry omitted")

    # ------------------------------------------------------------------
    # update_span_tags — same shape on an observation span.
    # ------------------------------------------------------------------
    span = (
        ObservationSpan.objects.filter(_scope, deleted=False)
        .order_by("-created_at")
        .first()
    )
    if span is not None:
        _span_id = str(span.id)
        _orig_span_tags = list(span.tags or [])

        def _assert_span_tags(ctx, result, _sid=_span_id):
            s = ObservationSpan.objects.get(id=_sid)
            return _MARK in (s.tags or [])

        def _restore_span_tags(ctx, result, _sid=_span_id, _tags=_orig_span_tags):
            s = ObservationSpan.objects.get(id=_sid)
            s.tags = _tags
            s.save(update_fields=["tags", "updated_at"])

        ROUNDTRIPS.append(
            {
                "tool": "update_span_tags",
                "args": {"span_id": _span_id, "tags": _orig_span_tags + [_MARK]},
                "assert_orm": _assert_span_tags,
                "compensate": _restore_span_tags,
            }
        )
    else:
        print("[writes_d] no ObservationSpan rows — update_span_tags entry omitted")

    # ------------------------------------------------------------------
    # add_span_annotations — only when an annotation label exists; writes
    # one value to the root span of the newest trace, compensates by
    # deleting the created Score rows for that (label, span) pair.
    # ------------------------------------------------------------------
    try:
        from model_hub.models.develop_annotations import AnnotationsLabels
    except Exception:
        AnnotationsLabels = None
    if AnnotationsLabels is not None and span is not None:
        label = (
            AnnotationsLabels.objects.filter(
                deleted=False, type="text", organization=org
            )
            .order_by("-created_at")
            .first()
        )
        if label is not None:
            _label_id = str(label.id)
            _span_id2 = str(span.id)

            def _assert_annotation(ctx, result, _sid=_span_id2, _lid=_label_id):
                from model_hub.models.score import Score

                # The handler upserts into the unified Score model keyed on
                # (observation_span, label, annotator, queue_item).
                return Score.no_workspace_objects.filter(
                    observation_span_id=_sid, label_id=_lid, deleted=False
                ).exists()

            def _delete_annotation(ctx, result, _sid=_span_id2, _lid=_label_id):
                from model_hub.models.score import Score

                Score.no_workspace_objects.filter(
                    observation_span_id=_sid, label_id=_lid, value=_MARK
                ).delete()

            ROUNDTRIPS.append(
                {
                    "tool": "add_span_annotations",
                    "args": {
                        "observation_span_id": _span_id2,
                        "annotation_values": {_label_id: _MARK},
                    },
                    "assert_orm": _assert_annotation,
                    "compensate": _delete_annotation,
                }
            )
        else:
            print("[writes_d] no text AnnotationsLabels — add_span_annotations omitted")

    # ------------------------------------------------------------------
    # reorder_dashboard_widgets — net-zero by construction: submit the
    # CURRENT order, assert positions unchanged.
    # ------------------------------------------------------------------
    try:
        from tracer.models.dashboard import Dashboard, DashboardWidget
    except Exception:
        Dashboard = DashboardWidget = None
    dashboard = None
    if Dashboard is not None:
        dashboard = (
            Dashboard.objects.filter(deleted=False, workspace=ws)
            .filter(widgets__deleted=False)
            .order_by("-created_at")
            .distinct()
            .first()
        )
    if dashboard is not None:
        _dash_id = str(dashboard.id)
        widgets = list(
            DashboardWidget.objects.filter(dashboard=dashboard, deleted=False).order_by(
                "position"
            )
        )
        _order = [str(w.id) for w in widgets]
        _positions = {str(w.id): w.position for w in widgets}

        def _assert_order(ctx, result, _dash=_dash_id, _ord=tuple(_order)):
            rows = DashboardWidget.objects.filter(
                dashboard_id=_dash, deleted=False
            ).order_by("position")
            return [str(w.id) for w in rows] == list(_ord)

        ROUNDTRIPS.append(
            {
                "tool": "reorder_dashboard_widgets",
                "args": {"dashboard_id": _dash_id, "order": _order},
                "assert_orm": _assert_order,
                # submitting the current order IS the compensation
            }
        )

        # --------------------------------------------------------------
        # duplicate_dashboard_widget — duplicates the first widget,
        # asserts the "(Copy)" row exists, deletes it afterwards.
        # --------------------------------------------------------------
        first_widget = widgets[0] if widgets else None
        if first_widget is not None:
            _widget_id = str(first_widget.id)
            _copy_name = f"{first_widget.name} (Copy)"

            def _assert_copy(ctx, result, _dash=_dash_id, _name=_copy_name):
                return DashboardWidget.objects.filter(
                    dashboard_id=_dash, name=_name, deleted=False
                ).exists()

            def _delete_copy(ctx, result, _dash=_dash_id, _name=_copy_name):
                DashboardWidget.objects.filter(
                    dashboard_id=_dash, name=_name
                ).delete()

            ROUNDTRIPS.append(
                {
                    "tool": "duplicate_dashboard_widget",
                    "args": {"widget_id": _widget_id, "dashboard_id": _dash_id},
                    "assert_orm": _assert_copy,
                    "compensate": _delete_copy,
                }
            )
    else:
        print("[writes_d] no Dashboard with widgets — dashboard write entries omitted")


try:
    _build()
except Exception as _e:  # never break the whole verify_writes run
    print(f"[writes_d] entry construction failed: {_e}")
