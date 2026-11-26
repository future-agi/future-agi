"""Reviewer-facing metadata for sources in the current environment and suite."""

import json
import re
from typing import Any

from simulate.models import HarnessSourceReview
from simulate.services.harness_versioning import drafts


def resolve_legacy_filenames(rows, scope):
    """Older previews stored parser aliases. Recover names from scoped upload records."""
    from model_hub.models.develop_dataset import KnowledgeBaseFile
    from simulate.models import HarnessDesignDocument

    legacy = [
        source
        for row in rows
        for source in row.preview.get("sources", [])
        if "sample_rows" not in source and source.get("origin")
    ]
    if not legacy:
        return
    origins = [source["origin"] for source in legacy]
    uploads = {
        str(pk): name
        for pk, name in HarnessDesignDocument.no_workspace_objects.filter(
            **scope,
            pk__in=[
                o["document_id"]
                for o in origins
                if o.get("document_id")
                and re.fullmatch(r"[0-9a-fA-F-]{32,36}", str(o["document_id"]))
            ],
            deleted=False,
        ).values_list("id", "name")
    }
    kb_files = {}
    kb_ids = [origin["kb_id"] for origin in origins if origin.get("kb_id")]
    if kb_ids and scope.get("workspace_id"):
        from django.db.models import Q

        from accounts.models.workspace import Workspace

        # require_source_scope has authorized the current workspace. Older KBs in
        # its default workspace store NULL, matching authorized_knowledge_base.
        workspace_filter = Q(workspace_id=scope["workspace_id"])
        if Workspace.objects.filter(
            pk=scope["workspace_id"],
            organization_id=scope["organization_id"],
            is_default=True,
            is_active=True,
            deleted=False,
        ).exists():
            workspace_filter |= Q(workspace_id__isnull=True)
        kb_files = {
            (str(kb), str(file_id)): name
            for kb, file_id, name in KnowledgeBaseFile.no_workspace_objects.filter(
                workspace_filter,
                organization_id=scope["organization_id"],
                pk__in=kb_ids,
                files__id__in=[o["file_id"] for o in origins if o.get("file_id")],
                deleted=False,
                files__deleted=False,
            ).values_list("id", "files__id", "files__name")
        }
    for source in legacy:
        origin = source["origin"]
        name = uploads.get(origin.get("document_id")) or kb_files.get(
            (origin.get("kb_id"), origin.get("file_id"))
        )
        if name:
            source["filename"] = name


