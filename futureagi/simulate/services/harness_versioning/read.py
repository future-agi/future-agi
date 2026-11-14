"""Read-only views of an environment's versions: the lists, a diff and members."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from django.conf import settings
from django.db.models import (
    BooleanField,
    CharField,
    ExpressionWrapper,
    F,
    Func,
    Q,
    QuerySet,
)
from django.db.models.fields.json import KeyTextTransform

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessJob,
    RunTest,
)
from simulate.services.harness_versioning import diffs
from simulate.services.harness_versioning.versions import versioning_enabled
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

PIECES = ("agent", "environment", "suite")

# A pre-versioning agent version has no side row; it ran, so it reads as active. A
# soft-deleted side row reads the same as no side row.
_AGENT_STATUS = {
    HarnessAgentVersionRuntime.PreflightStatus.PENDING: "draft",
    HarnessAgentVersionRuntime.PreflightStatus.FAILED: "draft",
    HarnessAgentVersionRuntime.PreflightStatus.PASSED: "active",
    HarnessAgentVersionRuntime.PreflightStatus.LEGACY: "active",
    HarnessAgentVersionRuntime.PreflightStatus.DISCARDED: "discarded",
}

# Large columns: the lists never read them; each read loads only what it uses.
_PIECE_DEFERRED = ("documents", "diff", "snapshot_diff")
_AGENT_DEFERRED = ("configuration_snapshot", "description", "release_notes")
# Never read by the lists; the preflight report holds names and statuses only.
_RUNTIME_SECRET = ("agent_payload", "credential_blob")
# The source facts an agent version item shows, by source kind: never a URL, an
# installation or a credential.
_SOURCE_FACTS = {
    "github": ("repository", "ref", "commit_sha"),
    "phone": ("phone_number", "call_direction"),
    "provider": ("provider", "assistant_id"),
    "livekit": ("agent_name",),
}
_PROMPT_KINDS = ("provider", "phone", "livekit")


def versioning_flags() -> dict[str, bool]:
    enabled = versioning_enabled()
    nudges = bool(getattr(settings, "HARNESS_VERSIONING_NUDGES_ENABLED", False))
    return {"enabled": enabled, "nudges_enabled": enabled and nudges}


def require_versioning() -> None:
    if not versioning_enabled():
        raise HostedHarnessError(
            "versioning_disabled",
            "environment versioning is not enabled",
            status_code=404,
        )


def version_not_found() -> HostedHarnessError:
    return HostedHarnessError(
        "version_not_found", "no such version in this environment", status_code=404
    )


def _malformed(version_id: Any, field: str) -> None:
    """A stored value of the wrong shape is a writer bug: read on, but leave a trace."""
    logger.warning(
        "harness version document malformed version=%s field=%s", version_id, field
    )


def _mapping(value: Any, version_id: Any = None, field: str = "") -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is not None and version_id is not None:
        _malformed(version_id, field)
    return {}


def _person(user: Any) -> dict[str, str] | None:
    if user is None:
        return None
    return {"id": str(user.id), "name": str(getattr(user, "name", "") or "")}


def _label(version_number: int | None) -> str | None:
    return f"v{version_number}" if version_number is not None else None


def _agent_label(name: str | None, version_number: int | None) -> str | None:
    return name or _label(version_number)


def _message_label(label: str | None) -> str:
    """A version's label as a message names it; never ``None``."""
    return label or "an unnumbered version"


def version_refs(
    agent_version: Any, environment_version: Any, suite_version: Any
) -> dict[str, dict[str, Any] | None]:
    """Each pinned version as ``{id, label}``, or ``None`` when that piece has none."""
    return {
        "agent": (
            {
                "id": str(agent_version.id),
                "label": _agent_label(
                    agent_version.version_name, agent_version.version_number
                ),
            }
            if agent_version is not None
            else None
        ),
        "environment": (
            {
                "id": str(environment_version.id),
                "label": _label(environment_version.version_number),
            }
            if environment_version is not None
            else None
        ),
        "suite": (
            {"id": str(suite_version.id), "label": _label(suite_version.version_number)}
            if suite_version is not None
            else None
        ),
    }


def _summary_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return (lines[0] if lines else "")[:1024]


def _newest_first(queryset: QuerySet) -> QuerySet:
    return queryset.order_by(
        F("version_number").desc(nulls_first=True), "-created_at", "-id"
    )


