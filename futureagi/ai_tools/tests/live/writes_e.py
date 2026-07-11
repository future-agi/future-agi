# ruff: noqa: E402
"""Packet E write round-trips (annotation-queue annotator loop + evaluations).

Loaded by ``ai_tools/tests/verify_writes.py`` (generalized ROUNDTRIPS dicts:
setup -> tool call -> fresh-shell ORM assert -> compensate, net-zero).

Staging strategy: ``args`` are static, so the annotator-loop entries run
against a deterministic, idempotently (re)staged fixture — a dedicated
"packet-e-writecheck" annotation queue with stable uuid5 ids. Each entry's
``setup`` callable get_or_creates the fixture and normalizes the row state it
needs; each ``compensate`` restores it. The fixture queue itself persists
between runs (like the USER_EMAIL seed account) but every run is net-zero on
item/score/label state.

Deliberately NOT round-tripped here (documented gaps, exercised elsewhere):
- review_queue_item / bulk_review_queue_items — need a second annotator's
  scores ("cannot review your own annotations") + requires_review staging;
  covered by the staged D1 sweep, guard verified in-method (3B-lite ok).
- export_queue_to_dataset, get_or_create_default_queue — create datasets/
  default queues keyed to project ids; compensation needs project staging.
- restore_annotation_queue — requires archiving the fixture queue mid-run.
- ground-truth writes (update_*_mapping, delete, trigger_embedding) — org has
  no EvalGroundTruth rows; upload path is the documented multipart gap.
- get_trace_evals / get_eval_playground / execute_composite_eval(_adhoc),
  evaluate_automation_rule — run real LLM evals / workflows (token cost).
- submit_eval_playground_feedback — needs a fresh playground log id (dynamic).
- stop_optimization_run — needs a RUNNING run; cancelling real work is not
  net-zero.
"""

import uuid

_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "packet-e-writecheck.futureagi")
QUEUE_ID = str(uuid.uuid5(_NS, "queue"))
LABEL_ID = str(uuid.uuid5(_NS, "label-text"))
LABEL2_ID = str(uuid.uuid5(_NS, "label-text-2"))
ITEM_COMPLETE_ID = str(uuid.uuid5(_NS, "item-complete"))
ITEM_SKIP_ID = str(uuid.uuid5(_NS, "item-skip"))
ITEM_ANNOTATE_ID = str(uuid.uuid5(_NS, "item-annotate"))
# Harvested ws1 seeds (org of verify_writes.USER_EMAIL, 2026-06-10).
# add_items and the annotate/submit flows DEREFERENCE the source trace, so the
# fixture must point at a REAL trace in the caller's org (synthetic uuids made
# add_items silently skip and submit_annotations 500 on Trace.DoesNotExist).
ADD_SOURCE_ID = "f7d6570c-1223-4345-9343-c3c9750fb66d"
VERSIONED_TEMPLATE_ID = "ff36cf02-77e9-4dae-bea9-577e839c79c9"
RULE_QUEUE_ID = "35817658-0caa-40a9-b217-cf99b11dc449"
RULE_ID = "11345ebe-2737-4a85-9642-5a84c3ce11f0"

_STATE: dict = {}
_FIXTURE_ITEM_IDS = (ITEM_COMPLETE_ID, ITEM_SKIP_ID, ITEM_ANNOTATE_ID)


def _real_trace_id(ctx) -> str:
    """A real org trace for ITEM_ANNOTATE (the submit/annotate serializers
    dereference the trace). Must differ from ADD_SOURCE_ID — the queue has a
    unique (queue, trace) constraint and add_queue_items targets that id."""
    if "trace_id" not in _STATE:
        from tracer.models.trace import Trace

        tr = (
            Trace.objects.filter(project__organization=ctx.organization)
            .exclude(id=ADD_SOURCE_ID)
            .order_by("-created_at")
            .first()
        )
        _STATE["trace_id"] = str(tr.id) if tr else str(uuid.uuid5(_NS, "no-trace"))
    return _STATE["trace_id"]


def _models():
    from model_hub.models.annotation_queues import (
        AnnotationQueue,
        AnnotationQueueAnnotator,
        AnnotationQueueLabel,
        QueueItem,
    )
    from model_hub.models.develop_annotations import AnnotationsLabels
    from model_hub.models.score import Score

    return (
        AnnotationQueue,
        AnnotationQueueAnnotator,
        AnnotationQueueLabel,
        QueueItem,
        AnnotationsLabels,
        Score,
    )


