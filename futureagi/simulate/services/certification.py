"""Production certification campaign service layer.

Owns everything the Temporal workflow and APIs need from the Django side:
campaign admission/creation, case job-payload construction, evidence
reconciliation, failure classification, and the notification outbox.

Rules carried over from the certification plan:

- A campaign is uniquely identified by (organization, deployment_id,
  suite_digest); re-posting the same deployment+suite returns the existing
  campaign exactly once.
- Each case becomes an ordinary HostedHarnessJob submitted through
  ``submit_hosted_harness_job`` with an exact-once idempotency key.
- Job state alone is never success — ``reconcile_case`` verifies the evidence
  checklist before a case can pass.
- Notifications go through the outbox; delivery failure never changes results.
"""

from __future__ import annotations

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from simulate.certification.manifest import (
    CertificationManifestError,
    campaign_idempotency_key,
    canonical_digest,
    case_idempotency_key,
    expand_enabled_cases,
    load_suite,
    pending_summary,
    require_admission_ready,
    suite_digest,
    validate_manifest,
)

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 30

# Blocking classes end a case as failed; exploratory evaluation problems only
# alert. Mirrors the plan's failure taxonomy.
BLOCKING_FAILURE_CLASSES = frozenset(
    {
        "configuration",
        "source",
        "authoring",
        "environment",
        "target",
        "transport",
        "evidence",
        "cleanup",
        "stable_behavior",
    }
)

_RECORDING_KINDS = {
    "recording_combined",
    "recording_stereo",
    "recording_customer",
    "recording_assistant",
}
_VOICE_RUN_TESTS = {"l2_audio_room_speak_l2"}

# HostedHarnessJob terminal states (job.State mirrors these strings).
_JOB_TERMINAL = {"completed", "failed", "canceled"}
_JOB_FAILED = {"failed"}

# Attempt.terminal_failure domains -> campaign failure classes.
_TERMINAL_DOMAIN_CLASS = {
    "infrastructure": "environment",
    "target": "target",
    "transport": "transport",
    "authoring": "authoring",
    "source": "source",
    "configuration": "configuration",
}


class CertificationError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# admission / creation
# ---------------------------------------------------------------------------


def validated_suite(suite_key: str) -> dict:
    doc = load_suite(suite_key)
    try:
        return validate_manifest(doc)
    except CertificationManifestError as exc:
        raise CertificationError(exc.code, exc.message, status_code=422) from exc


def create_campaign(
    organization,
    workspace,
    *,
    deployment_id: str,
    deployment_identity: dict,
    suite_key: str,
    requested_by: str = "",
):
    """Idempotent campaign admission. Returns (campaign, created)."""
    from simulate.models import CertificationCampaign, CertificationCase
    from simulate.temporal.client import start_certification_workflow

    normalized = validated_suite(suite_key)
    try:
        require_admission_ready(normalized)
    except CertificationManifestError as exc:
        raise CertificationError(exc.code, exc.message, status_code=422) from exc

    digest = suite_digest(normalized)
    idem_key = campaign_idempotency_key(deployment_id, digest)
    case_specs = expand_enabled_cases(normalized)

    try:
        with transaction.atomic():
            campaign, created = (
                CertificationCampaign.no_workspace_objects.get_or_create(
                    organization=organization,
                    deployment_id=deployment_id,
                    suite_digest=digest,
                    defaults={
                        "workspace": workspace,
                        "suite_key": suite_key,
                        "deployment_identity": deployment_identity,
                        "normalized_suite": normalized,
                        "idempotency_key": idem_key,
                        "cases_total": len(case_specs),
                        "requested_by": requested_by,
                    },
                )
            )
            if created:
                for i, spec in enumerate(case_specs):
                    CertificationCase.no_workspace_objects.create(
                        campaign=campaign,
                        case_key=spec["case_key"],
                        sequence=i,
                        idempotency_key=case_idempotency_key(
                            deployment_id, digest, spec["case_key"]
                        ),
                        spec=spec,
                    )
                enqueue_notification(
                    campaign,
                    None,
                    "campaign_started",
                    {
                        "suite_key": suite_key,
                        "suite_digest": digest,
                        "deployment_id": deployment_id,
                        "cases_total": len(case_specs),
                        "enabled_cases": [s["case_key"] for s in case_specs],
                        "pending": pending_summary(normalized)["disabled"],
                    },
                )
    except IntegrityError:
        # Concurrent admission of the same deployment+suite: the first writer
        # wins; return the existing campaign.
        campaign = CertificationCampaign.no_workspace_objects.get(
            organization=organization,
            deployment_id=deployment_id,
            suite_digest=digest,
        )
        created = False

    if created:
        start_certification_workflow(str(campaign.id))
        logger.info(
            "certification_campaign_total",
            state="created",
            campaign_id=str(campaign.id),
            suite_key=suite_key,
        )
    return campaign, created


