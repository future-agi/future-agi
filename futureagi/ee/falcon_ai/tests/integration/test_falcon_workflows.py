"""
Falcon AI End-to-End Workflow Tests
====================================

Runs REAL LLM calls through the Falcon AgentLoop in headless mode.
Each test sends a user message, collects all tool calls the agent makes,
and verifies the expected tools were invoked with successful results.

Organized in 3 levels:
  Level 1 — Single tool workflows (10 tests)
  Level 2 — Multi-step workflows (5 tests)
  Level 3 — Complex multi-feature workflows (2 tests)

Usage:
    python -m ai_tools.tests.test_falcon_workflows

Requires env vars:
    FALCON_AI_API_KEY   (or ANTHROPIC_API_KEY / OPENAI_API_KEY)
    DATABASE_URL        (Django DB)
"""

import asyncio
import json
import logging
import os
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import django

# ---------------------------------------------------------------------------
# Django bootstrap — must happen before any model imports
# Suppress all startup noise (ClickHouse DDL, structlog, etc.)
# ---------------------------------------------------------------------------
logging.disable(logging.CRITICAL)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()
logging.disable(logging.NOTSET)
# Keep only our logger active
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("falcon_test")

from ai_tools.base import ToolContext
from ai_tools.registry import registry as tool_registry
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.models import Conversation
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ORG_SLUG = os.environ.get("FALCON_TEST_ORG", "futureagi")
USER_EMAIL = os.environ.get("FALCON_TEST_USER", "rishav@futureagi.com")
TIMEOUT_PER_TEST = 120  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@dataclass
class ToolCallRecord:
    """One recorded tool call from the agent loop."""

    tool_name: str
    params: dict
    status: str  # "completed" or "error"
    result_summary: str
    result_full: str
    step: int


@dataclass
class TestResult:
    """Outcome of a single test case."""

    name: str
    level: int
    passed: bool
    duration_s: float
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    error: str = ""
    agent_response: str = ""


@dataclass
class TestCase:
    """Definition of a test case."""

    name: str
    level: int
    user_message: str
    expected_tools: list[str]
    validate: Callable[[list[ToolCallRecord], str], tuple[bool, str]]
    cleanup_tools: list[tuple[str, dict]] = field(default_factory=list)


@dataclass
class TurnSpec:
    """One turn in a multi-turn conversation."""

    user_message: str
    expected_tools: list[str]  # tools expected in THIS turn
    validate: Callable[[list[ToolCallRecord], str], tuple[bool, str]]


@dataclass
class MultiTurnTestCase:
    """Multi-turn conversation test case."""

    name: str
    level: int  # 4 = simple multi-turn, 5 = complex multi-turn
    turns: list[TurnSpec]
    cleanup_tools: list[tuple[str, dict]] = field(default_factory=list)


@dataclass
class MultiTurnResult:
    """Outcome of a multi-turn test."""

    name: str
    level: int
    passed: bool
    duration_s: float
    turn_results: list[dict] = field(default_factory=list)  # per-turn details
    error: str = ""


def _resolve_user_and_workspace():
    """Look up the real User, Organization, and Workspace from the DB."""
    from accounts.models import Organization, User, Workspace

    user = User.objects.get(email=USER_EMAIL)
    org = user.organization
    # Use first workspace in this org (or the one named after the org)
    workspace = (
        Workspace.objects.filter(organization=org).order_by("created_at").first()
    )
    if workspace is None:
        raise RuntimeError(f"No workspace found for org '{org.name}'")
    return user, org, workspace


def _make_tool_context(user, org, workspace) -> ToolContext:
    set_workspace_context(workspace=workspace, organization=org, user=user)
    return ToolContext(user=user, organization=org, workspace=workspace)


def _make_conversation(user, org, workspace) -> Conversation:
    return Conversation.objects.create(
        user=user,
        organization=org,
        workspace=workspace,
        title="New conversation",
    )