def _stage_queue(ctx):
    """Idempotently (re)create the writecheck queue, labels, role, items."""
    (
        AnnotationQueue,
        AnnotationQueueAnnotator,
        AnnotationQueueLabel,
        QueueItem,
        AnnotationsLabels,
        Score,
    ) = _models()

    queue, _ = AnnotationQueue.objects.update_or_create(
        id=QUEUE_ID,
        defaults={
            "name": "packet-e-writecheck-queue",
            "status": "active",
            "requires_review": False,
            "annotations_required": 1,
            "auto_assign": True,
            "organization": ctx.organization,
            "workspace": ctx.workspace,
            "deleted": False,
        },
    )
    AnnotationQueueAnnotator.objects.update_or_create(
        queue=queue,
        user=ctx.user,
        deleted=False,
        defaults={"role": "manager", "roles": ["manager", "annotator", "reviewer"]},
    )
    for label_id, name in ((LABEL_ID, "packet-e-writecheck-text"),):
        label, _ = AnnotationsLabels.objects.update_or_create(
            id=label_id,
            defaults={
                "name": name,
                "type": "text",
                "organization": ctx.organization,
                "workspace": ctx.workspace,
                "deleted": False,
            },
        )
        AnnotationQueueLabel.objects.update_or_create(
            queue=queue,
            label=label,
            defaults={"required": True, "deleted": False},
        )
    # Second label exists but is NOT attached (add_queue_label target).
    AnnotationsLabels.objects.update_or_create(
        id=LABEL2_ID,
        defaults={
            "name": "packet-e-writecheck-text-2",
            "type": "text",
            "organization": ctx.organization,
            "workspace": ctx.workspace,
            "deleted": False,
        },
    )
    # complete/skip never dereference the trace — synthetic per-item ids keep
    # the unique (queue, trace) constraint happy; only ITEM_ANNOTATE (whose
    # flows serialize the source) gets a real org trace.
    for item_id in _FIXTURE_ITEM_IDS:
        trace_id = (
            _real_trace_id(ctx)
            if item_id == ITEM_ANNOTATE_ID
            else str(uuid.uuid5(_NS, f"trace-{item_id}"))
        )
        QueueItem.objects.update_or_create(
            id=item_id,
            defaults={
                "queue": queue,
                "source_type": "trace",
                "trace_id": trace_id,
                "status": "pending",
                "review_status": None,
                "reserved_by": None,
                "reserved_at": None,
                "reservation_expires_at": None,
                "organization": ctx.organization,
                "workspace": ctx.workspace,
                "deleted": False,
            },
        )
    return queue


def _stage_complete(ctx):
    """complete_item requires the caller to have already annotated the item."""
    _stage_queue(ctx)
    *_, QueueItem, _labels, Score = _models()
    item = QueueItem.objects.get(id=ITEM_COMPLETE_ID)
    Score.objects.update_or_create(
        queue_item=item,
        label_id=LABEL_ID,
        annotator=ctx.user,
        deleted=False,
        defaults={
            "source_type": "trace",
            "trace_id": item.trace_id,
            "value": "packet-e-writecheck",
            "score_source": "human",
            "organization": ctx.organization,
            "workspace": ctx.workspace,
        },
    )


def _reset_item(item_id):
    def _compensate(ctx, result):
        *_, QueueItem, _labels, Score = _models()
        QueueItem.objects.filter(id=item_id).update(
            status="pending", review_status=None, reserved_by=None
        )
        # complete-item staging score is removed so each run starts clean.
        Score.objects.filter(queue_item_id=item_id).delete()

    return _compensate


def _assert_item_status(item_id, expected):
    def _assert(ctx, result):
        *_, QueueItem, _labels, _score = _models()
        return QueueItem.objects.get(id=item_id).status == expected

    return _assert


# --- per-entry callables (module-level so the harness can import/repr them) --


def _assert_added_item(ctx, result):
    *_, QueueItem, _labels, _score = _models()
    return (
        QueueItem.objects.filter(
            queue_id=QUEUE_ID, trace_id=ADD_SOURCE_ID, deleted=False
        )
        .exclude(id__in=_FIXTURE_ITEM_IDS)
        .exists()
    )


def _compensate_added_item(ctx, result):
    *_, QueueItem, _labels, _score = _models()
    QueueItem.objects.filter(queue_id=QUEUE_ID, trace_id=ADD_SOURCE_ID).exclude(
        id__in=_FIXTURE_ITEM_IDS
    ).delete()


def _assert_submitted_scores(ctx, result):
    *_, _qi, _labels, Score = _models()
    return Score.objects.filter(
        queue_item_id=ITEM_ANNOTATE_ID, annotator=ctx.user, deleted=False
    ).exists()


def _compensate_submitted_scores(ctx, result):
    *_, QueueItem, _labels, Score = _models()
    Score.objects.filter(queue_item_id=ITEM_ANNOTATE_ID).delete()
    QueueItem.objects.filter(id=ITEM_ANNOTATE_ID).update(
        status="pending", review_status=None, reserved_by=None
    )


def _assert_label2_attached(ctx, result):
    from model_hub.models.annotation_queues import AnnotationQueueLabel

    return AnnotationQueueLabel.objects.filter(
        queue_id=QUEUE_ID, label_id=LABEL2_ID, deleted=False
    ).exists()


def _detach_label2(ctx, result=None):
    from model_hub.models.annotation_queues import AnnotationQueueLabel

    AnnotationQueueLabel.objects.filter(queue_id=QUEUE_ID, label_id=LABEL2_ID).delete()


