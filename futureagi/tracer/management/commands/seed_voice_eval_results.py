"""Seed failing eval results on a voice (simulator) project's real calls.

The local DB already has voice projects with real conversation traces,
recordings and transcripts — what's missing for an Error Feed e2e test is
eval data. This command creates Pass/Fail eval configs themed for a voice
collections agent and writes FAILING EvalLogger rows (span-target, anchored
to each call's conversation span) over the project's existing traces, then
runs eval clustering so E-* clusters appear in the feed with
modality="voice" and the VoiceEvalPanel renders real call evidence.

Three failure themes → three eval configs → three clusters:
  - pii-disclosure        : agent reads sensitive data back in clear
  - compliance-disclosure : required collections disclosure never given
  - promise-accuracy      : logged payment amount/date contradicts the caller

Idempotent: rows carry ``eval_task_id = SENTINEL``; --clean (also implied by
a re-run) deletes prior seeded rows, their junction links, the eval clusters
left empty by that, and their CH centroids.

    python manage.py seed_voice_eval_results
    python manage.py seed_voice_eval_results --project-name "Mor (Morgan)"
    python manage.py seed_voice_eval_results --per-theme 25 --no-cluster
    python manage.py seed_voice_eval_results --clean
"""

from __future__ import annotations

import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace_error_analysis import ErrorClusterTraces, TraceErrorGroup

SENTINEL = "seed-voice-evals"

# (eval config name, [explanation templates]). Each template is formatted with
# a per-trace variation so explanations differ the way real judge output does.
THEMES = [
    (
        "pii-disclosure",
        [
            "The verdict is Fail because the agent read the caller's full "
            "account number ending {last4} back in clear speech instead of "
            "masking it. Sensitive payment data must never be spoken in full.",
            "Fail: while verifying identity the agent repeated the complete "
            "card number ending {last4} aloud. Only the last four digits may "
            "be referenced on a call.",
            "The agent disclosed the caller's full account number (ending "
            "{last4}) and read their email address back in clear during the "
            "call, so this evaluation fails the PII handling criteria.",
        ],
    ),
    (
        "compliance-disclosure",
        [
            "Fail: the agent never stated the required collections "
            "disclosure before discussing the {amount} balance. The "
            "disclosure must be given at the start of every call.",
            "The call fails because the mandatory debt-collection disclosure "
            "was skipped — the agent moved straight into negotiating the "
            "{amount} payment without identifying the call's purpose.",
            "No required disclosure was given at any point in this call "
            "about the {amount} debt, which violates the compliance "
            "criteria for collections conversations.",
        ],
    ),
    (
        "promise-accuracy",
        [
            "Fail: the caller agreed to pay {amount} on {date}, but the "
            "agent confirmed a different amount back to them. The logged "
            "promise does not match the caller's words.",
            "The agent recorded the payment promise as {amount} due {date}, "
            "contradicting the amount the caller actually stated earlier in "
            "the call. Promise details must match the caller's commitment.",
            "This evaluation fails because the payment date the agent "
            "confirmed ({date}) is not the date the caller agreed to, and "
            "the {amount} figure was never validated with the caller.",
        ],
    ),
]


