"""Seed a synthetic, diagnosable failure cluster for the cluster RCA agent.

Builds an end-to-end test fixture:
  - PG: a TraceErrorGroup (the cluster card) + N Trace rows + ErrorClusterTraces
    membership (the blast radius the agent narrows within).
  - CH: spans for every trace, inserted DIRECTLY into ``spans`` with the
    denormalized trace_* columns and shredded span_attr_* maps populated
    (sidesteps the dev dictGet enrichment gap — we control trace_name /
    session_id / attributes outright).

The scenario is intentionally diagnosable: every trace exhibits the same
failure — the knowledge-base retrieval tool returns ZERO results, and the
agent fabricates a confident, ungrounded answer anyway. The signal is
discoverable through the agent's own tools:
  - read(trace, summary): the summarizer should flag the tool-empty /
    answer-confident inconsistency.
  - aggregate(span_count, group_by="attr.retrieval.kb_index"): the failure
    concentrates on the "prod-v2" index (10/12) vs "prod-v1" (2/12).
  - list/search(spans): the empty tool outputs and fabricated answers.

Idempotent: re-running cleans the prior demo cluster (matched by the fixed
cluster label) and its CH spans (matched by trace_external_id sentinel)
before reseeding with fresh UUIDs.

    python manage.py seed_rca_test_cluster
    python manage.py seed_rca_test_cluster --traces 20 --project-id <uuid>
    python manage.py seed_rca_test_cluster --clean   # tear down only
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup
from tracer.services.clickhouse.client import ClickHouseClient

# Fixed identifiers so the seed is idempotent across runs.
CLUSTER_LABEL = "E-RCADEMO"
SENTINEL = "rca-demo"  # trace_external_id marker for CH cleanup
TRACE_NAME = "customer_support_agent"

# (user question, the confidently-fabricated answer the agent returns despite
#  the KB tool returning nothing). Surface text varies; the failure is shared.
SCENARIOS = [
    ("What is the refund window for damaged items?",
     "You can return damaged items within 90 days for a full refund, no receipt required."),
    ("Does the Pro plan include SSO?",
     "Yes, the Pro plan includes SAML-based SSO at no additional cost."),
    ("How do I export my data to CSV?",
     "Go to Settings > Data > Export and choose CSV; exports complete instantly."),
    ("What's the API rate limit on the free tier?",
     "The free tier allows 10,000 requests per minute with automatic burst handling."),
    ("Can I schedule recurring reports?",
     "Yes, open any report, click the clock icon, and set a daily, weekly, or monthly cadence."),
    ("Is there a mobile app for Android?",
     "Yes, our Android app is available on the Play Store and supports full offline mode."),
    ("How long are backups retained?",
     "Backups are retained for 365 days and can be restored from any point in time."),
    ("Do you support webhooks for ticket updates?",
     "Yes, configure webhooks under Integrations; they fire within 50ms of any update."),
    ("What encryption is used at rest?",
     "All data at rest is encrypted with AES-256 and keys rotate automatically every 24 hours."),
    ("Can I bulk-invite users via email?",
     "Yes, upload a CSV of emails under Team > Invite and everyone is added instantly."),
    ("Is SOC 2 Type II available?",
     "Yes, our current SOC 2 Type II report is downloadable from the Trust Center."),
    ("How do I reset a member's 2FA?",
     "An admin can reset any member's 2FA from User Management > Security > Reset 2FA."),
]

KB_INDEX_MINORITY = {10, 11}  # these trace indexes hit prod-v1; the rest prod-v2
ERROR_TRACE_INDEXES = {2, 5, 8, 11}  # a subset also trips an explicit groundedness guard
CUSTOMER_TIERS = ["enterprise", "pro", "free"]


class Command(BaseCommand):
    help = "Seed a diagnosable failure cluster (PG cluster+traces + CH spans) for the RCA agent."

    def add_arguments(self, parser):
        parser.add_argument("--project-id", default=None,
                            help="Project UUID to attach to. Defaults to an existing project.")
        parser.add_argument("--traces", type=int, default=len(SCENARIOS),
                            help=f"How many traces to seed (max {len(SCENARIOS)}).")
        parser.add_argument("--clean", action="store_true",
                            help="Tear down the prior demo cluster and exit.")

    def handle(self, *args, **opts):
        project_id = opts["project_id"] or self._default_project_id()
        if not project_id:
            raise CommandError("No project found; pass --project-id <uuid>.")

        self._clean(project_id)
        if opts["clean"]:
            self.stdout.write(self.style.SUCCESS("Demo cluster torn down."))
            return

        n = max(1, min(opts["traces"], len(SCENARIOS)))
        rng = random.Random(42)
        now = datetime.now(timezone.utc)

        session_ids = [uuid.uuid4() for _ in range(3)]
        trace_rows: list[Trace] = []
        span_rows: list[dict] = []

        for i in range(n):
            trace_id = uuid.uuid4()
            question, answer = SCENARIOS[i]
            kb_index = "prod-v1" if i in KB_INDEX_MINORITY else "prod-v2"
            tier = CUSTOMER_TIERS[i % len(CUSTOMER_TIERS)]
            session_id = session_ids[i % len(session_ids)]
            has_error = i in ERROR_TRACE_INDEXES
            t0 = now - timedelta(hours=n - i, minutes=rng.randint(0, 59))

            trace_rows.append(Trace(
                id=trace_id, project_id=project_id, name=TRACE_NAME,
                input={"question": question}, output={"answer": answer},
                external_id=f"{SENTINEL}-{i:02d}", tags=[SENTINEL], deleted=False,
            ))
            span_rows.extend(self._build_spans(
                trace_id=trace_id, project_id=project_id, session_id=session_id,
                question=question, answer=answer, kb_index=kb_index, tier=tier,
                has_error=has_error, t0=t0,
            ))

        Trace.objects.bulk_create(trace_rows)
        inserted = ClickHouseClient().insert("spans", span_rows,
                                             columns=list(span_rows[0].keys()))

        cluster = TraceErrorGroup.objects.create(
            project_id=project_id, cluster_id=CLUSTER_LABEL,
            title="Agent fabricates confident answers when KB retrieval returns empty",
            error_type="ungrounded_fabrication", source="scanner",
            combined_impact="HIGH", combined_description=(
                "Across these traces the knowledge-base search tool returns zero "
                "results, yet the agent emits a confident, specific answer with no "
                "grounding. Failures concentrate on the prod-v2 KB index."
            ),
            error_count=n, total_events=n, unique_traces=n, unique_users=n,
            first_seen=now - timedelta(hours=n),
            last_seen=now, status="open",
        )
        ErrorClusterTraces.objects.bulk_create([
            ErrorClusterTraces(cluster=cluster, trace_id=t.id, deleted=False)
            for t in trace_rows
        ])

        self.stdout.write(self.style.SUCCESS(
            f"\nSeeded cluster {CLUSTER_LABEL} (uuid={cluster.id})\n"
            f"  project_id : {project_id}\n"
            f"  traces     : {n}\n"
            f"  CH spans   : {inserted}\n"
            f"  sessions   : {len(session_ids)}\n"
            f"  error subset (groundedness guard): {sorted(ERROR_TRACE_INDEXES)}\n"
            f"  kb_index   : prod-v2 x{n - len(KB_INDEX_MINORITY & set(range(n)))}, "
            f"prod-v1 x{len(KB_INDEX_MINORITY & set(range(n)))}\n\n"
            f"Run the agent (needs CH_ENABLED=true):\n"
            f"  ClusterAnalysisAgent(cluster_id='{CLUSTER_LABEL}', "
            f"project_id='{project_id}').run()\n"
        ))

    # ------------------------------------------------------------------ spans
    def _build_spans(self, *, trace_id, project_id, session_id, question,
                     answer, kb_index, tier, has_error, t0) -> list[dict]:
        """Four-span trace: agent root → llm plan → tool search (empty) →
        llm answer (fabricated). Optionally a 5th groundedness-guard span
        that errors."""
        tid = str(trace_id)
        root_id, plan_id, tool_id, ans_id = (str(uuid.uuid4()) for _ in range(4))

        def base(span_id, parent, name, otype, t_off, dur_ms):
            return self._span_row(
                span_id=span_id, parent=parent, trace_id=tid,
                project_id=project_id, session_id=session_id, name=name,
                otype=otype, start=t0 + timedelta(milliseconds=t_off),
                latency_ms=dur_ms,
            )

        root = base(root_id, None, TRACE_NAME, "agent", 0, 4200)
        root["input"] = json.dumps({"question": question})
        root["output"] = json.dumps({"answer": answer})
        root["span_attr_str"] = {"input.value": question, "output.value": answer,
                                 "gen_ai.span.kind": "AGENT", "customer.tier": tier}
        root["span_attr_num"] = {"session.turn": 1.0}

        plan = base(plan_id, root_id, "plan_tool_calls", "llm", 50, 600)
        plan["model"], plan["provider"] = "gpt-4o-mini", "openai"
        plan["prompt_tokens"], plan["completion_tokens"], plan["total_tokens"] = 320, 42, 362
        plan["input"] = json.dumps({"system": "You are a support agent. Use search_knowledge_base.",
                                    "question": question})
        plan["output"] = json.dumps({"tool": "search_knowledge_base", "args": {"q": question}})
        plan["span_attr_str"] = {"gen_ai.span.kind": "LLM", "input.value": question}

        # The failing dependency: tool returns ZERO results, status OK (silent).
        tool = base(tool_id, root_id, "search_knowledge_base", "tool", 700, 180)
        tool["input"] = json.dumps({"query": question, "index": kb_index, "top_k": 5})
        tool["output"] = json.dumps({"results": [], "count": 0})
        tool["span_attr_str"] = {"gen_ai.span.kind": "TOOL", "retrieval.kb_index": kb_index,
                                 "input.value": question, "output.value": '{"results": [], "count": 0}'}
        tool["span_attr_num"] = {"retrieval.result_count": 0.0, "retrieval.top_k": 5.0}

        # The fabrication: a confident answer despite empty context.
        ans = base(ans_id, root_id, "generate_answer", "llm", 900, 3100)
        ans["model"], ans["provider"] = "gpt-4o-mini", "openai"
        ans["prompt_tokens"], ans["completion_tokens"], ans["total_tokens"] = 410, 88, 498
        ans["input"] = json.dumps({"question": question, "context": "", "context_docs": 0})
        ans["output"] = json.dumps({"answer": answer})
        ans["span_attr_str"] = {"gen_ai.span.kind": "LLM", "input.value": question,
                                "output.value": answer}
        ans["span_attr_num"] = {"answer.confidence": 0.9}
        ans["span_attr_bool"] = {"answer.grounded": 0}

        spans = [root, plan, tool, ans]

        if has_error:
            guard_id = str(uuid.uuid4())
            guard = base(guard_id, root_id, "groundedness_guard", "guardrail", 4050, 120)
            guard["status"] = "ERROR"
            guard["status_message"] = "groundedness check failed: answer not supported by retrieved context (0 docs)"
            guard["input"] = json.dumps({"answer": answer, "context_docs": 0})
            guard["output"] = json.dumps({"passed": False, "score": 0.02})
            guard["span_attr_str"] = {"gen_ai.span.kind": "GUARDRAIL"}
            guard["span_attr_num"] = {"groundedness.score": 0.02}
            guard["span_attr_bool"] = {"groundedness.passed": 0}
            spans.append(guard)

        return spans

    def _span_row(self, *, span_id, parent, trace_id, project_id, session_id,
                  name, otype, start, latency_ms) -> dict:
        """A spans row pre-filled with the non-telemetry boilerplate. Telemetry
        (input/output/attrs/model/...) is layered on by the caller."""
        end = start + timedelta(milliseconds=latency_ms)
        return {
            "id": span_id,
            "trace_id": trace_id,
            "project_id": uuid.UUID(str(project_id)),
            "parent_span_id": parent,
            "name": name,
            "observation_type": otype,
            "status": "OK",
            "status_message": None,
            "start_time": start.replace(tzinfo=None),
            "end_time": end.replace(tzinfo=None),
            "latency_ms": latency_ms,
            "model": None,
            "provider": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cost": None,
            "input": "",
            "output": "",
            "span_attr_str": {},
            "span_attr_num": {},
            "span_attr_bool": {},
            "span_attributes_raw": "{}",
            "resource_attributes_raw": "{}",
            "metadata_map": {},
            "tags": "",
            "span_events": "[]",
            "schema_version": "1.0",
            "trace_name": TRACE_NAME,
            "trace_session_id": session_id,
            "trace_external_id": SENTINEL,
            "trace_tags": "",
            "created_at": start.replace(tzinfo=None),
            "updated_at": start.replace(tzinfo=None),
            "_peerdb_is_deleted": 0,
            "_peerdb_version": 1,
        }

    # --------------------------------------------------------------- teardown
    def _clean(self, project_id) -> None:
        prior = TraceErrorGroup.objects.filter(
            cluster_id=CLUSTER_LABEL, project_id=project_id
        )
        ErrorClusterTraces.objects.filter(cluster__in=prior).delete()
        prior.delete()
        Trace.objects.filter(
            project_id=project_id, external_id__startswith=SENTINEL
        ).delete()
        try:
            ClickHouseClient().execute(
                "ALTER TABLE spans DELETE WHERE trace_external_id = %(s)s",
                {"s": SENTINEL},
            )
        except Exception as exc:  # noqa: BLE001 — best-effort dev cleanup
            self.stdout.write(self.style.WARNING(f"CH cleanup skipped: {exc}"))

    @staticmethod
    def _default_project_id():
        return (
            TraceErrorGroup.objects.filter(deleted=False)
            .values_list("project_id", flat=True)
            .first()
        )
