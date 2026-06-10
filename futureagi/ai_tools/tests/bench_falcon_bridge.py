# ruff: noqa: E402
"""Falcon AI performance benchmark for DRF bridge tools.

Two benches in one file:

1. LLM bench (default) — runs the TASKS below through Falcon's AgentLoop with
   REAL LLM calls. Measures:
     - tool selection accuracy (did Falcon pick the right tool?), per cluster
     - tool execution latency (how fast did the bridge tool run?)
     - end-to-end task latency (LLM + tool dispatch)
     - success rate (did the agent complete the task?)
     - tool call count per task (efficiency)

2. Selection bench (``--selection``) — deterministic, no LLM, no DB writes.
   Runs SELECTION_CASES against the two-tier discovery system and measures,
   per cluster:
     - search@1 / search@5  — expected tool's rank in ``search_tools`` results
     - active40             — expected tool inside the ~40-schema active set
                              (detect_mode → load_tools_for_mode →
                              filter_tools_for_message)
     - reachable            — active40 OR search@5 (the two-tier promise:
                              every tool is in-set or one search away)
   This is the measuring stick for any scoring/membership change in
   ``search_tools.py`` / ``modes.py`` — each change must be justified by a
   per-cluster delta here (improve-or-hold; PHASES.md Phase 2C).

Run via docker:
    docker exec ws1-backend python -m ai_tools.tests.bench_falcon_bridge
    docker exec ws1-backend python -m ai_tools.tests.bench_falcon_bridge --selection
"""

import asyncio
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field

import django

logging.disable(logging.CRITICAL)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()
logging.disable(logging.NOTSET)
logging.basicConfig(level=logging.WARNING)

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.models import Conversation

USER_EMAIL = "kartik.nvj@futureagi.com"

