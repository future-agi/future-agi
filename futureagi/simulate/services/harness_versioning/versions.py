"""Record environment and suite versions from an authoring archive and move the defaults."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
    HostedHarnessScenario,
    HostedHarnessStageOutput,
)
from simulate.models.harness_versioning import ArchiveLayout, HashKind, VersionStatus
from simulate.models.hosted_harness import MAX_SCENARIOS_PER_JOB
from simulate.services.harness_versioning import archives, diffs, hashing
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

CODE_SOURCE_KINDS = frozenset({"github", "archive", "remote"})
# Only a build proves its suite against its environment; edits carry older proofs.
PROVED_ORIGINS = frozenset({"authoring", "extend"})
# Saves where a person introduces scenarios; a dropped scenario's key is never reused.
USER_ORIGINS = frozenset({"chat", "manual"})
# The sandbox writes proof times on its own clock.
PROOF_CLOCK_SKEW = timedelta(minutes=2)
SCENARIO_COUNT_MAX = 32767
REMOVED_REASONS = {
    "manual": "removed by manual edit",
    "chat": "removed in chat",
    "extend": "removed when scenarios were added",
    "authoring": "removed when the environment was rebuilt",
    "nudge": "removed when the environment was updated",
}
SUITE_OUTPUT_KINDS = frozenset({"scenarios", "coverage"})
SEALED_MANIFEST = "environment-bundle/manifest.json"
# The pairs an environment was last defaulted to, so a roll back reuses their archive.
DEFAULT_PAIRS_KEPT = 20
# Not busy, but with nothing ready to move a default on.
UNREADY_STATES = frozenset(
    {HostedHarnessJob.State.FAILED, HostedHarnessJob.State.CANCELED}
)
MOVABLE_PIECES = ("agent", "environment", "suite")
MEMBER_FIELDS = (
    "name",
    "instruction",
    "use_case",
    "branch",
    "tests",
    "folder",
    "persona",
    "coverage",
    "sub_goals",
    "keywords",
    "background_noise",
    "max_turns",
)


@dataclass(frozen=True)
class RecordedVersions:
    environment: HarnessEnvironmentVersion
    suite: HarnessSuiteVersion
    pair_key: str


@dataclass(frozen=True)
class Pieces:
    """An authoring tree split into its environment and suite archives, hashed."""

    environment_body: bytes
    suite_body: bytes
    environment_key: str
    suite_key: str
    environment_hash: str
    suite_hash: str
    contract: dict[str, Any]
    scenarios: list[dict[str, Any]]
    environment_documents: dict[str, Any]
    suite_documents: dict[str, Any]
    environment_fields: dict[str, Any]
    suite_fields: dict[str, Any]


def versioning_enabled() -> bool:
    return bool(getattr(settings, "HARNESS_VERSIONING_ENABLED", False))


def environment_head(job: HostedHarnessJob) -> HostedHarnessJob | None:
    """The environment a job belongs to, read fresh; ``None`` once it is deleted."""
    return (
        HostedHarnessJob.no_workspace_objects.filter(id=job.environment_id or job.id)
        .only("id", "environment_version_id", "suite_version_id")
        .first()
    )


def is_versioned(job: HostedHarnessJob) -> bool:
    """Whether this environment's archive writes go through versions, read fresh.

    An environment that has versions keeps them current whether or not the flag is on;
    the flag only decides whether an environment without versions starts getting them.
    """
    head = environment_head(job)
    return head is not None and head.environment_version_id is not None


def pins_runs(environment: HostedHarnessJob) -> bool:
    """Whether a new run of this environment is pinned to its default versions.

    Recording, exact key pairing and refusing a dropped or taken key apply whenever an
    environment has versions, flag or not; only pinning a run waits for the flag.
    """
    return (
        versioning_enabled()
        and environment.environment_version_id is not None
        and environment.suite_version_id is not None
    )


def _json(files: Mapping[str, bytes], path: str) -> Any:
    body = files.get(path)
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _provider_agent(payload: Mapping[str, Any]) -> bool:
    """A hosted or phone agent's prompt belongs to the agent, not the environment."""
    source = payload.get("source") or {}
    return str(source.get("kind") or "") not in CODE_SOURCE_KINDS


