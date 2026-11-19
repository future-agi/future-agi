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
from model_hub.models.skills_hub_v11 import HubSkillLabelPolicyEvent
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextDependency,
    HubSkillContextOperation,
    HubSkillContextQuarantine,
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
    # Close the author's stale requests first so the fresh request is the newest row in every inbox.
    _supersede_stale_requests(
        principal=principal,
        skill=skill,
        keep_id=request_id,
        candidate_id=packet["candidate_version_id"],
        label=actual["transition"]["label"],
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


_STALE_PACKET_CODES = frozenset(
    {
        "review_stale",
        "release_conflict",
        "impact_changed",
        "target_configuration_changed",
        "version_not_found",
    }
)


def _supersede_stale_requests(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    keep_id: UUID | None,
    candidate_id: object,
    label: str,
) -> None:
    """Close the author's other open requests for the same candidate and label that went stale.

    A fresh packet from the same author for the same candidate and label replaces
    a request that can no longer be approved or executed, so no reviewer has to
    open and decline it. Only a packet that fails the current-state check is
    closed; a still-current request, a released one, another candidate or label
    and another author's request are untouched. Closing grants and moves
    nothing: the status only records why the request cannot proceed.
    """
    others = HubSkillReleaseRequest.objects.select_for_update().filter(
        **_scope(principal),
        skill=skill,
        author_id=principal.user_id,
        status__in=("pending", "approved"),
    )
    if keep_id is not None:
        others = others.exclude(pk=keep_id)
    for other in others:
        packet = other.hub_skill_packets.filter(revision=other.revision).first()
        if (
            packet is None
            or str(packet.candidate_id) != str(candidate_id)
            or packet.packet["transition"]["label"] != label
        ):
            continue
        try:
            _validate_packet(principal, packet.packet, lock=True)
        except SkillsHubError as exc:
            if exc.code not in _STALE_PACKET_CODES:
                continue
            other.status = "superseded"
            other.save(update_fields=["status", "updated_at"])


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
    result = dict(receipt.response)
    # Enrich old durable receipts only after request-scoped authorization. Never
    # revalidate historical evidence or change the retained release identity.
    decision = HubSkillReleaseDecision.objects.select_related("actor", "packet").get(
        pk=result["decision_id"], packet__request=request
    )
    executor = User.objects.filter(pk=result["executed_by"]).first()
    result.setdefault("executed_at", receipt.created_at.isoformat())
    result.setdefault(
        "executed_by_name",
        (executor.name or executor.email) if executor else "Former member",
    )
    result.setdefault("approved_by_name", decision.actor.name or decision.actor.email)
    result.setdefault(
        "previous_version", decision.packet.packet["transition"]["expected_version"]
    )
    result.setdefault("name", request.skill.name)
    result.setdefault("subject_id", str(request.skill_id))
    result.setdefault("subject_kind", request.skill.kind)
    return result


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
        or request.status in {"released", "superseded"}
    ):
        raise _error("review_conflict", "The release request changed.")
    if decision == "approve":
        # Only an approval needs the packet to be current. Declining a packet that
        # went stale (a moved release, changed evidence) grants nothing and is the
        # only way a reviewer can close it instead of leaving it pending forever.
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
    executor = User.objects.get(pk=principal.user_id)
    return _receipt(
        lookup,
        digest,
        request.pk,
        {
            "request_id": str(request.pk),
            "revision": revision,
            "decision_id": str(approval.pk),
            "executed_by": str(principal.user_id),
            "executed_by_name": executor.name or executor.email,
            "approved_by_name": approval.actor.name or approval.actor.email,
            "executed_at": timezone.now().isoformat(),
            "previous_version": transition["expected_version"],
            "name": skill.name,
            "subject_id": str(skill.pk),
            "subject_kind": skill.kind,
            "actor_api_key_id": (
                str(principal.api_key_id) if principal.api_key_id else None
            ),
            "currentness_at_action": before,
            "post_move_state": after,
        },
    )


# --- J4: explicit reviewer renewal after a precondition race -----------------
# A race (target configuration, annotation, evidence expiry, release policy)
# makes an approved packet stale. The assigned reviewer may explicitly inspect
# what changed and create the next immutable packet revision for the SAME
# author-chosen candidate, transition label, target binding and evidence
# selection. Nothing is refreshed silently: the renewal is a separate attributed
# operation, the new revision starts pending, and only a new decision on it can
# authorize execution. Evidence that is no longer available is removed and
# reported, never substituted.

_UNAVAILABLE_EVIDENCE = {"evidence_expired", "resource_not_found"}


def _renewal_actor(principal: SkillPrincipal, request: HubSkillReleaseRequest) -> None:
    if (
        request.author_id == principal.user_id
        or not request.hub_skill_recipients.filter(
            recipient_id=principal.user_id
        ).exists()
    ):
        raise _error(
            "permission_denied",
            "Only an assigned independent reviewer can renew this review.",
            403,
        )
    if request.status == "released":
        raise _error("review_conflict", "This request was already executed.")