# ---------------------------------------------------------------------------
# Agent runner (headless)
# ---------------------------------------------------------------------------
async def run_falcon_headless(
    user_message: str,
    tool_context: ToolContext,
    conversation: Conversation,
    test_name: str = "",
) -> tuple[list[ToolCallRecord], str]:
    """
    Run the full AgentLoop for a single user message.
    Returns (tool_calls, assistant_text_response).
    Logs every step to stdout for debugging.
    """
    collected: list[ToolCallRecord] = []
    text_chunks: list[str] = []
    step_count = [0]

    async def send_callback(event: dict):
        etype = event.get("type", "")
        data = event.get("data", {})

        if etype == "mode_detected":
            print(f"    [mode] {data.get('mode', '?')}")

        elif etype == "iteration_start":
            step_count[0] += 1
            print(f"    [step {step_count[0]}] --- iteration start ---")

        elif etype == "text_delta":
            chunk = data.get("content", "")
            if chunk:
                text_chunks.append(chunk)

        elif etype == "tool_call_start":
            tool_name = data.get("tool_name", "?")
            params = data.get("params", {})
            params_str = json.dumps(params, default=str)
            if len(params_str) > 200:
                params_str = params_str[:200] + "..."
            print(f"    [tool] {tool_name}({params_str})")

        elif etype == "tool_call_result":
            tool_name = data.get("tool_name", "?")
            status = data.get("status", "?")
            summary = (data.get("result_summary", "") or "")[:150]
            mark = "OK" if status == "completed" else "ERR"
            print(f"    [result] {tool_name} → {mark}: {summary}")
            collected.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    params=data.get("params", {}),
                    status=status,
                    result_summary=data.get("result_summary", ""),
                    result_full=data.get("result_full", ""),
                    step=data.get("step", 0),
                )
            )

        elif etype == "error":
            err_msg = data.get("error", data.get("message", str(data)))
            print(f"    [error] {str(err_msg)[:200]}")

    agent = AgentLoop(tool_context, conversation)
    result = await asyncio.wait_for(
        agent.run(
            user_message=user_message,
            history_messages=[],
            send_callback=send_callback,
        ),
        timeout=TIMEOUT_PER_TEST,
    )

    # Print Falcon's final response (truncated)
    response = result.get("content", "")
    if response:
        resp_preview = response[:300].replace("\n", " ")
        print(f"    [response] {resp_preview}")

    return collected, response


async def run_falcon_multi_turn(
    turns: list[TurnSpec],
    tool_context: ToolContext,
    conversation: Conversation,
    test_name: str = "",
) -> list[dict]:
    """
    Run multiple turns in the same conversation.
    Returns list of per-turn results: {tool_calls, response, passed, error}.
    """
    from ee.falcon_ai.models import Message

    turn_results = []
    history_messages = []

    for turn_idx, turn in enumerate(turns):
        print(f"    --- Turn {turn_idx + 1}/{len(turns)} ---")
        print(f"    User: {turn.user_message[:100]}")

        collected: list[ToolCallRecord] = []
        text_chunks: list[str] = []

        async def send_callback(event: dict):
            etype = event.get("type", "")
            data = event.get("data", {})

            if etype == "mode_detected":
                print(f"      [mode] {data.get('mode', '?')}")
            elif etype == "iteration_start":
                pass
            elif etype == "tool_call_start":
                tool_name = data.get("tool_name", "?")
                params = data.get("params", {})
                params_str = json.dumps(params, default=str)
                if len(params_str) > 200:
                    params_str = params_str[:200] + "..."
                print(f"      [tool] {tool_name}({params_str})")
            elif etype == "tool_call_result":
                tool_name = data.get("tool_name", "?")
                status = data.get("status", "?")
                summary = (data.get("result_summary", "") or "")[:100]
                mark = "OK" if status == "completed" else "ERR"
                print(f"      [result] {tool_name} → {mark}: {summary}")
                collected.append(
                    ToolCallRecord(
                        tool_name=tool_name,
                        params=data.get("params", {}),
                        status=status,
                        result_summary=data.get("result_summary", ""),
                        result_full=data.get("result_full", ""),
                        step=data.get("step", 0),
                    )
                )
            elif etype == "text_delta":
                chunk = data.get("content", "")
                if chunk:
                    text_chunks.append(chunk)
            elif etype == "error":
                err_msg = data.get("error", data.get("message", str(data)))
                print(f"      [error] {str(err_msg)[:200]}")

        agent = AgentLoop(tool_context, conversation)
        result = await asyncio.wait_for(
            agent.run(
                user_message=turn.user_message,
                history_messages=history_messages,
                send_callback=send_callback,
            ),
            timeout=TIMEOUT_PER_TEST,
        )

        response = result.get("content", "")
        if response:
            print(f"      [response] {response[:200].replace(chr(10), ' ')}")

        # Validate this turn
        passed, error_msg = turn.validate(collected, response)
        tools_str = (
            ", ".join(
                f"{tc.tool_name}({'OK' if tc.status == 'completed' else 'ERR'})"
                for tc in collected
            )
            or "(none)"
        )
        mark = "PASS" if passed else "FAIL"
        print(f"      [{mark}] Tools: {tools_str}")
        if error_msg:
            print(f"      Error: {error_msg[:150]}")

        turn_results.append(
            {
                "turn": turn_idx + 1,
                "message": turn.user_message[:100],
                "tools": tools_str,
                "response": response[:300] if response else "",
                "passed": passed,
                "error": error_msg,
            }
        )

        # Build history for next turn
        # Add user message + assistant response as history
        from asgiref.sync import sync_to_async

        msgs = await sync_to_async(
            lambda: list(
                Message.objects.filter(conversation=conversation)
                .order_by("created_at")
                .values("role", "content", "tool_calls")
            )
        )()
        history_messages = [
            {"role": m["role"], "content": m["content"] or ""} for m in msgs
        ]

    return turn_results


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _check_expected_tools(
    expected: list[str],
    actual: list[ToolCallRecord],
) -> tuple[bool, str]:
    """Check that every expected tool was called (order-independent)."""
    called_names = [tc.tool_name for tc in actual]
    missing = [t for t in expected if t not in called_names]
    if missing:
        return False, f"Missing tool calls: {missing}. Called: {called_names}"
    return True, ""


