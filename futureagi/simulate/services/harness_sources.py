from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
from datetime import timedelta
from pathlib import PurePath
from typing import Any
from uuid import uuid4

from minio.error import S3Error
from temporalio.common import WorkflowIDConflictPolicy

from model_hub.services.document_text import DocumentTextError, extract_document_text
from model_hub.services.kb_snapshot import (
    MAX_SELECTION_BYTES,
    MAX_SELECTION_FILES,
    CapturedKnowledgeBaseSource,
    KnowledgeBaseSnapshotError,
    capture_knowledge_base_selection,
)
from model_hub.utils.kb_download import authorized_knowledge_base_file
from simulate.services.alk_source_bridge import execute_source_request
from simulate.services.harness_storage import put_private, read_private
from simulate.services.harness_versioning import archives, drafts, versions
from simulate.services.hosted_harness import HostedHarnessError

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
    put_private(key, body, "application/json")


def _get_private(key: str) -> bytes:
    return read_private(key)


def _schedule_staging_expiry(key: str) -> None:
    from simulate.tasks.environment_source_imports import (
        expire_environment_source_import,
    )

    expire_environment_source_import.apply_async(
        args=[key],
        task_id=(
            "environment-source-import-expiry-"
            + hashlib.sha256(key.encode()).hexdigest()
        ),
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        start_delay=timedelta(seconds=IMPORT_REF_MAX_AGE_SECONDS + 60),
        dispatch_timeout_seconds=30,
    )


def resolve_evaluator_sources(run) -> dict[str, Any]:
    """Resolve private source inputs from the Environment version pinned to a run."""
    version = getattr(run, "environment_version", None)
    if (
        version is None
        or not getattr(run, "environment_id", None)
        or version.job_id != run.environment_id
        or version.organization_id != run.organization_id
        or getattr(version, "workspace_id", None) != getattr(run, "workspace_id", None)
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
        aggregate_key = _snapshot_key(run, aggregate_digest.removeprefix("sha256:"))
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
            key = _snapshot_key(run, digest.removeprefix("sha256:"))
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
        "The source processor could not use these inputs or decisions.",
        status_code=status_code,
        details={"diagnostics": _safe_diagnostics(error.get("diagnostics") or [])},
    )


def prepare_source_import(
    request,
    environment,
    selection: dict,
    *,
    expected_environment_version_id: str,
    expected_draft: dict,
    target_table: str | None = None,
) -> dict:
    current_ref = _require_import_basis(
        environment, expected_environment_version_id, expected_draft
    )
    captured = capture_kb(request, selection)
    return _prepare_captured(environment, captured, current_ref, target_table)


def capture_kb(request, selection: dict) -> tuple[CapturedKnowledgeBaseSource, ...]:
    try:
        return capture_knowledge_base_selection(
            request,
            kb_id=selection["kb_id"],
            file_ids=selection.get("file_ids"),
            whole_collection=bool(selection.get("whole_collection")),
        )
    except KnowledgeBaseSnapshotError as exc:
        raise HostedHarnessError(
            exc.code, str(exc), status_code=exc.status_code
        ) from exc


def prepare_upload_import(
    environment,
    uploaded,
    *,
    purpose: str | None,
    expected_environment_version_id: str,
    expected_draft: dict,
    target_table: str | None = None,
) -> dict:
    current_ref = _require_import_basis(
        environment, expected_environment_version_id, expected_draft
    )
    return _prepare_captured(
        environment, capture_uploads(uploaded, purpose), current_ref, target_table
    )


def capture_uploads(
    uploaded, purpose: str | None
) -> tuple[CapturedKnowledgeBaseSource, ...]:
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
        document_id = uuid4()
        source_id = f"src_{document_id.hex}"
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
                    "document_id": str(document_id),
                    "upload_id": digest,
                    "original_name": item.name,
                    "original_sha256": digest,
                    "parsed_sha256": digest,
                    "upstream_revision": None,
                },
            )
        )
    return tuple(captured)