def _piece_item(version: Any, default_id: Any, *, suite: bool) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "label": _label(version.version_number),
        "status": version.status,
        "is_default": version.id == default_id,
        "created_at": version.created_at.isoformat(),
        "created_by": _person(version.created_by),
        "origin": version.origin or None,
        "summary": version.summary or "",
        "hash": version.hash or None,
        "parent_id": str(version.parent_id) if version.parent_id else None,
        "derived_from_agent_version_id": (
            None
            if suite or not version.agent_version_id
            else str(version.agent_version_id)
        ),
        "scenario_count": version.scenario_count if suite else None,
        "source": None,
        "prompt": None,
        "preflight_status": None,
        "preflight_report": None,
        "preflighted_at": None,
        "check_started_at": None,
        "credential_names": None,
        "required_variable_names": None,
        "activation_outcome": None,
        "requested_here": None,
        "pickable": None,
        "discardable": None,
    }


def _agent_definition_id(job: HostedHarnessJob) -> Any:
    if job.agent_version_id:
        definition_id = (
            AgentVersion._base_manager.filter(id=job.agent_version_id)
            .values_list("agent_definition_id", flat=True)
            .first()
        )
        if definition_id:
            return definition_id
    if job.run_test_id:
        return (
            RunTest._base_manager.filter(id=job.run_test_id)
            .values_list("agent_definition_id", flat=True)
            .first()
        )
    return None


def in_workspace(job: HostedHarnessJob) -> Q:
    """Agent versions of the environment's workspace, or of none."""
    return Q(workspace_id=job.workspace_id) | Q(workspace__isnull=True)


def _agent_versions(
    job: HostedHarnessJob, ids: list[Any] | None = None
) -> list[AgentVersion]:
    """The agent versions of the environment's agent definition, newest first.

    Scoped by the environment's organisation and workspace as well as its definition,
    so a stray pin never lists another workspace's versions. The environment's pinned
    version is always listed, even soft-deleted, so a pin always has its default row.
    """
    definition_id = _agent_definition_id(job)
    if definition_id is None:
        return []
    scope = {
        "agent_definition_id": definition_id,
        "organization_id": job.organization_id,
    }
    versions = AgentVersion.no_workspace_objects.filter(
        in_workspace(job), **scope
    ).defer(*_AGENT_DEFERRED)
    if ids is not None:
        versions = versions.filter(id__in=ids)
    found = list(versions)
    from simulate.services.harness_agent_versions import lineage_version_ids

    # An agent environments share: only this environment's own versions.
    allowed = lineage_version_ids(job, definition_id)
    if allowed is not None:
        found = [version for version in found if str(version.id) in allowed]
    pinned = job.agent_version_id
    if pinned and pinned not in {version.id for version in found}:
        if ids is None or str(pinned) in {str(one) for one in ids}:
            found.extend(
                AgentVersion._base_manager.filter(id=pinned, **scope).defer(
                    *_AGENT_DEFERRED
                )
            )
    found.sort(key=lambda version: version.created_at, reverse=True)
    found.sort(key=lambda version: version.version_number or 0, reverse=True)
    return found


def _runtimes(
    job: HostedHarnessJob, versions: Iterable[AgentVersion], *, with_diff: bool = False
) -> dict[Any, HarnessAgentVersionRuntime]:
    deferred = _RUNTIME_SECRET if with_diff else (*_RUNTIME_SECRET, "diff")
    runtimes = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            organization_id=job.organization_id,
            agent_version_id__in=[version.id for version in versions],
        )
        .select_related("created_by")
        .defer(*deferred)
        .annotate(
            asked_by=F("agent_payload__requested_by_environments"),
            asked_for=F("agent_payload__created_for_environment"),
            access=F("agent_payload__source_access"),
            # Whether a discarded row was a legacy one, without reading its blob.
            blobless=ExpressionWrapper(
                Q(credential_blob__isnull=True) | Q(credential_blob=""),
                output_field=BooleanField(),
            ),
        )
    )
    return {runtime.agent_version_id: runtime for runtime in runtimes}


