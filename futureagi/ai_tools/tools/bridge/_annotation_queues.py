"""Bridge registration for the annotation-queue cluster (Phase 2A Packet E).

AnnotationQueueViewSet / QueueItemViewSet / AutomationRuleViewSet live in
model_hub/views/annotation_queues.py. The base CRUD registrations for
QueueItemViewSet + AutomationRuleViewSet (and the assign_items action) live in
_misc_viewsets.py — per A10 this module ADDS actions to those ViewSets without
editing that file (the registry only collides on same-name/different-class).

Same-name HW conversions (legacy modules deleted in the same change):
  add_queue_items          -> QueueItemViewSet.add_items
  get_queue_progress       -> AnnotationQueueViewSet.progress
  submit_queue_annotations -> QueueItemViewSet.submit_annotations

Phase 3A (confirmation-gated, registered at the end of this module):
  AnnotationQueueViewSet.hard_delete, QueueItemViewSet.bulk_remove

Skipped (UI discussion endpoints): QueueItemViewSet.discussion,
discussion_comment, resolve/reopen_discussion_thread,
discussion_comment_reaction.

3B-lite check (review tools): review_item and bulk_review enforce the
reviewer/manager guard IN-METHOD via _has_queue_role(queue_id, user, REVIEWER,
MANAGER) (annotation_queues.py:6712/:6868), which runs on the bridge path —
not via permission_classes (which the bridge bypasses). Safe to expose.

Org-only scoping flags for the Phase 3B cross-tenant sweep: every QueueItem
action filters by request.organization (not workspace) — tag:
complete_queue_item, skip_queue_item, get_next_queue_item, review_queue_item,
bulk_review_queue_items, import_queue_annotations, release_queue_item_reservation,
list_queue_item_annotations, get_queue_item_annotate_detail.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.annotation_queues import (
    AnnotationQueueViewSet,
    AutomationRuleViewSet,
    QueueItemViewSet,
)

expose_to_mcp(category="annotation_queues")(AnnotationQueueViewSet)

# --- AnnotationQueueViewSet custom @actions (queue-level) -------------------
expose_to_mcp(
    category="annotation_queues",
    tools={
        # get_queue_progress: same-name conversion of the legacy HW tool —
        # input field stays `queue_id`.
        "progress": {
            "name": "get_queue_progress",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Get progress stats for an annotation queue: total/pending/"
                "in-progress/in-review/completed/skipped counts, completion "
                "percentage, and per-annotator stats (assigned + actual "
                "annotation counts)."
            ),
        },
        "restore": {
            "name": "restore_annotation_queue",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Restore an archived (soft-deleted) annotation queue, "
                "including its rules, items, and history."
            ),
        },
        "update_status": {
            "name": "update_annotation_queue_status",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Change an annotation queue's status ('active', 'paused', "
                "'completed', 'archived'). Only valid transitions are allowed "
                "and only queue managers may call this."
            ),
        },
        "export_annotations": {
            "name": "export_queue_annotations",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Export every item in an annotation queue with its "
                "annotations, review state, notes, and eval metrics. "
                "Optional `status` filter (e.g. 'completed')."
            ),
        },
        "analytics": {
            "name": "get_queue_analytics",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Queue analytics: status breakdown, 30-day daily throughput, "
                "annotator performance, and label distribution for an "
                "annotation queue."
            ),
        },
        "export_fields": {
            "name": "get_queue_export_fields",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "List the source/label/attribute fields available when "
                "exporting an annotation queue to a dataset, plus the default "
                "column mapping. Call before export_queue_to_dataset to build "
                "a column_mapping."
            ),
        },
        "export_to_dataset": {
            "name": "export_queue_to_dataset",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Export annotation-queue items into a dataset (existing "
                "dataset_id or new dataset_name). Optional status_filter "
                "(default 'completed') and column_mapping — a list of "
                "{field, column, enabled} objects; get valid field ids from "
                "get_queue_export_fields (omit to use the default mapping)."
            ),
        },
        "agreement": {
            "name": "get_queue_agreement",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Inter-annotator agreement metrics for an annotation queue "
                "(requires the agreement-metrics entitlement)."
            ),
        },
        "get_or_create_default": {
            "name": "get_or_create_default_queue",
            "entity": "annotation_queue",
            "description": (
                "Get (or create/restore) the default annotation queue for a "
                "project, dataset, or agent definition. Pass exactly one of "
                "project_id, dataset_id, or agent_definition_id. Default "
                "queues are open to all org members."
            ),
        },
        "add_label": {
            "name": "add_queue_label",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": (
                "Add an annotation label to a queue (label_id from "
                "list_annotation_labels). `required` (default true) marks the "
                "label as mandatory and may reopen completed items missing it."
            ),
        },
        "remove_label": {
            "name": "remove_queue_label",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "description": "Remove a label (label_id) from an annotation queue.",
        },
        "for_source": {
            "name": "get_queue_for_source",
            "entity": "annotation_queue",
            "description": (
                "Find annotation queues containing a given source that the "
                "current user can annotate, with labels and existing scores. "
                "Pass source_type + source_id (or `sources` as a JSON array "
                "of {source_type, source_id} objects)."
            ),
            "query_params": {
                "source_type": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Source kind: 'trace', 'observation_span', "
                        "'trace_session', 'dataset_row', 'prototype_run', or "
                        "'call_execution'. Required unless `sources` is used."
                    ),
                },
                "source_id": {
                    "type": str,
                    "required": False,
                    "description": "ID of the source object (pairs with source_type).",
                },
                "sources": {
                    "type": str,
                    "required": False,
                    "description": (
                        "JSON-encoded array of {source_type, source_id} "
                        "objects for multi-source lookup (instead of "
                        "source_type/source_id)."
                    ),
                },
            },
        },
    },
)(AnnotationQueueViewSet)

# --- QueueItemViewSet: annotator loop + queue-item tail ---------------------
# All item-level detail actions take TWO URL kwargs:
#   pk_field "item_id"   -> standard pk routing (handler signature pk=)
#   path_kwargs queue_id -> A5 extra path kwarg (handler signature queue_id=)
_QUEUE_ID_KWARG = {
    "queue_id": {
        "description": "UUID of the annotation queue the item belongs to.",
        "id_source": "list_annotation_queues",
    }
}

expose_to_mcp(
    category="annotation_queues",
    tools={
        # add_queue_items: same-name conversion of the legacy HW tool.
        "add_items": {
            "name": "add_queue_items",
            "entity": "queue_item",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Add items to an annotation queue (queue-manager only). "
                "`items` is a list of {source_type, source_id} objects "
                "(source_type: trace / observation_span / trace_session / "
                "dataset_row / prototype_run / call_execution). Duplicates "
                "are skipped. (Filter-mode `selection` payloads are a UI "
                "feature — enumerate items explicitly here.)"
            ),
        },
        # submit_queue_annotations: same-name conversion of the legacy HW tool.
        "submit_annotations": {
            "name": "submit_queue_annotations",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Submit or update annotations for a queue item — REQUIRED "
                "before complete_queue_item. `annotations` is a list of "
                "{label_id, value} objects. Get each label_id from "
                "get_queue_item_annotate_detail and use the label's `label_id` "
                "field (the underlying label id), NOT the queue-label row "
                "`id`. The value type must match the label type: number, text, "
                "categorical choice(s), star 1-5, or thumbs up/down. Optional "
                "notes/item_notes. The queue must be active. (If none of the "
                "submitted ids match a queue label the call is rejected with "
                "the list of valid label_id values — resubmit with one of "
                "those.)"
            ),
        },
        "complete_item": {
            "name": "complete_queue_item",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Mark a queue item as completed and get the next pending "
                "item back. PREREQUISITE: you MUST first submit this item's "
                "annotations with submit_queue_annotations — completing an "
                "item you have not annotated is rejected ('You must submit "
                "annotations before completing'). The correct sequence is "
                "get_next_queue_item -> get_queue_item_annotate_detail (read "
                "the labels) -> submit_queue_annotations -> complete_queue_item. "
                "If you have NOT submitted yet, do NOT call this tool and do "
                "NOT ask the user for a value — call submit_queue_annotations "
                "FIRST with a concrete value YOU choose for each required label "
                "(use the `label_id` from get_queue_item_annotate_detail; a "
                "short note for a text label, a valid choice for a categorical "
                "one), then call complete_queue_item in the same turn. "
                "Items in queues that require review move to pending_review "
                "instead of completed. Optional exclude_review_status / "
                "include_completed tune which item comes back next."
            ),
        },
        "skip_item": {
            "name": "skip_queue_item",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Mark a queue item as skipped and get the next pending item "
                "back. Only queue members can skip; completed or "
                "pending-review items cannot be skipped."
            ),
        },
        "next_item": {
            "name": "get_next_queue_item",
            "entity": "queue_item",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Get the next item to annotate in a queue (respects "
                "assignment, reservation, review-status, and rework scoping "
                "for the current user). Returns null when nothing is left. "
                "This is the annotator-loop entry point: get_next_queue_item "
                "-> get_queue_item_annotate_detail -> "
                "submit_queue_annotations -> complete_queue_item."
            ),
            "query_params": {
                "exclude": {
                    "type": str,
                    "required": False,
                    "description": "Comma-separated item UUIDs to skip over.",
                },
                "before": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Item UUID — return the item immediately BEFORE this "
                        "one in queue order instead of the next pending item."
                    ),
                },
                "review_status": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Reviewer queues: only items with this review status "
                        "(e.g. 'pending_review')."
                    ),
                },
                "exclude_review_status": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Annotator queues: omit items with this review status."
                    ),
                },
                "include_completed": {
                    "type": bool,
                    "required": False,
                    "description": "When true, navigation can visit completed items.",
                },
            },
        },
        "annotate_detail": {
            "name": "get_queue_item_annotate_detail",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Full annotation-workspace data for a queue item: source "
                "content, queue labels (with types/settings), existing "
                "annotations, review threads, and progress. Set reserve=true "
                "to reserve the item for the current user while annotating."
            ),
        },
        "release_reservation": {
            "name": "release_queue_item_reservation",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Release the current user's reservation on a queue item so "
                "other annotators can pick it up."
            ),
        },
        "annotations_list": {
            "name": "list_queue_item_annotations",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "List all submitted annotations (scores) for a queue item, "
                "across all annotators."
            ),
        },
        "import_annotations": {
            "name": "import_queue_annotations",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Import externally-produced annotations onto a queue item. "
                "`annotations` is a list of {label_id, value, notes?, "
                "score_source?} objects; optional annotator_id attributes "
                "them to another workspace member (defaults to you)."
            ),
        },
        # Reviewer loop — reviewer/manager guard runs in-method (3B-lite OK).
        "review_item": {
            "name": "review_queue_item",
            "entity": "queue_item",
            "pk_field": "item_id",
            "id_source": "list_queue_items",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Review a pending-review queue item as a reviewer/manager: "
                "action='approve' completes it, 'request_changes'/'reject' "
                "sends it back (notes required), 'comment' leaves feedback. "
                "label_comments is an optional list of {label_id, comment, "
                "target_annotator_id} objects for per-label feedback. You "
                "cannot review your own annotations."
            ),
        },
        "bulk_review": {
            "name": "bulk_review_queue_items",
            "entity": "queue_item",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Approve or send back MANY pending-review items at once "
                "(reviewer/manager only). item_ids is a list of queue-item "
                "UUIDs; action is 'approve' or 'request_changes'/'reject' "
                "(notes required when requesting changes). Returns per-item "
                "errors for items that could not be reviewed."
            ),
        },
    },
)(QueueItemViewSet)

# --- AutomationRuleViewSet custom @actions ----------------------------------
expose_to_mcp(
    category="annotation_queues",
    tools={
        "evaluate": {
            "name": "evaluate_automation_rule",
            "entity": "automation_rule",
            "pk_field": "rule_id",
            "id_source": "list_automation_rules",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Manually run an annotation-queue automation rule now "
                "(queue-manager only). Small runs execute synchronously and "
                "return the result; large runs are handed to a background "
                "workflow (202) that emails on completion. Refused if the "
                "rule fired in the last 30 seconds or the queue is archived."
            ),
        },
        "preview": {
            "name": "preview_automation_rule",
            "entity": "automation_rule",
            "pk_field": "rule_id",
            "id_source": "list_automation_rules",
            "path_kwargs": _QUEUE_ID_KWARG,
            "description": (
                "Dry-run an annotation-queue automation rule: how many items "
                "WOULD be added, without adding them (queue-manager only)."
            ),
        },
    },
)(AutomationRuleViewSet)


# ---------------------------------------------------------------------------
# Phase 3A — destructive @actions (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py). execution_policy pinned explicitly for grep.
# ---------------------------------------------------------------------------


def _preview_hard_delete_annotation_queue(params: dict, context) -> str:
    from model_hub.models.annotation_queues import AnnotationQueue, QueueItem

    queue_id = params.get("queue_id")
    queue = (
        AnnotationQueue.all_objects.filter(
            pk=queue_id, organization=context.organization
        )
        .only("id", "name", "deleted")
        .first()
    )
    if queue is None:
        return (
            f"Annotation queue `{queue_id}` was not found in this "
            "organization — nothing will be deleted."
        )
    item_count = QueueItem.all_objects.filter(queue_id=queue.pk).count()
    state = "archived" if queue.deleted else "ACTIVE (not archived)"
    return (
        f"Will PERMANENTLY hard-delete annotation queue **'{queue.name}'** "
        f"(`{str(queue.pk)[:8]}…`, currently {state}) and everything attached "
        f"— **{item_count} item(s)**, rules, assignments and scores cascade-"
        "delete with it.\n\n"
        "The API additionally requires force=true and confirm_name set to "
        f"the queue's exact name ('{queue.name}').\n\n"
        "This cannot be undone (unlike the soft `delete_annotation_queue`, "
        "which can be reversed with `restore_annotation_queue`)."
    )


def _preview_bulk_remove_queue_items(params: dict, context) -> str:
    from model_hub.models.annotation_queues import AnnotationQueue, QueueItem

    queue_id = params.get("queue_id")
    item_ids = params.get("item_ids") or []
    queue = (
        AnnotationQueue.objects.filter(pk=queue_id, organization=context.organization)
        .only("id", "name")
        .first()
    )
    queue_label = f"'{queue.name}'" if queue else f"`{queue_id}` (not found)"
    matching = QueueItem.objects.filter(
        id__in=item_ids,
        queue_id=queue_id,
        organization=context.organization,
        deleted=False,
    ).count()
    return (
        f"Will remove **{matching} item(s)** (of {len(item_ids)} requested) "
        f"from annotation queue {queue_label}. Removed items are soft-"
        "deleted together with their annotations and review state.\n\n"
        "Undo: re-add the removed sources with `add_queue_items` (existing "
        "annotations on the removed items are not restored)."
    )


expose_to_mcp(
    category="annotation_queues",
    tools={
        # hard_delete: serializer auto-resolves from @validated_request
        # (QueueHardDeleteRequestSerializer: force + confirm_name) — the
        # view ALSO enforces force=true + exact-name match server-side.
        "hard_delete": {
            "name": "hard_delete_annotation_queue",
            "entity": "annotation_queue",
            "pk_field": "queue_id",
            "id_source": "list_annotation_queues",
            "execution_policy": "destructive",
            "confirm_preview": _preview_hard_delete_annotation_queue,
            "description": (
                "PERMANENTLY hard-delete an annotation queue and everything "
                "attached (items, rules, assignments, scores). No recovery — "
                "for reversible archiving use delete_annotation_queue + "
                "restore_annotation_queue instead. Requires force=true and "
                "confirm_name equal to the queue's exact name, plus user "
                "confirmation (preview first, then re-call with "
                "confirm=true)."
            ),
        },
    },
)(AnnotationQueueViewSet)

expose_to_mcp(
    category="annotation_queues",
    tools={
        # bulk_remove(request, queue_id=None) — detail=False with the queue
        # id as a URL kwarg -> path_kwargs; item_ids from
        # BulkRemoveItemsSerializer (auto-resolved).
        "bulk_remove": {
            "name": "bulk_remove_queue_items",
            "entity": "queue_item",
            "path_kwargs": {
                "queue_id": {
                    "description": "UUID of the annotation queue.",
                    "id_source": "list_annotation_queues",
                },
            },
            "execution_policy": "destructive",
            "confirm_preview": _preview_bulk_remove_queue_items,
            "undo_note": (
                "Undo: re-add the removed sources to the queue with "
                "`add_queue_items` (annotations on removed items are not "
                "restored)."
            ),
            "undo_prompt": (
                "Undo the bulk removal from annotation queue {queue_id}: "
                "re-add the removed items (previous item ids {item_ids}) "
                "using add_queue_items with their original sources."
            ),
            "description": (
                "Bulk-remove items from an annotation queue (soft-delete of "
                "the queue items and their annotations). DESTRUCTIVE: "
                "requires user confirmation (preview first, then re-call "
                "with confirm=true)."
            ),
        },
    },
)(QueueItemViewSet)
