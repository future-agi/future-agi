from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from minio.error import S3Error
from rest_framework.exceptions import PermissionDenied

from model_hub.services.kb_snapshot import CapturedKnowledgeBaseSource
from model_hub.utils.kb_download import authorized_knowledge_base
from simulate.models import (
    HarnessSourceReview,
    HarnessSourceScenarioGeneration,
    HostedHarnessJob,
)
from simulate.services import harness_sources as sources
from simulate.services.harness_provider import request_organization, request_workspace
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning import read as version_reads
from simulate.services.hosted_harness import HostedHarnessError
from tfc.constants.roles import RolePermissions

Owner = HarnessSourceReview.Owner
State = HarnessSourceReview.State
_REVIEWABLE = {State.READY, State.NEEDS_REPAIR}


def _build_selection(owner, kind):
    key = {
        Owner.DESIGN: "design_source_selection",
        Owner.BUILD_JOB: "build_source_selection",
    }.get(kind)
    return (owner.payload.get("metadata") or {}).get(key) if key else None


def _build_basis(owner, kind, target_table, captured):
    if kind == Owner.DESIGN:
        from simulate.services.harness_designs import design_source_basis

        return design_source_basis(owner, target_table, captured)
    if target_table:
        raise _conflict("A new connected build infers its record schema.")
    return {"revision": 1, "schema_digest": None}, None