def _stage_label2_attached(ctx):
    from model_hub.models.annotation_queues import AnnotationQueueLabel

    _stage_queue(ctx)
    AnnotationQueueLabel.objects.update_or_create(
        queue_id=QUEUE_ID,
        label_id=LABEL2_ID,
        defaults={"required": False, "deleted": False},
    )


def _assert_label2_detached(ctx, result):
    from model_hub.models.annotation_queues import AnnotationQueueLabel

    return not AnnotationQueueLabel.objects.filter(
        queue_id=QUEUE_ID, label_id=LABEL2_ID, deleted=False
    ).exists()


def _assert_queue_paused(ctx, result):
    from model_hub.models.annotation_queues import AnnotationQueue

    return AnnotationQueue.objects.get(id=QUEUE_ID).status == "paused"


def _stage_version_count(ctx):
    from model_hub.models.evals_metric import EvalTemplateVersion

    _STATE["version_ids"] = set(
        EvalTemplateVersion.objects.filter(
            eval_template_id=VERSIONED_TEMPLATE_ID
        ).values_list("id", flat=True)
    )


def _assert_version_created(ctx, result):
    from model_hub.models.evals_metric import EvalTemplateVersion

    now_ids = set(
        EvalTemplateVersion.objects.filter(
            eval_template_id=VERSIONED_TEMPLATE_ID
        ).values_list("id", flat=True)
    )
    _STATE["new_version_ids"] = now_ids - _STATE.get("version_ids", set())
    return len(_STATE["new_version_ids"]) == 1


def _compensate_version(ctx, result):
    from model_hub.models.evals_metric import EvalTemplateVersion

    new_ids = _STATE.get("new_version_ids") or set()
    if new_ids:
        EvalTemplateVersion.objects.filter(id__in=new_ids).delete()


ROUNDTRIPS = [
    # --- annotator loop (the Packet E headline) ------------------------------
    {
        "tool": "add_queue_items",
        "args": {
            "queue_id": QUEUE_ID,
            "items": [{"source_type": "trace", "source_id": ADD_SOURCE_ID}],
        },
        "setup": _stage_queue,
        "assert_orm": _assert_added_item,
        "compensate": _compensate_added_item,
    },
    {
        "tool": "submit_queue_annotations",
        "args": {
            "queue_id": QUEUE_ID,
            "item_id": ITEM_ANNOTATE_ID,
            "annotations": [{"label_id": LABEL_ID, "value": "packet-e-writecheck"}],
        },
        "setup": _stage_queue,
        "assert_orm": _assert_submitted_scores,
        "compensate": _compensate_submitted_scores,
    },
    {
        "tool": "complete_queue_item",
        "args": {"queue_id": QUEUE_ID, "item_id": ITEM_COMPLETE_ID},
        "setup": _stage_complete,
        "assert_orm": _assert_item_status(ITEM_COMPLETE_ID, "completed"),
        "compensate": _reset_item(ITEM_COMPLETE_ID),
    },
    {
        "tool": "skip_queue_item",
        "args": {"queue_id": QUEUE_ID, "item_id": ITEM_SKIP_ID},
        "setup": _stage_queue,
        "assert_orm": _assert_item_status(ITEM_SKIP_ID, "skipped"),
        "compensate": _reset_item(ITEM_SKIP_ID),
    },
    # --- queue label management ---------------------------------------------
    {
        "tool": "add_queue_label",
        "args": {"queue_id": QUEUE_ID, "label_id": LABEL2_ID, "required": False},
        "setup": _stage_queue,
        "assert_orm": _assert_label2_attached,
        "compensate": _detach_label2,
    },
    {
        "tool": "remove_queue_label",
        "args": {"queue_id": QUEUE_ID, "label_id": LABEL2_ID},
        "setup": _stage_label2_attached,
        "assert_orm": _assert_label2_detached,
        "compensate": _detach_label2,  # idempotent cleanup
    },
    # --- queue lifecycle ------------------------------------------------------
    {
        "tool": "update_annotation_queue_status",
        "args": {"queue_id": QUEUE_ID, "status": "paused"},
        "setup": _stage_queue,
        "assert_orm": _assert_queue_paused,
        # paused -> active is a valid transition; net-zero via the same tool.
        "compensate": (
            "update_annotation_queue_status",
            {"queue_id": QUEUE_ID, "status": "active"},
        ),
    },
    # --- automation rules (dry-run; no side effect, no compensate needed) ----
    {
        "tool": "preview_automation_rule",
        "args": {"queue_id": RULE_QUEUE_ID, "rule_id": RULE_ID},
    },
    # --- eval template version control ----------------------------------------
    {
        "tool": "create_eval_template_version",
        "args": {"eval_template_id": VERSIONED_TEMPLATE_ID},
        "setup": _stage_version_count,
        "assert_orm": _assert_version_created,
        "compensate": _compensate_version,
    },
]
