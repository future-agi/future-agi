"""Give environments built before versioning their versions, agent and run pins.

Run by hand after deploy with ``manage.py harness_versions_backfill`` (``--check`` first
for a read-only dry pass), never as a migration, so a backfill problem cannot block other
migrations. Idempotent: an environment already versioned only gains what it still lacks.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from minio.error import S3Error
from urllib3.exceptions import HTTPError as TransportError

from simulate.models.agent_version import AgentVersion
from simulate.models.harness_versioning import (
    ArchiveLayout,
    HarnessAgentVersionRuntime,
    HashKind,
    VersionOrigin,
    VersionStatus,
)
from simulate.models.hosted_harness import MAX_SCENARIOS_PER_JOB, HostedHarnessJob
from simulate.services.harness_versioning import archives, diffs, hashing
from simulate.services.harness_versioning.assembly import (
    legacy_runtime,
    runtime_identity,
    seed_ready_bundle,
)
from simulate.services.harness_versioning.versions import (
    MEMBER_FIELDS,
    SEALED_MANIFEST,
    SUITE_OUTPUT_KINDS,
    _land,
    _outgoing_pair,
    _remember_pair,
    note_revision,
    prepare_pieces,
    recorded_revisions,
)

logger = logging.getLogger(__name__)

CERTIFICATE = "runtime-validation.json"
_KEY_DIGEST = re.compile(r"/([0-9a-f]{64})\.tar\.gz$")
_MISSING_CODES = frozenset({"NoSuchKey", "NoSuchBucket"})
READ_ATTEMPTS = 3
_SAVE_ATTEMPTS = 3
# Revision labels a job carried that its archive's bytes did not match; never pinned.
UNVERIFIED_REVISIONS = "unverified_revisions"
# An archive that exists but cannot be split: versions would refuse every later save.
UNREADABLE_CODES = frozenset({"unreadable", "too_large", "incomplete"})
# Outcomes that need someone to look: the command exits with status 1 on any of them.
FAILED_OUTCOMES = frozenset(
    {
        "error",
        "changed_during_backfill",
        "skipped_unreadable",
        "not_found",
        "not_environment",
        "no_archive",
    }
)
# A job still running saves its whole payload from an unlocked read, so payload writes
# to it could be lost; it gets versions and pins, which are separate columns, only.
SETTLED_STATES = frozenset(
    {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }
)


@dataclass(frozen=True)
class Models:
    """The models the backfill reads and writes, looked up in an app registry."""

    AgentDefinition: Any
    AgentVersion: Any
    HarnessAgentVersionRuntime: Any
    HarnessAssembledBundle: Any
    HarnessEnvironmentVersion: Any
    HarnessSuiteMember: Any
    HarnessSuiteVersion: Any
    HostedHarnessJob: Any
    HostedHarnessScenario: Any
    RunTest: Any
    TestExecution: Any

    @classmethod
    def from_apps(cls, apps: Any) -> Models:
        return cls(
            **{one.name: apps.get_model("simulate", one.name) for one in fields(cls)}
        )


def default_models() -> Models:
    from django.apps import apps

    return Models.from_apps(apps)


def _live(model: Any):
    return model._base_manager.filter(deleted=False)


@dataclass
class BackfillResult:
    job_id: str
    outcome: str
    layout: str | None = None
    agent_source: str = ""
    agent_pinned: bool = False
    bundle_recorded: bool = False
    runs_pinned: int = 0
    runs_unmatched: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Archive:
    """What the backfill needs from an environment's archive, or why it has none."""

    pieces: Any = None
    sealed: dict[str, bytes] | None = None
    # The suite's scenarios as the archive holds them, each with its key.
    scenarios: list[dict[str, Any]] | None = None
    # The revision of the archive's actual bytes.
    revision: str = ""
    error: str = ""
    # missing / unreadable / too_large / incomplete
    error_code: str = ""


def _metadata(job: Any) -> dict[str, Any]:
    return dict((job.payload or {}).get("metadata") or {})


def _settled(job: Any) -> bool:
    return job.state in SETTLED_STATES and not _metadata(job).get("scenario_extend")


def get_archive(key: str, *, attempts: int = READ_ATTEMPTS) -> bytes | None:
    """The archive's bytes, ``None`` when it is gone; other storage errors are retried."""
    for attempt in range(1, attempts + 1):
        try:
            return archives.get_archive(key)
        except S3Error as exc:
            if exc.code in _MISSING_CODES:
                return None
            if attempt == attempts:
                raise
        except (OSError, TransportError):
            if attempt == attempts:
                raise
        time.sleep(attempt)
    return None


def _read(job: Any, key: str) -> _Archive:
    body = get_archive(key)
    if body is None:
        return _Archive(error="archive missing", error_code="missing")
    return split_archive(job, body)


