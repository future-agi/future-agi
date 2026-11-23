"""Environment designs (`environment-releases.md` §7.9).

A design is a hosted job in which the environment's builder designs a test environment from a
person's requirements, optionally starting from a root release (a template) and their uploaded
documents, and seals it into an environment release and base suite. The platform registers both
as the organization's own releases; from then on the design offers what a prebuilt environment
does: use it with your agent (adaptation), run our agent on it, or customize it again.

A design has no agent and no source. Its payload carries `metadata.design`, written here only,
and its launch stages the template's archives, the documents and, for a design continued after
it parked at an unanswered question, the checkpointed workspace.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shlex
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    HarnessDesignDocument,
    HarnessEnvironmentRelease,
    HarnessSuiteRelease,
    HostedHarnessConversation,
    HostedHarnessJob,
)
from simulate.services.harness_releases import (
    _chain,
    _download,
    _error,
    _release_archives,
    _visible,
    design_world_outputs,
    register_release,
)
from simulate.services.harness_storage import put_private, read_private
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

DESIGN_OUTPUT = "/work/authoring/design-output"
DOCUMENTS_ROOT = "/work/documents"
DOCUMENTS_ARCHIVE = "/work/documents.tar.gz"
PRIVATE_SNAPSHOT = "/run/futureagi/design-snapshot.json"
PRIVATE_CONTROLLER_JOB = "/run/futureagi/reviewed-source-job.json"
AWAITING_REVIEW = "awaiting_source_review"
PENDING_DISPATCH = "source_review_dispatch_pending"
# Codes a design that can be continued ends with: it parked at an unanswered question, or ran
# out of its authoring time with its last checkpoint kept.
CONTINUABLE = frozenset({"design_waiting", "design_unfinished"})
# What one attempt leaves when it ends, relative to the workspace. A later turn's checkpoint
# can carry it, and a continued design would read it as its own result and stop at once.
ATTEMPT_TERMINAL = ("design-output", ".futureagi-build-finished.json")

DOCUMENT_MAX_BYTES = 25 * 1024 * 1024
DESIGN_DOCUMENTS_MAX = 100
DESIGN_DOCUMENTS_MAX_BYTES = 200 * 1024 * 1024
# A docx or xlsx is a zip; one that would expand past these is refused before it is stored.
_UNZIPPED_MAX_BYTES = 200 * 1024 * 1024
_ZIP_MEMBERS_MAX = 10_000
_ZIP_RATIO_MAX = 100
_TEXT_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/jsonl",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
_ZIP_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_SLUG = re.compile(r"[^a-z0-9]+")


# --- documents -----------------------------------------------------------------------------------


def document_type(name: str, body: bytes) -> str:
    """The document's content type, decided by its signature and checked before it is stored:
    a PDF, a docx or xlsx whose zip would not expand too far, or UTF-8 text."""
    suffix = Path(name).suffix.lower()
    if body.startswith(b"%PDF-"):
        if suffix != ".pdf":
            raise _error("document_unsupported", f"{name} is a PDF; name it .pdf", 415)
        return "application/pdf"
    if body.startswith(b"PK\x03\x04"):
        if suffix not in _ZIP_TYPES:
            raise _error(
                "document_unsupported", f"{name}: a zip that is not .docx or .xlsx", 415
            )
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                members = archive.infolist()
        except zipfile.BadZipFile as exc:
            raise _error(
                "document_unsupported", f"{name} is not a valid {suffix}", 415
            ) from exc
        unzipped = sum(member.file_size for member in members)
        if (
            len(members) > _ZIP_MEMBERS_MAX
            or unzipped > _UNZIPPED_MAX_BYTES
            or unzipped / max(1, len(body)) > _ZIP_RATIO_MAX
        ):
            raise _error(
                "document_unsupported", f"{name} expands too far to be read safely", 415
            )
        return _ZIP_TYPES[suffix]
    if suffix in _TEXT_TYPES:
        try:
            body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _error(
                "document_unsupported", f"{name} is not UTF-8 text", 415
            ) from exc
        return _TEXT_TYPES[suffix]
    raise _error(
        "document_unsupported",
        f"{name}: upload a PDF, DOCX, XLSX, CSV, TSV, JSON, JSONL, TXT or Markdown file",
        415,
    )


def store_design_document(
    organization, workspace, user, *, name: str, body: bytes
) -> HarnessDesignDocument:
    if len(body) > DOCUMENT_MAX_BYTES:
        raise _error("document_too_large", "a document may be at most 25 MB", 413)
    name = Path(name.replace("\\", "/")).name[:255]
    if not name or name.startswith("."):
        raise _error("document_unsupported", "the document has no file name", 415)
    content_type = document_type(name, body)
    document = HarnessDesignDocument(
        organization=organization,
        workspace=workspace,
        uploaded_by=user if getattr(user, "is_authenticated", False) else None,
        name=name,
        content_type=content_type,
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
    )
    document.object_key = f"harness-design-documents/{organization.id}/{document.id}{Path(name).suffix.lower()}"
    put_private(document.object_key, body, content_type)
    document.save()
    return document


def serialize_document(document: HarnessDesignDocument) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "name": document.name,
        "content_type": document.content_type,
        "sha256": document.sha256,
        "size": document.size,
    }


def design_source_basis(job, target_table, captured):
    from simulate.services.harness_releases import template_source_columns
    from simulate.services.harness_source_reviews import _data_type, schema_digest

    template = design_of(job).get("template")
    basis = {"revision": 1, "schema_digest": None}
    if not template:
        if target_table:
            raise _error(
                "source_schema_invalid", "Scratch designs have no template table.", 422
            )
        return basis, None
    ref = template["environment"][0]["digest"]
    basis["template_digest"] = ref
    if not any(item.role == "seed_records" for item in captured) and not target_table:
        return basis, None
    release = _visible(HarnessEnvironmentRelease, job.organization, ref)
    if release is None:
        raise _error("release_not_found", "Template environment not found.", 404)
    tables = template_source_columns(release)
    if not tables:
        raise _error(
            "source_destination_unavailable",
            "This template has no record tables. Choose another template or start a scratch design.",
            422,
        )
    if target_table is None and len(tables) != 1:
        return {**basis, "destination_tables": sorted(tables)}, None
    table = target_table or next(iter(tables))
    if table not in tables:
        raise _error(
            "source_schema_invalid", "Choose a table from the pinned template.", 422
        )
    schema = {
        "fields": [
            {"name": name, "data_type": _data_type(kind), "required": bool(required)}
            for name, kind, required in sorted(tables[table])
        ]
    }
    return {
        **basis,
        "schema_digest": schema_digest(schema),
        "target_table": table,
    }, schema


def schedule_reviewed_design(job, request):
    """Persist acceptance before the workflow starts; a repeated accept retries failed dispatch."""
    if job.current_stage != PENDING_DISPATCH:
        return
    base_url = (
        getattr(settings, "HARNESS_PUBLIC_BASE_URL", "")
        or request.build_absolute_uri("/")
    ).rstrip("/")
    transaction.on_commit(lambda: dispatch_reviewed_design(job.id, base_url))


def recover_reviewed_designs(base_url):
    """Deliver durable approvals left pending by a scheduler outage or process restart."""
    pending = (
        HostedHarnessJob.no_workspace_objects.filter(
            current_stage=PENDING_DISPATCH,
            state=HostedHarnessJob.State.QUEUED,
            deleted=False,
        )
        .order_by("updated_at")
        .values_list("id", flat=True)[:50]
    )
    count = 0
    for job_id in list(pending):
        try:
            dispatch_reviewed_design(job_id, base_url)
            count += 1
        except HostedHarnessError:
            logger.warning("reviewed_design_dispatch_pending job=%s", job_id)
    return count


@transaction.atomic
def dispatch_reviewed_design(job_id, base_url):
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    job = HostedHarnessJob.no_workspace_objects.select_for_update().get(pk=job_id)
    if (
        job.current_stage != PENDING_DISPATCH
        or job.current_attempt_number
        or job.state != HostedHarnessJob.State.QUEUED
    ):
        return
    retry = job.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(job.id),
            base_url,
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
            allow_completed_replay=False,
        )
    except WorkflowAlreadyStartedError:
        pass
    except Exception:
        raise HostedHarnessError(
            "source_dispatch_unavailable",
            "The approved design could not be scheduled. Retry acceptance.",
            status_code=503,
            retryable=True,
        ) from None
    job.current_stage = "queued"
    job.deadline_at = timezone.now() + timedelta(
        seconds=job.payload["runtime"]["max_duration_seconds"]
    )
    job.save(update_fields=["current_stage", "deadline_at", "updated_at"])


def _documents(
    organization, ids: list[Any], *, workspace_id=None, allow_duplicate_names=False
) -> list[HarnessDesignDocument]:
    wanted = [str(one) for one in ids]
    if len(set(wanted)) != len(wanted):
        raise _error("design_invalid", "a document is listed twice")
    if len(wanted) > DESIGN_DOCUMENTS_MAX:
        raise _error(
            "design_invalid", f"a design takes at most {DESIGN_DOCUMENTS_MAX} documents"
        )
    found = {
        str(one.id): one
        for one in HarnessDesignDocument.no_workspace_objects.filter(
            organization=organization,
            workspace_id=workspace_id,
            id__in=wanted,
            deleted=False,
        )
    }
    if missing := [one for one in wanted if one not in found]:
        raise _error(
            "document_not_found", f"documents not found: {', '.join(missing)}", 404
        )
    documents = [found[one] for one in wanted]
    names = [one.name for one in documents]
    if not allow_duplicate_names and (
        repeated := sorted({name for name in names if names.count(name) > 1})
    ):
        # The sandbox stages each document under its name, so one would replace the other.
        raise _error(
            "design_documents_duplicate_name",
            f"documents share a name: {', '.join(repeated)}; rename one and upload it again",
        )
    if sum(one.size for one in documents) > DESIGN_DOCUMENTS_MAX_BYTES:
        raise _error(
            "design_invalid", "a design's documents may total at most 200 MiB", 413
        )
    return documents


# --- the job -------------------------------------------------------------------------------------


def design_of(job: HostedHarnessJob) -> dict[str, Any] | None:
    value = ((job.payload or {}).get("metadata") or {}).get("design")
    return value if isinstance(value, dict) else None


def design_slug(name: str) -> str:
    """The sealed environment's name: ALK's release slug, from the person's own name for it."""
    slug = _SLUG.sub("_", name.lower()).strip("_")[:60]
    if not slug or not slug[0].isalpha():
        slug = f"env_{slug}".rstrip("_")
    return slug


