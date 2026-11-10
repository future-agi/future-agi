from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

import structlog
import yaml
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import (
    Count,
    Max,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    prefetch_related_objects,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    AgentVersionSkillScan,
    AgentVersionSkillScanStatus,
    CallExecution,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
    Skill,
    SkillAttachment,
    SkillKind,
    SkillOrigin,
    SkillPromotion,
    SkillTag,
    SkillTagName,
    SkillTest,
    SkillTestState,
    SkillTestVerdict,
    SkillVersion,
    TestExecution,
)
from simulate.services.harness_usage import require_harness_call_usage
from simulate.services.harness_versioning.archives import (
    archive_size,
    get_archive,
    put_archive,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_selected_harness_run,
)
from simulate.services.run_results_v3_queries import (
    build_run_comparison,
    run_calls_queryset,
)
from simulate.temporal.client import start_hosted_harness_gateway_workflow

logger = structlog.get_logger(__name__)

MAX_FILES = 100
MAX_FILE_BYTES = 256 * 1024
MAX_VERSION_BYTES = 2 * 1024 * 1024
MAX_PATH_LENGTH = 512
ENTRY_FILES = {
    SkillKind.SKILL: frozenset({"SKILL.md"}),
    SkillKind.CONTEXT: frozenset({"AGENTS.md", "CLAUDE.md"}),
}
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class PreparedFiles:
    files: dict[str, bytes]
    manifest: list[dict[str, object]]
    files_hash: str
    description: str
    size: int


def skills_enabled() -> bool:
    return bool(getattr(settings, "HARNESS_SKILLS_ENABLED", False))


def require_skills() -> None:
    if not skills_enabled():
        raise HostedHarnessError(
            "skills_disabled", "the skills library is not enabled", status_code=404
        )


def skill_not_found() -> HostedHarnessError:
    return HostedHarnessError("skill_not_found", "Skill not found", status_code=404)


def scoped_skills(organization, workspace) -> QuerySet[Skill]:
    skills = Skill.no_workspace_objects.filter(organization=organization, deleted=False)
    if workspace is None:
        return skills.filter(workspace__isnull=True)
    return skills.filter(workspace=workspace)


def _tags() -> Prefetch:
    return Prefetch(
        "tags",
        queryset=SkillTag.no_workspace_objects.select_related("skill_version").order_by(
            "name"
        ),
    )


def skill_list(skills: QuerySet[Skill], *, search: str = "") -> QuerySet[Skill]:
    if search.strip():
        skills = skills.filter(
            Q(name__icontains=search.strip()) | Q(description__icontains=search.strip())
        )
    last_test = SkillTest.no_workspace_objects.filter(
        skill_version__skill=OuterRef("pk"), deleted=False
    ).order_by("-created_at")
    used_by = (
        SkillAttachment.no_workspace_objects.filter(skill=OuterRef("pk"), deleted=False)
        .order_by()
        .values("skill")
        .annotate(total=Count("id"))
        .values("total")
    )
    return skills.annotate(
        latest_version=Max(
            "versions__version_number", filter=Q(versions__deleted=False)
        ),
        used_by=Coalesce(Subquery(used_by), 0),
        last_test_version=Subquery(
            last_test.values("skill_version__version_number")[:1]
        ),
        last_test_state=Subquery(last_test.values("state")[:1]),
        last_test_verdict=Subquery(last_test.values("verdict")[:1]),
    ).prefetch_related(_tags())


def skill_detail(skill: Skill) -> Skill:
    skill.version_rows = list(
        _versions(skill).select_related("created_by").order_by("-version_number")
    )
    skill.promotion_rows = list(
        SkillPromotion.no_workspace_objects.filter(
            skill_version__skill=skill, deleted=False
        )
        .select_related("skill_version", "created_by")
        .order_by("-created_at")
    )
    skill.attachment_rows = list(
        SkillAttachment.no_workspace_objects.filter(skill=skill, deleted=False)
        .select_related("agent_definition", "skill_version")
        .order_by("-updated_at")
    )
    prefetch_related_objects([skill], _tags())
    return skill


def _refused(code: str, message: str, status_code: int = 400) -> HostedHarnessError:
    return HostedHarnessError(code, message, status_code=status_code)