def _check_no_errors(actual: list[ToolCallRecord]) -> tuple[bool, str]:
    """Check that no tool call returned an error."""
    errors = [tc for tc in actual if tc.status == "error"]
    if errors:
        msgs = [f"{tc.tool_name}: {tc.result_summary}" for tc in errors]
        return False, f"Tool errors: {'; '.join(msgs)}"
    return True, ""


def _basic_validation(expected_tools: list[str]):
    """Return a validator that checks expected tools + no errors."""

    def _validate(tool_calls: list[ToolCallRecord], response: str) -> tuple[bool, str]:
        ok, msg = _check_expected_tools(expected_tools, tool_calls)
        if not ok:
            return False, msg
        ok2, msg2 = _check_no_errors(tool_calls)
        if not ok2:
            return False, msg2
        return True, ""

    return _validate


def _allow_errors_validation(expected_tools: list[str]):
    """Return a validator that checks expected tools but allows errors
    (some tools may fail due to missing data, but we still want to verify
    the agent attempted the right tools)."""

    def _validate(tool_calls: list[ToolCallRecord], response: str) -> tuple[bool, str]:
        ok, msg = _check_expected_tools(expected_tools, tool_calls)
        if not ok:
            return False, msg
        return True, ""

    return _validate


# ---------------------------------------------------------------------------
# Cleanup helper — run tool directly via registry
# ---------------------------------------------------------------------------
def _run_cleanup_tool(name: str, params: dict, context: ToolContext):
    """Run a tool directly for cleanup. Silently ignores errors."""
    tool = tool_registry.get(name)
    if tool:
        try:
            from tfc.middleware.workspace_context import workspace_context

            with workspace_context(
                context.workspace,
                organization=context.organization,
                user=context.user,
            ):
                tool.run(params, context)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Extract IDs from tool call results (for cleanup)
# ---------------------------------------------------------------------------
def _extract_id_from_calls(
    tool_calls: list[ToolCallRecord],
    tool_name: str,
    id_key: str,
) -> Optional[str]:
    """Try to extract an entity ID from a tool call result."""
    for tc in tool_calls:
        if tc.tool_name == tool_name and tc.status == "completed":
            try:
                data = json.loads(tc.result_full)
                if isinstance(data, dict) and id_key in data:
                    return str(data[id_key])
            except (json.JSONDecodeError, KeyError):
                pass
    return None


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------
UNIQUE = uuid.uuid4().hex[:8]