def design_payload(
    organization,
    *,
    name: str,
    requirements: str,
    modality: str,
    template_suite: HarnessSuiteRelease | None = None,
    document_ids: list[Any] | None = None,
    source_selection: dict | None = None,
    target_table: str | None = None,
    workspace_id=None,
    resume_from: HostedHarnessJob | None = None,
) -> dict[str, Any]:
    """The validated hosted job payload of a design. A template must be a root release: a
    customization is a new root, and an extension cannot be one (decision 2)."""
    from simulate.serializers.harness_job import HarnessDesignCreateSerializer

    template = None
    if template_suite is not None:
        if template_suite.organization_id not in (None, organization.id):
            raise _error("release_not_found", "suite release not found", 404)
        chain = _chain(template_suite)
        if len(chain) != 1:
            raise _error(
                "design_template_invalid",
                f"{chain[-1].name} extends {chain[0].name}; customize a root release",
                422,
            )
        template = {
            "environment": [
                {"digest": one.digest, "name": one.name, "object_key": one.object_key}
                for one in chain
            ],
            "suite": {
                "digest": template_suite.digest,
                "name": template_suite.name,
                "object_key": template_suite.object_key,
            },
        }
    documents = _documents(
        organization,
        source_selection["document_ids"] if source_selection else document_ids or [],
        workspace_id=workspace_id,
        allow_duplicate_names=bool(source_selection),
    )
    serializer = HarnessDesignCreateSerializer(
        data={
            "artifacts": {"level": "full"},
            "scenario_count": len(template_suite.personas) if template_suite else 10,
            "metadata": {"name": name},
        }
    )
    if not serializer.is_valid():
        raise _error("design_invalid", json.dumps(serializer.errors, default=str))
    payload = serializer.validated_data
    payload["metadata"]["design"] = {
        "name": design_slug(name),
        "requirements": requirements,
        "modality": modality,
        "template": template,
        "documents": [serialize_document(one) for one in documents],
        "resume_from": str(resume_from.id) if resume_from is not None else None,
    }
    if source_selection:
        payload["metadata"]["design_source_selection"] = {
            **source_selection,
            "target_table": target_table,
        }
    return json.loads(json.dumps(payload, default=str))