def source_summary(environment, *, reviewer_scope=None) -> dict[str, Any]:
    head = environment.__class__.no_workspace_objects.select_related(
        "environment_version", "suite_version"
    ).get(id=environment.id)
    version = drafts.fresh_draft(head, "environment") or head.environment_version
    files = drafts.piece_files(head, "environment", version) if version else {}
    try:
        manifest = json.loads(files.get("sources/manifest.json", b"{}").decode())
    except (UnicodeDecodeError, ValueError):
        manifest = {}
    result = {
        "draft": bool(version and version.status == "draft"),
        "snapshot_digest": manifest.get("snapshot_digest"),
        "sources": manifest.get("sources") or [],
        "validation_status": manifest.get("validation_status"),
    }

    if reviewer_scope is None:
        return result

    from simulate.services.harness_source_generation import summary

    digests = manifest.get("source_snapshot_digests") or {}
    checkpoint = _accepted_checkpoint(head, reviewer_scope)
    checkpoint_digests = {}
    if checkpoint:
        checkpoint_sources = [
            source
            for source in checkpoint.receipt.get("sources", [])
            if source["source_id"] not in digests
        ]
        checkpoint_digests = {
            source["source_id"]: "sha256:" + checkpoint.accepted_artifact_digest
            for source in checkpoint_sources
        }
        result["sources"] = [*result["sources"], *checkpoint_sources]
        if not result["snapshot_digest"]:
            result.update(
                snapshot_digest=checkpoint.snapshot_digest, validation_status="accepted"
            )
    if not result["sources"]:
        return result
    sealed_digests = digests
    if result["draft"]:
        sealed = drafts.piece_files(head, "environment", head.environment_version)
        sealed_digests = json.loads(sealed.get("sources/manifest.json", b"{}")).get(
            "source_snapshot_digests", {}
        )
    reviews = (
        HarnessSourceReview.objects.filter(
            **reviewer_scope,
            owner=head,
            state=HarnessSourceReview.State.ACCEPTED,
            accepted_artifact_digest__in={
                v.removeprefix("sha256:") for v in digests.values()
            },
        )
        .select_related("scenario_generation")
        .order_by("-expires_at")
    )
    current = {**checkpoint_digests, **digests}
    sealed = {**checkpoint_digests, **sealed_digests}
    by_source = {item["source_id"]: item for item in result["sources"]}
    current_reviews = ([checkpoint] if checkpoint else []) + list(reviews)
    resolve_legacy_filenames(current_reviews, reviewer_scope)
    for review in current_reviews:
        for source in review.preview.get("sources", []):
            key = source["pin"]["source_id"]
            if (
                key not in by_source
                or current.get(key) != "sha256:" + review.accepted_artifact_digest
            ):
                continue
            origin = source.get("origin") or {}
            if origin.get("kind") == "knowledge_base":
                by_source[key]["origin"] = {
                    field: origin[field]
                    for field in ("kind", "kb_id", "file_id")
                    if field in origin
                }
            by_source[key].update(
                **{
                    field: source[field]
                    for field in (
                        "filename",
                        "updated_at",
                        "visibility",
                        "rejected_count",
                    )
                    if field in source
                },
                review_id=str(review.id),
                review_owner_id=str(review.owner_id),
                unsaved=sealed.get(key) != current[key],
            )
        generation = (
            None
            if review is checkpoint
            else getattr(review, "scenario_generation", None)
        )
        if generation and "scenario_generation" not in result:
            result.update(
                scenario_generation=summary(generation),
                generation_review_id=str(review.id),
            )
    _add_source_usage(head, by_source)
    return result


def _add_source_usage(head, by_source):
    suite = drafts.fresh_draft(head, "suite") or head.suite_version
    if suite:
        for source in by_source.values():
            source["used_by"] = []
        for scenario in (suite.documents or {}).get("scenarios", []):
            fixture = scenario.get("fixture")
            if not isinstance(fixture, dict):
                continue
            case = fixture.get("reviewed_source") or {}
            if not isinstance(case, dict):
                continue
            citations: dict[str, list[dict]] = {}
            for cite in case.get("citations") or []:
                if isinstance(cite, dict) and cite.get("source_id") in by_source:
                    citations.setdefault(cite["source_id"], []).append(
                        {
                            key: cite[key]
                            for key in ("source_id", "page", "row")
                            if key in cite
                        }
                    )
            for key, locations in citations.items():
                by_source[key]["used_by"].append(
                    {
                        "scenario_key": scenario.get("scenario_key"),
                        "name": scenario.get("name"),
                        "citations": locations,
                    }
                )


def _accepted_checkpoint(head, reviewer_scope):
    metadata = (head.payload or {}).get("metadata") or {}
    pin = metadata.get("build_source_pin") or metadata.get("design_source_pin") or {}
    if pin.get("review_id") and pin.get("owner_id"):
        return HarnessSourceReview.objects.filter(
            **reviewer_scope,
            pk=pin["review_id"],
            owner_id=pin["owner_id"],
            state=HarnessSourceReview.State.ACCEPTED,
            snapshot_digest=pin.get("digest"),
            receipt__snapshot_id=pin.get("id"),
            owner_kind__in=(
                HarnessSourceReview.Owner.DESIGN,
                HarnessSourceReview.Owner.BUILD_JOB,
            ),
        ).first()
    return None
