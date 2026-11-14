import json
import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
logging.disable(logging.CRITICAL)
import django

django.setup()

from tracer.models.trace_error_analysis import TraceErrorAnalysis, TraceErrorDetail

project_id = "a5b0c2b1-aa2a-49c1-b896-2fba4a9121b9"

# Get all analyses
analyses = TraceErrorAnalysis.objects.filter(
    project_id=project_id, agent_version="falcon-skill-1.0"
).order_by("analysis_date")

# Get CH logs using raw clickhouse-driver
ch_logs = {}
try:
    from clickhouse_driver import Client as CHDriver

    client = CHDriver(host="clickhouse", port=9000)
    rows = client.execute(
        "SELECT toString(trace_id), toString(conversation_id), model, "
        "input_tokens, output_tokens, tool_calls, response, "
        "errors_found, overall_score, recommended_priority "
        "FROM falcon_analysis_log "
        "WHERE toString(project_id) = %(pid)s "
        "ORDER BY started_at",
        {"pid": project_id},
    )
    print(f"CH rows: {len(rows)}")
    for row in rows:
        tid = str(row[0]).strip()
        ch_logs[tid] = {
            "conversation_id": str(row[1]),
            "model": str(row[2]),
            "input_tokens": int(row[3] or 0),
            "output_tokens": int(row[4] or 0),
            "tool_calls": str(row[5] or "[]"),
            "response": str(row[6] or ""),
            "errors_found": int(row[7] or 0),
            "overall_score": float(row[8]) if row[8] is not None else None,
            "priority": str(row[9] or ""),
        }
except Exception as e:
    import traceback

    print(f"CH error: {e}")
    traceback.print_exc()

print(f"Total analyses: {analyses.count()}")
print(f"CH logs matched: {len(ch_logs)}")
print()

for i, a in enumerate(analyses, 1):
    tid = str(a.trace_id)
    details = list(TraceErrorDetail.objects.filter(analysis=a))
    ch = ch_logs.get(tid, {})

    print(f"{'=' * 80}")
    print(f"  TRACE {i}/29: {tid}")
    print(f"{'=' * 80}")
    print(
        f"  Score: {a.overall_score}  Errors: {a.total_errors}  Priority: {a.recommended_priority}"
    )
    print(f"  Insights: {(a.insights or '')[:300]}")

    if a.factual_grounding_score:
        print(
            f"  Scores: FG={a.factual_grounding_score} PS={a.privacy_and_safety_score} IA={a.instruction_adherence_score} OPE={a.optimal_plan_execution_score}"
        )

    for d in details:
        print(f"\n  ERROR {d.error_id}:")
        print(f"    Category: {d.category}")
        print(f"    Impact: {d.impact} | Urgency: {d.urgency_to_fix}")
        print(f"    Description: {(d.description or '')[:300]}")
        print(f"    Root Causes: {d.root_causes}")
        print(f"    Recommendation: {(d.recommendation or '')[:300]}")
        print(f"    Evidence: {str(d.evidence_snippets)[:300]}")

    if ch:
        print(f"\n  --- CONVERSATION LOG ---")
        print(f"  Tokens: {ch['input_tokens']} in + {ch['output_tokens']} out")
        try:
            tools = json.loads(ch["tool_calls"]) if ch["tool_calls"] else []
            print(f"  Tool Calls ({len(tools)}):")
            for tc in tools:
                status = "✅" if tc.get("status") == "completed" else "❌"
                params = json.dumps(tc.get("params", {}), default=str)[:100]
                print(f"    {status} {tc.get('tool_name', '?')}({params})")
                if tc.get("result_summary"):
                    print(f"       → {tc['result_summary'][:200]}")
        except Exception:
            print(f"  Tool Calls: (parse error)")

        resp = ch.get("response", "")
        if resp:
            print(f"\n  Falcon Response:")
            print(f"    {resp[:500]}")
    else:
        print(f"\n  --- NO CH LOG ---")

    print()
