"""System environment templates.

A template is an environment the harness authored once from an agent kept in this repository
(``harness_templates/<slug>/agent``). The generated environment is committed beside that agent as
the one archive it was authored into, and every deployment seeds the same bytes into its own
object storage as an org-less system row, the way system evals are seeded. Nobody runs the
system row: someone who wants to test a template gets a copy in their organization, which from
then on is an ordinary environment of theirs.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import io
import json
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput
from simulate.services.harness_scenarios import index_scenarios
from simulate.services.hosted_harness import (
    HostedHarnessError,
    canonical_digest,
    create_hosted_job,
    provision_job_scenarios,
)
from simulate.services.hosted_harness_gateway import (
    AUTHORING_BASIS_FILE,
    PlatformSecretResolver,
    _archive_parts,
    _load_source_archive,
    authoring_content_digest,
    platform_template_target_secrets,
    put_source_archive,
    store_authoring_archive,
)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "harness_templates"
SCHEMA_VERSION = "futureagi.environment-template.v1"
MANIFEST_NAME = "template.json"
AGENT_DIR = "agent"
# The harness-authored environment, committed as the archive a sandbox restores.
AUTHORING_ARCHIVE = "authoring.tar.gz"
AUTHORED_REQUEST = "job.json"

# Live progress, not part of what was authored.
_TRANSIENT_STAGE_OUTPUTS = frozenset({"activity"})
_IGNORED_FILES = frozenset({".DS_Store"})
# What a copy may carry from the request that authored the template; everything else in the
# job's metadata is run bookkeeping that belongs to that one run.
_KEPT_METADATA = ("name", "agent_name", "domain", "authoring_key")


@dataclass(frozen=True)
class TemplateRelease:
    """One template as committed: its agent, its authored environment and its manifest."""

    slug: str
    root: Path
    manifest: dict[str, Any]

    def files(self, directory: str) -> list[tuple[str, bytes]]:
        base = self.root / directory
        return [
            (path.relative_to(base).as_posix(), path.read_bytes())
            for path in sorted(base.rglob("*"))
            if path.is_file()
            and path.name not in _IGNORED_FILES
            and "__pycache__" not in path.parts
        ]

    @property
    def digest(self) -> str:
        """Identity of everything a deployment seeds; unchanged files seed nothing."""
        digest = hashlib.sha256()
        entries = [
            (MANIFEST_NAME, (self.root / MANIFEST_NAME).read_bytes()),
            (AUTHORING_ARCHIVE, self.authoring_archive()),
        ]
        entries.extend(
            (f"{AGENT_DIR}/{name}", data) for name, data in self.files(AGENT_DIR)
        )
        for name, data in entries:
            encoded = name.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big") + encoded)
            digest.update(len(data).to_bytes(8, "big") + data)
        return digest.hexdigest()

    def source_archive(self) -> bytes:
        return _tar_gz([(f"source/{name}", data) for name, data in self.files(AGENT_DIR)])

    def authoring_archive(self) -> bytes:
        return (self.root / AUTHORING_ARCHIVE).read_bytes()


def releases() -> list[TemplateRelease]:
    """Every committed template, in slug order."""
    found = []
    for root in sorted(TEMPLATES_ROOT.iterdir()):
        path = root / MANIFEST_NAME
        if not path.is_file():
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"{path}: unsupported schema {manifest.get('schema_version')!r}")
        if manifest.get("slug") != root.name:
            raise ValueError(f"{path}: slug {manifest.get('slug')!r} != folder {root.name!r}")
        found.append(TemplateRelease(root.name, root, manifest))
    return found


def system_templates() -> QuerySet[HostedHarnessJob]:
    return HostedHarnessJob.no_workspace_objects.filter(
        organization__isnull=True, deleted=False
    ).exclude(template_slug="")


def system_template(slug: str) -> HostedHarnessJob | None:
    return system_templates().filter(template_slug=slug).first()


def template_metadata(job: HostedHarnessJob) -> dict[str, Any]:
    """What the seed recorded on a system row: release digest, stored source, provisioning."""
    return ((job.payload or {}).get("metadata") or {}).get("template") or {}


def template_summary(job: HostedHarnessJob) -> dict[str, Any]:
    """One template as the library lists it, from what the harness authored."""
    from simulate.services.harness_environment import contract_data

    meta = template_metadata(job)
    presentation = meta.get("presentation") or {}
    contract = contract_data(job)
    return {
        "slug": job.template_slug,
        "environment_id": str(job.id),
        "name": job.name,
        "description": presentation.get("description") or contract.get("one_liner") or "",
        "surface": presentation.get("surface") or "",
        "direction": presentation.get("direction") or "",
        "languages": presentation.get("languages") or [],
        "domain": presentation.get("domain") or "",
        "scenario_count": job.scenario_count,
        "tools": [
            {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
            }
            for tool in contract.get("tools") or []
            if isinstance(tool, dict) and tool.get("name")
        ],
        "rules": [str(rule) for rule in contract.get("hard_constraints") or []],
        "evaluations": list((meta.get("provision") or {}).get("chosen_evals") or []),
        "updated_at": job.content_updated_at or job.updated_at,
    }


def template_detail(job: HostedHarnessJob) -> dict[str, Any]:
    """The summary plus every scenario, each with the caller it was provisioned with."""
    documents = {
        str(doc.get("scenario_key") or doc.get("name") or ""): doc
        for doc in _scenario_documents(job.stage_outputs)
    }
    scenarios = []
    for persona in (template_metadata(job).get("provision") or {}).get("personas") or []:
        doc = documents.get(persona["scenario_key"]) or {}
        scenarios.append(
            {
                "scenario_key": persona["scenario_key"],
                "name": persona.get("scenario_name") or doc.get("name") or "",
                "use_case": str(doc.get("use_case") or ""),
                "situation": persona.get("situation") or "",
                "outcome": persona.get("outcome") or "",
                "persona": persona.get("persona") or {},
            }
        )
    return {**template_summary(job), "scenarios": scenarios}


def template_evaluations(job: HostedHarnessJob, organization, workspace) -> dict[str, Any]:
    """Preview a template's eval selection exactly as a copy in this tenant would bind it."""
    from simulate.services.harness_environment import eval_modality
    from simulate.services.harness_evals import offered_evals

    chosen = list((template_metadata(job).get("provision") or {}).get("chosen_evals") or [])
    entries = {
        entry["name"]: entry
        for entry in offered_evals(organization, workspace, eval_modality(job))
    }
    selected = [
        {**entries[name], "id": f"template:{name}", "runnable": True}
        for name in chosen
        if name in entries
    ]
    bound = {entry["name"] for entry in selected}
    return {
        "selected": selected,
        "available": [entry for name, entry in entries.items() if name not in bound],
    }