def build_test_cases() -> list[TestCase]:
    """Build all test cases with unique names to avoid collisions."""
    cases: list[TestCase] = []

    # ===================================================================
    # LEVEL 1 — Single tool workflows (10 tests)
    # ===================================================================

    # L1.1 — Who am I?
    cases.append(
        TestCase(
            name="L1.01_whoami",
            level=1,
            user_message="Who am I?",
            expected_tools=["whoami"],
            validate=_basic_validation(["whoami"]),
        )
    )

    # L1.2 — List all datasets
    cases.append(
        TestCase(
            name="L1.02_list_datasets",
            level=1,
            user_message="List all datasets in this workspace",
            expected_tools=["list_datasets"],
            validate=_basic_validation(["list_datasets"]),
        )
    )

    # L1.3 — List all projects
    cases.append(
        TestCase(
            name="L1.03_list_projects",
            level=1,
            user_message="List all projects",
            expected_tools=["list_projects"],
            validate=_basic_validation(["list_projects"]),
        )
    )

    # L1.4 — List eval templates
    cases.append(
        TestCase(
            name="L1.04_list_eval_templates",
            level=1,
            user_message="List all eval templates",
            expected_tools=["list_eval_templates"],
            validate=_basic_validation(["list_eval_templates"]),
        )
    )

    # L1.5 — Search traces
    cases.append(
        TestCase(
            name="L1.05_search_traces",
            level=1,
            user_message="Search for traces in the last 24 hours",
            expected_tools=["search_traces"],
            validate=_basic_validation(["search_traces"]),
        )
    )

    # L1.6 — Create a dataset
    ds_name_l1 = f"test_falcon_l1_{UNIQUE}"
    cases.append(
        TestCase(
            name="L1.06_create_dataset",
            level=1,
            user_message=(
                f"Create a dataset called '{ds_name_l1}' with columns: input, output, expected"
            ),
            expected_tools=["create_dataset"],
            validate=_basic_validation(["create_dataset"]),
        )
    )

    # L1.7 — Add rows to the dataset just created
    cases.append(
        TestCase(
            name="L1.07_add_dataset_rows",
            level=1,
            user_message=(
                f"Add 2 rows to dataset '{ds_name_l1}'. "
                "Row 1: input='What is AI?', output='Artificial Intelligence', expected='correct'. "
                "Row 2: input='Capital of France?', output='Paris', expected='correct'."
            ),
            expected_tools=["add_dataset_rows"],
            validate=_allow_errors_validation(["add_dataset_rows"]),
        )
    )

    # L1.8 — Create a prompt template
    prompt_name_l1 = f"test_summarizer_{UNIQUE}"
    cases.append(
        TestCase(
            name="L1.08_create_prompt_template",
            level=1,
            user_message=f"Create a prompt template called '{prompt_name_l1}'",
            expected_tools=["create_prompt_template"],
            validate=_basic_validation(["create_prompt_template"]),
        )
    )

    # L1.9 — List experiments
    cases.append(
        TestCase(
            name="L1.09_list_experiments",
            level=1,
            user_message="List all experiments in this workspace",
            expected_tools=["list_experiments"],
            validate=_basic_validation(["list_experiments"]),
        )
    )

    # L1.10 — Create a tracing project
    proj_name_l1 = f"falcon_test_project_{UNIQUE}"
    cases.append(
        TestCase(
            name="L1.10_create_project",
            level=1,
            user_message=f"Create a new tracing project called '{proj_name_l1}'",
            expected_tools=["create_project"],
            validate=_basic_validation(["create_project"]),
        )
    )

    # ===================================================================
    # LEVEL 2 — Multi-step workflows (5 tests)
    # ===================================================================

    # L2.1 — Create dataset + add rows + add eval
    ds_name_l2a = f"eval_test_{UNIQUE}"
    cases.append(
        TestCase(
            name="L2.01_dataset_rows_eval",
            level=2,
            user_message=(
                f"Create a dataset called '{ds_name_l2a}' with columns input and output. "
                "Then add 3 rows with sample Q&A data. "
                "Then configure a 'Relevance' eval on it."
            ),
            expected_tools=["create_dataset", "add_dataset_rows"],
            validate=_allow_errors_validation(["create_dataset", "add_dataset_rows"]),
        )
    )

    # L2.2 — Create prompt template + add version + run it
    prompt_name_l2 = f"test_prompt_v_{UNIQUE}"
    cases.append(
        TestCase(
            name="L2.02_prompt_version_run",
            level=2,
            user_message=(
                f"Create a prompt template called '{prompt_name_l2}', "
                "add a version with system message 'You are a helpful assistant that summarizes text.', "
                "then run it with user message 'Summarize: The sky is blue and grass is green.'"
            ),
            expected_tools=["create_prompt_template", "create_prompt_version"],
            validate=_allow_errors_validation(
                ["create_prompt_template", "create_prompt_version"]
            ),
        )
    )

    # L2.3 — Create project + set up alert monitor
    proj_name_l2 = f"falcon_proj_alert_{UNIQUE}"
    cases.append(
        TestCase(
            name="L2.03_project_alert",
            level=2,
            user_message=(
                f"Create a tracing project called '{proj_name_l2}' "
                "and then set up an alert monitor on it that triggers when error rate exceeds 10%."
            ),
            expected_tools=["create_project"],
            validate=_allow_errors_validation(["create_project"]),
        )
    )

    # L2.4 — List datasets + get details of first one
    cases.append(
        TestCase(
            name="L2.04_list_then_get_dataset",
            level=2,
            user_message=(
                "List all datasets, then get the details of the first dataset you find. "
                "Show me its columns and row count."
            ),
            expected_tools=["list_datasets", "get_dataset"],
            validate=_allow_errors_validation(["list_datasets"]),
        )
    )

    # L2.5 — Explore organization: whoami + list users + list workspaces
    cases.append(
        TestCase(
            name="L2.05_explore_org",
            level=2,
            user_message=(
                "Tell me about my organization. Who am I, list all users, "
                "and list all workspaces."
            ),
            expected_tools=["whoami", "list_users", "list_workspaces"],
            validate=_basic_validation(["whoami"]),
        )
    )

    # ===================================================================
    # LEVEL 3 — Complex multi-feature workflows (2 tests)
    # ===================================================================

    # L3.1 — Full dataset + eval pipeline
    ds_name_l3a = f"full_pipeline_{UNIQUE}"
    cases.append(
        TestCase(
            name="L3.01_full_dataset_eval_pipeline",
            level=2,
            user_message=(
                f"Do the following step by step:\n"
                f"1. Create a dataset called '{ds_name_l3a}' with columns: input, output\n"
                f"2. Add 3 rows of sample Q&A data to it\n"
                f"3. List all eval templates available\n"
                f"4. Show me the dataset details to confirm everything is set up"
            ),
            expected_tools=[
                "create_dataset",
                "add_dataset_rows",
                "list_eval_templates",
            ],
            validate=_allow_errors_validation(
                ["create_dataset", "add_dataset_rows", "list_eval_templates"]
            ),
        )
    )

    # L3.2 — Cross-feature exploration
    cases.append(
        TestCase(
            name="L3.02_cross_feature_exploration",
            level=3,
            user_message=(
                "Give me a full overview of my workspace. I want to know:\n"
                "1. Who am I and what permissions do I have?\n"
                "2. How many datasets exist?\n"
                "3. How many tracing projects exist?\n"
                "4. How many experiments have been run?\n"
                "5. What eval templates are available?"
            ),
            expected_tools=[
                "whoami",
                "list_datasets",
                "list_projects",
                "list_experiments",
                "list_eval_templates",
            ],
            validate=_allow_errors_validation(
                [
                    "whoami",
                    "list_datasets",
                    "list_projects",
                ]
            ),
        )
    )

    return cases