def _sub_goals(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    value = _json(files, "sub_goals.json")
    if isinstance(value, dict):
        value = value.get("sub_goals")
    return [goal for goal in value or [] if isinstance(goal, dict) and goal.get("name")]


def suite_documents(suite: Mapping[str, bytes]) -> list[dict[str, Any]]:
    """The suite's scenarios, from its index or else from each folder's document."""
    listed = _json(suite, "scenarios.json")
    if isinstance(listed, list):
        return [one for one in listed if isinstance(one, dict)]
    found: list[dict[str, Any]] = []
    for path in sorted(suite):
        parts = path.split("/")
        if len(parts) == 3 and parts[2] == "scenario.json":
            document = _json(suite, path)
            if isinstance(document, dict):
                found.append({"scenario_key": parts[1], **document})
    return found


def _file_digests(environment: Mapping[str, bytes]) -> dict[str, str]:
    return {
        path: archives.sha256_hex(data)
        for path, data in environment.items()
        if not hashing.is_environment_unhashed(path)
    }


def _stage_outputs(pair_body: bytes) -> list[dict[str, Any]]:
    from simulate.services.hosted_harness_gateway import (
        authoring_stage_outputs_from_archive,
    )

    return authoring_stage_outputs_from_archive(pair_body)


def prepare_pieces(
    job: HostedHarnessJob,
    split: archives.SplitArchive,
    *,
    outputs: list[dict[str, Any]],
    authoring_revision: str,
) -> Pieces:
    """Pack, key and hash both pieces and their readable documents; stores nothing."""
    payload = job.payload or {}
    connector = str((payload.get("agent") or {}).get("connector") or "")
    environment_body = archives.pack_archive(split.environment)
    suite_body = archives.pack_archive(split.suite)
    environment_key = archives.environment_key(
        job.organization_id, job.id, environment_body
    )
    suite_key = archives.suite_key(job.organization_id, job.id, suite_body)
    contract = _json(split.environment, "contract.json") or {}
    sub_goals = _sub_goals(split.environment)
    scenarios = suite_documents(split.suite)
    environment_documents = {
        "contract": contract,
        "sub_goals": sub_goals,
        "sub_goal_digests": hashing.sub_goal_digests(sub_goals),
        "file_digests": _file_digests(split.environment),
        "stage_outputs": [
            one for one in outputs if one.get("kind") not in SUITE_OUTPUT_KINDS
        ],
        "authoring_revisions": [authoring_revision],
    }
    suite_documents_ = {
        "scenarios": scenarios,
        "coverage": _json(split.suite, "coverage.json"),
        "stage_outputs": [
            one for one in outputs if one.get("kind") in SUITE_OUTPUT_KINDS
        ],
        "authoring_revisions": [authoring_revision],
    }
    return Pieces(
        environment_body=environment_body,
        suite_body=suite_body,
        environment_key=environment_key,
        suite_key=suite_key,
        environment_hash=hashing.environment_hash(
            split.environment, provider_agent=_provider_agent(payload)
        ),
        suite_hash=hashing.suite_hash(split.suite),
        contract=contract,
        scenarios=scenarios,
        environment_documents=environment_documents,
        suite_documents=suite_documents_,
        environment_fields={
            "archive_object_key": environment_key,
            "archive_layout": ArchiveLayout.SPLIT,
            "archive_sha256": archives.sha256_hex(environment_body),
            "archive_size": len(environment_body),
            "documents": environment_documents,
            "contract_hash_raw": hashing.contract_hash_raw(split.environment),
            "world_ir_hash": hashing.world_ir_hash(split.environment),
        },
        suite_fields={
            "archive_object_key": suite_key,
            "archive_layout": ArchiveLayout.SPLIT,
            "archive_sha256": archives.sha256_hex(suite_body),
            "archive_size": len(suite_body),
            "documents": suite_documents_,
            "certify_hash": hashing.suite_certify_hash(
                split.suite, keep_overlay=connector == "retell_chat"
            ),
        },
    )


def _sealed(
    split: archives.SplitArchive, metadata: Mapping[str, Any]
) -> dict[str, bytes]:
    """The archive's own sealed bundle, or the copy kept from an earlier one.

    Runs replay ``environment-bundle/`` from the pair, so a pair without it cannot run.
    """
    if SEALED_MANIFEST in split.sealed:
        return dict(split.sealed)
    key = str(metadata.get("sealed_bundle_object_key") or "")
    previous = archives.read_archive(archives.get_archive(key)) if key else {}
    return {**previous, **split.sealed}


def _next_number(model: Any, job: HostedHarnessJob) -> int:
    # The base manager: no workspace or soft-delete filter may hide a used number.
    highest = model._base_manager.filter(job=job).aggregate(top=Max("version_number"))
    return int(highest["top"] or 0) + 1


def _land(
    model: Any,
    job: HostedHarnessJob,
    *,
    hash: str,
    values: dict[str, Any],
    hash_kind: str = HashKind.TREE_V1,
) -> tuple[Any, bool]:
    """An active version with this hash: the existing one, or a new one numbered next."""
    existing = (
        # The base manager, so historical models in a migration work too.
        model._base_manager.filter(
            deleted=False,
            job=job,
            hash=hash,
            hash_kind=hash_kind,
            status=VersionStatus.ACTIVE,
        )
        .order_by("-version_number")
        .first()
    )
    if existing is not None:
        return existing, False
    created = model._base_manager.create(
        job=job,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        status=VersionStatus.ACTIVE,
        version_number=_next_number(model, job),
        hash=hash,
        hash_kind=hash_kind,
        **values,
    )
    return created, True


def recorded_revisions(version: Any) -> list[str]:
    """The authoring revisions a version was recorded from."""
    return [
        str(one)
        for one in (version.documents or {}).get("authoring_revisions") or []
        if one
    ]


def note_revision(version: Any, revision: str) -> None:
    """Remember that a reused version also stands for this authoring revision."""
    if not revision or revision in recorded_revisions(version):
        return
    documents = dict(version.documents or {})
    documents["authoring_revisions"] = [*recorded_revisions(version), revision]
    version.documents = documents
    version.save(update_fields=["documents", "updated_at"])


def _refuse_retired_keys(
    job: HostedHarnessJob, documents: list[dict[str, Any]]
) -> None:
    """A scenario a person adds may not take the key of one that was dropped.

    Only keys new to the environment's default suite are checked, compared as
    normalised tokens, so a scenario the default already holds is never refused.
    """
    retired = retired_keys(job, documents)
    if retired:
        raise HostedHarnessError(
            "scenario_key_retired",
            ", ".join(retired)
            + (" was" if len(retired) == 1 else " were")
            + " removed earlier and can't be reused; give the scenario a new name",
            status_code=422,
        )


def retired_keys(job: HostedHarnessJob, documents: list[dict[str, Any]]) -> list[str]:
    """The dropped scenarios' keys that documents new to the default suite reuse."""
    from simulate.services.hosted_harness_gateway import _scenario_token

    keys = {
        _scenario_token(doc.get("scenario_key"))
        for doc in documents
        if str(doc.get("scenario_key") or "").strip()
    } - {""}
    if job.suite_version_id is not None:
        keys -= {
            _scenario_token(key)
            for key in HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=job.suite_version_id
            ).values_list("scenario_key", flat=True)
        }
        # A member paired by name carries its row's key; its document keeps its own.
        documents_ = (
            HarnessSuiteVersion._base_manager.filter(id=job.suite_version_id)
            .values_list("documents", flat=True)
            .first()
        )
        scenarios = (
            documents_.get("scenarios") if isinstance(documents_, dict) else None
        )
        keys -= {
            _scenario_token(doc.get("scenario_key"))
            for doc in scenarios or []
            if isinstance(doc, dict)
        }
    if not keys:
        return []
    return sorted(
        key
        for key in HostedHarnessScenario._base_manager.filter(
            job=job, deleted=True
        ).values_list("scenario_key", flat=True)
        if _scenario_token(key) in keys
    )