def seed_templates() -> dict[str, list[str]]:
    """Make this deployment's system templates match the committed releases."""
    outcome: dict[str, list[str]] = {"created": [], "updated": [], "unchanged": []}
    for release in releases():
        outcome[_seed(release)].append(release.slug)
    return outcome


def _seed(release: TemplateRelease) -> str:
    digest = release.digest
    existing = system_template(release.slug)
    if existing is not None and template_metadata(existing).get("digest") == digest:
        return "unchanged"
    prefix = f"harness-templates/{release.slug}/{digest}"
    authoring = release.authoring_archive()
    source_key = _put(f"{prefix}/source.tar.gz", release.source_archive())
    authoring_key = _put(f"{prefix}/authoring.tar.gz", authoring)

    manifest = release.manifest
    payload = copy.deepcopy(manifest["payload"])
    payload["source"] = {"kind": "archive"}
    payload.setdefault("metadata", {}).update(
        {
            "authoring_object_key": authoring_key,
            "authoring_revision": authoring_content_digest(authoring),
            "template": {
                "digest": digest,
                "source_object_key": source_key,
                "presentation": manifest["presentation"],
                "provision": manifest["provision"],
            },
        }
    )
    now = timezone.now()
    fields = {
        "name": manifest["name"],
        "payload": payload,
        "request_digest": canonical_digest(payload),
        "schema_version": payload["schema_version"],
        "state": HostedHarnessJob.State.COMPLETED,
        "current_stage": "completed",
        "seed": manifest["seed"],
        "scenario_count": manifest["scenario_count"],
        "artifact_level": payload["artifacts"]["level"],
        "max_artifact_bytes": payload["artifacts"]["max_artifact_bytes"],
        "deadline_at": now,
        "terminal_at": now,
        "content_updated_at": now,
        "stage_outputs": manifest["stage_outputs"],
        "bundle_digest": manifest.get("bundle_digest"),
    }
    with transaction.atomic():
        if existing is None:
            job = HostedHarnessJob.no_workspace_objects.create(
                organization=None,
                workspace=None,
                template_slug=release.slug,
                run_id=uuid.uuid4(),
                idempotency_key=f"template:{release.slug}",
                **fields,
            )
        else:
            job = existing
            for name, value in fields.items():
                setattr(job, name, value)
            job.save(update_fields=[*fields, "updated_at"])
        _replace_stage_outputs(job, manifest["normalized_stage_outputs"])
        index_scenarios(job, _scenario_documents(manifest["stage_outputs"]), prune=True)
    return "created" if existing is None else "updated"


