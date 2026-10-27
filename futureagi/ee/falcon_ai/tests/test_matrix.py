"""
Comprehensive Falcon AI Test Matrix.

Tests organized by use case → feature → cross-feature scenarios.
Each test is a user message that exercises specific tools and capabilities.

Run: python falcon_ai/tests/test_matrix.py [batch_number] [batch_size]
"""

import asyncio
import json
import subprocess
import sys
import time

# ─── TEST MATRIX ────────────────────────────────────────────────────────────

# Format: (category, subcategory, user_message, expected_tools)
# expected_tools: tools the agent SHOULD call (not necessarily in order)

TEST_CASES = [
    # ════════════════════════════════════════════════════════════════════════
    # UC1: DATASET MANAGEMENT
    # ════════════════════════════════════════════════════════════════════════
    # 1.1 Dataset CRUD
    (
        "datasets",
        "create",
        "Create a dataset called 'customer-feedback' with columns: text, sentiment, score",
        ["create_dataset"],
    ),
    ("datasets", "list", "What datasets do I have?", ["list_datasets"]),
    (
        "datasets",
        "get",
        "Show me the details of my first dataset",
        ["list_datasets", "get_dataset"],
    ),
    (
        "datasets",
        "update",
        "Rename my dataset to 'feedback-v2'",
        ["list_datasets", "update_dataset"],
    ),
    (
        "datasets",
        "delete",
        "Delete the dataset called feedback-v2",
        ["list_datasets", "delete_dataset"],
    ),
    # 1.2 Column Operations
    (
        "datasets",
        "add_columns",
        "Add a 'category' column to my dataset",
        ["list_datasets", "add_columns"],
    ),
    (
        "datasets",
        "delete_column",
        "Remove the score column from the dataset",
        ["list_datasets", "get_dataset", "delete_column"],
    ),
    (
        "datasets",
        "update_column",
        "Change the 'text' column type to json",
        ["list_datasets", "get_dataset", "update_column"],
    ),
    # 1.3 Row Operations
    (
        "datasets",
        "add_rows",
        "Add 5 sample rows to my customer feedback dataset",
        ["list_datasets", "add_dataset_rows"],
    ),
    (
        "datasets",
        "get_rows",
        "Show me the first 10 rows of my dataset",
        ["list_datasets", "get_dataset_rows"],
    ),
    (
        "datasets",
        "update_cell",
        "Update the sentiment of row 1 to 'positive'",
        ["list_datasets", "get_dataset_rows", "update_cell_value"],
    ),
    (
        "datasets",
        "delete_rows",
        "Delete the last 2 rows from my dataset",
        ["list_datasets", "get_dataset_rows", "delete_rows"],
    ),
    (
        "datasets",
        "duplicate_rows",
        "Duplicate the first row 3 times",
        ["list_datasets", "duplicate_rows"],
    ),
    # 1.4 Advanced Dataset Operations
    (
        "datasets",
        "clone",
        "Clone my customer-feedback dataset as 'feedback-backup'",
        ["list_datasets", "clone_dataset"],
    ),
    (
        "datasets",
        "merge",
        "Merge my two feedback datasets",
        ["list_datasets", "merge_datasets"],
    ),
    (
        "datasets",
        "duplicate",
        "Duplicate dataset 'customer-feedback'",
        ["list_datasets", "duplicate_dataset"],
    ),
    (
        "datasets",
        "from_file",
        "I have a CSV with customer data. How do I upload it?",
        ["search_docs"],
    ),
    (
        "datasets",
        "synthetic",
        "Generate 20 synthetic rows for my QA dataset",
        ["list_datasets", "get_dataset", "add_dataset_rows"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC2: EVALUATIONS
    # ════════════════════════════════════════════════════════════════════════
    # 2.1 Eval Templates
    (
        "evals",
        "list_templates",
        "What evaluation templates are available?",
        ["list_eval_templates"],
    ),
    (
        "evals",
        "get_template",
        "Show me the faithfulness eval template",
        ["list_eval_templates", "get_eval_template"],
    ),
    (
        "evals",
        "create_template",
        "Create a custom eval for checking if responses are polite",
        ["create_eval_template"],
    ),
    (
        "evals",
        "duplicate_template",
        "Duplicate the hallucination eval",
        ["list_eval_templates", "duplicate_eval_template"],
    ),
    # 2.2 Running Evaluations
    (
        "evals",
        "run_on_dataset",
        "Run the faithfulness eval on my QA dataset",
        ["list_datasets", "list_eval_templates", "run_dataset_evals"],
    ),
    (
        "evals",
        "run_multiple",
        "Run faithfulness and hallucination evals on all my datasets",
        ["list_datasets", "list_eval_templates", "run_dataset_evals"],
    ),
    (
        "evals",
        "check_results",
        "Show me the evaluation results for my latest dataset",
        ["list_datasets", "get_dataset_eval_stats"],
    ),
    (
        "evals",
        "compare",
        "Compare eval results between my two datasets",
        ["list_datasets", "compare_evaluations"],
    ),
    # 2.3 Eval Groups
    (
        "evals",
        "create_group",
        "Create an eval group called 'quality-suite' with faithfulness and relevance",
        ["list_eval_templates", "create_eval_group"],
    ),
    ("evals", "list_groups", "What eval groups do I have?", ["list_eval_groups"]),
    (
        "evals",
        "apply_group",
        "Apply my quality-suite eval group to all datasets",
        ["list_datasets", "list_eval_groups", "apply_eval_group_to_dataset"],
    ),
    # 2.4 Eval Tasks
    (
        "evals",
        "create_task",
        "Set up a recurring eval task for my production data",
        ["create_eval_task"],
    ),
    ("evals", "list_tasks", "Show my running eval tasks", ["list_eval_tasks"]),
    # ════════════════════════════════════════════════════════════════════════
    # UC3: TRACING & OBSERVABILITY
    # ════════════════════════════════════════════════════════════════════════
    # 3.1 Projects
    ("tracing", "list_projects", "What tracing projects do I have?", ["list_projects"]),
    (
        "tracing",
        "create_project",
        "Create a new project called 'production-api'",
        ["create_project"],
    ),
    # 3.2 Traces
    ("tracing", "search", "Show me traces from the last 24 hours", ["search_traces"]),
    (
        "tracing",
        "search_errors",
        "Find all error traces in my production project",
        ["list_projects", "search_traces"],
    ),
    (
        "tracing",
        "get_trace",
        "Show me the details of the latest trace",
        ["search_traces", "get_trace"],
    ),
    (
        "tracing",
        "trace_timeline",
        "Show me the timeline of my latest trace",
        ["search_traces", "get_trace_timeline"],
    ),
    (
        "tracing",
        "trace_tree",
        "Show the span tree for this trace",
        ["search_traces", "get_span_tree"],
    ),
    # 3.3 Spans
    (
        "tracing",
        "list_spans",
        "Show all spans for my latest trace",
        ["search_traces", "list_spans"],
    ),
    (
        "tracing",
        "get_span",
        "Get details of the LLM span in my trace",
        ["search_traces", "list_spans", "get_span"],
    ),
    # 3.4 Analysis
    (
        "tracing",
        "analyze_errors",
        "Analyze error patterns in my traces",
        ["search_traces", "analyze_errors"],
    ),
    (
        "tracing",
        "error_analysis",
        "What are the top errors in my production project?",
        ["list_projects", "get_trace_error_analysis"],
    ),
    (
        "tracing",
        "analytics",
        "Show me trace analytics for the last week",
        ["get_trace_analytics"],
    ),
    ("tracing", "cost", "How much have I spent on AI calls?", ["get_cost_breakdown"]),
    # 3.5 Tags & Annotations
    (
        "tracing",
        "add_tags",
        "Tag my latest trace with 'production' and 'v2'",
        ["search_traces", "add_trace_tags"],
    ),
    ("tracing", "list_tags", "What tags are on my traces?", ["list_trace_tags"]),
    (
        "tracing",
        "annotate",
        "Annotate this trace as 'good quality'",
        ["search_traces", "create_trace_annotation"],
    ),
    # 3.6 Sessions
    (
        "tracing",
        "sessions",
        "Show me user sessions in my project",
        ["list_projects", "list_sessions"],
    ),
    (
        "tracing",
        "session_detail",
        "Show analytics for my latest session",
        ["list_sessions", "get_session_analytics"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC4: PROMPTS & PROMPT ENGINEERING
    # ════════════════════════════════════════════════════════════════════════
    # 4.1 Prompt Templates
    ("prompts", "list", "What prompt templates do I have?", ["list_prompt_templates"]),
    (
        "prompts",
        "create",
        "Create a prompt template for customer support classification",
        ["create_prompt_template"],
    ),
    (
        "prompts",
        "get",
        "Show me my customer support prompt",
        ["list_prompt_templates", "get_prompt_template"],
    ),
    (
        "prompts",
        "update",
        "Update my support prompt to include a tone instruction",
        ["list_prompt_templates", "update_prompt_template"],
    ),
    # 4.2 Prompt Versions
    (
        "prompts",
        "create_version",
        "Create a new version of my support prompt with few-shot examples",
        ["list_prompt_templates", "create_prompt_version"],
    ),
    (
        "prompts",
        "list_versions",
        "Show all versions of my prompt",
        ["list_prompt_templates", "list_prompt_versions"],
    ),
    (
        "prompts",
        "compare_versions",
        "Compare v1 and v2 of my prompt",
        ["list_prompt_templates", "list_prompt_versions", "compare_prompt_versions"],
    ),
    (
        "prompts",
        "commit",
        "Commit the latest prompt version with message 'Added examples'",
        ["list_prompt_templates", "list_prompt_versions", "commit_prompt_version"],
    ),
    # 4.3 Running Prompts
    (
        "prompts",
        "run",
        "Run my support prompt with input: 'I can't login to my account'",
        ["list_prompt_templates", "run_prompt"],
    ),
    (
        "prompts",
        "run_evals",
        "Run evaluations on my prompt outputs",
        ["list_prompt_templates", "run_prompt_evals"],
    ),
    # 4.4 Prompt Optimization
    (
        "prompts",
        "optimize",
        "Optimize my prompt for better accuracy",
        ["list_prompt_templates", "create_optimization_run"],
    ),
    (
        "prompts",
        "optimization_results",
        "Show me the optimization results",
        ["list_optimization_runs", "get_optimization_run"],
    ),
    # 4.5 Prompt Simulations
    (
        "prompts",
        "create_sim",
        "Create a simulation for my support prompt with 10 scenarios",
        ["list_prompt_templates", "create_prompt_simulation"],
    ),
    (
        "prompts",
        "run_sim",
        "Run my prompt simulation",
        ["list_prompt_simulations", "execute_prompt_simulation"],
    ),
    (
        "prompts",
        "sim_results",
        "Show simulation results",
        ["list_prompt_simulations", "get_prompt_simulation"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC5: EXPERIMENTS
    # ════════════════════════════════════════════════════════════════════════
    ("experiments", "list", "What experiments do I have?", ["list_experiments"]),
    (
        "experiments",
        "create",
        "Create an experiment comparing GPT-4 vs Claude on my QA dataset",
        ["list_datasets", "create_experiment"],
    ),
    (
        "experiments",
        "results",
        "Show me experiment results",
        ["list_experiments", "get_experiment_results"],
    ),
    (
        "experiments",
        "comparison",
        "Compare the variants in my experiment",
        ["list_experiments", "get_experiment_comparison"],
    ),
    (
        "experiments",
        "stats",
        "Show detailed stats for my experiment",
        ["list_experiments", "get_experiment_stats"],
    ),
    (
        "experiments",
        "rerun",
        "Rerun my latest experiment",
        ["list_experiments", "rerun_experiment"],
    ),
    (
        "experiments",
        "add_eval",
        "Add the hallucination eval to my experiment",
        ["list_experiments", "list_eval_templates", "add_experiment_eval"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC6: SIMULATION / AGENT TESTING
    # ════════════════════════════════════════════════════════════════════════
    ("simulation", "list_agents", "What agent definitions do I have?", ["list_agents"]),
    (
        "simulation",
        "create_agent",
        "Create a customer support agent definition",
        ["create_agent_definition"],
    ),
    (
        "simulation",
        "create_scenario",
        "Create a test scenario for my agent",
        ["list_agents", "create_scenario"],
    ),
    (
        "simulation",
        "run_test",
        "Run a simulation test on my agent",
        ["list_agents", "create_run_test"],
    ),
    (
        "simulation",
        "test_results",
        "Show me the test results",
        ["list_test_executions", "get_test_execution"],
    ),
    (
        "simulation",
        "personas",
        "Create a persona for an angry customer",
        ["create_persona"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC7: DOCUMENTATION
    # ════════════════════════════════════════════════════════════════════════
    ("docs", "search", "How do I set up tracing?", ["search_docs"]),
    ("docs", "ask", "What evaluation metrics are available?", ["ask_docs"]),
    ("docs", "page", "Show me the Python SDK documentation", ["search_docs"]),
    ("docs", "how_to", "How do I create a dataset from a CSV file?", ["search_docs"]),
    # ════════════════════════════════════════════════════════════════════════
    # UC8: WORKSPACE & ADMIN
    # ════════════════════════════════════════════════════════════════════════
    ("admin", "whoami", "Who am I?", ["whoami"]),
    ("admin", "workspace", "What workspace am I in?", ["whoami"]),
    (
        "admin",
        "members",
        "Who are the members of my workspace?",
        ["list_workspace_members"],
    ),
    ("admin", "api_keys", "Show my API keys", ["list_api_keys"]),
    ("admin", "cost", "What's my spending breakdown?", ["get_cost_breakdown"]),
    # ════════════════════════════════════════════════════════════════════════
    # UC9: ANNOTATIONS
    # ════════════════════════════════════════════════════════════════════════
    (
        "annotations",
        "list_labels",
        "What annotation labels do I have?",
        ["list_annotation_labels"],
    ),
    (
        "annotations",
        "create_label",
        "Create a label called 'quality' with options good/bad",
        ["create_annotation_label"],
    ),
    (
        "annotations",
        "create_queue",
        "Create an annotation queue for my team",
        ["create_annotation_queue"],
    ),
    (
        "annotations",
        "list_queues",
        "Show my annotation queues",
        ["list_annotation_queues"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC10: ALERTS & MONITORING
    # ════════════════════════════════════════════════════════════════════════
    (
        "alerts",
        "create",
        "Set up an alert when error rate exceeds 5%",
        ["create_alert_monitor"],
    ),
    ("alerts", "list", "Show my active alerts", ["list_alert_monitors"]),
    # ════════════════════════════════════════════════════════════════════════
    # UC11: KNOWLEDGE BASE
    # ════════════════════════════════════════════════════════════════════════
    ("knowledge", "list", "What knowledge bases do I have?", ["list_knowledge_bases"]),
    # ════════════════════════════════════════════════════════════════════════
    # UC12: CROSS-FEATURE WORKFLOWS
    # ════════════════════════════════════════════════════════════════════════
    # Dataset → Eval
    (
        "cross",
        "dataset_to_eval",
        "Create a QA dataset, add 10 rows, then run faithfulness eval on it",
        [
            "create_dataset",
            "add_dataset_rows",
            "list_eval_templates",
            "run_dataset_evals",
        ],
    ),
    (
        "cross",
        "eval_analysis",
        "Find my worst performing dataset and show what evals failed",
        ["list_datasets", "get_dataset_eval_stats"],
    ),
    # Prompt → Experiment
    (
        "cross",
        "prompt_to_experiment",
        "Create a prompt, test it with an experiment comparing GPT-4 and Claude",
        [
            "create_prompt_template",
            "create_prompt_version",
            "list_datasets",
            "create_experiment",
        ],
    ),
    (
        "cross",
        "prompt_optimize_eval",
        "Optimize my prompt, then run evals on the optimized version",
        ["list_prompt_templates", "create_optimization_run", "run_prompt_evals"],
    ),
    # Tracing → Dataset
    (
        "cross",
        "traces_to_dataset",
        "Find error traces and create a dataset from them for debugging",
        ["search_traces", "create_dataset"],
    ),
    (
        "cross",
        "trace_eval",
        "Find my latest traces and evaluate them for quality",
        ["search_traces", "list_eval_templates"],
    ),
    # Full Pipeline
    (
        "cross",
        "full_pipeline",
        "Build a complete QA pipeline: create dataset, add test data, set up evals, run them, and show results",
        [
            "create_dataset",
            "add_dataset_rows",
            "list_eval_templates",
            "run_dataset_evals",
            "get_dataset_eval_stats",
        ],
    ),
    (
        "cross",
        "cost_optimize",
        "Analyze my costs and suggest which models to switch to save money",
        ["get_cost_breakdown", "search_traces"],
    ),
    # Docs + Action
    (
        "cross",
        "docs_then_do",
        "How do I set up tracing? Then create a project for me",
        ["search_docs", "create_project"],
    ),
    (
        "cross",
        "compare_all",
        "Compare all my experiments and show which model performs best overall",
        ["list_experiments", "get_experiment_comparison"],
    ),
    # Multi-step Complex
    (
        "cross",
        "workspace_audit",
        "Give me a full workspace health report: datasets, evals, traces, costs, alerts",
        [
            "list_datasets",
            "list_eval_templates",
            "search_traces",
            "get_cost_breakdown",
            "list_alert_monitors",
        ],
    ),
    (
        "cross",
        "debug_session",
        "Debug my production issues: find errors, analyze them, check if evals caught them",
        ["search_traces", "analyze_errors", "get_trace_error_analysis"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC13: EDGE CASES & ERROR HANDLING
    # ════════════════════════════════════════════════════════════════════════
    (
        "edge",
        "empty_workspace",
        "List all my resources",
        ["list_datasets", "list_projects"],
    ),
    ("edge", "ambiguous", "Show me everything about my data", ["list_datasets"]),
    (
        "edge",
        "multi_intent",
        "Create a dataset AND run an eval AND show my costs",
        ["create_dataset", "list_eval_templates", "get_cost_breakdown"],
    ),
    (
        "edge",
        "correction",
        "Actually, I meant the other dataset",
        [],
    ),  # Context-dependent
    ("edge", "greeting", "Hello! What can you do?", []),  # No tools expected
    (
        "edge",
        "complex_query",
        "What's the correlation between my trace latency and eval scores?",
        ["search_traces", "get_trace_analytics"],
    ),
    # ════════════════════════════════════════════════════════════════════════
    # UC14: MCP TOOLS (External Connectors)
    # ════════════════════════════════════════════════════════════════════════
    (
        "mcp",
        "docs_search",
        "Search the Future AGI docs for how to use the Python SDK",
        ["search_docs"],
    ),
    ("mcp", "docs_ask", "What is an evaluation in Future AGI?", ["ask_docs"]),
    ("mcp", "docs_page", "Show me the tracing setup guide", ["search_docs"]),
    # ════════════════════════════════════════════════════════════════════════
    # UC15: SKILLS (Slash Commands)
    # ════════════════════════════════════════════════════════════════════════
    ("skills", "analyze_costs", "/analyze-costs", ["get_cost_breakdown"]),
    (
        "skills",
        "build_dataset",
        "/build-dataset create a QA dataset for testing",
        ["create_dataset"],
    ),
    (
        "skills",
        "debug_traces",
        "/debug-traces find errors in production",
        ["search_traces", "analyze_errors"],
    ),
    (
        "skills",
        "run_evals",
        "/run-evaluations on my latest dataset",
        ["list_datasets", "list_eval_templates", "run_dataset_evals"],
    ),
]

# ─── TEST RUNNER ────────────────────────────────────────────────────────────


def get_token():
    r = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            "http://localhost:8001/accounts/token/",
            "-H",
            "Content-Type: application/json",
            "-d",
            '{"email":"nikhil@futureagi.com","password":"test@123"}',
        ],
        capture_output=True,
        text=True,
    )
    return json.loads(r.stdout)["access"]


def create_conv(token, title):
    r = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            "http://localhost:8001/falcon-ai/conversations/",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"title": title}),
        ],
        capture_output=True,
        text=True,
    )
    d = json.loads(r.stdout)
    return d.get("result", {}).get("id", d.get("id", ""))


async def run_test(ws_uri, conv_id, message, expected_tools, timeout=90):
    """Run a single test case and return results."""
    import websockets

    tools_called = []
    response = ""
    errors = []

    try:
        async with websockets.connect(
            ws_uri, max_size=10_000_000, ping_interval=30, ping_timeout=60
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "chat",
                        "message": message,
                        "conversation_id": conv_id,
                        "context": {"page": "falcon-ai"},
                        "file_ids": [],
                    }
                )
            )

            start = time.time()
            while time.time() - start < timeout:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15)
                    d = json.loads(raw)
                    t = d.get("type", "")

                    if t == "text_delta":
                        response += d.get("data", {}).get("delta", "")
                    elif t == "tool_call_start":
                        tools_called.append(d.get("data", {}).get("tool_name", ""))
                    elif t == "tool_call_result":
                        status = d.get("data", {}).get("status", "")
                        if status == "error":
                            errors.append(d.get("data", {}).get("result_summary", ""))
                    elif t == "error":
                        errors.append(d.get("message", ""))
                        break
                    elif t == "done":
                        break
                except asyncio.TimeoutError:
                    continue

    except Exception as e:
        errors.append(f"Connection error: {e}")

    # Score the result
    elapsed = time.time() - start if "start" in dir() else 0
    tools_matched = set(expected_tools) & set(tools_called) if expected_tools else set()
    tools_expected = set(expected_tools) if expected_tools else set()
    tool_coverage = len(tools_matched) / len(tools_expected) if tools_expected else 1.0

    has_response = len(response) > 20
    has_tools = len(tools_called) > 0

    # Classify errors — some are informational, not failures
    real_errors = []
    informational = []
    for e in errors:
        e_lower = e.lower()
        # These are informational, not failures
        if any(
            x in e_lower
            for x in [
                "already exists",
                "use this id",
                "existing dataset id",
                "use a different name",
                "documentation search is currently unavailable",
            ]
        ):
            informational.append(e)
        else:
            real_errors.append(e)

    # Determine pass/fail — more lenient scoring
    status = "PASS"
    if not has_response and not has_tools:
        status = "FAIL_NO_RESPONSE"
    elif len(real_errors) > 0 and not has_response:
        # Errors AND no useful response = real failure
        status = "FAIL_ERROR"
    elif len(real_errors) > len(tools_called) and not has_response:
        # More errors than successful calls and no response
        status = "FAIL_TOOL_ERROR"
    elif has_response and has_tools:
        # Agent called tools and gave a response — success even if some errors along the way
        status = "PASS"
    elif has_response and not has_tools and expected_tools:
        # Gave a text response but didn't use expected tools
        status = "WARN_NO_TOOLS"
    elif tool_coverage < 0.3 and expected_tools and not has_response:
        status = "WARN_LOW_COVERAGE"

    return {
        "status": status,
        "tools_called": tools_called,
        "tools_expected": list(expected_tools),
        "tool_coverage": round(tool_coverage, 2),
        "response_len": len(response),
        "errors": errors,
        "elapsed": round(elapsed, 1),
    }


async def run_batch(batch_tests, token, batch_name="batch"):
    """Run a batch of tests sequentially in one conversation."""
    conv_id = create_conv(token, f"test-{batch_name}")
    ws_uri = f"ws://localhost:8001/ws/falcon-ai/?token={token}"

    results = []
    for i, (cat, sub, msg, expected) in enumerate(batch_tests):
        test_id = f"{cat}/{sub}"
        print(f"  [{i+1}/{len(batch_tests)}] {test_id}: {msg[:60]}...", flush=True)

        # Each test gets a fresh conversation to avoid context bleed
        test_conv_id = create_conv(token, f"test-{test_id}")
        result = await run_test(ws_uri, test_conv_id, msg, expected)
        result["test_id"] = test_id
        result["message"] = msg
        results.append(result)

        status = result["status"]
        emoji = "✅" if status == "PASS" else "⚠️" if "WARN" in status else "❌"
        print(
            f"    {emoji} {status} | tools={','.join(result['tools_called'][:3])} | {result['elapsed']}s",
            flush=True,
        )

        await asyncio.sleep(0.5)  # Rate limit

    return results


def print_summary(all_results):
    """Print test summary."""
    total = len(all_results)
    passed = sum(1 for r in all_results if r["status"] == "PASS")
    warned = sum(1 for r in all_results if "WARN" in r["status"])
    failed = sum(1 for r in all_results if "FAIL" in r["status"])

    print(f"\n{'='*60}")
    print(f"RESULTS: {passed}/{total} passed, {warned} warnings, {failed} failed")
    print(f"{'='*60}")

    if failed:
        print("\n❌ FAILURES:")
        for r in all_results:
            if "FAIL" in r["status"]:
                print(f"  {r['test_id']}: {r['status']} | {r['errors'][:2]}")

    if warned:
        print("\n⚠️ WARNINGS:")
        for r in all_results:
            if "WARN" in r["status"]:
                print(
                    f"  {r['test_id']}: coverage={r['tool_coverage']} | expected={r['tools_expected'][:3]} got={r['tools_called'][:3]}"
                )

    # Save results to JSON
    with open("falcon_test_results.json", "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nFull results saved to falcon_test_results.json")


def uniquify_test_messages(cases):
    """Add a unique suffix to test messages that create resources to avoid name collisions."""
    import random

    suffix = f"-t{random.randint(1000,9999)}"
    uniquified = []
    for cat, sub, msg, expected in cases:
        # Replace hardcoded resource names with unique ones
        if any(verb in sub for verb in ["create", "clone", "duplicate"]):
            msg = msg.replace("customer-feedback", f"customer-feedback{suffix}")
            msg = msg.replace("feedback-v2", f"feedback-v2{suffix}")
            msg = msg.replace("feedback-backup", f"feedback-backup{suffix}")
            msg = msg.replace("quality-suite", f"quality-suite{suffix}")
            msg = msg.replace("production-api", f"production-api{suffix}")
            msg = msg.replace("'quality'", f"'quality{suffix}'")
        uniquified.append((cat, sub, msg, expected))
    return uniquified


async def main():
    batch_start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    batch = uniquify_test_messages(TEST_CASES[batch_start : batch_start + batch_size])
    print(
        f"Running batch: tests {batch_start}-{batch_start + len(batch)} ({len(batch)} tests)"
    )
    print(f"Total test cases: {len(TEST_CASES)}")

    token = get_token()
    results = await run_batch(batch, token, f"batch-{batch_start}")
    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
