"""
Seed a mock SIMULATOR project + eval-source TraceErrorGroup for local UI testing.

Used to verify the error-feed FE/BE fixes for sim/voice projects:
  - Pattern Summary populates from bool-typed eval rollups
  - Drawer switches to VoiceDetailDrawerV2 (when project.source == 'simulator')
  - Trends KPIs / sparklines compute from bool-typed evals
  - Empty-state copy varies by cluster source

Idempotent: re-running with --reset wipes the seeded project and recreates it.
By default, reuses existing rows (safe to run repeatedly without --reset).

Usage:
    docker exec core-backend-backend-1 python manage.py seed_mock_eval_cluster
    docker exec core-backend-backend-1 python manage.py seed_mock_eval_cluster --reset
"""

import random
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    FeedIssueStatus,
    TraceErrorGroup,
)


PROJECT_NAME = "Mock Voice Sim - Eval Feed Test"
CLUSTER_ID = "E-MOCK0001"

# Eval names mirror the screenshot Kartik shared so the FE looks familiar.
EVAL_NAMES = [
    "prosody_and_intonation",
    "customer_agent_human_escalation",
    "customer_agent_conversation_quality",
    "objection_handling",
    "turn_taking_and_flow",
    "detect_hallucination",
]

# Per-eval pass rate baked in so the seed produces predictable Pattern Summary
# averages: low score = surfaces first as "worst eval".
EVAL_PASS_PROB = {
    "prosody_and_intonation": 0.95,
    "customer_agent_human_escalation": 0.10,
    "customer_agent_conversation_quality": 0.45,
    "objection_handling": 0.85,
    "turn_taking_and_flow": 0.05,
    "detect_hallucination": 0.30,
}

CLUSTER_TITLE = (
    "This evaluation is given because the interaction demonstrates a "
    "repetitive loop and a lack of responsiveness."
)


