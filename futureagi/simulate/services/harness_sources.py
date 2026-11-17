from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import PurePath
from typing import Any

from django.conf import settings
from django.core import signing
from django.utils import timezone
from minio.error import S3Error

from model_hub.services.kb_snapshot import (
    MAX_SELECTION_BYTES,
    MAX_SELECTION_FILES,
    CapturedKnowledgeBaseSource,
    KnowledgeBaseSnapshotError,
    capture_knowledge_base_selection,
)
from model_hub.utils.kb_download import authorized_knowledge_base_file
from simulate.services.alk_source_bridge import execute_source_request
from simulate.services.harness_versioning import archives, drafts, versions
from simulate.services.hosted_harness import HostedHarnessError
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

IMPORT_REF_SALT = "simulate.environment-source-import.v1"
IMPORT_REF_MAX_AGE_SECONDS = 30 * 60
REQUEST_VERSION = "environment-sources.request.v1"
STAGING_PREFIX = "environment-source-imports"
SNAPSHOT_PREFIX = "environment-source-snapshots"


def source_capabilities(environment) -> dict[str, Any]:
    refs = drafts.draft_refs(environment)
    return {
        "upload": {
            "enabled": True,
            "formats": ["pdf", "txt", "csv", "json"],
            "max_files": MAX_SELECTION_FILES,
            "max_file_bytes": 25 * 1024 * 1024,
            "max_total_bytes": MAX_SELECTION_BYTES,
        },
        "knowledge_base": {
            "enabled": True,
            "formats": ["pdf", "docx", "rtf", "txt"],
            "max_files": MAX_SELECTION_FILES,
            "max_file_bytes": 25 * 1024 * 1024,
            "max_total_bytes": MAX_SELECTION_BYTES,
        },
        "environment_version_id": str(environment.environment_version_id),
        "draft": _ref_value(refs["environment"]),
        "participant_visibility_enabled": False,
        "participant_visibility_reason": (
            "The current runtime cannot prove per-participant source isolation."
        ),
    }


def source_summary(environment) -> dict[str, Any]:
    head = environment.__class__.no_workspace_objects.select_related(
        "environment_version"
    ).get(id=environment.id)
    version = drafts.fresh_draft(head, "environment") or head.environment_version
    files = drafts._piece_files(head, "environment", version)
    try:
        manifest = json.loads(files.get("sources/manifest.json", b"{}").decode())
    except (UnicodeDecodeError, ValueError):
        manifest = {}
    return {
        "draft": version.status == "draft",
        "snapshot_digest": manifest.get("snapshot_digest"),
        "sources": manifest.get("sources") or [],
        "validation_status": manifest.get("validation_status"),
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _ref_value(ref: drafts.DraftRef) -> dict[str, Any]:
    return {"id": ref.id, "revision": ref.revision}


def _ref_from(value: dict[str, Any]) -> drafts.DraftRef:
    return drafts.DraftRef(value.get("id"), int(value.get("revision", 0)))


def _scenario_validation(suite_files: dict[str, bytes]) -> dict[str, Any]:
    affected = [
        {
            "scenario_key": str(
                scenario.get("scenario_key") or scenario.get("name") or ""
            ),
            "name": str(scenario.get("name") or ""),
            "executed": False,
        }
        for scenario in versions.suite_documents(suite_files)
    ]
    return {"status": "structural_only", "affected_scenarios": affected}


def _put_private(key: str, body: bytes) -> None:
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=key,
        data=io.BytesIO(body),
        length=len(body),
        content_type="application/json",
    )