def _refuse_taken_keys(job: HostedHarnessJob, documents: list[dict[str, Any]]) -> None:
    """A scenario a person adds may not take the key of a row another scenario holds.

    A backfill may have paired a scenario with a row keyed differently; that row stays
    with its scenario, so its key is not free for a new one.
    """
    taken = taken_keys(job, documents)
    if taken:
        raise HostedHarnessError(
            "scenario_key_taken",
            ", ".join(taken)
            + (" is" if len(taken) == 1 else " are")
            + " already used by another scenario; give the scenario a new name",
            status_code=422,
        )


def taken_keys(job: HostedHarnessJob, documents: list[dict[str, Any]]) -> list[str]:
    """The keys of rows other scenarios hold that the documents ask for."""
    from simulate.services.harness_scenarios import _default_member_rows
    from simulate.services.hosted_harness_gateway import _scenario_token

    if job.suite_version_id is None:
        return []
    paired = _default_member_rows(
        job, list(HostedHarnessScenario.all_objects.filter(job=job))
    )
    held = {
        token: row.scenario_key
        for key_token, row in paired.items()
        if (token := _scenario_token(row.scenario_key)) != key_token
    }
    return sorted(
        {
            held[token]
            for doc in documents
            if (token := _scenario_token(doc.get("scenario_key"))) in held
            and token not in paired
        }
    )


def _member_rows(
    job: HostedHarnessJob, documents: list[dict[str, Any]]
) -> list[tuple[HostedHarnessScenario, dict[str, Any]]]:
    from simulate.services.harness_scenarios import pair_scenarios

    # The rows indexing wrote each document to, so members never pair differently.
    return pair_scenarios(job, documents, prune=True, exact=True)


def _proved_at(proof: Mapping[str, Any]) -> datetime | None:
    """When a proof was made; a time without a zone is the sandbox's UTC."""
    try:
        value = datetime.fromisoformat(str(proof.get("proved_at") or ""))
    except ValueError:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _member_content(document: Mapping[str, Any]) -> dict[str, Any]:
    return {field: document[field] for field in MEMBER_FIELDS if field in document}


def _add_members(
    suite_version: HarnessSuiteVersion,
    paired: list[tuple[HostedHarnessScenario, dict[str, Any]]],
) -> None:
    HarnessSuiteMember.no_workspace_objects.bulk_create(
        [
            HarnessSuiteMember(
                suite_version=suite_version,
                scenario=row,
                scenario_key=row.scenario_key,
                number=row.number,
                content=_member_content(document),
            )
            for row, document in paired
        ]
    )


def environment_change(
    base: HarnessEnvironmentVersion | None,
    contract: Any,
    documents: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """The environment documents' diff against ``base``, and its summary."""
    diff = None
    if base is not None:
        previous = base.documents or {}
        diff = diffs.environment_diff(
            previous_contract=previous.get("contract"),
            contract=contract,
            previous_sub_goals=previous.get("sub_goal_digests") or {},
            sub_goals=documents.get("sub_goal_digests") or {},
            previous_files=previous.get("file_digests"),
            files=documents.get("file_digests") or {},
        )
    return diff, diffs.summarize_environment(diff)


def suite_change(
    base: HarnessSuiteVersion | None,
    scenarios: list[dict[str, Any]],
    origin: str,
) -> tuple[dict[str, Any] | None, str]:
    """The scenarios' diff against ``base``, and its summary."""
    diff = None
    if base is not None:
        diff = diffs.suite_diff(
            (base.documents or {}).get("scenarios") or [],
            scenarios,
            removed_reason=REMOVED_REASONS.get(origin, "removed"),
        )
    return diff, diffs.summarize_suite(diff)


def _carried_proofs(
    environment_version: HarnessEnvironmentVersion,
    paired: list[tuple[HostedHarnessScenario, dict[str, Any]]],
) -> dict[str, Any]:
    """Earlier proofs against this environment version for scenarios that did not change.

    An extend proves only its new scenarios; the others keep the proofs they already have.
    """
    previous = (
        HarnessSuiteValidation.no_workspace_objects.filter(
            environment_version=environment_version
        )
        .order_by("-proved_at")
        .first()
    )
    if previous is None or not isinstance(previous.proofs, dict):
        return {}

    def proved(content: Mapping[str, Any]) -> dict[str, Any]:
        # A rename keeps the key and what the proof checked.
        return {field: value for field, value in content.items() if field != "name"}

    contents = {
        member.scenario_key: proved(member.content or {})
        for member in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=previous.suite_version_id
        )
    }
    carried: dict[str, Any] = {}
    for row, document in paired:
        proof = previous.proofs.get(row.scenario_key)
        if (
            isinstance(proof, dict)
            and row.scenario_key in contents
            and contents[row.scenario_key] == proved(_member_content(document))
        ):
            carried[row.scenario_key] = proof
    return carried