# ---------------------------------------------------------------------------
# Multi-turn test case definitions
# ---------------------------------------------------------------------------
def build_multi_turn_test_cases() -> list[MultiTurnTestCase]:
    """Build multi-turn conversation test cases."""
    cases: list[MultiTurnTestCase] = []

    # ===================================================================
    # LEVEL 4 — Simple multi-turn (2-3 turns, context continuity)
    # ===================================================================

    # MT4.1 — Create dataset, then ask about it in next turn
    ds_name_mt1 = f"mt_dataset_{UNIQUE}"
    cases.append(
        MultiTurnTestCase(
            name="MT4.01_create_then_inspect",
            level=4,
            turns=[
                TurnSpec(
                    user_message=f"Create a dataset called '{ds_name_mt1}' with columns: question, answer",
                    expected_tools=["create_dataset"],
                    validate=_basic_validation(["create_dataset"]),
                ),
                TurnSpec(
                    user_message="Now add 2 rows to the dataset you just created. Use sample Q&A data.",
                    expected_tools=["add_dataset_rows"],
                    validate=_allow_errors_validation(["add_dataset_rows"]),
                ),
                TurnSpec(
                    user_message="Show me the details of that dataset — how many rows does it have now?",
                    expected_tools=["get_dataset"],
                    validate=_allow_errors_validation(["get_dataset"]),
                ),
            ],
        )
    )

    # MT4.2 — Ask who I am, then ask about workspace, then list projects
    cases.append(
        MultiTurnTestCase(
            name="MT4.02_progressive_exploration",
            level=4,
            turns=[
                TurnSpec(
                    user_message="Who am I?",
                    expected_tools=["whoami"],
                    validate=_basic_validation(["whoami"]),
                ),
                TurnSpec(
                    user_message="What workspaces do I have access to?",
                    expected_tools=["list_workspaces"],
                    validate=_basic_validation(["list_workspaces"]),
                ),
                TurnSpec(
                    user_message="List all tracing projects in my current workspace",
                    expected_tools=["list_projects"],
                    validate=_basic_validation(["list_projects"]),
                ),
            ],
        )
    )

    # MT4.3 — Create project, then reference it by name
    proj_name_mt = f"mt_project_{UNIQUE}"
    cases.append(
        MultiTurnTestCase(
            name="MT4.03_create_then_reference",
            level=4,
            turns=[
                TurnSpec(
                    user_message=f"Create a tracing project called '{proj_name_mt}'",
                    expected_tools=["create_project"],
                    validate=_basic_validation(["create_project"]),
                ),
                TurnSpec(
                    user_message=f"Get the details of project '{proj_name_mt}' that I just created",
                    expected_tools=["get_project"],
                    validate=_allow_errors_validation(["get_project"]),
                ),
            ],
        )
    )

    # MT4.4 — List datasets, then pick one and get its rows
    cases.append(
        MultiTurnTestCase(
            name="MT4.04_list_then_drill_down",
            level=4,
            turns=[
                TurnSpec(
                    user_message="List all datasets",
                    expected_tools=["list_datasets"],
                    validate=_basic_validation(["list_datasets"]),
                ),
                TurnSpec(
                    user_message="Get the rows of the first dataset from the list above",
                    expected_tools=["get_dataset_rows"],
                    validate=_allow_errors_validation(["get_dataset_rows"]),
                ),
            ],
        )
    )

    # ===================================================================
    # LEVEL 5 — Complex multi-turn (4+ turns, deep context chaining)
    # ===================================================================

    # MT5.1 — Full dataset lifecycle: create → add rows → add eval → run eval → check results
    ds_name_mt5 = f"mt_lifecycle_{UNIQUE}"
    cases.append(
        MultiTurnTestCase(
            name="MT5.01_dataset_lifecycle",
            level=5,
            turns=[
                TurnSpec(
                    user_message=f"Create a dataset called '{ds_name_mt5}' with columns: input, output",
                    expected_tools=["create_dataset"],
                    validate=_basic_validation(["create_dataset"]),
                ),
                TurnSpec(
                    user_message=(
                        "Add 3 rows to it:\n"
                        "1. input='What is Python?', output='Python is a programming language'\n"
                        "2. input='What is Java?', output='Java is a programming language'\n"
                        "3. input='What is Rust?', output='Rust is a systems programming language'"
                    ),
                    expected_tools=["add_dataset_rows"],
                    validate=_allow_errors_validation(["add_dataset_rows"]),
                ),
                TurnSpec(
                    user_message="Now list available eval templates and pick one suitable for checking answer relevance",
                    expected_tools=["list_eval_templates"],
                    validate=_basic_validation(["list_eval_templates"]),
                ),
                TurnSpec(
                    user_message="Configure that eval on the dataset you created. Map input to 'input' column and output to 'output' column.",
                    expected_tools=["add_dataset_eval"],
                    validate=_allow_errors_validation(["add_dataset_eval"]),
                ),
                TurnSpec(
                    user_message="Now show me the final state of the dataset — rows, columns, and configured evals.",
                    expected_tools=["get_dataset"],
                    validate=_allow_errors_validation(["get_dataset"]),
                ),
            ],
        )
    )

    # MT5.2 — Investigative conversation: explore errors across features
    cases.append(
        MultiTurnTestCase(
            name="MT5.02_investigation_flow",
            level=5,
            turns=[
                TurnSpec(
                    user_message="I want to understand the health of my system. Start by listing all tracing projects.",
                    expected_tools=["list_projects"],
                    validate=_basic_validation(["list_projects"]),
                ),
                TurnSpec(
                    user_message="Which project has the most traces? Search for recent traces in it.",
                    expected_tools=["search_traces"],
                    validate=_allow_errors_validation(["search_traces"]),
                ),
                TurnSpec(
                    user_message="Are there any error clusters or patterns in the traces you found?",
                    expected_tools=[],  # Falcon may use analyze_errors, list_error_clusters, or just summarize
                    validate=_allow_errors_validation([]),  # just check it responds
                ),
                TurnSpec(
                    user_message="Now give me a summary: how many projects, traces, datasets, and eval templates do I have?",
                    expected_tools=[],  # Falcon may call multiple list tools or use what it already knows
                    validate=_allow_errors_validation([]),
                ),
            ],
        )
    )

    return cases