def copy_template(
    template: HostedHarnessJob,
    *,
    organization,
    workspace,
    idempotency_key: str,
) -> tuple[HostedHarnessJob, bool]:
    """Give an organization its own copy of a system template, ready to run and edit.

    The copy owns new objects in storage, because edits rewrite an environment's archive in
    place and must never reach the template or another organization's copy. A retried request
    with the same key returns the copy it already made.
    """
    key = (
        f"template:{template.template_slug}:"
        f"{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
    )
    existing = HostedHarnessJob.no_workspace_objects.filter(
        organization=organization, idempotency_key=key
    ).first()
    if existing is not None:
        if existing.workspace_id != getattr(workspace, "id", None):
            raise HostedHarnessError(
                "idempotency_conflict",
                "the idempotency key was already used in a different workspace",
                status_code=409,
            )
        return existing, False

    meta = template_metadata(template)
    source_id = put_source_archive(organization.id, _read(meta["source_object_key"]))
    authoring = _read(template.payload["metadata"]["authoring_object_key"])

    payload = copy.deepcopy(template.payload)
    payload["source"] = {"kind": "archive", "archive_artifact_id": source_id}
    payload["metadata"] = {
        name: value
        for name, value in (payload.get("metadata") or {}).items()
        if name in _KEPT_METADATA
    }
    with transaction.atomic():
        job, created = create_hosted_job(
            organization, payload, idempotency_key=key, workspace=workspace
        )
        if not created:
            return job, False
        job.template_slug = template.template_slug
        job.name = template.name
        job.state = HostedHarnessJob.State.COMPLETED
        job.current_stage = "completed"
        job.stage_outputs = copy.deepcopy(template.stage_outputs)
        job.bundle_digest = template.bundle_digest
        job.terminal_at = timezone.now()
        job.save(
            update_fields=[
                "template_slug",
                "name",
                "state",
                "current_stage",
                "stage_outputs",
                "bundle_digest",
                "terminal_at",
                "updated_at",
            ]
        )
        _replace_stage_outputs(
            job,
            list(
                HostedHarnessStageOutput.no_workspace_objects.filter(
                    job=template
                ).values("title", "summary", "kind", "data")
            ),
        )
        store_authoring_archive(job, authoring, advance_lifecycle=False)
        index_scenarios(job, _scenario_documents(job.stage_outputs))
        provision = meta["provision"]
        provision_job_scenarios(job, provision)
        if provision.get("enable_tool_evaluation"):
            job.run_test.enable_tool_evaluation = True
            job.run_test.save(update_fields=["enable_tool_evaluation", "updated_at"])
    return job, True