def schema_digest(schema: dict) -> str:
    body = json.dumps(
        schema,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _conflict(message: str = "The source review changed. Reload it and try again."):
    return HostedHarnessError("source_review_stale", message, status_code=409)


def _not_found():
    return HostedHarnessError(
        "source_review_not_found", "Source review not found.", status_code=404
    )


def require_source_scope(request, *, write: bool) -> dict:
    organization = request_organization(request)
    workspace = request_workspace(request)
    if organization is None or (
        workspace is not None
        and (
            workspace.organization_id != organization.id
            or not workspace.is_active
            or workspace.deleted
        )
    ):
        raise _not_found()
    if workspace is None:
        roles = (
            RolePermissions.WRITE_ACCESS_ROLES
            if write
            else RolePermissions.READ_ACCESS_ROLES
        )
        permitted = request.user.get_organization_role(organization) in roles
    else:
        permitted = (
            request.user.can_write_to_workspace(workspace)
            if write
            else request.user.can_read_from_workspace(workspace)
        )
    if not permitted:
        raise PermissionDenied("Access denied to this source review.")
    return {
        "organization_id": organization.id,
        "workspace_id": getattr(workspace, "id", None),
    }


def _owner(query: dict, owner_id) -> HostedHarnessJob:
    row = HostedHarnessJob.no_workspace_objects.filter(
        **query, pk=owner_id, deleted=False, environment_id=None
    ).first()
    if row is None:
        raise _not_found()
    return row


def _environment_basis(
    owner: HostedHarnessJob,
    expected_draft: drafts.DraftRef,
    target_table=None,
    *,
    records=True,
) -> tuple[dict, dict | None]:
    refs = drafts.draft_refs(owner)
    if refs["environment"] != expected_draft:
        raise _conflict(
            "The Environment draft changed. Prepare the source review again."
        )
    schema, tables = (
        _environment_schema(owner, target_table)
        if records
        else ({"fields": [], "origin": "simulation", "repair_choices": []}, [])
    )
    return {
        "environment_version_id": str(owner.environment_version_id),
        "draft": sources._ref_value(expected_draft),
        "suite_version_id": str(owner.suite_version_id),
        "suite_draft": sources._ref_value(refs["suite"]),
        "target_table": (target_table or (tables[0] if len(tables) == 1 else None))
        if records
        else None,
        **({"record_mapping_required": False} if not records else {}),
        "schema_digest": schema_digest(schema) if schema else None,
        **({"destination_tables": tables} if schema is None else {}),
    }, schema


def _environment_schema(
    owner: HostedHarnessJob, target_table
) -> tuple[dict | None, list[str]]:
    version = drafts.fresh_draft(owner, "environment") or owner.environment_version
    schema = ((version.documents or {}).get("contract") or {}).get("data_schema") or {}
    columns = (
        [
            (str(table), str(name), str(sql_type))
            for table, fields in schema.items()
            if isinstance(fields, dict)
            for name, sql_type in fields.items()
        ]
        if isinstance(schema, dict)
        else []
    )
    if not columns:
        columns = _sql_columns(version.archive_object_key)
    tables = sorted({table for table, _name, _kind in columns})
    if target_table is not None and target_table not in tables:
        raise _conflict(
            "The selected destination table is unavailable in this Environment version."
        )
    if target_table is None and len(tables) > 1:
        return None, tables
    fields = [
        {"name": name, "data_type": _data_type(sql_type), "required": False}
        for table, name, sql_type in sorted(set(columns))
        if target_table is None or table == target_table
    ]
    if not fields:
        raise _conflict(
            "The Environment version has no typed source destination schema."
        )
    return {"fields": fields}, tables


def _sql_columns(archive_key: str) -> list[tuple[str, str, str]]:
    from sqlglot import exp, parse
    from sqlglot.errors import ParseError

    files = archives.read_members(
        archives.get_archive(archive_key),
        {"schema.sql"},
        max_size=256 * 1024,
    )
    try:
        statements = parse(files.get("schema.sql", b"").decode(), dialect="postgres")
    except (UnicodeDecodeError, ParseError):
        return []
    return [
        (statement.this.this.name, column.name, column.kind.sql())
        for statement in statements
        if isinstance(statement, exp.Create)
        and statement.kind == "TABLE"
        and isinstance(statement.this, exp.Schema)
        for column in statement.this.expressions
        if isinstance(column, exp.ColumnDef) and column.kind is not None
    ]


def _data_type(sql_type: str) -> str:
    words = sql_type.upper().split("(")[0].split()
    name = words[0] if words else ""
    if name in {"BOOLEAN", "BOOL"}:
        return "boolean"
    if name in {"SMALLINT", "INT", "INTEGER", "BIGINT", "SERIAL", "BIGSERIAL"}:
        return "integer"
    if name in {"DECIMAL", "NUMERIC", "REAL", "DOUBLE", "FLOAT"}:
        return "number"
    if name == "DATE":
        return "date"
    if name in {"TIMESTAMP", "TIMESTAMPTZ", "DATETIME"}:
        return "datetime"
    return "string"


def _owner_basis(
    owner: HostedHarnessJob, kind: str, scope: dict, *, records=True
) -> tuple[dict, dict | None]:
    metadata = (owner.payload or {}).get("metadata") or {}
    if kind == Owner.ENVIRONMENT:
        version_reads.require_versioning()
        if (
            metadata.get("design")
            or metadata.get("adaptation")
            or not owner.environment_version_id
        ):
            raise _not_found()
        current = sources._require_import_basis(
            owner, scope["environment_version_id"], scope["expected_draft"]
        )
        return _environment_basis(
            owner, current, scope.get("target_table"), records=records
        )
    if bool(metadata.get("design")) != (kind == Owner.DESIGN) or metadata.get(
        "adaptation"
    ):
        raise _not_found()
    raw = metadata.get("source_review_basis")
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("destination_schema"), dict)
        or not raw["destination_schema"].get("fields")
        or not isinstance(raw.get("revision"), int)
    ):
        raise _conflict("The owner has not published a source review schema.")
    destination_schema = raw["destination_schema"]
    digest = schema_digest(destination_schema)
    if (raw.get("revision"), raw.get("schema_digest")) != (
        scope.get("revision", raw["revision"]),
        digest,
    ) or (scope.get("schema_digest", digest) != digest):
        raise _conflict("The owner published a different source review schema.")
    return {"revision": raw["revision"], "schema_digest": digest}, destination_schema


