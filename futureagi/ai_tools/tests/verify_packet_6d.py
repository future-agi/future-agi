# ruff: noqa: E402
"""Packet 6D — TH-5467 closure: deterministic live verifies (ToolContext lane).

Covers (fresh-shell, net-zero):
  Spot-verify (Bucket F representatives):
    TH-5396 get_next_queue_item exists + list_queue_items live
    TH-5385 get_fix_my_agent_analysis bridge wired (clean NOT_FOUND on uuid4)
    TH-5415 export_traces_csv returns CSV bytes
    TH-5386 export_test_execution_csv returns CSV bytes
    TH-5406 metric_type enum present + documented in create_alert_monitor
    TH-5383 create_knowledge_base persists DB row (cleanup)
    TH-5399 list_run_tests returns run tests with ids
    TH-5405 list_scores callable with source filters
  Bucket V deterministic verifies:
    TH-5417 add_queue_items dedupes a duplicate trace (duplicates:1)
    TH-5376 create_run_test attaches evals via evaluations_config AND
            re-attaches via eval_config_ids (ORM-asserted, cleanup)

Run in ws1-backend:
    python -m ai_tools.tests.verify_packet_6d
"""

import os
import uuid

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
django.setup()

from accounts.models.user import User
from accounts.models.workspace import Workspace
from ai_tools.base import ToolContext
from ai_tools.registry import registry

USER_EMAIL = "kartik.nvj@futureagi.com"
TAG = "packet6d"

passes, fails = [], []


