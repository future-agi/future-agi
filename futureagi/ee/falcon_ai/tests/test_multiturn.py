"""
Multi-turn Falcon AI Test Suite.

Each test is a conversation with up to 10 follow-up messages in the same session.
Tests verify the agent can handle sequential tasks with context from previous turns.

Run: python falcon_ai/tests/test_multiturn.py [group] [batch_start] [batch_size]
Groups: datasets, evals, tracing, prompts, experiments, simulation, annotations, admin, docs
"""

import asyncio
import json
import random
import subprocess
import sys
import time

# ─── MULTI-TURN TEST CASES ──────────────────────────────────────────────────
# Format: (group, test_id, [list of messages in conversation order])

TESTS = [
    # ═══════════════════════════════════════════════════════════════════
    # DATASETS — Full lifecycle with follow-ups
    # ═══════════════════════════════════════════════════════════════════
    (
        "datasets",
        "full_lifecycle",
        [
            "Create a dataset called 'mt-sales-data' with columns: customer_name, inquiry, response, sentiment",
            "Show me the dataset schema",
            "Add 5 sample rows with realistic sales conversation data",
            "Show me the rows you added",
            "Update the sentiment of the first row to 'positive'",
            "Add a new column called 'priority' with type integer",
            "Duplicate the first 2 rows",
            "How many rows do we have now?",
            "Delete the last row",
            "Show the final state of the dataset",
        ],
    ),
    (
        "datasets",
        "eval_workflow",
        [
            "What datasets do I have?",
            "Show me the details of the largest dataset",
            "What eval templates are available?",
            "Add the faithfulness eval to this dataset",
            "Run the evals",
            "Show me the eval results",
            "Which rows scored the lowest?",
        ],
    ),
    (
        "datasets",
        "clone_and_modify",
        [
            "List my datasets",
            "Clone the first one as 'mt-clone-test'",
            "Show the clone's schema",
            "Add 3 new rows to the clone",
            "Compare row counts between original and clone",
            "Merge them together",
        ],
    ),
    (
        "datasets",
        "column_operations",
        [
            "Create a dataset 'mt-col-test' with columns: text, label",
            "Add a column called 'confidence' with type float",
            "Add a column called 'category' with type text",
            "Show the schema now",
            "Update the 'label' column type to integer",
            "Remove the 'category' column",
            "Show final schema",
        ],
    ),
    (
        "datasets",
        "bulk_data",
        [
            "Create a dataset 'mt-bulk-test' with columns: question, answer, source",
            "Add 10 QA rows about customer support topics",
            "Show me the first 5 rows",
            "Update row 3's answer to something more detailed",
            "Duplicate rows 1 through 3",
            "How many total rows now?",
            "Delete rows where source is empty",
            "Show final dataset stats",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # EVALUATIONS — Template management and execution
    # ═══════════════════════════════════════════════════════════════════
    (
        "evals",
        "template_lifecycle",
        [
            "List all eval templates",
            "Show me the faithfulness template in detail",
            "Create a custom eval template called 'mt-tone-check' that evaluates response tone",
            "Show the template I just created",
            "What datasets could I run this on?",
            "Run it on my first dataset",
            "Show the results",
        ],
    ),
    (
        "evals",
        "group_workflow",
        [
            "What eval templates do I have?",
            "Create an eval group called 'mt-quality-suite' combining faithfulness and hallucination",
            "Show the group details",
            "List my datasets",
            "Apply this group to my largest dataset",
            "Run the evaluations",
            "Compare results across eval types",
        ],
    ),
    (
        "evals",
        "compare_across",
        [
            "List my datasets",
            "Show eval results for the first dataset",
            "Show eval results for the second dataset",
            "Compare eval results between these two datasets",
            "Which dataset has better quality overall?",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # TRACING — Debug and analysis workflows
    # ═══════════════════════════════════════════════════════════════════
    (
        "tracing",
        "debug_flow",
        [
            "What tracing projects do I have?",
            "Search for traces from the last 24 hours",
            "Show me the latest trace in detail",
            "Show its timeline",
            "Show the span tree",
            "Are there any errors?",
            "Analyze error patterns across all recent traces",
            "What's the most common error?",
        ],
    ),
    (
        "tracing",
        "cost_analysis",
        [
            "Show my cost breakdown",
            "Which model costs the most?",
            "Search for traces using that model",
            "How many traces in the last week?",
            "What's the average latency?",
            "Show trace analytics for the last 7 days",
        ],
    ),
    (
        "tracing",
        "tagging_flow",
        [
            "Find my latest trace",
            "What tags does it have?",
            "Tag it with 'reviewed' and 'production'",
            "Show the tags now",
            "Add a quality score of 0.95 to this trace",
            "Annotate it as 'verified good'",
            "Show all scores for this trace",
        ],
    ),
    (
        "tracing",
        "session_analysis",
        [
            "List user sessions in my project",
            "Show analytics for the latest session",
            "How many traces in this session?",
            "Show the session details",
        ],
    ),
    (
        "tracing",
        "project_management",
        [
            "List all projects",
            "Create a project called 'mt-test-project'",
            "Show project details",
            "Update the description to 'Multi-turn test project'",
            "What traces are in this project?",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # PROMPTS — Engineering and iteration
    # ═══════════════════════════════════════════════════════════════════
    (
        "prompts",
        "iterate_versions",
        [
            "Create a prompt template called 'mt-support-bot' for customer support",
            "Show the template",
            "Create version 2 with improved system instructions that include empathy",
            "Create version 3 that adds few-shot examples",
            "List all versions",
            "Compare version 1 and version 3",
            "Commit version 3 with message 'Added empathy and examples'",
        ],
    ),
    (
        "prompts",
        "test_and_eval",
        [
            "List my prompt templates",
            "Pick the first one and run it with input: 'I cant login to my account'",
            "Run it again with: 'How do I upgrade my plan?'",
            "Run it with: 'Your product is terrible'",
            "Show all execution results",
            "Run evaluations on these outputs",
            "Show eval results",
        ],
    ),
    (
        "prompts",
        "optimization",
        [
            "List my prompt templates",
            "Show the first template in detail",
            "Start optimizing it",
            "Show the optimization status",
            "List all optimization runs",
        ],
    ),
    (
        "prompts",
        "simulation_flow",
        [
            "List my prompt templates",
            "Create a simulation for the first template with 5 scenarios",
            "Execute the simulation",
            "Show simulation results",
            "What were the best and worst scenarios?",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # EXPERIMENTS — A/B testing
    # ═══════════════════════════════════════════════════════════════════
    (
        "experiments",
        "full_experiment",
        [
            "List my datasets",
            "Create an experiment comparing GPT-4 and Claude on my first dataset",
            "Show the experiment status",
            "Show results",
            "Compare the variants",
            "Which model performed better?",
            "Add hallucination eval to this experiment",
            "Run the evals",
            "Show detailed stats",
        ],
    ),
    (
        "experiments",
        "rerun_and_compare",
        [
            "List my experiments",
            "Show results of the latest one",
            "Rerun it",
            "Compare the new results with the previous run",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # SIMULATION — Agent testing
    # ═══════════════════════════════════════════════════════════════════
    (
        "simulation",
        "agent_test_suite",
        [
            "Create an agent definition for a customer support bot",
            "Create a test scenario for it",
            "Create a persona for an angry customer",
            "Create a persona for a confused customer",
            "List all personas",
            "Run a test with the angry customer persona",
            "Show test results",
        ],
    ),
    (
        "simulation",
        "version_management",
        [
            "List my agents",
            "Show the first agent's details",
            "Create a new version of it",
            "List all versions",
            "Compare the versions",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # ANNOTATIONS — Review workflow
    # ═══════════════════════════════════════════════════════════════════
    (
        "annotations",
        "setup_and_review",
        [
            "Create an annotation label called 'mt-review-quality' with options: good, bad, neutral",
            "Create an annotation queue called 'mt-review-queue'",
            "Show the queue details",
            "List all annotation labels",
            "Show annotation summary",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # ADMIN — Workspace management
    # ═══════════════════════════════════════════════════════════════════
    (
        "admin",
        "workspace_overview",
        [
            "Who am I?",
            "What workspace am I in?",
            "List workspace members",
            "Show my permissions",
            "List my API keys",
            "Show my organization details",
        ],
    ),
    (
        "admin",
        "cost_audit",
        [
            "Show my cost breakdown",
            "How much have I spent this month?",
            "What's my most expensive model?",
            "Show trace analytics",
            "Any alerts configured?",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # DOCS — Learning workflows
    # ═══════════════════════════════════════════════════════════════════
    (
        "docs",
        "learn_tracing",
        [
            "How do I set up tracing in my Python app?",
            "What about for LangChain?",
            "How do I add custom metadata to traces?",
            "What evaluation metrics are available?",
            "Show me the Python SDK documentation",
        ],
    ),
    (
        "docs",
        "learn_evals",
        [
            "What evaluation metrics does Future AGI support?",
            "How do I create a custom evaluation?",
            "What's the difference between faithfulness and hallucination evals?",
            "How do I run evaluations via the SDK?",
        ],
    ),
    # ═══════════════════════════════════════════════════════════════════
    # CROSS-FEATURE — Multi-domain conversations
    # ═══════════════════════════════════════════════════════════════════
    (
        "cross",
        "data_to_eval_to_experiment",
        [
            "Create a QA dataset called 'mt-cross-test' with columns: question, answer, expected_answer",
            "Add 5 rows of QA data",
            "Add faithfulness eval to this dataset",
            "Run the evals",
            "Show results — which rows failed?",
            "Create an experiment comparing GPT-4 and Claude on this dataset",
            "Show experiment results",
            "What model should I use based on all this data?",
        ],
    ),
    (
        "cross",
        "trace_to_improve",
        [
            "Search for error traces from the last week",
            "Analyze the error patterns",
            "Search docs for how to fix the most common error",
            "Create a dataset from the error traces for debugging",
            "Show my cost breakdown — is the error costing us money?",
        ],
    ),
    (
        "cross",
        "full_workspace_audit",
        [
            "Who am I and what's my workspace?",
            "List all my datasets with row counts",
            "List all prompt templates",
            "List all experiments and their status",
            "Show cost breakdown by model",
            "Any active alerts?",
            "Search for error traces",
            "Summarize the overall health of my workspace",
        ],
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


async def run_multiturn(ws_uri, conv_id, messages, timeout_per_turn=120):
    """Run a multi-turn conversation and return results."""
    import websockets

    total_tools = []
    total_errors = []
    turn_results = []

    try:
        async with websockets.connect(
            ws_uri, max_size=10_000_000, ping_interval=30, ping_timeout=90
        ) as ws:
            for turn_num, msg in enumerate(messages):
                response = ""
                tools = []
                errors = []

                await ws.send(
                    json.dumps(
                        {
                            "type": "chat",
                            "message": msg,
                            "conversation_id": conv_id,
                            "context": {"page": "falcon-ai"},
                            "file_ids": [],
                        }
                    )
                )

                start = time.time()
                while time.time() - start < timeout_per_turn:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=20)
                        d = json.loads(raw)
                        t = d.get("type", "")
                        if t == "text_delta":
                            response += d.get("data", {}).get("delta", "")
                        elif t == "tool_call_start":
                            tools.append(d.get("data", {}).get("tool_name", ""))
                        elif t == "tool_call_result":
                            if d.get("data", {}).get("status") == "error":
                                err = d.get("data", {}).get("result_summary", "")
                                if not any(
                                    x in err.lower()
                                    for x in ["already exists", "use this id"]
                                ):
                                    errors.append(err)
                        elif t == "error":
                            errors.append(d.get("message", ""))
                            break
                        elif t == "done":
                            break
                    except asyncio.TimeoutError:
                        continue

                elapsed = time.time() - start
                has_response = len(response) > 10
                has_tools = len(tools) > 0

                turn_status = "PASS" if (has_response or has_tools) else "FAIL"
                turn_results.append(
                    {
                        "turn": turn_num + 1,
                        "message": msg[:60],
                        "status": turn_status,
                        "tools": tools,
                        "response_len": len(response),
                        "errors": errors,
                        "elapsed": round(elapsed, 1),
                    }
                )

                total_tools.extend(tools)
                total_errors.extend(errors)

                await asyncio.sleep(0.5)

    except Exception as e:
        total_errors.append(f"Connection: {e}")

    turns_passed = sum(1 for t in turn_results if t["status"] == "PASS")
    total_turns = len(turn_results)

    return {
        "status": (
            "PASS"
            if turns_passed == total_turns
            else "PARTIAL" if turns_passed > 0 else "FAIL"
        ),
        "turns_passed": turns_passed,
        "total_turns": total_turns,
        "total_tools": len(total_tools),
        "unique_tools": list(set(total_tools)),
        "errors": total_errors,
        "turn_results": turn_results,
    }


async def run_tests(tests, token, group_name):
    results = []
    suffix = f"-t{random.randint(1000,9999)}"

    for i, (group, test_id, messages) in enumerate(tests):
        # Uniquify resource names
        messages = [m.replace("mt-", f"mt{suffix}-") for m in messages]

        print(
            f"\n  [{i+1}/{len(tests)}] {group}/{test_id} ({len(messages)} turns)",
            flush=True,
        )

        conv_id = create_conv(token, f"{group_name}-{test_id}")
        ws_uri = f"ws://localhost:8001/ws/falcon-ai/?token={token}"

        result = await run_multiturn(ws_uri, conv_id, messages)
        result["test_id"] = f"{group}/{test_id}"
        results.append(result)

        emoji = (
            "✅"
            if result["status"] == "PASS"
            else "⚠️" if result["status"] == "PARTIAL" else "❌"
        )
        print(
            f"    {emoji} {result['status']} | {result['turns_passed']}/{result['total_turns']} turns | {result['total_tools']} tools | {len(result['unique_tools'])} unique",
            flush=True,
        )

        for tr in result["turn_results"]:
            te = "✅" if tr["status"] == "PASS" else "❌"
            print(
                f"      T{tr['turn']}: {te} {tr['message']}... | {len(tr['tools'])} tools | {tr['elapsed']}s",
                flush=True,
            )

    return results


def print_summary(results, name):
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    partial = sum(1 for r in results if r["status"] == "PARTIAL")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    total_turns = sum(r["total_turns"] for r in results)
    passed_turns = sum(r["turns_passed"] for r in results)
    total_tool_calls = sum(r["total_tools"] for r in results)

    print(f"\n{'='*60}")
    print(
        f"{name}: {passed}/{total} conversations passed | {partial} partial | {failed} failed"
    )
    print(f"Turns: {passed_turns}/{total_turns} ({passed_turns*100//total_turns}%)")
    print(f"Total tool calls: {total_tool_calls}")
    print(f"{'='*60}")

    if failed + partial > 0:
        print("\n❌ FAILURES/PARTIAL:")
        for r in results:
            if r["status"] != "PASS":
                print(
                    f"  {r['test_id']}: {r['status']} ({r['turns_passed']}/{r['total_turns']})"
                )
                for tr in r["turn_results"]:
                    if tr["status"] != "PASS":
                        print(
                            f"    T{tr['turn']}: {tr['message']}... | errors={tr['errors'][:1]}"
                        )

    with open(f"falcon_multiturn_{name.lower()}.json", "w") as f:
        json.dump(results, f, indent=2)


async def main():
    group = sys.argv[1] if len(sys.argv) > 1 else "all"
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 999

    if group == "all":
        tests = TESTS
    else:
        tests = [t for t in TESTS if t[0] == group]

    batch = tests[start : start + size]
    print(f"Running {len(batch)} multi-turn tests (group={group})")
    print(f"Total turns: {sum(len(t[2]) for t in batch)}")

    token = get_token()
    results = await run_tests(batch, token, f"mt-{group}")
    print_summary(results, f"MultiTurn-{group}")


if __name__ == "__main__":
    asyncio.run(main())