def _parent_agent_versions(
    job: HostedHarnessJob, runtimes: Mapping[Any, HarnessAgentVersionRuntime]
) -> dict[Any, Any]:
    """Each side row's live parent side row, as the agent version it belongs to."""
    parent_ids = {
        runtime.parent_id for runtime in runtimes.values() if runtime.parent_id
    }
    if not parent_ids:
        return {}
    return dict(
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            organization_id=job.organization_id, id__in=parent_ids
        ).values_list("id", "agent_version_id")
    )


def _activation_entry(
    report: Mapping[str, Any] | None, environment_id: Any
) -> Mapping[str, Any] | None:
    """This environment's last recorded activation entry."""
    for entry in (report or {}).get("activation") or []:
        if isinstance(entry, Mapping) and entry.get("environment_id") == str(
            environment_id
        ):
            return entry
    return None


def _activation_outcome(
    report: Mapping[str, Any] | None, environment_id: Any
) -> str | None:
    """Why a passed version is or isn't this environment's default, as last recorded."""
    entry = _activation_entry(report, environment_id)
    return (str(entry.get("outcome") or "") or None) if entry is not None else None


def _source_item(runtime: Any, job: HostedHarnessJob, legacy: bool) -> Any:
    """An agent version's non-secret source facts."""
    from simulate.services.harness_versioning.backfill import source_access

    kind = str(getattr(runtime, "source_kind", "") or "")
    if not kind:
        return None
    stored = runtime.source if isinstance(runtime.source, Mapping) else {}
    if kind == "provider" and stored.get("provider") == "remote":
        return {"kind": kind, "provider": "remote"}
    item = {"kind": kind}
    item.update(
        (name, stored[name])
        for name in _SOURCE_FACTS.get(kind, ())
        if stored.get(name) is not None
    )
    if kind == "github":
        # A legacy row is shared: this environment's own access.
        access = (
            source_access(job.payload or {})
            if legacy
            else getattr(runtime, "access", None)
        )
        item["visibility"] = (
            access.get("visibility") if isinstance(access, Mapping) else None
        )
    return item


def _default_prompt(
    version: AgentVersion, runtime: Any, facts: Any, source: Any
) -> str | None:
    """The default agent version's prompt, as its runs read it; a code agent's
    belongs to its source."""
    if not source or source["kind"] not in _PROMPT_KINDS:
        return None
    if source.get("provider") == "remote":
        return None
    if facts is runtime:
        prompt = (
            HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id)
            .values_list("agent_payload__config__target_system_prompt", flat=True)
            .first()
        )
    else:
        config = (getattr(facts, "agent_payload", None) or {}).get("config") or {}
        prompt = config.get("target_system_prompt")
    return _prompt_or_description(version, prompt) or None


def _prompt_or_description(version: AgentVersion, prompt: Any) -> str | None:
    """A configured prompt; else the agent version's description."""
    if isinstance(prompt, str):
        return prompt
    return (
        AgentVersion._base_manager.filter(id=version.id)
        .values_list("description", flat=True)
        .first()
    )


