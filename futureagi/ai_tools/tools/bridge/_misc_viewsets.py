"""Bridge registration for remaining ModelViewSets across multiple apps.

Covers dashboards, scores, optimisation, secrets, observability,
shared links, saved views, tts voices, api keys, tools, and feedback.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.annotation_queues import (
    AutomationRuleViewSet,
    QueueItemViewSet,
)
from model_hub.views.dataset_optimization import DatasetOptimizationViewSet
from model_hub.views.develop_dataset import FeedbackViewSet
from model_hub.views.run_prompt import ApiKeyViewSet
from model_hub.views.scores import ScoreViewSet
from model_hub.views.secrets import SecretViewSet
from model_hub.views.tools import ToolsViewSet
from model_hub.views.tts_voices import TTSVoiceViewSet
from simulate.views.agent_prompt_optimiser import AgentPromptOptimiserRunViewSet
from tracer.views.custom_eval_config import CustomEvalConfigView
from tracer.views.dashboard import DashboardViewSet, DashboardWidgetViewSet
from tracer.views.observability_provider import ObservabilityProviderViewSet
from tracer.views.saved_view import SavedViewViewSet
from tracer.views.shared_link import SharedLinkViewSet

# Tracer
expose_to_mcp(category="tracing")(DashboardViewSet)
expose_to_mcp(category="tracing")(DashboardWidgetViewSet)
expose_to_mcp(category="tracing")(ObservabilityProviderViewSet)
expose_to_mcp(category="tracing")(SharedLinkViewSet)
expose_to_mcp(category="tracing")(SavedViewViewSet)
expose_to_mcp(
    category="evaluations",
    tools={
        "list": {"name": "list_custom_eval_configs"},
        "retrieve": {"name": "get_custom_eval_config"},
        "create": {"name": "create_custom_eval_config"},
        # PATCH (partial_update) is the maintained write path that runs the
        # optional-key mapping normalization; the view has no PUT handler, so the
        # old "update" fell through to DRF's full-update (required every field)
        # and was uncallable for a simple {id, mapping} edit (TH-5442).
        "partial_update": {
            "name": "update_custom_eval_config",
            "include_fields": ["mapping"],
            "description": (
                "Set/update the variable mapping for an eval attached to a "
                "trace project (the Observe -> Evaluations 'map variables' "
                "step). This is how Falcon performs eval-task variable mapping "
                "end to end:\n"
                "1. Identify the eval config: list_custom_eval_configs "
                "(filter by project_id / name) -> note its id and eval_template "
                "id.\n"
                "2. Read the required keys: get_eval_template with that "
                "eval_template id -> its required_keys (and config.optional_keys) "
                "list the mapping KEYS. The mapping keys MUST be exactly these "
                "required_keys (e.g. ['conversation', 'context']) — do NOT "
                "invent generic keys like input/output unless those are the "
                "template's actual required_keys.\n"
                "3. Read the available span attribute paths for the project: "
                "get_span_eval_attributes with "
                'filters={"project_id": "<project id>"} -> these are the valid '
                "mapping VALUES. If that returns an empty list (e.g. no spans "
                "ingested yet), fall back to get_span_attributes_list / "
                "get_observation_span_fields, or map each required key to the "
                "most semantically appropriate standard span field "
                "(input.value, output.value, etc.) — still produce a mapping.\n"
                "4. Call this tool with the eval config id and "
                'mapping={"<required_key>": "<attribute path>", ...} that has '
                "one entry for EVERY required key. Only the mapping field is "
                "changed; the eval is now runnable. Do not ask the user to "
                "provide the mapping values yourself when you can read them from "
                "the steps above."
            ),
        },
        "destroy": {"name": "delete_custom_eval_config"},
    },
)(CustomEvalConfigView)

# Model hub
expose_to_mcp(category="annotation_queues")(QueueItemViewSet)
# assign_queue_items -> QueueItemViewSet.assign_items (POST .../queues/<queue_id>
# /items/assign): assign queue items to one or more annotators — the action the
# UI uses, which Falcon couldn't do (TH-5576). Detail-style on the queue id;
# body carries item_ids + user_ids + action (add/set/remove).
expose_to_mcp(
    category="annotation_queues",
    tools={
        "assign_items": {
            "name": "assign_queue_items",
            "method": "POST",
            "detail": True,
            "pk_field": "queue_id",
            "pk_kwarg": "queue_id",
            "entity": "queue",
            "description": (
                "Assign annotation-queue items to one or more annotators (the "
                "queue's 'assign' action). Provide queue_id, item_ids (UUIDs of "
                "the queue items — get them from list_queue_items), and user_ids "
                "(annotators to assign). action: 'add' (default) adds users, "
                "'set' replaces all assignments, 'remove' removes the given "
                "users. Requires queue-manager permission."
            ),
            "query_params": {
                "item_ids": {
                    "type": list,
                    "required": True,
                    "description": "List of queue item UUIDs to assign.",
                },
                "user_ids": {
                    "type": list,
                    "required": False,
                    "description": "List of annotator (user) UUIDs to assign.",
                },
                "action": {
                    "type": str,
                    "required": False,
                    "description": "'add' (default), 'set', or 'remove'.",
                },
            },
        }
    },
)(QueueItemViewSet)
expose_to_mcp(category="annotation_queues")(AutomationRuleViewSet)
expose_to_mcp(category="datasets")(FeedbackViewSet)
expose_to_mcp(category="datasets")(DatasetOptimizationViewSet)
expose_to_mcp(category="users")(ApiKeyViewSet)
expose_to_mcp(category="datasets")(SecretViewSet)
expose_to_mcp(category="prompts")(ToolsViewSet)
expose_to_mcp(category="simulation")(TTSVoiceViewSet)
# ScoreViewSet is the canonical DRF API the Annotations UI uses for the unified
# Score model (GET /model-hub/scores/?source_type=trace&source_id=<uuid>).
# Expose the list action's real source filters so `list_scores` can return the
# scores for a specific trace/span/etc. — this replaces the hand-written
# `list_trace_scores` tool (TH-5405): one API, one source of truth.
expose_to_mcp(
    category="evaluations",
    tools={
        "list": {
            "description": (
                "List human/annotation scores for a source, from the unified "
                "Score model the Annotations UI uses. For a trace's scores pass "
                "source_type='trace' and source_id=<trace_id>; for a span use "
                "source_type='observation_span'. NOTE: automated EVALUATION "
                "scores (faithfulness, instruction adherence, privacy & safety, "
                "optimal-plan execution, overall) are stored separately — use "
                "get_trace_error_analysis for those."
            ),
            "query_params": {
                "source_type": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Source kind, paired with source_id: 'trace', "
                        "'observation_span', 'trace_session', 'call_execution', "
                        "'dataset_row', or 'prototype_run'."
                    ),
                },
                "source_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "UUID of the source (e.g. the trace id when "
                        "source_type='trace')."
                    ),
                },
                "label_id": {
                    "type": str,
                    "required": False,
                    "description": "Optional annotation-label UUID filter.",
                },
                "annotator_id": {
                    "type": str,
                    "required": False,
                    "description": "Optional annotator (user) UUID filter.",
                },
            },
        },
        "retrieve": {},
        "create": {},
        "update": {},
        "destroy": {},
    },
)(ScoreViewSet)

# Simulation
expose_to_mcp(category="optimization")(AgentPromptOptimiserRunViewSet)


# ---------------------------------------------------------------------------
# Phase 3A — destructive @actions (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py). execution_policy pinned explicitly for grep.
# ---------------------------------------------------------------------------


def _preview_remove_shared_link_access(params: dict, context) -> str:
    from tracer.models.shared_link import SharedLink, SharedLinkAccess

    link_id = params.get("link_id")
    access_id = params.get("access_id")
    link = (
        SharedLink.objects.filter(pk=link_id)
        .only("id", "resource_type", "resource_id")
        .first()
    )
    entry = (
        SharedLinkAccess.objects.filter(pk=access_id, shared_link_id=link_id)
        .only("id", "email")
        .first()
    )
    if link is None or entry is None:
        return (
            f"Shared link `{link_id}` / access entry `{access_id}` was not "
            "found in this workspace — nothing will be removed."
        )
    return (
        f"Will revoke **{entry.email}**'s access to the shared "
        f"{link.resource_type} link (`{str(link.pk)[:8]}…`).\n\n"
        "Undo: re-grant access by re-sharing the link with that email "
        "(Share dialog or the shared-link create tool)."
    )


# remove_shared_link_access -> SharedLinkViewSet.remove_access (DELETE,
# detail=True, extra `access_id` URL kwarg -> path_kwargs).
expose_to_mcp(
    category="tracing",
    tools={
        "remove_access": {
            "name": "remove_shared_link_access",
            "entity": "shared_link",
            "pk_field": "link_id",
            "id_source": "list_shared_links",
            "path_kwargs": {
                "access_id": {
                    "description": (
                        "UUID of the ACL entry to remove (from the shared "
                        "link's access_list in `get_shared_link`)."
                    ),
                },
            },
            "query_params": {},
            "execution_policy": "destructive",
            "confirm_preview": _preview_remove_shared_link_access,
            "undo_note": (
                "Undo: re-grant access by re-sharing the link with the "
                "affected email."
            ),
            "description": (
                "Remove an email from a shared link's access list (ACL). "
                "DESTRUCTIVE: requires user confirmation (preview first, "
                "then re-call with confirm=true)."
            ),
        },
    },
)(SharedLinkViewSet)