def continued_payload(organization, job: HostedHarnessJob) -> dict[str, Any]:
    """A new design that resumes `job` from its last checkpoint, with the same request."""
    design = design_of(job)
    if design is None or job.organization_id != organization.id:
        raise _error("design_not_found", "design not found", 404)
    code = str((job.failure or {}).get("code") or "")
    if job.state != HostedHarnessJob.State.FAILED or code not in CONTINUABLE:
        raise _error(
            "design_not_continuable",
            "only a design that stopped waiting for an answer or out of time can be continued",
            409,
        )
    conversation = HostedHarnessConversation.no_workspace_objects.filter(
        job=job
    ).first()
    if conversation is None or not conversation.latest_workspace_object_key:
        raise _error(
            "design_not_continuable", "this design has no saved checkpoint", 409
        )
    template = design.get("template")
    payload = design_payload(
        organization,
        name=str((job.payload.get("metadata") or {}).get("name") or design["name"]),
        requirements=str(design.get("requirements") or ""),
        modality=str(design.get("modality") or "chat"),
        template_suite=(
            _visible(HarnessSuiteRelease, organization, template["suite"]["digest"])
            if template
            else None
        ),
        document_ids=[],
        workspace_id=job.workspace_id,
        resume_from=job,
    )
    payload["metadata"]["design"]["documents"] = design.get("documents") or []
    previous = job.payload["metadata"]
    if previous.get("design_source_selection"):
        if not previous.get("design_source_pin"):
            raise _error(
                "source_review_required",
                "Accept the source review before continuing.",
                409,
            )
        for key in ("design_source_selection", "design_source_pin", "source_review_id"):
            payload["metadata"][key] = previous[key]
    return payload


