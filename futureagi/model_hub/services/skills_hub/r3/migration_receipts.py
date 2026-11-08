"""Immutable source identities and byte-checked migration/exit receipts.

Uploaded source identities are client-reported, not proof of external ownership.
Publication binds the accepted source receipt once. Changed Hub content is a
relationship of derivation, never an assertion of the old external version.
"""

from __future__ import annotations

import copy
import re
from uuid import UUID

from django.db import transaction
from django.db.models import Q

from model_hub.models.skills_hub import HubSkill, HubSkillDraft, HubSkillVersion
from model_hub.models.skills_hub_r3 import HubSkillIntakePlan, HubSkillMigrationReceipt
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import (
    build_zip,
    file_sha256,
    manifest_digest,
    parse_upload,
)
from model_hub.services.skills_hub.types import FileInput
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.resolution_leases import (
    require_distribution_eligibility,
)

_SOURCE_FIELDS = {"origin", "project", "resource", "revision", "digest"}


def validate_source(source: dict, files: list[FileInput]) -> dict:
    if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS:
        raise SkillsHubError(
            "import_invalid",
            "Supply the complete immutable source identity.",
            status=400,
        )
    for key, limit in (
        ("origin", 255),
        ("project", 200),
        ("resource", 200),
        ("revision", 200),
    ):
        value = source[key]
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > limit
            or any(ord(char) < 32 for char in value)
        ):
            raise SkillsHubError(
                "import_invalid",
                "Source identity fields must be bounded nonempty text.",
                status=400,
            )
    if not isinstance(source["digest"], str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", source["digest"]
    ):
        raise SkillsHubError(
            "import_invalid", "Supply the exact source manifest digest.", status=400
        )
    if source["digest"] != manifest_digest(files):
        raise SkillsHubError(
            "candidate_changed",
            "Source content does not match its manifest digest.",
            status=409,
        )
    return copy.deepcopy(source)


def _source_rows(principal: SkillPrincipal, source: dict):
    return HubSkillMigrationReceipt.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        source_origin=source["origin"],
        source_project=source["project"],
        source_resource=source["resource"],
    )


def source_destination(
    *,
    principal: SkillPrincipal,
    source: dict | None,
) -> tuple[HubSkill | None, HubSkillMigrationReceipt | None]:
    if source is None:
        return None, None
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=False)
    rows = _source_rows(principal, source)
    exact = rows.filter(source_revision=source["revision"]).first()
    if exact and exact.source_digest != source["digest"]:
        raise SkillsHubError(
            "source_conflict",
            "An immutable source revision cannot change bytes.",
            status=409,
        )
    destination_ids = list(rows.values_list("destination_id", flat=True).distinct()[:2])
    if len(destination_ids) > 1:
        raise SkillsHubError(
            "source_conflict",
            "This source has ambiguous destination mappings.",
            status=409,
        )
    if not destination_ids:
        return None, None
    skill = get_resource(
        principal=principal, resource_id=destination_ids[0], kind="skill"
    )
    basis = rows.order_by("-created_at", "-id").first()
    return skill, basis


def migration_comparison(
    *,
    basis: HubSkillMigrationReceipt | None,
    source_files: list[FileInput],
    skill: HubSkill | None,
) -> dict:
    """Three manifest columns; no guessed text for an unavailable historical source."""
    base = basis.mapping["source_files"] if basis else []
    source = [
        {"path": row["path"], "sha256": file_sha256(row["content"])}
        for row in source_files
    ]
    head = (
        skill.versions.filter(number=skill.latest_version_number).first()
        if skill
        else None
    )
    hub = list(head.files.values("path", "sha256")) if head else []
    columns = [
        {row["path"]: row["sha256"] for row in files} for files in (base, source, hub)
    ]
    paths = sorted(set().union(*(set(column) for column in columns)))
    return {
        "basis_receipt_id": str(basis.pk) if basis else None,
        "basis_revision": basis.source_revision if basis else None,
        "source_changed": bool(
            basis and basis.source_digest != manifest_digest(source_files)
        ),
        "hub_diverged": bool(basis and head and head.digest != basis.source_digest),
        "files": [
            {
                "path": path,
                "base_sha256": columns[0].get(path),
                "source_sha256": columns[1].get(path),
                "hub_sha256": columns[2].get(path),
            }
            for path in paths
        ],
    }