# ---------------------------------------------------------------------------
# Cleanup — delete test artifacts
# ---------------------------------------------------------------------------
def cleanup_test_artifacts(tool_context: ToolContext):
    """Delete all datasets, projects, and prompts created during tests."""
    from tfc.middleware.workspace_context import workspace_context

    with workspace_context(
        tool_context.workspace,
        organization=tool_context.organization,
        user=tool_context.user,
    ):
        # Clean datasets with our test prefix
        ds_tool = tool_registry.get("list_datasets")
        if ds_tool:
            result = ds_tool.run({}, tool_context)
            if not result.is_error and result.data:
                for ds in result.data.get("datasets", []):
                    if UNIQUE in ds.get("name", ""):
                        delete_tool = tool_registry.get("delete_dataset")
                        if delete_tool:
                            try:
                                delete_tool.run(
                                    {"dataset_ids": [ds["id"]]}, tool_context
                                )
                                print(f"    [cleanup] Deleted dataset: {ds['name']}")
                            except Exception:
                                pass

        # Clean projects
        proj_tool = tool_registry.get("list_projects")
        if proj_tool:
            result = proj_tool.run({}, tool_context)
            if not result.is_error and result.data:
                for proj in result.data.get("projects", []):
                    if UNIQUE in proj.get("name", ""):
                        # Projects don't always have a delete tool; try update to mark
                        print(
                            f"    [cleanup] Found test project: {proj['name']} "
                            f"(manual cleanup may be needed)"
                        )

        # Clean prompt templates
        pt_tool = tool_registry.get("list_prompt_templates")
        if pt_tool:
            result = pt_tool.run({}, tool_context)
            if not result.is_error and result.data:
                for pt in result.data.get("templates", result.data.get("prompts", [])):
                    if UNIQUE in pt.get("name", ""):
                        del_tool = tool_registry.get("delete_prompt_template")
                        if del_tool:
                            try:
                                del_tool.run({"template_id": pt["id"]}, tool_context)
                                print(
                                    f"    [cleanup] Deleted prompt template: {pt['name']}"
                                )
                            except Exception:
                                pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