@dataclass(frozen=True)
class DesignLaunch:
    """What the gateway puts in a design job's sandbox, and the process it runs there."""

    uploads: tuple[tuple[str, bytes], ...]
    prepare_command: str
    command: str
    receipt: dict | None = None
    documents: tuple = ()

    def guest_metadata(self, metadata, attempt_id):
        return {
            **metadata,
            "design": {**metadata["design"], "attempt_id": str(attempt_id)},
        }

    def controller_job(self, shared):
        metadata = shared["metadata"]
        return {
            **shared,
            "metadata": {
                **metadata,
                "design": {**metadata["design"], "documents": self.documents},
                "source_review_receipt": self.receipt,
            },
        }


def _review_owner(job, pin):
    owner = job
    visited = set()
    while str(owner.id) != pin["owner_id"]:
        parent_id = (design_of(owner) or {}).get("resume_from")
        if parent_id is None or parent_id in visited:
            raise _error(
                "source_snapshot_changed",
                "The source review belongs to another design.",
                409,
            )
        visited.add(parent_id)
        owner = HostedHarnessJob.no_workspace_objects.filter(
            pk=parent_id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            deleted=False,
        ).first()
        if owner is None or owner.payload["metadata"].get("design_source_pin") != pin:
            raise _error(
                "source_snapshot_changed",
                "The continued source review is unavailable.",
                409,
            )
    return owner