def capture_documents(
    owner, ids, purposes=None
) -> tuple[CapturedKnowledgeBaseSource, ...]:
    from simulate.services.harness_designs import _documents

    staged = _documents(
        owner.organization,
        ids,
        workspace_id=owner.workspace_id,
        allow_duplicate_names=True,
    )
    purposes = purposes or {}
    captured = []
    for document in staged:
        try:
            original = raw = read_private(document.object_key)
            if (
                len(raw) != document.size
                or hashlib.sha256(raw).hexdigest() != document.sha256
            ):
                raise ValueError("Uploaded source digest mismatch")
            name = document.name
            suffix = PurePath(name).suffix.lower()
            if suffix in {".docx", ".rtf", ".md"}:
                raw = (
                    extract_document_text(raw, name).encode()
                    if suffix != ".md"
                    else raw
                )
                suffix = ".txt"
            elif suffix == ".tsv":
                input_rows = csv.reader(
                    io.StringIO(raw.decode("utf-8-sig")), delimiter="\t"
                )
                output = io.StringIO()
                csv.writer(output).writerows(input_rows)
                raw, suffix = output.getvalue().encode(), ".csv"
            elif suffix == ".jsonl":
                raw = _canonical(
                    [
                        json.loads(line)
                        for line in raw.decode("utf-8-sig").splitlines()
                        if line.strip()
                    ]
                )
                suffix = ".json"
            elif suffix == ".xlsx":
                raw, suffix = _spreadsheet_as_csv(raw), ".csv"
            normalized = io.BytesIO(raw)
            normalized.name = f"document{suffix}"
            parsed = capture_uploads(normalized, purposes.get(str(document.id)))[0]
            captured.append(
                CapturedKnowledgeBaseSource(
                    source_id=f"src_{document.id.hex}",
                    filename=parsed.filename,
                    media_type=parsed.media_type,
                    role=parsed.role,
                    content=parsed.content,
                    original_content=original,
                    origin={
                        **parsed.origin,
                        "source_id": f"src_{document.id.hex}",
                        "document_id": str(document.id),
                        "original_name": name,
                        "original_sha256": f"sha256:{document.sha256}",
                    },
                )
            )
        except (DocumentTextError, UnicodeDecodeError, ValueError, S3Error):
            raise HostedHarnessError(
                "source_content_unavailable",
                "An uploaded document could not be prepared.",
                status_code=422,
            ) from None
    return tuple(captured)


def _spreadsheet_as_csv(raw: bytes) -> bytes:
    from openpyxl import load_workbook

    workbook = load_workbook(
        io.BytesIO(raw), read_only=True, data_only=True, keep_links=False
    )
    try:
        if len(workbook.worksheets) != 1:
            raise ValueError("A record workbook must contain one sheet")
        output = io.StringIO()
        rows = workbook.active.iter_rows(values_only=True)
        output_rows = csv.writer(output)
        for row in rows:
            output_rows.writerow(row)
            if output.tell() > 25 * 1024 * 1024:
                raise ValueError("Workbook output limit")
        return output.getvalue().encode()
    finally:
        workbook.close()


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
    target_table: str | None,
) -> dict:
    from simulate.services.harness_source_reviews import prepare_environment_review

    return prepare_environment_review(environment, captured, current_ref, target_table)


def prepare_draft(
    captured: tuple[CapturedKnowledgeBaseSource, ...],
    destination_schema: dict | None,
    schema_repairs=(),
) -> dict:
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
        **(
            {"destination_schema": destination_schema}
            if destination_schema is not None
            else {}
        ),
        **({"schema_repairs": schema_repairs} if schema_repairs else {}),
    }
    return _alk_result(execute_source_request(prepare_request))["draft"]


def _safe_diagnostics(items: list[dict]) -> list[dict]:
    return [
        {
            "code": item["code"],
            "severity": item["severity"],
            "span": {
                key: span[key] for key in ("source_id", "page", "row") if key in span
            },
        }
        for item in items
        if isinstance(item, dict)
        and re.fullmatch(r"[a-z_]{1,100}", str(item.get("code") or ""))
        and item.get("severity") in {"warning", "blocking"}
        and isinstance(span := item.get("span") or {}, dict)
    ]


def safe_source_preview(draft: dict, origins: list[dict]) -> dict:
    identity = {
        item["source_id"]: {
            key: item[key]
            for key in ("kind", "document_id", "kb_id", "file_id")
            if key in item
        }
        for item in origins
    }
    display = []
    for item in draft.get("sources", []):
        pin = item["pin"]
        display.append(
            {
                "pin": {
                    key: pin[key]
                    for key in ("source_id", "source_sha256", "parser_revision", "role")
                    if key in pin
                },
                "filename": item.get("filename"),
                "origin": identity.get(pin["source_id"]),
                "parse_state": item.get("parse_state"),
                "row_count": len(item.get("rows", [])),
                "fields": [
                    {
                        key: field[key]
                        for key in (
                            "source_field",
                            "inferred_type",
                            "null_count",
                            "unique_count",
                            "suggested_target_field",
                            "suggested_stable_id",
                            "personal_data_suspected",
                        )
                        if key in field
                    }
                    for field in item.get("fields", [])
                ],
                "statements": [
                    {
                        "statement_id": statement["statement_id"],
                        "page": (statement.get("span") or {}).get("page"),
                    }
                    for statement in item.get("statements", [])
                ],
            }
        )
    proposals = draft.get("proposals") or {}
    return {
        "draft_digest": draft["draft_digest"],
        "sources": display,
        "destination_schema": draft.get("destination_schema"),
        "diagnostics": _safe_diagnostics(draft.get("diagnostics", [])),
        "proposals": {
            "field_mappings": proposals.get("field_mappings", []),
            "relationships": [
                {"relationship": item["relationship"], "confidence": item["confidence"]}
                for item in proposals.get("relationships", [])
            ],
            "policy_rules": [
                {
                    key: item[key]
                    for key in (
                        "rule_id",
                        "statement_id",
                        "kind",
                        "check_type",
                        "conflict_group",
                        "status",
                    )
                    if key in item
                }
                for item in proposals.get("policy_rules", [])
            ],
        },
    }


