# ruff: noqa: E402
"""Falcon AI performance benchmark for DRF bridge tools.

Runs 25 tasks (mix of single-step and multi-step) through Falcon's AgentLoop
with REAL LLM calls. Measures:
  - tool selection accuracy (did Falcon pick the right tool?)
  - tool execution latency (how fast did the bridge tool run?)
  - end-to-end task latency (LLM + tool dispatch)
  - success rate (did the agent complete the task?)
  - tool call count per task (efficiency)

Run via docker:
    docker exec ws1-backend python -m ai_tools.tests.bench_falcon_bridge
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
        "msg": "Make a new experiment project called 'bench-test-03'",
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
]


@dataclass
class TaskResult:
    id: int
    msg: str
    expected_tools: list
    actual_tools: list = field(default_factory=list)
    tool_latencies_ms: list = field(default_factory=list)
    total_latency_ms: float = 0.0
    success: bool = False
    error: str | None = None
    iterations: int = 0
    iteration_tokens: int = 0

    @property
    def tools_match(self) -> bool:
        return any(t in self.actual_tools for t in self.expected_tools)

    @property
    def all_expected_called(self) -> bool:
        return all(t in self.actual_tools for t in self.expected_tools)


async def run_task(task: dict, tool_context: ToolContext) -> TaskResult:
    result = TaskResult(
        id=task["id"], msg=task["msg"], expected_tools=task["expected_tools"]
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

    async def send_callback(event):
        events.append(event)
        ev_type = event.get("type", "")
        data = event.get("data", {})
        if ev_type == "tool_call_start":
            call_id = data.get("call_id")
            tool_name = data.get("tool_name")
            result.actual_tools.append(tool_name)
            tool_starts[call_id] = (tool_name, time.time())
        elif ev_type == "tool_call_result":
            call_id = data.get("call_id")
            if call_id in tool_starts:
                tool_name, start = tool_starts[call_id]
                latency_ms = (time.time() - start) * 1000
                result.tool_latencies_ms.append(latency_ms)

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
    result.iterations = (
        getattr(agent, "_iterations", 0)
        if hasattr(agent, "_iterations")
        else len([e for e in events if e.get("type") == "tool_call_start"])
    )
    return result


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

    print(f"\n{'=' * 80}")
    print(f"Falcon Bridge Tool Benchmark — {len(TASKS)} tasks")
    print(f"User: {user.email}")
    print(f"Workspace: {workspace.name if workspace else None}")
    print(f"{'=' * 80}\n")

    ctx = ToolContext(user=user, organization=user.organization, workspace=workspace)
    results = []

    for task in TASKS:
        print(f"Task {task['id']:2d}: {task['msg'][:70]}...")
        r = await run_task(task, ctx)
        results.append(r)
        status = "OK" if r.success and r.tools_match else "FAIL"
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
            ]
            else ""
        )
        print(f"  {tool}: {count}{marker}")

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

    return 0 if (succeeded == total and tools_match >= total * 0.8) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
