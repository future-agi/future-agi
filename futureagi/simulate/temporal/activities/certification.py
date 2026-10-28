"""Certification campaign activities (QUEUE_RUNNER / simulation_runner).

Every activity is thin over ``simulate.services.certification`` so replaying a
campaign workflow never re-runs admission logic twice: submission keys,
outbox dedupe, and unique constraints give exact-once job/notification
behavior even across retries.
"""

from __future__ import annotations

from temporalio import activity

from simulate.temporal.activities.hosted_runner import _run_db
from simulate.temporal.types.certification import (
    CertificationCampaignInput,
    CertificationCaseInput,
    CertificationCasesOutput,
    CertificationFinalizeOutput,
    CertificationNotifyOutput,
    CertificationPollOutput,
    CertificationReconcileOutput,
    CertificationResultInput,
    CertificationSubmitOutput,
)


@activity.defn(name="certification_load_cases")
async def certification_load_cases(
    input: CertificationCampaignInput,
) -> CertificationCasesOutput:
    """Snapshot the campaign's pending cases and mark it running (idempotent)."""
    from django.utils import timezone

    from simulate.models import CertificationCampaign, CertificationCase

    def _load() -> CertificationCasesOutput:
        campaign = CertificationCampaign.no_workspace_objects.get(id=input.campaign_id)
        if campaign.state in (
            CertificationCampaign.State.PENDING,
            CertificationCampaign.State.RUNNING,
        ):
            update = ["state", "updated_at"]
            campaign.state = CertificationCampaign.State.RUNNING
            if campaign.started_at is None:
                campaign.started_at = timezone.now()
                update.append("started_at")
            campaign.save(update_fields=update)
        # Resume-safe: pending cases (never dispatched) plus dispatched/running
        # cases (submission happened; outcome unknown after replay).
        cases = CertificationCase.no_workspace_objects.filter(
            campaign=campaign,
            state__in=(
                CertificationCase.State.PENDING,
                CertificationCase.State.DISPATCHED,
                CertificationCase.State.RUNNING,
                CertificationCase.State.RECONCILING,
            ),
        ).order_by("sequence")
        return CertificationCasesOutput(
            pending_case_ids=[
                str(c.id) for c in cases if c.state == CertificationCase.State.PENDING
            ],
            active_case_ids=[
                str(c.id) for c in cases if c.state != CertificationCase.State.PENDING
            ],
            max_active_cases=campaign.max_active_cases,
            cancel_requested=campaign.cancel_requested_at is not None,
        )

    return await _run_db(_load)


@activity.defn(name="certification_submit_case")
async def certification_submit_case(
    input: CertificationCaseInput,
) -> CertificationSubmitOutput:
    """Create the case's HostedHarnessJob via the shared submission service.

    Exact-once: the case idempotency key makes a second submit return the
    existing job, so workflow retries/replays never double-dispatch.
    """
    from django.conf import settings
    from django.utils import timezone

    from simulate.models import CertificationCase
    from simulate.services.certification import build_case_job_payload
    from simulate.services.hosted_harness_submission import (
        submit_hosted_harness_job,
    )

    def _submit() -> CertificationSubmitOutput:
        case = CertificationCase.no_workspace_objects.select_related(
            "campaign__organization"
        ).get(id=input.case_id)
        campaign = case.campaign
        if case.hosted_harness_job_id is not None:
            return CertificationSubmitOutput(
                hosted_harness_job_id=str(case.hosted_harness_job_id)
            )
        payload = build_case_job_payload(case, campaign)
        base_url = str(getattr(settings, "HARNESS_PUBLIC_BASE_URL", "") or "").rstrip(
            "/"
        )
        job, _created = submit_hosted_harness_job(
            campaign.organization,
            campaign.workspace,
            payload,
            case.idempotency_key,
            base_url,
        )
        case.hosted_harness_job = job
        case.state = CertificationCase.State.RUNNING
        if case.started_at is None:
            case.started_at = timezone.now()
        case.save(
            update_fields=[
                "hosted_harness_job",
                "state",
                "started_at",
                "updated_at",
            ]
        )
        return CertificationSubmitOutput(hosted_harness_job_id=str(job.id))

    return await _run_db(_submit)


@activity.defn(name="certification_poll_case")
async def certification_poll_case(
    input: CertificationCaseInput,
) -> CertificationPollOutput:
    """Observe the linked HostedHarnessJob's terminal state."""
    from simulate.models import CertificationCase, HostedHarnessJob

    def _poll() -> CertificationPollOutput:
        case = CertificationCase.no_workspace_objects.get(id=input.case_id)
        job = case.hosted_harness_job
        if job is None:
            return CertificationPollOutput(done=False, job_state="missing")
        job.refresh_from_db(fields=["state"])
        done = job.state in {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }
        return CertificationPollOutput(done=done, job_state=job.state)

    return await _run_db(_poll)