def _validation(
    suite_version: HarnessSuiteVersion,
    environment_version: HarnessEnvironmentVersion,
    proofs: object,
    paired: list[tuple[HostedHarnessScenario, dict[str, Any]]],
    job: HostedHarnessJob,
    proved_since: datetime,
) -> None:
    """Record the build's proofs of this suite against this environment.

    Only proofs made in this session count: the proofs file travels with the suite, so
    older entries were made against other content. A proof without a time is ignored.
    Unchanged scenarios keep their earlier proofs against the same environment version.
    """
    # TODO: require each proof's environment_hash once ALK writes it.
    if not isinstance(proofs, dict) or not paired:
        return
    from simulate.services.hosted_harness_gateway import _scenario_token

    cutoff = proved_since - PROOF_CLOCK_SKEW
    by_token = {
        _scenario_token(key): value
        for key, value in proofs.items()
        if isinstance(value, dict)
        and (proved := _proved_at(value)) is not None
        and proved >= cutoff
    }
    if proofs and not by_token:
        logger.warning(
            "every proof predates this session job=%s proofs=%s since=%s",
            job.id,
            len(proofs),
            proved_since.isoformat(),
        )
    kept: dict[str, Any] = _carried_proofs(environment_version, paired)
    for row, document in paired:
        proof = by_token.get(_scenario_token(row.scenario_key)) or by_token.get(
            _scenario_token(document.get("scenario_key"))
        )
        if proof is not None:
            kept[row.scenario_key] = proof
    if not kept:
        return
    keys = [row.scenario_key for row, _ in paired]
    holding = [key for key in keys if kept.get(key, {}).get("holds") is True]
    if len(holding) == len(keys):
        status = HarnessSuiteValidation.Status.PASSED
    elif holding:
        status = HarnessSuiteValidation.Status.PARTIAL
    else:
        status = HarnessSuiteValidation.Status.FAILED
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=suite_version,
        environment_version=environment_version,
        defaults={
            "status": status,
            "proofs": kept,
            "job": job,
            "proved_at": timezone.now(),
        },
    )


def record_authoring(
    job: HostedHarnessJob,
    body: bytes,
    *,
    origin: str,
    user: Any = None,
    expected_pair_key: str | None = None,
    proved_since: datetime | None = None,
    prepare_payload: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    consumes_extend_marker: bool = False,
) -> RecordedVersions | None:
    """Version an environment head's authoring archive and make it the default pair.

    Unchanged pieces reuse their active version. Run jobs, and environments without
    versions while versioning is off, are left to the legacy single archive. An environment
    that already has versions is always recorded; whether it has them is read fresh, since
    a backfill can land while a caller holds an old copy of the job.
    ``expected_pair_key`` is the pair an edit was made against; a newer default refuses
    the edit instead of losing it. ``proved_since`` is when this build's session started;
    without it no proof is recorded. ``prepare_payload`` applies the caller's own payload
    changes in the same save that moves the archive pointer. With
    ``consumes_extend_marker`` nothing is recorded once the job's extend marker is gone:
    an overlapping poll already recorded this extend.
    """
    if job.environment_id is not None:
        return None
    if not versioning_enabled() and not is_versioned(job):
        return None
    from simulate.services.hosted_harness_gateway import authoring_content_digest

    split = archives.split_authoring(archives.read_archive(body))
    if "contract.json" not in split.environment or not any(
        path.startswith(archives.SUITE_ROOTS) for path in split.suite
    ):
        raise HostedHarnessError(
            "authoring_incomplete",
            "the authoring archive has no contract or no scenarios to version",
            status_code=422,
        )
    payload = dict(job.payload or {})
    metadata = dict(payload.get("metadata") or {})
    sealed = _sealed(split, metadata)

    pair_body = archives.pack_archive(
        archives.compose(split.environment, split.suite, sealed)
    )
    pair_key = archives.pair_key(job.organization_id, job.id, pair_body)
    revision = authoring_content_digest(pair_body)
    pieces = prepare_pieces(
        job, split, outputs=_stage_outputs(pair_body), authoring_revision=revision
    )
    if origin in USER_ORIGINS:
        # Refused before anything is uploaded; checked again under the lock below.
        _refuse_retired_keys(job, pieces.scenarios)
        _refuse_taken_keys(job, pieces.scenarios)
    archives.put_archive(pieces.environment_key, pieces.environment_body)
    archives.put_archive(pieces.suite_key, pieces.suite_body)
    archives.put_archive(pair_key, pair_body)
    sealed_key = ""
    sealed_body = b""
    if sealed:
        sealed_body = archives.pack_archive(sealed)
        sealed_key = archives.sealed_key(job.organization_id, job.id, sealed_body)
        archives.put_archive(sealed_key, sealed_body)
    contract = pieces.contract
    documents = pieces.scenarios

    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if not versioning_enabled() and locked.environment_version_id is None:
            return None
        if consumes_extend_marker and not (
            ((locked.payload or {}).get("metadata") or {}).get("scenario_extend")
        ):
            return None
        current_pair = ((locked.payload or {}).get("metadata") or {}).get(
            "authoring_object_key"
        )
        if expected_pair_key is not None and current_pair != expected_pair_key:
            raise HostedHarnessError(
                "version_conflict",
                "the environment changed while this edit was being saved",
                status_code=409,
            )
        if origin in USER_ORIGINS:
            _refuse_retired_keys(locked, documents)
            _refuse_taken_keys(locked, documents)
        from simulate.services.harness_agent_versions import fill_agent_pin

        # A build made before its environment was pinned is the pin's version.
        agent_pinned = fill_agent_pin(locked)
        base_environment = locked.environment_version
        base_suite = locked.suite_version
        environment_diff, environment_summary = environment_change(
            base_environment, contract, pieces.environment_documents
        )
        environment_version, environment_created = _land(
            HarnessEnvironmentVersion,
            locked,
            hash=pieces.environment_hash,
            values={
                **pieces.environment_fields,
                "origin": origin,
                "parent": base_environment,
                "summary": environment_summary,
                "diff": environment_diff,
                "agent_version_id": locked.agent_version_id,
                "created_by": user,
            },
        )
        suite_diff, suite_summary = suite_change(base_suite, documents, origin)
        suite_version, suite_created = _land(
            HarnessSuiteVersion,
            locked,
            hash=pieces.suite_hash,
            values={
                **pieces.suite_fields,
                "origin": origin,
                "parent": base_suite,
                "summary": suite_summary,
                "diff": suite_diff,
                "scenario_count": min(len(documents), SCENARIO_COUNT_MAX),
                "authored_against": environment_version,
                "created_by": user,
            },
        )
        if not environment_created:
            note_revision(environment_version, revision)
        if not suite_created:
            note_revision(suite_version, revision)
        paired = _member_rows(locked, documents)
        if suite_created:
            _add_members(suite_version, paired)
        if origin in PROVED_ORIGINS and proved_since is not None:
            _validation(
                suite_version,
                environment_version,
                _json(split.suite, "proofs.json"),
                paired,
                locked,
                proved_since,
            )

        if origin in PROVED_ORIGINS and SEALED_MANIFEST in split.sealed:
            # Only a build seals a bundle for this pair; edits carry an older one.
            from simulate.services.harness_versioning.assembly import seed_from_build

            try:
                with transaction.atomic():
                    seed_from_build(
                        locked,
                        environment_version=environment_version,
                        suite_version=suite_version,
                        sealed=split.sealed,
                        object_key=sealed_key,
                        sha256=archives.sha256_hex(sealed_body),
                        size=len(sealed_body),
                    )
            except Exception:  # noqa: BLE001 - the cache never fails the save
                logger.exception(
                    "could not cache the sealed bundle job=%s origin=%s", job.id, origin
                )

        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        if (
            base_environment is not None
            and base_suite is not None
            and metadata.get("authoring_object_key")
            and _recorded_against(metadata, base_environment, base_suite)
        ):
            _remember_pair(
                metadata,
                base_environment,
                base_suite,
                _outgoing_pair(metadata),
                agent_version_id=locked.agent_version_id,
                replace=False,
            )
        metadata["authoring_object_key"] = pair_key
        metadata["authoring_revision"] = revision
        if sealed_key:
            metadata["sealed_bundle_object_key"] = sealed_key
        # ``sealed`` already falls back to the kept copy, so this is what runs replay.
        metadata["default_pair_sealed"] = SEALED_MANIFEST in sealed
        _remember_pair(
            metadata,
            environment_version,
            suite_version,
            _Pair(
                key=pair_key,
                revision=revision,
                sealed=metadata["default_pair_sealed"],
                sealed_key=sealed_key,
            ),
            agent_version_id=locked.agent_version_id,
        )
        payload["metadata"] = metadata
        if prepare_payload is not None:
            payload = prepare_payload(payload)
        update_fields = [
            "payload",
            "environment_version",
            "suite_version",
            "content_updated_at",
            "updated_at",
        ]
        if agent_pinned:
            update_fields.append("agent_version")
        if 1 <= suite_version.scenario_count <= MAX_SCENARIOS_PER_JOB:
            payload["scenario_count"] = suite_version.scenario_count
            locked.scenario_count = suite_version.scenario_count
            update_fields.append("scenario_count")
        locked.payload = payload
        locked.environment_version = environment_version
        locked.suite_version = suite_version
        locked.content_updated_at = timezone.now()
        locked.save(update_fields=update_fields)
        if (
            base_environment is not None
            and environment_version.id != base_environment.id
        ):
            from simulate.services.harness_versioning.nudges import (
                schedule_environment_moved,
            )

            schedule_environment_moved(
                locked.id,
                from_environment_version_id=base_environment.id,
                environment_version_id=environment_version.id,
            )

    job.refresh_from_db()
    return RecordedVersions(
        environment=environment_version, suite=suite_version, pair_key=pair_key
    )


