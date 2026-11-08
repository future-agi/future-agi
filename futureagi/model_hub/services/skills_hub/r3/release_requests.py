"""Exact immutable review packets and explicit, currently authorized execution.

The existing label service owns pointer/event writes. This module owns only the
review handoff and its pre-move/currentness receipt, never a second release log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkill, HubSkillLabelEvent, HubSkillVersion
from model_hub.models.skills_hub_r3 import (
    HubSkillReleaseDecision,
    HubSkillReleasePacket,
    HubSkillReleaseRecipient,
    HubSkillReleaseRequest,
)
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextDependency,
    HubSkillContextOperation,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import _set_label_locked, _validate_name
from model_hub.services.skills_hub.r3.human_review import evidence_reference
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.v11.evidence import currentness
from model_hub.services.skills_hub.v11.governance import authorize_label_change_locked
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.resolution_leases import (
    require_distribution_eligibility,
)


def _error(code: str, message: str, status: int = 409) -> SkillsHubError:
    return SkillsHubError(code, message, status=status)


def _review_value(value: object) -> object:
    """DRF can normalize 0 to 0.0 and UTC timestamps to another aware zone."""
    if isinstance(value, dict):
        return {key: _review_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_review_value(item) for item in value]
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            raise ValueError("An evidence timestamp must be aware.")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _review_digest(value: object) -> str:
    try:
        return canonical_digest(_review_value(value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise _error(
            "invalid_release_request", "Use a valid immutable review identity.", 400
        ) from exc


def _scope(principal: SkillPrincipal) -> dict:
    return {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
    }


def _authorize(principal: SkillPrincipal, *, write: bool) -> None:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=write)


def _workspace_lock(principal: SkillPrincipal) -> None:
    Workspace.no_workspace_objects.select_for_update(no_key=True).get(
        pk=principal.scope.workspace_id,
        organization_id=principal.scope.organization_id,
        is_active=True,
    )


def _skill(
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: str,
    *,
    lock: bool = False,
    candidate_id: UUID | None = None,
) -> HubSkill:
    if kind not in {"skill", "agent_context"}:
        raise _error("resource_not_found", "Resource not found.", 404)
    rows = HubSkill.no_workspace_objects.filter(**_scope(principal))
    if lock:
        children = HubSkillContextDependency.objects.filter(
            **_scope(principal), parent_version_id=candidate_id
        ).values_list("child_skill_id", flat=True)
        ids = {resource_id, *children}
        # Child quarantine and parent publication share the same sorted lock order.
        list(
            HubSkill.all_objects.filter(**_scope(principal), pk__in=ids)
            .select_for_update()
            .order_by("pk")
        )
    result = rows.filter(pk=resource_id, kind=kind).first()
    if result is None:
        raise _error("resource_not_found", "Resource not found.", 404)
    return result


def release_impact(*, principal: SkillPrincipal, skill: HubSkill, label: str) -> str:
    """Only followers of the affected label participate; receipts never do."""
    rows = HubSkillContextBinding.objects.filter(
        **_scope(principal),
        context=skill,
        retired_at__isnull=True,
        selector={"label": label},
    ).order_by("pk")
    return canonical_digest(
        {
            "subject_id": str(skill.pk),
            "label": label,
            "bindings": list(
                rows.values(
                    "id", "revision", "selector", "update_mode", "configuration_digest"
                )
            ),
        }
    )


def _target(
    principal: SkillPrincipal, skill: HubSkill, target: dict | None
) -> dict | None:
    if target is None:
        return None
    if target.get("environment_id") is not None or target.get("binding_id") is None:
        raise _error(
            "runtime_incompatible",
            "Choose a qualified context binding; environment targets are not yet supported.",
            422,
        )
    row = HubSkillContextBinding.objects.filter(
        **_scope(principal),
        context=skill,
        pk=target["binding_id"],
        retired_at__isnull=True,
    ).first()
    if row is None:
        raise _error("resource_not_found", "Target not found.", 404)
    actual = {
        "binding_id": str(row.pk),
        "environment_id": None,
        "revision": row.revision,
        "configuration_digest": row.configuration_digest,
    }
    expected = {**target, "binding_id": str(target["binding_id"])}
    if actual != expected:
        raise _error(
            "target_configuration_changed", "The reviewed runtime target changed."
        )
    if (
        not row.configuration_digest
        or canonical_digest(row.configuration) != row.configuration_digest
    ):
        raise _error(
            "runtime_incompatible",
            "The target has no qualified immutable configuration.",
            422,
        )
    return actual


def _evidence(
    principal: SkillPrincipal,
    skill: HubSkill,
    candidate: HubSkillVersion,
    selections: list[dict],
    target: dict | None,
    *,
    lock: bool,
) -> list[dict]:
    if len(selections) > 20 or len(
        {(s["source"], str(s["source_id"])) for s in selections}
    ) != len(selections):
        raise _error(
            "invalid_evidence", "Choose at most twenty distinct evidence sources.", 400
        )
    refs = []
    for selection in sorted(
        selections, key=lambda s: (s["source"], str(s["source_id"]))
    ):
        ref = evidence_reference(
            principal=principal,
            source=selection["source"],
            source_id=selection["source_id"],
            shared=True,
            lock=lock,
        )
        if (
            ref["subject_id"] != str(skill.pk)
            or ref["subject_kind"] != skill.kind
            or ref["candidate_digest"] != candidate.digest
        ):
            raise _error(
                "evidence_mismatch", "Evidence must describe this exact candidate."
            )
        if ref["annotation_revision"] != selection["annotation_revision"]:
            raise _error(
                "annotation_conflict",
                "Human review changed; review the new annotation.",
            )
        if ref["source"] == "simulation_native" and (
            target is None
            or ref["configuration"]["digest"] != target["configuration_digest"]
        ):
            raise _error(
                "target_configuration_changed",
                "Native evidence does not apply to this runtime target.",
            )
        refs.append(ref)
    return refs


def _build_packet(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    candidate_id: UUID,
    transition: dict,
    target: dict | None,
    evidence: list[dict],
    external_pipeline: dict | None,
    lock: bool,
) -> dict:
    candidate = HubSkillVersion.objects.filter(skill=skill, pk=candidate_id).first()
    if candidate is None:
        raise _error("version_not_found", "Candidate version not found.", 404)
    require_distribution_eligibility(candidate)
    label = transition["label"]
    _validate_name(label)
    current = (
        skill.labels.filter(name=label)
        .values_list("version__number", flat=True)
        .first()
    )
    if (
        transition["version"] != candidate.number
        or transition["expected_version"] != current
        or transition["expected_release_revision"] != skill.release_revision
    ):
        raise _error("release_conflict", "The pointer or release policy changed.")
    if transition["expected_security_revision"] != skill.security_revision:
        raise _error("release_conflict", "The release security state changed.")
    if transition["expected_impact_fingerprint"] != release_impact(
        principal=principal, skill=skill, label=label
    ):
        raise _error("impact_changed", "The affected bindings changed.")
    actual_target = _target(principal, skill, target)
    refs = _evidence(principal, skill, candidate, evidence, actual_target, lock=lock)
    return {
        "schema": 1,
        "subject_kind": skill.kind,
        "subject_id": str(skill.pk),
        "candidate_version_id": str(candidate.pk),
        "candidate_digest": candidate.digest,
        "transition": transition,
        "target": actual_target,
        "evidence": refs,
        "external_pipeline": (
            {**external_pipeline, "recorded_by": str(external_pipeline["recorded_by"])}
            if external_pipeline is not None
            else None
        ),
    }


def _external(principal: SkillPrincipal, value: dict | None) -> dict | None:
    if value is None:
        return None
    from urllib.parse import urlsplit

    url = value.get("url", "")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or len(url) > 2000
    ):
        raise _error(
            "invalid_release_request",
            "Use an HTTPS external-pipeline reference without credentials.",
            400,
        )
    if value.get("trust") != "operator_recorded_unimported" or any(
        not isinstance(value.get(k), str) or not 0 < len(value[k]) <= limit
        for k, limit in (("configuration_identity", 255), ("decision", 2000))
    ):
        raise _error(
            "invalid_release_request",
            "Record the external pipeline decision without claiming imported verification.",
            400,
        )
    return {
        "url": url,
        "configuration_identity": value["configuration_identity"],
        "decision": value["decision"],
        "trust": "operator_recorded_unimported",
        "recorded_by": str(principal.user_id),
    }


def preview_release_packet(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: str,
    candidate_version_id: UUID,
    label: str,
    target: dict | None = None,
    evidence: list[dict] | None = None,
    external_pipeline: dict | None = None,
) -> dict:
    _authorize(principal, write=False)
    skill = _skill(principal, resource_id, kind)
    candidate = skill.versions.filter(pk=candidate_version_id).first()
    if candidate is None:
        raise _error("version_not_found", "Candidate version not found.", 404)
    transition = {
        "label": label,
        "version": candidate.number,
        "expected_version": skill.labels.filter(name=label)
        .values_list("version__number", flat=True)
        .first(),
        "expected_release_revision": skill.release_revision,
        "expected_security_revision": skill.security_revision,
        "expected_impact_fingerprint": release_impact(
            principal=principal, skill=skill, label=label
        ),
    }
    packet = _build_packet(
        principal=principal,
        skill=skill,
        candidate_id=candidate_version_id,
        transition=transition,
        target=target,
        evidence=evidence or [],
        external_pipeline=_external(principal, external_pipeline),
        lock=False,
    )
    return {"packet": packet, "fingerprint": _review_digest(packet)}


def _operation(
    principal: SkillPrincipal, name: str, key: UUID, body: object
) -> tuple[dict, str, HubSkillContextOperation | None]:
    lookup = {
        **_scope(principal),
        "principal_key": f"user:{principal.user_id}",
        "operation": name,
        "idempotency_key": key,
    }
    digest = _review_digest(body)
    old = HubSkillContextOperation.objects.filter(**lookup).first()
    if old and old.request_digest != digest:
        raise _error("idempotency_conflict", "The operation key has another request.")
    return lookup, digest, old


def _receipt(lookup: dict, digest: str, resource_id: UUID, response: dict) -> dict:
    HubSkillContextOperation.objects.create(
        **lookup,
        request_digest=digest,
        result_resource_id=resource_id,
        response=response,
        completed_at=timezone.now(),
    )
    return response


def _validate_packet(
    principal: SkillPrincipal, packet: dict, *, lock: bool
) -> tuple[HubSkill, dict]:
    skill = _skill(
        principal,
        UUID(str(packet["subject_id"])),
        packet["subject_kind"],
        lock=lock,
        candidate_id=packet["candidate_version_id"],
    )
    actual = _build_packet(
        principal=principal,
        skill=skill,
        candidate_id=packet["candidate_version_id"],
        transition=packet["transition"],
        target=packet["target"],
        evidence=packet["evidence"],
        external_pipeline=packet["external_pipeline"],
        lock=lock,
    )
    if _review_digest(actual) != _review_digest(packet):
        raise _error(
            "review_stale", "Evidence or candidate changed. Review a new packet."
        )
    return skill, actual


def _recipient_principal(principal: SkillPrincipal, user_id: UUID) -> SkillPrincipal:
    return SkillPrincipal(principal.scope, user_id, None, "human")


@transaction.atomic
def submit_release_request(
    *,
    principal: SkillPrincipal,
    packet: dict,
    expected_fingerprint: str,
    recipient_ids: list[UUID],
    idempotency_key: UUID,
    request_id: UUID | None = None,
    revision: int = 0,
) -> dict:
    _authorize(principal, write=True)
    assert principal.user_id is not None
    _workspace_lock(principal)
    lookup, digest, old = _operation(
        principal,
        "release_request",
        idempotency_key,
        [
            packet,
            expected_fingerprint,
            sorted(str(i) for i in recipient_ids),
            request_id,
            revision,
        ],
    )
    if old:
        assert old.result_resource_id is not None
        _request_packet(principal=principal, request_id=old.result_resource_id)
        return dict(old.response)
    if expected_fingerprint != _review_digest(packet):
        raise _error("review_stale", "The reviewed packet changed.")
    if (
        not 1 <= len(recipient_ids) <= 20
        or len(set(recipient_ids)) != len(recipient_ids)
        or principal.user_id in recipient_ids
    ):
        raise _error(
            "invalid_release_request",
            "Assign one to twenty distinct reviewers other than the author.",
            400,
        )
    for user_id in recipient_ids:
        try:
            _authorize(_recipient_principal(principal, user_id), write=True)
        except SkillsHubError as exc:
            raise _error("resource_not_found", "Reviewer unavailable.", 404) from exc
    skill, actual = _validate_packet(principal, packet, lock=True)
    external = actual["external_pipeline"]
    if external and external != _external(principal, external):
        raise _error(
            "invalid_release_request",
            "External decisions must be attributed to their recorder.",
            400,
        )
    request: HubSkillReleaseRequest | None
    if request_id is None:
        if revision != 0:
            raise _error("review_conflict", "A new request starts at revision zero.")
        request = HubSkillReleaseRequest.objects.create(
            **_scope(principal), skill=skill, author_id=principal.user_id
        )
    else:
        request = (
            HubSkillReleaseRequest.objects.select_for_update()
            .filter(
                **_scope(principal),
                pk=request_id,
                author_id=principal.user_id,
                skill=skill,
            )
            .first()
        )
        if request is None:
            raise _error("resource_not_found", "Release request not found.", 404)
        if request.revision != revision or request.status == "released":
            raise _error(
                "review_conflict", "This request changed or was already executed."
            )
        request.revision += 1
        request.status = "pending"
        request.save(update_fields=["revision", "status", "updated_at"])
    HubSkillReleasePacket.objects.create(
        **_scope(principal),
        request=request,
        revision=request.revision,
        fingerprint=_review_digest(actual),
        candidate_id=packet["candidate_version_id"],
        packet=actual,
    )
    request.hub_skill_recipients.exclude(recipient_id__in=recipient_ids).delete()
    for user_id in recipient_ids:
        HubSkillReleaseRecipient.objects.get_or_create(
            request=request, recipient_id=user_id, defaults=_scope(principal)
        )
    return _receipt(
        lookup,
        digest,
        request.pk,
        {"id": str(request.pk), "revision": request.revision, "status": request.status},
    )


def _request_packet(
    *, principal: SkillPrincipal, request_id: UUID
) -> tuple[HubSkillReleaseRequest, HubSkillReleasePacket]:
    _authorize(principal, write=False)
    request = (
        HubSkillReleaseRequest.objects.select_related("skill")
        .filter(
            **_scope(principal),
            pk=request_id,
            skill__deleted=False,
            skill__organization_id=principal.scope.organization_id,
            skill__workspace_id=principal.scope.workspace_id,
        )
        .first()
    )
    if request is None or (
        request.author_id != principal.user_id
        and not request.hub_skill_recipients.filter(
            recipient_id=principal.user_id
        ).exists()
    ):
        raise _error("resource_not_found", "Release request not found.", 404)
    if request.author_id != principal.user_id:
        _authorize(principal, write=True)
    return request, request.hub_skill_packets.get(revision=request.revision)


def get_release_receipt(*, principal: SkillPrincipal, request_id: UUID) -> dict:
    """Metadata-only execution history remains readable after detailed evidence expires."""
    request, _ = _request_packet(principal=principal, request_id=request_id)
    receipt = (
        HubSkillContextOperation.objects.filter(
            **_scope(principal),
            operation="execute_release",
            result_resource_id=request.pk,
        )
        .order_by("created_at", "pk")
        .first()
    )
    if receipt is None:
        raise _error("resource_not_found", "Release execution receipt not found.", 404)
    return dict(receipt.response)


def get_release_request(
    *, principal: SkillPrincipal, request_id: UUID
) -> tuple[HubSkillReleaseRequest, HubSkillReleasePacket]:
    request, packet = _request_packet(principal=principal, request_id=request_id)
    # Do not let retained packet JSON leak evidence after sharing is revoked.
    for ref in packet.packet["evidence"]:
        evidence_reference(
            principal=principal,
            source=ref["source"],
            source_id=ref["source_id"],
            shared=True,
        )
    return request, packet


@transaction.atomic
def decide_release_request(
    *,
    principal: SkillPrincipal,
    request_id: UUID,
    revision: int,
    expected_fingerprint: str,
    decision: str,
    comment: str,
    idempotency_key: UUID,
) -> dict:
    _authorize(principal, write=True)
    assert principal.user_id is not None
    _workspace_lock(principal)
    request, packet = _request_packet(principal=principal, request_id=request_id)
    lookup, digest, old = _operation(
        principal,
        "release_decision",
        idempotency_key,
        [request_id, revision, expected_fingerprint, decision, comment],
    )
    if old:
        return dict(old.response)
    if (
        decision not in {"approve", "request_changes"}
        or not isinstance(comment, str)
        or len(comment) > 2000
    ):
        raise _error(
            "invalid_release_request",
            "Use a supported decision and bounded comment.",
            400,
        )
    if (
        request.author_id == principal.user_id
        or not request.hub_skill_recipients.filter(
            recipient_id=principal.user_id
        ).exists()
    ):
        raise _error(
            "permission_denied", "An assigned independent reviewer must decide.", 403
        )
    if (
        request.revision != revision
        or packet.fingerprint != expected_fingerprint
        or request.status == "released"
    ):
        raise _error("review_conflict", "The release request changed.")
    _validate_packet(principal, packet.packet, lock=True)
    if packet.hub_skill_decisions.filter(actor_id=principal.user_id).exists():
        raise _error(
            "review_conflict",
            "This reviewer already decided this immutable packet; submit a new revision.",
        )
    row = HubSkillReleaseDecision.objects.create(
        **_scope(principal),
        packet=packet,
        actor_id=principal.user_id,
        decision=decision,
        comment=comment,
    )
    request.status = (
        "changes_requested"
        if packet.hub_skill_decisions.filter(decision="request_changes").exists()
        else "approved"
    )
    request.save(update_fields=["status", "updated_at"])
    return _receipt(
        lookup,
        digest,
        request.pk,
        {
            "id": str(row.pk),
            "request_id": str(request.pk),
            "revision": revision,
            "decision": decision,
            "status": request.status,
        },
    )


@transaction.atomic
def execute_release_request(
    *,
    principal: SkillPrincipal,
    request_id: UUID,
    revision: int,
    expected_fingerprint: str,
    idempotency_key: UUID,
) -> dict:
    _authorize(principal, write=True)
    assert principal.user_id is not None
    _workspace_lock(principal)
    request, packet = _request_packet(principal=principal, request_id=request_id)
    skill = _skill(
        principal,
        request.skill_id,
        request.skill.kind,
        lock=True,
        candidate_id=packet.candidate_id,
    )
    transition = packet.packet["transition"]
    # Authority also applies to replay receipts; an approval never grants a role.
    from model_hub.services.skills_hub.v11.principals import require_skill_admin

    if transition["label"] in skill.protected_label_names:
        require_skill_admin(principal=principal, skill=skill)
    lookup, digest, old = _operation(
        principal,
        "execute_release",
        idempotency_key,
        [request_id, revision, expected_fingerprint],
    )
    if old:
        return dict(old.response)
    if (
        request.revision != revision
        or packet.fingerprint != expected_fingerprint
        or request.status != "approved"
    ):
        raise _error(
            "review_conflict", "Approve the current packet before executing it."
        )
    approval = (
        packet.hub_skill_decisions.filter(decision="approve")
        .order_by("created_at", "id")
        .first()
    )
    if (
        approval is None
        or packet.hub_skill_decisions.filter(decision="request_changes").exists()
    ):
        raise _error("review_conflict", "This packet has no usable approval.")
    _authorize(_recipient_principal(principal, approval.actor_id), write=True)
    skill, actual = _validate_packet(principal, packet.packet, lock=True)
    authorize_label_change_locked(
        principal=principal,
        skill=skill,
        name=transition["label"],
        expected_version=transition["expected_version"],
        expected_release_revision=transition["expected_release_revision"],
    )
    before = {
        "packet_fingerprint": packet.fingerprint,
        "release_revision": skill.release_revision,
        "security_revision": skill.security_revision,
        "target": actual["target"],
        "evidence": [],
    }
    from model_hub.models.skills_hub_v11 import HubSkillTestRun

    for ref in actual["evidence"]:
        freshness = {
            "content": True,
            "payload_available": True,
            "target_configuration": True,
        }
        if ref["source"] == "skills_text":
            freshness = currentness(
                run=HubSkillTestRun.objects.get(pk=ref["source_id"]),
                skill=skill,
                candidate_digest=actual["candidate_digest"],
            )
        before["evidence"].append(
            {
                "source": ref["source"],
                "source_id": ref["source_id"],
                "annotation_revision": ref["annotation_revision"],
                "currentness": freshness,
            }
        )
    actor = Actor(
        user=User.objects.get(pk=principal.user_id),
        api_key=(
            OrgApiKey.no_workspace_objects.get(pk=principal.api_key_id)
            if principal.api_key_id
            else None
        ),
    )
    _set_label_locked(
        skill=skill,
        actor=actor,
        name=transition["label"],
        version_number=transition["version"],
        expected_digest=actual["candidate_digest"],
    )
    event = (
        None
        if transition["expected_version"] == transition["version"]
        else HubSkillLabelEvent.objects.get(
            skill=skill, release_revision=skill.release_revision
        )
    )
    after = {
        "label": transition["label"],
        "version": transition["version"],
        "release_revision": skill.release_revision,
        "event_id": str(event.pk) if event else None,
    }
    # The immutable decision remains untouched. A separate operation receipt binds
    # the actual executor and event, including no-op releases with no fabricated event.
    request.status = "released"
    request.save(update_fields=["status", "updated_at"])
    return _receipt(
        lookup,
        digest,
        request.pk,
        {
            "request_id": str(request.pk),
            "revision": revision,
            "decision_id": str(approval.pk),
            "executed_by": str(principal.user_id),
            "actor_api_key_id": (
                str(principal.api_key_id) if principal.api_key_id else None
            ),
            "currentness_at_action": before,
            "post_move_state": after,
        },
    )