def read_import_comparison(
    *,
    principal: SkillPrincipal,
    plan_id: UUID,
    item_key: str,
    expected_fingerprint: str,
) -> dict:
    """Inspect exact planned Source/Hub text and the retained Base when available."""
    from model_hub.services.skills_hub.compare import compare_file_sets
    from model_hub.services.skills_hub.r3.import_plans import checked_plan_item

    _, item = checked_plan_item(
        principal=principal,
        plan_id=plan_id,
        item_key=item_key,
        expected_fingerprint=expected_fingerprint,
        kind="skill_import",
    )
    hub_files: list[FileInput] = []
    base_files: list[FileInput] | None = None
    if item["destination_id"]:
        skill = get_resource(
            principal=principal, resource_id=UUID(item["destination_id"]), kind="skill"
        )
        head = skill.versions.filter(number=item["expected_head_version"]).first()
        if head:
            require_distribution_eligibility(head)
            hub_files = [
                {"path": row.path, "content": row.blob.content}
                for row in head.files.select_related("blob").order_by("path")
            ]
        basis = (
            HubSkillMigrationReceipt.objects.filter(
                pk=item["migration"]["basis_receipt_id"],
                destination=skill,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
                relation="identical",
                destination_version__isnull=False,
            )
            .select_related("destination_version")
            .first()
        )
        if basis:
            assert basis.destination_version is not None
            require_distribution_eligibility(basis.destination_version)
            base_files = [
                {"path": row.path, "content": row.blob.content}
                for row in basis.destination_version.files.select_related(
                    "blob"
                ).order_by("path")
            ]
    return {
        "source_hub": compare_file_sets(item["files"], hub_files),
        "base_source": (
            compare_file_sets(base_files, item["files"])
            if base_files is not None
            else None
        ),
        "base_hub": (
            compare_file_sets(base_files, hub_files) if base_files is not None else None
        ),
        "basis_payload_available": base_files is not None,
        "basis": item["migration"],
        "expected_head_version": item["expected_head_version"],
        "expected_head_digest": item["expected_head_digest"],
    }


def record_migration_locked(
    *,
    principal: SkillPrincipal,
    skill: HubSkill,
    source: dict,
    files: list[FileInput],
    source_manifest: list[dict],
    draft_revision: int,
) -> HubSkillMigrationReceipt:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Migration acceptance requires a locked transaction.")
    # The source manifest was built from the original retained upload, not from
    # caller claims at acceptance or the separately reviewed reconciled files.
    if source["digest"] != manifest_digest(source_manifest):
        raise SkillsHubError(
            "source_conflict", "The retained source manifest changed.", status=409
        )
    rows = _source_rows(principal, source)
    if rows.exclude(destination=skill).exists():
        raise SkillsHubError(
            "source_conflict",
            "This source already maps to another identity.",
            status=409,
        )
    existing = rows.filter(
        source_revision=source["revision"], destination=skill
    ).first()
    if existing:
        if existing.source_digest != source["digest"]:
            raise SkillsHubError(
                "source_conflict",
                "An immutable source revision cannot change bytes.",
                status=409,
            )
        # Re-importing a revision cannot silently create a second authored copy.
        raise SkillsHubError(
            "source_conflict",
            "This source revision is already accepted; use its receipt.",
            status=409,
            detail={"receipt_id": str(existing.pk)},
        )
    return HubSkillMigrationReceipt.objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        source_origin=source["origin"],
        source_project=source["project"],
        source_resource=source["resource"],
        source_revision=source["revision"],
        source_digest=source["digest"],
        destination=skill,
        destination_digest=manifest_digest(files),
        relation=(
            "identical" if source["digest"] == manifest_digest(files) else "derived"
        ),
        mapping={
            "schema": 1,
            "trust": "client_reported",
            "rights_confirmed": True,
            "draft_revision": draft_revision,
            "source_files": copy.deepcopy(source_manifest),
        },
    )


