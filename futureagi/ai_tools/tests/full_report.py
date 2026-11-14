"""
Full report of Falcon trace analysis run.
Shows: main conversation, each headless trace analysis, tool calls, results, final output.

Run:
    docker exec backend bash -c 'cd /app/backend && PYTHONPATH=/app/backend python ai_tools/tests/full_report.py'
"""

import json
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
logging.disable(logging.CRITICAL)
import django

django.setup()

from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail

project_id = "a5b0c2b1-aa2a-49c1-b896-2fba4a9121b9"
project = Project.objects.get(id=project_id)

# ── Section 1: DB State ──
print("=" * 80)
print("  SECTION 1: DATABASE STATE")
print("=" * 80)

traces = Trace.objects.filter(project=project)
total = traces.count()
completed = traces.filter(error_analysis_status="completed").count()
processing = traces.filter(error_analysis_status="processing").count()
pending = traces.filter(error_analysis_status="pending").count()
failed = traces.filter(error_analysis_status="failed").count()

analyses = TraceErrorAnalysis.objects.filter(
    project=project, agent_version="falcon-skill-1.0"
)
details = TraceErrorDetail.objects.filter(
    analysis__project=project, analysis__agent_version="falcon-skill-1.0"
)

print(f"\n  Project: {project.name}")
print(f"  Total traces: {total}")
print(
    f"  Completed: {completed}  Processing: {processing}  Pending: {pending}  Failed: {failed}"
)
print(f"  Analyses (falcon-skill-1.0): {analyses.count()}")
print(f"  Error details: {details.count()}")

# ── Section 2: Per-trace analysis ──
print(f"\n{'=' * 80}")
print("  SECTION 2: PER-TRACE ANALYSIS RESULTS")
print("=" * 80)

for a in analyses.order_by("-analysis_date"):
    d_count = TraceErrorDetail.objects.filter(analysis=a).count()
    score = f"{a.overall_score:.1f}" if a.overall_score else "N/A"
    print(f"\n  Trace: {a.trace_id}")
    print(
        f"    Score: {score}  Errors: {a.total_errors}  Details: {d_count}  Priority: {a.recommended_priority}"
    )
    print(f"    Insights: {(a.insights or '')[:200]}")
    if a.factual_grounding_score:
        print(
            f"    Factual Grounding: {a.factual_grounding_score:.1f} — {(a.factual_grounding_reason or '')[:100]}"
        )
    if a.instruction_adherence_score:
        print(
            f"    Instruction Adherence: {a.instruction_adherence_score:.1f} — {(a.instruction_adherence_reason or '')[:100]}"
        )
    if a.optimal_plan_execution_score:
        print(
            f"    Optimal Plan Execution: {a.optimal_plan_execution_score:.1f} — {(a.optimal_plan_execution_reason or '')[:100]}"
        )

    # Show error details for this analysis
    for d in TraceErrorDetail.objects.filter(analysis=a):
        print(f"    ERROR {d.error_id}:")
        print(f"      Category: {d.category}")
        print(f"      Impact: {d.impact}  Urgency: {d.urgency_to_fix}")
        print(f"      Description: {(d.description or '')[:200]}")
        print(f"      Root Causes: {d.root_causes}")
        print(f"      Recommendation: {(d.recommendation or '')[:200]}")
        print(f"      Evidence: {str(d.evidence_snippets)[:200]}")

# ── Section 3: ClickHouse logs ──
print(f"\n{'=' * 80}")
print("  SECTION 3: CLICKHOUSE CONVERSATION LOGS")
print("=" * 80)

try:
    from tracer.services.clickhouse.client import ClickHouseClient

    ch = ClickHouseClient()
    rows = ch.execute_read(
        "SELECT conversation_id, trace_id, model, input_tokens, output_tokens, "
        "tool_calls, response, errors_found, overall_score, recommended_priority, "
        "started_at, completed_at "
        "FROM falcon_analysis_log "
        "WHERE project_id = %(pid)s "
        "ORDER BY started_at",
        {"pid": project_id},
    )

    if not rows:
        print("\n  No ClickHouse logs found.")
    else:
        print(f"\n  Found {len(rows)} log entries\n")
        for row in rows:
            (
                conv_id,
                tid,
                model,
                in_tok,
                out_tok,
                tc_json,
                response,
                errs,
                score,
                priority,
                started,
                completed,
            ) = row
            print(f"  --- Trace: {str(tid)[:8]} ---")
            print(f"    Conversation: {conv_id}")
            print(f"    Model: {model}  Tokens: {in_tok}+{out_tok}")
            print(f"    Errors: {errs}  Score: {score}  Priority: {priority}")
            print(f"    Time: {started} → {completed}")

            # Parse and show tool calls
            try:
                tools = json.loads(tc_json) if tc_json else []
                if tools:
                    print(f"    Tool Calls ({len(tools)}):")
                    for tc in tools:
                        status = (
                            "✅"
                            if tc.get("status") == "completed"
                            else "❌"
                            if tc.get("status") == "error"
                            else "⏳"
                        )
                        params_str = json.dumps(tc.get("params", {}), default=str)[:80]
                        print(
                            f"      {status} {tc.get('tool_name', '?')}({params_str})"
                        )
                        if tc.get("result_summary"):
                            print(f"         → {tc['result_summary'][:150]}")
                else:
                    print(f"    Tool Calls: none")
            except Exception:
                print(f"    Tool Calls: (parse error)")

            # Show response
            resp = response or ""
            if resp:
                print(f"    Response: {resp[:300]}")
            print()

except Exception as e:
    print(f"\n  ClickHouse error: {e}")

# ── Section 4: Summary ──
print(f"{'=' * 80}")
print("  SECTION 4: SUMMARY")
print("=" * 80)

with_errors = analyses.filter(total_errors__gt=0).count()
clean = analyses.filter(total_errors=0).count()
scores = [a.overall_score for a in analyses if a.overall_score is not None]

print(f"\n  Traces analyzed: {analyses.count()}/{total}")
print(f"  Clean: {clean}")
print(f"  With errors: {with_errors}")
print(f"  Total error details: {details.count()}")
if scores:
    print(f"  Score range: {min(scores):.1f} — {max(scores):.1f}")
    print(f"  Average score: {sum(scores) / len(scores):.2f}")

# Error category breakdown
categories = {}
for d in details:
    cat = d.category.split(" > ")[0] if d.category else "Unknown"
    categories[cat] = categories.get(cat, 0) + 1
if categories:
    print(f"  Error categories:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")

print(f"\n{'=' * 80}\n")