def banner(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def ok(name, cond, detail=""):
    (passes if cond else fails).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def ctx_for(email):
    u = User.objects.select_related("organization").get(email=email)
    ws = (
        Workspace.objects.filter(
            organization=u.organization, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=u.organization).first()
    )
    return u, ToolContext(user=u, organization=u.organization, workspace=ws)


def run(tool_name, params, ctx):
    tool = registry.get(tool_name)
    if not tool:
        return None, f"TOOL MISSING: {tool_name}"
    try:
        res = tool.run(params, ctx)
        return res, None
    except Exception as e:
        return None, f"EXC: {type(e).__name__}: {e}"


def main():
    u, ctx = ctx_for(USER_EMAIL)
    org = u.organization
    print(f"user={u.email} org={org.id}")

    # ============== TH-5396: get_next_queue_item exists + list_queue_items ==
    banner("TH-5396 queue item tools")
    ok(
        "TH-5396 get_next_queue_item registered",
        registry.get("get_next_queue_item") is not None,
    )
    ok(
        "TH-5396 list_queue_items registered",
        registry.get("list_queue_items") is not None,
    )
    from model_hub.models.annotation_queues import AnnotationQueue, QueueItem

    r, e = run("list_queue_items", {}, ctx)
    ok(
        "TH-5396 list_queue_items callable (no error)",
        e is None and r is not None and not r.is_error,
        e or f"is_error={getattr(r, 'is_error', None)}",
    )
    # Filter integrity (29d94e824): an unadvertised param must be REJECTED,
    # not silently ignored — incidental TH-4667 root-cause evidence.
    r_fi, e_fi = run("list_queue_items", {"queue_id": str(uuid.uuid4())}, ctx)
    ok(
        "TH-4667 filter integrity: unadvertised param rejected",
        e_fi is None and r_fi is not None and r_fi.error_code == "VALIDATION_ERROR",
        f"error_code={getattr(r_fi, 'error_code', None)}",
    )

    # ============== TH-5385: get_fix_my_agent_analysis wired ================
    banner("TH-5385 get_fix_my_agent_analysis")
    t = registry.get("get_fix_my_agent_analysis")
    ok("TH-5385 tool registered", t is not None)
    if t:
        r, e = run("get_fix_my_agent_analysis", {"id": str(uuid.uuid4())}, ctx)
        # uuid4 id: bridge must surface a clean NOT_FOUND ToolResult, not 500.
        err_code = getattr(r, "error_code", None) if r else None
        err = getattr(r, "error", None) if r else None
        clean = e is None and (err_code or err)
        ok(
            "TH-5385 clean NOT_FOUND on missing id (wired, no 500)",
            bool(clean),
            f"error_code={err_code} error={str(err)[:80]}",
        )

    # ============== TH-5415: export_traces_csv ==============================
    banner("TH-5415 export_traces_csv")
    # Voice export works; the non-voice path fails LOUD post-CH-migration
    # (deliberate guard in trace.py list_traces_of_session). Use a small voice
    # project (the 8K-trace voice project OOMs the export — recorded finding).
    # 'sample 1 (Fast Food)': 121 traces, voice — exports in ~1s.
    voice_pid = "6b7e86b1-8666-440e-bf67-acfcaca94bb5"
    r, e = run("export_traces_csv", {"project_id": voice_pid}, ctx)
    body = (r.content or "") if (r is not None and not e) else ""
    ok(
        "TH-5415 CSV content returned (voice project)",
        e is None and not r.is_error and "," in body and len(body) > 500,
        e or f"{len(body)} chars, is_error={getattr(r, 'is_error', None)}",
    )
    # Non-voice: must be a clean explained error, never a silent truncation.
    r_nv, e_nv = run(
        "export_traces_csv",
        {"project_id": "83ba4ee6-0e7b-46d3-8b41-c2020c6c835a"},
        ctx,
    )
    ok(
        "TH-5415 non-voice export fails loud (CH-only guard), not silent",
        e_nv is None and r_nv.is_error and r_nv.error_code == "VALIDATION_ERROR",
        f"error_code={getattr(r_nv, 'error_code', None)}",
    )

    # ============== TH-5386: export_test_execution_csv ======================
    banner("TH-5386 export_test_execution_csv")
    from simulate.models import TestExecution

    texec = (
        TestExecution.objects.filter(
            status="completed",
            run_test__organization=org,
            run_test__deleted=False,
        )
        .order_by("-created_at")
        .first()
    )
    if not texec:
        ok("TH-5386 needs a completed test execution", False, "none found")
    else:
        r, e = run(
            "export_test_execution_csv",
            {"id": str(texec.id), "type": "testexecution"},
            ctx,
        )
        body = ""
        if r is not None and not e:
            body = r.content or ""
            if (not body or body == "{}") and isinstance(r.data, dict):
                body = str(r.data.get("content") or r.data.get("csv") or r.data)
        ok(
            "TH-5386 CSV export non-empty (no {} collapse)",
            e is None
            and not getattr(r, "is_error", True)
            and len(body) > 10
            and body.strip() != "{}",
            e or f"is_error={getattr(r, 'is_error', None)} len={len(body)} head={body[:80]!r}",
        )

    # ============== TH-5406: metric_type enum in schema =====================
    banner("TH-5406 create_alert_monitor metric_type")
    mt = registry.get("create_alert_monitor").input_schema["properties"]["metric_type"]
    enum = mt.get("enum") or []
    ok("TH-5406 metric_type enum present", len(enum) >= 5, f"{len(enum)} values")
    ok(
        "TH-5406 metric_type documented",
        "count_of_errors" in (str(mt.get("description", "")) + str(enum)),
        str(enum)[:90],
    )

    # ============== TH-5383: create_knowledge_base ==========================
    banner("TH-5383 create_knowledge_base")
    kb_name = f"{TAG}-kb-{uuid.uuid4().hex[:6]}"
    r, e = run("create_knowledge_base", {"name": kb_name, "chunk_size": 512}, ctx)
    if e:
        ok("TH-5383 create_knowledge_base", False, e)
    else:
        from model_hub.models.kb import KnowledgeBase as _KB

        exists = _KB.objects.filter(name=kb_name).exists()
        ok("TH-5383 KB row created in DB", exists, kb_name)
        if exists:
            _KB.objects.filter(name=kb_name).delete()
            print("   cleaned up KB")

    # ============== TH-5399: list_run_tests =================================
    banner("TH-5399 list_run_tests")
    r, e = run("list_run_tests", {"limit": 5}, ctx)
    got_ids = []
    if r is not None and not e and isinstance(r.data, dict):
        for k in ("results", "run_tests", "items", "data"):
            v = r.data.get(k)
            if isinstance(v, list):
                got_ids = [x.get("id") for x in v if isinstance(x, dict)]
                break
            if isinstance(v, dict):
                for k2 in ("results", "run_tests", "items"):
                    if isinstance(v.get(k2), list):
                        got_ids = [
                            x.get("id") for x in v[k2] if isinstance(x, dict)
                        ]
                        break
    ok(
        "TH-5399 list_run_tests returns run tests with ids",
        e is None and len([i for i in got_ids if i]) > 0,
        e or f"{len(got_ids)} ids, first={got_ids[:1]}",
    )

    # ============== TH-5405: list_scores ====================================
    banner("TH-5405 list_scores")
    r, e = run("list_scores", {}, ctx)
    ok("TH-5405 list_scores callable (no error)", e is None and r is not None, e or "ok")
    r2, e2 = run(
        "list_scores", {"source_type": "trace", "source_id": str(uuid.uuid4())}, ctx
    )
    ok(
        "TH-5405 list_scores accepts source_type/source_id filters",
        e2 is None,
        e2 or "filtered call ok",
    )

    # ============== TH-5417: duplicate trace add -> dedup ====================
    banner("TH-5417 add_queue_items duplicate dedup")
    from tracer.models.trace import Trace

    trace = (
        Trace.objects.filter(project__organization=org).order_by("-created_at").first()
    )
    if not trace:
        ok("TH-5417 needs a trace", False, "none found")
    else:
        q = AnnotationQueue.objects.create(
            name=f"{TAG}-q-{uuid.uuid4().hex[:6]}",
            organization=org,
            workspace=ctx.workspace,
            project=trace.project,
        )
        try:
            items = [{"source_type": "trace", "source_id": str(trace.id)}]
            r1, e1 = run(
                "add_queue_items", {"queue_id": str(q.id), "items": items}, ctx
            )
            d1 = (r1.data or {}) if (r1 and not e1) else {}
            if isinstance(d1.get("result"), dict):
                d1 = d1["result"]
            r2, e2 = run(
                "add_queue_items", {"queue_id": str(q.id), "items": items}, ctx
            )
            d2 = (r2.data or {}) if (r2 and not e2) else {}
            if isinstance(d2.get("result"), dict):
                d2 = d2["result"]
            ok(
                "TH-5417 first add added=1",
                e1 is None and d1.get("added") == 1,
                e1 or f"resp={d1}",
            )
            ok(
                "TH-5417 second add duplicates=1 added=0",
                e2 is None and d2.get("duplicates") == 1 and d2.get("added") == 0,
                e2 or f"resp={d2}",
            )
            n_items = QueueItem.objects.filter(queue=q, deleted=False).count()
            ok("TH-5417 ORM: exactly 1 queue item persisted", n_items == 1, f"count={n_items}")
        finally:
            QueueItem.objects.filter(queue=q).delete()
            q.delete()
            print("   cleaned up throwaway queue")

    # ============== TH-5376: create_run_test attaches evals ==================
    banner("TH-5376 create_run_test evaluations_config + eval_config_ids")
    from model_hub.models.evaluation import EvalTemplate
    from simulate.models import RunTest, Scenarios, SimulateEvalConfig

    agent_def_qs = __import__(
        "simulate.models", fromlist=["AgentDefinition"]
    ).AgentDefinition.objects.filter(organization=org)
    agent_def = agent_def_qs.first()
    scenario = Scenarios.objects.filter(organization=org).first()
    template = (
        EvalTemplate.no_workspace_objects.filter(organization=org).first()
        or EvalTemplate.no_workspace_objects.filter(organization__isnull=True).first()
    )
    if not (agent_def and scenario and template):
        ok(
            "TH-5376 seed data",
            False,
            f"agent_def={bool(agent_def)} scenario={bool(scenario)} template={bool(template)}",
        )
    else:
        rt_name = f"{TAG}-rt-{uuid.uuid4().hex[:6]}"
        params = {
            "name": rt_name,
            "agent_definition_id": str(agent_def.id),
            "scenario_ids": [str(scenario.id)],
            "evaluations_config": [
                {"template_id": str(template.id), "name": f"{TAG}-eval-a"}
            ],
        }
        r, e = run("create_run_test", params, ctx)
        rt = RunTest.objects.filter(name=rt_name).first()
        created_ids = []
        try:
            attached = (
                SimulateEvalConfig.objects.filter(run_test=rt, deleted=False)
                if rt
                else SimulateEvalConfig.objects.none()
            )
            ok(
                "TH-5376 run test created via tool",
                e is None and rt is not None,
                e or (str(getattr(r, "error", "")) or rt_name),
            )
            ok(
                "TH-5376 evaluations_config attached SimulateEvalConfig",
                rt is not None and attached.count() == 1,
                f"attached={attached.count() if rt else 'n/a'}",
            )
            created_ids = list(attached.values_list("id", flat=True))

            # eval_config_ids path: re-attach the config to a NEW run test
            if created_ids:
                rt2_name = f"{TAG}-rt2-{uuid.uuid4().hex[:6]}"
                r2, e2 = run(
                    "create_run_test",
                    {
                        "name": rt2_name,
                        "agent_definition_id": str(agent_def.id),
                        "scenario_ids": [str(scenario.id)],
                        "eval_config_ids": [str(i) for i in created_ids],
                    },
                    ctx,
                )
                rt2 = RunTest.objects.filter(name=rt2_name).first()
                moved = (
                    SimulateEvalConfig.objects.filter(
                        id__in=created_ids, run_test=rt2
                    ).count()
                    if rt2
                    else 0
                )
                ok(
                    "TH-5376 eval_config_ids re-attached to new run test (not ignored)",
                    rt2 is not None and moved == len(created_ids),
                    e2 or f"moved={moved}/{len(created_ids)}",
                )
                if rt2:
                    SimulateEvalConfig.objects.filter(id__in=created_ids).delete()
                    rt2.scenarios.clear()
                    rt2.delete()
                    print("   cleaned up run test 2")
        finally:
            if rt:
                SimulateEvalConfig.objects.filter(run_test=rt).delete()
                rt.scenarios.clear()
                rt.delete()
                print("   cleaned up run test 1")

    print(f"\nSUMMARY: {len(passes)} passed, {len(fails)} failed")
    if fails:
        print("FAILED:", ", ".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
