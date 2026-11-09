"""Fixed-inventory, reviewed repository campaigns. No Git/network work runs here.

The operational models are integrated; the independent feature switch still
fails closed. Importing this module never writes or enables a slice.
Plans and Git observations are client-reported, not remote attestations.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import NoReturn, TypeVar
from uuid import UUID, uuid4

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkill, HubSkillVersion
from model_hub.models.skills_hub_v12 import (
    HubSkillContextBinding,
    HubSkillContextCampaign,
    HubSkillContextCampaignTarget,
    HubSkillContextOperation,
    HubSkillContextReceipt,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.principals import (
    effective_scope_level,
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.credentials import canonical_digest
from model_hub.services.skills_hub.v12.resolution_leases import (
    require_distribution_eligibility,
)
from tfc.constants.levels import Level

MAX_TARGETS = 100
MAX_PLAN_BYTES = 65536
MAX_ATTEMPTS = 100
CLAIM_SECONDS = 300
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_PLAN_FIELDS = {
    "campaign_revision",
    "binding_revision",
    "repository_id",
    "base_commit",
    "previous_lock_digest",
    "desired_version_id",
    "materialization_digest",
    "plan_digest",
    "changes",
    "local_review_required",
    "blockers",
}
_CHANGE_FIELDS = {
    "path",
    "action",
    "before_sha256",
    "after_sha256",
    "ownership",
    "diff_preview",
}


def _fail(code: str, message: str, status: int = 409) -> NoReturn:
    raise SkillsHubError(code, message, status=status)


def _fields(value: dict, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        _fail("invalid_campaign", "Supply exactly the documented fields.", 400)


def _uuid(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        _fail("invalid_campaign", "A valid UUID is required.", 400)


def _integer(value: object, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        _fail("invalid_campaign", "A bounded integer is required.", 400)


def _text(value: object, maximum: int, *, empty: bool = False) -> None:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not empty and not value.strip())
        or any(ord(char) < 32 and char not in "\n\t\r" for char in value)
    ):
        _fail("invalid_campaign", "Text is missing or exceeds its limit.", 400)


def _hash(value: object, *, nullable: bool = False, bare: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not (_SHA if bare else _DIGEST).fullmatch(value):
        _fail("invalid_campaign", "A canonical SHA-256 digest is required.", 400)


def _scope(principal: SkillPrincipal) -> dict:
    return {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
    }


def require_campaigns_enabled() -> None:
    if not getattr(settings, "SKILLS_CONTEXT_CAMPAIGNS_ENABLED", False):
        _fail("skills_unavailable", "Repository campaigns are not enabled.", 503)


def _authorize(principal: SkillPrincipal, *, write: bool = False) -> None:
    if not isinstance(principal, SkillPrincipal):
        _fail("principal_not_supported", "Use a real user-bound identity.", 403)
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=write)


def _admin(principal: SkillPrincipal) -> None:
    # Bindings currently have no ownership column. Never infer ownership from
    # names, context authorship, or a client assertion; use the specified admin path.
    if effective_scope_level(principal=principal) < Level.WORKSPACE_ADMIN:
        _fail(
            "permission_denied", "A current workspace administrator is required.", 403
        )


def _operation(
    principal: SkillPrincipal, operation: str, key: UUID, body: dict
) -> tuple[dict, str, dict | None]:
    lookup = {
        **_scope(principal),
        "principal_key": f"{principal.kind}:{principal.api_key_id or principal.user_id}",
        "operation": operation,
        "idempotency_key": _uuid(key),
    }
    digest = canonical_digest(body)
    existing = HubSkillContextOperation.objects.filter(**lookup).first()
    if existing and existing.request_digest != digest:
        _fail("idempotency_conflict", "This operation key has a different request.")
    return lookup, digest, copy.deepcopy(existing.response) if existing else None


def _complete(lookup: dict, digest: str, response: dict, resource_id: UUID) -> dict:
    HubSkillContextOperation.objects.create(
        **lookup,
        request_digest=digest,
        response=response,
        result_resource_id=resource_id,
        completed_at=timezone.now(),
    )
    return response


def _campaign(
    principal: SkillPrincipal, campaign_id: UUID, *, lock: bool = False
) -> HubSkillContextCampaign:
    query = HubSkillContextCampaign.objects.filter(**_scope(principal), pk=campaign_id)
    if lock:
        query = query.select_for_update()
    campaign = query.first()
    if campaign is None:
        _fail("resource_not_found", "Campaign not found.", 404)
    return campaign


def _targets(
    principal: SkillPrincipal, campaign: HubSkillContextCampaign
) -> models.QuerySet[HubSkillContextCampaignTarget]:
    return (
        HubSkillContextCampaignTarget.objects.filter(
            **_scope(principal), campaign=campaign
        )
        .select_related("binding", "desired_version", "desired_version__skill")
        .order_by("binding_id")
    )


@contextmanager
def _locked(
    principal: SkillPrincipal, campaign_id: UUID
) -> Iterator[tuple[HubSkillContextCampaign, list[HubSkillContextCampaignTarget]]]:
    _authorize(principal, write=True)
    with transaction.atomic():
        campaign = _campaign(principal, campaign_id)
        context_ids = list(
            _targets(principal, campaign).values_list("binding__context_id", flat=True)
        )
        list(
            HubSkill.all_objects.filter(**_scope(principal), pk__in=context_ids)
            .order_by("pk")
            .select_for_update()
        )
        campaign = _campaign(principal, campaign_id, lock=True)
        list(
            HubSkillContextBinding.objects.filter(
                **_scope(principal), pk__in=campaign.target_ids
            )
            .order_by("pk")
            .select_for_update()
        )
        _authorize(principal, write=True)
        yield campaign, list(_targets(principal, campaign))


def _target(
    targets: list[HubSkillContextCampaignTarget], binding_id: UUID
) -> HubSkillContextCampaignTarget:
    for target in targets:
        if target.binding_id == _uuid(binding_id):
            return target
    _fail("resource_not_found", "Campaign target not found.", 404)


def _revision(campaign: HubSkillContextCampaign, revision: int) -> None:
    _integer(revision, 1)
    if campaign.revision != revision:
        _fail("campaign_conflict", "The campaign changed. Review it again.")


def _live_target(target: HubSkillContextCampaignTarget) -> None:
    binding, version = target.binding, target.desired_version
    if binding.retired_at or binding.context.deleted or binding.kind != "repository":
        _fail("target_plan_changed", "The repository binding is no longer available.")
    if version.skill_id != binding.context_id or version.skill.kind != "agent_context":
        _fail("target_plan_changed", "The target release does not match its binding.")
    require_distribution_eligibility(version)
    if binding.revision != target.binding_revision:
        _fail(
            "target_plan_changed", "The binding changed. Create a newly reviewed plan."
        )


def _repository_id(binding: HubSkillContextBinding) -> str:
    repository = binding.repository
    if not isinstance(repository, dict) or repository.get("provider") != "github":
        _fail("target_plan_changed", "The repository identity is unavailable.")
    repository_id = repository.get("repository_id")
    if not isinstance(repository_id, str) or not repository_id:
        _fail("target_plan_changed", "The repository identity is unavailable.")
    return repository_id


def _plan_current(target: HubSkillContextCampaignTarget) -> dict:
    _live_target(target)
    plan = target.plan
    binding = target.binding
    if not isinstance(plan, dict) or not plan or plan["blockers"]:
        _fail("target_plan_changed", "A complete unblocked plan is required.")
    if plan["repository_id"] != _repository_id(binding):
        _fail("target_plan_changed", "The repository identity changed.")
    if (
        "version" in binding.selector
        and binding.selector["version"] != target.desired_version.number
    ):
        _fail("target_plan_changed", "This binding is deliberately pinned. Exclude it.")
    return plan


def _manifest(
    campaign: HubSkillContextCampaign, targets: list[HubSkillContextCampaignTarget]
) -> str:
    return canonical_digest(
        {
            "schema": 1,
            "campaign_id": str(campaign.pk),
            "parent_campaign_id": (
                str(campaign.parent_campaign_id)
                if campaign.parent_campaign_id
                else None
            ),
            "targets": [
                {
                    "binding_id": str(target.binding_id),
                    "binding_revision": target.binding_revision,
                    "version_id": str(target.desired_version_id),
                    "digest": target.desired_version.digest,
                    "plan_digest": target.plan_digest,
                }
                for target in sorted(targets, key=lambda row: str(row.binding_id))
            ],
        }
    )


def _summary(campaign: HubSkillContextCampaign) -> dict:
    return {
        "id": str(campaign.pk),
        "name": campaign.name,
        "revision": campaign.revision,
        "status": campaign.status,
        "target_count": len(campaign.target_ids),
        "manifest_digest": campaign.manifest_digest,
        "parent_campaign_id": (
            str(campaign.parent_campaign_id) if campaign.parent_campaign_id else None
        ),
        "authorized_at": (
            campaign.authorized_at.isoformat() if campaign.authorized_at else None
        ),
        "reason": campaign.reason,
        "created_at": campaign.created_at.isoformat(),
        "provenance": "client_reported",
    }


def _target_out(target: HubSkillContextCampaignTarget) -> dict:
    attempts = target.attempts
    latest = attempts[-1] if attempts else None
    confirmed = [row for row in attempts if row["state"] in {"pr_open", "merged"}]
    git_state = (
        confirmed[-1]["state"]
        if confirmed
        else latest["state"] if latest else "not_started"
    )
    return {
        "binding_id": str(target.binding_id),
        "name": target.binding.name,
        "binding_revision": target.binding_revision,
        "selector": target.binding.selector,
        "desired_version": target.desired_version.number,
        "desired_version_id": str(target.desired_version_id),
        "desired_digest": target.desired_version.digest,
        "repository": target.binding.repository,
        "plan": target.plan,
        "plan_digest": target.plan_digest,
        "decision": target.decision,
        "reason": target.reason,
        "attempt": latest,
        "pr_state": git_state,
        "merge_state": "merged" if git_state == "merged" else "unknown",
        "provenance": "client_reported",
    }


CampaignRow = TypeVar("CampaignRow", bound=models.Model)


def _page(
    query: models.QuerySet[CampaignRow],
    page: int,
    page_size: int,
    mapper: Callable[[CampaignRow], dict],
) -> dict:
    _integer(page, 1)
    _integer(page_size, 1)
    if page_size > 100 or page > 10000:
        _fail("invalid_campaign", "Pagination exceeds its limit.", 400)
    count = query.count()
    return {
        "results": [
            mapper(row) for row in query[(page - 1) * page_size : page * page_size]
        ],
        "count": count,
        "page": page,
        "page_size": page_size,
        "total_pages": (count + page_size - 1) // page_size,
    }


def list_campaigns(
    *, principal: SkillPrincipal, page: int = 1, page_size: int = 25
) -> dict:
    _authorize(principal)
    return _page(
        HubSkillContextCampaign.objects.filter(**_scope(principal)).order_by(
            "-created_at", "id"
        ),
        page,
        page_size,
        _summary,
    )


def get_campaign(*, principal: SkillPrincipal, campaign_id: UUID) -> dict:
    _authorize(principal)
    campaign = _campaign(principal, campaign_id)
    return {
        **_summary(campaign),
        "can_authorize": effective_scope_level(principal=principal)
        >= Level.WORKSPACE_ADMIN,
        "enabled": bool(getattr(settings, "SKILLS_CONTEXT_CAMPAIGNS_ENABLED", False)),
    }


def list_campaign_targets(
    *, principal: SkillPrincipal, campaign_id: UUID, page: int = 1, page_size: int = 25
) -> dict:
    _authorize(principal)
    campaign = _campaign(principal, campaign_id)
    return _page(_targets(principal, campaign), page, page_size, _target_out)


@transaction.atomic
def create_campaign(
    *, principal: SkillPrincipal, request: dict, idempotency_key: UUID
) -> dict:
    _authorize(principal, write=True)
    require_campaigns_enabled()
    _fields(request, {"name", "targets", "parent_campaign_id"})
    _text(request["name"], 120)
    selected = request["targets"]
    if not isinstance(selected, list) or not 1 <= len(selected) <= MAX_TARGETS:
        _fail("invalid_campaign", "Select between 1 and 100 repository bindings.", 400)
    for item in selected:
        _fields(item, {"binding_id", "version"})
        _uuid(item["binding_id"])
        _integer(item["version"], 1)
    ids = [_uuid(item["binding_id"]) for item in selected]
    if len(set(ids)) != len(ids):
        _fail("invalid_campaign", "A binding can appear only once.", 400)
    if request["parent_campaign_id"] is not None:
        _uuid(request["parent_campaign_id"])
    Workspace.no_workspace_objects.select_for_update().get(
        pk=principal.scope.workspace_id, organization_id=principal.scope.organization_id
    )
    lookup, digest, replay = _operation(
        principal, "create_campaign", idempotency_key, request
    )
    if replay is not None:
        return replay
    parent = (
        _campaign(principal, request["parent_campaign_id"])
        if request["parent_campaign_id"]
        else None
    )
    if parent and not set(map(str, ids)).issubset(set(parent.target_ids)):
        _fail(
            "invalid_campaign",
            "A reverse campaign may contain only its parent's targets.",
            400,
        )
    bindings = list(
        HubSkillContextBinding.objects.filter(
            **_scope(principal), pk__in=ids
        ).select_related("context")
    )
    if len(bindings) != len(ids):
        _fail("resource_not_found", "A selected binding is unavailable.", 404)
    list(
        HubSkill.all_objects.filter(
            **_scope(principal), pk__in=[row.context_id for row in bindings]
        )
        .order_by("pk")
        .select_for_update()
    )
    bindings = list(
        HubSkillContextBinding.objects.filter(**_scope(principal), pk__in=ids)
        .select_related("context")
        .order_by("pk")
        .select_for_update(of=("self",))
    )
    versions = {str(item["binding_id"]): item["version"] for item in selected}
    assert principal.user_id is not None  # The private principal was authorized.
    campaign = HubSkillContextCampaign.objects.create(
        **_scope(principal),
        name=request["name"],
        target_ids=sorted(map(str, ids)),
        author_id=principal.user_id,
        parent_campaign=parent,
    )
    for binding in bindings:
        if (
            binding.kind != "repository"
            or binding.retired_at
            or binding.context.deleted
            or binding.context.kind != "agent_context"
            or not binding.repository
            or binding.repository.get("provider") != "github"
        ):
            _fail(
                "resource_not_found",
                "A selected repository binding is unavailable.",
                404,
            )
        version = HubSkillVersion.objects.filter(
            skill=binding.context, number=versions[str(binding.pk)]
        ).first()
        if version is None:
            _fail("resource_not_found", "A selected release is unavailable.", 404)
        require_distribution_eligibility(version)
        HubSkillContextCampaignTarget.objects.create(
            **_scope(principal),
            campaign=campaign,
            binding=binding,
            binding_revision=binding.revision,
            desired_version=version,
        )
    return _complete(lookup, digest, _summary(campaign), campaign.pk)


def _safe_destination(path: str) -> None:
    _text(path, 255)
    parts = path.split("/")
    if (
        len(parts) > 8
        or any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in parts)
        or "\\" in path
        or any(ord(char) < 32 for char in path)
    ):
        _fail("destination_forbidden", "The plan contains an unsafe path.", 422)
    # Only profile-owned outputs. Supporting material lives in the immutable
    # generation or the native skill subtree, never in arbitrary repo locations.
    allowed = path in {"AGENTS.md", "CLAUDE.md", ".futureagi-context.lock.json"}
    allowed = allowed or path.startswith(
        (".futureagi/context/", ".agents/skills/", ".claude/skills/")
    )
    if not allowed or any(part.lower() == ".git" for part in parts):
        _fail(
            "destination_forbidden",
            "This destination is not an instruction output.",
            422,
        )


def target_plan_digest(plan: dict) -> str:
    """Hash exact review bytes, excluding transport CAS and the digest itself."""
    return canonical_digest(
        {
            key: value
            for key, value in plan.items()
            if key not in {"campaign_revision", "plan_digest"}
        }
    )


def validate_target_plan(plan: dict) -> dict:
    _fields(plan, _PLAN_FIELDS)
    _integer(plan["campaign_revision"], 1)
    _integer(plan["binding_revision"], 1)
    _text(plan["repository_id"], 100)
    if not isinstance(plan["base_commit"], str) or not _COMMIT.fullmatch(
        plan["base_commit"]
    ):
        _fail("invalid_campaign", "An exact repository base commit is required.", 400)
    _uuid(plan["desired_version_id"])
    for key in ("plan_digest", "materialization_digest"):
        _hash(plan[key])
    _hash(plan["previous_lock_digest"], nullable=True)
    if (
        type(plan["local_review_required"]) is not bool
        or not isinstance(plan["blockers"], list)
        or len(plan["blockers"]) > 50
    ):
        _fail("invalid_campaign", "Invalid local review requirements.", 400)
    for blocker in plan["blockers"]:
        _text(blocker, 200)
    if not isinstance(plan["changes"], list) or len(plan["changes"]) > 500:
        _fail("invalid_campaign", "The change inventory exceeds its limit.", 400)
    paths: set[str] = set()
    for change in plan["changes"]:
        _fields(change, _CHANGE_FIELDS)
        _safe_destination(change["path"])
        folded = change["path"].casefold()
        if folded in paths or any(
            folded.startswith(old + "/") or old.startswith(folded + "/")
            for old in paths
        ):
            _fail("invalid_campaign", "Paths collide in the reviewed plan.", 400)
        paths.add(folded)
        if change["ownership"] not in {"managed", "adopted"} or change[
            "action"
        ] not in {"add", "replace", "remove"}:
            _fail("invalid_campaign", "Unknown change action or ownership.", 400)
        _hash(change["before_sha256"], nullable=True, bare=True)
        _hash(change["after_sha256"], nullable=True, bare=True)
        before, after = change["before_sha256"], change["after_sha256"]
        if (
            (change["action"] == "add" and (before is not None or after is None))
            or (change["action"] == "remove" and (before is None or after is not None))
            or (
                change["action"] == "replace"
                and (before is None or after is None or before == after)
            )
        ):
            _fail("invalid_campaign", "The change does not match its preimages.", 400)
        if change["ownership"] == "adopted" and (
            change["action"] == "remove" or not plan["local_review_required"]
        ):
            _fail(
                "invalid_campaign",
                "Adoption needs local review and cannot delete local policy.",
                400,
            )
        _text(change["diff_preview"], 8192, empty=True)
    if (
        len(json.dumps(plan, ensure_ascii=False, default=str).encode("utf-8"))
        > MAX_PLAN_BYTES
    ):
        _fail("payload_too_large", "The plan exceeds 64 KiB.", 413)
    if target_plan_digest(plan) != plan["plan_digest"]:
        _fail(
            "target_plan_changed",
            "The submitted plan digest does not match its review.",
        )
    retained = copy.deepcopy(plan)
    retained["desired_version_id"] = str(_uuid(retained["desired_version_id"]))
    return retained


def record_target_plan(
    *, principal: SkillPrincipal, campaign_id: UUID, binding_id: UUID, request: dict
) -> dict:
    _authorize(principal, write=True)
    require_campaigns_enabled()
    plan = validate_target_plan(request)
    with _locked(principal, campaign_id) as (campaign, targets):
        _revision(campaign, plan["campaign_revision"])
        if campaign.status != "draft":
            _fail(
                "campaign_conflict",
                "Authorized plans are immutable. Create a new campaign.",
            )
        target = _target(targets, binding_id)
        _live_target(target)
        if (
            plan["binding_revision"] != target.binding_revision
            or plan["desired_version_id"] != str(target.desired_version_id)
            or plan["repository_id"] != _repository_id(target.binding)
        ):
            _fail("target_plan_changed", "The plan no longer matches its fixed target.")
        target.plan, target.plan_digest = plan, plan["plan_digest"]
        target.save(update_fields=["plan", "plan_digest"])
        campaign.revision += 1
        campaign.manifest_digest = _manifest(campaign, targets)
        campaign.save(update_fields=["revision", "manifest_digest"])
        return {
            "target": _target_out(target),
            "revision": campaign.revision,
            "plan_digest": target.plan_digest,
        }


def authorize_campaign(
    *,
    principal: SkillPrincipal,
    campaign_id: UUID,
    revision: int,
    expected_manifest_digest: str,
    decisions: list[dict],
    acknowledge_repository_writes: bool = False,
) -> dict:
    _authorize(principal, write=True)
    require_campaigns_enabled()
    _admin(principal)
    if acknowledge_repository_writes is not True:
        _fail("invalid_campaign", "Explicitly acknowledge repository writes.", 400)
    with _locked(principal, campaign_id) as (campaign, targets):
        _admin(principal)
        _revision(campaign, revision)
        if campaign.status != "draft" or expected_manifest_digest != _manifest(
            campaign, targets
        ):
            _fail("campaign_conflict", "Review the current complete campaign manifest.")
        if not isinstance(decisions, list) or len(decisions) != len(targets):
            _fail("invalid_campaign", "Decide every fixed target exactly once.", 400)
        chosen = {}
        for decision in decisions:
            _fields(decision, {"binding_id", "plan_digest", "decision", "reason"})
            bid = str(_uuid(decision["binding_id"]))
            _text(decision["reason"], 500, empty=True)
            if bid in chosen or decision["decision"] not in {"include", "exclude"}:
                _fail(
                    "invalid_campaign", "Decide every fixed target exactly once.", 400
                )
            chosen[bid] = decision
        if set(chosen) != set(campaign.target_ids):
            _fail("invalid_campaign", "Decisions must match the fixed target set.", 400)
        for target in targets:
            decision = chosen[str(target.binding_id)]
            if decision["plan_digest"] != target.plan_digest:
                _fail("target_plan_changed", "A target plan changed after review.")
            if decision["decision"] == "include":
                plan = _plan_current(target)
                if plan["local_review_required"] and not decision["reason"].strip():
                    _fail(
                        "invalid_campaign",
                        "Record the local-instruction review for this target.",
                        400,
                    )
            elif not decision["reason"].strip():
                _fail("invalid_campaign", "Explain each exclusion.", 400)
            target.decision, target.reason = decision["decision"], decision["reason"]
            target.save(update_fields=["decision", "reason"])
        campaign.status = (
            "authorized"
            if any(row.decision == "include" for row in targets)
            else "settled"
        )
        campaign.authorizer_id = principal.user_id
        campaign.authorized_at = timezone.now()
        campaign.manifest_digest = expected_manifest_digest
        campaign.revision += 1
        campaign.save(
            update_fields=[
                "status",
                "authorizer",
                "authorized_at",
                "manifest_digest",
                "revision",
            ]
        )
        return _summary(campaign)


def claim_campaign_target(
    *,
    principal: SkillPrincipal,
    campaign_id: UUID,
    binding_id: UUID,
    idempotency_key: UUID,
    plan_digest: str,
) -> dict:
    _authorize(principal, write=True)
    require_campaigns_enabled()
    _hash(plan_digest)
    with _locked(principal, campaign_id) as (campaign, targets):
        target = _target(targets, binding_id)
        if campaign.authorizer_id is None:
            _fail("campaign_conflict", "This campaign has no current authorizer.")
        _admin(SkillPrincipal(principal.scope, campaign.authorizer_id, None, "human"))
        body = {
            "campaign_id": str(campaign_id),
            "binding_id": str(binding_id),
            "plan_digest": plan_digest,
        }
        lookup, digest, replay = _operation(
            principal, "claim_campaign_target", idempotency_key, body
        )
        if replay is not None:
            require_distribution_eligibility(target.desired_version)
            # Recover the original attempt, never repeat its external write.
            return {
                **replay,
                "replay": True,
                "mode": "reconcile_only",
                "campaign_status": campaign.status,
            }
        if target.decision != "include":
            _fail("campaign_conflict", "This target is excluded or not authorized.")
        _plan_current(target)
        if target.plan_digest != plan_digest:
            _fail("target_plan_changed", "Claim the exact authorized plan.")
        attempts = copy.deepcopy(target.attempts)
        previous = attempts[-1] if attempts else None
        if previous and previous["state"] == "merged":
            _fail("campaign_conflict", "This target already has a merged pull request.")
        if (
            previous
            and datetime.fromisoformat(previous["expires_at"]) > timezone.now()
            and previous["state"] == "claimed"
        ):
            _fail("campaign_conflict", "This target already has an active claim.")
        # A lost process/response may already have written. Every subsequent claim
        # is read-only reconciliation, even if a previous client reported failed.
        mode = "reconcile_only" if previous else "execute"
        if mode == "execute" and campaign.status not in {"authorized", "running"}:
            _fail("campaign_conflict", "This campaign does not allow new Git writes.")
        if mode == "reconcile_only" and campaign.status == "draft":
            _fail("campaign_conflict", "This campaign has not been authorized.")
        if len(attempts) >= MAX_ATTEMPTS:
            _fail(
                "campaign_conflict",
                "The reconciliation limit was reached. Inspect the repository.",
            )
        generation = previous["claim_generation"] + 1 if previous else 1
        attempt = {
            "attempt_id": str(uuid4()),
            "claim_generation": generation,
            "expires_at": (
                timezone.now() + timedelta(seconds=CLAIM_SECONDS)
            ).isoformat(),
            "approved_plan_digest": plan_digest,
            "mode": mode,
            "state": "claimed",
            "reporter_key": lookup["principal_key"],
            "receipt": None,
            "branch": f"futureagi/context/{campaign.pk}/{target.binding_id}",
            "marker": f"futureagi-context:{campaign.pk}:{target.binding_id}:{plan_digest}",
        }
        attempts.append(attempt)
        target.attempts = attempts
        target.save(update_fields=["attempts"])
        if campaign.status == "authorized":
            campaign.status = "running"
            campaign.revision += 1
            campaign.save(update_fields=["status", "revision"])
        response = {
            key: value
            for key, value in attempt.items()
            if key not in {"reporter_key", "receipt", "state"}
        }
        return _complete(
            lookup,
            digest,
            {**response, "replay": False, "campaign_status": campaign.status},
            target.pk,
        )


def record_campaign_attempt(
    *, principal: SkillPrincipal, campaign_id: UUID, binding_id: UUID, request: dict
) -> dict:
    _authorize(principal, write=True)
    # Recording an already sent outcome remains possible while the feature is off.
    _fields(
        request,
        {
            "attempt_id",
            "claim_generation",
            "state",
            "repository_commit",
            "pull_request_number",
            "reason",
        },
    )
    request = {**request, "attempt_id": str(_uuid(request["attempt_id"]))}
    _integer(request["claim_generation"], 1)
    _text(request["reason"], 500, empty=True)
    if request["state"] not in {"pr_open", "merged", "failed", "unknown"}:
        _fail("invalid_receipt", "Unknown campaign attempt state.", 400)
    if request["repository_commit"] is not None and (
        not isinstance(request["repository_commit"], str)
        or not _COMMIT.fullmatch(request["repository_commit"])
    ):
        _fail("invalid_receipt", "An exact repository commit is required.", 400)
    if request["pull_request_number"] is not None:
        _integer(request["pull_request_number"], 1)
    if request["state"] in {"pr_open", "merged"} and (
        request["repository_commit"] is None or request["pull_request_number"] is None
    ):
        _fail(
            "invalid_receipt",
            "Pull-request observations need a commit and PR number.",
            400,
        )
    with _locked(principal, campaign_id) as (campaign, targets):
        target = _target(targets, binding_id)
        attempts = copy.deepcopy(target.attempts)
        matches = [
            row for row in attempts if row["attempt_id"] == str(request["attempt_id"])
        ]
        if not matches:
            _fail("resource_not_found", "Attempt not found.", 404)
        attempt = matches[0]
        reporter = f"{principal.kind}:{principal.api_key_id or principal.user_id}"
        if attempt["reporter_key"] != reporter:
            _fail("resource_not_found", "Attempt not found.", 404)
        if attempt["receipt"] is not None:
            if attempt["receipt"] != request:
                _fail(
                    "receipt_conflict",
                    "This immutable attempt already has a different receipt.",
                )
            return copy.deepcopy(attempt)
        if (
            attempt is not attempts[-1]
            or attempt["claim_generation"] != request["claim_generation"]
        ):
            _fail(
                "receipt_conflict", "A newer reconciliation claim fences this attempt."
            )
        prior_prs = {
            row["receipt"]["pull_request_number"]
            for row in attempts
            if row["receipt"] and row["receipt"]["pull_request_number"] is not None
        }
        if (
            request["pull_request_number"] is not None
            and prior_prs
            and prior_prs != {request["pull_request_number"]}
        ):
            _fail(
                "receipt_conflict", "Reconciliation must retain the same pull request."
            )
        attempt["state"], attempt["receipt"] = request["state"], copy.deepcopy(request)
        target.attempts = attempts
        target.save(update_fields=["attempts"])
        terminal = all(
            row.decision == "exclude"
            or (
                row.attempts
                and row.attempts[-1]["state"] in {"pr_open", "merged", "failed"}
            )
            for row in targets
        )
        if terminal and campaign.status in {"running", "authorized"}:
            campaign.status = "settled"
            campaign.revision += 1
            campaign.save(update_fields=["status", "revision"])
        return copy.deepcopy(attempt)


def change_campaign_status(
    *,
    principal: SkillPrincipal,
    campaign_id: UUID,
    revision: int,
    action: str,
    reason: str,
) -> dict:
    _authorize(principal, write=True)
    _admin(principal)
    _text(reason, 500)
    if action == "resume":
        require_campaigns_enabled()
    allowed = {
        "pause": {"authorized", "running"},
        "resume": {"paused"},
        "cancel": {"draft", "authorized", "running", "paused"},
    }
    if action not in allowed:
        _fail("invalid_campaign", "Unknown campaign action.", 400)
    with _locked(principal, campaign_id) as (campaign, targets):
        _admin(principal)
        _revision(campaign, revision)
        if campaign.status not in allowed[action]:
            _fail("campaign_conflict", "This campaign cannot make that transition.")
        # Resume only opens admission; every target rechecks its own immutable
        # plan. A stale target never invalidates or refreshes its unaffected peers.
        campaign.status = {
            "pause": "paused",
            "resume": "authorized",
            "cancel": "cancelled",
        }[action]
        campaign.reason = reason
        campaign.revision += 1
        campaign.save(update_fields=["status", "reason", "revision"])
        return _summary(campaign)


def preview_campaign_rollback(
    *, principal: SkillPrincipal, campaign_id: UUID, revision: int
) -> dict:
    _authorize(principal)
    campaign = _campaign(principal, campaign_id)
    _revision(campaign, revision)
    results = []
    for target in _targets(principal, campaign):
        plan = target.plan
        changes = (
            []
            if not plan
            else [
                {
                    **change,
                    "action": {"add": "remove", "remove": "add", "replace": "replace"}[
                        change["action"]
                    ],
                    "before_sha256": change["after_sha256"],
                    "after_sha256": change["before_sha256"],
                    "diff_preview": "Recompute the reverse diff from the retained local lock and current repository.",
                }
                for change in reversed(plan["changes"])
            ]
        )
        results.append(
            {
                "binding_id": str(target.binding_id),
                "previous_lock_digest": plan["previous_lock_digest"] if plan else None,
                "base_commit": plan["base_commit"] if plan else None,
                "reverse_changes": changes,
                "state": "local_review_required",
                "blockers": [
                    "current_repository_preimages_required",
                    "retained_previous_lock_required",
                ],
            }
        )
    return {
        "parent_campaign_id": str(campaign.pk),
        "revision": campaign.revision,
        "targets": results,
        "executable": False,
    }


def campaign_target_observations(
    *, principal: SkillPrincipal, campaign_id: UUID, binding_id: UUID
) -> dict:
    """Independent retained facts, never PR-to-install or merge-to-load inference."""
    _authorize(principal)
    campaign = _campaign(principal, campaign_id)
    target = _target(list(_targets(principal, campaign)), binding_id)
    receipts = HubSkillContextReceipt.objects.filter(
        **_scope(principal),
        binding_id=binding_id,
        version_id=target.desired_version_id,
        digest=target.desired_version.digest,
        expires_at__gt=timezone.now(),
    )
    stages = {}
    for stage in ("installed", "discovered", "instructions_delivered"):
        receipt = receipts.filter(stage=stage).order_by("-created_at", "-id").first()
        stages[stage] = (
            None
            if receipt is None
            else {
                "id": str(receipt.pk),
                "version_id": str(receipt.version_id),
                "digest": receipt.digest,
                "observed_at": receipt.observed_at.isoformat(),
                "origin": receipt.origin,
                "stale": (timezone.now() - receipt.created_at).total_seconds()
                > target.binding.expected_report_interval_seconds,
            }
        )
    return {"binding_id": str(binding_id), "coverage": "declared_binding", **stages}