def _path(raw: str) -> str:
    text = str(raw).strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    path = PurePosixPath(text)
    if (
        not text
        or len(text) > MAX_PATH_LENGTH
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _refused("skill_file_path_invalid", f"invalid file path: {raw!r}")
    return path.as_posix()


def _decoded(files: Iterable[Mapping[str, str]]) -> dict[str, bytes]:
    decoded: dict[str, bytes] = {}
    for item in files:
        path = _path(item["path"])
        if path in decoded:
            raise _refused("skill_file_duplicate", f"duplicate file: {path}")
        content = item["content"]
        if "\x00" in content:
            raise _refused("skill_file_unreadable", f"{path} is not a text file")
        body = content.encode("utf-8")
        if len(body) > MAX_FILE_BYTES:
            raise _refused(
                "skill_file_too_large",
                f"{path} is larger than {MAX_FILE_BYTES // 1024} KB",
            )
        decoded[path] = body
    if not decoded:
        raise _refused("skill_files_missing", "a version needs at least one file")
    if len(decoded) > MAX_FILES:
        raise _refused(
            "skill_too_many_files", f"a version holds at most {MAX_FILES} files"
        )
    if sum(len(body) for body in decoded.values()) > MAX_VERSION_BYTES:
        raise _refused(
            "skill_too_large",
            f"a version holds at most {MAX_VERSION_BYTES // (1024 * 1024)} MB",
        )
    return decoded


def _frontmatter(text: str) -> dict[str, object]:
    match = _FRONTMATTER.match(text)
    if match is None:
        raise _refused(
            "skill_frontmatter_missing",
            "SKILL.md must start with frontmatter holding name and description",
        )
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise _refused(
            "skill_frontmatter_invalid", "SKILL.md frontmatter is not valid YAML"
        ) from exc
    if not isinstance(data, dict):
        raise _refused(
            "skill_frontmatter_invalid", "SKILL.md frontmatter must be a mapping"
        )
    return data


def _description(kind: str, name: str, files: Mapping[str, bytes]) -> str | None:
    entries = ENTRY_FILES[SkillKind(kind)] & files.keys()
    if len(entries) != 1:
        wanted = " or ".join(sorted(ENTRY_FILES[SkillKind(kind)]))
        raise _refused("skill_entry_missing", f"the folder needs exactly one {wanted}")
    if kind != SkillKind.SKILL:
        return None
    data = _frontmatter(files["SKILL.md"].decode("utf-8"))
    if data.get("name") != name:
        raise _refused(
            "skill_frontmatter_name_mismatch",
            f"SKILL.md frontmatter name must be {name!r}",
        )
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        raise _refused(
            "skill_frontmatter_description_missing",
            "SKILL.md frontmatter needs a description",
        )
    return description.strip()


def _manifest(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [
        {
            "path": path,
            "size": len(files[path]),
            "sha256": hashlib.sha256(files[path]).hexdigest(),
        }
        for path in sorted(files)
    ]


def _files_hash(manifest: list[dict[str, object]]) -> str:
    pairs = [[entry["path"], entry["sha256"]] for entry in manifest]
    return hashlib.sha256(
        json.dumps(pairs, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _archive(files: Mapping[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in sorted(files):
                info = tarfile.TarInfo(path)
                info.size = len(files[path])
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(files[path]))
    return raw.getvalue()


def _object_key(organization_id: object, files_hash: str) -> str:
    return f"skills/{organization_id}/{files_hash}.tar.gz"


def prepare_files(
    kind: str, name: str, description: str, files: Iterable[Mapping[str, str]]
) -> PreparedFiles:
    decoded = _decoded(files)
    found = _description(kind, name, decoded)
    manifest = _manifest(decoded)
    return PreparedFiles(
        files=decoded,
        manifest=manifest,
        files_hash=_files_hash(manifest),
        description=found if found is not None else description.strip(),
        size=sum(len(body) for body in decoded.values()),
    )


def _store(organization_id: object, prepared: PreparedFiles) -> str:
    key = _object_key(organization_id, prepared.files_hash)
    put_archive(key, _archive(prepared.files))
    return key


def create_skill(
    *,
    organization,
    workspace,
    user,
    name: str,
    kind: str,
    files: Iterable[Mapping[str, str]],
    description: str = "",
    note: str = "",
) -> Skill:
    if not _NAME.match(name) or len(name) > 64:
        raise _refused(
            "skill_name_invalid",
            "a name uses lowercase letters, digits and single hyphens",
        )
    prepared = prepare_files(kind, name, description, files)
    key = _store(organization.id, prepared)
    try:
        with transaction.atomic():
            skill = Skill.no_workspace_objects.create(
                organization=organization,
                workspace=workspace,
                name=name,
                kind=kind,
                description=prepared.description,
                created_by=user,
            )
            _create_version(skill, 1, prepared, key, user, note or "First version")
    except IntegrityError as exc:
        raise _refused(
            "skill_name_taken", f"a skill named {name!r} already exists", 409
        ) from exc
    logger.info("skill_created", skill_id=str(skill.id), files_hash=prepared.files_hash)
    return skill


def _create_version(
    skill: Skill,
    number: int,
    prepared: PreparedFiles,
    key: str,
    user,
    note: str,
) -> SkillVersion:
    return SkillVersion.no_workspace_objects.create(
        skill=skill,
        organization_id=skill.organization_id,
        origin=SkillOrigin.LIBRARY,
        kind=skill.kind,
        version_number=number,
        name=skill.name,
        description=prepared.description,
        files_hash=prepared.files_hash,
        files_manifest=prepared.manifest,
        object_key=key,
        size=prepared.size,
        note=note,
        created_by=user,
    )


def _versions(skill: Skill) -> QuerySet[SkillVersion]:
    return SkillVersion.no_workspace_objects.filter(skill=skill, deleted=False)


def add_version(
    skill: Skill, *, user, files: Iterable[Mapping[str, str]], note: str = ""
) -> SkillVersion:
    prepared = prepare_files(skill.kind, skill.name, skill.description, files)
    key = _store(skill.organization_id, prepared)
    with transaction.atomic():
        locked = Skill.no_workspace_objects.select_for_update().get(id=skill.id)
        latest = _versions(locked).order_by("-version_number").first()
        if latest is not None and latest.files_hash == prepared.files_hash:
            raise _refused(
                "skill_version_unchanged",
                f"these files are the same as v{latest.version_number}",
                409,
            )
        number = (latest.version_number if latest is not None else 0) + 1
        version = _create_version(locked, number, prepared, key, user, note)
        if locked.description != prepared.description:
            locked.description = prepared.description
            locked.save(update_fields=["description", "updated_at"])
    logger.info(
        "skill_version_created",
        skill_id=str(skill.id),
        version_number=number,
        files_hash=prepared.files_hash,
    )
    return version


def version_of(skill: Skill, number: int) -> SkillVersion:
    version = _versions(skill).filter(version_number=number).first()
    if version is None:
        raise HostedHarnessError(
            "skill_version_not_found", f"v{number} does not exist", status_code=404
        )
    return version


def read_version_files(version: SkillVersion) -> list[dict[str, str]]:
    return read_archive_files(version.object_key)


def read_archive_files(object_key: str) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(get_archive(object_key))) as tar:
        for member in tar:
            if not member.isfile():
                continue
            source = tar.extractfile(member)
            body = source.read() if source is not None else b""
            files.append({"path": member.name, "content": body.decode("utf-8")})
    return files


def promote(
    skill: Skill, *, version_number: int, tag: str, reason: str, user
) -> SkillTag:
    version = version_of(skill, version_number)
    reason = reason.strip()
    tested = version_tested(version)
    if not tested and not reason:
        raise _refused(
            "skill_promotion_reason_required",
            "this version has not been tested; say why it is going out anyway",
        )
    with transaction.atomic():
        Skill.no_workspace_objects.select_for_update().get(id=skill.id)
        current = SkillTag.no_workspace_objects.filter(skill=skill, name=tag).first()
        if current is not None and current.skill_version_id == version.id:
            raise _refused(
                "skill_tag_unchanged",
                f"{tag} already points at v{version_number}",
                409,
            )
        if current is None:
            current = SkillTag.no_workspace_objects.create(
                skill=skill, name=SkillTagName(tag), skill_version=version
            )
        else:
            current.skill_version = version
            current.save(update_fields=["skill_version", "updated_at"])
        SkillPromotion.no_workspace_objects.create(
            skill_version=version,
            tag=tag,
            tested=tested,
            reason=reason,
            created_by=user,
        )
    logger.info(
        "skill_promoted",
        skill_id=str(skill.id),
        version_number=version_number,
        tag=tag,
    )
    return current


UNSUPPORTED_SOURCE_KINDS = frozenset(
    {
        HarnessAgentVersionRuntime.SourceKind.PROVIDER,
        HarnessAgentVersionRuntime.SourceKind.PHONE,
        HarnessAgentVersionRuntime.SourceKind.LIVEKIT,
    }
)


def scoped_agent(organization, workspace, agent_definition_id) -> AgentDefinition:
    agents = AgentDefinition.no_workspace_objects.filter(
        id=agent_definition_id, organization=organization, deleted=False
    )
    if workspace is not None:
        agents = agents.filter(Q(workspace=workspace) | Q(workspace__isnull=True))
    agent = agents.first()
    if agent is None:
        raise HostedHarnessError("agent_not_found", "Agent not found", status_code=404)
    return agent


def agent_source_kind(agent: AgentDefinition) -> str | None:
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition=agent, deleted=False
        )
        .order_by("-agent_version__version_number")
        .values_list("source_kind", flat=True)
        .first()
    )
    return runtime


def require_supported(agent: AgentDefinition) -> None:
    kind = agent_source_kind(agent)
    if kind in UNSUPPORTED_SOURCE_KINDS:
        raise _refused(
            "skill_runtime_unsupported",
            "this agent runs outside our sandbox, so it cannot load skills",
            422,
        )


def _attachments(agent: AgentDefinition) -> QuerySet[SkillAttachment]:
    return SkillAttachment.no_workspace_objects.filter(
        agent_definition=agent, deleted=False, skill__deleted=False
    )


def attach(
    skill: Skill,
    agent: AgentDefinition,
    *,
    tag: str | None,
    version_number: int | None,
    user,
) -> SkillAttachment:
    if (tag is None) == (version_number is None):
        raise _refused(
            "skill_attachment_ref_invalid", "attach a tag or a version, not both"
        )
    require_supported(agent)
    version = version_of(skill, version_number) if version_number is not None else None
    with transaction.atomic():
        current = (
            SkillAttachment.no_workspace_objects.select_for_update()
            .filter(skill=skill, agent_definition=agent, deleted=False)
            .first()
        )
        if current is None:
            current = SkillAttachment.no_workspace_objects.create(
                skill=skill,
                agent_definition=agent,
                tag=tag or "",
                skill_version=version,
                created_by=user,
            )
        else:
            current.tag = tag or ""
            current.skill_version = version
            current.created_by = user
            current.save(
                update_fields=["tag", "skill_version", "created_by", "updated_at"]
            )
    logger.info(
        "skill_attached",
        skill_id=str(skill.id),
        agent_definition_id=str(agent.id),
        tag=tag,
        version_number=version_number,
    )
    return current


def detach(skill: Skill, agent: AgentDefinition) -> None:
    detached = SkillAttachment.no_workspace_objects.filter(
        skill=skill, agent_definition=agent, deleted=False
    ).update(deleted=True, deleted_at=timezone.now())
    if not detached:
        raise HostedHarnessError(
            "skill_attachment_not_found",
            "this skill is not attached to the agent",
            status_code=404,
        )
    logger.info(
        "skill_detached", skill_id=str(skill.id), agent_definition_id=str(agent.id)
    )


def _resolved(attachment: SkillAttachment) -> SkillVersion | None:
    if attachment.skill_version_id is not None:
        return attachment.skill_version
    tag = SkillTag.no_workspace_objects.filter(
        skill_id=attachment.skill_id, name=attachment.tag
    ).first()
    return tag.skill_version if tag is not None else None


def attached_skills(agent: AgentDefinition) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for attachment in _attachments(agent).select_related("skill", "skill_version"):
        version = _resolved(attachment)
        rows.append(
            {
                "skill_id": attachment.skill_id,
                "name": attachment.skill.name,
                "kind": attachment.skill.kind,
                "tag": attachment.tag or None,
                "pinned_version": (
                    attachment.skill_version.version_number
                    if attachment.skill_version_id is not None
                    else None
                ),
                "resolved_version": (
                    version.version_number if version is not None else None
                ),
                "attached_at": attachment.updated_at,
            }
        )
    return rows


def _snapshot_entry(
    skill: Skill, version: SkillVersion, from_tag: str | None
) -> dict[str, object]:
    return {
        "skill_id": str(skill.id),
        "skill_version_id": str(version.id),
        "name": skill.name,
        "kind": skill.kind,
        "origin": SkillOrigin.LIBRARY,
        "version_number": version.version_number,
        "from_tag": from_tag,
        "files_hash": version.files_hash,
        "object_key": version.object_key,
    }


def skill_snapshot(
    agent: AgentDefinition | None,
    *,
    agent_version_id: object = None,
    overrides: Mapping[str, SkillVersion | None] | None = None,
) -> list[dict[str, object]] | None:
    if agent is None or not skills_enabled():
        return None
    overrides = dict(overrides or {})
    entries: list[dict[str, object]] = []
    for attachment in _attachments(agent).select_related("skill", "skill_version"):
        skill_id = str(attachment.skill_id)
        if skill_id in overrides:
            continue
        version = _resolved(attachment)
        if version is None:
            logger.warning(
                "skill_attachment_unresolved",
                skill_id=skill_id,
                agent_definition_id=str(agent.id),
                tag=attachment.tag,
            )
            continue
        entries.append(
            _snapshot_entry(attachment.skill, version, attachment.tag or None)
        )
    for version in overrides.values():
        if version is not None:
            entries.append(_snapshot_entry(version.skill, version, None))
    if agent_version_id is not None:
        entries.extend(
            _repo_entries(agent_version_id, {str(entry["name"]) for entry in entries})
        )
    return entries or None


def dispatch_skills(snapshot: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "name": entry["name"],
            "kind": entry["kind"],
            "origin": entry.get("origin", SkillOrigin.LIBRARY),
            "version_number": entry["version_number"],
            "files_hash": entry["files_hash"],
            "files": (
                []
                if entry.get("origin") == SkillOrigin.REPO
                else read_archive_files(str(entry["object_key"]))
            ),
        }
        for entry in snapshot
    ]


def _call_loads(job: HostedHarnessJob) -> list[list[dict[str, object]]]:
    if not job.test_execution_id:
        return []
    return [
        loads
        for loads in CallExecution.no_workspace_objects.filter(
            test_execution_id=job.test_execution_id, deleted=False
        ).values_list("call_metadata__skill_loads", flat=True)
        if isinstance(loads, list)
    ]


def _load_summary(
    entry: Mapping[str, object], calls: list[list[dict[str, object]]]
) -> dict[str, object]:
    reports = [
        load
        for loads in calls
        for load in loads
        if isinstance(load, Mapping) and load.get("name") == entry["name"]
    ]
    observed = (
        all(bool(report.get("observed")) for report in reports) if reports else None
    )
    return {
        "name": entry["name"],
        "kind": entry["kind"],
        "origin": entry.get("origin", SkillOrigin.LIBRARY),
        "version_number": entry["version_number"],
        "from_tag": entry.get("from_tag"),
        "observed": observed,
        "loaded_in": sum(report.get("loaded") == "yes" for report in reports),
        "total": len(reports),
    }


def run_skill_summary(job: HostedHarnessJob | None) -> list[dict[str, object]]:
    if job is None or not job.skill_snapshot:
        return []
    calls = _call_loads(job)
    return [_load_summary(entry, calls) for entry in job.skill_snapshot]


TERMINAL_RUN_STATES = frozenset(
    {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }
)


def scoped_environment(organization, workspace, environment_id) -> HostedHarnessJob:
    environments = HostedHarnessJob.no_workspace_objects.filter(
        id=environment_id,
        organization=organization,
        environment__isnull=True,
        deleted=False,
    )
    environments = (
        environments.filter(workspace__isnull=True)
        if workspace is None
        else environments.filter(workspace=workspace)
    )
    environment = environments.select_related("run_test").first()
    if environment is None:
        raise HostedHarnessError(
            "environment_not_found", "Environment not found", status_code=404
        )
    return environment


def _test_seed(test: SkillTest) -> int:
    digest = hashlib.sha256(f"skill-test:{test.id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def _dispatch_test_run(run: HostedHarnessJob) -> None:
    retry = run.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(run.id),
            str(settings.HARNESS_PUBLIC_BASE_URL).rstrip("/"),
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
        )
    except Exception:
        logger.exception("skill_test_run_dispatch_failed", run=str(run.id))
        _fail_unscheduled(run)


def _fail_unscheduled(run: HostedHarnessJob) -> None:
    message = "the skill test run could not be scheduled"
    now = timezone.now()
    with transaction.atomic():
        marked = HostedHarnessJob.no_workspace_objects.filter(
            id=run.id, state=HostedHarnessJob.State.QUEUED
        ).update(
            state=HostedHarnessJob.State.FAILED,
            current_stage="failed",
            terminal_at=now,
            failure={
                "domain": "infrastructure",
                "stage": "queued",
                "code": "scheduler_unavailable",
                "message": message,
            },
        )
        if marked:
            TestExecution.no_workspace_objects.filter(id=run.test_execution_id).update(
                status=TestExecution.ExecutionStatus.FAILED,
                completed_at=now,
                error_reason=message,
            )


def _current_version(skill: Skill, agent: AgentDefinition) -> SkillVersion | None:
    attachment = (
        _attachments(agent).filter(skill=skill).select_related("skill_version").first()
    )
    return _resolved(attachment) if attachment is not None else None


def start_test(
    skill: Skill,
    *,
    version_number: int,
    environment: HostedHarnessJob,
    agent: AgentDefinition,
    scenario_keys: list[str],
    trials: int,
    user,
) -> SkillTest:
    version = version_of(skill, version_number)
    if not scenario_keys:
        raise _refused(
            "skill_test_no_scenarios",
            "pick scenarios where this skill matters; if the environment has none, "
            "add scenarios for it first",
        )
    if environment.run_test is None or environment.run_test.agent_definition_id != (
        agent.id
    ):
        raise _refused(
            "skill_test_agent_mismatch",
            "this environment tests a different agent",
            409,
        )
    require_supported(agent)
    require_harness_call_usage(environment)
    baseline = _current_version(skill, agent)
    with transaction.atomic():
        test = SkillTest.no_workspace_objects.create(
            skill_version=version,
            agent_definition=agent,
            environment=environment,
            baseline_version=baseline,
            scenario_keys=list(scenario_keys),
            trials=trials,
            created_by=user,
        )
        seed = _test_seed(test)
        runs: dict[str, HostedHarnessJob] = {}
        for arm, override in (("a", baseline), ("b", version)):
            run, _ = create_selected_harness_run(
                environment,
                scenario_keys=list(scenario_keys),
                trials=trials,
                idempotency_key=f"skill-test:{test.id}:{arm}",
                seed=seed,
                skill_test=test,
                skill_overrides={str(skill.id): override},
            )
            runs[arm] = run
        test.run_a = runs["a"]
        test.run_b = runs["b"]
        test.save(update_fields=["run_a", "run_b", "updated_at"])
        for run in runs.values():
            transaction.on_commit(lambda run=run: _dispatch_test_run(run))
    logger.info(
        "skill_test_started",
        skill_id=str(skill.id),
        version_number=version_number,
        test_id=str(test.id),
    )
    return test


def _verdict(
    comparison: Mapping[str, object], loads: Mapping[str, object]
) -> SkillTestVerdict:
    summary = comparison.get("summary") or {}
    improved = int(summary.get("improved") or 0)
    regressed = int(summary.get("regressed") or 0)
    unchanged = int(summary.get("no_meaningful_change") or 0)
    if loads.get("observed") and not loads.get("loaded_in"):
        return SkillTestVerdict.NOT_USED
    if improved and regressed:
        return SkillTestVerdict.HELPS_WITH_REGRESSION
    if improved:
        return SkillTestVerdict.HELPS
    if regressed or unchanged:
        return SkillTestVerdict.NO_IMPROVEMENT
    return SkillTestVerdict.INCONCLUSIVE


def _candidate_loads(test: SkillTest) -> dict[str, object]:
    for row in run_skill_summary(test.run_b):
        if row["name"] == test.skill_version.skill.name:
            return row
    return {"observed": None, "loaded_in": 0, "total": 0}


def refresh_test(test: SkillTest) -> SkillTest:
    if test.state != SkillTestState.RUNNING:
        return test
    runs = (test.run_a, test.run_b)
    if any(run is None or run.state not in TERMINAL_RUN_STATES for run in runs):
        return test
    now = timezone.now()
    if any(run.state != HostedHarnessJob.State.COMPLETED for run in runs):
        test.state = SkillTestState.FAILED
        test.finished_at = now
        test.summary = {"runs": {"a": test.run_a.state, "b": test.run_b.state}}
        test.save(update_fields=["state", "finished_at", "summary", "updated_at"])
        return test
    candidate, baseline = test.run_b.test_execution, test.run_a.test_execution
    comparison = build_run_comparison(
        candidate,
        run_calls_queryset(candidate),
        against=baseline,
        with_pairs=True,
    )
    loads = _candidate_loads(test)
    test.verdict = _verdict(comparison, loads)
    test.state = SkillTestState.FINISHED
    test.finished_at = now
    test.summary = {
        "comparison": comparison.get("summary"),
        "pairs": comparison.get("pairs"),
        "loads": loads,
    }
    test.save(
        update_fields=["verdict", "state", "finished_at", "summary", "updated_at"]
    )
    logger.info("skill_test_finished", test_id=str(test.id), verdict=test.verdict)
    return test


def skill_tests(skill: Skill) -> list[SkillTest]:
    tests = list(
        SkillTest.no_workspace_objects.filter(skill_version__skill=skill, deleted=False)
        .select_related(
            "skill_version__skill",
            "baseline_version",
            "agent_definition",
            "run_a__test_execution",
            "run_b__test_execution",
            "created_by",
        )
        .order_by("-created_at")
    )
    return [refresh_test(test) for test in tests]


def version_tested(version: SkillVersion) -> bool:
    return SkillTest.no_workspace_objects.filter(
        skill_version=version, state=SkillTestState.FINISHED, deleted=False
    ).exists()


SCAN_MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
SCAN_MAX_SKILLS = 50
_SOURCE_ROOT = "source/"
_SCAN_SKIP_PARTS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages"}
)
_CONTEXT_NAMES = {"AGENTS.md": "agents-md", "CLAUDE.md": "claude-md"}


