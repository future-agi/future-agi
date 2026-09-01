"""
E2E test: Falcon analyzes real traces with the analyze-trace-errors skill.
Uses the actual project a5b0c2b1-aa2a-49c1-b896-2fba4a9121b9 (swe_bench).

Run:
    docker exec backend bash -c "cd /app/backend && PYTHONPATH=/app/backend python ai_tools/tests/test_e2e_trace_analysis.py"
"""

import asyncio
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
logging.disable(logging.CRITICAL)

import django

django.setup()

import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL)
)

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


class CallbackCollector:
    def __init__(self):
        self.tool_calls = []
        self.tool_results = []

    async def __call__(self, event):
        etype = event.get("type", "")
        if etype == "tool_call_start":
            self.tool_calls.append(event["data"])
        elif etype == "tool_call_result":
            self.tool_results.append(event["data"])

    @property
    def tool_names(self):
        return [tc["tool_name"] for tc in self.tool_calls]

    @property
    def failed(self):
        return [tr for tr in self.tool_results if tr["status"] == "error"]


def setup():
    """Use the real futureagi org that owns the project."""
    clear_workspace_context()
    org = Organization.objects.get(id="8f22b8a3-f2c3-418e-880d-d6a72bf2b334")
    user = User.objects.filter(organization=org).first()
    workspace = Workspace.objects.get(id="1cf34c23-3c50-445d-98e6-acaa205ddba3")
    set_workspace_context(workspace=workspace, organization=org, user=user)
    print(f"  Org: {org.name}, User: {user.email}, Workspace: {workspace.name}")
    return (
        ToolContext(user=user, organization=org, workspace=workspace),
        user,
        workspace,
    )


def run_falcon(ctx, user, workspace, message):
    from ee.falcon_ai.agent import AgentLoop
    from ee.falcon_ai.models import Conversation, Skill

    conversation = Conversation.objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        title="New conversation",
    )

    skill = Skill.objects.filter(
        organization=user.organization, slug="analyze-trace-errors", is_active=True
    ).first()
    print(f"  Skill: {skill.name if skill else 'NOT FOUND'}")

    agent = AgentLoop(tool_context=ctx, conversation=conversation)
    collector = CallbackCollector()

    async def _run():
        return await agent.run(
            user_message=message,
            history_messages=[],
            send_callback=collector,
            context_page="tracing",
            skill=skill,
        )

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        loop.close()
    return result, collector


def main():
    ctx, user, workspace = setup()
    project_id = "a5b0c2b1-aa2a-49c1-b896-2fba4a9121b9"
    message = f"Analyze all traces in project {project_id} for errors."

    print(f"\n{'='*70}")
    print(f"  E2E: {message}")
    print(f"{'='*70}\n")

    result, collector = run_falcon(ctx, user, workspace, message)

    print(f"\n--- TOOL CALLS ({len(collector.tool_calls)}) ---")
    for i, tc in enumerate(collector.tool_calls, 1):
        matching = [
            tr
            for tr in collector.tool_results
            if tr.get("call_id") == tc.get("call_id")
        ]
        status = "✅" if matching and matching[0]["status"] == "completed" else "❌"
        params_short = str(tc.get("params", {}))[:100]
        print(f"  {i}. {status} {tc['tool_name']}({params_short})")

    print(f"\n--- FAILURES ({len(collector.failed)}) ---")
    for f in collector.failed:
        print(f"  ❌ {f['tool_name']}: {f['result_summary'][:200]}")

    findings = collector.tool_names.count("submit_trace_finding")
    scores = collector.tool_names.count("submit_trace_scores")
    print(f"\n--- RESULTS ---")
    print(f"  Findings submitted: {findings}")
    print(f"  Scores submitted: {scores}")
    print(f"  Total tool calls: {len(collector.tool_calls)}")
    print(f"  Failed: {len(collector.failed)}")

    from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail

    recent = TraceErrorAnalysis.objects.filter(
        agent_version="falcon-skill-1.0"
    ).order_by("-analysis_date")[:10]
    print(f"\n--- DB (falcon-skill-1.0) ---")
    for a in recent:
        details = TraceErrorDetail.objects.filter(analysis=a).count()
        print(
            f"  trace={str(a.trace_id)[:8]}... errors={a.total_errors} details={details} score={a.overall_score}"
        )

    print(f"\n--- RESPONSE ---")
    print(result.get("content", "")[:2000])

    print(f"\n--- TOKENS ---")
    print(
        f"  Input: {result.get('input_tokens')}, Output: {result.get('output_tokens')}"
    )
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