def _documents_archive(job: HostedHarnessJob, listed: list[dict[str, Any]]) -> bytes:
    documents = _documents(
        job.organization, [one["id"] for one in listed], workspace_id=job.workspace_id
    )
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        for document in documents:
            body = read_private(document.object_key)
            if hashlib.sha256(body).hexdigest() != document.sha256:
                raise _error(
                    "document_changed",
                    f"{document.name} no longer matches its upload",
                    409,
                )
            info = tarfile.TarInfo(document.name)
            info.size, info.mode = len(body), 0o444
            archive.addfile(info, io.BytesIO(body))
    return raw.getvalue()


def _without_terminal_state(checkpoint: bytes) -> bytes:
    """The checkpoint with the previous attempt's result and finished marker left out."""
    raw = io.BytesIO()
    with (
        tarfile.open(fileobj=io.BytesIO(checkpoint), mode="r:gz") as source,
        tarfile.open(fileobj=raw, mode="w:gz") as target,
    ):
        for member in source.getmembers():
            top = member.name.removeprefix("./").split("/", 1)[0]
            if top in ATTEMPT_TERMINAL:
                continue
            target.addfile(
                member, source.extractfile(member) if member.isfile() else None
            )
    return raw.getvalue()


def design_launch_for(job: HostedHarnessJob) -> DesignLaunch | None:
    design = design_of(job)
    if design is None:
        return None
    uploads: list[tuple[str, bytes]] = []
    prepare: list[str] = []
    metadata = job.payload["metadata"]
    receipt = None
    if metadata.get("design_source_selection"):
        from simulate.services.harness_source_reviews import accepted_source_snapshot

        pin = metadata.get("design_source_pin")
        if not pin:
            raise _error(
                "source_review_required",
                "Accept the selected sources before launching.",
                409,
            )
        owner = _review_owner(job, pin)
        snapshot, receipt = accepted_source_snapshot(
            pin["review_id"], owner=owner, owner_kind="design"
        )
        if (receipt["snapshot_id"], receipt["snapshot_digest"]) != (
            pin["id"],
            pin["digest"],
        ):
            raise _error(
                "source_snapshot_changed", "The source review no longer matches.", 409
            )
        uploads.append((PRIVATE_SNAPSHOT, snapshot))
        prepare.insert(
            0,
            f"chown svc-control:svc-control {PRIVATE_SNAPSHOT} {PRIVATE_CONTROLLER_JOB} && chmod 0600 {PRIVATE_SNAPSHOT} {PRIVATE_CONTROLLER_JOB}",
        )
    if design.get("template"):
        template_uploads, extract = _release_archives(
            job.organization,
            design["template"]["environment"],
            design["template"]["suite"],
        )
        uploads += template_uploads
        prepare.append(extract)
    root = shlex.quote(DOCUMENTS_ROOT)
    if design.get("documents") and receipt is None:
        uploads.append(
            (
                DOCUMENTS_ARCHIVE,
                _documents_archive(job, design["documents"]),
            )
        )
        prepare.append(
            f"mkdir -p {root} && tar -xzf {shlex.quote(DOCUMENTS_ARCHIVE)} -C {root} && "
            f"rm {shlex.quote(DOCUMENTS_ARCHIVE)} && chmod -R a-w {root}"
        )
    if design.get("resume_from"):
        from simulate.services.hosted_harness_conversation import load_workspace_archive

        previous = HostedHarnessJob.no_workspace_objects.filter(
            id=design["resume_from"],
            organization=job.organization,
            workspace_id=job.workspace_id,
        ).first()
        conversation = (
            HostedHarnessConversation.no_workspace_objects.filter(job=previous).first()
            if previous is not None
            else None
        )
        checkpoint = (
            load_workspace_archive(conversation) if conversation is not None else None
        )
        if checkpoint is None:
            raise _error(
                "design_checkpoint_missing", "the design's checkpoint is gone", 409
            )
        # The gateway extracts /work/authoring.tar.gz into the workspace before anything runs.
        uploads.append(("/work/authoring.tar.gz", _without_terminal_state(checkpoint)))
    return DesignLaunch(
        uploads=tuple(uploads),
        prepare_command=" && ".join(prepare) or "true",
        command=(
            f"python -m fi.alk.harness.design hosted --job {PRIVATE_CONTROLLER_JOB if receipt is not None else '/work/job.json'} "
            f"--releases /work/releases --workspace /work/authoring --documents {root}"
            + (
                f" --reviewed-snapshot {PRIVATE_SNAPSHOT}"
                if receipt is not None
                else ""
            )
        ),
        receipt=receipt,
        documents=tuple(design.get("documents") or ()) if receipt is not None else (),
    )