def request_campaign_cancel(campaign) -> None:
    from simulate.temporal.client import cancel_certification_workflow

    campaign.cancel_requested_at = timezone.now()
    if campaign.state == CertificationState.RUNNING:
        campaign.state = CertificationState.CANCELING
    elif campaign.state == CertificationState.PENDING:
        campaign.state = CertificationState.CANCELED
        campaign.finished_at = timezone.now()
    campaign.save(
        update_fields=["cancel_requested_at", "state", "finished_at", "updated_at"]
    )
    cancel_certification_workflow(str(campaign.id))


# CertificationState alias kept import-light for callers.
from simulate.models import CertificationCampaign as _CC  # noqa: E402

CertificationState = _CC.State


# ---------------------------------------------------------------------------
# case job payload
# ---------------------------------------------------------------------------


def build_case_job_payload(case, campaign) -> dict:
    """Materialize a HostedHarnessJob payload from the expanded case spec.

    The payload passes through the same create serializer the public API uses,
    so certification jobs are indistinguishable from hand-submitted ones.
    Certification provenance rides in ``metadata.certification`` (the ALK
    contract requires preserving it end-to-end).
    """
    from simulate.serializers.harness_job import HarnessJobCreateSerializer

    spec = case.spec
    agent = spec["agent"]
    target = spec["target"]
    interactions = spec["interactions"]

    source = agent.get("source") or {}
    if source.get("repository"):
        repo = source["repository"]
        for prefix in ("https://github.com/", "git@github.com:"):
            if repo.startswith(prefix):
                repo = repo[len(prefix) :]
        repo = repo.removesuffix(".git")
        src = {
            "kind": "github",
            "repository": repo,
            "ref": source["commit_sha"],
            "commit_sha": source["commit_sha"],
            "visibility": "public",
        }
        if source.get("path"):
            src["path"] = source["path"]
    else:
        src = {"kind": "remote", "endpoint": target["ref"]}

    secret_refs = {}
    secrets_seen = set()
    for i in interactions:
        for ref in i.get("secret_ref_set", []):
            alias = ref["key"]
            if alias not in secrets_seen:
                secrets_seen.add(alias)
                secret_refs[alias] = {
                    "manager": ref["manager"],
                    "key": ref["key"],
                    "purpose": ref["purpose"],
                }

    defaults = campaign.normalized_suite.get("defaults") or {}
    payload = {
        "schema_version": "futureagi.harness-job.v1",
        "source": src,
        "agent": {
            "connector": agent.get("connector", "auto"),
            "config": {"target_ref": target["ref"]},
            "secret_refs": secret_refs,
        },
        "scenario_count": len(interactions),
        "seed": interactions[0].get("seed"),
        "runtime": {
            "isolation": "dedicated_vm",
            "cpu_units": 4,
            "memory_mb": 8192,
            "parallelism": 1,
            "concurrency_weight": 1,
            "max_duration_seconds": defaults.get("max_duration_seconds", 7200),
            "network_policy": "live",
        },
        "security": {
            "untrusted_source": True,
            "read_only_source": True,
            "allow_privileged": False,
            "allow_host_runtime_control": False,
            "allowed_egress_domains": [],
        },
        "retry": {
            "max_infrastructure_attempts": 2,
            "initial_backoff_seconds": 5,
            "max_backoff_seconds": 60,
            "retryable_domains": ["infrastructure"],
        },
        "artifacts": {
            "level": (defaults.get("artifacts") or {}).get("level", "full"),
            "retention_days": 30,
            "allow_bundle_download": True,
            "max_artifact_bytes": 0,
        },
        "metadata": {
            "certification": {
                "campaign_id": str(campaign.id),
                "case_key": case.case_key,
                "suite_key": campaign.suite_key,
                "suite_digest": campaign.suite_digest,
                "deployment_id": campaign.deployment_id,
                "run_test_id": spec["run_test_id"],
                "authorization": spec["authorization"],
                "interactions": interactions,
            }
        },
    }
    serializer = HarnessJobCreateSerializer(data=payload)
    if not serializer.is_valid():
        raise CertificationError(
            "case_payload_invalid",
            f"case {case.case_key}: manifest materialized an invalid job "
            f"payload: {serializer.errors}",
            status_code=422,
        )
    return serializer.validated_data


# ---------------------------------------------------------------------------
# reconciliation — job state alone is never success
# ---------------------------------------------------------------------------