def _get_private(key: str) -> bytes:
    response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def resolve_evaluator_sources(run) -> dict[str, Any]:
    """Resolve private source inputs from the Environment version pinned to a run."""
    version = getattr(run, "environment_version", None)
    if (
        version is None
        or not getattr(run, "environment_id", None)
        or version.job_id != run.environment_id
        or version.organization_id != run.organization_id
    ):
        raise HostedHarnessError(
            "source_snapshot_unavailable",
            "The run does not have a valid pinned Environment source snapshot.",
            status_code=409,
        )
    files = archives.read_archive(archives.get_archive(version.archive_object_key))
    try:
        manifest = json.loads(files["sources/manifest.json"])
        aggregate_digest = manifest["snapshot_digest"]
        aggregate_key = (
            f"{SNAPSHOT_PREFIX}/{run.organization_id}/"
            f"{aggregate_digest.removeprefix('sha256:')}.json"
        )
        aggregate = json.loads(_get_private(aggregate_key))
        aggregate_payload = {
            "schema_version": aggregate["schema_version"],
            "private_snapshot_digests": aggregate["private_snapshot_digests"],
            "source_snapshot_digests": aggregate["source_snapshot_digests"],
        }
        actual_aggregate_digest = (
            "sha256:" + hashlib.sha256(_canonical(aggregate_payload)).hexdigest()
        )
        if actual_aggregate_digest != aggregate_digest:
            raise ValueError("aggregate digest mismatch")
        source_digests = aggregate_payload["source_snapshot_digests"]
        if set(source_digests) != {
            source["source_id"] for source in manifest.get("sources", [])
        }:
            raise ValueError("source map mismatch")
        resolved = []
        for digest in sorted(set(source_digests.values())):
            key = (
                f"{SNAPSHOT_PREFIX}/{run.organization_id}/"
                f"{digest.removeprefix('sha256:')}.json"
            )
            body = _get_private(key)
            if "sha256:" + hashlib.sha256(body).hexdigest() != digest:
                raise ValueError("leaf digest mismatch")
            leaf = json.loads(body)
            resolved.append(
                {
                    "source_ids": sorted(
                        source_id
                        for source_id, source_digest in source_digests.items()
                        if source_digest == digest
                    ),
                    "evaluator_inputs": leaf["compiled"]["evaluator_inputs"],
                }
            )
        return {
            "snapshot_digest": aggregate_digest,
            "sources": resolved,
        }
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        S3Error,
    ) as exc:
        raise HostedHarnessError(
            "source_snapshot_invalid",
            "The pinned Environment source snapshot could not be verified.",
            status_code=409,
        ) from exc


def _alk_result(response: dict) -> dict:
    if response.get("ok") is True and isinstance(response.get("result"), dict):
        return response["result"]
    error = response.get("error") or {}
    code = str(error.get("code") or "source_request_invalid")
    status_code = {
        "source_too_large": 413,
        "source_review_stale": 409,
        "source_snapshot_invalid": 409,
        "source_mapping_invalid": 422,
        "source_relationship_missing": 422,
        "source_relationship_invalid": 422,
        "source_audience_invalid": 422,
    }.get(code, 422)
    raise HostedHarnessError(
        code,
        str(error.get("message") or "The Environment source request is invalid"),
        status_code=status_code,
        details={"diagnostics": error.get("diagnostics") or []},
    )


def prepare_source_import(
    request,
    environment,
    selection: dict,
    *,
    expected_environment_version_id: str,
    expected_draft: dict,
) -> dict:
    current_ref = _require_import_basis(
        environment, expected_environment_version_id, expected_draft
    )
    try:
        captured = capture_knowledge_base_selection(
            request,
            kb_id=selection["kb_id"],
            file_ids=selection.get("file_ids"),
            whole_collection=bool(selection.get("whole_collection")),
        )
    except KnowledgeBaseSnapshotError as exc:
        raise HostedHarnessError(
            exc.code, str(exc), status_code=exc.status_code
        ) from exc
    return _prepare_captured(environment, captured, current_ref)