@activity.defn(name="certification_reconcile_case")
async def certification_reconcile_case(
    input: CertificationCaseInput,
) -> CertificationReconcileOutput:
    """Run the evidence checklist; job state alone is not success."""
    from simulate.models import CertificationCase
    from simulate.services.certification import reconcile_case

    def _reconcile() -> CertificationReconcileOutput:
        case = CertificationCase.no_workspace_objects.select_related(
            "campaign", "hosted_harness_job"
        ).get(id=input.case_id)
        result = reconcile_case(case, expected_terminal=input.expected_terminal)
        case.reconciliation = result
        case.state = CertificationCase.State.RECONCILING
        case.save(update_fields=["reconciliation", "state", "updated_at"])
        return CertificationReconcileOutput(
            ok=result["ok"],
            missing=result["missing"],
            failure_class=result["failure_class"],
        )

    return await _run_db(_reconcile)


@activity.defn(name="certification_record_case_result")
async def certification_record_case_result(
    input: CertificationResultInput,
) -> None:
    """Apply one replay-safe terminal case transition and derive counters."""
    from django.db import transaction
    from django.utils import timezone

    from simulate.models import (
        CertificationCampaign,
        CertificationCase,
        CertificationNotification,
    )
    from simulate.services.certification import (
        detect_exploratory_failures,
        detect_stable_behavior_failures,
        enqueue_notification,
    )

    def _record() -> None:
        with transaction.atomic():
            case = (
                CertificationCase.no_workspace_objects.select_for_update()
                .select_related("campaign")
                .get(id=input.case_id)
            )
            campaign = (
                CertificationCampaign.no_workspace_objects.select_for_update().get(
                    id=case.campaign_id
                )
            )
            if case.state in {
                CertificationCase.State.COMPLETED,
                CertificationCase.State.FAILED,
                CertificationCase.State.CANCELED,
            }:
                return
            stable_failures = (
                detect_stable_behavior_failures(case)
                if input.outcome == CertificationCase.State.COMPLETED
                else []
            )
            exploratory = detect_exploratory_failures(case)
            if (
                input.outcome == CertificationCase.State.COMPLETED
                and not stable_failures
            ):
                case.state = CertificationCase.State.COMPLETED
                case.failure_class = None
                case.failure_detail = {}
            elif input.outcome == CertificationCase.State.CANCELED:
                case.state = CertificationCase.State.CANCELED
                case.failure_class = None
                case.failure_detail = input.failure_detail
            else:
                case.state = CertificationCase.State.FAILED
                case.failure_class = (
                    CertificationCase.FailureClass.STABLE_BEHAVIOR
                    if stable_failures
                    else input.failure_class or CertificationCase.FailureClass.EVIDENCE
                )
                case.failure_detail = {
                    **input.failure_detail,
                    "stable_failures": stable_failures,
                }
            case.finished_at = timezone.now()
            case.save(
                update_fields=[
                    "state",
                    "failure_class",
                    "failure_detail",
                    "finished_at",
                    "updated_at",
                ]
            )

            campaign.cases_completed = CertificationCase.no_workspace_objects.filter(
                campaign=campaign, state=CertificationCase.State.COMPLETED
            ).count()
            campaign.cases_failed = CertificationCase.no_workspace_objects.filter(
                campaign=campaign, state=CertificationCase.State.FAILED
            ).count()
            campaign.save(
                update_fields=["cases_completed", "cases_failed", "updated_at"]
            )
            if case.state == CertificationCase.State.FAILED:
                enqueue_notification(
                    campaign,
                    case,
                    CertificationNotification.EventKind.BLOCKING_FAILURE,
                    {
                        "failure_class": case.failure_class,
                        "case_key": case.case_key,
                        "missing": (case.reconciliation or {}).get("missing"),
                        "stable_failures": stable_failures,
                    },
                )
            for failure in (
                exploratory if case.state == CertificationCase.State.COMPLETED else []
            ):
                enqueue_notification(
                    campaign,
                    case,
                    CertificationNotification.EventKind.STABLE_ASSERTION_FAILURE,
                    {
                        "case_key": case.case_key,
                        "exploratory": True,
                        **failure,
                    },
                )
        import structlog

        structlog.get_logger(__name__).info(
            "certification_case_total",
            state=case.state,
            failure_class=case.failure_class or "",
            modality=(case.spec or {}).get("run_test_id", ""),
            case_key=case.case_key,
            campaign_id=str(campaign.id),
        )

    await _run_db(_record)


@activity.defn(name="certification_deliver_notifications")
async def certification_deliver_notifications(
    input: CertificationCampaignInput,
) -> CertificationNotifyOutput:
    """Attempt delivery of pending outbox rows; never fails the campaign."""
    from simulate.models import CertificationCampaign
    from simulate.services.certification import deliver_pending_notifications

    def _deliver() -> CertificationNotifyOutput:
        campaign = CertificationCampaign.no_workspace_objects.get(id=input.campaign_id)
        counts = deliver_pending_notifications(campaign)
        return CertificationNotifyOutput(
            delivered=counts["delivered"],
            failed=counts["failed"],
            skipped=counts["skipped"],
        )

    return await _run_db(_deliver)


