"""Read-only, fingerprint-bound release inspection; never infer native success.

The existing packet service owns scope, visibility, expiry and currentness.
Native completion is NOT an outcome: a native record carries outcomes only when
its completed skills-native job is reduced from its own receipts (the same
reduction the run result shows). Anything else keeps its planned denominator
and unknown outcomes.
"""

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.models.skills_hub_r3 import HubSkillEvidenceAnnotation
from model_hub.models.skills_hub_v11 import HubSkillTestRun
from model_hub.models.skills_hub_v12 import HubSkillContextBinding
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.capability_changes import capability_changes
from model_hub.services.skills_hub.r3.human_review import evidence_record
from model_hub.services.skills_hub.r3.release_requests import preview_release_packet
from model_hub.services.skills_hub.v12.resolution_leases import (
    require_distribution_eligibility,
)


def _summary(principal, ref):
    record = evidence_record(
        principal=principal, source=ref["source"], source_id=ref["source_id"]
    )
    annotations = HubSkillEvidenceAnnotation.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        source=ref["source"],
        source_id=ref["source_id"],
        revision__lte=ref["annotation_revision"],
    )
    # Append-only feedback has no implicit resolution. An agreement appended
    # later must not erase an earlier disagreement from this review.
    value = {
        "source": ref["source"],
        "source_id": ref["source_id"],
        "annotation_revision": ref["annotation_revision"],
        "disagreements": annotations.filter(verdict="disagree").count(),
        "cannot_assess": annotations.filter(verdict="cannot_assess").count(),
        "planned": None,
        "graded": None,
        "incomplete": None,
        "regressions": None,
        "answer": "Not observed",
        "selection": "Not observed",
        "delivery": "Not observed",
        "tools": "Not observed",
        "availability": "unavailable",
        "result": None,
        "cases": [],
    }
    if isinstance(record, HubSkillTestRun):
        counts, decision = record.counts, record.summary.get("decision", {})
        value.update(
            planned=counts.get("planned"),
            graded=counts.get("graded"),
            incomplete=counts.get("incomplete"),
            regressions=decision.get("regressed_cases"),
            answer=decision.get("state", "Not observed"),
            tools="Not applicable — text comparison executes no tools",
        )
        # A persisted terminal job without a reducer result is not a passing run.
        if counts and decision:
            value["availability"] = "available"
        for family, field in (
            ("selection", "routing_verdict"),
            ("delivery", "delivery_verdict"),
        ):
            verdicts = set(record.hub_skill_attempts.values_list(field, flat=True))
            value[family] = (
                "fail"
                if "fail" in verdicts
                else (
                    "pass"
                    if verdicts == {"pass"}
                    else (
                        "Not applicable"
                        if verdicts == {"not_applicable"}
                        else "Not observed"
                    )
                )
            )
    else:
        value["planned"] = len(record.planned_matrix)
        value.update(native_outcomes(record))
    return value


def _family_outcome(cells, family):
    verdicts = {cell[family] for cell in cells}
    if not cells or verdicts - {"pass", "fail", "not_applicable"}:
        # Incomplete, unverifiable or ungraded work never reads as an outcome.
        return "fail" if "fail" in verdicts else "Not observed"
    if "fail" in verdicts:
        return "fail"
    return "pass" if "pass" in verdicts else "Not applicable"


def summarize_native(reduced, arms, titles):
    """Map one native reduction to review outcomes. Pure; never upgrades a result."""
    cells = reduced["cells"]
    candidate = [cell for cell in cells if cell["variant"] == "candidate"]
    return {
        "planned": len(cells),
        "graded": sum(
            cell["status"] == "completed" and not cell["incomplete"] for cell in cells
        ),
        "incomplete": sum(cell["incomplete"] for cell in cells),
        "regressions": reduced["regressed_cases"],
        **{
            family: _family_outcome(candidate, family)
            for family in ("answer", "selection", "delivery", "tools")
        },
        "availability": "available",
        "result": {
            "state": reduced["state"],
            "reproduction": reduced["reproduction"],
            "repair_verified": reduced["repair_verified"],
        },
        "cases": [
            {
                "case_id": cell["case_id"],
                "title": titles.get(cell["case_id"], ""),
                "variant": cell["variant"],
                "version": (arms.get(cell["variant"]) or {}).get("version"),
                "repeat_index": cell["repeat_index"],
                "status": cell["status"],
                **{
                    family: cell[family]
                    for family in ("answer", "selection", "delivery", "tools")
                },
            }
            for cell in cells
        ],
    }


def native_outcomes(record):
    """Outcomes of a completed, retained skills-native run; empty otherwise."""
    from model_hub.services.skills_hub.r3.native_runs import _reduce
    from simulate.models.hosted_harness import HostedHarnessJob

    job = record.simulation_job
    if (
        job.state != HostedHarnessJob.State.COMPLETED
        or not job.payload.get("skills_context")
        or record.payload_purged_at is not None
    ):
        return {}
    control = job.payload.get("skills_native_control") or {}
    reduced, _ = _reduce(job)
    titles = {case["id"]: case.get("title", "") for case in control.get("cases", [])}
    return summarize_native(reduced, control.get("arms") or {}, titles)


def inspect_release(*, principal, expected_fingerprint, **selection):
    first = preview_release_packet(principal=principal, **selection)
    if first["fingerprint"] != expected_fingerprint:
        raise SkillsHubError(
            "review_conflict", "The reviewed packet changed.", status=409
        )
    packet = first["packet"]
    versions = HubSkillVersion.objects.filter(skill_id=packet["subject_id"])
    after = versions.get(pk=packet["candidate_version_id"])
    before_number = packet["transition"]["expected_version"]
    before = versions.get(number=before_number) if before_number is not None else None

    def files(version):
        if version is None:
            return []
        require_distribution_eligibility(version)
        return [
            {"path": row.path, "content": row.blob.content}
            for row in version.files.select_related("blob").order_by("path")
        ]

    capabilities = capability_changes(before=files(before), after=files(after))
    summaries = [_summary(principal, ref) for ref in packet["evidence"]]
    target_configuration = None
    if packet["target"] is not None:
        target_configuration = HubSkillContextBinding.objects.get(
            pk=packet["target"]["binding_id"],
            context_id=packet["subject_id"],
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        ).configuration
    # Recheck after every slow read: never bind new evidence/annotations to an
    # old fingerprint or make a best-effort partial inspection look complete.
    last = preview_release_packet(principal=principal, **selection)
    if last["fingerprint"] != expected_fingerprint:
        raise SkillsHubError(
            "review_conflict", "The review changed during inspection.", status=409
        )
    return {
        "fingerprint": expected_fingerprint,
        "capabilities": capabilities,
        "summaries": summaries,
        "target_configuration": target_configuration,
    }