def _renewed_packet(
    principal: SkillPrincipal,
    request: HubSkillReleaseRequest,
    previous: dict,
    *,
    lock: bool,
) -> tuple[dict, list[dict], list[dict]]:
    skill = _skill(
        principal,
        request.skill_id,
        previous["subject_kind"],
        lock=lock,
        candidate_id=UUID(previous["candidate_version_id"]),
    )
    old = previous["transition"]
    label = old["label"]
    transition = {
        "label": label,
        "version": old["version"],
        "expected_version": skill.labels.filter(name=label)
        .values_list("version__number", flat=True)
        .first(),
        "expected_release_revision": skill.release_revision,
        "expected_security_revision": skill.security_revision,
        "expected_impact_fingerprint": release_impact(
            principal=principal, skill=skill, label=label
        ),
    }
    target = None
    if previous["target"] is not None:
        row = HubSkillContextBinding.objects.filter(
            **_scope(principal),
            context=skill,
            pk=previous["target"]["binding_id"],
            retired_at__isnull=True,
        ).first()
        if row is None:
            raise _error(
                "target_retired",
                "The reviewed target was retired. The author must choose a new target.",
            )
        target = {
            "binding_id": str(row.pk),
            "environment_id": None,
            "revision": row.revision,
            "configuration_digest": row.configuration_digest,
        }
    selections, removed = [], []
    for ref in previous["evidence"]:
        try:
            current = evidence_reference(
                principal=principal,
                source=ref["source"],
                source_id=ref["source_id"],
                shared=True,
                lock=lock,
            )
        except SkillsHubError as exc:
            if exc.code not in _UNAVAILABLE_EVIDENCE:
                raise
            removed.append(
                {
                    "source": ref["source"],
                    "source_id": ref["source_id"],
                    "reason": exc.code,
                }
            )
            continue
        selections.append(
            {
                "source": ref["source"],
                "source_id": ref["source_id"],
                "annotation_revision": current["annotation_revision"],
            }
        )
    packet = _build_packet(
        principal=principal,
        skill=skill,
        candidate_id=UUID(previous["candidate_version_id"]),
        transition=transition,
        target=target,
        evidence=selections,
        external_pipeline=previous["external_pipeline"],
        lock=lock,
    )
    changes = [
        {"item": f"transition.{key}", "before": old[key], "after": transition[key]}
        for key in sorted(transition)
        if old[key] != transition[key]
    ]
    if previous["target"] != packet["target"]:
        changes.append(
            {"item": "target", "before": previous["target"], "after": packet["target"]}
        )
    before = {(r["source"], r["source_id"]): r for r in previous["evidence"]}
    for ref in packet["evidence"]:
        old_ref = before[(ref["source"], ref["source_id"])]
        for key in ("annotation_revision", "status", "payload_expires_at"):
            if old_ref[key] != ref[key]:
                changes.append(
                    {
                        "item": f"evidence.{ref['source']}.{ref['source_id']}.{key}",
                        "before": old_ref[key],
                        "after": ref[key],
                    }
                )
    for dropped in removed:
        changes.append(
            {
                "item": f"evidence.{dropped['source']}.{dropped['source_id']}",
                "before": "attached",
                "after": dropped["reason"],
            }
        )
    return packet, changes, removed


# --- UXF-10: what changed in the release rules since approval ---------------
# A packet stores only the rule counters it was approved under
# (`expected_release_revision`, `expected_security_revision`), never the rule
# values. The values come from the append-only audit rows written in the same
# transaction as each counter bump: label-protection events carry the old and
# new protected names, quarantine events carry each version's state. An entry
# is reported only when the audit rows account for the counter delta exactly;
# any bump they don't account for is reported with before/after, who and when
# all null, which the dialog shows as "Values at approval weren't recorded".
# Label pointer moves also advance `release_revision`; they are not rule
# changes and produce no entry (the `changes` rows already report the move).
_POLICY_CHANGE_LIMIT = 100


def _person(user) -> str | None:
    return (user.name or user.email) if user is not None else None


def _unrecorded(field: str) -> dict:
    return {
        "field": field,
        "before": None,
        "after": None,
        "changed_by": None,
        "changed_at": None,
    }


