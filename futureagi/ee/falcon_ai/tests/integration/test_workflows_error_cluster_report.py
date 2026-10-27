"""
Report-only script: runs each workflow test and prints the query, tools called, and response.

Run:
    docker exec backend bash -c "cd /app/backend && python ai_tools/tests/test_workflows_error_cluster_report.py"
"""

import asyncio
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")

# Silence all logging before Django setup
import logging

logging.disable(logging.CRITICAL)

import django

django.setup()

# Re-enable only critical
logging.disable(logging.WARNING)

# Silence structlog
import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.tests.fixtures import make_full_error_cluster
from tfc.constants.roles import OrganizationRoles
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
    def tool_names_called(self):
        return [tc["tool_name"] for tc in self.tool_calls]

    @property
    def failed_tools(self):
        return [tr for tr in self.tool_results if tr["status"] == "error"]


def setup_context():
    clear_workspace_context()
    org, _ = Organization.objects.get_or_create(name="Report Test Org")
    set_workspace_context(organization=org)
    user, created = User.objects.get_or_create(
        email="report-test@futureagi.com",
        defaults={
            "name": "Report Test User",
            "organization": org,
            "organization_role": OrganizationRoles.OWNER,
        },
    )
    if created:
        user.set_password("testpassword123")
        user.save()
    workspace, _ = Workspace.objects.get_or_create(
        organization=org,
        name="Report Test Workspace",
        defaults={"is_default": True, "is_active": True, "created_by": user},
    )
    set_workspace_context(workspace=workspace, organization=org, user=user)
    ctx = ToolContext(user=user, organization=org, workspace=workspace)
    return ctx, user, workspace


def run_falcon(
    tool_context, user, workspace, message, context_page="", context_info=None
):
    from ee.falcon_ai.agent import AgentLoop
    from ee.falcon_ai.models import Conversation

    conversation = Conversation.objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        title="New conversation",
    )
    agent = AgentLoop(tool_context=tool_context, conversation=conversation)
    collector = CallbackCollector()

    async def _run():
        return await agent.run(
            user_message=message,
            history_messages=[],
            send_callback=collector,
            context_page=context_page,
            context_info=context_info or {},
        )

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        loop.close()
    return result, collector


def print_report(label, query, result, collector, context_info=None):
    print(f"\n{'='*80}")
    print(f"  {label}")
    print(f"{'='*80}")
    print(f"\n📝 USER QUERY: {query}")
    if context_info:
        print(f"📍 CONTEXT: {context_info}")
    print(f"\n🔧 TOOLS CALLED ({len(collector.tool_calls)}):")
    for i, tc in enumerate(collector.tool_calls, 1):
        status = "✅"
        # find matching result
        matching = [
            tr
            for tr in collector.tool_results
            if tr.get("call_id") == tc.get("call_id")
        ]
        if matching and matching[0]["status"] == "error":
            status = "❌"
        print(f"   {i}. {status} {tc['tool_name']}({tc.get('params', {})})")
    for tr in collector.tool_results:
        if tr["status"] == "error":
            print(f"      ❌ Error: {tr['result_summary'][:200]}")
    print(f"\n🤖 FALCON RESPONSE:")
    content = result.get("content", "")
    # Print first 1500 chars
    if len(content) > 1500:
        print(f"   {content[:1500]}...")
        print(f"   [... truncated, {len(content)} chars total]")
    else:
        print(f"   {content}")
    print(
        f"\n📊 STATS: mode={result.get('mode')}, "
        f"input_tokens={result.get('input_tokens')}, "
        f"output_tokens={result.get('output_tokens')}"
    )


def main():
    ctx, user, workspace = setup_context()

    # Create test data with unique IDs to avoid conflicts on rerun
    import uuid

    suffix = uuid.uuid4().hex[:6].upper()
    cluster1, *_ = make_full_error_cluster(
        ctx, num_traces=3, cluster_id=f"KRPT{suffix}A"
    )
    cluster2, *_ = make_full_error_cluster(
        ctx, num_traces=2, cluster_id=f"KRPT{suffix}B"
    )

    # --- Test 1: List clusters ---
    q1 = "What are the most common errors in my traces this week?"
    r1, c1 = run_falcon(ctx, user, workspace, q1, context_page="tracing")
    print_report("WORKFLOW 1: List Clusters", q1, r1, c1)

    # --- Test 2: Investigate with context ---
    q2 = (
        f'Investigate error cluster {cluster1.cluster_id}: "Hallucinated Content". '
        f"What's the root cause pattern across affected traces?"
    )
    ci2 = {
        "entity_type": "error_cluster",
        "entity_id": cluster1.cluster_id,
        "path": f"/dashboard/feed/{cluster1.cluster_id}",
    }
    r2, c2 = run_falcon(
        ctx, user, workspace, q2, context_page="tracing", context_info=ci2
    )
    print_report("WORKFLOW 2: Investigate Cluster from Context", q2, r2, c2, ci2)

    # --- Test 3: "Analyze this" with context only ---
    q3 = "Analyze this error cluster. What should we fix first?"
    ci3 = {
        "entity_type": "error_cluster",
        "entity_id": cluster2.cluster_id,
        "path": f"/dashboard/feed/{cluster2.cluster_id}",
    }
    r3, c3 = run_falcon(
        ctx, user, workspace, q3, context_page="tracing", context_info=ci3
    )
    print_report("WORKFLOW 3: Context-Only ('analyze this')", q3, r3, c3, ci3)

    print(f"\n{'='*80}")
    print("  ALL DONE")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