@dataclass(frozen=True)
class FoundItem:
    kind: str
    path: str
    name: str
    description: str
    problem: str
    manifest: list[dict[str, object]]
    files_hash: str
    size: int


def _upload_key(runtime: HarnessAgentVersionRuntime) -> str | None:
    from simulate.services.hosted_harness_gateway import _source_object_key

    if runtime.source_kind != HarnessAgentVersionRuntime.SourceKind.ARCHIVE:
        return None
    archive_id = str((runtime.source or {}).get("archive_artifact_id") or "")
    if not archive_id:
        return None
    return _source_object_key(runtime.organization_id, archive_id)


def _source_members(tar: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in tar:
        if not member.isfile() or not member.name.startswith(_SOURCE_ROOT):
            continue
        path = member.name[len(_SOURCE_ROOT) :]
        if path and not _SCAN_SKIP_PARTS & set(PurePosixPath(path).parts):
            members[path] = member
    return members


def _read_members(
    tar: tarfile.TarFile, members: Mapping[str, tarfile.TarInfo]
) -> dict[str, bytes]:
    read: dict[str, bytes] = {}
    for path, member in members.items():
        source = tar.extractfile(member)
        read[path] = source.read() if source is not None else b""
    return read


def _repo_frontmatter(body: bytes, fallback: str) -> tuple[str, str, str]:
    try:
        data = _frontmatter(body.decode("utf-8"))
    except UnicodeDecodeError:
        return fallback, "", "not_text"
    except HostedHarnessError as exc:
        return fallback, "", exc.code.removeprefix("skill_")
    name = data.get("name")
    if not isinstance(name, str) or not _NAME.match(name) or len(name) > 64:
        return fallback, "", "frontmatter_name_invalid"
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return name, "", "frontmatter_description_missing"
    return name, description.strip(), ""


def _found_item(
    kind: str, path: str, name: str, files: Mapping[str, bytes]
) -> FoundItem:
    manifest = _manifest(files)
    description, problem = "", ""
    if kind == SkillKind.SKILL:
        name, description, problem = _repo_frontmatter(files["SKILL.md"], name)
    return FoundItem(
        kind=kind,
        path=path,
        name=name[:64],
        description=description,
        problem=problem,
        manifest=manifest,
        files_hash=_files_hash(manifest),
        size=sum(len(body) for body in files.values()),
    )


def _oversized_item(
    path: str, name: str, members: Mapping[str, tarfile.TarInfo]
) -> FoundItem:
    manifest = [
        {"path": rel, "size": members[rel].size, "sha256": ""}
        for rel in sorted(members)
    ]
    return FoundItem(
        kind=SkillKind.SKILL,
        path=path,
        name=name[:64],
        description="",
        problem="too_large",
        manifest=manifest,
        files_hash=_files_hash(
            [{"path": f"{e['path']}:{e['size']}", "sha256": ""} for e in manifest]
        ),
        size=sum(member.size for member in members.values()),
    )


def _skill_folders(members: Mapping[str, tarfile.TarInfo]) -> list[str]:
    folders = {
        PurePosixPath(path).parent.as_posix()
        for path in members
        if PurePosixPath(path).name == "SKILL.md"
    }
    folders.discard(".")
    return sorted(folders)[:SCAN_MAX_SKILLS]


def _found_items(body: bytes) -> list[FoundItem]:
    items: list[FoundItem] = []
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        members = _source_members(tar)
        for folder in _skill_folders(members):
            inside = {
                path[len(folder) + 1 :]: member
                for path, member in members.items()
                if path.startswith(f"{folder}/")
            }
            fallback = PurePosixPath(folder).name.lower()
            if (
                len(inside) > MAX_FILES
                or sum(member.size for member in inside.values()) > MAX_VERSION_BYTES
            ):
                items.append(_oversized_item(folder, fallback, inside))
                continue
            items.append(
                _found_item(
                    SkillKind.SKILL, folder, fallback, _read_members(tar, inside)
                )
            )
        for filename, name in _CONTEXT_NAMES.items():
            member = members.get(filename)
            if member is None or member.size > MAX_FILE_BYTES:
                continue
            files = _read_members(tar, {filename: member})
            items.append(_found_item(SkillKind.CONTEXT, filename, name, files))
    return items


def _scan_source(key: str) -> tuple[str | None, list[FoundItem]]:
    try:
        size = archive_size(key)
        if size is None:
            return AgentVersionSkillScanStatus.SOURCE_MISSING, []
        if size > SCAN_MAX_COMPRESSED_BYTES:
            return AgentVersionSkillScanStatus.TOO_LARGE, []
        return AgentVersionSkillScanStatus.SCANNED, _found_items(get_archive(key))
    except (S3Error, tarfile.TarError, OSError) as exc:
        logger.warning("skill_scan_failed", source_key=key, error=type(exc).__name__)
        return None, []


def _repo_version(organization_id: object, item: FoundItem) -> SkillVersion:
    lookup = {
        "organization_id": organization_id,
        "origin": SkillOrigin.REPO,
        "files_hash": item.files_hash,
        "path": item.path,
        "deleted": False,
    }
    existing = SkillVersion.no_workspace_objects.filter(**lookup).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return SkillVersion.no_workspace_objects.create(
                organization_id=organization_id,
                origin=SkillOrigin.REPO,
                kind=item.kind,
                path=item.path,
                name=item.name,
                description=item.description,
                readable=not item.problem,
                problem=item.problem,
                files_hash=item.files_hash,
                files_manifest=item.manifest,
                size=item.size,
            )
    except IntegrityError:
        return SkillVersion.no_workspace_objects.get(**lookup)


def scan_agent_version(agent_version_id: object) -> AgentVersionSkillScan | None:
    existing = AgentVersionSkillScan.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    ).first()
    if existing is not None:
        return existing
    runtime = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=agent_version_id
        )
        .only("organization_id", "source_kind", "source", "agent_version_id")
        .first()
    )
    key = _upload_key(runtime) if runtime is not None else None
    if key is None:
        return None
    status, items = _scan_source(key)
    if status is None:
        return None
    versions = [_repo_version(runtime.organization_id, item) for item in items]
    try:
        with transaction.atomic():
            scan = AgentVersionSkillScan.no_workspace_objects.create(
                agent_version_id=agent_version_id,
                organization_id=runtime.organization_id,
                source_key=key,
                status=status,
            )
            scan.skill_versions.set(versions)
    except IntegrityError:
        return AgentVersionSkillScan.no_workspace_objects.filter(
            agent_version_id=agent_version_id
        ).first()
    logger.info(
        "agent_version_skills_scanned",
        agent_version_id=str(agent_version_id),
        status=status,
        found=len(versions),
    )
    return scan