def _required_basis(row: HarnessSourceReview, owner: HostedHarnessJob) -> None:
    metadata = (owner.payload or {}).get("metadata") or {}
    if _build_selection(owner, row.owner_kind):
        if (
            row.state != State.READING
            and str(row.id) != metadata.get("source_review_id")
        ) or owner.current_stage != "awaiting_source_review":
            raise _conflict("The design is no longer waiting for this review.")
        return
    scope = dict(row.basis)
    if row.owner_kind == Owner.ENVIRONMENT:
        scope["expected_draft"] = scope["draft"]
    current, _schema = _owner_basis(
        owner,
        row.owner_kind,
        scope,
        records=row.basis.get("record_mapping_required", True),
    )
    if current != row.basis:
        raise _conflict()


def _staging_key(row: HarnessSourceReview) -> str:
    return f"{sources.STAGING_PREFIX}/{row.organization_id}/{row.owner_id}/{row.staged_digest}.json"


def _check_batch(captured) -> None:
    if (
        len(captured) > sources.MAX_SELECTION_FILES
        or sum(len(item.original_content) for item in captured)
        > sources.MAX_SELECTION_BYTES
    ):
        raise HostedHarnessError(
            "source_limit_exceeded",
            "Choose at most 100 files up to 200 MiB in total.",
            status_code=413,
        )


def _store_staging(row, staged):
    body = sources._canonical(staged)
    row.staged_digest = hashlib.sha256(body).hexdigest()
    key = _staging_key(row)
    try:
        sources._schedule_staging_expiry(key)
        sources._put_private(key, body)
    except Exception:
        raise HostedHarnessError(
            "source_processor_unavailable",
            "Source storage is temporarily unavailable.",
            status_code=503,
            retryable=True,
        ) from None


def _retained_inputs(captured):
    return [
        {
            "source_id": item.source_id,
            "filename": item.filename,
            "role": item.role,
            "media_type": item.media_type,
            "origin": item.origin,
            "content": base64.b64encode(item.content).decode(),
            **(
                {"original": base64.b64encode(item.original_content).decode()}
                if item.content != item.original_content
                else {}
            ),
        }
        for item in captured
    ]


def _stage(
    row: HarnessSourceReview,
    capture,
    schema: dict | None,
    schema_repairs=(),
    *,
    repair_request=None,
) -> HarnessSourceReview:
    reading = HarnessSourceReview.objects.filter(
        pk=row.id, state=State.READING, revision=row.revision
    )
    try:
        captured = capture()
        _check_batch(captured)
        if not captured:
            raise HostedHarnessError(
                "source_selection_empty",
                "Choose files or a Knowledge Base.",
                status_code=422,
            )
        if len({item.source_id for item in captured}) != len(captured):
            raise HostedHarnessError(
                "source_duplicate", "An input is selected twice.", status_code=422
            )
        if schema is None and "destination_tables" in row.basis:
            _store_staging(
                row,
                {
                    "review_id": str(row.id),
                    "retained_inputs": _retained_inputs(captured),
                },
            )
            if not reading.filter(expires_at__gt=timezone.now()).update(
                state=State.NEEDS_REPAIR,
                revision=row.revision + 1,
                staged_digest=row.staged_digest,
                preview={
                    "destination_tables": row.basis["destination_tables"],
                    "diagnostics": [
                        {
                            "code": "source_schema_ambiguous",
                            "severity": "blocking",
                            "span": {},
                        }
                    ],
                },
            ):
                raise _conflict()
            row.refresh_from_db()
            return row
        draft = (
            sources.prepare_draft(captured, schema, schema_repairs)
            if schema_repairs
            else sources.prepare_draft(captured, schema)
        )
        owner = _owner(
            {"organization_id": row.organization_id, "workspace_id": row.workspace_id},
            row.owner_id,
        )
        _required_basis(row, owner)
        basis = (
            row.basis
            if schema is not None
            else {
                **row.basis,
                "schema_digest": schema_digest(draft["destination_schema"]),
            }
        )
        staged = {
            "draft_payload": draft,
            "origins": [item.origin for item in captured],
            "originals": [
                {
                    "source_id": item.source_id,
                    "content_base64": base64.b64encode(item.original_content).decode(),
                }
                for item in captured
            ],
            "review_id": str(row.id),
        }
        blocking = any(
            item.get("severity") == "blocking" for item in draft.get("diagnostics", [])
        )
        if blocking:
            staged["retained_inputs"] = _retained_inputs(captured)
        if repair_request is not None:
            staged["repair_request"] = repair_request
        _store_staging(row, staged)
        updated = reading.filter(expires_at__gt=timezone.now()).update(
            basis=basis,
            state=State.NEEDS_REPAIR if blocking else State.READY,
            revision=row.revision + 1,
            staged_digest=row.staged_digest,
            draft_digest=draft["draft_digest"],
            preview=sources.safe_source_preview(draft, staged["origins"]),
        )
        row.refresh_from_db()
        if not updated:
            raise _conflict()
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, HostedHarnessError)
            else "source_processor_unavailable"
        )
        reading.update(
            state=State.NEEDS_REPAIR,
            revision=row.revision + 1,
            preview={
                "diagnostics": [{"code": code, "severity": "blocking", "span": {}}]
            },
        )
        if not isinstance(exc, (HostedHarnessError, PermissionDenied)):
            raise HostedHarnessError(
                "source_processor_unavailable",
                "Source processing is temporarily unavailable.",
                status_code=503,
                retryable=True,
            ) from exc
        raise
    return row