def split_archive(job: Any, body: bytes) -> _Archive:
    """What the backfill would record from these archive bytes; stores nothing."""
    from simulate.services.hosted_harness import HostedHarnessError
    from simulate.services.hosted_harness_gateway import (
        authoring_content_digest,
        authoring_stage_outputs_from_archive,
    )

    try:
        split = archives.split_authoring(archives.read_archive(body))
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        too_large = (
            isinstance(exc, HostedHarnessError) and exc.code == "archive_too_large"
        )
        return _Archive(
            error=f"archive unreadable: {type(exc).__name__}",
            error_code="too_large" if too_large else "unreadable",
        )
    if "contract.json" not in split.environment or not any(
        path.startswith(archives.SUITE_ROOTS) for path in split.suite
    ):
        return _Archive(
            error="archive has no contract or no scenarios", error_code="incomplete"
        )
    revision = authoring_content_digest(body)
    pieces = prepare_pieces(
        job,
        split,
        outputs=authoring_stage_outputs_from_archive(body),
        authoring_revision=revision,
    )
    return _Archive(
        pieces=pieces,
        sealed=dict(split.sealed),
        scenarios=_archive_scenarios(split.suite),
        revision=revision,
    )


def _json_document(body: bytes | None) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _archive_scenarios(suite: Mapping[str, bytes]) -> list[dict[str, Any]]:
    """Each scenario in the archive, keyed.

    The index entry is merged with its folder's ``scenario.json``; the folder document wins,
    because that is what a run replays. An entry finds its folder by folder, key or name,
    and takes its key from the folder when it has none.
    """
    from simulate.services.hosted_harness_gateway import _scenario_token

    documents: dict[str, dict[str, Any]] = {}
    for path in sorted(suite):
        parts = path.split("/")
        if len(parts) == 3 and parts[2] == "scenario.json":
            document = _json_document(suite[path])
            if isinstance(document, dict):
                documents.setdefault(parts[1], document)
    by_token: dict[str, str] = {}
    for folder in documents:
        by_token.setdefault(_scenario_token(folder), folder)
    by_name: dict[str, str] = {}
    for folder, document in documents.items():
        if token := _scenario_token(document.get("name")):
            by_name.setdefault(token, folder)
    index = _json_document(suite.get("scenarios.json"))
    entries = (
        [one for one in index if isinstance(one, dict)]
        if isinstance(index, list)
        else [{"folder": folder} for folder in documents]
    )
    scenarios = []
    for entry in entries:
        folder = next(
            (
                found
                for found in (
                    by_token.get(_scenario_token(entry.get("folder"))),
                    by_token.get(_scenario_token(entry.get("scenario_key"))),
                    by_name.get(_scenario_token(entry.get("name"))),
                )
                if found
            ),
            "",
        )
        document = {**entry, **documents.get(folder, {})}
        document["scenario_key"] = str(document.get("scenario_key") or folder or "")
        scenarios.append(document)
    return scenarios


def _stage_output(job: Any, kind: str) -> Any:
    for output in job.stage_outputs or []:
        if isinstance(output, dict) and output.get("kind") == kind:
            return output.get("data")
    return None