def bind_migration_publication_locked(
    *, skill: HubSkill, draft: HubSkillDraft, version: HubSkillVersion
) -> None:
    """Publication hook, before tombstoning, in the existing publication transaction."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Migration publication requires an atomic caller.")
    receipt_id = draft.source.get("migration_receipt_id")
    if receipt_id is None:
        return
    receipt = (
        HubSkillMigrationReceipt.objects.select_for_update()
        .filter(
            pk=receipt_id,
            destination=skill,
            organization_id=skill.organization_id,
            workspace_id=skill.workspace_id,
        )
        .first()
    )
    if receipt is None or version.skill_id != skill.pk:
        raise SkillsHubError(
            "source_conflict", "The accepted source receipt is unavailable.", status=409
        )
    # Subsequent edited releases retain derivation in version.source; never rewrite
    # the original source-to-publication mapping.
    if receipt.destination_version_id is not None:
        return
    receipt.destination_version = version
    receipt.destination_digest = version.digest
    receipt.relation = (
        "identical" if version.digest == receipt.source_digest else "derived"
    )
    receipt.save(
        update_fields=["destination_version", "destination_digest", "relation"]
    )


def lookup_migration(
    *,
    principal: SkillPrincipal,
    source_origin: str,
    source_project: str,
    source_resource: str,
    source_revision: str,
    source_digest: str,
) -> dict | None:
    require_scope_access(principal=principal, write=False)
    row = (
        _source_rows(
            principal,
            {
                "origin": source_origin,
                "project": source_project,
                "resource": source_resource,
            },
        )
        .filter(
            source_revision=source_revision,
            source_digest=source_digest,
            destination_version__isnull=False,
            destination__deleted=False,
            destination__kind="skill",
        )
        .select_related("destination_version", "destination")
        .first()
    )
    if row is None:
        return None
    require_distribution_eligibility(row.destination_version)
    return {
        "receipt_id": str(row.pk),
        "resource_id": str(row.destination_id),
        "version_id": str(row.destination_version_id),
        "digest": row.destination_digest,
        "relation": row.relation,
        "source_digest": row.source_digest,
        "trust": "client_reported_byte_checked",
    }


def export_migrated_version(
    *, principal: SkillPrincipal, resource_id: UUID, number: int
) -> dict:
    """Lossless supported-text export; does not uninstall files or revoke credentials."""
    skill = get_resource(principal=principal, resource_id=resource_id, kind="skill")
    version = skill.versions.filter(number=number).first()
    if version is None:
        raise SkillsHubError("version_not_found", "Version not found.", status=404)
    require_distribution_eligibility(version)
    files = [
        {"path": row.path, "content": row.blob.content}
        for row in version.files.select_related("blob").order_by("path")
    ]
    receipts = list(
        HubSkillMigrationReceipt.objects.filter(
            Q(destination_version=version)
            | Q(pk=version.source.get("migration_receipt_id")),
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            destination=skill,
        ).values(
            "id",
            "source_origin",
            "source_project",
            "source_resource",
            "source_revision",
            "source_digest",
            "relation",
        )[
            :50
        ]
    )
    return {
        "archive": build_zip(
            [
                {
                    "name": skill.name,
                    "files": files,
                    "version": version.number,
                    "version_id": str(version.pk),
                    "digest": version.digest,
                    "label": None,
                }
            ],
            origin="futureagi",
        ),
        "mapping_receipts": [
            {
                **row,
                "id": str(row["id"]),
                "destination_version_id": str(version.pk),
                "destination_digest": version.digest,
                "relation": (
                    "identical" if row["source_digest"] == version.digest else "derived"
                ),
            }
            for row in receipts
        ],
        "resource_id": str(skill.pk),
        "version_id": str(version.pk),
        "digest": version.digest,
        "exit": {
            "source_modified": False,
            "credentials_revoked": False,
            "local_removal": "reviewed_client_plan_required",
        },
    }


def preview_langfuse_export(
    *, principal: SkillPrincipal, export: dict
) -> HubSkillIntakePlan:
    """Bounded exchange schema, not an arbitrary Langfuse API or prompt converter.

    Callers provide format=langfuse-skills-v1, an origin/project and skills containing
    resource/revision/digest plus exact path/content files. No remote fetching.
    Native export-format conformance must be qualified independently.
    """
    from model_hub.services.skills_hub.r3.import_plans import preview_import

    if (
        not isinstance(export, dict)
        or set(export) != {"format", "origin", "project", "skills"}
        or export["format"] != "langfuse-skills-v1"
    ):
        raise SkillsHubError(
            "import_invalid",
            "Use the versioned langfuse-skills-v1 exchange format.",
            status=400,
        )
    if not isinstance(export["skills"], list) or not 1 <= len(export["skills"]) <= 50:
        raise SkillsHubError(
            "payload_too_large", "Supply 1–50 skill packages.", status=413
        )
    uploads: list[tuple[str, bytes]] = []
    sources: list[dict] = []
    total = 0
    for index, item in enumerate(export["skills"]):
        if not isinstance(item, dict) or set(item) != {
            "resource",
            "revision",
            "digest",
            "files",
        }:
            raise SkillsHubError(
                "import_invalid", "Unsupported export member.", status=400
            )
        from model_hub.services.skills_hub.drafts import _validate_payload

        _validate_payload(item["files"], "")
        source = validate_source(
            {
                "origin": export["origin"],
                "project": export["project"],
                **{key: item[key] for key in ("resource", "revision", "digest")},
            },
            item["files"],
        )
        if any(old["resource"] == source["resource"] for old in sources):
            raise SkillsHubError(
                "source_conflict",
                "A source resource occurs twice in the export.",
                status=409,
            )
        sources.append(source)
        # A ZIP per package preserves the upload's aggregate 1000-entry and 20 MiB
        # ceilings instead of treating a 15-package library as 200 loose files.
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for file in item["files"]:
                raw = file["content"].encode("utf-8")
                total += len(raw)
                if total > 20 * 1024 * 1024:
                    raise SkillsHubError(
                        "payload_too_large",
                        "Keep the export within 20 MiB.",
                        status=413,
                    )
                archive.writestr(file["path"], raw)
        uploads.append((f"package-{index}.zip", buffer.getvalue()))
    parsed = parse_upload(uploads)
    identities = {}
    for bundle in parsed["bundles"]:
        index = int(bundle["filename"].removeprefix("package-").removesuffix(".zip"))
        identities[bundle["key"]] = sources[index]
    return preview_import(
        principal=principal, uploads=uploads, source_identities=identities
    )