def _stage_review(
    owner,
    kind,
    basis,
    schema,
    capture,
    idempotency_key=None,
    request_digest="",
    schema_repairs=(),
):
    values = {
        "organization_id": owner.organization_id,
        "workspace_id": owner.workspace_id,
        "basis": basis,
        "expires_at": timezone.now()
        + timedelta(seconds=sources.IMPORT_REF_MAX_AGE_SECONDS),
        "request_digest": request_digest,
    }
    if idempotency_key:
        row, created = HarnessSourceReview.objects.get_or_create(
            owner=owner,
            owner_kind=kind,
            idempotency_key=idempotency_key,
            defaults=values,
        )
        if not created:
            recorded = (
                {**row.basis, "schema_digest": None}
                if basis.get("schema_digest") is None
                else row.basis
            )
            if row.request_digest != request_digest or recorded != basis:
                raise _conflict(
                    "The idempotency key already names a different source selection."
                )
            row = _expire(row)
            if not (
                row.state == State.NEEDS_REPAIR
                and not row.staged_digest
                and not row.draft_digest
            ):
                return row
            claimed = HarnessSourceReview.objects.filter(
                pk=row.pk,
                state=State.NEEDS_REPAIR,
                revision=row.revision,
                expires_at__gt=timezone.now(),
            ).update(state=State.READING, revision=row.revision + 1, preview={})
            row.refresh_from_db()
            if not claimed:
                return _expire(row)
    else:
        row = HarnessSourceReview.objects.create(owner=owner, owner_kind=kind, **values)
    return _stage(row, capture, schema, schema_repairs)


def prepare_environment_review(
    owner, captured, expected_draft, target_table=None
) -> dict:
    basis, schema = _environment_basis(
        owner,
        expected_draft,
        target_table,
        records=any(item.role == "seed_records" for item in captured),
    )
    row = _stage_review(owner, Owner.ENVIRONMENT, basis, schema, lambda: captured)
    return {
        "import_ref": str(row.id),
        "revision": row.revision,
        "state": row.state,
        "draft_digest": row.draft_digest,
        "draft": row.preview,
        "suggested_purposes": {item.source_id: item.role for item in captured},
        "capabilities": sources.source_capabilities(owner),
    }