def _agent_item(
    version: AgentVersion,
    runtime: HarnessAgentVersionRuntime | None,
    default_id: Any,
    parents: Mapping[Any, Any],
    job: HostedHarnessJob,
    lineage: set[Any],
) -> dict[str, Any]:
    from simulate.services.harness_agent_versions import (
        _held_elsewhere,
        agent_pick_allowed,
        requested_here,
        version_credential_names,
    )
    from simulate.services.harness_versioning.assembly import legacy_runtime
    from simulate.services.harness_versioning.backfill import effective_runtime

    environment_id = job.id
    Status = HarnessAgentVersionRuntime.PreflightStatus
    legacy = runtime is not None and legacy_runtime(runtime)
    # A legacy row is shared: this environment's own variables are its names.
    named = effective_runtime(job, runtime) if legacy else runtime
    is_default = version.id == default_id
    # A default without a side row stands for the environment's own facts.
    facts = (
        effective_runtime(job, None, version=version)
        if is_default and runtime is None
        else named
    )
    source = _source_item(facts, job, legacy or runtime is None)

    if runtime is not None and (runtime.summary or "").strip():
        summary = _summary_line(runtime.summary)
    else:
        summary = _summary_line(version.commit_message)
    parent = (
        parents.get(runtime.parent_id)
        if runtime is not None and runtime.parent_id
        else None
    )
    report = (
        runtime.preflight_report
        if runtime is not None and isinstance(runtime.preflight_report, Mapping)
        else None
    )
    # Annotated by ``_runtimes``.
    asked_here = runtime is not None and requested_here(
        getattr(runtime, "asked_by", None),
        getattr(runtime, "asked_for", None),
        environment_id,
    )
    return {
        "id": str(version.id),
        "label": _agent_label(version.version_name, version.version_number),
        "status": (
            _AGENT_STATUS.get(runtime.preflight_status, "active")
            if runtime is not None
            else "active"
        ),
        "is_default": version.id == default_id,
        # As a run's pick or a move takes it: the default, a version whose check
        # passed, or a legacy or pre-versioning one of the default's lineage.
        "pickable": version.id == default_id
        or agent_pick_allowed(job, version.id, runtime, lineage),
        "created_at": version.created_at.isoformat(),
        "created_by": _person(runtime.created_by) if runtime is not None else None,
        "origin": (runtime.trigger or None) if runtime is not None else None,
        "summary": summary,
        "hash": (runtime.hash or None) if runtime is not None else None,
        "parent_id": str(parent) if parent is not None else None,
        "derived_from_agent_version_id": None,
        "scenario_count": None,
        "source": source,
        # The default's only, so a polled list never repeats it.
        "prompt": (
            _default_prompt(version, runtime, facts, source) if is_default else None
        ),
        "preflight_status": (
            (runtime.preflight_status or None) if runtime is not None else None
        ),
        "preflight_report": report,
        "preflighted_at": (
            runtime.preflighted_at.isoformat()
            if runtime is not None and runtime.preflighted_at
            else None
        ),
        "check_started_at": (
            runtime.check_started_at.isoformat()
            if runtime is not None and runtime.check_started_at
            else None
        ),
        "credential_names": (
            version_credential_names(named) if named is not None else None
        ),
        "required_variable_names": (
            [str(name) for name in runtime.required_variable_names]
            if runtime is not None and isinstance(runtime.required_variable_names, list)
            else None
        ),
        "activation_outcome": _activation_outcome(report, environment_id),
        "requested_here": asked_here,
        # As a discard from here takes it: never one another environment holds.
        "discardable": runtime is not None
        and not legacy
        and runtime.preflight_status != Status.DISCARDED
        and version.id != default_id
        and (asked_here or version.id in lineage)
        and not _held_elsewhere(
            version, getattr(runtime, "asked_for", None), environment_id
        ),
    }


def agent_version_item(job: HostedHarnessJob, version: AgentVersion) -> dict[str, Any]:
    """One agent version of ``job``'s agent, as the version lists show it."""
    from simulate.services.harness_agent_versions import _default_lineage

    runtimes = _runtimes(job, [version])
    return _agent_item(
        version,
        runtimes.get(version.id),
        job.agent_version_id,
        _parent_agent_versions(job, runtimes),
        job,
        _default_lineage(job),
    )


def list_versions(job: HostedHarnessJob) -> dict[str, list[dict[str, Any]]]:
    environments = _newest_first(
        HarnessEnvironmentVersion.no_workspace_objects.filter(job=job)
        .select_related("created_by")
        .defer(*_PIECE_DEFERRED)
    )
    suites = _newest_first(
        HarnessSuiteVersion.no_workspace_objects.filter(job=job)
        .select_related("created_by")
        .defer(*_PIECE_DEFERRED)
    )
    from simulate.services.harness_agent_versions import _default_lineage

    agents = _agent_versions(job)
    runtimes = _runtimes(job, agents)
    parents = _parent_agent_versions(job, runtimes)
    lineage = _default_lineage(job)
    return {
        "agent": [
            _agent_item(
                version,
                runtimes.get(version.id),
                job.agent_version_id,
                parents,
                job,
                lineage,
            )
            for version in agents
        ],
        "environment": [
            _piece_item(version, job.environment_version_id, suite=False)
            for version in environments
        ],
        "suite": [
            _piece_item(version, job.suite_version_id, suite=True) for version in suites
        ],
    }


def _piece_version(
    job: HostedHarnessJob, piece: str, version_id: Any, *, load: tuple[str, ...] = ()
) -> Any:
    """One environment or suite version of this environment, with only ``load`` read."""
    model = HarnessEnvironmentVersion if piece == "environment" else HarnessSuiteVersion
    deferred = [field for field in _PIECE_DEFERRED if field not in load]
    version = (
        model.no_workspace_objects.filter(job=job, id=version_id)
        .defer(*deferred)
        .first()
    )
    if version is None:
        raise version_not_found()
    return version


