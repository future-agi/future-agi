"""Owner-private GitHub previews and atomic, unpublished source-derived drafts."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import unquote, urlsplit
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.skills_hub import HubSkill, HubSkillDraft, HubSkillVersion
from model_hub.models.skills_hub_v11 import (
    HubSkillGitHubPreview,
    HubSkillGitHubSource,
    HubSkillJob,
)
from model_hub.services.skills_hub.checks import run_checks
from model_hub.services.skills_hub.drafts import save_draft
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256, manifest_digest
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
    require_skill_admin,
)
from model_hub.services.skills_hub.v11.repository_zip import (
    parse_repository_zip,
    validate_repository_path,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.versions import create_skill
from tfc.utils import github_public

_SHA = re.compile(r"^[0-9a-f]{40}$")
_TERMINAL = {"completed", "failed", "cancelled", "timed_out", "interrupted"}


@dataclass(frozen=True)
class GitHubSource:
    repo: str
    path: str
    ref: str
    ref_kind: str


def _error(
    code: str, message: str, status: int = 409, **detail: object
) -> SkillsHubError:
    return SkillsHubError(code, message, status=status, detail=detail)


def parse_github_source(*, url: str, path: str | None, ref: str | None) -> GitHubSource:
    """Parse once, with explicit tree/blob boundaries; never probe ambiguous refs."""
    if (
        not isinstance(url, str)
        or len(url) > 2048
        or re.search(r"[\\\s\x00-\x1f\x7f]", url)
    ):
        raise _error(
            "github_source_invalid", "Use an HTTPS public GitHub repository URL.", 400
        )
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Not a repository URL")
        if re.search(r"%(?:2f|5c|25)", parsed.path, re.I):
            raise ValueError("Encoded separators")
        decoded = unquote(parsed.path, errors="strict")
        parts = decoded.removeprefix("/").removesuffix("/").split("/")
        if len(parts) < 2:
            raise ValueError("Repository missing")
        repo = "/".join(parts[:2])
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not github_public.safe_repository(repo):
            raise ValueError("Repository invalid")
        if len(parts) > 2:
            if len(parts) < 4 or parts[2] not in {"tree", "blob"}:
                raise ValueError("Unsupported GitHub URL")
            if ref is None or path is None:
                raise _error(
                    "github_source_ambiguous",
                    "Confirm the branch/tag and folder separately for this tree or file URL.",
                    400,
                    suggested_ref=parts[3],
                    suggested_path="/".join(
                        parts[4:-1] if parts[2] == "blob" else parts[4:]
                    ),
                )
            tail = "/".join(parts[3:])
            expected = (
                (github_public.short_ref(ref) or "HEAD")
                + ("/" + path if path else "")
                + ("/SKILL.md" if parts[2] == "blob" else "")
            )
            # A caller cannot confirm a different target while retaining a misleading URL.
            if tail != expected:
                raise ValueError("Confirmed URL does not match")
        path = path or ""
        if not validate_repository_path(path):
            raise ValueError("Invalid folder")
        ref = ref or "HEAD"
        if not github_public.safe_ref(ref) or (
            ref.startswith("refs/")
            and not ref.startswith(("refs/heads/", "refs/tags/"))
        ):
            raise ValueError("Invalid ref")
        if re.fullmatch(r"[0-9a-fA-F]{7,40}", ref) and not _SHA.fullmatch(ref):
            raise ValueError("Use full lowercase SHA")
    except (ValueError, UnicodeError) as exc:
        raise _error(
            "github_source_invalid",
            "Use a valid repository, relative folder and branch, tag or full commit SHA.",
            400,
        ) from exc
    kind = (
        "default"
        if ref == "HEAD"
        else "commit"
        if _SHA.fullmatch(ref)
        else "tag"
        if ref.startswith("refs/tags/")
        else "branch"
    )
    return GitHubSource(repo, path, ref, kind)


def _require_enabled() -> None:
    if not getattr(settings, "SKILLS_GITHUB_ENABLED", False):
        raise _error(
            "skills_unavailable",
            "GitHub skill imports are not enabled on this deployment.",
            503,
        )


def _authorize(principal: SkillPrincipal) -> None:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)


def _skill(scope: SkillScope, skill_id: UUID, *, lock: bool = False) -> HubSkill:
    rows = HubSkill.no_workspace_objects.filter(
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        deleted=False,
    )
    if lock:
        rows = rows.select_for_update()
    try:
        return rows.get(pk=skill_id)
    except HubSkill.DoesNotExist as exc:
        raise _error("resource_not_found", "Skill not found.", 404) from exc


def _owned(
    principal: SkillPrincipal, preview_id: UUID, *, lock: bool = False
) -> HubSkillGitHubPreview:
    rows = HubSkillGitHubPreview.objects.filter(
        job__organization_id=principal.scope.organization_id,
        job__workspace_id=principal.scope.workspace_id,
        job__owner_user_id=principal.user_id,
        job__kind="github",
    )
    if lock:
        rows = rows.select_for_update(of=("self",))
    try:
        preview = rows.select_related("job").get(pk=preview_id)
    except HubSkillGitHubPreview.DoesNotExist as exc:
        raise _error("resource_not_found", "Preview not found.", 404) from exc
    if preview.job.skill_id:
        _skill(principal.scope, preview.job.skill_id)
    return preview


def _live(preview: HubSkillGitHubPreview) -> None:
    if preview.expires_at is None or preview.expires_at <= timezone.now():
        raise _error(
            "github_preview_expired",
            "This GitHub preview expired. Preview the source again.",
            410,
        )


def _request_digest(request: dict, skill_id: UUID | None) -> str:
    payload = json.dumps(
        {"request": request, "skill_id": str(skill_id) if skill_id else None},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _replay(
    principal: SkillPrincipal, key: UUID, digest: str
) -> HubSkillGitHubPreview | None:
    job = HubSkillJob.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        owner_user_id=principal.user_id,
        kind="github",
        idempotency_key=key,
    ).first()
    if job is None:
        return None
    if job.request_digest != digest:
        raise _error(
            "idempotency_conflict",
            "This request key was already used for different inputs.",
        )
    if job.skill_id:
        _skill(principal.scope, job.skill_id)
    return HubSkillGitHubPreview.objects.get(job=job)


def _source(scope: SkillScope, skill: HubSkill) -> HubSkillGitHubSource:
    try:
        return HubSkillGitHubSource.objects.get(
            skill=skill,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
        )
    except HubSkillGitHubSource.DoesNotExist as exc:
        raise _error(
            "source_not_found", "This skill has no adopted GitHub source.", 404
        ) from exc


def create_github_preview(
    *,
    principal: SkillPrincipal,
    idempotency_key: UUID,
    request: dict,
    skill_id: UUID | None = None,
) -> HubSkillGitHubPreview:
    from model_hub.services.skills_hub.v11.jobs import (
        create_job_locked,
        dispatch_job,
        lock_quota,
    )

    _authorize(principal)
    digest = _request_digest(request, skill_id)
    replay = _replay(principal, idempotency_key, digest)
    if replay:
        return replay
    _require_enabled()
    continuation = "parent_preview_id" in request
    allowed = (
        {"parent_preview_id", "path"}
        if continuation
        else {"expected_source_revision"}
        if skill_id
        else {"url", "path", "ref"}
    )
    if set(request) - allowed or (continuation and set(request) != allowed):
        raise _error(
            "github_source_invalid",
            "Use either a repository URL or a parent preview and discovered root.",
            400,
        )
    with transaction.atomic():
        lock_quota(principal.scope.organization_id)
        if skill_id:
            _skill(principal.scope, skill_id, lock=True)
        job = create_job_locked(
            principal=principal,
            kind="github",
            skill_id=skill_id,
            idempotency_key=idempotency_key,
            request_digest=digest,
            deadline_at=timezone.now() + timedelta(seconds=60),
            limits={},
        )
        existing = HubSkillGitHubPreview.objects.filter(job=job).first()
        if existing:
            return existing
        values = {
            "job": job,
            "operation": "update" if skill_id else "import",
            "license": {
                "status": "unknown",
                "declared": None,
                "files": [],
                "requires_admin_review": True,
            },
        }
        if continuation:
            parent = _owned(principal, request["parent_preview_id"])
            _live(parent)
            if (
                parent.job.skill_id != skill_id
                or parent.outcome != "needs_path"
                or request["path"] not in parent.roots
            ):
                raise _error(
                    "github_source_invalid",
                    "Choose a discovered root from this preview.",
                    400,
                )
            values.update(
                {
                    name: getattr(parent, name)
                    for name in (
                        "repo",
                        "repository_id",
                        "ref",
                        "ref_kind",
                        "commit_sha",
                        "source_revision",
                        "expected_latest_version",
                        "expected_latest_digest",
                    )
                }
            )
            values.update(parent_preview=parent, path=request["path"])
        elif skill_id:
            skill = _skill(principal.scope, skill_id, lock=True)
            source = _source(principal.scope, skill)
            if request.get("expected_source_revision") != source.revision:
                raise _error(
                    "source_conflict",
                    "The tracked source changed. Reload it.",
                    revision=source.revision,
                )
            latest = skill.versions.get(number=skill.latest_version_number)
            values.update(
                {
                    name: getattr(source, name)
                    for name in ("repo", "repository_id", "path", "ref", "ref_kind")
                }
            )
            values.update(
                source_revision=source.revision,
                expected_latest_version=latest.number,
                expected_latest_digest=latest.digest,
            )
        else:
            parsed = parse_github_source(
                url=request.get("url"), path=request.get("path"), ref=request.get("ref")
            )
            values.update(
                repo=parsed.repo,
                path=parsed.path,
                ref=parsed.ref,
                ref_kind=parsed.ref_kind,
            )
        preview = HubSkillGitHubPreview.objects.create(**values)
        transaction.on_commit(
            lambda: dispatch_job(job_id=job.pk, scope=principal.scope)
        )
        return preview


def _principal(job: HubSkillJob, scope: SkillScope) -> SkillPrincipal:
    return SkillPrincipal(
        scope=scope,
        user_id=job.owner_user_id,
        api_key_id=job.actor_api_key_id,
        kind=job.principal_kind,
    )


def _guard(scope: SkillScope, job_id: UUID, generation: int) -> None:
    job = HubSkillJob.objects.get(
        pk=job_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        kind="github",
    )
    if (
        job.status != "running"
        or job.claim_generation != generation
        or job.cancel_requested_at
    ):
        raise _error("execution_unknown", "This preview is no longer active.")
    if job.deadline_at <= timezone.now():
        raise _error("queue_timeout", "The preview deadline expired.")
    _authorize(_principal(job, scope))
    _require_enabled()
    if job.skill_id:
        _skill(scope, job.skill_id)


def _resolve_preview(
    preview: HubSkillGitHubPreview, scope: SkillScope, generation: int
) -> dict:
    deadline = time.monotonic() + max(
        0, min(60, (preview.job.deadline_at - timezone.now()).total_seconds())
    )
    source = (
        _source(scope, _skill(scope, preview.job.skill_id))
        if preview.operation == "update"
        else None
    )
    result = {
        name: getattr(preview, name)
        for name in ("repo", "repository_id", "path", "ref", "ref_kind", "commit_sha")
    }
    if source and source.revision != preview.source_revision:
        raise _error(
            "source_conflict",
            "The tracked source changed during this check.",
            revision=source.revision,
        )
    if source and source.ref_kind == "commit":
        return {**result, "commit_sha": source.imported_sha, "outcome": "pinned"}
    if not preview.commit_sha:
        _guard(scope, preview.pk, generation)
        metadata = github_public.repository_metadata(
            repo=preview.repo,
            organization_id=str(scope.organization_id),
            deadline=deadline,
        )
        if preview.repository_id and preview.repository_id != metadata["repository_id"]:
            raise _error(
                "github_source_unavailable", "The repository identity changed.", 404
            )
        result.update(repo=metadata["repo"], repository_id=metadata["repository_id"])
        _guard(scope, preview.pk, generation)
        result["ref_kind"] = (
            source.ref_kind
            if source
            else github_public.resolve_ref_kind(
                repo=result["repo"],
                ref=preview.ref,
                default_branch=metadata["default_branch"],
                organization_id=str(scope.organization_id),
                deadline=deadline,
            )
        )
        resolved_ref = preview.ref
        if result["ref_kind"] in {"branch", "tag"} and not resolved_ref.startswith(
            "refs/"
        ):
            namespace = "heads" if result["ref_kind"] == "branch" else "tags"
            resolved_ref = f"refs/{namespace}/{resolved_ref}"
        _guard(scope, preview.pk, generation)
        head = github_public.resolve_sha(
            repo=result["repo"],
            ref=resolved_ref,
            organization_id=str(scope.organization_id),
            deadline=deadline,
        )
        result["commit_sha"] = head["sha"]
        # HEAD remains tracked as default, never converted to a pinned commit.
    if source and result["commit_sha"] == source.imported_sha:
        return {**result, "outcome": "unchanged_commit"}
    _guard(scope, preview.pk, generation)
    raw = github_public.download_archive(
        repo=result["repo"],
        sha=result["commit_sha"],
        organization_id=str(scope.organization_id),
        deadline=deadline,
    )
    parsed = parse_repository_zip(raw, path=preview.path)
    if time.monotonic() >= deadline:
        raise _error("queue_timeout", "The preview deadline expired.")
    if source and parsed["roots"] != [preview.path]:
        # An update tracks one exact root, not a newly discovered nested skill.
        return {
            **result,
            "outcome": "source_missing",
            "roots": parsed["roots"],
            "bundle": None,
        }
    for item in parsed["license"]["files"]:
        item["url_at_commit"] = (
            f"https://github.com/{result['repo']}/blob/{result['commit_sha']}/{item['path']}"
        )
    result.update(parsed)
    if parsed["bundle"]:
        result["path"] = parsed["bundle"]["root"]
    if source and parsed["bundle"] and parsed["outcome"] != "blocked":
        from model_hub.services.skills_hub.compare import compare_file_sets

        skill = _skill(scope, preview.job.skill_id)
        latest = skill.versions.get(number=preview.expected_latest_version)
        before = [
            {"path": row.path, "content": row.blob.content}
            for row in latest.files.select_related("blob")
        ]
        result["diff"] = {
            **compare_file_sets(before, parsed["bundle"]["files"]),
            "from": latest.number,
            "to": "draft",
        }
        result["local_diverged"] = (
            preview.expected_latest_digest != source.upstream_digest
        )
        result["outcome"] = (
            "unchanged_content"
            if parsed["bundle"]["digest"] == source.upstream_digest
            else "changed"
        )
    if result.get("bundle"):
        result["bundle"]["files"] = [
            {
                **item,
                "sha256": file_sha256(item["content"]),
                "size_bytes": len(item["content"].encode("utf-8")),
            }
            for item in result["bundle"]["files"]
        ]
    return result


def execute_github_preview(*, scope: SkillScope, preview_id: UUID) -> dict:
    """Claim once, release DB locks for I/O, and fence every terminal write."""
    with transaction.atomic():
        try:
            job = HubSkillJob.objects.select_for_update().get(
                pk=preview_id,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                kind="github",
            )
        except HubSkillJob.DoesNotExist as exc:
            raise _error("resource_not_found", "Preview not found.", 404) from exc
        # Native start_job claims generation 1 before this one-shot activity.
        # Generation 2 is the fetch claim, also used by direct queued execution.
        if job.status not in {"queued", "running"} or job.claim_generation >= 2:
            return {"id": str(job.pk), "status": job.status}
        job.status = "running"
        job.claim_generation = 2
        job.started_at = job.started_at or timezone.now()
        job.heartbeat_at = timezone.now()
        job.save(
            update_fields=["status", "claim_generation", "started_at", "heartbeat_at"]
        )
    preview = HubSkillGitHubPreview.objects.select_related("job").get(pk=preview_id)
    error = None
    try:
        _guard(scope, preview_id, job.claim_generation)
        result = _resolve_preview(preview, scope, job.claim_generation)
    except SkillsHubError as exc:
        error = exc
        result = {
            "outcome": "blocked"
            if exc.code in {"github_bundle_invalid", "license_restricted"}
            else "error"
        }
    with transaction.atomic():
        # Source/latest share the same skill lock as staging and publication.
        skill = _skill(scope, job.skill_id, lock=True) if job.skill_id else None
        current = HubSkillJob.objects.select_for_update().get(pk=job.pk)
        if (
            current.claim_generation != job.claim_generation
            or current.status in _TERMINAL
        ):
            return {"id": str(current.pk), "status": current.status}
        if current.cancel_requested_at:
            current.status = "cancelled"
            result = {"outcome": "error"}
        elif current.deadline_at <= timezone.now():
            current.status = "timed_out"
            error = _error("queue_timeout", "The preview deadline expired.")
            result = {"outcome": "error"}
        else:
            current.status = "failed" if error else "completed"
        if skill and not error and current.status == "completed":
            source = _source(scope, skill)
            updated = HubSkillGitHubSource.objects.filter(
                pk=source.pk, revision=preview.source_revision
            ).update(
                revision=source.revision + 1,
                checked_sha=result.get("commit_sha"),
                checked_at=timezone.now(),
                check_status=result["outcome"],
            )
            if not updated:
                error = _error(
                    "source_conflict", "The source changed while checking for updates."
                )
                current.status = "failed"
                result = {"outcome": "error"}
            else:
                result["source_revision"] = source.revision + 1
        if error:
            current.failure_code = error.code
            current.failure_detail = {**error.detail, "message": error.message}
        current.finished_at = current.heartbeat_at = current.reservation_released_at = (
            timezone.now()
        )
        current.save()
        HubSkillGitHubPreview.objects.filter(pk=preview_id).update(
            **result, expires_at=timezone.now() + timedelta(minutes=15)
        )
        return {"id": str(current.pk), "status": current.status}


def _rights(
    principal: SkillPrincipal,
    preview: HubSkillGitHubPreview,
    request: dict,
    skill: HubSkill,
) -> dict:
    rights = request.get("rights", {})
    if (
        not isinstance(rights, dict)
        or set(rights) != {"confirmed", "review_note"}
        or rights.get("confirmed") is not True
        or not isinstance(rights.get("review_note"), str)
        or len(rights["review_note"]) > 2000
    ):
        raise _error(
            "license_review_required", "Confirm your rights to import these files.", 422
        )
    if preview.license.get("status") == "restricted":
        raise _error(
            "license_restricted", "These license terms do not permit this import.", 422
        )
    if preview.license.get("status") not in {"identified"}:
        require_skill_admin(principal=principal, skill=skill)
        if not rights["review_note"].strip():
            raise _error(
                "license_review_required",
                "An admin must record a rights-review note.",
                422,
            )
    return {
        **rights,
        "reviewed_by": str(principal.user_id),
        "actor_api_key": str(principal.api_key_id) if principal.api_key_id else None,
        "reviewed_at": timezone.now().isoformat(),
    }


def _provenance(preview: HubSkillGitHubPreview) -> dict:
    return {
        "type": "import",
        "provider": "github",
        **{
            key: getattr(preview, key)
            for key in ("repo", "repository_id", "path", "ref", "ref_kind")
        },
        "commit_sha": preview.commit_sha,
        "upstream_digest": preview.bundle["digest"],
        "imported_at": timezone.now().isoformat(),
        "local_modifications": False,
        "license": preview.license,
        "source_revision": preview.source_revision,
        "preview_id": str(preview.pk),
    }


def stage_github_preview(
    *, principal: SkillPrincipal, preview_id: UUID, request: dict
) -> dict:
    _authorize(principal)
    preview = _owned(principal, preview_id)
    allowed = (
        {"preview_id", "rights"}
        if preview.operation == "import"
        else {
            "preview_id",
            "revision",
            "expected_source_revision",
            "expected_latest_version",
            "expected_latest_digest",
            "accept_replacement",
            "rights",
        }
    )
    if set(request) - allowed or (
        "preview_id" in request and str(request["preview_id"]) != str(preview_id)
    ):
        raise _error(
            "github_source_invalid", "Use the preview's import or update request.", 400
        )
    with transaction.atomic():
        if preview.operation == "import":
            from model_hub.services.skills_hub.v11.jobs import lock_quota

            # Serialize initial acceptance before a destination skill exists. This
            # avoids taking a preview lock before the new skill/draft locks.
            lock_quota(principal.scope.organization_id)
        skill = (
            _skill(principal.scope, preview.job.skill_id, lock=True)
            if preview.job.skill_id
            else None
        )
        if skill:
            # Lock any existing draft before the preview/job row.
            list(
                HubSkillDraft.objects.select_for_update().filter(
                    skill=skill, user_id=principal.user_id
                )
            )
        preview = _owned(principal, preview_id, lock=skill is not None)
        if preview.consumed_skill_id:
            destination = _skill(principal.scope, preview.consumed_skill_id)
            return {
                "skill": destination,
                "draft": None,
                "published": False,
                "replayed": True,
                "consumed": {
                    "skill_id": str(destination.pk),
                    "draft_revision": preview.consumed_draft_revision,
                },
            }
        _require_enabled()
        _live(preview)
        if (
            preview.job.status != "completed"
            or not preview.bundle
            or preview.outcome not in {"ready", "changed", "unchanged_content"}
        ):
            raise _error(
                "github_bundle_invalid", "This preview is not ready to stage.", 422
            )
        files = [
            {"path": item["path"], "content": item["content"]}
            for item in preview.bundle["files"]
        ]
        checks = run_checks(
            files, expected_name=skill.name if skill else preview.bundle["name"]
        )
        if (
            checks["summary"]["errors"]
            or manifest_digest(files) != preview.bundle["digest"]
        ):
            raise _error(
                "github_bundle_invalid", "The reviewed bundle is invalid.", 422
            )
        user = User.objects.get(pk=principal.user_id)
        if skill:
            source = _source(principal.scope, skill)
            if (
                source.revision != request.get("expected_source_revision")
                or source.revision != preview.source_revision
            ):
                raise _error(
                    "source_conflict",
                    "The tracked source changed. Check it again.",
                    revision=source.revision,
                )
            latest = skill.versions.get(number=skill.latest_version_number)
            if (
                request.get("expected_latest_version"),
                request.get("expected_latest_digest"),
            ) != (latest.number, latest.digest) or (
                preview.expected_latest_version,
                preview.expected_latest_digest,
            ) != (latest.number, latest.digest):
                raise _error(
                    "candidate_changed",
                    "The local latest version changed. Preview it again.",
                )
            if (
                type(request.get("revision")) is not int
                or request["revision"] != 0
                or HubSkillDraft.objects.filter(
                    skill=skill, user_id=principal.user_id
                ).exists()
            ):
                raise _error(
                    "draft_conflict",
                    "Open or discard your existing draft before staging this update.",
                )
            if preview.local_diverged and request.get("accept_replacement") is not True:
                raise _error(
                    "candidate_changed",
                    "Confirm replacement of the reviewed local changes.",
                )
            revision = 0
        else:
            skill = create_skill(
                organization=Organization.objects.get(
                    pk=principal.scope.organization_id
                ),
                workspace=Workspace.no_workspace_objects.get(
                    pk=principal.scope.workspace_id,
                    organization_id=principal.scope.organization_id,
                ),
                actor=Actor(user=user, api_key=None),
                name=preview.bundle["name"],
                description=checks["frontmatter"]["description"],
                tags=[],
            )
            revision = HubSkillDraft.objects.get(skill=skill, user=user).revision
        rights = _rights(principal, preview, request, skill)
        # Shared v1 model migration is mandatory; never silently drop provenance.
        if not any(field.name == "source" for field in HubSkillDraft._meta.fields):
            raise _error(
                "skills_unavailable", "Source-aware drafts are not enabled.", 503
            )
        draft = save_draft(
            skill=skill,
            user=user,
            files=files,
            note="",
            base_version=skill.latest_version_number or None,
            revision=revision,
        )
        draft.source = _provenance(preview)
        draft.save(update_fields=["source"])
        preview.consumed_skill = skill
        preview.consumed_draft_revision = draft.revision
        preview.rights_review = rights
        preview.save(
            update_fields=["consumed_skill", "consumed_draft_revision", "rights_review"]
        )
        return {
            "skill": skill,
            "draft": draft,
            "published": False,
            "replayed": False,
            "consumed": {"skill_id": str(skill.pk), "draft_revision": draft.revision},
        }


def adopt_draft_source_locked(
    *, skill: HubSkill, draft: HubSkillDraft, version: HubSkillVersion
) -> None:
    """Advance only the reviewed source binding, including a same-content publish."""
    if not connection.in_atomic_block:
        raise RuntimeError(
            "Source adoption requires the publication transaction and skill lock."
        )
    source = draft.source
    if source.get("provider") != "github":
        return
    if draft.skill_id != skill.pk or version.skill_id != skill.pk:
        raise _error("source_conflict", "The source does not belong to this skill.")
    preview = HubSkillGitHubPreview.objects.filter(
        pk=source.get("preview_id"),
        consumed_skill=skill,
        job__organization_id=skill.organization_id,
        job__workspace_id=skill.workspace_id,
    ).first()
    if not preview or preview.commit_sha != source.get("commit_sha"):
        raise _error("source_conflict", "The source review receipt is unavailable.")
    binding = HubSkillGitHubSource.objects.filter(skill=skill).first()
    if (
        binding
        and skill.versions.filter(source__preview_id=source["preview_id"])
        .exclude(pk=version.pk)
        .exists()
    ):
        # A restore/ordinary edit retains historical provenance, not a new adoption.
        return
    # Ordinary edits inherit the adopted basis. They must not rewind a later check/adoption.
    if (
        binding
        and binding.imported_sha == source["commit_sha"]
        and binding.upstream_digest == source["upstream_digest"]
    ):
        return
    if binding and source.get("source_revision") != binding.revision:
        raise _error(
            "source_conflict",
            "The adopted source changed after this draft was staged.",
            revision=binding.revision,
        )
    values = {
        key: source[key]
        for key in (
            "repo",
            "repository_id",
            "path",
            "ref",
            "ref_kind",
            "upstream_digest",
        )
    }
    values.update(
        imported_sha=source["commit_sha"],
        associated_version=version,
        revision=(binding.revision + 1) if binding else 1,
    )
    HubSkillGitHubSource.objects.update_or_create(
        skill=skill,
        organization_id=skill.organization_id,
        workspace_id=skill.workspace_id,
        defaults=values,
    )
