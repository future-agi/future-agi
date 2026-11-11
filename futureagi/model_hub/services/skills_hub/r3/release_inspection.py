"""Read-only, fingerprint-bound release inspection; never infer native success.

The existing packet service owns scope, visibility, expiry and currentness.
Native completion is NOT an outcome: until the authenticated native result
reader is available, show its planned denominator and unknown outcomes.
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
        for family, field in (("selection", "routing_verdict"), ("delivery", "delivery_verdict")):
            verdicts = set(record.hub_skill_attempts.values_list(field, flat=True))
            value[family] = (
                "fail" if "fail" in verdicts else
                "pass" if verdicts == {"pass"} else
                "Not applicable" if verdicts == {"not_applicable"} else "Not observed"
            )
    else:
        value["planned"] = len(record.planned_matrix)
    return value


def inspect_release(*, principal, expected_fingerprint, **selection):
    first = preview_release_packet(principal=principal, **selection)
    if first["fingerprint"] != expected_fingerprint:
        raise SkillsHubError("review_conflict", "The reviewed packet changed.", status=409)
    packet = first["packet"]
    versions = HubSkillVersion.objects.filter(skill_id=packet["subject_id"])
    after = versions.get(pk=packet["candidate_version_id"])
    before_number = packet["transition"]["expected_version"]
    before = versions.get(number=before_number) if before_number is not None else None

    def files(version):
        if version is None:
            return []
        require_distribution_eligibility(version)
        return [{"path": row.path, "content": row.blob.content}
                for row in version.files.select_related("blob").order_by("path")]

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
        raise SkillsHubError("review_conflict", "The review changed during inspection.", status=409)
    return {"fingerprint": expected_fingerprint, "capabilities": capabilities,
            "summaries": summaries, "target_configuration": target_configuration}