def create_review(
    request,
    *,
    scope,
    document_ids,
    knowledge_base,
    idempotency_key,
    document_purposes=None,
    schema_repairs=(),
) -> dict:
    owner = _owner(require_source_scope(request, write=True), scope["id"])
    kind = scope["kind"]
    if not document_ids and not knowledge_base:
        raise HostedHarnessError(
            "source_selection_empty",
            "Choose files or a Knowledge Base.",
            status_code=422,
        )
    web_ids = [str(item) for item in document_ids]
    selected = json.dumps(knowledge_base, default=str)
    digest = hashlib.sha256(
        sources._canonical(
            {
                "documents": web_ids,
                "document_purposes": document_purposes or {},
                "knowledge_base": json.loads(selected),
                **({"schema_repairs": schema_repairs} if schema_repairs else {}),
                **(
                    {"target_table": scope["target_table"]}
                    if "target_table" in scope
                    else {}
                ),
            }
        )
    ).hexdigest()

    def capture():
        captured = list(
            sources.capture_documents(owner, document_ids, document_purposes)
        )
        for selection in knowledge_base:
            captured.extend(sources.capture_kb(request, selection))
            _check_batch(captured)
        return tuple(captured)

    if _build_selection(owner, kind):
        from simulate.services.harness_designs import (
            AWAITING_REVIEW,
        )

        with transaction.atomic():
            owner = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                pk=owner.pk
            )
            metadata = owner.payload["metadata"]
            selection = _build_selection(owner, kind)
            if owner.current_stage != AWAITING_REVIEW or (
                web_ids,
                json.loads(selected),
                document_purposes or {},
            ) != (
                selection["document_ids"],
                selection["knowledge_base"],
                selection["document_purposes"],
            ):
                raise _conflict("This review must use the design's selected sources.")
            if "revision" in scope:
                previous = HarnessSourceReview.objects.filter(
                    owner=owner, pk=metadata.get("source_review_id")
                ).first()
                if not previous or (scope["revision"], scope["schema_digest"]) != (
                    previous.basis["revision"],
                    previous.basis["schema_digest"],
                ):
                    raise _conflict()
            if (
                HarnessSourceReview.objects.filter(
                    owner=owner, owner_kind=kind, idempotency_key=idempotency_key
                )
                .exclude(pk=metadata.get("source_review_id"))
                .exists()
            ):
                raise _conflict("This source preview was superseded.")
            captured = capture()
            _check_batch(captured)
            basis, schema = _build_basis(
                owner,
                kind,
                scope.get("target_table", selection.get("target_table")),
                captured,
            )
            row = _stage_review(
                owner,
                kind,
                basis,
                schema,
                lambda: captured,
                idempotency_key,
                digest,
                schema_repairs,
            )
            if row.state in _REVIEWABLE and metadata.get("source_review_id") != str(
                row.id
            ):
                metadata["source_review_id"] = str(row.id)
                owner.save(update_fields=["payload", "updated_at"])
            return public_review(row)
    if schema_repairs:
        raise _conflict("Schema repairs require an inferred simulation schema.")
    if kind == Owner.ENVIRONMENT:
        captured = capture()
        _check_batch(captured)
        basis, schema = _owner_basis(
            owner,
            kind,
            scope,
            records=any(item.role == "seed_records" for item in captured),
        )
        return public_review(
            _stage_review(
                owner, kind, basis, schema, lambda: captured, idempotency_key, digest
            )
        )
    basis, schema = _owner_basis(owner, kind, scope)
    return public_review(
        _stage_review(owner, kind, basis, schema, capture, idempotency_key, digest)
    )


def _find(review_id, scope: dict, *, lock=False) -> HarnessSourceReview:
    try:
        review_id = UUID(str(review_id))
    except (ValueError, TypeError):
        raise _not_found() from None
    queryset = (
        HarnessSourceReview.objects.select_related("owner").select_for_update()
        if lock
        else HarnessSourceReview.objects
    )
    row = queryset.filter(
        pk=review_id,
        owner__deleted=False,
        owner__organization_id=scope["organization_id"],
        owner__workspace_id=scope["workspace_id"],
        **scope,
    ).first()
    if row is None:
        raise _not_found()
    return row