def reconcile_case(case) -> dict:
    """Verify the evidence checklist for a terminal case.

    Returns {ok, checks: {name: {ok, detail}}, missing: [...], failure_class}.
    """
    from simulate.models import (
        HostedHarnessArtifact,
        HostedHarnessCleanupReceipt,
        HostedHarnessConversationTranscript,
        HostedHarnessExecution,
        HostedHarnessReceipt,
        HostedHarnessScenario,
    )

    job = case.hosted_harness_job
    spec = case.spec
    interactions = spec.get("interactions") or []
    expected = len(interactions) or 2
    voice = spec.get("run_test_id") in _VOICE_RUN_TESTS
    declared_evidence = any(
        i.get("expected_tools") or i.get("expected_state") for i in interactions
    )

    checks: dict[str, dict] = {}
    if job is None:
        return {
            "ok": False,
            "checks": {"job_submitted": {"ok": False, "detail": "no job linked"}},
            "missing": ["job_submitted"],
            "failure_class": "environment",
        }

    def _record(name, ok, detail=""):
        checks[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            logger.info(
                "certification_artifact_missing_total",
                kind=name,
                case_key=case.case_key,
            )

    _record(
        "job_terminal",
        job.state in _JOB_TERMINAL,
        f"job state {job.state}",
    )

    attempt = (
        job.attempts.order_by("-attempt_number").first()
        if hasattr(job, "attempts")
        else None
    )
    expected_snapshot = (case.campaign.deployment_identity or {}).get(
        "alk_snapshot_digest"
    )
    if attempt is None:
        _record("snapshot_digest", False, "no attempt recorded")
    elif expected_snapshot:
        ok = attempt.snapshot_digest == expected_snapshot
        _record(
            "snapshot_digest",
            ok,
            f"attempt {attempt.snapshot_digest} vs expected {expected_snapshot}",
        )
        if not ok:
            logger.info(
                "certification_snapshot_mismatch_total",
                case_key=case.case_key,
                expected=expected_snapshot,
                actual=attempt.snapshot_digest,
            )
    else:
        _record(
            "snapshot_digest",
            bool(attempt.snapshot_digest),
            f"attempt digest {attempt.snapshot_digest or 'absent'}",
        )

    scenario_regs = HostedHarnessScenario.no_workspace_objects.filter(job=job).count()
    _record(
        "scenario_registrations",
        scenario_regs >= expected,
        f"{scenario_regs}/{expected} registered",
    )

    terminal_receipts = HostedHarnessReceipt.no_workspace_objects.filter(
        job=job, status__in=("passed", "failed")
    ).count()
    _record(
        "terminal_receipts",
        terminal_receipts >= expected,
        f"{terminal_receipts}/{expected} terminal receipts",
    )

    call_execs = HostedHarnessExecution.no_workspace_objects.filter(
        job=job, call_execution__isnull=False
    ).count()
    _record(
        "call_executions",
        call_execs >= expected,
        f"{call_execs}/{expected} call executions",
    )

    artifacts = HostedHarnessArtifact.no_workspace_objects.filter(job=job)
    transcript_count = artifacts.filter(kind="transcript").count()
    convo_entries = HostedHarnessConversationTranscript.no_workspace_objects.filter(
        conversation__job=job
    ).exclude(entries=[])
    _record(
        "transcripts",
        transcript_count >= expected or convo_entries.count() >= expected,
        f"{transcript_count} transcript artifacts, "
        f"{convo_entries.count()} conversation transcripts",
    )

    if voice:
        recordings = artifacts.filter(kind__in=_RECORDING_KINDS).count()
        _record(
            "voice_recordings",
            recordings >= expected,
            f"{recordings}/{expected} recording artifacts",
        )

    if declared_evidence:
        tool_traces = artifacts.filter(kind__in=("tool_trace", "result")).count()
        _record(
            "declared_evidence",
            tool_traces > 0,
            f"{tool_traces} tool/state evidence artifacts",
        )

    cleanup = None
    if attempt is not None:
        cleanup = HostedHarnessCleanupReceipt.no_workspace_objects.filter(
            attempt=attempt
        ).first()
    _record(
        "cleanup_receipt",
        cleanup is not None and cleanup.verified_absent,
        "verified_absent=%s" % (cleanup.verified_absent if cleanup else "absent"),
    )

    missing = [name for name, c in checks.items() if not c["ok"]]
    ok = not missing
    failure_class = None
    if not ok:
        if "cleanup_receipt" in missing:
            failure_class = "cleanup"
        elif "snapshot_digest" in missing:
            failure_class = "environment"
        elif "job_terminal" in missing:
            failure_class = classify_job_failure(job)
        else:
            failure_class = "evidence"
    return {
        "ok": ok,
        "checks": checks,
        "missing": missing,
        "failure_class": failure_class,
    }


def classify_job_failure(job) -> str:
    """Map a failed job's terminal domain onto the plan's failure classes."""
    attempt = job.attempts.order_by("-attempt_number").first()
    domain = (
        (attempt.terminal_failure or {}).get("domain") if attempt is not None else None
    )
    return _TERMINAL_DOMAIN_CLASS.get(domain, "target")


def detect_stable_behavior_failures(case) -> list[dict]:
    """Behavioral assertion failures inside an otherwise-complete case.

    Returns one entry per failed terminal receipt — stable-behavior failures
    are blocking even when every artifact exists.
    """
    from simulate.models import HostedHarnessReceipt

    job = case.hosted_harness_job
    if job is None:
        return []
    return [
        {
            "scenario_key": r.scenario.scenario_key if r.scenario_id else None,
            "receipt_id": str(r.id),
            "status": r.status,
        }
        for r in HostedHarnessReceipt.no_workspace_objects.filter(
            job=job, status="failed"
        ).select_related("scenario")
    ]


def detect_exploratory_failures(case) -> list[dict]:
    """Non-blocking exploratory evaluation failures (alert only)."""
    from simulate.models import HostedHarnessReceipt

    job = case.hosted_harness_job
    if job is None:
        return []
    out = []
    for r in HostedHarnessReceipt.no_workspace_objects.filter(job=job).exclude(
        body__isnull=True
    ):
        for ev in (r.body or {}).get("evaluations") or []:
            if ev.get("exploratory") and ev.get("status") in {"failed", "error"}:
                out.append(
                    {
                        "receipt_id": str(r.id),
                        "evaluation": ev.get("name"),
                        "status": ev.get("status"),
                    }
                )
    return out


# ---------------------------------------------------------------------------
# notification outbox
# ---------------------------------------------------------------------------


def enqueue_notification(campaign, case, event_kind: str, payload: dict):
    """Insert an outbox row exactly once; returns (notification, created)."""
    from simulate.models import CertificationNotification

    digest = canonical_digest(payload)
    try:
        notification, created = (
            CertificationNotification.no_workspace_objects.get_or_create(
                campaign=campaign,
                case=case,
                event_kind=event_kind,
                payload_digest=digest,
                defaults={"payload": payload},
            )
        )
    except IntegrityError:
        notification = CertificationNotification.no_workspace_objects.get(
            campaign=campaign,
            case=case,
            event_kind=event_kind,
            payload_digest=digest,
        )
        created = False
    return notification, created


def _slack_webhook_url() -> str:
    from django.conf import settings

    return str(
        getattr(settings, "PRODUCTION_CERTIFICATION_SLACK_WEBHOOK", "") or ""
    ).strip()


def _format_notification_text(campaign, case, event_kind, payload) -> str:
    base = f"[certification] {event_kind} campaign={campaign.id}"
    if case is not None:
        base += f" case={case.case_key}"
    extras = []
    for key in ("failure_class", "suite_key", "deployment_id", "state"):
        if payload.get(key):
            extras.append(f"{key}={payload[key]}")
    return " ".join([base, *extras])[:1500]


def deliver_pending_notifications(campaign) -> dict:
    """Attempt delivery of every pending outbox row once.

    Uses the dedicated PRODUCTION_CERTIFICATION_SLACK_WEBHOOK (never the
    critical-error webhook). A delivery failure is recorded on the row and
    reported in the return value — it never changes campaign results.
    """
    from slack_sdk import WebhookClient

    from simulate.models import CertificationNotification

    url = _slack_webhook_url()
    counts = {"delivered": 0, "failed": 0, "skipped": 0}
    pending = CertificationNotification.no_workspace_objects.filter(
        campaign=campaign, state=CertificationNotification.State.PENDING
    ).select_related("case")
    if not url:
        for row in pending:
            row.state = CertificationNotification.State.FAILED
            row.last_error = "PRODUCTION_CERTIFICATION_SLACK_WEBHOOK unset"
            row.attempts += 1
            row.save(update_fields=["state", "last_error", "attempts", "updated_at"])
            counts["failed"] += 1
        return counts

    client = WebhookClient(url)
    for row in pending:
        try:
            response = client.send(
                text=_format_notification_text(
                    row.campaign, row.case, row.event_kind, row.payload
                )
            )
            if response.status_code == 200:
                row.state = CertificationNotification.State.DELIVERED
                row.delivered_at = timezone.now()
                counts["delivered"] += 1
            else:
                row.state = CertificationNotification.State.FAILED
                row.last_error = f"webhook status {response.status_code}"
                counts["failed"] += 1
        except Exception as exc:  # noqa: BLE001 — delivery must not change results
            row.state = CertificationNotification.State.FAILED
            row.last_error = str(exc)[:500]
            counts["failed"] += 1
        row.attempts += 1
        row.save(
            update_fields=[
                "state",
                "delivered_at",
                "last_error",
                "attempts",
                "updated_at",
            ]
        )
    return counts