class Command(BaseCommand):
    help = "Seed failing voice eval results + clusters for Error Feed e2e testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project-name", default="debt collection (Alex)", help="Voice project"
        )
        parser.add_argument(
            "--per-theme", type=int, default=18, help="Failing results per theme"
        )
        parser.add_argument(
            "--passing-per-theme",
            type=int,
            default=6,
            help="Passing results per theme (powers the working-call comparison)",
        )
        parser.add_argument("--seed", type=int, default=7, help="RNG seed")
        parser.add_argument(
            "--no-cluster",
            action="store_true",
            help="Seed rows only; skip running eval clustering",
        )
        parser.add_argument(
            "--clean", action="store_true", help="Tear down seeded data and exit"
        )

    def handle(self, *args, **opts):
        project = Project.objects.filter(name=opts["project_name"]).first()
        if not project:
            raise CommandError(f"Project {opts['project_name']!r} not found")

        self._clean(project)
        if opts["clean"]:
            self.stdout.write(self.style.SUCCESS("Cleaned seeded voice eval data."))
            return

        rng = random.Random(opts["seed"])

        # Conversation root spans = one per call; each seeded eval anchors to
        # its call's conversation span (voice evals are span-target).
        conv_spans = list(
            ObservationSpan.objects.filter(
                trace__project_id=project.id, observation_type="conversation"
            )
            .select_related("trace")
            .order_by("trace__created_at")
        )
        if not conv_spans:
            raise CommandError("Project has no conversation spans to evaluate")

        needed = opts["per_theme"] * len(THEMES)
        if len(conv_spans) < needed:
            self.stdout.write(
                self.style.WARNING(
                    f"Only {len(conv_spans)} calls for {needed} planned results — "
                    "themes will share calls."
                )
            )

        now = timezone.now()
        created = 0
        for theme_idx, (eval_name, templates) in enumerate(THEMES):
            config, _ = CustomEvalConfig.objects.get_or_create(
                project=project,
                name=eval_name,
                deleted=False,
                defaults={
                    "eval_template": self._template(),
                    "config": {"output": "Pass/Fail"},
                },
            )

            start = theme_idx * opts["per_theme"]
            picks = [
                conv_spans[(start + i) % len(conv_spans)]
                for i in range(opts["per_theme"])
            ]
            for span in picks:
                explanation = rng.choice(templates).format(
                    last4=f"{rng.randint(0, 9999):04d}",
                    amount=f"${rng.randint(8, 95) * 25}",
                    date=(now + timedelta(days=rng.randint(3, 30))).strftime("%B %d"),
                )
                row = EvalLogger.objects.create(
                    trace=span.trace,
                    observation_span=span,
                    target_type="span",
                    custom_eval_config=config,
                    eval_explanation=explanation,
                    output_bool=False,
                    eval_task_id=SENTINEL,
                )
                # created_at is auto_now_add — backdate via update so the
                # feed trends spread over the past week.
                EvalLogger.objects.filter(pk=row.pk).update(
                    created_at=now - timedelta(hours=rng.randint(0, 24 * 7))
                )
                created += 1

            # Passing rows on OTHER calls — the feed's working-call fallback
            # picks one of these as the comparison reference.
            pass_start = start + opts["per_theme"]
            pass_picks = [
                conv_spans[(pass_start + i) % len(conv_spans)]
                for i in range(opts["passing_per_theme"])
            ]
            for span in pass_picks:
                EvalLogger.objects.create(
                    trace=span.trace,
                    observation_span=span,
                    target_type="span",
                    custom_eval_config=config,
                    eval_explanation=(
                        "Pass: the agent handled this correctly — no violation "
                        f"of the {eval_name.replace('-', ' ')} criteria observed."
                    ),
                    output_bool=True,
                    eval_task_id=SENTINEL,
                )
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {created} eval results (incl. "
                f"{opts['passing_per_theme'] * len(THEMES)} passing) across "
                f"{len(THEMES)} configs on {project.name!r}."
            )
        )

        if opts["no_cluster"]:
            return

        from tracer.utils.eval_clustering import cluster_eval_results

        summary = cluster_eval_results(str(project.id))
        self.stdout.write(
            self.style.SUCCESS(
                f"Clustering: clustered={summary.clustered} "
                f"new_clusters={summary.new_clusters} assigned={summary.assigned}"
            )
        )

    # ------------------------------------------------------------------
    def _template(self):
        """Reuse any existing Pass/Fail-ish eval template (dev DB has them)."""
        template = (
            CustomEvalConfig.objects.filter(deleted=False)
            .exclude(eval_template=None)
            .values_list("eval_template", flat=True)
            .first()
        )
        if not template:
            raise CommandError("No EvalTemplate available to attach configs to")
        from model_hub.models.evals_metric import EvalTemplate

        return EvalTemplate.objects.get(id=template)

    def _clean(self, project):
        seeded = EvalLogger.objects.filter(
            eval_task_id=SENTINEL, trace__project_id=project.id
        )
        seeded_ids = list(seeded.values_list("id", flat=True))
        if not seeded_ids:
            return

        junctions = ErrorClusterTraces.objects.filter(eval_logger_id__in=seeded_ids)
        cluster_pks = set(junctions.values_list("cluster_id", flat=True))
        junctions.delete()

        # Drop eval clusters this seed created (those now left memberless).
        empty = TraceErrorGroup.objects.filter(
            pk__in=cluster_pks, source="eval", clusters__isnull=True
        )
        empty_cluster_ids = list(empty.values_list("cluster_id", flat=True))
        empty.delete()
        seeded.delete()

        if empty_cluster_ids:
            try:
                from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
                from tracer.queries.eval_clustering import CENTROIDS_TABLE

                db = ClickHouseVectorDB()
                ids = ",".join(f"'{c}'" for c in empty_cluster_ids)
                db.client.execute(
                    f"ALTER TABLE {CENTROIDS_TABLE} DELETE WHERE cluster_id IN ({ids})"
                )
            except Exception as exc:  # noqa: BLE001 — dev cleanup, CH optional
                self.stdout.write(
                    self.style.WARNING(f"Centroid cleanup skipped: {exc}")
                )

        self.stdout.write(
            f"Cleaned {len(seeded_ids)} seeded results, "
            f"{len(empty_cluster_ids)} clusters."
        )