def _contract(documents: Mapping[str, Any], version_id: Any) -> dict[str, Any]:
    contract = dict(_mapping(documents.get("contract"), version_id, "contract"))
    if "tools" in contract and not isinstance(contract["tools"], list):
        _malformed(version_id, "contract.tools")
    if not isinstance(contract.get("tools"), list):
        contract.pop("tools", None)
    return contract


def _digests(
    value: Any, version_id: Any = None, field: str = ""
) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        if value is not None and version_id is not None:
            _malformed(version_id, field)
        return None
    return {str(key): str(digest) for key, digest in value.items()}


def _environment_diff(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    previous_id: Any,
    current_id: Any,
) -> dict[str, Any]:
    previous_files = _digests(previous.get("file_digests"), previous_id, "file_digests")
    files = _digests(current.get("file_digests"), current_id, "file_digests")
    known = previous_files is not None and files is not None
    previous_goals = previous.get("sub_goal_digests")
    goals = current.get("sub_goal_digests")
    return diffs.environment_diff(
        previous_contract=_contract(previous, previous_id),
        contract=_contract(current, current_id),
        previous_sub_goals=_digests(previous_goals, previous_id, "sub_goal_digests")
        or {},
        sub_goals=_digests(goals, current_id, "sub_goal_digests") or {},
        previous_files=previous_files if known else None,
        files=files or {},
    )


def _scenarios(
    documents: Mapping[str, Any], version_id: Any
) -> list[Mapping[str, Any]]:
    scenarios = documents.get("scenarios")
    if not isinstance(scenarios, list):
        if scenarios is not None:
            _malformed(version_id, "scenarios")
        return []
    kept = [one for one in scenarios if isinstance(one, Mapping)]
    if len(kept) != len(scenarios):
        _malformed(version_id, "scenarios[]")
    return kept


def _agent_facts(
    runtime: HarnessAgentVersionRuntime, *, names_only: bool
) -> dict[str, Any]:
    version_id = runtime.agent_version_id
    fingerprints = (
        _digests(runtime.credential_fingerprints, version_id, "credential_fingerprints")
        or {}
    )
    names = runtime.credential_names
    names = [str(name) for name in names] if isinstance(names, list) else []
    # Provider keys are fingerprinted only, while a legacy row names them.
    names = sorted({*names, *fingerprints})
    source = _mapping(runtime.source, version_id, "source")
    return {
        "source": {"kind": runtime.source_kind, **source},
        "prompt_hash": runtime.prompt_hash,
        # A legacy row has names but no fingerprints, so a pair with one compares names
        # only: added and removed variables, never a value or a "changed".
        "credential_fingerprints": (
            dict.fromkeys(names, "") if names_only else fingerprints
        ),
    }


def _stored_diff(value: Any, version_id: Any) -> Mapping[str, Any] | None:
    if value is not None and not isinstance(value, Mapping):
        _malformed(version_id, "diff")
        return None
    return value


def _agent_diff(
    job: HostedHarnessJob, version_id: Any, against_id: Any | None
) -> dict[str, Any]:
    from simulate.services.harness_versioning.backfill import effective_runtime

    wanted = [version_id] if against_id is None else [version_id, against_id]
    versions = {str(version.id): version for version in _agent_versions(job, wanted)}
    version = versions.get(str(version_id))
    if version is None or (against_id is not None and str(against_id) not in versions):
        raise version_not_found()
    runtimes = _runtimes(job, versions.values(), with_diff=against_id is None)
    runtime = runtimes.get(version.id)
    if against_id is None:
        if runtime is None:
            return {"summary": "", "diff": None}
        diff = _stored_diff(runtime.diff, version.id)
        return {"summary": runtime.summary or "", "diff": diff}
    other = runtimes.get(versions[str(against_id)].id)
    if runtime is None or other is None:
        return {"summary": "", "diff": None}
    # A legacy row is shared: it stands for this environment's own facts.
    runtime = effective_runtime(job, runtime)
    other = effective_runtime(job, other)
    names_only = not (
        _digests(runtime.credential_fingerprints)
        and _digests(other.credential_fingerprints)
    )
    return {
        "summary": "",
        "diff": diffs.agent_diff(
            _agent_facts(other, names_only=names_only),
            _agent_facts(runtime, names_only=names_only),
        ),
    }


