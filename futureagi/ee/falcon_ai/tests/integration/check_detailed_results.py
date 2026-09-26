import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
logging.disable(logging.CRITICAL)
import django

django.setup()

from ee.falcon_ai.models import Conversation, Message
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail

project_id = "a5b0c2b1-aa2a-49c1-b896-2fba4a9121b9"

# 1. What happened in each trace's hidden conversation
print("=" * 80)
print("  1. WHAT HAPPENED IN EACH HIDDEN CONVERSATION")
print("=" * 80)

hidden_convos = Conversation.objects.filter(
    title__startswith="Auto-analysis",
    metadata__hidden=True,
).order_by("created_at")

for conv in hidden_convos:
    trace_id = conv.title.replace("Auto-analysis: ", "")
    msgs = Message.objects.filter(conversation=conv).order_by("created_at")

    # Find corresponding analysis
    analysis = (
        TraceErrorAnalysis.objects.filter(
            trace__id__startswith=trace_id,
            agent_version="falcon-skill-1.0",
        )
        .order_by("-analysis_date")
        .first()
    )

    print(f"\nTrace: {trace_id}")
    print(f"  Messages in conversation: {msgs.count()}")

    for msg in msgs:
        role = msg.role
        if role == "assistant":
            # Check tool calls
            tool_calls = msg.tool_calls or []
            if tool_calls:
                for tc in tool_calls:
                    status = tc.get("status", "?")
                    name = tc.get("tool_name", "?")
                    summary = tc.get("result_summary", "")[:100]
                    print(f"  [{status}] {name}: {summary}")
            content = (msg.content or "")[:200]
            if content:
                print(f"  Response: {content[:150]}")
        elif role == "user":
            print(f"  User: {(msg.content or '')[:100]}")

    if analysis:
        print(
            f"  Analysis: errors={analysis.total_errors} score={analysis.overall_score} priority={analysis.recommended_priority}"
        )
    else:
        print(f"  Analysis: (fallback - no errors found)")

# 2. The 5 error details
print("\n" + "=" * 80)
print("  2. THE 5 ERROR DETAILS")
print("=" * 80)

details = TraceErrorDetail.objects.filter(
    analysis__project_id=project_id,
    analysis__agent_version="falcon-skill-1.0",
).select_related("analysis", "analysis__trace")

for d in details:
    print(f"\n  Error {d.error_id} (trace {str(d.analysis.trace_id)[:8]}...)")
    print(f"    Category: {d.category}")
    print(f"    Impact: {d.impact}")
    print(f"    Description: {(d.description or '')[:200]}")
    print(f"    Root causes: {d.root_causes}")
    print(f"    Recommendation: {(d.recommendation or '')[:200]}")
    print(f"    Evidence: {(str(d.evidence_snippets) or '')[:200]}")

# 3. Final analysis summary
print("\n" + "=" * 80)
print("  3. ALL 29 ANALYSES SUMMARY")
print("=" * 80)

analyses = TraceErrorAnalysis.objects.filter(
    project_id=project_id,
    agent_version="falcon-skill-1.0",
).order_by("-analysis_date")

with_errors = 0
clean = 0
total_errors = 0
scores = []

for a in analyses:
    detail_count = TraceErrorDetail.objects.filter(analysis=a).count()
    score_str = f"{a.overall_score:.1f}" if a.overall_score else "N/A"
    errors_str = f"{a.total_errors} errors" if a.total_errors > 0 else "clean"
    insights = (a.insights or "")[:100]

    if a.total_errors > 0:
        with_errors += 1
        total_errors += a.total_errors
    else:
        clean += 1

    if a.overall_score:
        scores.append(a.overall_score)

    print(
        f"  {str(a.trace_id)[:8]}  score={score_str:>4s}  {errors_str:>10s}  details={detail_count}  priority={a.recommended_priority:>6s}  {insights}"
    )

print(f"\n  TOTALS:")
print(f"    Clean traces: {clean}")
print(f"    Traces with errors: {with_errors}")
print(f"    Total errors: {total_errors}")
print(f"    Avg score: {sum(scores)/len(scores):.2f}" if scores else "    No scores")
print(f"    Score range: {min(scores):.1f} - {max(scores):.1f}" if scores else "")