# --- completion ----------------------------------------------------------------------------------


def _design_failure(code: str, message: str) -> dict[str, Any]:
    return {
        "domain": "environment",
        "stage": "designing",
        "code": code,
        "message": message[:1000],
    }


def _register(
    job: HostedHarnessJob, output: Path, attempt_id
) -> tuple[dict[str, Any], dict | None]:
    try:
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, _design_failure(
            "design_result_missing", "the design wrote no result.json"
        )
    if result.get("attempt_id") != str(attempt_id):
        return {}, _design_failure(
            "design_attempt_mismatch", "The design output belongs to another attempt."
        )
    if result.get("status") == "failed":
        failure = result.get("failure") or {}
        return {}, _design_failure(
            str(failure.get("code") or "design_failed"),
            str(failure.get("message") or "the design failed"),
        )
    if result.get("status") != "completed":
        return {}, _design_failure(
            "design_result_invalid", "result.json has no final status"
        )
    # Both digests are recomputed and every file checked before anything is stored; the
    # release is the organization's, never a system release.
    environment, suite = register_release(
        output / "environment",
        output / "suite",
        organization=job.organization,
        expected_design=_expected_design(job, result),
    )
    return {
        "environment": {
            "id": str(environment.id),
            "digest": environment.digest,
            "name": environment.name,
        },
        "suite": {
            "id": str(suite.id),
            "digest": suite.digest,
            "name": suite.name,
            "scenarios": len(suite.personas),
        },
    }, None


def _expected_design(job, result):
    metadata = job.payload["metadata"]
    pin = metadata.get("design_source_pin")
    if metadata.get("design_source_selection") and not pin:
        raise _error(
            "source_review_required", "The design has no accepted source pin.", 409
        )
    if pin:
        from simulate.models import HarnessSourceReview

        owner = _review_owner(job, pin)
        if not HarnessSourceReview.objects.filter(
            pk=pin["review_id"],
            owner=owner,
            workspace_id=job.workspace_id,
            organization_id=job.organization_id,
            owner_kind="design",
            state="accepted",
            snapshot_digest=pin["digest"],
            receipt__snapshot_id=pin["id"],
        ).exists():
            raise _error(
                "source_snapshot_changed",
                "The design source approval is unavailable.",
                409,
            )
    return {
        "source": {"id": pin["id"], "digest": pin["digest"]} if pin else None,
        "digests": tuple(
            (result.get(key) or {}).get("digest") for key in ("environment", "suite")
        ),
    }