def prepare_upload_import(
    environment,
    uploaded,
    *,
    purpose: str | None,
    expected_environment_version_id: str,
    expected_draft: dict,
) -> dict:
    current_ref = _require_import_basis(
        environment, expected_environment_version_id, expected_draft
    )
    uploads = tuple(uploaded) if isinstance(uploaded, (list, tuple)) else (uploaded,)
    if len(uploads) > MAX_SELECTION_FILES:
        raise HostedHarnessError(
            "source_limit_exceeded",
            "Upload at most 100 files in one Environment import.",
            status_code=413,
        )
    defaults = {
        ".pdf": ("reference", "application/pdf"),
        ".txt": ("reference", "text/plain"),
        ".csv": ("seed_records", "text/csv"),
        ".json": ("seed_records", "application/json"),
    }
    valid_roles = {
        ".pdf": {"policy", "reference"},
        ".txt": {"reference"},
        ".csv": {"seed_records"},
        ".json": {"seed_records"},
    }
    captured = []
    total_bytes = 0
    for index, item in enumerate(uploads, start=1):
        original = item.read(25 * 1024 * 1024 + 1)
        if len(original) > 25 * 1024 * 1024:
            raise HostedHarnessError(
                "source_limit_exceeded",
                "An upload exceeds the 25 MiB Environment import limit.",
                status_code=413,
            )
        total_bytes += len(original)
        if total_bytes > MAX_SELECTION_BYTES:
            raise HostedHarnessError(
                "source_limit_exceeded",
                "Uploads exceed the 200 MiB Environment import limit.",
                status_code=413,
            )
        suffix = PurePath(item.name).suffix.lower()
        if suffix not in defaults:
            raise HostedHarnessError(
                "source_format_unsupported",
                "Upload PDF, TXT, CSV, or JSON files.",
                status_code=422,
            )
        suggested, media_type = defaults[suffix]
        role = purpose or suggested
        if role not in valid_roles[suffix]:
            raise HostedHarnessError(
                "source_format_unsupported",
                "The selected purpose is not supported for every file.",
                status_code=422,
            )
        digest = "sha256:" + hashlib.sha256(original).hexdigest()
        source_id = (
            "src_"
            + hashlib.sha256(f"upload:{item.name}:{digest}".encode()).hexdigest()[:24]
        )
        captured.append(
            CapturedKnowledgeBaseSource(
                source_id=source_id,
                filename=f"source-{index}{suffix}",
                media_type=media_type,
                role=role,
                content=original,
                original_content=original,
                origin={
                    "source_id": source_id,
                    "kind": "upload",
                    "upload_id": digest,
                    "original_name": item.name,
                    "original_sha256": digest,
                    "parsed_sha256": digest,
                    "upstream_revision": None,
                },
            )
        )
    return _prepare_captured(environment, tuple(captured), current_ref)


def _require_import_basis(
    environment, expected_environment_version_id: str, expected_draft: dict
) -> drafts.DraftRef:
    current_ref = drafts.draft_refs(environment)["environment"]
    if str(environment.environment_version_id) != str(
        expected_environment_version_id
    ) or _ref_value(current_ref) != {
        "id": str(expected_draft["id"]) if expected_draft["id"] else None,
        "revision": expected_draft["revision"],
    }:
        raise HostedHarnessError(
            "environment_draft_conflict",
            "The Environment changed. Start the source review again.",
            status_code=409,
        )
    return current_ref