@activity.defn(name="certification_cancel_case")
async def certification_cancel_case(input: CertificationCaseInput) -> None:
    """Cancel a running case's hosted gateway workflow (best-effort)."""
    from simulate.models import CertificationCase

    def _cancel() -> None:
        case = CertificationCase.no_workspace_objects.get(id=input.case_id)
        if case.hosted_harness_job_id is None:
            return
        from simulate.temporal.client import (
            cancel_hosted_harness_gateway_workflow,
        )

        cancel_hosted_harness_gateway_workflow(str(case.hosted_harness_job_id))

    await _run_db(_cancel)


@activity.defn(name="certification_finalize_campaign")
async def certification_finalize_campaign(
    input: CertificationCampaignInput,
) -> CertificationFinalizeOutput:
    """Set terminal campaign state, summary result, final notification."""
    from django.db import transaction
    from django.utils import timezone

    from simulate.models import CertificationCampaign, CertificationCase
    from simulate.services.certification import enqueue_notification

    @transaction.atomic
    def _finalize() -> CertificationFinalizeOutput:
        campaign = CertificationCampaign.no_workspace_objects.select_for_update().get(
            id=input.campaign_id
        )
        cases = CertificationCase.no_workspace_objects.select_for_update().filter(
            campaign=campaign
        )
        terminal = {
            CertificationCase.State.COMPLETED,
            CertificationCase.State.FAILED,
            CertificationCase.State.CANCELED,
        }
        incomplete = [
            {"id": str(row["id"]), "state": row["state"]}
            for row in cases.exclude(state__in=terminal).values("id", "state")
        ]
        if incomplete:
            now = timezone.now()
            for case in cases.exclude(state__in=terminal):
                previous_state = case.state
                case.state = CertificationCase.State.FAILED
                case.failure_class = CertificationCase.FailureClass.ENVIRONMENT
                case.failure_detail = {
                    "reason": "orchestration_incomplete_at_finalize",
                    "previous_state": previous_state,
                }
                case.finished_at = now
                case.save(
                    update_fields=[
                        "state",
                        "failure_class",
                        "failure_detail",
                        "finished_at",
                        "updated_at",
                    ]
                )
        counts = {
            state: CertificationCase.no_workspace_objects.filter(
                campaign=campaign, state=state
            ).count()
            for state in (
                CertificationCase.State.COMPLETED,
                CertificationCase.State.FAILED,
                CertificationCase.State.CANCELED,
            )
        }
        if incomplete:
            state = CertificationCampaign.State.FAILED
        elif counts[CertificationCase.State.FAILED] > 0:
            state = CertificationCampaign.State.COMPLETED_WITH_FAILURES
        elif campaign.cancel_requested_at:
            state = CertificationCampaign.State.CANCELED
        else:
            state = CertificationCampaign.State.COMPLETED
        campaign.state = state
        campaign.finished_at = timezone.now()
        campaign.result = {
            "cases_total": campaign.cases_total,
            "cases_completed": counts[CertificationCase.State.COMPLETED],
            "cases_failed": counts[CertificationCase.State.FAILED],
            "cases_canceled": counts[CertificationCase.State.CANCELED],
            "failure_classes": list(
                CertificationCase.no_workspace_objects.filter(
                    campaign=campaign, failure_class__isnull=False
                )
                .exclude(failure_class="")
                .values_list("failure_class", flat=True)
                .distinct()
            ),
            "report_url": f"/simulate/api/certification-campaigns/{campaign.id}/",
        }
        campaign.cases_completed = counts[CertificationCase.State.COMPLETED]
        campaign.cases_failed = counts[CertificationCase.State.FAILED]
        campaign.failure_detail = {"incomplete_cases": incomplete} if incomplete else {}
        campaign.save(
            update_fields=[
                "state",
                "finished_at",
                "result",
                "cases_completed",
                "cases_failed",
                "failure_detail",
                "updated_at",
            ]
        )
        enqueue_notification(
            campaign,
            None,
            "campaign_completed",
            {
                "state": state,
                "cases_total": campaign.cases_total,
                "cases_failed": counts[CertificationCase.State.FAILED],
                "report_url": campaign.result["report_url"],
            },
        )
        import structlog

        structlog.get_logger(__name__).info(
            "certification_campaign_total",
            state=state,
            campaign_id=str(campaign.id),
        )
        return CertificationFinalizeOutput(
            state=state,
            cases_completed=counts[CertificationCase.State.COMPLETED],
            cases_failed=counts[CertificationCase.State.FAILED],
        )

    return await _run_db(_finalize)