def version_diff(
    job: HostedHarnessJob, piece: str, version_id: Any, against_id: Any | None = None
) -> dict[str, Any]:
    """A version against its parent, or against another version of the same piece."""
    if piece not in PIECES:
        raise version_not_found()
    if piece == "agent":
        return _agent_diff(job, version_id, against_id)
    if against_id is None:
        version = _piece_version(job, piece, version_id, load=("diff", "snapshot_diff"))
        diff = _stored_diff(version.diff, version.id)
        return {"summary": version.summary or "", "diff": diff, **_draft_edits(version)}
    version = _piece_version(
        job, piece, version_id, load=("documents", "snapshot_diff")
    )
    other = _piece_version(job, piece, against_id, load=("documents",))
    previous = _mapping(other.documents, other.id, "documents")
    current = _mapping(version.documents, version.id, "documents")
    if piece == "environment":
        diff = _environment_diff(previous, current, other.id, version.id)
        return {
            "summary": diffs.summarize_environment(diff),
            "diff": diff,
            **_draft_edits(version),
        }
    diff = diffs.suite_diff(
        _scenarios(previous, other.id),
        _scenarios(current, version.id),
        removed_reason="not in this version",
    )
    return {
        "summary": diffs.summarize_suite(diff),
        "diff": diff,
        **_draft_edits(version),
    }


def _draft_edits(version: Any) -> dict[str, Any]:
    """Whether a rebuild made someone's unsaved edits live with this version, and
    what they were (the default before it, against that draft)."""
    included = bool(version.includes_draft_edits)
    return {
        "includes_draft_edits": included,
        "snapshot_diff": version.snapshot_diff if included else None,
    }


def suite_members(job: HostedHarnessJob, version_id: Any) -> list[dict[str, Any]]:
    suite = _piece_version(job, "suite", version_id)
    members = (
        HarnessSuiteMember.no_workspace_objects.filter(suite_version=suite)
        .order_by("number", "scenario_key")
        .values(
            "scenario_key",
            "number",
            name=KeyTextTransform("name", "content"),
            use_case=KeyTextTransform("use_case", "content"),
            shape=Func(F("content"), function="jsonb_typeof", output_field=CharField()),
        )
    )
    members = list(members)
    if any(member["shape"] not in (None, "object", "null") for member in members):
        _malformed(suite.id, "members.content")
    return [
        {
            "scenario_key": member["scenario_key"],
            "number": member["number"],
            "name": member["name"] or "",
            "use_case": member["use_case"] or "",
        }
        for member in members
    ]


def run_version_labels(execution_ids: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Each hosted run's pinned agent, environment and suite version, by test execution.

    Read from the run job's own pins only, never the environment's defaults; an
    unpinned run is absent, so callers show it as before versioning. The newest live
    pinned job wins, as in the run's results. ``agent_differs`` says the run's agent is
    not the one its environment version was built with.
    """
    ids = [one for one in execution_ids if one]
    if not ids:
        return {}
    rows = (
        HostedHarnessJob.no_workspace_objects.filter(test_execution_id__in=ids)
        .exclude(environment_version__isnull=True, suite_version__isnull=True)
        .order_by("created_at")
        .values_list(
            "test_execution_id",
            "environment_version_id",
            "environment_version__version_number",
            "suite_version_id",
            "suite_version__version_number",
            "agent_version_id",
            "agent_version__version_name",
            "agent_version__version_number",
            "environment_version__agent_version_id",
        )
    )
    return {
        str(execution_id): {
            "environment_version": (
                {"id": str(env_id), "label": _label(env_number)} if env_id else None
            ),
            "suite_version": (
                {"id": str(suite_id), "label": _label(suite_number)}
                if suite_id
                else None
            ),
            "agent_version": (
                {"id": str(agent_id), "label": _agent_label(agent_name, agent_number)}
                if agent_id
                else None
            ),
            "agent_differs": bool(agent_id and built_with and agent_id != built_with),
        }
        for (
            execution_id,
            env_id,
            env_number,
            suite_id,
            suite_number,
            agent_id,
            agent_name,
            agent_number,
            built_with,
        ) in rows
    }