def _end_parked_owner(row):
    if row.owner_kind not in {Owner.DESIGN, Owner.BUILD_JOB}:
        return
    from simulate.services.harness_designs import AWAITING_REVIEW

    cancelled = row.state == State.CANCELLED
    state = (
        HostedHarnessJob.State.CANCELED if cancelled else HostedHarnessJob.State.FAILED
    )
    now = timezone.now()
    HostedHarnessJob.no_workspace_objects.filter(
        pk=row.owner_id,
        current_stage=AWAITING_REVIEW,
        state=HostedHarnessJob.State.QUEUED,
        payload__metadata__source_review_id=str(row.id),
    ).update(
        state=state,
        current_stage=state,
        terminal_at=now,
        failure={
            "code": (
                "source_review_cancelled" if cancelled else "source_review_expired"
            ),
            "message": (
                "The source review was cancelled."
                if cancelled
                else "The source review expired."
            ),
        },
        updated_at=now,
    )


@transaction.atomic
def _expire(row: HarnessSourceReview) -> HarnessSourceReview:
    if row.expires_at <= timezone.now() and row.state in {State.READING, *_REVIEWABLE}:
        HarnessSourceReview.objects.filter(pk=row.id, revision=row.revision).update(
            state=State.EXPIRED, revision=row.revision + 1, preview={}
        )
        row.refresh_from_db()
        if row.state == State.EXPIRED:
            _end_parked_owner(row)
    return row


def public_review(row: HarnessSourceReview) -> dict:
    result = {
        "id": str(row.id),
        "state": row.state,
        "revision": row.revision,
        "scope": {"kind": row.owner_kind, "id": str(row.owner_id), **row.basis},
        "draft_digest": row.draft_digest or None,
        "preview": row.preview,
        "receipt": row.receipt,
        "expires_at": row.expires_at,
    }
    if row.owner_kind == Owner.ENVIRONMENT and row.state == State.ACCEPTED:
        from simulate.services.harness_source_generation import summary

        generation = HarnessSourceScenarioGeneration.objects.filter(review=row).first()
        result["scenario_generation"] = (
            summary(generation) if generation is not None else None
        )
    return result


@contextmanager
def _accepting_review(review_id, scope):
    with transaction.atomic():
        row = _expire(_find(review_id, scope, lock=True))
        if row.state != State.EXPIRED:
            yield row
            return
    raise _conflict("The source review expired. Prepare the sources again.")


def get_review(request, review_id) -> dict:
    return public_review(
        _expire(_find(review_id, require_source_scope(request, write=False)))
    )


def reprepare_review(
    request,
    review_id,
    *,
    expected_revision,
    idempotency_key,
    target_table=None,
    schema_repairs=(),
    environment=None,
    expected_draft=None,
):
    scope = require_source_scope(request, write=True)
    if environment:
        scope["owner_id"] = environment.id
    repair_request = schema_digest(
        {
            "key": idempotency_key,
            "expected_revision": expected_revision,
            "target_table": target_table,
            "schema_repairs": schema_repairs,
        }
    )
    with _accepting_review(review_id, scope) as row:
        if environment and (
            row.owner_kind != Owner.ENVIRONMENT
            or row.basis["draft"] != sources._ref_value(expected_draft)
        ):
            raise _conflict()
        if row.state not in _REVIEWABLE or not row.staged_digest:
            raise _conflict()
        staged = _read_staging(row)
        if staged["review_id"] != str(row.id):
            raise _conflict()
        if staged.get("repair_request") == repair_request:
            return public_review(row)
        if (
            row.state != State.NEEDS_REPAIR
            or row.revision != expected_revision
            or not staged.get("retained_inputs")
        ):
            raise _conflict(
                "This source preview changed. Reload it before choosing a repair."
            )
        _required_basis(row, row.owner)
        captured = []
        kb_files = defaultdict(set)
        for item in staged["retained_inputs"]:
            origin = item["origin"]
            if origin.get("kind") == "knowledge_base":
                kb_files[origin["kb_id"]].add(origin["file_id"])
            captured.append(
                CapturedKnowledgeBaseSource(
                    source_id=item["source_id"],
                    filename=item["filename"],
                    role=item["role"],
                    media_type=item["media_type"],
                    origin=origin,
                    content=base64.b64decode(item["content"]),
                    original_content=base64.b64decode(
                        item.get("original", item["content"])
                    ),
                )
            )
        for kb_id, file_ids in kb_files.items():
            kb = authorized_knowledge_base(request, kb_id)
            if kb.files.filter(pk__in=file_ids).count() != len(file_ids):
                raise _not_found()
        if row.owner_kind == Owner.ENVIRONMENT and target_table and not schema_repairs:
            scope = {
                **row.basis,
                "expected_draft": row.basis["draft"],
                "target_table": target_table,
            }
            basis, schema = _owner_basis(row.owner, Owner.ENVIRONMENT, scope)
        elif _build_selection(row.owner, row.owner_kind):
            basis, schema = _build_basis(
                row.owner, row.owner_kind, target_table, captured
            )
            if schema_repairs and schema is not None:
                raise _conflict("Schema repairs require an inferred simulation schema.")
        else:
            raise _conflict("This review does not support that repair.")
        row.basis = basis
        row.state = State.READING
        row.save(update_fields=["state", "basis"])
        return public_review(
            _stage(
                row,
                lambda: captured,
                schema,
                schema_repairs,
                repair_request=repair_request,
            )
        )


