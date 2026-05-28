"""Run the cluster RCA agent against a cluster and print the investigation.

A thin CLI harness for end-to-end testing the agent outside the SSE view —
prints each tool call / result as the loop progresses, then the findings +
synthesis. Pair with ``seed_rca_test_cluster``.

    CH_ENABLED=true python manage.py run_rca_agent --cluster E-RCADEMO
    CH_ENABLED=true python manage.py run_rca_agent --cluster E-RCADEMO --question "why are these failing?"
"""

from __future__ import annotations

import dataclasses
import json
import logging

from django.core.management.base import BaseCommand, CommandError


def _compact(value, limit: int = 600) -> str:
    s = json.dumps(value, default=str, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + f"…(+{len(s) - limit} chars)"


class Command(BaseCommand):
    help = "Run the cluster RCA agent against a cluster and print the investigation."

    def add_arguments(self, parser):
        parser.add_argument("--cluster", required=True, help="Cluster label (E-XXX) or UUID.")
        parser.add_argument("--project-id", default=None)
        parser.add_argument("--question", default=None)
        parser.add_argument("--thinking-budget", type=int, default=0,
                            help="Gemini thinking budget in tokens (0 = off).")
        parser.add_argument("--quiet-logs", action="store_true", default=True)

    def handle(self, *args, **opts):
        if opts["quiet_logs"]:
            logging.disable(logging.CRITICAL)

        from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent

        w = self.stdout.write

        def on_event(event_type: str, payload: dict) -> None:
            turn = payload.get("turn", "")
            if event_type == "tool_call":
                w(f"\n→ [t{turn}] {payload['tool']}({_compact(payload.get('args'), 300)})")
            elif event_type == "tool_result":
                res = payload.get("result")
                err = isinstance(res, dict) and res.get("is_error")
                tag = "ERR" if err else "ok "
                w(f"  ← {tag} {_compact(res, 700)}")
            elif event_type == "finding":
                w(f"\n  ★ FINDING: {_compact(payload, 900)}")
            elif event_type == "synthesis":
                w(f"\n  ✦ SYNTHESIS: {_compact(payload, 1200)}")
            elif event_type == "reasoning":
                r = payload.get("reasoning") or payload.get("content")
                if r:
                    w(f"\n  🧠 [t{turn}] {_compact(r, 1400)}")
            elif event_type == "error":
                w(self.style.ERROR(f"\n  !! ERROR t{turn}: {payload.get('message')}"))
            elif event_type == "done":
                w(self.style.WARNING(f"\n\n=== DONE: {_compact(payload)} ==="))

        try:
            agent = ClusterAnalysisAgent(
                cluster_id=opts["cluster"],
                project_id=opts["project_id"],
                question=opts["question"],
                on_event=on_event,
                thinking_budget=opts["thinking_budget"] or None,
            )
        except Exception as exc:
            raise CommandError(f"Agent init failed: {exc}")

        w(self.style.SUCCESS(
            f"Running RCA agent on cluster {agent.cluster_id} "
            f"(project {agent.project_id}, model {agent.model})\n"
        ))
        result = agent.run()

        w("\n\n" + "=" * 70)
        w(f"RESULT  cluster={result.cluster_id}  turns={result.turn_count}  "
          f"reason={result.terminated_reason}  cost=${agent.total_cost_usd:.4f}")
        if result.error:
            w(self.style.ERROR(f"ERROR: {result.error}"))

        w(f"\nFINDINGS ({len(result.findings)}):")
        for i, f in enumerate(result.findings, 1):
            d = dataclasses.asdict(f) if dataclasses.is_dataclass(f) else f
            w(f"  [{i}] {_compact(d, 1000)}")

        w("\nSYNTHESIS:")
        if result.synthesis is not None:
            s = (dataclasses.asdict(result.synthesis)
                 if dataclasses.is_dataclass(result.synthesis) else result.synthesis)
            w(json.dumps(s, default=str, indent=2, ensure_ascii=False))
        else:
            w("  (none submitted)")
        w("=" * 70)