def scan_after_commit(agent_version_id: object) -> None:
    if skills_enabled():
        transaction.on_commit(lambda: scan_agent_version(agent_version_id), robust=True)


def _latest_agent_version(agent: AgentDefinition) -> AgentVersion | None:
    return (
        AgentVersion.no_workspace_objects.filter(agent_definition=agent, deleted=False)
        .order_by("-version_number")
        .first()
    )


def repo_skills(agent: AgentDefinition) -> dict[str, object]:
    version = _latest_agent_version(agent)
    if version is None:
        return {"agent_version_number": None, "status": "no_version", "items": []}
    if agent_source_kind(agent) != HarnessAgentVersionRuntime.SourceKind.ARCHIVE:
        return {
            "agent_version_number": version.version_number,
            "status": "unsupported_source",
            "items": [],
        }
    scan = AgentVersionSkillScan.no_workspace_objects.filter(
        agent_version=version
    ).first()
    if scan is None:
        return {
            "agent_version_number": version.version_number,
            "status": "not_scanned",
            "items": [],
        }
    return {
        "agent_version_number": version.version_number,
        "status": scan.status,
        "items": list(
            scan.skill_versions.filter(deleted=False).order_by("kind", "path")
        ),
    }


def _scanned_item(
    agent: AgentDefinition, skill_version_id: object
) -> tuple[AgentVersionSkillScan, SkillVersion]:
    version = _latest_agent_version(agent)
    scan = (
        AgentVersionSkillScan.no_workspace_objects.filter(agent_version=version).first()
        if version is not None
        else None
    )
    item = (
        scan.skill_versions.filter(id=skill_version_id, deleted=False).first()
        if scan is not None
        else None
    )
    if item is None:
        raise HostedHarnessError(
            "repo_skill_not_found",
            "this skill is not in the agent's latest version",
            status_code=404,
        )
    return scan, item