def cancel_review(
    request, review_id, *, expected_revision, expected_draft_digest
) -> dict:
    with transaction.atomic():
        row = _expire(
            _find(review_id, require_source_scope(request, write=True), lock=True)
        )
        if row.state == State.CANCELLED:
            return public_review(row)
        if row.state not in {State.READING, *_REVIEWABLE} or (
            expected_revision,
            expected_draft_digest or "",
        ) != (row.revision, row.draft_digest):
            raise _conflict()
        row.state = State.CANCELLED
        row.revision += 1
        row.preview = {}
        row.save(update_fields=["state", "revision", "preview"])
        _end_parked_owner(row)
        return public_review(row)


def _read_staging(row: HarnessSourceReview) -> dict:
    try:
        body = sources._get_private(_staging_key(row))
    except S3Error:
        raise _conflict("The prepared sources are no longer available.") from None
    if hashlib.sha256(body).hexdigest() != row.staged_digest:
        raise _conflict()
    return json.loads(body)


def _accept(
    request,
    row,
    *,
    expected_revision,
    expected_draft_digest,
    decisions,
    idempotency_key,
):
    digest = hashlib.sha256(
        sources._canonical(
            {
                "review": decisions,
                "revision": expected_revision,
                "draft_digest": expected_draft_digest,
            }
        )
    ).hexdigest()
    if row.state == State.ACCEPTED:
        if row.acceptance_key == idempotency_key and row.acceptance_digest == digest:
            if row.owner_kind == Owner.ENVIRONMENT:
                from simulate.services.harness_source_generation import reserve

                reserve(row, request.user)
            if _build_selection(row.owner, row.owner_kind):
                from simulate.services.harness_designs import schedule_reviewed_design

                schedule_reviewed_design(row.owner, request)
            return row
        raise _conflict("This source review has already been accepted.")
    if (
        row.state not in _REVIEWABLE
        or not row.draft_digest
        or not row.staged_digest
        or (row.revision, row.draft_digest)
        != (expected_revision, expected_draft_digest)
    ):
        raise _conflict()
    owner = row.owner
    _required_basis(row, owner)
    if _build_selection(owner, row.owner_kind):
        from simulate.services.harness_usage import require_harness_authoring

        require_harness_authoring(str(owner.organization_id))
    if (
        row.owner_kind != Owner.ENVIRONMENT
        and HarnessSourceReview.objects.filter(
            owner=owner,
            owner_kind=row.owner_kind,
            basis=row.basis,
            state=State.ACCEPTED,
        ).exists()
    ):
        raise _conflict("A source review was already accepted for this checkpoint.")
    staged = _read_staging(row)
    if row.owner_kind == Owner.ENVIRONMENT:
        result, artifact_digest = sources.apply_environment_review(
            request, owner, staged, row.basis, decisions
        )
    else:
        result, artifact_digest = sources.accept_checkpoint_review(
            request, owner, staged, decisions
        )
    row.receipt = {
        **result,
        "review_id": str(row.id),
        "accepted_revision": row.revision,
    }
    row.snapshot_digest = result["snapshot_digest"]
    row.acceptance_key = idempotency_key
    row.acceptance_digest = digest
    row.accepted_artifact_digest = artifact_digest
    row.state = State.ACCEPTED
    row.revision += 1
    row.save(
        update_fields=[
            "receipt",
            "snapshot_digest",
            "acceptance_key",
            "acceptance_digest",
            "accepted_artifact_digest",
            "state",
            "revision",
        ]
    )
    if row.owner_kind == Owner.ENVIRONMENT:
        from simulate.services.harness_source_generation import reserve

        reserve(row, request.user)
    if _build_selection(owner, row.owner_kind):
        from simulate.services.harness_designs import (
            PENDING_DISPATCH,
            schedule_reviewed_design,
        )

        pin_key = (
            "design_source_pin"
            if row.owner_kind == Owner.DESIGN
            else "build_source_pin"
        )
        owner.payload["metadata"][pin_key] = {
            "owner_id": str(owner.id),
            "review_id": str(row.id),
            "id": result["snapshot_id"],
            "digest": row.snapshot_digest,
            **(
                {"schema_digest": row.basis["schema_digest"]}
                if row.owner_kind == Owner.BUILD_JOB
                else {}
            ),
        }
        owner.current_stage = PENDING_DISPATCH
        owner.save(update_fields=["payload", "current_stage", "updated_at"])
        schedule_reviewed_design(owner, request)
    return row


