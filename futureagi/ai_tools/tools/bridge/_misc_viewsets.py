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