@transaction.atomic
def complete_design(attempt, output: Path | None) -> HostedHarnessJob:
    """Finish a design job from its `design-output` (contract §7.9). A completed design registers
    its environment and suite as the organization's releases and records
    `metadata.design_result`; anything else fails the job with the reason, which is never
    retried: every exception is caught here, because a poll that re-entered would register
    twice."""
    job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
        pk=attempt.job_id
    )
    if job.current_attempt_number != attempt.attempt_number or job.terminal_at:
        return job
    design_result: dict[str, Any] = {}
    if output is None:
        failure = _design_failure("design_result_missing", "the design left no output")
    else:
        try:
            design_result, failure = _register(job, Path(output), attempt.id)
        except HostedHarnessError as exc:
            failure = _design_failure(exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - recorded on the job, which then fails
            failure = _design_failure(
                "design_registration_failed", f"{type(exc).__name__}: {exc}"
            )
    world: list[dict[str, Any]] = []
    if design_result:
        try:
            world = design_world_outputs(
                HarnessEnvironmentRelease.no_workspace_objects.select_related(
                    "parent"
                ).get(id=design_result["environment"]["id"])
            )
        except Exception:  # noqa: BLE001 - the world view is a convenience; the seal stands
            logger.warning(
                "design world view unavailable job=%s", job.id, exc_info=True
            )
    now = timezone.now()
    payload = dict(job.payload)
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "design_result": design_result or None,
    }
    job.payload = payload
    if world:
        # The workspace shows the sealed world beside what the session checkpointed.
        job.stage_outputs = [
            output
            for output in (job.stage_outputs or [])
            if output.get("kind") not in ("environment", "stores")
        ] + world
    job.state = (
        HostedHarnessJob.State.FAILED if failure else HostedHarnessJob.State.COMPLETED
    )
    job.current_stage = job.state
    job.failure = failure
    job.terminal_at = now
    job.content_updated_at = now
    job.save(
        update_fields=[
            "payload",
            "stage_outputs",
            "state",
            "current_stage",
            "failure",
            "terminal_at",
            "content_updated_at",
            "updated_at",
        ]
    )
    # The builder has finished; nothing it asked is left to answer here. A parked
    # question is asked again by the design that continues this one.
    # Its sandbox is gone too, so a turn cut off mid-reply no longer reads as running.
    HostedHarnessConversation.no_workspace_objects.filter(job=job).update(
        blocking_input=None,
        state=HostedHarnessConversation.State.COLD,
        updated_at=now,
    )
    return job


def unpack_output(body: bytes) -> tempfile.TemporaryDirectory[str]:
    """The design-output archive in a fresh directory, refusing links and paths outside it."""
    scratch = tempfile.TemporaryDirectory(prefix="design-output-")
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        archive.extractall(scratch.name, filter="data")
    return scratch


def publish_design(job: HostedHarnessJob) -> tuple[Any, Any]:
    """Register a completed design's sealed releases again as system releases, so every
    organization sees it as a prebuilt environment. An operator decides this (the
    `register_harness_release --from-design` command); a design itself never publishes beyond
    its organization. The archives are the ones the design registered, so both digests and
    every file are checked again on the way."""
    result = (job.payload or {}).get("metadata", {}).get("design_result") or {}
    if job.state != HostedHarnessJob.State.COMPLETED or not result:
        raise _error(
            "design_not_sealed", "only a completed design can be published", 409
        )
    environment = HarnessEnvironmentRelease.no_workspace_objects.get(
        id=result["environment"]["id"]
    )
    suite = HarnessSuiteRelease.no_workspace_objects.get(id=result["suite"]["id"])
    with tempfile.TemporaryDirectory(prefix="design-publish-") as scratch:
        root = Path(scratch)
        for name, record in (("environment", environment), ("suite", suite)):
            with tarfile.open(
                fileobj=io.BytesIO(_download(record.object_key)), mode="r:gz"
            ) as tar:
                tar.extractall(root / name, filter="data")
        return register_release(
            root / "environment",
            root / "suite",
            organization=None,
            expected_design=_expected_design(job, result),
        )