def record_authoring_quietly(
    job: HostedHarnessJob,
    body: bytes,
    *,
    origin: str,
    user: Any = None,
    proved_since: datetime | None = None,
) -> RecordedVersions | None:
    """``record_authoring`` for an environment's first versions; never fails the legacy
    save. Saves to an environment that already has versions call ``record_authoring``.
    """
    try:
        return record_authoring(
            job, body, origin=origin, user=user, proved_since=proved_since
        )
    except Exception:  # noqa: BLE001 - the legacy archive stays authoritative
        logger.exception("could not record versions job=%s origin=%s", job.id, origin)
        return None


@dataclass(frozen=True)
class _Pair:
    """A runnable authoring archive for one environment and suite version."""

    key: str
    revision: str
    # Whether it holds a sealed bundle a run replays; without one a run certifies.
    sealed: bool
    # The sealed files kept apart, so an edit on top of the pair carries them.
    sealed_key: str = ""


def _pair_name(environment_version: Any, suite_version: Any) -> str:
    return f"{environment_version.id}:{suite_version.id}"


def _outgoing_pair(metadata: Mapping[str, Any]) -> _Pair:
    """The default pair's archive as the metadata knows it; a legacy one is sealed."""
    return _Pair(
        key=str(metadata["authoring_object_key"]),
        revision=str(metadata.get("authoring_revision") or ""),
        sealed=metadata.get("default_pair_sealed") is not False,
        sealed_key=str(metadata.get("sealed_bundle_object_key") or ""),
    )


def _recorded_against(
    metadata: Mapping[str, Any], environment_version: Any, suite_version: Any
) -> bool:
    """Whether the default archive is one the default versions were recorded from; a
    legacy freeze racing a backfill can store another one under them.
    """
    revision = str(metadata.get("authoring_revision") or "")
    return bool(revision) and all(
        revision in recorded_revisions(version)
        for version in (environment_version, suite_version)
    )


def _kept_agent_matches(kept: Mapping[str, Any], job: HostedHarnessJob) -> bool:
    from simulate.services.harness_versioning.assembly import same_bundle_agent

    agent_version_id = kept.get("agent_version_id")
    # An entry kept before agents were named is the current agent's: an agent move
    # names every such entry first (``_name_kept_pairs``).
    if not agent_version_id:
        return True
    # An agent with the same bundle key replays the same sealed bundle.
    return same_bundle_agent(agent_version_id, job.agent_version_id, job)


def _name_kept_pairs(metadata: dict[str, Any], agent_version_id: Any) -> None:
    """Name the outgoing agent on entries kept before agents were named; with no
    agent to name, their bundles are not replayed."""
    pairs = metadata.get("default_pairs")
    if not isinstance(pairs, dict):
        return
    named = {}
    for name, entry in pairs.items():
        if isinstance(entry, dict) and not entry.get("agent_version_id"):
            entry = dict(entry)
            if agent_version_id:
                entry["agent_version_id"] = str(agent_version_id)
            else:
                entry["sealed"] = False
                entry.pop("sealed_key", None)
        named[name] = entry
    metadata["default_pairs"] = named