def export_template(
    job: HostedHarnessJob, *, slug: str, domain: str | None = None
) -> TemplateRelease:
    """Write a harness-generated environment into the repository as template ``slug``.

    The job must have been authored from ``harness_templates/<slug>/agent`` exactly; anything
    else would commit an environment that does not describe the agent beside it.
    """
    root = TEMPLATES_ROOT / slug
    if not (root / AGENT_DIR).is_dir():
        raise HostedHarnessError(
            "template_agent_missing", f"no agent at {root / AGENT_DIR}", status_code=400
        )
    metadata = (job.payload or {}).get("metadata") or {}
    registrations = list(
        job.scenario_registrations.filter(deleted=False)
        .select_related("dataset_row")
        .order_by("number", "created_at")
    )
    if (
        job.environment_id is not None
        or job.state != HostedHarnessJob.State.COMPLETED
        or not metadata.get("authoring_object_key")
        or not job.run_test_id
        or len(registrations) != job.scenario_count
    ):
        raise HostedHarnessError(
            "template_environment_not_ready",
            "only a completed, validated environment with its full suite can become a template",
            status_code=409,
        )
    release = TemplateRelease(slug, root, {})
    _require_same_agent(job, release)
    authoring = _read(metadata["authoring_object_key"])

    previous = _manifest_at(root)
    config = json.loads((root / AGENT_DIR / "config.json").read_text(encoding="utf-8"))
    run_test = job.run_test
    definition = run_test.agent_definition
    from simulate.services.harness_evals import selected_eval_configs

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "name": str(config.get("display_name") or slug),
        "presentation": {
            "description": str(config.get("description") or ""),
            "surface": str(config.get("channel") or ""),
            "direction": str(config.get("direction") or ""),
            "languages": list(config.get("languages") or []),
            "domain": domain or (previous.get("presentation") or {}).get("domain") or "",
        },
        "payload": _template_payload(job),
        "seed": job.seed,
        "scenario_count": job.scenario_count,
        "bundle_digest": job.bundle_digest,
        "stage_outputs": [
            output
            for output in job.stage_outputs or []
            if isinstance(output, dict)
            and output.get("kind") not in _TRANSIENT_STAGE_OUTPUTS
        ],
        "normalized_stage_outputs": list(
            HostedHarnessStageOutput.no_workspace_objects.filter(job=job)
            .exclude(kind__in=_TRANSIENT_STAGE_OUTPUTS)
            .order_by("created_at")
            .values("title", "summary", "kind", "data")
        ),
        "provision": {
            "name": run_test.name,
            "description": run_test.description or "",
            "modality": (
                "voice"
                if definition.agent_type == definition.AgentTypeChoices.VOICE
                else "text"
            ),
            "agent_name": definition.agent_name,
            "agent_prompt": definition.description or "",
            "chosen_evals": [
                str(config.eval_template.name)
                for config in selected_eval_configs(run_test)
            ],
            "enable_tool_evaluation": bool(run_test.enable_tool_evaluation),
            "personas": _personas(registrations),
        },
    }
    # The retained authoring request must not keep the originating tenant's vault references.
    (root / AUTHORING_ARCHIVE).write_bytes(_template_authoring(authoring, manifest["payload"]))
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return TemplateRelease(slug, root, manifest)


def _template_payload(job: HostedHarnessJob) -> dict[str, Any]:
    """The authoring request, minus the run that made it and anything only that org holds."""
    payload = copy.deepcopy(job.payload)
    for name in ("job_id", "run_id", "execution", "platform_run_id"):
        payload.pop(name, None)
    payload["source"] = {"kind": "archive"}
    metadata = payload.get("metadata") or {}
    payload["metadata"] = {
        name: metadata[name] for name in _KEPT_METADATA if metadata.get(name)
    }
    agent = payload["agent"]
    refs = agent.get("secret_refs") or {}
    platform_values = platform_template_target_secrets()
    resolved = PlatformSecretResolver().resolve(job)
    # Vertex's boolean environment flag is case-insensitive.
    for values in (platform_values, resolved):
        if "GOOGLE_GENAI_USE_VERTEXAI" in values:
            values["GOOGLE_GENAI_USE_VERTEXAI"] = values["GOOGLE_GENAI_USE_VERTEXAI"].lower()
    unsupported = sorted(
        alias
        for alias in refs
        if alias not in platform_values or resolved.get(alias) != platform_values[alias]
    )
    if unsupported:
        raise HostedHarnessError(
            "template_secret_unsupported",
            "a template's agent may only use platform-owned keys; not allowed: "
            + ", ".join(unsupported),
            status_code=400,
        )
    agent["secret_refs"] = {
        alias: {"manager": "platform-vault", "key": alias, "purpose": "target_provider"}
        for alias in refs
    }
    return payload