def _legacy_fields(
    job: Any, key: str, revision: str
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Hash, hash kind and fields of a version kept on the combined archive it lost."""
    match = _KEY_DIGEST.search(key)
    if revision:
        hash_value, hash_kind = revision, HashKind.LEGACY_COMBINED
    elif match:
        hash_value, hash_kind = f"sha256:{match.group(1)}", HashKind.LEGACY_KEY
    else:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        hash_value, hash_kind = f"sha256:{digest}", HashKind.LEGACY_KEY
    common = {
        "archive_object_key": key,
        "archive_layout": ArchiveLayout.COMBINED,
        # The archive is gone: the key names the bytes it was first written with.
        "archive_sha256": match.group(1) if match else "",
        "archive_size": 0,
    }
    outputs = [one for one in job.stage_outputs or [] if isinstance(one, dict)]
    sub_goals_document = _stage_output(job, "sub_goals")
    sub_goals = [
        goal
        for goal in (
            (sub_goals_document or {}).get("sub_goals")
            if isinstance(sub_goals_document, dict)
            else []
        )
        or []
        if isinstance(goal, dict) and goal.get("name")
    ]
    scenarios = _stage_output(job, "scenarios")
    revisions = [revision] if revision else []
    environment = {
        **common,
        "documents": {
            "contract": _stage_output(job, "contract") or {},
            "sub_goals": sub_goals,
            "sub_goal_digests": hashing.sub_goal_digests(sub_goals),
            "stage_outputs": [
                one for one in outputs if one.get("kind") not in SUITE_OUTPUT_KINDS
            ],
            "authoring_revisions": revisions,
        },
    }
    suite = {
        **common,
        "documents": {
            "scenarios": [one for one in scenarios or [] if isinstance(one, dict)],
            "coverage": _stage_output(job, "coverage"),
            "stage_outputs": [
                one for one in outputs if one.get("kind") in SUITE_OUTPUT_KINDS
            ],
            "authoring_revisions": revisions,
        },
    }
    return hash_value, hash_kind, environment, suite


@dataclass
class MemberMatch:
    """How live scenario rows line up with the archive's scenarios."""

    pairs: list[tuple[Any, dict[str, Any], int]] = field(default_factory=list)
    by_key: int = 0
    by_name: int = 0
    scenarios_without_row: int = 0
    rows_without_scenario: int = 0
    duplicate_rows: int = 0


def _live_rows(models: Models, job: Any) -> list[Any]:
    return list(
        _live(models.HostedHarnessScenario)
        .filter(job_id=job.id)
        .order_by("number", "created_at", "id")
    )


def match_members(rows: list[Any], scenarios: list[dict[str, Any]]) -> MemberMatch:
    """Rows matched to archive scenarios: every key match first, then names; each row once.

    Matching every key before any name means one scenario's name can never take the row
    another scenario owns by key.
    """
    from simulate.services.hosted_harness_gateway import _scenario_token

    match = MemberMatch()
    by_key: dict[str, Any] = {}
    for row in rows:
        if token := _scenario_token(row.scenario_key):
            if token in by_key:
                match.duplicate_rows += 1
            by_key.setdefault(token, row)
    by_name: dict[str, Any] = {}
    for row in rows:
        if token := _scenario_token(row.name):
            by_name.setdefault(token, row)
    used: set[Any] = set()
    paired: dict[int, tuple[Any, dict[str, Any], int]] = {}
    for position, document in enumerate(scenarios, start=1):
        row = by_key.get(_scenario_token(document.get("scenario_key")))
        if row is not None and row.id not in used:
            used.add(row.id)
            paired[position] = (row, document, position)
            match.by_key += 1
    for position, document in enumerate(scenarios, start=1):
        if position in paired:
            continue
        row = by_name.get(_scenario_token(document.get("name")))
        if row is None:
            row = by_key.get(_scenario_token(document.get("name")))
        if row is not None and row.id not in used:
            used.add(row.id)
            paired[position] = (row, document, position)
            match.by_name += 1
    match.pairs = [paired[position] for position in sorted(paired)]
    match.scenarios_without_row = len(scenarios) - len(match.pairs)
    match.rows_without_scenario = len(rows) - len(used)
    return match


def _members(
    models: Models,
    job: Any,
    scenarios: list[dict[str, Any]] | None,
    notes: list[str],
) -> list[Any]:
    """One member per archive scenario that has a live row; nothing is re-indexed.

    Without the archive, every live row is a member with its own columns.
    """
    rows = _live_rows(models, job)

    def member(row: Any, position: int, source: Mapping[str, Any]) -> Any:
        return models.HarnessSuiteMember(
            scenario_id=row.id,
            scenario_key=row.scenario_key,
            number=row.number or position,
            content={
                name: source[name]
                for name in MEMBER_FIELDS
                if source.get(name) not in (None, "")
            },
        )

    if scenarios is None:
        return [
            member(row, position, vars(row))
            for position, row in enumerate(rows, start=1)
        ]
    match = match_members(rows, scenarios)
    if match.scenarios_without_row:
        notes.append(
            f"{match.scenarios_without_row} archive scenarios without a live row"
        )
    if match.rows_without_scenario:
        notes.append(
            f"{match.rows_without_scenario} live rows without an archive scenario"
        )
    return [member(row, position, document) for row, document, position in match.pairs]


# --- agent -----------------------------------------------------------------------------


def _clean(values: Mapping[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in values.items() if value not in (None, "")}


def _safe_url(value: object) -> str:
    """A URL without credentials, query or fragment, which can carry tokens."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def legacy_source(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """The non-secret source facts of a pre-versioning agent, by source kind."""
    source = payload.get("source") or {}
    agent = payload.get("agent") or {}
    config = agent.get("config") or {}
    connector = str(agent.get("connector") or "")
    kind = str(source.get("kind") or "")
    Kind = HarnessAgentVersionRuntime.SourceKind
    if kind == "github":
        return Kind.GITHUB, _clean(
            {
                "repository": source.get("repository"),
                "ref": source.get("ref"),
                "commit_sha": source.get("commit_sha"),
            }
        )
    if kind == "archive":
        return Kind.ARCHIVE, _clean(
            {"archive_artifact_id": str(source.get("archive_artifact_id") or "")}
        )
    if connector == "phone":
        return Kind.PHONE, _clean(
            {
                "phone_number": config.get("phone_number"),
                "call_direction": agent.get("call_direction"),
            }
        )
    if connector == "livekit":
        # TODO: payloads carry ``livekit_url``; reading it would move every existing
        # LiveKit hash, so it waits for a step that rehashes them.
        return Kind.LIVEKIT, _clean(
            {
                "agent_name": config.get("agent_name"),
                "server_url": _safe_url(config.get("url")),
            }
        )
    if kind == "remote":
        return Kind.PROVIDER, _clean(
            {"provider": "remote", "endpoint": _safe_url(source.get("endpoint"))}
        )
    return Kind.PROVIDER, _clean(
        {
            "provider": connector,
            "assistant_id": config.get("assistant_id") or config.get("agent_id"),
        }
    )


def source_access(payload: Mapping[str, Any]) -> dict[str, Any]:
    """How the source is reached: kept beside the identity, as a private clone or a
    remote target needs it. Holds no secret: the token comes from the installation."""
    source = payload.get("source") or {}
    return _clean(
        {
            "kind": str(source.get("kind") or ""),
            "visibility": source.get("visibility"),
            "installation_id": str(source.get("installation_id") or ""),
            "repository_id": source.get("repository_id"),
        }
    )


def legacy_agent_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The agent settings of a pre-versioning agent, secret-looking config redacted."""
    from simulate.services.hosted_harness_gateway import _secret_safe

    agent = payload.get("agent") or {}
    return _clean(
        {
            "connector": agent.get("connector"),
            "mode": agent.get("mode"),
            "call_direction": agent.get("call_direction"),
            "config": _secret_safe(agent.get("config") or {}),
            "source_access": source_access(payload),
        }
    )


def _legacy_refs(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Secret references only: which stored secret each name reads, never a value."""
    refs = ((payload.get("agent") or {}).get("secret_refs")) or {}
    return {
        str(name): {
            field_name: ref.get(field_name)
            for field_name in ("manager", "key", "version", "purpose")
            if ref.get(field_name) is not None
        }
        for name, ref in refs.items()
        if isinstance(ref, Mapping)
    }


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _run_test(models: Models, job: Any) -> Any:
    if not job.run_test_id:
        return None
    return _live(models.RunTest).filter(id=job.run_test_id).first()


def _definition(models: Models, job: Any) -> Any:
    wanted = _uuid(_metadata(job).get("agent_definition_id"))
    if wanted is not None:
        found = (
            _live(models.AgentDefinition)
            .filter(id=wanted, organization_id=job.organization_id)
            .first()
        )
        if found is not None:
            return found
    run_test = _run_test(models, job)
    if run_test is not None and run_test.agent_definition_id:
        return (
            _live(models.AgentDefinition)
            .filter(id=run_test.agent_definition_id)
            .first()
        )
    return None


def agent_candidate(models: Models, job: Any) -> tuple[Any, str]:
    """The agent version that becomes A1 and where it came from, or ``(None, "none")``.

    In order: the job's own pin, the run test's pin, the bound definition's and then the
    run test definition's active (else latest) version, else its newest one in the
    job's workspace whose side row is legacy or missing. A candidate other than the
    job's own pin is passed over when its side row holds a credential set.
    """
    if job.agent_version_id:
        # The job's own pin stands even if that version was later soft-deleted.
        pinned = models.AgentVersion._base_manager.filter(
            id=job.agent_version_id
        ).first()
        if pinned is not None:
            return pinned, "job_pin"
    run_test = _run_test(models, job)
    if run_test is not None and run_test.agent_version_id:
        pinned = models.AgentVersion._base_manager.filter(
            id=run_test.agent_version_id
        ).first()
        if pinned is not None and _pinnable(models, job, pinned):
            return pinned, "run_test_pin"
    definition = _definition(models, job)
    if definition is None:
        return None, "none"
    source = (
        "bound_definition"
        if str(_uuid(_metadata(job).get("agent_definition_id"))) == str(definition.id)
        else "run_test"
    )
    versions = _live(models.AgentVersion).filter(agent_definition_id=definition.id)
    version = (
        versions.filter(status=AgentVersion.StatusChoices.ACTIVE)
        .order_by("-version_number")
        .first()
        or versions.order_by("-version_number").first()
    )
    if version is not None and _pinnable(models, job, version):
        return version, source
    # The newest one without another environment's credential set, in this workspace.
    held = models.HarnessAgentVersionRuntime._base_manager.exclude(
        preflight_status=HarnessAgentVersionRuntime.PreflightStatus.LEGACY
    ).values("agent_version_id")
    version = (
        versions.filter(Q(workspace_id=job.workspace_id) | Q(workspace__isnull=True))
        .exclude(id__in=held)
        .order_by("-version_number")
        .first()
    )
    if version is None:
        return None, "none"
    return version, source


def _pinnable(models: Models, job: Any, version: Any) -> bool:
    """Only a version without a side row or with a legacy one: any other holds another
    environment's credential set."""
    status = (
        models.HarnessAgentVersionRuntime._base_manager.filter(
            agent_version_id=version.id
        )
        .values_list("preflight_status", flat=True)
        .first()
    )
    if status is None or status == HarnessAgentVersionRuntime.PreflightStatus.LEGACY:
        return True
    logger.info(
        "harness_agent_pin_skipped environment=%s agent_version=%s status=%s",
        job.id,
        version.id,
        status,
    )
    return False


def _legacy_facts(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A legacy side row's facts, as an environment's payload holds them."""
    source_kind, source = legacy_source(payload)
    refs = _legacy_refs(payload)
    return {
        "source_kind": source_kind,
        "source": source,
        "agent_payload": _clean(
            {**legacy_agent_payload(payload), "legacy_secret_refs": refs}
        ),
        "credential_names": sorted(refs),
        "credential_names_hash": hashing.credential_names_hash(refs),
    }


def _legacy_row(Runtime: Any, job: Any, version: Any) -> Any:
    runtime = Runtime(
        agent_version_id=version.id,
        agent_definition_id=version.agent_definition_id,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        prompt_hash="sha256:"
        + hashlib.sha256((version.description or "").encode("utf-8")).hexdigest(),
        preflight_status=HarnessAgentVersionRuntime.PreflightStatus.LEGACY,
        trigger=HarnessAgentVersionRuntime.Trigger.BACKFILL,
        **_legacy_facts(job.payload or {}),
    )
    # Legacy rows are outside the hash's unique constraint, so versions of one definition
    # that share every recorded fact share the hash too.
    runtime.hash = hashing.agent_hash(runtime_identity(runtime))
    return runtime


def side_row_plan(models: Models, job: Any, version: Any) -> tuple[Any, Any]:
    """The existing side row, or an unsaved new one."""
    Runtime = models.HarnessAgentVersionRuntime
    # The one-to-one ignores soft deletion, so a deleted side row is still the row.
    existing = Runtime._base_manager.filter(agent_version_id=version.id).first()
    if existing is not None:
        return existing, None
    return None, _legacy_row(Runtime, job, version)


def effective_runtime(environment: Any, runtime: Any, *, version: Any = None) -> Any:
    """The facts an agent version stands for in ``environment``'s runs; read only.

    A legacy side row is shared by every environment pinned to its version but records
    the first one's facts; it stands for ``environment``'s own, built from its payload
    as the backfill builds a row. So does a ``version`` without a side row. Any other
    row holds facts of its own. The result is never saved or linked to.
    """
    if runtime is None:
        if environment is None or version is None:
            return None
        return _legacy_row(HarnessAgentVersionRuntime, environment, version)
    if environment is None or not legacy_runtime(runtime):
        return runtime
    own = copy.copy(runtime)
    for name, value in _legacy_facts(environment.payload or {}).items():
        setattr(own, name, value)
    own.hash = hashing.agent_hash(runtime_identity(own))
    return own


def _side_row(models: Models, job: Any, version: Any) -> Any:
    existing, runtime = side_row_plan(models, job, version)
    if existing is not None:
        return existing
    runtime.save()
    return runtime


def _version_at(models: Models, run: Any, definition_id: Any) -> Any:
    found = (
        _live(models.AgentVersion)
        .filter(agent_definition_id=definition_id, created_at__lte=run.created_at)
        .order_by("-version_number")
        .first()
    )
    return found if found is not None and _pinnable(models, run, found) else None


# --- environment -----------------------------------------------------------------------


def unverified_revisions(version: Any) -> list[tuple[str, str]]:
    """``(label, archive key)`` pairs a job carried for bytes that hashed to other things."""
    pairs = []
    for one in (version.documents or {}).get(UNVERIFIED_REVISIONS) or []:
        if isinstance(one, (list, tuple)) and len(one) == 2 and all(one):
            pairs.append((str(one[0]), str(one[1])))
    return pairs


def _note_unverified(version: Any, revision: str, key: str) -> None:
    """Remember a label the job carried, with its archive key, for bytes that hashed to
    something else; it is only trusted again while the key is the same."""
    if (
        not revision
        or revision in recorded_revisions(version)
        or (revision, key) in unverified_revisions(version)
    ):
        return
    documents = dict(version.documents or {})
    documents[UNVERIFIED_REVISIONS] = [
        *[list(pair) for pair in unverified_revisions(version)],
        [revision, key],
    ]
    version.documents = documents
    version.save(update_fields=["documents", "updated_at"])


def _unchanged(models: Models, job: Any, revision: str) -> bool:
    """An already versioned environment whose archive is still its default's."""
    if job.environment_version_id is None:
        return False
    default = (
        _live(models.HarnessEnvironmentVersion)
        .filter(id=job.environment_version_id)
        .first()
    )
    if default is None:
        return False
    recorded = recorded_revisions(default)
    key = str(_metadata(job).get("authoring_object_key") or "").strip()
    # Without both revisions a change cannot be told apart from none. A label already
    # found not to match its bytes counts only while the archive key is the same.
    return (
        not revision
        or not recorded
        or revision in recorded
        or (revision, key) in unverified_revisions(default)
    )


def _land_versions(
    models: Models,
    locked: Any,
    archive: _Archive,
    *,
    key: str,
    revision: str,
    agent: Any,
    notes: list[str],
) -> tuple[Any, Any, bool, bool]:
    base_environment = (
        models.HarnessEnvironmentVersion._base_manager.filter(
            id=locked.environment_version_id
        ).first()
        if locked.environment_version_id
        else None
    )
    base_suite = (
        models.HarnessSuiteVersion._base_manager.filter(
            id=locked.suite_version_id
        ).first()
        if locked.suite_version_id
        else None
    )
    if archive.pieces is not None:
        pieces = archive.pieces
        environment_hash, suite_hash = pieces.environment_hash, pieces.suite_hash
        hash_kind = HashKind.TREE_V1
        environment_fields, suite_fields = (
            pieces.environment_fields,
            pieces.suite_fields,
        )
    else:
        value, hash_kind, environment_fields, suite_fields = _legacy_fields(
            locked, key, revision
        )
        environment_hash = suite_hash = value
    environment_diff = suite_diff = None
    if archive.pieces is not None and base_environment is not None:
        previous = base_environment.documents or {}
        environment_diff = diffs.environment_diff(
            previous_contract=previous.get("contract"),
            contract=archive.pieces.contract,
            previous_sub_goals=previous.get("sub_goal_digests") or {},
            sub_goals=archive.pieces.environment_documents["sub_goal_digests"],
            previous_files=previous.get("file_digests"),
            files=archive.pieces.environment_documents["file_digests"],
        )
    if archive.pieces is not None and base_suite is not None:
        suite_diff = diffs.suite_diff(
            (base_suite.documents or {}).get("scenarios") or [],
            archive.pieces.scenarios,
        )
    if base_environment is None:
        environment_summary = suite_summary = (
            "Recorded from the environment as it stood before versioning"
        )
    else:
        changed = "Recorded from an archive saved outside versioning"
        environment_summary = (
            diffs.summarize_environment(environment_diff)
            if environment_diff
            else changed
        )
        suite_summary = diffs.summarize_suite(suite_diff) if suite_diff else changed
    environment_version, environment_created = _land(
        models.HarnessEnvironmentVersion,
        locked,
        hash=environment_hash,
        hash_kind=hash_kind,
        values={
            **environment_fields,
            "origin": VersionOrigin.BACKFILL,
            "parent_id": base_environment.id if base_environment else None,
            "summary": environment_summary,
            "diff": environment_diff,
            "agent_version_id": agent.id if agent is not None else None,
        },
    )
    members = _members(models, locked, archive.scenarios, notes)
    # The suite is what the archive holds; without it, what the live rows say.
    count = len(archive.scenarios) if archive.scenarios is not None else len(members)
    suite_version, suite_created = _land(
        models.HarnessSuiteVersion,
        locked,
        hash=suite_hash,
        hash_kind=hash_kind,
        values={
            **suite_fields,
            "origin": VersionOrigin.BACKFILL,
            "parent_id": base_suite.id if base_suite else None,
            "summary": suite_summary,
            "diff": suite_diff,
            "scenario_count": min(count, 32767),
            "authored_against_id": environment_version.id,
        },
    )
    if not environment_created:
        note_revision(environment_version, revision)
    if not suite_created:
        note_revision(suite_version, revision)
    if suite_created:
        for member in members:
            member.suite_version_id = suite_version.id
        models.HarnessSuiteMember._base_manager.bulk_create(members)
    return environment_version, suite_version, environment_created, suite_created


def bundle_certificate(archive: _Archive, hash_kind: str) -> dict[str, Any] | None:
    """The archive's certificate when it can seed a bundle row, else ``None``."""
    sealed = archive.sealed or {}
    if (
        archive.pieces is None
        or SEALED_MANIFEST not in sealed
        or CERTIFICATE not in sealed
        or hash_kind != HashKind.TREE_V1
    ):
        return None
    try:
        certificate = json.loads(sealed[CERTIFICATE])
    except ValueError:
        return None
    return certificate if isinstance(certificate, dict) else None


@dataclass(frozen=True)
class _SealedCopy:
    object_key: str
    sha256: str
    size: int


def _store_sealed(job: Any, archive: _Archive) -> _SealedCopy | None:
    """Store the archive's sealed bundle, content-addressed, before any row lock."""
    if bundle_certificate(archive, HashKind.TREE_V1) is None:
        return None
    body = archives.pack_archive(archive.sealed or {})
    object_key = archives.sealed_key(job.organization_id, job.id, body)
    archives.put_archive(object_key, body)
    return _SealedCopy(object_key, archives.sha256_hex(body), len(body))


def _bundle(
    models: Models,
    locked: Any,
    archive: _Archive,
    sealed: _SealedCopy | None,
    runtime: Any,
    environment_version: Any,
    suite_version: Any,
) -> Any:
    """The bundle the environment's archive already carries, as the first cache row."""
    if bundle_certificate(archive, environment_version.hash_kind) is None or not sealed:
        return None
    return seed_ready_bundle(
        models.HarnessAssembledBundle,
        job=locked,
        runtime=runtime,
        environment_version=environment_version,
        suite_version=suite_version,
        sealed=archive.sealed or {},
        object_key=sealed.object_key,
        sha256=sealed.sha256,
        size=sealed.size,
        bundle_digest=locked.bundle_digest,
    )


def _by_revision(model: Any, job: Any) -> dict[str, Any]:
    """Each recorded authoring revision and the newest active version recorded from it."""
    found: dict[str, Any] = {}
    for version in (
        _live(model)
        .filter(job_id=job.id, status=VersionStatus.ACTIVE)
        .order_by("version_number")
    ):
        for revision in recorded_revisions(version):
            found[revision] = version
    return found


@dataclass
class RunPlan:
    """What the backfill does to one run."""

    environment_version: Any = None
    suite_version: Any = None
    legacy_revision: str = ""
    agent_version_id: Any = None
    execution: Any = None
    fill_execution: bool = False
    revision: str = ""
    settled: bool = True
    # Pinned by an earlier pass; only an empty agent pin is left to fill.
    pinned: bool = False

    @property
    def matched(self) -> bool:
        return self.environment_version is not None and self.suite_version is not None


def run_plan(
    models: Models,
    run: Any,
    environments: Mapping[str, Any],
    suites: Mapping[str, Any],
    definition_id: Any,
    fill_agent_id: Any = None,
) -> RunPlan:
    """Pins for one unpinned run, from the versions recorded from each revision.

    ``fill_agent_id`` is the agent an environment version without one would get first;
    the check passes it, since the run has already written it by then.
    """
    metadata = _metadata(run)
    revision = str(metadata.get("authoring_revision") or "")
    plan = RunPlan(
        revision=revision,
        settled=_settled(run),
        pinned=run.environment_version_id is not None,
    )
    if not plan.pinned:
        plan.environment_version = environments.get(revision)
        plan.suite_version = suites.get(revision)
    if not plan.pinned and not plan.matched:
        plan.environment_version = plan.suite_version = None
        pins = metadata.get("version_pins") or {}
        if revision and plan.settled and pins.get("legacy_revision") != revision:
            plan.legacy_revision = revision
    plan.execution = (
        _live(models.TestExecution).filter(id=run.test_execution_id).first()
        if run.test_execution_id
        else None
    )
    agent_id = run.agent_version_id
    if agent_id is None:
        # What the run itself recorded, else what was current when it started (by the
        # run's own definition when it has one), else what its versions derive from.
        execution_definition = (
            plan.execution.agent_definition_id if plan.execution is not None else None
        )
        pinned_environment = plan.environment_version
        if plan.pinned:
            pinned_environment = (
                _live(models.HarnessEnvironmentVersion)
                .filter(id=run.environment_version_id)
                .first()
            )
        if plan.execution is not None and plan.execution.agent_version_id:
            agent_id = plan.execution.agent_version_id
        elif (execution_definition or definition_id) is not None and (
            found := _version_at(models, run, execution_definition or definition_id)
        ):
            agent_id = found.id
        elif pinned_environment is not None:
            agent_id = pinned_environment.agent_version_id or fill_agent_id
        plan.agent_version_id = agent_id
    plan.fill_execution = (
        plan.execution is not None
        and plan.execution.agent_version_id is None
        and agent_id is not None
    )
    return plan


def _unpinned_runs(models: Models, job: Any):
    """Runs missing a version pin or an agent pin."""
    return (
        _live(models.HostedHarnessJob)
        .filter(environment_id=job.id)
        .filter(Q(environment_version__isnull=True) | Q(agent_version__isnull=True))
        .order_by("created_at", "id")
    )


def _definition_id(models: Models, job: Any, runtime: Any) -> Any:
    if runtime is not None:
        return runtime.agent_definition_id
    definition = _definition(models, job)
    return definition.id if definition else None


def _pin_runs(
    models: Models, locked: Any, runtime: Any, result: BackfillResult
) -> None:
    """Pin each unpinned run to the versions recorded from the archive it replayed."""
    environments = _by_revision(models.HarnessEnvironmentVersion, locked)
    suites = _by_revision(models.HarnessSuiteVersion, locked)
    definition_id = _definition_id(models, locked, runtime)
    for run in _unpinned_runs(models, locked).select_for_update():
        plan = run_plan(models, run, environments, suites, definition_id)
        update_fields: list[str] = []
        if plan.matched:
            run.environment_version_id = plan.environment_version.id
            run.suite_version_id = plan.suite_version.id
            update_fields += ["environment_version", "suite_version"]
            result.runs_pinned += 1
        elif plan.legacy_revision:
            payload = dict(run.payload or {})
            metadata = dict(payload.get("metadata") or {})
            pins = dict(metadata.get("version_pins") or {})
            pins["legacy_revision"] = plan.legacy_revision
            metadata["version_pins"] = pins
            payload["metadata"] = metadata
            run.payload = payload
            update_fields.append("payload")
            result.runs_unmatched += 1
        if plan.agent_version_id is not None:
            run.agent_version_id = plan.agent_version_id
            update_fields.append("agent_version")
        if update_fields:
            run.save(update_fields=[*update_fields, "updated_at"])
        if plan.fill_execution:
            # Fills only an empty pin, whatever was written since it was read.
            _live(models.TestExecution).filter(
                id=plan.execution.id, agent_version__isnull=True
            ).update(agent_version_id=run.agent_version_id)


def _pin_agent(locked: Any, agent: Any, result: BackfillResult) -> list[str]:
    if agent is None or locked.agent_version_id is not None:
        return []
    locked.agent_version_id = agent.id
    result.agent_pinned = True
    return ["agent_version"]


def _backfill_once(models: Models, job: Any, result: BackfillResult) -> bool:
    """One locked pass; ``False`` when the environment was saved while it was read."""
    metadata = _metadata(job)
    key = str(metadata.get("authoring_object_key") or "").strip()
    revision = str(metadata.get("authoring_revision") or "")
    record = not _unchanged(models, job, revision)
    archive = _read(job, key) if record else _Archive()
    unreadable = record and archive.error_code in UNREADABLE_CODES
    sealed = None
    if record and archive.pieces is not None:
        archives.put_archive(
            archive.pieces.environment_key, archive.pieces.environment_body
        )
        archives.put_archive(archive.pieces.suite_key, archive.pieces.suite_body)
        if agent_candidate(models, job)[0] is not None:
            sealed = _store_sealed(job, archive)

    with transaction.atomic():
        locked = (
            _live(models.HostedHarnessJob).select_for_update().filter(id=job.id).first()
        )
        if locked is None:
            result.outcome = "not_found"
            return True
        locked_metadata = _metadata(locked)
        if (
            str(locked_metadata.get("authoring_object_key") or "").strip() != key
            or str(locked_metadata.get("authoring_revision") or "") != revision
        ):
            return False
        if archive.error:
            result.notes.append(archive.error)
        agent, result.agent_source = agent_candidate(models, locked)
        runtime = _side_row(models, locked, agent) if agent is not None else None
        if agent is None:
            result.notes.append("no agent version to pin")
        if unreadable:
            # Versions over these bytes would refuse every later save; pin the agent only.
            result.outcome = "skipped_unreadable"
            update_fields = _pin_agent(locked, agent, result)
            if update_fields:
                locked.save(update_fields=[*update_fields, "updated_at"])
            return True
        update_fields: list[str] = []
        if record:
            first = locked.environment_version_id is None
            defaults = (locked.environment_version_id, locked.suite_version_id)
            recorded = archive.revision or revision
            (
                environment_version,
                suite_version,
                environment_created,
                suite_created,
            ) = _land_versions(
                models,
                locked,
                archive,
                key=key,
                revision=recorded,
                agent=agent,
                notes=result.notes,
            )
            if revision and archive.revision and revision != archive.revision:
                # Runs labelled with the stale revision may have replayed other bytes.
                for version in (environment_version, suite_version):
                    _note_unverified(version, revision, key)
                result.notes.append("revision label does not match the archive")
            if first:
                result.outcome = "versioned"
            elif environment_created or suite_created:
                result.outcome = "appended"
            elif defaults != (environment_version.id, suite_version.id):
                result.outcome = "default_moved"
            else:
                result.outcome = "revision_noted"
            result.layout = environment_version.archive_layout
            locked.environment_version_id = environment_version.id
            locked.suite_version_id = suite_version.id
            update_fields += ["environment_version", "suite_version"]
            count = suite_version.scenario_count
            # Only an archive says how many scenarios the suite has; an extend in flight
            # is still raising the count, and its freeze records S2.
            if (
                suite_created
                and archive.pieces is not None
                and _settled(locked)
                and 1 <= count <= MAX_SCENARIOS_PER_JOB
                and count != locked.scenario_count
            ):
                payload = dict(locked.payload or {})
                payload["scenario_count"] = count
                locked.payload = payload
                locked.scenario_count = count
                update_fields += ["payload", "scenario_count"]
            if key and _settled(locked):
                # A roll back to the pinned pair replays the archive runs replayed; a
                # busy environment's payload is its build's, so an edit remembers it.
                payload = dict(locked.payload or {})
                metadata = _metadata(locked)
                _remember_pair(
                    metadata,
                    environment_version,
                    suite_version,
                    _outgoing_pair(metadata),
                    # The agent this save pins: runs replayed the archive with it.
                    agent_version_id=locked.agent_version_id
                    or getattr(agent, "id", None),
                    replace=False,
                )
                payload["metadata"] = metadata
                locked.payload = payload
                update_fields.append("payload")
            if runtime is not None:
                bundle = _bundle(
                    models,
                    locked,
                    archive,
                    sealed,
                    runtime,
                    environment_version,
                    suite_version,
                )
                if bundle is not None:
                    result.bundle_recorded = True
                    if locked.assembled_bundle_id is None:
                        locked.assembled_bundle_id = bundle.id
                        update_fields.append("assembled_bundle")
        else:
            result.outcome = "current"
        update_fields += _pin_agent(locked, agent, result)
        if agent is not None:
            # Versions recorded before the agent was known derive from it too.
            _live(models.HarnessEnvironmentVersion).filter(
                job_id=locked.id,
                status=VersionStatus.ACTIVE,
                agent_version__isnull=True,
            ).update(agent_version_id=agent.id, updated_at=timezone.now())
        if update_fields:
            locked.save(update_fields=[*dict.fromkeys(update_fields), "updated_at"])
        _pin_runs(models, locked, runtime, result)
    return True


def backfill_environment(
    job_id: Any, *, models: Models | None = None
) -> BackfillResult:
    """Version one environment head and pin its agent and runs."""
    models = models or default_models()
    result = BackfillResult(job_id=str(job_id), outcome="not_found")
    for _ in range(_SAVE_ATTEMPTS):
        job = _live(models.HostedHarnessJob).filter(id=job_id).first()
        if job is None:
            return result
        if job.environment_id is not None:
            result.outcome = "not_environment"
            return result
        if not str(_metadata(job).get("authoring_object_key") or "").strip():
            result.outcome = "no_archive"
            return result
        if _backfill_once(models, job, result):
            return result
    # Saved on every pass; a later run of the command picks it up.
    result.outcome = "changed_during_backfill"
    return result


def environments_to_backfill(models: Models) -> list[Any]:
    """Environment heads with an authoring archive key, oldest first."""
    rows = (
        _live(models.HostedHarnessJob)
        .filter(
            environment__isnull=True,
            payload__metadata__authoring_object_key__isnull=False,
        )
        .order_by("created_at", "id")
        .values_list("id", "payload__metadata__authoring_object_key")
    )
    # A JSON null, empty or blank key is no archive at all.
    return [job_id for job_id, key in rows if isinstance(key, str) and key.strip()]


def run_backfill(
    job_ids: Iterable[Any] | None = None, *, models: Models | None = None
) -> list[BackfillResult]:
    """Backfill every environment head, or only these ids; one failing never stops the rest.

    Ids given explicitly are each reported, including ones the backfill cannot use.
    """
    models = models or default_models()
    ids = list(job_ids) if job_ids is not None else environments_to_backfill(models)
    results = []
    for job_id in ids:
        try:
            result = backfill_environment(job_id, models=models)
        except Exception as exc:  # noqa: BLE001 - one environment never stops the rest
            logger.exception("harness versions backfill failed job=%s", job_id)
            result = BackfillResult(
                job_id=str(job_id),
                outcome="error",
                notes=[f"{type(exc).__name__}: {exc}"],
            )
        if result.notes or result.outcome in FAILED_OUTCOMES:
            logger.info(
                "harness versions backfill job=%s outcome=%s notes=%s",
                job_id,
                result.outcome,
                result.notes,
            )
        results.append(result)
    return results


def backfill_all(
    models: Models | None = None, job_ids: Iterable[Any] | None = None
) -> dict[str, int]:
    """How many environments ended in each outcome."""
    outcomes: dict[str, int] = {}
    for result in run_backfill(job_ids, models=models):
        outcomes[result.outcome] = outcomes.get(result.outcome, 0) + 1
    logger.info("harness versions backfill done: %s", outcomes)
    return outcomes