# 25 tasks mixing single-tool, multi-step, and complex multi-tool scenarios
TASKS = [
    # --- Level 1: Single bridge tool (10 tasks) ---
    {
        "id": 1,
        "msg": "List all my tracing projects",
        "expected_tools": ["list_trace_projects"],
    },
    {
        "id": 2,
        "msg": "Show me the projects I have",
        "expected_tools": ["list_trace_projects"],
    },
    {
        "id": 3,
        "msg": "How many projects do I have?",
        "expected_tools": ["list_trace_projects"],
    },
    {
        "id": 4,
        "msg": "List projects of type observe",
        "expected_tools": ["list_trace_projects"],
    },
    {
        "id": 5,
        "msg": "Show me only experiment projects",
        "expected_tools": ["list_trace_projects"],
    },
    {
        "id": 6,
        "msg": "Create a new project called 'bench-test-01' of type experiment with model_type 'llm'",
        "expected_tools": ["create_trace_project"],
    },
    {
        "id": 7,
        "msg": "Create a project named 'bench-test-02' for observing LLM responses",
        "expected_tools": ["create_trace_project"],
    },
    {
        "id": 8,
        # {nonce}: project names are unique per (name, type, org, ws) —
        # stale bench-test-* rows from earlier runs made this correctly
        # answerable with "already exists" (run-1 adjudication).
        "msg": "Make a new experiment project called 'bench-test-{nonce}'",
        "expected_tools": ["create_trace_project"],
    },
    {
        "id": 9,
        "msg": "Set up a project 'bench-test-04' for tracing",
        "expected_tools": ["create_trace_project"],
    },
    {
        "id": 10,
        "msg": "I want to add a new project 'bench-test-05' for experiments",
        "expected_tools": ["create_trace_project"],
    },
    # --- Level 2: Multi-step (10 tasks) ---
    {
        "id": 11,
        "msg": "List my projects, then get details of the first one",
        "expected_tools": ["list_trace_projects", "get_trace_project"],
    },
    {
        "id": 12,
        "msg": "Find my projects and show me details of the experiment ones",
        "expected_tools": ["list_trace_projects", "get_trace_project"],
    },
    {
        "id": 13,
        "msg": "Create a project 'bench-multi-01' and then show its details",
        "expected_tools": ["create_trace_project", "get_trace_project"],
    },
    {
        "id": 14,
        "msg": "Make a project called 'bench-multi-02' then list all projects to confirm",
        "expected_tools": ["create_trace_project", "list_trace_projects"],
    },
    {
        "id": 15,
        "msg": "Show me my projects, then rename the first one to 'renamed-bench-01'",
        "expected_tools": ["list_trace_projects", "rename_trace_project"],
    },
    {
        "id": 16,
        "msg": "Find the project called 'bench-test-01' and show me its config",
        "expected_tools": ["list_trace_projects", "get_trace_project"],
    },
    {
        "id": 17,
        "msg": "List projects of type experiment then get details of one",
        "expected_tools": ["list_trace_projects", "get_trace_project"],
    },
    {
        "id": 18,
        "msg": "Rename project bench-test-02 to 'bench-renamed-02'",
        "expected_tools": ["list_trace_projects", "rename_trace_project"],
    },
    {
        "id": 19,
        "msg": "Create project 'bench-multi-03' then update its name to 'final-bench-03'",
        "expected_tools": ["create_trace_project", "rename_trace_project"],
    },
    {
        "id": 20,
        "msg": "Show me my workspace info and then list my projects",
        "expected_tools": ["whoami", "list_trace_projects"],
    },
    # --- Level 3: Complex multi-step (5 tasks) ---
    {
        "id": 21,
        "msg": "List my projects, find the most recent one, and rename it to 'most-recent-renamed'",
        "expected_tools": ["list_trace_projects", "rename_trace_project"],
    },
    {
        "id": 22,
        "msg": "Create two projects: 'bench-complex-01' (experiment) and 'bench-complex-02' (observe)",
        "expected_tools": ["create_trace_project"],
    },
    {
        "id": 23,
        "msg": "List my experiment projects and tell me which ones are named with 'bench'",
        "expected_tools": ["list_trace_projects"],
    },
    {
        "id": 24,
        "msg": "Show me all my projects and pick one to update its sampling rate",
        "expected_tools": ["list_trace_projects", "rename_trace_project"],
    },
    {
        "id": 25,
        "msg": "Who am I? List my projects, and get details on one",
        "expected_tools": ["whoami", "list_trace_projects", "get_trace_project"],
    },
    # --- Prompt template bridge tools (read-only, no destructive ops) ---
    {
        "id": 26,
        "msg": "List all my prompt templates",
        "expected_tools": ["list_prompt_templates"],
    },
    {
        "id": 27,
        "msg": "Show me my prompt templates",
        "expected_tools": ["list_prompt_templates"],
    },
    {
        "id": 28,
        "msg": "Search for prompt templates with 'eval' in the name",
        "expected_tools": ["list_prompt_templates"],
    },
    {
        "id": 29,
        "msg": "How many prompt templates do I have?",
        "expected_tools": ["list_prompt_templates"],
    },
    {
        "id": 30,
        "msg": "List my prompt templates, then get details of the first one",
        "expected_tools": ["list_prompt_templates", "get_prompt_template"],
    },
    {
        "id": 31,
        "msg": "Find a prompt template called 'summarization' and show me its details",
        "expected_tools": ["list_prompt_templates", "get_prompt_template"],
    },
    {
        "id": 32,
        "msg": "Show me what variables my prompt templates use",
        "expected_tools": ["list_prompt_templates"],
    },
    # --- APIView bridge tools (read-only, accounts) ---
    {
        "id": 33,
        "msg": "List all my workspaces",
        "expected_tools": ["list_workspaces"],
    },
    {
        "id": 34,
        "msg": "Show me the workspaces in my organization",
        "expected_tools": ["list_workspaces"],
    },
    {
        "id": 35,
        "msg": "List the users in my org",
        "expected_tools": ["list_users"],
    },
    {
        "id": 36,
        "msg": "Show me everyone on my team",
        "expected_tools": ["list_users"],
        "accept_tools": ["list_workspace_members"],
        "adjudication": (
            "'my team' maps to the current workspace's member roster; "
            "list_workspace_members returns exactly that (names, emails, "
            "roles) — a semantically correct alternate to org-wide "
            "list_users (run-1 transcript: full 11-member roster table)."
        ),
    },
    {
        "id": 37,
        "msg": "Find workspaces matching 'falcon' in the name",
        "expected_tools": ["list_workspaces"],
    },
    # --- Phase 2A clusters (added in 2C so accuracy is MEASURED on them) ---
    # Experiments V2 actions. Fake ids → 404, no side effects; the metric is
    # tool SELECTION (tool_call_start), not execution success.
    {
        "id": 38,
        "msg": "Stop the running experiment with id 999999",
        "expected_tools": ["stop_experiment"],
        "cluster": "experiments_v2",
    },
    {
        "id": 39,
        # realistic-but-nonexistent uuid4: 999999 reads as a placeholder and
        # provoked validate-first list_experiments (run-1 adjudication); a
        # uuid4 404s with zero side effects.
        "msg": "Rerun the failed cells of experiment {missing_exp_a}",
        "expected_tools": ["rerun_experiment_cells"],
        "cluster": "experiments_v2",
    },
    {
        "id": 40,
        "msg": "Export my experiments to CSV",
        "expected_tools": ["export_experiments_csv"],
        "accept_tools": ["list_experiments"],
        "adjudication": (
            "export_experiments_csv is per-experiment (required "
            "experiment_id pk); with no id in the task, listing experiments "
            "and asking which to export is the only correct first step "
            "(run-1 transcript did exactly this)."
        ),
        "cluster": "experiments_v2",
    },
    {
        "id": 41,
        "msg": "Suggest a good name for a new experiment",
        "expected_tools": ["suggest_experiment_name"],
        "accept_tools": ["list_datasets"],
        "adjudication": (
            "suggest_experiment_name requires a dataset_id pk (names are "
            "DS_<dataset>_exp_...); with no dataset in the task, fetching "
            "datasets and asking which one is the only correct first step "
            "(run-1 transcript did exactly this)."
        ),
        "cluster": "experiments_v2",
    },
    {
        "id": 42,
        # real COMPLETED experiment ids (read-only comparison): integer
        # placeholders made the model correctly answer "those aren't real
        # ids — experiments use UUIDs" (run-1 adjudication).
        "msg": "Compare experiments {exp_a_id} and {exp_b_id} for me",
        "expected_tools": ["compare_experiments", "get_experiment_comparison"],
        "cluster": "experiments_v2",
    },
    # Annotator loop (annotation_queues) — the 2A packet E persona flow.
    {
        "id": 43,
        "msg": "Give me the next item to annotate from queue 00000000-0000-0000-0000-000000000000",
        "expected_tools": ["get_next_queue_item"],
        "cluster": "annotator_loop",
    },
    {
        # Queue-item actions require BOTH queue_id (path kwarg) and item_id:
        # the old zero-UUID item with no queue forced a clarify/list turn
        # (run-1 adjudication). Real queue + realistic-missing item: the
        # action tool is callable and 404s with zero side effects.
        "id": 44,
        "msg": (
            "Mark queue item {missing_item_id} in annotation queue "
            "{queue_id} as complete"
        ),
        "expected_tools": ["complete_queue_item"],
        "cluster": "annotator_loop",
    },
    {
        "id": 45,
        "msg": (
            "Skip item {missing_item_id} in annotation queue {queue_id}"
        ),
        "expected_tools": ["skip_queue_item"],
        "cluster": "annotator_loop",
    },
    {
        "id": 46,
        "msg": (
            "Review item {missing_item_id} in annotation queue {queue_id} "
            "and approve it"
        ),
        "expected_tools": ["review_queue_item", "bulk_review_queue_items"],
        "cluster": "annotator_loop",
    },
    {
        "id": 47,
        # real queue id — get_queue_progress is read-only.
        "msg": "How far along is annotation queue {queue_id}? Show its progress",
        "expected_tools": ["get_queue_progress"],
        "cluster": "annotator_loop",
    },
    {
        "id": 48,
        "msg": "Export the annotations of queue 00000000-0000-0000-0000-000000000000",
        "expected_tools": ["export_queue_annotations"],
        "cluster": "annotator_loop",
    },
    # Dashboards (query engine + widgets, landed in category 'tracing').
    {
        "id": 49,
        "msg": "List my observability dashboards",
        "expected_tools": ["list_dashboards"],
        "cluster": "dashboards",
    },
    {
        "id": 50,
        "msg": "Show me the widgets on dashboard 00000000-0000-0000-0000-000000000000",
        "expected_tools": ["list_dashboard_widgets", "get_dashboard"],
        "cluster": "dashboards",
    },
    {
        "id": 51,
        # duplicate needs widget_id + dashboard_id (path kwarg); zero-UUID
        # with no dashboard provoked a lookup spiral (run-1 adjudication).
        # Real dashboard + realistic-missing widget: callable, 404s.
        "msg": (
            "Duplicate widget {missing_widget_id} on dashboard "
            "{dashboard_id}"
        ),
        "expected_tools": ["duplicate_dashboard_widget"],
        "cluster": "dashboards",
    },
    {
        "id": 52,
        "msg": "What metrics can I plot on a dashboard?",
        "expected_tools": ["list_dashboard_metrics"],
        "cluster": "dashboards",
    },
    # Destructives — 3A confirmation layer: first call returns a preview +
    # CONFIRMATION_REQUIRED, zero side effects; cold confirm:true is also
    # preview-only. Selection is what we measure.
    {
        "id": 53,
        "msg": "Delete annotation queue 00000000-0000-0000-0000-000000000000",
        "expected_tools": ["delete_annotation_queue"],
        "cluster": "destructives_confirm",
    },
    {
        "id": 54,
        # realistic-but-nonexistent uuid4: the zero-UUID made the model
        # (correctly) call it out as a placeholder and look the queue up
        # first (run-1 adjudication). uuid4 404s before any confirm flow.
        "msg": (
            "Permanently delete annotation queue {missing_queue_id} — "
            "hard delete it"
        ),
        "expected_tools": ["hard_delete_annotation_queue"],
        "cluster": "destructives_confirm",
    },
    {
        "id": 55,
        "msg": "Restore the deleted annotation queue 00000000-0000-0000-0000-000000000000",
        "expected_tools": ["restore_annotation_queue"],
        "cluster": "destructives_confirm",
    },
    {
        "id": 56,
        "msg": "Delete dashboard 00000000-0000-0000-0000-000000000000",
        "expected_tools": ["delete_dashboard"],
        "cluster": "destructives_confirm",
    },
]