def _remember_pair(
    metadata: dict[str, Any],
    environment_version: Any,
    suite_version: Any,
    pair: _Pair,
    *,
    agent_version_id: Any = None,
    replace: bool = True,
) -> None:
    """Keep the pair's archive for a later roll back; only the newest pairs are kept.

    Entries carry their own time: a JSON column does not keep key order. A sealed
    bundle is the default agent's, so the entry names that agent.
    """
    pairs = {
        name: entry
        for name, entry in (metadata.get("default_pairs") or {}).items()
        if isinstance(entry, dict)
    }
    name = _pair_name(environment_version, suite_version)
    if name in pairs and not replace:
        return
    entry = {
        "key": pair.key,
        "revision": pair.revision,
        "sealed": pair.sealed,
        "at": timezone.now().isoformat(),
    }
    if pair.sealed_key:
        entry["sealed_key"] = pair.sealed_key
    if agent_version_id:
        entry["agent_version_id"] = str(agent_version_id)
    pairs[name] = entry
    newest = sorted(pairs.items(), key=lambda item: str(item[1].get("at") or ""))
    metadata["default_pairs"] = dict(newest[-DEFAULT_PAIRS_KEPT:])


def _rollback_unavailable(piece: str) -> HostedHarnessError:
    return HostedHarnessError(
        "rollback_unavailable",
        "This combination hasn't been prepared yet",
        status_code=422,
        details={"piece": piece},
    )


def _default_runtime(job: HostedHarnessJob) -> HarnessAgentVersionRuntime | None:
    """The default agent's side row, with the facts it runs with in ``job``."""
    from simulate.services.harness_versioning.backfill import effective_runtime

    if not job.agent_version_id:
        return None
    return effective_runtime(
        job,
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=job.agent_version_id
        ).first(),
    )


def _cached_sealed(
    job: HostedHarnessJob,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
) -> tuple[dict[str, bytes] | None, str]:
    """The sealed files of a ready bundle the default agent has for this pair."""
    from simulate.services.harness_versioning import assembly

    runtime = _default_runtime(job)
    if runtime is None:
        return None, ""
    bundle = HarnessAssembledBundle._base_manager.filter(
        job_id=job.id,
        assembly_key=assembly.bundle_key(runtime, environment_version, suite_version),
        state=HarnessAssembledBundle.State.READY,
        object_key__isnull=False,
    ).first()
    files = assembly._cached_files(bundle) if bundle is not None else None
    return (files, bundle.object_key) if files is not None else (None, "")


def _default_pair(
    job: HostedHarnessJob,
    metadata: Mapping[str, Any],
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    piece: str,
) -> _Pair:
    """The archive a run of this pair replays: one kept from when it was the default,
    else composed with a cached bundle, else composed bare for runs to certify.
    """
    from simulate.services.harness_versioning import assembly
    from simulate.services.hosted_harness_gateway import authoring_content_digest

    kept = (metadata.get("default_pairs") or {}).get(
        _pair_name(environment_version, suite_version)
    )
    pair = None
    if (
        isinstance(kept, dict)
        and kept.get("key")
        # A bundle sealed for another agent is not this agent's to replay.
        and (kept.get("sealed") is False or _kept_agent_matches(kept, job))
        and archives.archive_exists(str(kept["key"]))
    ):
        sealed_key = str(kept.get("sealed_key") or "")
        if sealed_key and not archives.archive_exists(sealed_key):
            # The pair still holds its bundle; only the copy an edit carries is gone.
            sealed_key = ""
        pair = _Pair(
            key=str(kept["key"]),
            revision=str(kept.get("revision") or ""),
            sealed=kept.get("sealed") is not False,
            sealed_key=sealed_key,
        )
        if pair.sealed:
            return pair
    sealed, sealed_key = _cached_sealed(job, environment_version, suite_version)
    if sealed is None:
        # A bare pair is certified by a run, which needs the default agent's side row.
        if not assembly.certify_enabled() or _default_runtime(job) is None:
            raise _rollback_unavailable(piece)
        if pair is not None:
            return pair
    try:
        # A backfilled combined archive may be gone.
        tree = archives.compose(
            archives.read_archive(
                archives.get_archive(environment_version.archive_object_key)
            ),
            archives.read_archive(
                archives.get_archive(suite_version.archive_object_key)
            ),
            sealed,
        )
    except S3Error as exc:
        if assembly.is_transient(exc):
            raise
        raise _rollback_unavailable(piece) from exc
    except HostedHarnessError as exc:
        raise _rollback_unavailable(piece) from exc
    body = archives.pack_archive(tree)
    key = archives.pair_key(job.organization_id, job.id, body)
    archives.put_archive(key, body)
    return _Pair(
        key=key,
        revision=authoring_content_digest(body),
        sealed=sealed is not None,
        sealed_key=sealed_key,
    )


