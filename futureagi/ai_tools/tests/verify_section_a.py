# ruff: noqa: E402
"""TH-5467 Section A live verification — exercises the EXACT MCP bridge tools
Falcon calls (real ToolContext), then cross-checks the DB side effect and
cleans up. Proves the UI->Falcon->MCP->DRF->DB chain (minus the LLM) for the
"no MCP tool to X" cluster.

Run:  python -m ai_tools.tests.verify_section_a
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

EMAIL = "kartik.nvj@futureagi.com"
TAG = "th5467sa"


def ctx_for(email):
    u = User.objects.select_related("organization").get(email=email)
    ws = (
        Workspace.objects.filter(
            organization=u.organization, is_default=True, is_active=True
        ).first()
        or Workspace.objects.filter(organization=u.organization).first()
    )
    return u, ToolContext(user=u, organization=u.organization, workspace=ws)


def run(name, params, ctx):
    t = registry.get(name)
    if not t:
        return None, f"MISSING:{name}"
    try:
        return t.run(params, ctx), None
    except Exception as e:
        return None, f"{type(e).__name__}:{e}"


def items(r):
    d = getattr(r, "data", None)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in (
            "results",
            "items",
            "data",
            "personas",
            "scenarios",
            "datasets",
            "projects",
            "queues",
            "templates",
        ):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def main():
    u, ctx = ctx_for(EMAIL)
    org = u.organization
    print(f"user={u.email} org={org.id}")
    P, F, S = [], [], []

    def ok(name, cond, detail=""):
        (P if cond else F).append(name)
        print(
            f"[{'PASS' if cond else 'FAIL'}] {name}"
            + (f" — {detail}" if detail else "")
        )

    def skip(name, why):
        S.append(name)
        print(f"[SKIP] {name} — {why}")

    from tracer.models.project import Project

    proj = Project.no_workspace_objects.filter(
        organization=org, trace_type="observe", deleted=False
    ).first()

    # ---- TH-5415: export_traces_csv (read) ----
    if not proj:
        skip("TH-5415", "no observe project")
    else:
        r, e = run("export_traces_csv", {"project_id": str(proj.id)}, ctx)
        body = ""
        if r is not None:
            body = (getattr(r, "content", "") or "") + str(getattr(r, "data", "") or "")
        ok(
            "TH-5415 export_traces_csv returns CSV",
            e is None and len(body) > 0,
            e or f"len={len(body)}",
        )

    # ---- TH-5387: list_personas type partition (page_size high to avoid cap) ----
    r_all, e1 = run("list_personas", {"page_size": 200}, ctx)
    r_cus, e2 = run("list_personas", {"type": "custom", "page_size": 200}, ctx)
    r_pre, e3 = run("list_personas", {"type": "prebuilt", "page_size": 200}, ctx)
    if e1 or e2 or e3:
        ok(
            "TH-5387 list_personas type filter",
            False,
            f"{e1 or ''}{e2 or ''}{e3 or ''}",
        )
    else:

        def ids(r):
            return {
                str(x.get("id"))
                for x in items(r)
                if isinstance(x, dict) and x.get("id")
            }

        a, c, p = ids(r_all), ids(r_cus), ids(r_pre)
        disjoint = c.isdisjoint(p)
        print(
            f"   counts: all={len(a)} custom={len(c)} prebuilt={len(p)} disjoint={disjoint}"
        )
        ok(
            "TH-5387 custom/prebuilt are disjoint partitions",
            disjoint and (len(c) + len(p) > 0),
            f"all={len(a)} custom={len(c)} prebuilt={len(p)}",
        )

    # ---- TH-5375: get_scenario (read graph) ----
    r_ls, e = run("list_scenarios", {"limit": 5}, ctx)
    sc = items(r_ls)
    if e or not sc:
        skip("TH-5375", e or "no scenarios")
    else:
        sid = str(sc[0].get("id"))
        r, e = run("get_scenario", {"id": sid}, ctx)
        ok(
            "TH-5375 get_scenario returns scenario",
            e is None and r is not None,
            e or sid[:8],
        )

    # ---- TH-5442: create_custom_eval_config with mapping (create + verify + cleanup) ----
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    tmpl = EvalTemplate.objects.filter(deleted=False).first()
    if not (proj and tmpl):
        skip("TH-5442", "need project + eval template")
    else:
        nm = f"{TAG}-map-{uuid.uuid4().hex[:6]}"
        mapping = {"input": "llm.input", "output": "llm.output"}
        r, e = run(
            "create_custom_eval_config",
            {
                "eval_template": str(tmpl.id),
                "project": str(proj.id),
                "name": nm,
                "mapping": mapping,
            },
            ctx,
        )
        if e:
            ok("TH-5442 create_custom_eval_config(mapping)", False, e)
        else:
            row = CustomEvalConfig.objects.filter(name=nm).first()
            persisted = bool(row) and bool(getattr(row, "mapping", None))
            ok(
                "TH-5442 eval config + mapping persisted",
                persisted,
                f"mapping={getattr(row, 'mapping', None)}",
            )
            if row:
                row.delete()

    # ---- TH-4499: create_annotation persists a row (the confirm-without-create bug) ----
    from model_hub.models.develop_annotations import Annotations

    nm = f"{TAG}-annot-{uuid.uuid4().hex[:6]}"
    r, e = run("create_annotation", {"name": nm}, ctx)
    if e:
        ok("TH-4499 create_annotation persists", False, e)
    else:
        row = Annotations.objects.filter(name=nm).first()
        ok(
            "TH-4499 annotation row exists in DB (not just chat-confirm)",
            bool(row),
            f"id={getattr(row, 'id', None)}",
        )
        if row:
            row.delete()

    # ---- TH-5576: assign annotator to a queue persists AnnotationQueueAnnotator ----
    from model_hub.models.annotation_queues import (
        AnnotationQueue,
        AnnotationQueueAnnotator,
    )

    q = AnnotationQueue.objects.filter(organization=org, deleted=False).first()
    if not q:
        skip("TH-5576", "no annotation queue")
    else:
        before = AnnotationQueueAnnotator.objects.filter(
            queue=q, user=u, deleted=False
        ).exists()
        r, e = run(
            "update_annotation_queue",
            {
                "id": str(q.id),
                "name": q.name,
                "annotator_ids": [str(u.id)],
            },
            ctx,
        )
        if e:
            ok("TH-5576 assign annotator via MCP", False, e)
        else:
            now = AnnotationQueueAnnotator.objects.filter(
                queue=q, user=u, deleted=False
            ).exists()
            ok(
                "TH-5576 AnnotationQueueAnnotator row exists after assign",
                now,
                f"before={before} after={now}",
            )
            if now and not before:
                AnnotationQueueAnnotator.objects.filter(queue=q, user=u).delete()

    # ---- TH-4666: create_composite_eval persists composite + children ----
    tmpls = list(EvalTemplate.objects.filter(deleted=False)[:2])
    if len(tmpls) < 2:
        skip("TH-4666", "need >=2 eval templates")
    else:
        nm = f"{TAG}-comp-{uuid.uuid4().hex[:6]}"
        r, e = run(
            "create_composite_eval",
            {
                "name": nm,
                "child_template_ids": [str(t.id) for t in tmpls],
            },
            ctx,
        )
        if e:
            ok("TH-4666 create_composite_eval", False, e)
        else:
            comp = EvalTemplate.objects.filter(name=nm).first()
            ok(
                "TH-4666 composite eval template created",
                bool(comp),
                f"id={getattr(comp, 'id', None)}",
            )
            if comp:
                comp.delete()

    print(f"\nSUMMARY: {len(P)} passed, {len(F)} failed, {len(S)} skipped")
    if F:
        print("FAILED:", ", ".join(F))
    if S:
        print("SKIPPED:", ", ".join(S))


if __name__ == "__main__":
    main()