# Legacy tasks (ids 1-37) predate per-cluster reporting — label them.
for _t in TASKS:
    if "cluster" not in _t:
        _t["cluster"] = (
            "trace_projects"
            if _t["id"] <= 25
            else "prompts"
            if _t["id"] <= 32
            else "accounts"
        )


# ---------------------------------------------------------------------------
# Deterministic selection bench (no LLM): query → expected tool, per cluster.
# "expect" = any of these tools counts as a hit (mirrors TaskResult.tools_match).
# "page" simulates the frontend context page (PAGE_CONTEXT_MAP values) so mode
# detection / PAGE_TO_MODE membership is exercised; default "general".
# ---------------------------------------------------------------------------
SELECTION_CASES = [
    # --- trace_projects ---
    {"query": "list all my tracing projects", "expect": ["list_trace_projects"], "cluster": "trace_projects"},
    {"query": "create a new project for tracing", "expect": ["create_trace_project"], "cluster": "trace_projects"},
    {"query": "rename my trace project", "expect": ["rename_trace_project"], "cluster": "trace_projects"},
    {"query": "update project sampling rate", "expect": ["rename_trace_project"], "cluster": "trace_projects"},
    {"query": "how many projects do I have", "expect": ["list_trace_projects"], "cluster": "trace_projects"},
    # --- prompts ---
    {"query": "list my prompt templates", "expect": ["list_prompt_templates"], "cluster": "prompts"},
    {"query": "show the variables of my prompt template", "expect": ["get_prompt_template_variables"], "cluster": "prompts"},
    {"query": "improve my prompt", "expect": ["improve_prompt"], "cluster": "prompts"},
    {"query": "generate a prompt for summarization", "expect": ["generate_prompt"], "cluster": "prompts"},
    {"query": "analyze my prompt for issues", "expect": ["analyze_prompt"], "cluster": "prompts"},
    {"query": "compare two prompt versions", "expect": ["compare_prompt_versions"], "cluster": "prompts"},
    {"query": "commit a new prompt version", "expect": ["commit_prompt_version"], "cluster": "prompts"},
    {"query": "get the sdk code for my prompt", "expect": ["get_prompt_sdk_code"], "cluster": "prompts"},
    {"query": "move a prompt into a folder", "expect": ["move_prompt_to_folder"], "cluster": "prompts"},
    # --- accounts ---
    {"query": "list all my workspaces", "expect": ["list_workspaces"], "cluster": "accounts"},
    {"query": "show me everyone on my team", "expect": ["list_users", "list_workspace_members"], "cluster": "accounts"},
    {"query": "invite new users to my organization", "expect": ["invite_users"], "cluster": "accounts"},
    {"query": "create an api key", "expect": ["create_api_key"], "cluster": "accounts"},
    # --- experiments_v2 ---
    {"query": "stop a running experiment", "expect": ["stop_experiment"], "cluster": "experiments_v2", "page": "experiments"},
    {"query": "rerun the failed cells of an experiment", "expect": ["rerun_experiment_cells"], "cluster": "experiments_v2", "page": "experiments"},
    {"query": "export my experiments to csv", "expect": ["export_experiments_csv"], "cluster": "experiments_v2", "page": "experiments"},
    {"query": "suggest a name for my experiment", "expect": ["suggest_experiment_name"], "cluster": "experiments_v2", "page": "experiments"},
    {"query": "validate an experiment name", "expect": ["validate_experiment_name"], "cluster": "experiments_v2", "page": "experiments"},
    {"query": "show the row level diff between experiments", "expect": ["get_experiment_row_diff"], "cluster": "experiments_v2", "page": "experiments"},
    {"query": "compare two experiments", "expect": ["compare_experiments", "get_experiment_comparison"], "cluster": "experiments_v2", "page": "experiments"},
    {"query": "submit feedback on an experiment", "expect": ["submit_experiment_feedback", "create_experiment_feedback"], "cluster": "experiments_v2", "page": "experiments"},
    # --- annotator_loop (page = annotation_queues, as the frontend sends it) ---
    {"query": "get the next item to annotate", "expect": ["get_next_queue_item"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "complete the annotation item", "expect": ["complete_queue_item"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "skip this annotation item", "expect": ["skip_queue_item"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "review annotation items", "expect": ["review_queue_item", "bulk_review_queue_items"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "bulk review the queue items", "expect": ["bulk_review_queue_items"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "submit annotations for a queue item", "expect": ["submit_queue_annotations"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "annotation queue progress", "expect": ["get_queue_progress"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "export queue annotations", "expect": ["export_queue_annotations"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "import annotations into a queue", "expect": ["import_queue_annotations"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "inter annotator agreement for a queue", "expect": ["get_queue_agreement"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "release my reservation on a queue item", "expect": ["release_queue_item_reservation"], "cluster": "annotator_loop", "page": "annotation_queues"},
    {"query": "send annotated queue items to a dataset", "expect": ["export_queue_to_dataset"], "cluster": "annotator_loop", "page": "annotation_queues"},
    # --- dashboards (category 'tracing') ---
    {"query": "list my dashboards", "expect": ["list_dashboards"], "cluster": "dashboards", "page": "tracing"},
    {"query": "create a dashboard", "expect": ["create_dashboard"], "cluster": "dashboards", "page": "tracing"},
    {"query": "add a widget to my dashboard", "expect": ["create_dashboard_widget"], "cluster": "dashboards", "page": "tracing"},
    {"query": "duplicate a dashboard widget", "expect": ["duplicate_dashboard_widget"], "cluster": "dashboards", "page": "tracing"},
    {"query": "reorder the widgets on my dashboard", "expect": ["reorder_dashboard_widgets"], "cluster": "dashboards", "page": "tracing"},
    {"query": "preview a widget query", "expect": ["preview_widget_query"], "cluster": "dashboards", "page": "tracing"},
    {"query": "run a dashboard query", "expect": ["execute_dashboard_query", "execute_widget_query"], "cluster": "dashboards", "page": "tracing"},
    {"query": "what metrics can dashboards plot", "expect": ["list_dashboard_metrics"], "cluster": "dashboards", "page": "tracing"},
    # --- destructives_confirm ---
    {"query": "delete an annotation queue", "expect": ["delete_annotation_queue"], "cluster": "destructives_confirm", "page": "annotation_queues"},
    {"query": "permanently delete an annotation queue", "expect": ["hard_delete_annotation_queue", "delete_annotation_queue"], "cluster": "destructives_confirm", "page": "annotation_queues"},
    {"query": "restore a deleted annotation queue", "expect": ["restore_annotation_queue"], "cluster": "destructives_confirm", "page": "annotation_queues"},
    {"query": "delete a dashboard", "expect": ["delete_dashboard"], "cluster": "destructives_confirm", "page": "tracing"},
    {"query": "remove many items from an annotation queue at once", "expect": ["bulk_remove_queue_items"], "cluster": "destructives_confirm", "page": "annotation_queues"},
    {"query": "delete an experiment", "expect": ["delete_experiment"], "cluster": "destructives_confirm", "page": "experiments"},
    # --- simulation (2A packet C) ---
    {"query": "add rows to a scenario", "expect": ["add_scenario_rows"], "cluster": "simulation", "page": "agents"},
    {"query": "duplicate a persona", "expect": ["duplicate_persona"], "cluster": "simulation", "page": "agents"},
    {"query": "restore an old agent version", "expect": ["restore_agent_version"], "cluster": "simulation", "page": "agents"},
    {"query": "get the kpis for a run test", "expect": ["get_run_test_kpis"], "cluster": "simulation", "page": "agents"},
    {"query": "rerun a test execution", "expect": ["rerun_test_execution"], "cluster": "simulation", "page": "agents"},
    {"query": "get the call transcript", "expect": ["get_call_transcript"], "cluster": "simulation", "page": "agents"},
    {"query": "cancel a test execution", "expect": ["cancel_test_execution"], "cluster": "simulation", "page": "agents"},
    # --- evals (2A packet E) ---
    {"query": "compare versions of an eval template", "expect": ["compare_eval_template_versions"], "cluster": "evals", "page": "evaluations"},
    {"query": "restore an eval template version", "expect": ["restore_eval_template_version"], "cluster": "evals", "page": "evaluations"},
    {"query": "duplicate an eval template", "expect": ["duplicate_eval_template"], "cluster": "evals", "page": "evaluations"},
    {"query": "ground truth status", "expect": ["get_ground_truth_status"], "cluster": "evals", "page": "evaluations"},
    {"query": "eval usage stats", "expect": ["get_eval_usage_stats"], "cluster": "evals", "page": "evaluations"},
    {"query": "test an eval template before saving", "expect": ["test_eval_template"], "cluster": "evals", "page": "evaluations"},
    # --- agentcc (gateway administration) ---
    {"query": "create a gateway routing policy", "expect": ["create_agentcc_routing_policy"], "cluster": "agentcc", "page": "gateway"},
    {"query": "set a budget for the gateway", "expect": ["set_gateway_budget"], "cluster": "agentcc", "page": "gateway"},
    {"query": "list gateway guardrail policies", "expect": ["list_agentcc_guardrail_policys"], "cluster": "agentcc", "page": "gateway"},
    {"query": "add words to the gateway blocklist", "expect": ["add_blocklist_words"], "cluster": "agentcc", "page": "gateway"},
    # --- error_feed ---
    {"query": "show error clusters", "expect": ["list_error_clusters"], "cluster": "error_feed", "page": "tracing"},
    {"query": "analyze an error cluster", "expect": ["analyze_error_cluster"], "cluster": "error_feed", "page": "tracing"},
]


def run_selection_bench() -> int:
    """Deterministic two-tier selection accuracy — no LLM, no DB writes.

    For each SELECTION_CASE:
      - search rank: where the expected tool lands in search_tools(query)
      - active40:    expected tool inside the ~40-schema active set for the
                     detected mode (tier-1 load + tier-2 filter)
      - reachable:   active40 OR search rank < 5 (the two-tier contract)
    Prints per-cluster and overall accuracy plus active-set token economics.
    """
    import json

    from ai_tools.tools.context.search_tools import SearchToolsInput, SearchToolsTool
    from ee.falcon_ai.modes import (
        detect_mode,
        filter_tools_for_message,
        load_tools_for_mode,
    )

    ctx = ToolContext(
        user=None, organization=None, workspace=None, transport="harness"
    )
    search = SearchToolsTool()

    rows = []
    active_set_sizes = []
    active_set_tokens = []
    for case in SELECTION_CASES:
        query = case["query"]
        expect = case["expect"]
        page = case.get("page") or "general"

        res = search.execute(SearchToolsInput(query=query, limit=12), ctx)
        names = [t["name"] for t in (res.data or {}).get("tools", [])]
        rank = None
        for e in expect:
            if e in names:
                r = names.index(e)
                rank = r if rank is None else min(rank, r)

        mode = detect_mode(page, query)
        tier1 = load_tools_for_mode(mode)
        active = filter_tools_for_message(tier1, query, max_tools=40)
        active_names = {t.name for t in active}
        in_active = any(e in active_names for e in expect)

        # Token economics: approximate schema tokens for the active set
        # (JSON chars / 4) — must hold ~8K with the 40-cap.
        approx_tokens = 0
        for t in active:
            try:
                schema_str = json.dumps(
                    {"name": t.name, "description": t.description, "schema": t.input_schema}
                )
            except (TypeError, ValueError):
                schema_str = f"{t.name} {t.description}"
            approx_tokens += len(schema_str) // 4
        active_set_sizes.append(len(active))
        active_set_tokens.append(approx_tokens)

        rows.append(
            {
                "cluster": case["cluster"],
                "query": query,
                "expect": expect,
                "rank": rank,
                "search1": rank == 0,
                "search5": rank is not None and rank < 5,
                "active40": in_active,
                "reachable": in_active or (rank is not None and rank < 5),
                "mode": mode,
            }
        )

    clusters: dict[str, list[dict]] = {}
    for r in rows:
        clusters.setdefault(r["cluster"], []).append(r)

    print(f"\n{'=' * 96}")
    print(f"Selection bench — {len(rows)} cases, {len(clusters)} clusters (deterministic, no LLM)")
    print(f"{'=' * 96}")
    print(
        f"{'cluster':<22} {'n':>3} {'search@1':>9} {'search@5':>9} {'active40':>9} {'reachable':>10}"
    )
    def _pct(xs):
        return f"{100 * sum(xs) / len(xs):5.0f}%" if xs else "  n/a"
    for cname in sorted(clusters):
        rs = clusters[cname]
        print(
            f"{cname:<22} {len(rs):>3} "
            f"{_pct([r['search1'] for r in rs]):>9} "
            f"{_pct([r['search5'] for r in rs]):>9} "
            f"{_pct([r['active40'] for r in rs]):>9} "
            f"{_pct([r['reachable'] for r in rs]):>10}"
        )
    print("-" * 96)
    print(
        f"{'TOTAL':<22} {len(rows):>3} "
        f"{_pct([r['search1'] for r in rows]):>9} "
        f"{_pct([r['search5'] for r in rows]):>9} "
        f"{_pct([r['active40'] for r in rows]):>9} "
        f"{_pct([r['reachable'] for r in rows]):>10}"
    )
    print(
        f"\nActive-set economics: avg {sum(active_set_sizes) / len(active_set_sizes):.1f} "
        f"tools/turn, avg ~{sum(active_set_tokens) / len(active_set_tokens):,.0f} tokens, "
        f"max ~{max(active_set_tokens):,.0f} tokens"
    )

    misses = [r for r in rows if not r["reachable"]]
    if misses:
        print("\nUnreachable (not in active set AND not in search top-5):")
        for r in misses:
            print(f"  [{r['cluster']}] '{r['query']}' → {r['expect']} (rank={r['rank']}, mode={r['mode']})")
    search_misses = [r for r in rows if not r["search5"]]
    if search_misses:
        print("\nsearch@5 misses:")
        for r in search_misses:
            print(f"  [{r['cluster']}] '{r['query']}' → {r['expect']} (rank={r['rank']})")
    return 0 if not misses else 1


@dataclass
class TaskResult:
    id: int
    msg: str
    expected_tools: list
    accept_tools: list = field(default_factory=list)
    adjudication: str | None = None
    actual_tools: list = field(default_factory=list)
    tool_latencies_ms: list = field(default_factory=list)
    total_latency_ms: float = 0.0
    success: bool = False
    error: str | None = None
    iterations: int = 0
    iteration_tokens: int = 0
    # Adjudication evidence: condensed event transcript + final answer text.
    transcript: list = field(default_factory=list)
    assistant_text: str = ""

    @property
    def expected_hit(self) -> bool:
        return any(t in self.actual_tools for t in self.expected_tools)

    @property
    def accepted_alternate(self) -> bool:
        """Explicitly adjudicated semantically-valid alternate was used."""
        return not self.expected_hit and any(
            t in self.actual_tools for t in self.accept_tools
        )

    @property
    def tools_match(self) -> bool:
        # accept_tools count as a hit ONLY because each carries a written
        # adjudication in the task data — never a silent pass.
        return self.expected_hit or self.accepted_alternate

    @property
    def all_expected_called(self) -> bool:
        return all(t in self.actual_tools for t in self.expected_tools)

    @property
    def verdict(self) -> str:
        if self.expected_hit:
            return "expected"
        if self.accepted_alternate:
            return "accepted_alternate"
        return "miss"


async def run_task(task: dict, tool_context: ToolContext) -> TaskResult:
    result = TaskResult(
        id=task["id"],
        msg=task["msg"],
        expected_tools=task["expected_tools"],
        accept_tools=task.get("accept_tools", []),
        adjudication=task.get("adjudication"),
    )

    conv = Conversation(
        id=uuid.uuid4(),
        user=tool_context.user,
        organization=tool_context.organization,
        workspace=tool_context.workspace,
        title=f"bench-{task['id']}",
    )

    agent = AgentLoop(tool_context=tool_context, conversation=conv)

    events = []
    tool_starts = {}
    text_parts = []

    async def send_callback(event):
        events.append(event)
        ev_type = event.get("type", "")
        data = event.get("data", {})
        if ev_type == "tool_call_start":
            call_id = data.get("call_id")
            tool_name = data.get("tool_name")
            result.actual_tools.append(tool_name)
            tool_starts[call_id] = (tool_name, time.time())
            result.transcript.append(
                {
                    "ev": "tool_call_start",
                    "tool": tool_name,
                    "params": data.get("params"),
                }
            )
        elif ev_type == "tool_call_result":
            call_id = data.get("call_id")
            if call_id in tool_starts:
                tool_name, start = tool_starts[call_id]
                latency_ms = (time.time() - start) * 1000
                result.tool_latencies_ms.append(latency_ms)
            result.transcript.append(
                {
                    "ev": "tool_call_result",
                    "tool": data.get("tool_name"),
                    "status": data.get("status"),
                    "result": (data.get("result_full") or "")[:600],
                }
            )
        elif ev_type == "text_delta":
            text_parts.append(data.get("text") or data.get("delta") or "")

    start = time.time()
    try:
        await asyncio.wait_for(
            agent.run(
                user_message=task["msg"],
                history_messages=[],
                send_callback=send_callback,
                context_page="general",
            ),
            timeout=120.0,
        )
        result.success = True
    except TimeoutError:
        result.error = "TIMEOUT after 120s"
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"

    result.total_latency_ms = (time.time() - start) * 1000
    result.assistant_text = "".join(text_parts)
    result.iterations = (
        getattr(agent, "_iterations", 0)
        if hasattr(agent, "_iterations")
        else len([e for e in events if e.get("type") == "tool_call_start"])
    )
    return result


_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _resolve_seed_ids(user) -> dict:
    """Resolve REAL entity ids for the Phase-2A task templates.

    Bench-adjudication finding (2026-06-11 wave): fake ids (999999 /
    zero-UUIDs) tempt the model into validate-first behavior — it lists or
    searches to check the id instead of calling the action tool, so the task
    measured id-validation, not selection. Destructive/action tasks are still
    side-effect-free with real ids: the 3A confirmation layer makes the first
    call preview-only, and stop/rerun on a non-running experiment is a no-op
    404-class response.

    Falls back to the old fake ids when the dev DB has no rows, so the bench
    degrades rather than crashes on an empty database.
    """
    import secrets

    from model_hub.models.annotation_queues import AnnotationQueue, QueueItem
    from model_hub.models.experiments import ExperimentsTable
    from tracer.models.dashboard import Dashboard, DashboardWidget

    org = user.organization
    # Unique-per-run suffix for create-project tasks: project names are
    # unique per (name, trace_type, org, workspace) — stale bench-test-*
    # rows from earlier runs made 'create X' correctly answerable with
    # "X already exists" (another task-design artifact, run 1 tasks 8/10).
    seeds = {"nonce": secrets.token_hex(3)}

    exps = list(
        ExperimentsTable.objects.filter(dataset__workspace__organization=org)
        .order_by("-created_at")
        .values_list("id", "status")[:25]
    )
    running = [str(i) for i, s in exps if s == "Running"]
    completed = [str(i) for i, s in exps if s == "Completed"]
    any_ids = [str(i) for i, _ in exps]
    seeds["exp_running_id"] = (running + any_ids + ["999999"])[0]
    comp = completed + [i for i in any_ids if i not in completed]
    seeds["exp_a_id"] = (comp + ["999998"])[0]
    seeds["exp_b_id"] = (comp[1:] + ["999999"])[0]

    queue = None
    for q in AnnotationQueue.objects.filter(organization=org).order_by(
        "-created_at"
    )[:25]:
        if QueueItem.objects.filter(queue=q).exists():
            queue = q
            break
    if queue is None:
        queue = AnnotationQueue.objects.filter(organization=org).first()
    seeds["queue_id"] = str(queue.id) if queue else _ZERO_UUID
    item = None
    if queue is not None:
        item = (
            QueueItem.objects.filter(queue=queue, status="pending").first()
            or QueueItem.objects.filter(queue=queue).first()
        )
    seeds["queue_item_id"] = str(item.id) if item else _ZERO_UUID

    dash = None
    for d in Dashboard.objects.filter(workspace__organization=org).order_by(
        "-created_at"
    )[:25]:
        if DashboardWidget.objects.filter(dashboard=d).exists():
            dash = d
            break
    if dash is None:
        dash = Dashboard.objects.filter(workspace__organization=org).first()
    seeds["dashboard_id"] = str(dash.id) if dash else _ZERO_UUID
    widget = (
        DashboardWidget.objects.filter(dashboard=dash).first() if dash else None
    )
    seeds["widget_id"] = str(widget.id) if widget else _ZERO_UUID

    # Realistic-but-nonexistent ids for MUTATE-class action tasks
    # (stop/rerun/complete/skip/review/duplicate): a real id would actually
    # execute the mutation against dev data (only `destructive` tools are
    # confirm-gated, `mutate` tools run immediately). A random uuid4 looks
    # exactly like a real id — no placeholder smell tempting validate-first
    # behavior — and the call 404s with zero side effects.
    for key in (
        "missing_exp_a",
        "missing_exp_b",
        "missing_item_id",
        "missing_widget_id",
        "missing_queue_id",
    ):
        seeds[key] = str(uuid.uuid4())
    return seeds


async def main():
    from asgiref.sync import sync_to_async

    def _load():
        u = User.objects.select_related("organization").get(email=USER_EMAIL)
        ws_obj = Workspace.objects.filter(
            organization=u.organization, is_default=True, is_active=True
        ).first()
        if not ws_obj:
            ws_obj = Workspace.objects.filter(organization=u.organization).first()
        return u, ws_obj

    user, workspace = await sync_to_async(_load)()
    seeds = await sync_to_async(_resolve_seed_ids)(user)

    print(f"\n{'=' * 80}")
    print(f"Falcon Bridge Tool Benchmark — {len(TASKS)} tasks")
    print(f"User: {user.email}")
    print(f"Workspace: {workspace.name if workspace else None}")
    print(f"{'=' * 80}\n")

    ctx = ToolContext(user=user, organization=user.organization, workspace=workspace)
    results = []

    only_ids = None
    if "--only" in sys.argv:
        only_ids = {
            int(x) for x in sys.argv[sys.argv.index("--only") + 1].split(",")
        }

    for task in TASKS:
        if only_ids is not None and task["id"] not in only_ids:
            continue
        task = {**task, "msg": task["msg"].format(**seeds)}
        print(f"Task {task['id']:2d}: {task['msg'][:70]}...")
        r = await run_task(task, ctx)
        results.append(r)
        status = "OK" if r.success and r.tools_match else "FAIL"
        if status == "OK" and r.accepted_alternate:
            status = "OK*"  # adjudicated alternate, see accept_tools
        tools_str = ",".join(r.actual_tools[:5]) if r.actual_tools else "(none)"
        print(f"  [{status}] {r.total_latency_ms:6.0f}ms | tools called: {tools_str}")
        if r.error:
            print(f"  ERROR: {r.error[:100]}")

    print(f"\n{'=' * 80}")
    print("RESULTS SUMMARY")
    print(f"{'=' * 80}\n")

    total = len(results)
    succeeded = sum(1 for r in results if r.success)
    tools_match = sum(1 for r in results if r.tools_match)
    all_expected = sum(1 for r in results if r.all_expected_called)

    print(
        f"Tasks completed:            {succeeded}/{total} ({100 * succeeded / total:.0f}%)"
    )
    print(
        f"Expected tool was called:   {tools_match}/{total} ({100 * tools_match / total:.0f}%)"
    )
    print(
        f"All expected tools called:  {all_expected}/{total} ({100 * all_expected / total:.0f}%)"
    )

    # Per-cluster selection accuracy (the Phase 2C acceptance metric:
    # improve-or-hold per cluster vs baseline).
    by_cluster: dict[str, list[TaskResult]] = {}
    task_clusters = {t["id"]: t.get("cluster", "other") for t in TASKS}
    for r in results:
        by_cluster.setdefault(task_clusters.get(r.id, "other"), []).append(r)
    print("\nPer-cluster selection accuracy (expected tool called):")
    for cname in sorted(by_cluster):
        rs = by_cluster[cname]
        hit = sum(1 for r in rs if r.tools_match)
        print(f"  {cname:<22} {hit}/{len(rs)} ({100 * hit / len(rs):.0f}%)")

    all_latencies = [r.total_latency_ms for r in results]
    if all_latencies:
        all_latencies_sorted = sorted(all_latencies)
        print("\nEnd-to-end latency (ms):")
        print(f"  min:    {min(all_latencies):.0f}")
        print(f"  median: {all_latencies_sorted[len(all_latencies) // 2]:.0f}")
        print(f"  p95:    {all_latencies_sorted[int(len(all_latencies) * 0.95)]:.0f}")
        print(f"  max:    {max(all_latencies):.0f}")
        print(f"  avg:    {sum(all_latencies) / len(all_latencies):.0f}")

    tool_latencies = [t for r in results for t in r.tool_latencies_ms]
    if tool_latencies:
        tool_sorted = sorted(tool_latencies)
        print(
            f"\nBridge tool execution latency (ms):  [{len(tool_latencies)} total calls]"
        )
        print(f"  min:    {min(tool_latencies):.0f}")
        print(f"  median: {tool_sorted[len(tool_latencies) // 2]:.0f}")
        print(f"  p95:    {tool_sorted[int(len(tool_latencies) * 0.95)]:.0f}")
        print(f"  max:    {max(tool_latencies):.0f}")
        print(f"  avg:    {sum(tool_latencies) / len(tool_latencies):.0f}")

    tool_count_per_task = [len(r.actual_tools) for r in results]
    if tool_count_per_task:
        print("\nTool calls per task:")
        print(f"  min:    {min(tool_count_per_task)}")
        print(f"  median: {sorted(tool_count_per_task)[len(tool_count_per_task) // 2]}")
        print(f"  max:    {max(tool_count_per_task)}")
        print(f"  total:  {sum(tool_count_per_task)}")

    tool_usage = {}
    for r in results:
        for t in r.actual_tools:
            tool_usage[t] = tool_usage.get(t, 0) + 1
    print("\nMost-called tools:")
    for tool, count in sorted(tool_usage.items(), key=lambda x: -x[1])[:10]:
        marker = (
            " (BRIDGE)"
            if tool
            in [
                "list_trace_projects",
                "get_trace_project",
                "create_trace_project",
                "rename_trace_project",
                "list_prompt_templates",
                "get_prompt_template",
                "create_prompt_template",
                "update_prompt_template",
                "delete_prompt_template",
                "list_workspaces",
                "list_users",
            ]
            else ""
        )
        print(f"  {tool}: {count}{marker}")

    accepted = [r for r in results if r.accepted_alternate]
    if accepted:
        print("\nAccepted alternates (explicitly adjudicated in task data):")
        for r in accepted:
            print(
                f"  Task {r.id}: got {r.actual_tools[:5]} "
                f"(accept: {r.accept_tools}) — {r.adjudication or 'no note'}"
            )

    print("\nFailed tasks:")
    failures = [r for r in results if not r.success or not r.tools_match]
    if not failures:
        print("  (none)")
    for r in failures:
        why = (
            "no tools called"
            if not r.actual_tools
            else "wrong tools"
            if not r.tools_match
            else (r.error or "unknown")
        )
        print(f"  Task {r.id}: {why}")
        print(f"    expected: {r.expected_tools}, got: {r.actual_tools[:5]}")

    # Adjudication artifact: condensed transcripts for every task so misses
    # are classified from evidence (task-design artifact vs valid alternate
    # vs genuine selection failure), never from vibes.
    out_path = None
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]
    if out_path:
        import json as _json

        payload = [
            {
                "id": r.id,
                "msg": r.msg,
                "cluster": task_clusters.get(r.id, "other"),
                "expected": r.expected_tools,
                "accept": r.accept_tools,
                "adjudication": r.adjudication,
                "actual_tools": r.actual_tools,
                "verdict": r.verdict,
                "success": r.success,
                "error": r.error,
                "latency_ms": round(r.total_latency_ms),
                "assistant_text": r.assistant_text[:3000],
                "transcript": r.transcript,
            }
            for r in results
        ]
        with open(out_path, "w") as f:
            _json.dump(
                {
                    "total": total,
                    "tools_match": tools_match,
                    "tasks": payload,
                },
                f,
                indent=1,
                default=str,
            )
        print(f"\nTranscript artifact: {out_path}")

    return 0 if (succeeded == total and tools_match >= total * 0.8) else 1


if __name__ == "__main__":
    if "--selection" in sys.argv:
        sys.exit(run_selection_bench())
    sys.exit(asyncio.run(main()))