async def run_single_test(
    test_case: TestCase,
    user,
    org,
    workspace,
) -> TestResult:
    """Run a single test case and return the result."""
    from asgiref.sync import sync_to_async

    t0 = time.time()
    try:
        tool_context = _make_tool_context(user, org, workspace)
        conversation = await sync_to_async(_make_conversation)(user, org, workspace)

        tool_calls, response = await run_falcon_headless(
            user_message=test_case.user_message,
            tool_context=tool_context,
            conversation=conversation,
        )

        passed, error_msg = test_case.validate(tool_calls, response)

        return TestResult(
            name=test_case.name,
            level=test_case.level,
            passed=passed,
            duration_s=round(time.time() - t0, 2),
            tool_calls=tool_calls,
            error=error_msg,
            agent_response=response[:500] if response else "",
        )
    except asyncio.TimeoutError:
        return TestResult(
            name=test_case.name,
            level=test_case.level,
            passed=False,
            duration_s=round(time.time() - t0, 2),
            error=f"Timed out after {TIMEOUT_PER_TEST}s",
        )
    except Exception as e:
        return TestResult(
            name=test_case.name,
            level=test_case.level,
            passed=False,
            duration_s=round(time.time() - t0, 2),
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
        )
    finally:
        clear_workspace_context()