def _default_outputs(
    job: HostedHarnessJob,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
) -> None:
    """Show the new defaults' authored snapshots; the environment output is kept."""
    from simulate.services.hosted_harness import (
        overlay_stage_outputs,
        pinned_stage_outputs,
    )
    from simulate.services.hosted_harness_conversation import _CHECKPOINT_STAGE_KINDS

    pinned = pinned_stage_outputs(environment_version, suite_version)
    job.stage_outputs = overlay_stage_outputs(
        list(job.stage_outputs or []), pinned, drop=_CHECKPOINT_STAGE_KINDS
    )
    rows = list(
        HostedHarnessStageOutput._base_manager.filter(
            job=job, kind__in=_CHECKPOINT_STAGE_KINDS
        ).order_by("deleted", "-created_at")
    )
    now = timezone.now()
    shown: set[str] = set()
    for row in rows:
        output = pinned.get(row.kind)
        if output is None:
            if not row.deleted:
                row.deleted, row.deleted_at = True, now
                row.save(update_fields=["deleted", "deleted_at", "updated_at"])
            continue
        if row.deleted and row.kind in shown:
            continue
        shown.add(row.kind)
        row.data = output.get("data")
        row.summary = str(output.get("summary") or "")[:1024]
        row.deleted, row.deleted_at = False, None
        row.save(
            update_fields=["data", "summary", "deleted", "deleted_at", "updated_at"]
        )
    # A job without rows reads its outputs from the job itself.
    if rows or HostedHarnessStageOutput.no_workspace_objects.filter(job=job).exists():
        HostedHarnessStageOutput.no_workspace_objects.bulk_create(
            [
                HostedHarnessStageOutput(
                    job=job,
                    kind=kind,
                    title=str(output.get("title") or kind)[:255],
                    summary=str(output.get("summary") or "")[:1024],
                    data=output.get("data"),
                )
                for kind, output in pinned.items()
                if kind not in shown
            ]
        )


def _conflict(piece: str, current: Any) -> HostedHarnessError:
    from simulate.services.harness_versioning.read import _label, _message_label

    return HostedHarnessError(
        "version_conflict",
        "The default changed to "
        + _message_label(_label(getattr(current, "version_number", None)))
        + " since you opened this; pick again",
        status_code=409,
        details={"piece": piece},
    )


def move_agent(
    locked: HostedHarnessJob, version: Any, *, moved: Mapping[str, Any] | None = None
) -> None:
    """Make ``version`` the locked environment's default agent.

    The default pair's archive may hold a bundle sealed for the outgoing agent, so the
    pair is resolved again for the new one: kept when its bundle key is the outgoing
    one's, else composed with its cached bundle, else bare for runs to certify, else
    refused with ``rollback_unavailable``. A move by hand is recorded as ``moved``.
    """
    outgoing = locked.agent_version_id
    environment_version = locked.environment_version
    suite_version = locked.suite_version
    payload = dict(locked.payload or {})
    metadata = dict(payload.get("metadata") or {})
    update_fields = ["agent_version", "content_updated_at", "updated_at"]
    locked.agent_version = version
    if (
        environment_version is not None
        and suite_version is not None
        and metadata.get("authoring_object_key")
    ):
        _name_kept_pairs(metadata, outgoing)
        if _recorded_against(metadata, environment_version, suite_version):
            _remember_pair(
                metadata,
                environment_version,
                suite_version,
                _outgoing_pair(metadata),
                agent_version_id=outgoing,
                replace=False,
            )
        pair = _default_pair(
            locked, metadata, environment_version, suite_version, "agent"
        )
        metadata["authoring_object_key"] = pair.key
        metadata["authoring_revision"] = pair.revision
        metadata["default_pair_sealed"] = pair.sealed
        if pair.sealed_key:
            metadata["sealed_bundle_object_key"] = pair.sealed_key
        else:
            metadata.pop("sealed_bundle_object_key", None)
        _remember_pair(
            metadata,
            environment_version,
            suite_version,
            pair,
            agent_version_id=version.id,
        )
        payload["metadata"] = metadata
        locked.payload = payload
        update_fields.append("payload")
    if moved is not None:
        from simulate.services.harness_agent_versions import HAND_MOVE_KEY

        metadata[HAND_MOVE_KEY] = dict(moved)
        payload["metadata"] = metadata
        locked.payload = payload
        if "payload" not in update_fields:
            update_fields.append("payload")
    locked.content_updated_at = timezone.now()
    locked.save(update_fields=update_fields)
    from simulate.services.harness_agent_versions import mirror_default_status

    mirror_default_status(version)


def _move_agent_default(
    locked: HostedHarnessJob, version_id: Any, expected_default_id: Any, user: Any
) -> HostedHarnessJob:
    from simulate.models import AgentVersion
    from simulate.services.harness_agent_versions import hand_move, require_agent_pick
    from simulate.services.harness_versioning.nudges import schedule_environment_nudge

    # Locked, so a concurrent discard sees this move before its in-use check.
    require_agent_pick(
        locked,
        version_id,
        refusal="This older agent version can't be made the default",
        lock=True,
    )
    current = locked.agent_version
    if current is not None and current.id == version_id:
        return locked
    if (current.id if current is not None else None) != expected_default_id:
        raise _conflict("agent", current)
    version = AgentVersion.no_workspace_objects.get(id=version_id)
    move_agent(locked, version, moved=hand_move(user, version.id))
    schedule_environment_nudge(
        locked.id,
        version.id,
        default_moved=True,
        outgoing_agent_version_id=current.id if current is not None else None,
    )
    logger.info(
        "harness_default_moved piece=agent from=%s to=%s actor=%s",
        current.id if current is not None else None,
        version.id,
        getattr(user, "id", None),
    )
    return locked


def require_not_busy(locked: HostedHarnessJob, piece: str) -> None:
    """Refuse to move a default while the environment builds, extends or is unready."""
    from simulate.services.harness_agent_versions import _busy

    if _busy(locked):
        raise HostedHarnessError(
            "environment_busy",
            (
                "The environment isn't ready; the default can't be changed now"
                if locked.state in UNREADY_STATES
                else "The environment is being built or extended; try again "
                "when it finishes"
            ),
            status_code=409,
            details={"piece": piece},
        )