def _prepare_captured(
    environment,
    captured: tuple[CapturedKnowledgeBaseSource, ...],
    current_ref: drafts.DraftRef,
) -> dict:
    fields = []
    for source in captured:
        if source.role != "seed_records":
            continue
        try:
            if source.filename.endswith(".csv"):
                headers = next(
                    csv.reader(io.StringIO(source.content.decode("utf-8-sig")))
                )
            else:
                rows = json.loads(source.content)
                headers = [key for row in rows if isinstance(row, dict) for key in row]
        except (StopIteration, UnicodeDecodeError, ValueError, TypeError):
            headers = []
        for header in headers:
            if isinstance(header, str) and header.strip() and header not in fields:
                fields.append(header)
    destination_fields = fields or ["id"]
    prepare_request = {
        "version": REQUEST_VERSION,
        "operation": "prepare",
        "sources": [
            {
                "source_id": source.source_id,
                "filename": source.filename,
                "media_type": source.media_type,
                "role": source.role,
                "content_base64": base64.b64encode(source.content).decode(),
            }
            for source in captured
        ],
        "destination_schema": {
            "fields": [
                {"name": name, "data_type": "string", "required": False}
                for name in destination_fields
            ]
        },
    }
    result = _alk_result(execute_source_request(prepare_request))
    latest = environment.__class__.no_workspace_objects.only(
        "environment_version_id"
    ).get(id=environment.id)
    refs = drafts.draft_refs(environment)
    if (
        latest.environment_version_id != environment.environment_version_id
        or _ref_value(refs["environment"]) != _ref_value(current_ref)
    ):
        raise HostedHarnessError(
            "environment_draft_conflict",
            "The Environment changed while sources were being prepared.",
            status_code=409,
        )
    staged = {
        "version": 1,
        "environment_id": str(environment.id),
        "environment_version_id": str(environment.environment_version_id),
        "draft": _ref_value(refs["environment"]),
        "draft_payload": result["draft"],
        "origins": [source.origin for source in captured],
        "originals": [
            {
                "source_id": source.source_id,
                "content_base64": base64.b64encode(source.original_content).decode(),
            }
            for source in captured
        ],
        "captured_at": timezone.now().isoformat(),
    }
    staged_body = _canonical(staged)
    digest = hashlib.sha256(staged_body).hexdigest()
    key = (
        f"{STAGING_PREFIX}/{environment.organization_id}/{environment.id}/{digest}.json"
    )
    _put_private(key, staged_body)
    workspace_id = getattr(environment, "workspace_id", None)
    import_ref = signing.dumps(
        {
            "v": 1,
            "organization_id": str(environment.organization_id),
            "workspace_id": str(workspace_id) if workspace_id else None,
            "environment_id": str(environment.id),
            "environment_version_id": str(environment.environment_version_id),
            "draft": staged["draft"],
            "key": key,
            "digest": digest,
        },
        key=settings.SECRET_KEY,
        salt=IMPORT_REF_SALT,
        compress=True,
    )
    return {
        "import_ref": import_ref,
        "draft_digest": result["draft"]["draft_digest"],
        "draft": result["draft"],
        "suggested_purposes": {source.source_id: source.role for source in captured},
        "capabilities": source_capabilities(environment),
    }


def _load_staged(import_ref: str, environment) -> dict:
    try:
        token = signing.loads(
            import_ref,
            key=settings.SECRET_KEY,
            salt=IMPORT_REF_SALT,
            max_age=IMPORT_REF_MAX_AGE_SECONDS,
        )
    except signing.SignatureExpired as exc:
        raise HostedHarnessError(
            "source_review_stale",
            "The source review expired. Prepare it again.",
            status_code=409,
        ) from exc
    except (signing.BadSignature, TypeError, ValueError) as exc:
        raise HostedHarnessError(
            "source_request_invalid",
            "The source import reference is invalid.",
            status_code=422,
        ) from exc
    expected = {
        "v": 1,
        "organization_id": str(environment.organization_id),
        "workspace_id": str(environment.workspace_id)
        if environment.workspace_id
        else None,
        "environment_id": str(environment.id),
        "environment_version_id": str(environment.environment_version_id),
    }
    if not isinstance(token, dict) or any(
        token.get(k) != v for k, v in expected.items() if k != "environment_version_id"
    ):
        raise HostedHarnessError(
            "source_access_denied",
            "The source import does not belong to this Environment.",
            status_code=404,
        )
    try:
        body = _get_private(str(token.get("key") or ""))
    except S3Error as exc:
        raise HostedHarnessError(
            "source_review_stale",
            "The source review expired. Prepare it again.",
            status_code=409,
        ) from exc
    if hashlib.sha256(body).hexdigest() != token.get("digest"):
        raise HostedHarnessError(
            "source_revision_changed",
            "The captured source revision changed.",
            status_code=409,
        )
    try:
        staged = json.loads(body)
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise HostedHarnessError(
            "source_request_invalid",
            "The source import reference is invalid.",
            status_code=422,
        ) from exc
    if staged.get("draft") != token.get("draft"):
        raise HostedHarnessError(
            "source_request_invalid",
            "The source import reference is invalid.",
            status_code=422,
        )
    return staged


