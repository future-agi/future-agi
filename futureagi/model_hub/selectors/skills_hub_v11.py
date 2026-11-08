"""Private/shared selectors. Scope and visibility are applied before pagination."""

from __future__ import annotations

from collections.abc import Sequence
from math import ceil
from typing import NoReturn
from uuid import UUID

from django.db.models import Q, QuerySet
from django.utils import timezone

from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v11 import (
    HubSkillAssistProposal,
    HubSkillGitHubPreview,
    HubSkillGitHubSource,
    HubSkillTestAttempt,
    HubSkillTestRun,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope


def _not_found() -> NoReturn:
    raise SkillsHubError("resource_not_found", "Resource not found.", status=404)


def _authorize(*, principal: SkillPrincipal, scope: SkillScope | None = None) -> None:
    # Classify first: a synthetic system user must never reach an owner lookup.
    require_private_principal(principal=principal)
    if scope is not None and scope != principal.scope:
        _not_found()
    require_scope_access(principal=principal, write=False)


def _parent(*, principal: SkillPrincipal, skill_id: UUID) -> HubSkill:
    skill = HubSkill.no_workspace_objects.filter(
        pk=skill_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    ).first()
    if skill is None:
        _not_found()
    return skill


def list_visible_runs(
    *,
    principal: SkillPrincipal,
    skill_id: UUID,
    page: int = 1,
    page_size: int = 25,
    scope: SkillScope | None = None,
) -> dict:
    _authorize(principal=principal, scope=scope)
    _parent(principal=principal, skill_id=skill_id)
    queryset = (
        HubSkillTestRun.objects.filter(
            job__organization_id=principal.scope.organization_id,
            job__workspace_id=principal.scope.workspace_id,
            job__kind="test",
            job__skill_id=skill_id,
        )
        .filter(Q(job__owner_user_id=principal.user_id) | Q(visibility="workspace"))
        .select_related("job", "job__skill")
        .order_by("-job__created_at", "-job_id")
    )
    if (
        type(page) is not int
        or type(page_size) is not int
        or page < 1
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError("invalid_test_config", "Invalid pagination.", status=400)
    from django.db.models import BooleanField, ExpressionWrapper

    count = queryset.count()
    rows = list(
        queryset.defer("snapshot").annotate(
            payload_present=ExpressionWrapper(
                Q(snapshot__isnull=False), output_field=BooleanField()
            )
        )[(page - 1) * page_size : page * page_size]
    )
    context = _summary_context(rows)
    return {
        "results": [_run_summary(run, context) for run in rows],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(count / page_size),
    }


def get_visible_run(
    *,
    principal: SkillPrincipal,
    run_id: UUID,
    skill_id: UUID | None = None,
    scope: SkillScope | None = None,
) -> HubSkillTestRun:
    _authorize(principal=principal, scope=scope)
    queryset = HubSkillTestRun.objects.filter(
        job_id=run_id,
        job__organization_id=principal.scope.organization_id,
        job__workspace_id=principal.scope.workspace_id,
        job__kind="test",
        job__skill__deleted=False,
        job__skill__organization_id=principal.scope.organization_id,
        job__skill__workspace_id=principal.scope.workspace_id,
    ).filter(Q(job__owner_user_id=principal.user_id) | Q(visibility="workspace"))
    if skill_id is not None:
        queryset = queryset.filter(job__skill_id=skill_id)
    from django.db.models import BooleanField, ExpressionWrapper

    run = (
        queryset.select_related("job", "job__skill")
        .defer("snapshot")
        .annotate(
            payload_present=ExpressionWrapper(
                Q(snapshot__isnull=False), output_field=BooleanField()
            )
        )
        .first()
    )
    if run is None:
        _not_found()
    return run


def require_run_payload(run: HubSkillTestRun) -> None:
    if (
        run.snapshot is None
        or run.payload_purged_at is not None
        or (
            run.payload_expires_at is not None
            and run.payload_expires_at <= timezone.now()
        )
    ):
        raise SkillsHubError(
            "evidence_expired", "Detailed evidence has expired.", status=410
        )


def get_owned_proposal(
    *,
    principal: SkillPrincipal,
    proposal_id: UUID,
    skill_id: UUID | None = None,
    scope: SkillScope | None = None,
) -> HubSkillAssistProposal:
    _authorize(principal=principal, scope=scope)
    queryset = HubSkillAssistProposal.objects.filter(
        job_id=proposal_id,
        job__kind="assist",
        job__owner_user_id=principal.user_id,
        job__organization_id=principal.scope.organization_id,
        job__workspace_id=principal.scope.workspace_id,
        skill__organization_id=principal.scope.organization_id,
        skill__workspace_id=principal.scope.workspace_id,
        skill__deleted=False,
    )
    if skill_id is not None:
        queryset = queryset.filter(skill_id=skill_id, job__skill_id=skill_id)
    proposal = queryset.select_related("job", "skill").first()
    if proposal is None or proposal.job.skill_id != proposal.skill_id:
        _not_found()
    return proposal


def get_owned_preview(
    *,
    principal: SkillPrincipal,
    preview_id: UUID,
    skill_id: UUID | None = None,
    scope: SkillScope | None = None,
) -> HubSkillGitHubPreview:
    _authorize(principal=principal, scope=scope)
    queryset = HubSkillGitHubPreview.objects.filter(
        job_id=preview_id,
        job__kind="github",
        job__owner_user_id=principal.user_id,
        job__organization_id=principal.scope.organization_id,
        job__workspace_id=principal.scope.workspace_id,
    ).filter(
        Q(job__skill__isnull=True)
        | Q(
            job__skill__deleted=False,
            job__skill__organization_id=principal.scope.organization_id,
            job__skill__workspace_id=principal.scope.workspace_id,
        )
    )
    if skill_id is not None:
        queryset = queryset.filter(job__skill_id=skill_id)
    preview = queryset.select_related("job").first()
    if preview is None:
        _not_found()
    return preview


def get_attempt_detail(
    *,
    principal: SkillPrincipal,
    skill_id: UUID,
    run_id: UUID,
    attempt_id: UUID,
    scope: SkillScope | None = None,
) -> dict:
    run = get_visible_run(
        principal=principal, scope=scope, skill_id=skill_id, run_id=run_id
    )
    require_run_payload(run)
    assert run.snapshot is not None
    attempt = (
        HubSkillTestAttempt.objects.filter(pk=attempt_id, run=run)
        .select_related("run", "run__job")
        .prefetch_related("hub_skill_calls")
        .first()
    )
    if attempt is None:
        _not_found()
    case = next(
        (case for case in run.snapshot["cases"] if case["id"] == str(attempt.case_id)),
        None,
    )
    if case is None:
        _not_found()
    calls = _calls_for(job_id=run.pk, attempt_id=attempt.pk, payload=True)
    result = {
        "id": attempt.pk,
        "case": case,
        "variant": attempt.variant,
        "repeat_index": attempt.repeat_index,
        "status": attempt.status,
        "routing_verdict": attempt.routing_verdict,
        "delivery_verdict": attempt.delivery_verdict,
        "answer_verdict": attempt.answer_verdict,
        "answer": attempt.answer,
        "target_requested": attempt.target_requested,
        "target_loaded": attempt.target_loaded,
        "root_fully_delivered": attempt.root_fully_delivered,
        "checks": attempt.check_results,
        "receipts": attempt.receipts,
        "calls": [_call_summary(call) for call in calls],
        "metrics": _metrics(
            calls=calls, started_at=attempt.started_at, finished_at=attempt.finished_at
        ),
        "error": attempt.error,
    }
    import json

    if (
        len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8"))
        > 256 * 1024
    ):
        raise SkillsHubError(
            "payload_too_large",
            "Attempt evidence exceeds the response limit.",
            status=413,
        )
    return result


def _case_comparisons(row: dict, *, diagnostic: bool) -> list[dict]:
    variants = {item["variant"]: item for item in row["variants"]}
    candidate = variants["candidate"]
    comparisons = []
    for baseline, variant in variants.items():
        if baseline == "candidate":
            continue
        matched = all(
            item["completed"] == item["planned"] == item["answer_graded"]
            and item["planned"] > 0
            for item in (candidate, variant)
        )
        comparisons.append(
            {
                "baseline": baseline,
                "matched_cases": int(matched),
                "expected_cases": 1,
                "delta_percentage_points": (
                    100
                    * (
                        candidate["answer_passed"] / candidate["planned"]
                        - variant["answer_passed"] / variant["planned"]
                    )
                    if matched and not diagnostic
                    else None
                ),
            }
        )
    return comparisons


def list_case_results(
    *,
    principal: SkillPrincipal,
    skill_id: UUID,
    run_id: UUID,
    filter: str = "all",
    page: int = 1,
    page_size: int = 25,
    scope: SkillScope | None = None,
) -> dict:
    run = get_visible_run(
        principal=principal, scope=scope, skill_id=skill_id, run_id=run_id
    )
    require_run_payload(run)
    assert run.snapshot is not None
    if (
        type(page) is not int
        or type(page_size) is not int
        or page < 1
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError("invalid_test_config", "Invalid pagination.", status=400)
    if filter not in {
        "all",
        "regressions",
        "wrong_selection",
        "inconsistent",
        "errors",
        "not_graded",
    }:
        raise SkillsHubError(
            "invalid_test_config", "Invalid result filter.", status=400
        )
    # Reuse the execution reducer; do not independently define improvement.
    from model_hub.services.skills_hub.v11.grading import reduce_results

    attempts = list(
        HubSkillTestAttempt.objects.filter(run=run).values(
            "id",
            "case_id",
            "variant",
            "repeat_index",
            "status",
            "answer",
            "target_loaded",
            "routing_verdict",
            "delivery_verdict",
            "answer_verdict",
        )
    )
    rows = reduce_results(
        cases=run.snapshot["cases"],
        attempts=[dict(attempt) for attempt in attempts],
        baselines=run.snapshot["configuration"]["baselines"],
        repetitions=run.repetitions,
        diagnostic=run.validity == "diagnostic",
    )["case_results"]

    def matches(row: dict) -> bool:
        cells = [
            attempt for variant in row["variants"] for attempt in variant["attempts"]
        ]
        return (
            filter == "all"
            or (filter == "regressions" and row["change"] in {"regressed", "mixed"})
            or (
                filter == "wrong_selection"
                and any(cell["routing_verdict"] == "fail" for cell in cells)
            )
            or (
                filter == "inconsistent"
                and any(variant["inconsistent"] for variant in row["variants"])
            )
            or (
                filter == "errors"
                and any(cell["status"] in {"error", "unknown"} for cell in cells)
            )
            or (
                filter == "not_graded"
                and any(
                    cell["answer_verdict"] in {"not_graded", "unverifiable"}
                    for cell in cells
                )
            )
        )

    selected = [row for row in rows if matches(row)]
    count = len(selected)
    return {
        "results": [
            {
                **row,
                "answer_comparisons": _case_comparisons(
                    row, diagnostic=run.validity == "diagnostic"
                ),
            }
            for row in selected[(page - 1) * page_size : page * page_size]
        ],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(count / page_size),
    }


def get_source(
    *, principal: SkillPrincipal, skill_id: UUID, scope: SkillScope | None = None
) -> dict:
    _authorize(principal=principal, scope=scope)
    _parent(principal=principal, skill_id=skill_id)
    source = (
        HubSkillGitHubSource.objects.filter(
            skill_id=skill_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        )
        .select_related("associated_version")
        .first()
    )
    if source is None:
        raise SkillsHubError(
            "source_not_found", "No adopted GitHub source.", status=404
        )
    return {
        "provider": "github",
        "revision": source.revision,
        "repo": source.repo,
        "repository_id": source.repository_id,
        "path": source.path,
        "ref": source.ref,
        "ref_kind": source.ref_kind,
        "imported_sha": source.imported_sha,
        "upstream_digest": source.upstream_digest,
        "associated_version": source.associated_version.number,
        "checked_sha": source.checked_sha,
        "checked_at": source.checked_at,
        "check_status": source.check_status,
    }


def _page(queryset: QuerySet, *, page: int, page_size: int, render) -> dict:
    if (
        type(page) is not int
        or type(page_size) is not int
        or page < 1
        or not 1 <= page_size <= 100
    ):
        raise SkillsHubError("invalid_test_config", "Invalid pagination.", status=400)
    count = queryset.count()
    return {
        "results": [
            render(item) for item in queryset[(page - 1) * page_size : page * page_size]
        ],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": ceil(count / page_size),
    }


def _elapsed(start, finish) -> int | None:
    return (
        max(0, int((finish - start).total_seconds() * 1000))
        if start is not None and finish is not None
        else None
    )


def _call_metrics(calls: list[dict]) -> tuple[dict, dict]:
    from decimal import Decimal

    known_input, known_output, unknown, estimate, reserved = (
        0,
        0,
        0,
        Decimal(0),
        Decimal(0),
    )
    sources = set()
    for call in calls:
        usage = call["usage"]
        if usage is None:
            unknown += 1
        else:
            known_input += usage["input_tokens"]
            known_output += usage["output_tokens"]
        if call["estimated_cost_usd"] is not None:
            estimate += call["estimated_cost_usd"]
        if usage is None or call["estimated_cost_usd"] is None:
            reserved += call["reserved_estimate_usd"]
        if call["pricing_source"]:
            sources.add(call["pricing_source"])
    unknown_cost = sum(call["estimated_cost_usd"] is None for call in calls)
    return (
        {
            "known_input_tokens": known_input,
            "known_output_tokens": known_output,
            "known_total_tokens": known_input + known_output,
            "unknown_calls": unknown,
            "complete": unknown == 0,
        },
        {
            "known_estimate_usd": str(estimate),
            "reserved_estimate_usd": str(reserved),
            "unknown_calls": unknown_cost,
            "complete": unknown_cost == 0,
            "pricing_source": ", ".join(sorted(sources)),
        },
    )


def _metrics(*, calls: list[dict], started_at, finished_at) -> dict:
    task_usage, task_cost = _call_metrics(
        [call for call in calls if call["kind"] != "judge"]
    )
    judge_usage, judge_cost = _call_metrics(
        [call for call in calls if call["kind"] == "judge"]
    )
    durations = [call["latency_ms"] for call in calls if call["latency_ms"] is not None]
    return {
        "task_usage": task_usage,
        "judge_usage": judge_usage,
        "task_cost": task_cost,
        "judge_cost": judge_cost,
        "elapsed_ms": _elapsed(started_at, finished_at),
        "provider_duration_ms": sum(durations) if durations else None,
    }


def _calls_for(
    *, job_id: UUID, attempt_id: UUID | None = None, payload: bool = False
) -> list[dict]:
    from model_hub.models.skills_hub_v11 import HubSkillModelCall

    queryset = HubSkillModelCall.objects.filter(job_id=job_id)
    if attempt_id is not None:
        queryset = queryset.filter(attempt_id=attempt_id)
    columns = [
        "id",
        "sequence",
        "kind",
        "state",
        "requested_model",
        "returned_model",
        "finish_reason",
        "provider_request_id",
        "usage",
        "estimated_cost_usd",
        "reserved_estimate_usd",
        "pricing_source",
        "latency_ms",
        "error",
    ]
    if payload:
        columns.append("assistant_message")
    return list(queryset.order_by("sequence").values(*columns))


def _summary_context(runs: Sequence[HubSkillTestRun]) -> dict:
    """Batch compact projections; never fetch run snapshots or call transcripts."""
    from django.contrib.postgres.aggregates import ArrayAgg
    from django.db.models import BigIntegerField, Count, Sum
    from django.db.models.functions import Cast

    from model_hub.models.skills_hub import (
        HubSkillDraft,
        HubSkillLabel,
        HubSkillLabelEvent,
        HubSkillVersion,
    )
    from model_hub.models.skills_hub_v11 import HubSkillModelCall, HubSkillTestSuite
    from model_hub.services.skills_hub.packages import manifest_digest

    if not runs:
        return {
            name: {}
            for name in (
                "labels",
                "events",
                "suites",
                "drafts",
                "competitors",
                "attempts",
                "metrics",
            )
        }
    organization_id, workspace_id = (
        runs[0].job.organization_id,
        runs[0].job.workspace_id,
    )
    skill_scope = {
        "skill__organization_id": organization_id,
        "skill__workspace_id": workspace_id,
    }
    run_ids = [run.pk for run in runs]
    skill_ids = {run.job.skill_id for run in runs}
    owners = {run.job.owner_user_id for run in runs}
    labels = {
        row["skill_id"]: row["version_id"]
        for row in HubSkillLabel.objects.filter(
            skill_id__in=skill_ids, name="production", **skill_scope
        ).values("skill_id", "version_id")
    }
    events = {
        row["skill_id"]: row["id"]
        for row in HubSkillLabelEvent.objects.filter(
            skill_id__in=skill_ids, label="production", **skill_scope
        )
        .order_by("skill_id", "-created_at", "-id")
        .distinct("skill_id")
        .values("skill_id", "id")
    }
    suites = dict(
        HubSkillTestSuite.objects.filter(
            skill_id__in=skill_ids,
            organization_id=organization_id,
            workspace_id=workspace_id,
        ).values_list("skill_id", "revision")
    )
    drafts = {
        (row["skill_id"], row["user_id"]): manifest_digest(row["files"])
        for row in HubSkillDraft.objects.filter(
            skill_id__in=skill_ids, user_id__in=owners, **skill_scope
        ).values("skill_id", "user_id", "files")
    }
    competitor_ids = {item["version_id"] for run in runs for item in run.competitors}
    competitors = {
        str(row["id"]): (row["skill__organization_id"], row["skill__workspace_id"])
        for row in HubSkillVersion.objects.filter(
            pk__in=competitor_ids, skill__deleted=False, **skill_scope
        ).values("id", "skill__organization_id", "skill__workspace_id")
    }
    attempts = {
        row["run_id"]: {key: value for key, value in row.items() if key != "run_id"}
        for row in HubSkillTestAttempt.objects.filter(
            run_id__in=run_ids,
            run__job__organization_id=organization_id,
            run__job__workspace_id=workspace_id,
        )
        .values("run_id")
        .annotate(
            planned=Count("pk"),
            finished=Count("pk", filter=~Q(status__in=["not_started", "running"])),
            answered=Count("pk", filter=Q(answer__isnull=False)),
            graded=Count("pk", filter=Q(answer_verdict__in=["pass", "fail"])),
            errors=Count("pk", filter=Q(status__in=["error", "unknown"])),
            incomplete=Count(
                "pk",
                filter=Q(status__in=["incomplete", "cancelled", "unknown", "running"]),
            ),
            not_started=Count("pk", filter=Q(status="not_started")),
        )
    }
    aggregates = (
        HubSkillModelCall.objects.filter(
            job_id__in=run_ids,
            job__organization_id=organization_id,
            job__workspace_id=workspace_id,
        )
        .values("job_id", "kind")
        .annotate(
            input_total=Sum(Cast("usage__input_tokens", BigIntegerField()), default=0),
            output_total=Sum(
                Cast("usage__output_tokens", BigIntegerField()), default=0
            ),
            missing_usage=Count("pk", filter=Q(usage__isnull=True)),
            missing_cost=Count("pk", filter=Q(estimated_cost_usd__isnull=True)),
            estimate=Sum("estimated_cost_usd", default=0),
            reserved=Sum(
                "reserved_estimate_usd",
                filter=Q(usage__isnull=True) | Q(estimated_cost_usd__isnull=True),
                default=0,
            ),
            duration=Sum("latency_ms"),
            sources=ArrayAgg("pricing_source", distinct=True),
        )
    )
    metrics = {
        run.pk: _metrics(
            calls=[], started_at=run.job.started_at, finished_at=run.job.finished_at
        )
        for run in runs
    }
    for row in aggregates:
        target = metrics[row["job_id"]]
        kind = "judge" if row["kind"] == "judge" else "task"
        target[f"{kind}_usage"] = {
            "known_input_tokens": row["input_total"],
            "known_output_tokens": row["output_total"],
            "known_total_tokens": row["input_total"] + row["output_total"],
            "unknown_calls": row["missing_usage"],
            "complete": row["missing_usage"] == 0,
        }
        target[f"{kind}_cost"] = {
            "known_estimate_usd": str(row["estimate"]),
            "reserved_estimate_usd": str(row["reserved"]),
            "unknown_calls": row["missing_cost"],
            "complete": row["missing_cost"] == 0,
            "pricing_source": ", ".join(sorted(filter(None, row["sources"]))),
        }
        if row["duration"] is not None:
            target["provider_duration_ms"] = (
                target["provider_duration_ms"] or 0
            ) + row["duration"]
    return {
        "labels": labels,
        "events": events,
        "suites": suites,
        "drafts": drafts,
        "competitors": competitors,
        "attempts": attempts,
        "metrics": metrics,
    }


def run_summary(*, principal: SkillPrincipal, run: HubSkillTestRun) -> dict:
    """Render only persisted configuration, never the current model catalog."""
    _authorize(principal=principal)
    if (
        run.job.organization_id != principal.scope.organization_id
        or run.job.workspace_id != principal.scope.workspace_id
        or run.job.skill is None
        or run.job.skill.deleted
        or (
            run.visibility != "workspace" and run.job.owner_user_id != principal.user_id
        )
    ):
        _not_found()
    return _run_summary(run, _summary_context([run]))


def _run_summary(run: HubSkillTestRun, context: dict) -> dict:
    job = run.job
    skill_id = job.skill_id
    digest = (
        context["drafts"].get((skill_id, job.owner_user_id))
        if run.candidate.get("draft_revision") is not None
        else run.candidate_digest
    )
    present = (
        run.payload_present
        if hasattr(run, "payload_present")
        else run.snapshot is not None
    )
    freshness = {
        "content": digest == run.candidate_digest,
        "baseline": (
            None
            if run.baseline_version_id is None
            else context["labels"].get(skill_id) == run.baseline_version_id
            and context["events"].get(skill_id) == run.baseline_event_id
        ),
        "suite": (
            None
            if run.suite_revision is None
            else context["suites"].get(skill_id) == run.suite_revision
        ),
        "competitors": (
            None
            if not run.competitors
            else all(
                context["competitors"].get(str(item["version_id"]))
                == (job.organization_id, job.workspace_id)
                for item in run.competitors
            )
        ),
        "payload_available": present
        and run.payload_purged_at is None
        and (run.payload_expires_at is None or run.payload_expires_at > timezone.now()),
    }
    counts = context["attempts"].get(
        run.pk,
        {
            "planned": 0,
            "finished": 0,
            "answered": 0,
            "graded": 0,
            "errors": 0,
            "incomplete": 0,
            "not_started": 0,
        },
    )
    decision = {
        "state": "diagnostic" if run.validity == "diagnostic" else "inconclusive",
        "improved_cases": 0,
        "regressed_cases": 0,
        "unchanged_cases": 0,
        "unmatched_cases": len(run.selected_case_ids),
    }
    decision.update(run.summary.get("decision", {}))
    return {
        "id": run.pk,
        "status": job.status,
        "profile": run.profile,
        "visibility": run.visibility,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "deadline_at": job.deadline_at,
        "parent_run_id": run.parent_run_id,
        "execution_fingerprint": run.execution_fingerprint,
        "execution_configuration": run.execution_configuration,
        "candidate": run.candidate,
        "production": run.production,
        "competitors": run.competitors,
        "suite_revision": run.suite_revision,
        "selected_case_ids": run.selected_case_ids,
        "full_suite": run.full_suite,
        "repetitions": run.repetitions,
        "counts": counts,
        "decision": decision,
        "answer_comparisons": run.summary.get("answer_comparisons", []),
        "validity": run.validity,
        "validity_reasons": run.validity_reasons,
        "currentness": freshness,
        "metrics": context["metrics"][run.pk],
        "error": _job_error(job),
        "payload_expires_at": run.payload_expires_at,
        "payload_expired": not freshness["payload_available"],
    }


def _job_error(job) -> dict | None:
    return (
        {
            "code": job.failure_code,
            "message": "This job did not finish successfully.",
            "detail": job.failure_detail,
        }
        if job.failure_code
        else None
    )


def _call_summary(call: dict) -> dict:
    usage, cost = _call_metrics([call])
    message = call.get("assistant_message")
    return {
        **{
            key: call[key]
            for key in (
                "id",
                "sequence",
                "kind",
                "state",
                "requested_model",
                "returned_model",
                "finish_reason",
                "provider_request_id",
                "latency_ms",
                "error",
            )
        },
        "assistant_message": (
            {
                key: message[key]
                for key in ("role", "content", "tool_calls")
                if key in message
            }
            if message
            else None
        ),
        "usage": usage,
        "cost": cost,
    }


def get_snapshot_file(
    *,
    principal: SkillPrincipal,
    skill_id: UUID,
    run_id: UUID,
    snapshot_skill_id: UUID,
    variant: str,
    path: str,
    offset: int = 0,
    max_bytes: int = 24000,
) -> dict:
    run = get_visible_run(principal=principal, skill_id=skill_id, run_id=run_id)
    require_run_payload(run)
    assert run.snapshot is not None
    from model_hub.services.skills_hub.v11.runner import read_snapshot_chunk

    if variant not in {"candidate", *run.snapshot["configuration"]["baselines"]}:
        _not_found()
    bundles = list(run.snapshot["competitors"])
    if variant != "without_target":
        bundles.append(run.snapshot[variant])
    bundle = next(
        (item for item in bundles if str(item["skill_id"]) == str(snapshot_skill_id)),
        None,
    )
    if bundle is None:
        _not_found()
    # UI reads reuse immutable byte/offset semantics, not runtime activation state.
    return read_snapshot_chunk(
        snapshot={"selected": bundle},
        skill_handle="selected",
        path=path,
        offset=offset,
        max_bytes=max_bytes,
    )


def proposal_summary(*, proposal: HubSkillAssistProposal) -> dict:
    job = proposal.job
    expired = proposal.payload_purged_at is not None or (
        proposal.payload_expires_at is not None
        and proposal.payload_expires_at <= timezone.now()
    )
    return {
        "id": proposal.pk,
        "status": job.status,
        "kind": proposal.kind,
        "base_revision": proposal.base_revision,
        "base_digest": proposal.base_digest,
        "input_fingerprint": proposal.input_fingerprint,
        "proposal": None if expired else proposal.proposal,
        "missing_information": [] if expired else proposal.missing_information,
        "checks": None if expired else proposal.checks,
        "exposed_case_ids": proposal.selected_case_ids,
        "metrics": _metrics(
            calls=_calls_for(job_id=job.pk),
            started_at=job.started_at,
            finished_at=job.finished_at,
        ),
        "error": _job_error(job),
        "payload_expired": expired,
    }


def preview_summary(*, preview: HubSkillGitHubPreview) -> dict:
    expired = preview.expires_at is not None and preview.expires_at <= timezone.now()
    consumed = (
        {
            "skill_id": preview.consumed_skill_id,
            "draft_revision": preview.consumed_draft_revision,
        }
        if preview.consumed_skill_id
        else None
    )
    error = _job_error(preview.job)
    if expired:
        error = {
            "code": "github_preview_expired",
            "message": "Preview content expired. Preview again before staging.",
            "detail": {},
        }
    return {
        "id": preview.pk,
        "status": preview.job.status,
        "operation": preview.operation,
        "expires_at": preview.expires_at,
        "source": {
            "repo": preview.repo,
            "repository_id": preview.repository_id,
            "path": preview.path,
            "ref": preview.ref,
            "ref_kind": preview.ref_kind or "default",
            "commit_sha": preview.commit_sha,
        },
        "outcome": preview.outcome,
        "roots": preview.roots,
        "bundle": None if expired else preview.bundle,
        "license": (
            {
                "status": "unknown",
                "declared": None,
                "files": [],
                "requires_admin_review": True,
            }
            if expired or not preview.license
            else preview.license
        ),
        "diff": None if expired else preview.diff,
        "local_diverged": preview.local_diverged,
        "source_revision": preview.source_revision,
        "expected_latest_version": preview.expected_latest_version,
        "expected_latest_digest": preview.expected_latest_digest,
        "can_stage": not expired
        and consumed is None
        and preview.outcome in {"ready", "changed", "unchanged_content"},
        "error": error,
        "retry_at": preview.job.failure_detail.get("retry_at"),
        "consumed": consumed,
    }


def get_label_protection(*, principal: SkillPrincipal, skill_id: UUID) -> dict:
    _authorize(principal=principal)
    skill = _parent(principal=principal, skill_id=skill_id)
    from model_hub.services.skills_hub.v11.principals import effective_scope_level
    from tfc.constants.levels import Level

    return {
        "protected_label_names": skill.protected_label_names,
        "release_revision": skill.release_revision,
        "can_manage": effective_scope_level(principal=principal)
        >= Level.WORKSPACE_ADMIN,
    }


def list_label_protection_history(
    *, principal: SkillPrincipal, skill_id: UUID, page: int = 1, page_size: int = 25
) -> dict:
    _authorize(principal=principal)
    _parent(principal=principal, skill_id=skill_id)
    from model_hub.models.skills_hub_v11 import HubSkillLabelPolicyEvent

    events = (
        HubSkillLabelPolicyEvent.objects.filter(
            skill_id=skill_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        )
        .select_related("actor", "actor_api_key")
        .order_by("-created_at", "-id")
    )

    def render(event):
        actor = None
        if event.actor_api_key_id:
            actor = {
                "id": event.actor_api_key_id,
                "name": event.actor_api_key.name,
                "kind": "api_key",
            }
        elif event.actor_id:
            actor = {"id": event.actor_id, "name": event.actor.name, "kind": "user"}
        return {
            "old_names": event.old_names,
            "new_names": event.new_names,
            "from_revision": event.from_revision,
            "to_revision": event.to_revision,
            "actor": actor,
            "created_at": event.created_at,
        }

    return _page(events, page=page, page_size=page_size, render=render)