def set_defaults(
    locked: HostedHarnessJob,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    pair: _Pair,
    *,
    pieces: set[str],
    outgoing_agent_version_id: Any,
) -> None:
    """Make the pair the locked environment's defaults, with every side effect.

    The outgoing pair is kept for a roll back under the agent it ran with, which
    differs from ``locked``'s when the agent moves in the same step. An environment
    or suite move tells the nudges once the caller commits.
    """
    from simulate.services import harness_scenarios
    from simulate.services.harness_versioning.nudges import (
        schedule_environment_moved,
        schedule_reconcile,
    )
    from simulate.services.hosted_harness_conversation import _bind_added_scenarios

    base_environment = locked.environment_version
    base_suite = locked.suite_version
    agent_moved = locked.agent_version_id != outgoing_agent_version_id
    if agent_moved:
        _name_kept_pairs(metadata, outgoing_agent_version_id)
    if (
        base_environment is not None
        and base_suite is not None
        and metadata.get("authoring_object_key")
        and _recorded_against(metadata, base_environment, base_suite)
    ):
        _remember_pair(
            metadata,
            base_environment,
            base_suite,
            _outgoing_pair(metadata),
            agent_version_id=outgoing_agent_version_id,
            replace=False,
        )
    locked.environment_version = environment_version
    locked.suite_version = suite_version
    if "suite" in pieces:
        # Reads the new default's members off the job: its rows come back.
        harness_scenarios.index_scenarios(
            locked,
            (suite_version.documents or {}).get("scenarios") or [],
            prune=True,
        )
        _bind_added_scenarios(locked)
    _default_outputs(locked, environment_version, suite_version)
    update_fields = [
        "payload",
        "agent_version",
        "environment_version",
        "suite_version",
        "stage_outputs",
        "content_updated_at",
        "updated_at",
    ]
    if 1 <= suite_version.scenario_count <= MAX_SCENARIOS_PER_JOB:
        payload["scenario_count"] = suite_version.scenario_count
        locked.scenario_count = suite_version.scenario_count
        update_fields.append("scenario_count")
    metadata["authoring_object_key"] = pair.key
    metadata["authoring_revision"] = pair.revision
    metadata["default_pair_sealed"] = pair.sealed
    if pair.sealed_key:
        metadata["sealed_bundle_object_key"] = pair.sealed_key
    else:
        # An edit on top must not carry another pair's bundle.
        metadata.pop("sealed_bundle_object_key", None)
    _remember_pair(
        metadata,
        environment_version,
        suite_version,
        pair,
        agent_version_id=locked.agent_version_id,
    )
    payload["metadata"] = metadata
    locked.payload = payload
    locked.content_updated_at = timezone.now()
    locked.save(update_fields=update_fields)
    if agent_moved and locked.agent_version is not None:
        from simulate.services.harness_agent_versions import mirror_default_status

        mirror_default_status(locked.agent_version)
    if "environment" in pieces:
        schedule_environment_moved(
            locked.id,
            from_environment_version_id=getattr(base_environment, "id", None),
            environment_version_id=environment_version.id,
        )
    if "suite" in pieces:
        # A pending suite card the new suite is proved against closes.
        schedule_reconcile(locked.id)


def _lock_environment(environment: HostedHarnessJob) -> HostedHarnessJob:
    locked = (
        HostedHarnessJob.no_workspace_objects.select_for_update()
        .filter(id=environment.id)
        .first()
    )
    if locked is None:
        raise HostedHarnessError(
            "environment_not_found", "Environment not found", status_code=404
        )
    return locked


def _offer_reapply(locked: HostedHarnessJob, piece: str, version: Any) -> None:
    """A rolled-back version that made someone's unsaved edits live offers them again."""
    from simulate.services.harness_versioning.read import _label, _message_label
    from simulate.services.hosted_harness_conversation import post_system_message

    label = _label(version.version_number)
    transaction.on_commit(
        lambda: post_system_message(
            locked,
            content=(
                f"The edits that went live with {_message_label(label)} were rolled"
                " back. Re-apply them in chat?"
            ),
            payload={
                "reapply": {
                    "piece": piece,
                    "version_id": str(version.id),
                    "label": label,
                }
            },
        )
    )


def move_default(
    environment: HostedHarnessJob,
    piece: str,
    version_id: Any,
    *,
    expected_default_id: Any,
    user: Any = None,
) -> HostedHarnessJob:
    """Make an agent, environment or suite version the environment's default.

    ``expected_default_id`` is the default the caller saw; a newer one refuses the move.
    The pair's runnable archive is found before anything moves, and the whole move is
    one transaction.
    """
    from simulate.services.harness_versioning.drafts import require_no_rebuild
    from simulate.services.hosted_harness import _not_pickable

    model = HarnessEnvironmentVersion if piece == "environment" else HarnessSuiteVersion
    with transaction.atomic():
        locked = _lock_environment(environment)
        require_not_busy(locked, piece)
        if piece == "agent":
            return _move_agent_default(locked, version_id, expected_default_id, user)
        require_no_rebuild(locked, piece=piece)
        version = model.no_workspace_objects.filter(
            job=locked, id=version_id, status=VersionStatus.ACTIVE
        ).first()
        if version is None:
            raise _not_pickable(piece)
        current = getattr(locked, f"{piece}_version")
        # A repeated click after the move succeeded is the same no-op.
        if current is not None and current.id == version.id:
            return locked
        if (current.id if current is not None else None) != expected_default_id:
            raise _conflict(piece, current)
        environment_version = (
            version if piece == "environment" else locked.environment_version
        )
        suite_version = version if piece == "suite" else locked.suite_version
        if suite_version is None:
            raise HostedHarnessError(
                "version_not_pickable",
                "This environment has no scenario version yet",
                status_code=422,
                details={"piece": piece},
            )
        if environment_version is None:
            raise _not_pickable(piece)
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        pair = _default_pair(
            locked, metadata, environment_version, suite_version, piece
        )
        set_defaults(
            locked,
            payload,
            metadata,
            environment_version,
            suite_version,
            pair,
            pieces={piece},
            outgoing_agent_version_id=locked.agent_version_id,
        )
        if current is not None and current.includes_draft_edits:
            _offer_reapply(locked, piece, current)
    logger.info(
        "harness_default_moved piece=%s from=%s to=%s actor=%s",
        piece,
        current.id if current is not None else None,
        version.id,
        getattr(user, "id", None),
    )
    return locked