def accept_review(
    request,
    review_id,
    *,
    expected_revision,
    expected_draft_digest,
    expected_schema_digest,
    decisions,
    idempotency_key,
) -> dict:
    scope = require_source_scope(request, write=True)
    with _accepting_review(review_id, scope) as row:
        if row.basis["schema_digest"] != expected_schema_digest:
            raise _conflict("The reviewed destination schema changed.")
        return public_review(
            _accept(
                request,
                row,
                expected_revision=expected_revision,
                expected_draft_digest=expected_draft_digest,
                decisions=decisions,
                idempotency_key=idempotency_key,
            )
        )


def accept_environment_review(
    request,
    owner,
    review_id,
    *,
    version_id,
    draft,
    draft_digest,
    decisions,
    revision=None,
    idempotency_key=None,
) -> dict:
    scope = {
        "owner_id": owner.id,
        "organization_id": owner.organization_id,
        "workspace_id": owner.workspace_id,
    }
    request_scope = require_source_scope(request, write=True)
    if any(
        request_scope[key] != scope[key] for key in ("organization_id", "workspace_id")
    ):
        raise _not_found()
    with _accepting_review(review_id, scope) as row:
        if row.owner_kind != Owner.ENVIRONMENT or (
            row.basis["environment_version_id"],
            row.basis["draft"],
        ) != (
            str(version_id),
            {
                "id": str(draft["id"]) if draft["id"] else None,
                "revision": draft["revision"],
            },
        ):
            raise _conflict()
        expected_revision = (
            revision
            if revision is not None
            else row.revision - (row.state == State.ACCEPTED)
        )
        return _accept(
            request,
            row,
            expected_revision=expected_revision,
            expected_draft_digest=draft_digest,
            decisions=decisions,
            idempotency_key=idempotency_key or str(review_id),
        ).receipt


def accepted_source_snapshot(
    review_id, *, owner: HostedHarnessJob, owner_kind: str
) -> tuple[bytes, dict]:
    """Read a scoped immutable ALK envelope for the sandbox, never for browser serialization."""
    row = _find(
        review_id,
        {
            "owner_id": owner.id,
            "organization_id": owner.organization_id,
            "workspace_id": owner.workspace_id,
            "owner_kind": owner_kind,
            "state": State.ACCEPTED,
        },
    )
    snapshot, receipt = sources.read_accepted_snapshot(
        owner, row.accepted_artifact_digest
    )
    if receipt["review_id"] != str(row.id):
        raise _conflict()
    return snapshot, receipt