def _item_bytes(scan: AgentVersionSkillScan, item: SkillVersion) -> dict[str, bytes]:
    prefix = f"{_SOURCE_ROOT}{item.path}"
    try:
        with tarfile.open(
            fileobj=io.BytesIO(get_archive(scan.source_key)), mode="r:gz"
        ) as tar:
            if item.kind == SkillKind.CONTEXT:
                wanted = {item.path: tar.getmember(prefix)}
            else:
                wanted = {
                    member.name[len(prefix) + 1 :]: member
                    for member in tar
                    if member.isfile() and member.name.startswith(f"{prefix}/")
                }
            return _read_members(tar, wanted)
    except (S3Error, tarfile.TarError, KeyError, OSError) as exc:
        raise HostedHarnessError(
            "repo_skill_source_unavailable",
            "the uploaded folder this skill came from can no longer be read",
            status_code=422,
        ) from exc


def repo_skill_files(
    agent: AgentDefinition, skill_version_id: object
) -> list[dict[str, str]]:
    scan, item = _scanned_item(agent, skill_version_id)
    return [
        {"path": path, "content": body.decode("utf-8", errors="replace")}
        for path, body in sorted(_item_bytes(scan, item).items())
    ]


def copy_to_library(
    agent: AgentDefinition, skill_version_id: object, *, workspace, user
) -> Skill:
    scan, item = _scanned_item(agent, skill_version_id)
    if not item.readable:
        raise _refused(
            "repo_skill_unreadable",
            f"this skill cannot be copied: {item.problem.replace('_', ' ')}",
            422,
        )
    try:
        files = [
            {"path": path, "content": body.decode("utf-8")}
            for path, body in sorted(_item_bytes(scan, item).items())
        ]
    except UnicodeDecodeError as exc:
        raise _refused(
            "repo_skill_not_text",
            "only text files can be copied into the library",
            422,
        ) from exc
    return create_skill(
        organization=agent.organization,
        workspace=workspace,
        user=user,
        name=item.name,
        kind=item.kind,
        files=files,
        description=item.description,
        note=f"Copied from {agent.agent_name} at {item.path}"[:255],
    )


def _repo_entries(
    agent_version_id: object, library_names: set[str]
) -> list[dict[str, object]]:
    scan = AgentVersionSkillScan.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    ).first()
    if scan is None:
        return []
    return [
        {
            "skill_id": None,
            "skill_version_id": str(item.id),
            "name": item.name,
            "kind": item.kind,
            "origin": SkillOrigin.REPO,
            "version_number": None,
            "from_tag": None,
            "files_hash": item.files_hash,
            "object_key": "",
            "path": item.path,
        }
        for item in scan.skill_versions.filter(
            deleted=False, kind=SkillKind.SKILL, readable=True
        ).order_by("path")
        if _NAME.match(item.name) and item.name not in library_names
    ]