def _personas(registrations) -> list[dict[str, Any]]:
    """Each registered scenario exactly as provisioned, read back from its dataset row."""
    from model_hub.models.develop_dataset import Cell

    cells: dict[str, dict[str, str]] = {}
    for cell in Cell.objects.filter(
        row_id__in=[reg.dataset_row_id for reg in registrations],
        column__name__in=("persona", "situation", "outcome"),
        deleted=False,
    ).select_related("column"):
        cells.setdefault(str(cell.row_id), {})[cell.column.name] = cell.value or ""
    personas = []
    for reg in registrations:
        row = cells.get(str(reg.dataset_row_id)) or {}
        identity = json.loads(row.get("persona") or "{}")
        personas.append(
            {
                "scenario_key": reg.scenario_key,
                "scenario_name": str(
                    (reg.dataset_row.metadata or {}).get("scenario_name") or reg.name
                ),
                "name": str(identity.get("name") or reg.name),
                "role": str(identity.get("role") or identity.get("occupation") or ""),
                "situation": row.get("situation", ""),
                "outcome": row.get("outcome", ""),
                "persona": identity,
            }
        )
    return personas


def _require_same_agent(job: HostedHarnessJob, release: TemplateRelease) -> None:
    submitted = {}
    with tarfile.open(fileobj=io.BytesIO(_load_source_archive(job)), mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = _archive_parts(member.name)
            handle = archive.extractfile(member) if member.isfile() else None
            if handle is not None and parts[:1] == ("source",) and len(parts) > 1:
                submitted["/".join(parts[1:])] = handle.read()
    if submitted != dict(release.files(AGENT_DIR)):
        raise HostedHarnessError(
            "template_agent_mismatch",
            f"this environment was not authored from {release.root / AGENT_DIR}",
            status_code=409,
        )


def _template_authoring(body: bytes, payload: dict[str, Any]) -> bytes:
    """The authored environment as a template commits it: the basis marker dropped and the
    request replaced by its sanitized form, repacked so equal content is equal bytes."""
    entries = {
        AUTHORED_REQUEST: (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    }
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = _archive_parts(member.name)
            if not member.isfile() or parts in {(AUTHORING_BASIS_FILE,), (AUTHORED_REQUEST,)}:
                continue
            if ".." in parts or member.name.startswith("/"):
                raise HostedHarnessError(
                    "template_archive_unsafe", f"unsafe path {member.name!r}"
                )
            if parts[-1] in _IGNORED_FILES or "__pycache__" in parts:
                continue
            handle = archive.extractfile(member)
            entries["/".join(parts)] = handle.read() if handle is not None else b""
    # Path order, as the archive was always packed from a folder, so re-exporting unchanged
    # content reproduces the committed bytes.
    return _tar_gz(sorted(entries.items(), key=lambda entry: PurePosixPath(entry[0]).parts))


def _manifest_at(root: Path) -> dict[str, Any]:
    path = root / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _scenario_documents(stage_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for output in stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == "scenarios":
            return list(output.get("data") or [])
    return []


def _replace_stage_outputs(job: HostedHarnessJob, outputs: list[dict[str, Any]]) -> None:
    HostedHarnessStageOutput.no_workspace_objects.filter(job=job).delete()
    HostedHarnessStageOutput.no_workspace_objects.bulk_create(
        HostedHarnessStageOutput(
            job=job,
            title=output["title"],
            summary=output.get("summary") or "",
            kind=output["kind"],
            data=output["data"],
        )
        for output in outputs
    )


def _tar_gz(entries: list[tuple[str, bytes]]) -> bytes:
    """A byte-stable archive: same files, same bytes, in every deployment."""
    out = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if data[:2] == b"#!" else 0o644
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def _put(key: str, body: bytes) -> str:
    client = get_storage_client()
    ensure_bucket(client, UPLOAD_BUCKET_NAME)
    client.put_object(
        bucket_name=UPLOAD_BUCKET_NAME,
        object_name=key,
        data=io.BytesIO(body),
        length=len(body),
        content_type="application/gzip",
    )
    return key


def _read(key: str) -> bytes:
    response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()