def _policy_changes(
    principal: SkillPrincipal, skill_id: UUID, old: dict, new: dict
) -> list[dict]:
    scope = _scope(principal)
    changes: list[dict] = []
    release_from = old["expected_release_revision"]
    release_to = new["expected_release_revision"]
    if release_to > release_from:
        protection = list(
            HubSkillLabelPolicyEvent.objects.filter(
                **scope,
                skill_id=skill_id,
                from_revision__gte=release_from,
                to_revision__lte=release_to,
            )
            .select_related("actor")
            .order_by("to_revision")[:_POLICY_CHANGE_LIMIT]
        )
        moves = set(
            HubSkillLabelEvent.objects.filter(
                skill_id=skill_id,
                release_revision__gt=release_from,
                release_revision__lte=release_to,
            ).values_list("release_revision", flat=True)
        )
        accounted = moves | {event.to_revision for event in protection}
        for event in protection:
            changes.append(
                {
                    "field": "protected_labels",
                    "before": sorted(event.old_names),
                    "after": sorted(event.new_names),
                    "changed_by": _person(event.actor),
                    "changed_at": event.created_at,
                }
            )
        if any(
            revision not in accounted
            for revision in range(release_from + 1, release_to + 1)
        ):
            changes.append(_unrecorded("release_rules"))
    security_from = old["expected_security_revision"]
    security_to = new["expected_security_revision"]
    if security_to > security_from:
        delta = security_to - security_from
        # Every security bump is one quarantine event under the skill row lock,
        # so the newest `delta` events are exactly the ones after approval.
        limit = min(delta, _POLICY_CHANGE_LIMIT)
        events = list(
            HubSkillContextQuarantine.objects.filter(
                **scope, version__skill_id=skill_id
            )
            .select_related("version", "actor")
            .order_by("-created_at", "-revision")[:limit]
        )
        for event in reversed(events):
            # Revision 1 is the version's first event: it was not quarantined
            # before. A missing earlier event leaves the earlier state unknown.
            before = (
                False
                if event.revision == 1
                else HubSkillContextQuarantine.objects.filter(
                    **scope, version_id=event.version_id, revision=event.revision - 1
                )
                .values_list("active", flat=True)
                .first()
            )
            changes.append(
                {
                    "field": "quarantine",
                    "before": (
                        None
                        if before is None
                        else {"version": event.version.number, "quarantined": before}
                    ),
                    "after": {
                        "version": event.version.number,
                        "quarantined": event.active,
                    },
                    "changed_by": _person(event.actor),
                    "changed_at": event.created_at,
                }
            )
        if len(events) < delta:
            changes.append(_unrecorded("security_rules"))
    return changes


def release_renewal_preview(*, principal: SkillPrincipal, request_id: UUID) -> dict:
    _authorize(principal, write=True)
    request, packet = _request_packet(principal=principal, request_id=request_id)
    _renewal_actor(principal, request)
    renewed, changes, removed = _renewed_packet(
        principal, request, packet.packet, lock=False
    )
    fingerprint = _review_digest(renewed)
    if fingerprint == packet.fingerprint:
        raise _error("review_current", "This packet is still current.")
    policy_changes = _policy_changes(
        principal,
        request.skill_id,
        packet.packet["transition"],
        renewed["transition"],
    )
    return {
        "request_id": str(request.pk),
        "revision": request.revision,
        "status": request.status,
        "subject_id": str(request.skill_id),
        "previous_fingerprint": packet.fingerprint,
        "packet": renewed,
        "fingerprint": fingerprint,
        "changes": changes,
        "removed_evidence": removed,
        "policy_changes": policy_changes,
    }


@transaction.atomic
def renew_release_request(
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
    lookup, digest, old = _operation(
        principal,
        "release_renewal",
        idempotency_key,
        [request_id, revision, expected_fingerprint],
    )
    if old:
        _request_packet(principal=principal, request_id=request_id)
        return dict(old.response)
    request, packet = _request_packet(principal=principal, request_id=request_id)
    request = HubSkillReleaseRequest.objects.select_for_update().get(pk=request.pk)
    _renewal_actor(principal, request)
    if request.revision != revision:
        raise _error("review_conflict", "The release request changed.")
    packet = request.hub_skill_packets.get(revision=request.revision)
    renewed, changes, removed = _renewed_packet(
        principal, request, packet.packet, lock=True
    )
    fingerprint = _review_digest(renewed)
    if fingerprint == packet.fingerprint:
        raise _error("review_current", "This packet is still current.")
    if fingerprint != expected_fingerprint:
        raise _error(
            "review_stale", "Preconditions changed again. Inspect the new renewal."
        )
    request.revision += 1
    request.status = "pending"
    request.save(update_fields=["revision", "status", "updated_at"])
    HubSkillReleasePacket.objects.create(
        **_scope(principal),
        request=request,
        revision=request.revision,
        fingerprint=fingerprint,
        candidate_id=renewed["candidate_version_id"],
        packet=renewed,
    )
    # The renewing reviewer has inspected this exact revision; other assigned
    # reviewers still see it as unread.
    request.hub_skill_recipients.filter(recipient_id=principal.user_id).update(
        last_seen_revision=request.revision
    )
    return _receipt(
        lookup,
        digest,
        request.pk,
        {
            "id": str(request.pk),
            "revision": request.revision,
            "status": request.status,
            "renewed_by": str(principal.user_id),
            "from_revision": revision,
            "changes": changes,
            "removed_evidence": removed,
        },
    )