def accept_source_import(
    request,
    environment,
    *,
    import_ref: str,
    review: dict,
    expected_environment_version_id: str,
    expected_draft: dict,
    expected_draft_digest: str,
) -> dict:
    staged = _load_staged(import_ref, environment)
    request_ref = {
        "id": str(expected_draft["id"]) if expected_draft["id"] else None,
        "revision": expected_draft["revision"],
    }
    if (
        str(expected_environment_version_id) != staged["environment_version_id"]
        or request_ref != staged["draft"]
        or expected_draft_digest != staged["draft_payload"]["draft_digest"]
    ):
        raise HostedHarnessError(
            "source_review_stale",
            "The source review no longer matches its prepared draft.",
            status_code=409,
        )
    for origin in staged["origins"]:
        if origin["kind"] == "knowledge_base":
            authorized_knowledge_base_file(request, origin["kb_id"], origin["file_id"])
    for decision in (
        *review.get("record_reviews", []),
        *review.get("policy_reviews", []),
        *review.get("reference_reviews", []),
    ):
        if (decision.get("audience") or {}).get("participant_ids"):
            raise HostedHarnessError(
                "participant_isolation_unavailable",
                "Participant-specific source visibility is unavailable in this runtime.",
                status_code=422,
            )
    head = environment.__class__.no_workspace_objects.select_related(
        "environment_version", "suite_version"
    ).get(id=environment.id)
    if str(head.environment_version_id) != staged["environment_version_id"]:
        raise HostedHarnessError(
            "source_review_stale",
            "The source review no longer matches the current Environment version.",
            status_code=409,
        )
    environment_version = (
        drafts.fresh_draft(head, "environment") or head.environment_version
    )
    suite_version = drafts.fresh_draft(head, "suite") or head.suite_version
    environment_files = drafts._piece_files(head, "environment", environment_version)
    suite_files = drafts._piece_files(head, "suite", suite_version)
    validation = _scenario_validation(suite_files)
    try:
        existing_manifest = json.loads(
            environment_files.get("sources/manifest.json", b"null").decode()
        )
        existing_projection = json.loads(
            environment_files.get("sources/tested-agent.json", b"[]").decode()
        )
    except (UnicodeDecodeError, ValueError):
        existing_manifest = None
        existing_projection = []
    if not isinstance(existing_projection, list):
        existing_projection = []
    result = _alk_result(
        execute_source_request(
            {
                "version": REQUEST_VERSION,
                "operation": "accept",
                "draft": staged["draft_payload"],
                "review": review,
                "reviewer_id": str(request.user.id),
                "existing_environments": existing_projection,
            }
        )
    )
    snapshot = result["snapshot"]
    snapshot_digest = snapshot["data"]["snapshot_digest"]
    pins = {
        item["pin"]["source_id"]: item["pin"] for item in snapshot["data"]["sources"]
    }
    if set(pins) != {origin["source_id"] for origin in staged["origins"]}:
        raise HostedHarnessError(
            "source_snapshot_invalid",
            "The accepted source snapshot is incomplete.",
            status_code=409,
        )
    origins = []
    for origin in staged["origins"]:
        if pins[origin["source_id"]]["source_sha256"] != origin["parsed_sha256"]:
            raise HostedHarnessError(
                "source_snapshot_invalid",
                "The accepted source revision does not match capture.",
                status_code=409,
            )
        origins.append(origin)
    private = _canonical(
        {
            "schema_version": 1,
            "snapshot_digest": snapshot_digest,
            "snapshot": snapshot,
            "origins": origins,
            "originals": staged["originals"],
            "compiled": result["compiled"],
        }
    )
    private_object_digest = "sha256:" + hashlib.sha256(private).hexdigest()
    private_key = f"{SNAPSHOT_PREFIX}/{environment.organization_id}/{private_object_digest.removeprefix('sha256:')}.json"
    _put_private(private_key, private)

    compiled = result["compiled"]
    agent_projection = compiled["agent_environments"]
    source_snapshot_digests = (
        dict(existing_manifest.get("source_snapshot_digests") or {})
        if isinstance(existing_manifest, dict)
        else {}
    )
    source_snapshot_digests.update(dict.fromkeys(pins, private_object_digest))
    private_snapshot_digests = sorted(set(source_snapshot_digests.values()))
    aggregate_payload = {
        "schema_version": 1,
        "private_snapshot_digests": private_snapshot_digests,
        "source_snapshot_digests": source_snapshot_digests,
    }
    aggregate_digest = (
        "sha256:" + hashlib.sha256(_canonical(aggregate_payload)).hexdigest()
    )
    aggregate_key = f"{SNAPSHOT_PREFIX}/{environment.organization_id}/{aggregate_digest.removeprefix('sha256:')}.json"
    _put_private(
        aggregate_key,
        _canonical(
            {
                **aggregate_payload,
                "snapshot_digest": aggregate_digest,
            }
        ),
    )
    safe_sources = {
        item["source_id"]: item
        for item in (
            existing_manifest.get("sources", [])
            if isinstance(existing_manifest, dict)
            else []
        )
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    safe_sources.update(
        {
            source_id: {"source_id": source_id, "purpose": pin["role"]}
            for source_id, pin in pins.items()
        }
    )
    manifest = {
        "schema_version": 1,
        "snapshot_digest": aggregate_digest,
        "private_snapshot_digests": private_snapshot_digests,
        "source_snapshot_digests": source_snapshot_digests,
        "source_count": len(safe_sources),
        "sources": [safe_sources[source_id] for source_id in sorted(safe_sources)],
        "tested_agent_projection_sha256": "sha256:"
        + hashlib.sha256(_canonical(agent_projection)).hexdigest(),
        "validation_status": "accepted",
    }
    environment_files["sources/manifest.json"] = _canonical(manifest)
    environment_files["sources/tested-agent.json"] = _canonical(agent_projection)
    if (
        isinstance(existing_manifest, dict)
        and private_object_digest
        in (existing_manifest.get("private_snapshot_digests") or [])
        and environment_version.status == "draft"
    ):
        return {
            "draft": {
                "id": str(environment_version.id),
                "revision": environment_version.revision,
            },
            "snapshot_digest": aggregate_digest,
            "sources": manifest["sources"],
            "diagnostics": snapshot["data"].get("diagnostics", []),
            "validation": validation,
        }
    body = archives.pack_archive(archives.compose(environment_files, suite_files))
    expected = {"environment": _ref_from(staged["draft"])}
    try:
        write = drafts.record_draft(
            head,
            body,
            origin="manual",
            user=request.user,
            expected=expected,
            pieces={"environment"},
        )
    except HostedHarnessError as exc:
        if exc.code == "draft_changed":
            raise HostedHarnessError(
                "environment_draft_conflict",
                "The Environment draft changed. Reapply the source review.",
                status_code=409,
            ) from exc
        raise
    draft = write.environment if write is not None else None
    return {
        "draft": (
            {"id": str(draft.id), "revision": draft.revision}
            if draft is not None
            else None
        ),
        "snapshot_digest": aggregate_digest,
        "sources": manifest["sources"],
        "diagnostics": snapshot["data"].get("diagnostics", []),
        "validation": validation,
    }