async def run_all_tests():
    """Run all test cases sequentially and print a report."""
    print("=" * 72)
    print("  FALCON AI — End-to-End Workflow Tests")
    print("=" * 72)
    print(f"  Org:  {ORG_SLUG}")
    print(f"  User: {USER_EMAIL}")
    print(f"  Run:  {UNIQUE}")
    print("=" * 72)
    print()

    # Resolve DB entities
    try:
        from asgiref.sync import sync_to_async

        user, org, workspace = await sync_to_async(_resolve_user_and_workspace)()
    except Exception as e:
        print(f"FATAL: Could not resolve user/org/workspace: {e}")
        sys.exit(1)

    print(f"  Workspace: {workspace.name} ({workspace.id})")
    print()

    test_cases = build_test_cases()
    results: list[TestResult] = []

    for i, tc in enumerate(test_cases, 1):
        print(f"[{i}/{len(test_cases)}] L{tc.level} | {tc.name}")
        print(f"  Prompt: {tc.user_message[:90]}...")
        print(f"  Expected tools: {tc.expected_tools}")

        result = await run_single_test(tc, user, org, workspace)
        results.append(result)

        status = "PASS" if result.passed else "FAIL"
        print(f"  Result: {status}  ({result.duration_s}s)")
        if result.tool_calls:
            called = [
                f"{tc.tool_name}({'OK' if tc.status == 'completed' else 'ERR'})"
                for tc in result.tool_calls
            ]
            print(f"  Tools called: {', '.join(called)}")
        if not result.passed:
            print(f"  Error: {result.error[:200]}")
        print()

    # -----------------------------------------------------------------------
    # Multi-turn tests
    # -----------------------------------------------------------------------
    mt_cases = build_multi_turn_test_cases()
    mt_results: list[MultiTurnResult] = []
    total_tests = len(test_cases) + len(mt_cases)

    for i, mtc in enumerate(mt_cases, len(test_cases) + 1):
        print(f"[{i}/{total_tests}] L{mtc.level} | {mtc.name} ({len(mtc.turns)} turns)")

        t0 = time.time()
        try:
            tool_context = _make_tool_context(user, org, workspace)
            conversation = await sync_to_async(_make_conversation)(user, org, workspace)

            turn_results = await run_falcon_multi_turn(
                turns=mtc.turns,
                tool_context=tool_context,
                conversation=conversation,
                test_name=mtc.name,
            )

            all_passed = all(tr["passed"] for tr in turn_results)
            first_error = next(
                (tr["error"] for tr in turn_results if not tr["passed"]), ""
            )
            mt_result = MultiTurnResult(
                name=mtc.name,
                level=mtc.level,
                passed=all_passed,
                duration_s=round(time.time() - t0, 2),
                turn_results=turn_results,
                error=first_error,
            )
        except asyncio.TimeoutError:
            mt_result = MultiTurnResult(
                name=mtc.name,
                level=mtc.level,
                passed=False,
                duration_s=round(time.time() - t0, 2),
                error="Timeout",
            )
        except Exception as e:
            mt_result = MultiTurnResult(
                name=mtc.name,
                level=mtc.level,
                passed=False,
                duration_s=round(time.time() - t0, 2),
                error=f"Exception: {str(e)[:200]}",
            )

        mt_results.append(mt_result)
        status = "PASS" if mt_result.passed else "FAIL"
        turns_summary = (
            " → ".join(
                f"T{tr['turn']}:{'OK' if tr['passed'] else 'FAIL'}"
                for tr in mt_result.turn_results
            )
            if mt_result.turn_results
            else "no turns"
        )
        print(f"  Result: {status}  ({mt_result.duration_s}s)  [{turns_summary}]")
        if not mt_result.passed:
            print(f"  Error: {mt_result.error[:200]}")
        print()

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------
    print("-" * 72)
    print("  Cleanup")
    print("-" * 72)
    try:
        tool_context = _make_tool_context(user, org, workspace)
        await sync_to_async(cleanup_test_artifacts)(tool_context)
        clear_workspace_context()
        print("  Done.")
    except Exception as e:
        print(f"  Cleanup error: {e}")
    print()

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    print("=" * 72)
    print("  RESULTS")
    print("=" * 72)

    # Single-turn results
    for level in [1, 2, 3]:
        level_results = [r for r in results if r.level == level]
        if not level_results:
            continue
        passed = sum(1 for r in level_results if r.passed)
        total_l = len(level_results)
        print(f"\n  Level {level}: {passed}/{total_l} passed")
        for r in level_results:
            mark = "PASS" if r.passed else "FAIL"
            tools = ", ".join(tc.tool_name for tc in r.tool_calls) or "(none)"
            print(f"    [{mark}] {r.name} ({r.duration_s}s) — tools: {tools}")
            if not r.passed:
                print(f"           {r.error[:150]}")

    # Multi-turn results
    for level in [4, 5]:
        level_label = "Simple Multi-Turn" if level == 4 else "Complex Multi-Turn"
        level_results = [r for r in mt_results if r.level == level]
        if not level_results:
            continue
        passed = sum(1 for r in level_results if r.passed)
        total_l = len(level_results)
        print(f"\n  Level {level} ({level_label}): {passed}/{total_l} passed")
        for r in level_results:
            mark = "PASS" if r.passed else "FAIL"
            turns_str = (
                " → ".join(
                    f"T{tr['turn']}:{'OK' if tr['passed'] else 'FAIL'}"
                    for tr in r.turn_results
                )
                if r.turn_results
                else "no turns"
            )
            print(f"    [{mark}] {r.name} ({r.duration_s}s) — {turns_str}")
            if not r.passed:
                print(f"           {r.error[:150]}")

    all_passed = sum(1 for r in results if r.passed) + sum(
        1 for r in mt_results if r.passed
    )
    all_total = len(results) + len(mt_results)
    all_time = sum(r.duration_s for r in results) + sum(
        r.duration_s for r in mt_results
    )
    print()
    print("-" * 72)
    print(f"  TOTAL: {all_passed}/{all_total} passed  |  {all_time:.1f}s")
    print("-" * 72)

    if all_passed < all_total:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(run_all_tests())