class Command(BaseCommand):
    help = "Seed a mock SIMULATOR project + eval-source cluster for UI testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--traces",
            type=int,
            default=20,
            help="Number of traces to create in the cluster (default 20).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Wipe and recreate the seeded project before seeding.",
        )
        parser.add_argument(
            "--user-email",
            type=str,
            required=True,
            help="Email of the user that should own the project. "
            "Required (User model lives on a separate DB; need an exact lookup).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        n_traces = options["traces"]

        user, org, workspace = self._bootstrap_account(options["user_email"])

        if options["reset"]:
            existing = Project.objects.filter(
                name=PROJECT_NAME, organization=org
            ).first()
            if existing:
                self.stdout.write(f"--reset: deleting existing project {existing.id}")
                existing.delete()

        project, created = Project.objects.get_or_create(
            name=PROJECT_NAME,
            organization=org,
            trace_type="observe",
            defaults={
                "model_type": "GenerativeLLM",
                "source": "simulator",
                "user": user,
                "workspace": workspace,
                "metadata": {"seeded": True, "purpose": "ui-test-eval-cluster"},
            },
        )
        if created:
            self.stdout.write(f"created project: {project.id} ({project.name})")
        else:
            self.stdout.write(f"reusing project: {project.id}")
            # Make sure source is correct in case the row pre-existed with wrong source.
            if project.source != "simulator":
                project.source = "simulator"
                project.save(update_fields=["source"])

        # CustomEvalConfigs (one per eval name)
        eval_template = EvalTemplate.objects.first()
        if eval_template is None:
            eval_template = EvalTemplate.objects.create(
                name="MockEvalTemplate",
                description="Stub template for the seed_mock_eval_cluster command.",
                organization=org,
                config={},
            )
            self.stdout.write(f"created stub EvalTemplate: {eval_template.id}")

        eval_configs: dict[str, CustomEvalConfig] = {}
        for name in EVAL_NAMES:
            cfg, _ = CustomEvalConfig.objects.get_or_create(
                project=project,
                name=name,
                defaults={"eval_template": eval_template, "config": {}},
            )
            eval_configs[name] = cfg

        # Traces + spans + EvalLogger rows
        now = timezone.now()
        trace_ids: list[str] = []
        for i in range(n_traces):
            ts = now - timedelta(minutes=random.randint(0, 60 * 24 * 5))
            trace = Trace.objects.create(
                project=project,
                name=f"sim-call-{i + 1}",
                metadata={"seeded": True, "provider": "vapi"},
                input={"text": "order never arrived"},
                output={"text": "connect customer service agent right away"},
            )
            # backdate created_at so the trends window has something to plot
            Trace.objects.filter(id=trace.id).update(created_at=ts)

            duration_s = random.randint(30, 600)
            end_ts = ts + timedelta(seconds=duration_s)
            provider_log_id = str(uuid.uuid4())

            # Mock VAPI raw_log with a 4-turn transcript so the drawer's
            # transcript tab has something to render. Keep it tiny — we want
            # structure visible, not a realistic call.
            raw_log = {
                "id": provider_log_id,
                "type": "outboundPhoneCall",
                "status": "ended",
                "endedReason": "customer-ended-call",
                "startedAt": ts.isoformat(),
                "endedAt": end_ts.isoformat(),
                "createdAt": ts.isoformat(),
                "updatedAt": end_ts.isoformat(),
                "artifact": {
                    "messages": [
                        {"role": "system", "message": "You are a customer support voice agent.", "secondsFromStart": 0},
                        {"role": "bot", "message": "Hi! How can I help you today?", "secondsFromStart": 1.2},
                        {"role": "user", "message": "My order never arrived.", "secondsFromStart": 4.5},
                        {"role": "bot", "message": "I'm sorry to hear that. Let me check your order.", "secondsFromStart": 7.0},
                        {"role": "user", "message": "It's been three days.", "secondsFromStart": 10.5},
                        {"role": "bot", "message": "I'll connect you with a customer service agent right away.", "secondsFromStart": 13.0},
                    ],
                    "transcript": (
                        "AI: Hi! How can I help you today?\n"
                        "User: My order never arrived.\n"
                        "AI: I'm sorry to hear that. Let me check your order.\n"
                        "User: It's been three days.\n"
                        "AI: I'll connect you with a customer service agent right away."
                    ),
                    "performanceMetrics": {
                        "turnLatencies": [round(random.uniform(0.4, 1.6), 2) for _ in range(3)],
                    },
                },
                "analysis": {
                    "summary": "Customer reported a missing order and was escalated to a human agent.",
                    "structuredData": {
                        "userTurns": 2,
                        "botTurns": 3,
                        "talkRatio": round(random.uniform(0.3, 0.7), 2),
                    },
                },
            }

            span = ObservationSpan.objects.create(
                id=str(uuid.uuid4()),
                trace=trace,
                project=project,
                parent_span_id=None,
                name="Vapi Call Log",
                operation_name="vapi.call",
                # `conversation` is the type the voice_call_detail view filters on.
                observation_type="conversation",
                provider="vapi",
                start_time=ts,
                end_time=end_ts,
                latency_ms=duration_s * 1000,
                span_attributes={
                    "provider": "vapi",
                    "provider_log_id": provider_log_id,
                    "providerLogId": provider_log_id,
                    "bot_wpm": 300,
                    "user_wpm": random.randint(120, 180),
                    "call.status": "ended",
                    "talk_ratio": round(random.uniform(0.3, 0.7), 2),
                    "raw_log": raw_log,
                },
            )
            ObservationSpan.objects.filter(id=span.id).update(created_at=ts)

            for name in EVAL_NAMES:
                passed = random.random() < EVAL_PASS_PROB[name]
                EvalLogger.objects.create(
                    trace=trace,
                    observation_span=span,
                    custom_eval_config=eval_configs[name],
                    output_bool=passed,
                    output_float=None,
                    eval_explanation=(
                        f"Mock {name} verdict: {'pass' if passed else 'fail'}"
                    ),
                )

            trace_ids.append(str(trace.id))

        # The cluster: one E-MOCK0001 with source=EVAL, all traces attached.
        cluster, c_created = TraceErrorGroup.objects.get_or_create(
            project=project,
            cluster_id=CLUSTER_ID,
            defaults={
                "source": ClusterSource.EVAL,
                "issue_group": "turn_taking_and_flow",
                "title": "turn_taking_and_flow",
                "combined_description": CLUSTER_TITLE,
                "combined_impact": "medium",
                "status": FeedIssueStatus.ESCALATING,
                "error_type": "turn_taking_and_flow",
                "total_events": n_traces,
                "unique_traces": n_traces,
                "error_count": n_traces,
                "first_seen": now - timedelta(days=6),
                "last_seen": now,
            },
        )
        if not c_created:
            # Refresh counts in case --traces changed
            cluster.total_events = n_traces
            cluster.unique_traces = n_traces
            cluster.error_count = n_traces
            cluster.last_seen = now
            cluster.save()

        # Junction rows
        existing_links = set(
            ErrorClusterTraces.objects.filter(cluster=cluster).values_list(
                "trace_id", flat=True
            )
        )
        for tid in trace_ids:
            if tid not in {str(x) for x in existing_links}:
                ErrorClusterTraces.objects.create(cluster=cluster, trace_id=tid)

        self.stdout.write(self.style.SUCCESS("✔ seeded"))
        self.stdout.write(f"  project_id : {project.id}")
        self.stdout.write(f"  cluster_id : {CLUSTER_ID}")
        self.stdout.write(f"  traces     : {len(trace_ids)}")
        self.stdout.write(
            "  open the Error Feed → click the cluster → verify Pattern "
            "Summary, Trends and the trace drawer."
        )

    def _bootstrap_account(self, email: str):
        """Get-or-create a User + Organization + Workspace.

        Local Docker dev DBs ship empty — no users, no orgs, no workspaces — so
        we bootstrap a minimal trio if they're missing. Idempotent: if the
        user already exists we reuse them and their default org/workspace.
        """
        from accounts.models.user import User

        user = User.objects.filter(email=email).first()
        if user is None:
            user = User.objects.create(
                email=email,
                name=email.split("@")[0] or "Local Dev",
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
            self.stdout.write(f"created user: {user.email}")
        else:
            self.stdout.write(f"reusing user: {user.email}")

        org = getattr(user, "organization", None) or Organization.objects.first()
        if org is None:
            org = Organization.objects.create(name="Local Dev Org")
            self.stdout.write(f"created org: {org.name}")
        if not user.organization_id:
            user.organization = org
            user.save(update_fields=["organization"])

        workspace = Workspace.objects.filter(organization=org).first()
        if workspace is None:
            workspace = Workspace.objects.create(
                organization=org, name="default", created_by=user
            )
            self.stdout.write(f"created workspace: {workspace.name}")

        return user, org, workspace