def accept_source_import(
    request,
    environment,
    *,
    import_ref: str,
    review: dict,
    expected_environment_version_id: str,
    expected_draft: dict,
    expected_draft_digest: str,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    from simulate.services.harness_source_reviews import accept_environment_review

    return accept_environment_review(
        request,
        environment,
        import_ref,
        version_id=expected_environment_version_id,
        draft=expected_draft,
        draft_digest=expected_draft_digest,
        decisions=review,
        revision=expected_revision,
        idempotency_key=idempotency_key,
    )


def _checked_review(request, staged: dict, review: dict) -> dict:
    for origin in staged["origins"]:
        if origin["kind"] == "knowledge_base":
            kb, _file = authorized_knowledge_base_file(
                request, origin["kb_id"], origin["file_id"]
            )
            if kb.workspace_id != getattr(request.workspace, "id", None):
                raise HostedHarnessError(
                    "source_access_denied", "Source not found.", status_code=404
                )
    checked = dict(review)
    proposals = {
        item["rule_id"]: item
        for item in staged["draft_payload"].get("proposals", {}).get("policy_rules", [])
    }
    for key in ("record_reviews", "policy_reviews", "reference_reviews"):
        decisions = review.get(key, [])
        if not isinstance(decisions, list) or len(decisions) > MAX_SELECTION_FILES:
            raise HostedHarnessError(
                "source_request_invalid",
                "The review contains invalid source decisions.",
                status_code=422,
            )
        for decision in decisions:
            if not isinstance(decision, dict) or (
                decision.get("audience") is not None
                and not isinstance(decision["audience"], dict)
            ):
                raise HostedHarnessError(
                    "source_request_invalid",
                    "The review contains invalid source decisions.",
                    status_code=422,
                )
            if (decision.get("audience") or {}).get("participant_ids"):
                raise HostedHarnessError(
                    "participant_isolation_unavailable",
                    "Participant-specific source visibility is unavailable in this runtime.",
                    status_code=422,
                )
        if key == "policy_reviews":
            checked[key] = [
                _policy_decision(decision, proposals) for decision in decisions
            ]
    checked["expected_draft_digest"] = staged["draft_payload"]["draft_digest"]
    checked.setdefault("record_reviews", [])
    checked.setdefault("policy_reviews", [])
    return checked


def _policy_decision(decision: dict, proposals: dict) -> dict:
    rules = decision.get("rules", [])
    if not isinstance(rules, list) or any(not isinstance(item, dict) for item in rules):
        raise HostedHarnessError(
            "source_request_invalid",
            "The review contains invalid policy decisions.",
            status_code=422,
        )
    checked = []
    for item in rules:
        proposal = item.get("proposal") or {}
        rule_id = item.get("rule_id") or (
            proposal.get("rule_id") if isinstance(proposal, dict) else None
        )
        if not isinstance(rule_id, str) or rule_id not in proposals:
            raise HostedHarnessError(
                "source_request_invalid",
                "The review contains an unknown policy rule.",
                status_code=422,
            )
        checked.append(
            {"proposal": proposals[rule_id], "decision": item.get("decision")}
        )
    return {**decision, "rules": checked}


def apply_environment_review(
    request, head, staged: dict, basis: dict, review: dict
) -> tuple[dict, str]:
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
    result, snapshot, pins, private_object_digest = _accept_captured(
        request, head, staged, review, existing_projection
    )
    environment = head
    compiled = result["compiled"]
    agent_projection = compiled["agent_environments"]
    private_object_digest = "sha256:" + private_object_digest
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
    aggregate_key = _snapshot_key(environment, aggregate_digest.removeprefix("sha256:"))
    _put_private(
        aggregate_key,
        _canonical({**aggregate_payload, "snapshot_digest": aggregate_digest}),
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
    body = archives.pack_archive(archives.compose(environment_files, suite_files))
    try:
        write = drafts.record_draft(
            head,
            body,
            origin="manual",
            user=request.user,
            expected={
                "environment": _ref_from(basis["draft"]),
                "suite": _ref_from(basis["suite_draft"]),
            },
            pieces={"environment"},
            checked={"suite"},
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
    if write is None:
        raise HostedHarnessError(
            "source_review_stale",
            "The Environment can no longer accept this source review.",
            status_code=409,
        )
    return {
        "draft": {"id": str(draft.id), "revision": draft.revision}
        if draft is not None
        else None,
        "snapshot_digest": aggregate_digest,
        "sources": manifest["sources"],
        "diagnostics": _safe_diagnostics(snapshot["data"].get("diagnostics", [])),
        "validation": validation,
    }, private_object_digest.removeprefix("sha256:")


def _accept_captured(request, owner, staged, review, existing_projection):
    result = _alk_result(
        execute_source_request(
            {
                "version": REQUEST_VERSION,
                "operation": "accept",
                "draft": staged["draft_payload"],
                "review": _checked_review(request, staged, review),
                "reviewer_id": str(request.user.id),
                "existing_environments": existing_projection,
            }
        )
    )
    snapshot = result["snapshot"]
    schema = staged["draft_payload"].get("destination_schema") or {}
    if schema.get("origin") == "simulation":
        from simulate.services.harness_source_reviews import schema_digest

        if snapshot["data"].get("simulation_schema_digest") != schema_digest(schema):
            raise HostedHarnessError(
                "source_snapshot_invalid",
                "The approved simulation schema does not match the reviewed fields.",
                status_code=409,
            )
    snapshot_digest = snapshot["data"]["snapshot_digest"]
    reviewed = snapshot["data"]["sources"]
    pins = {item["pin"]["source_id"]: item["pin"] for item in reviewed}
    if len(pins) != len(reviewed) or set(pins) != {
        origin["source_id"] for origin in staged["origins"]
    }:
        raise HostedHarnessError(
            "source_snapshot_invalid",
            "The accepted source snapshot is incomplete.",
            status_code=409,
        )
    origins = []
    bindings = []
    for origin in staged["origins"]:
        pin = pins[origin["source_id"]]
        if pin["source_sha256"] != origin["parsed_sha256"]:
            raise HostedHarnessError(
                "source_snapshot_invalid",
                "The accepted source revision does not match capture.",
                status_code=409,
            )
        origins.append(origin)
        identity = (
            {"document_id": origin["document_id"]}
            if origin["kind"] == "upload"
            else {"kb_id": origin["kb_id"], "file_id": origin["file_id"]}
        )
        bindings.append(
            {
                **{
                    key: pin[key]
                    for key in ("source_id", "source_sha256", "parser_revision", "role")
                },
                "origin": {
                    "kind": origin["kind"],
                    **identity,
                    "original_sha256": origin["original_sha256"],
                },
            }
        )
    private = _canonical(
        {
            "schema_version": 1,
            "snapshot_digest": snapshot_digest,
            "snapshot": snapshot,
            "origins": origins,
            "originals": staged["originals"],
            "compiled": result["compiled"],
            "launch_receipt": {
                "review_id": staged["review_id"],
                "snapshot_id": snapshot["data"]["snapshot_id"],
                "snapshot_digest": snapshot_digest,
                "bindings": sorted(bindings, key=lambda item: item["source_id"]),
            },
        }
    )
    digest = hashlib.sha256(private).hexdigest()
    _put_private(_snapshot_key(owner, digest), private)
    return result, snapshot, pins, digest


def _snapshot_key(owner, digest: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid snapshot digest.")
    workspace = getattr(owner, "workspace_id", None) or "unscoped"
    return f"{SNAPSHOT_PREFIX}/{owner.organization_id}/{workspace}/{digest}.json"


def accept_checkpoint_review(
    request, owner, staged: dict, review: dict
) -> tuple[dict, str]:
    _, snapshot, pins, digest = _accept_captured(request, owner, staged, review, [])
    return {
        "draft": None,
        "snapshot_id": snapshot["data"]["snapshot_id"],
        "snapshot_digest": snapshot["data"]["snapshot_digest"],
        "sources": [
            {"source_id": key, "purpose": pin["role"]}
            for key, pin in sorted(pins.items())
        ],
        "diagnostics": _safe_diagnostics(snapshot["data"].get("diagnostics", [])),
        "validation": {"status": "accepted"},
    }, digest


def read_accepted_snapshot(owner, digest: str) -> tuple[bytes, dict]:
    try:
        raw = _get_private(_snapshot_key(owner, digest))
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Accepted snapshot digest mismatch")
        artifact = json.loads(raw)
        return _canonical(artifact["snapshot"]), artifact["launch_receipt"]
    except (S3Error, ValueError, KeyError):
        raise HostedHarnessError(
            "source_snapshot_invalid",
            "The accepted source snapshot could not be verified.",
            status_code=409,
        ) from None
