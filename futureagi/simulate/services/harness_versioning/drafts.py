"""Unsaved edits to an environment's versions, and Save and Discard on them.

Chat, the Scenarios tab and Extend write one draft per piece (environment, suite) on
top of that piece's default. Nothing a draft holds is runnable: runs, run plans, roll
backs and agent moves read the defaults only. Save makes every open draft live in one
default move; Discard sets one aside.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from minio.error import S3Error

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessNudge,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationLease,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.harness_versioning import DiscardReason, HashKind, VersionStatus
from simulate.services.canonical_json import canonical_digest
from simulate.services.harness_versioning import archives, hashing
from simulate.services.harness_versioning.versions import (
    SCENARIO_COUNT_MAX,
    SEALED_MANIFEST,
    USER_ORIGINS,
    _add_members,
    _default_pair,
    _default_runtime,
    _lock_environment,
    _member_content,
    _member_rows,
    _next_number,
    _Pair,
    _refuse_retired_keys,
    _refuse_taken_keys,
    _sealed,
    _stage_outputs,
    environment_change,
    environment_head,
    note_revision,
    prepare_pieces,
    require_not_busy,
    retired_keys,
    set_defaults,
    suite_change,
    taken_keys,
    versioning_enabled,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

DRAFT_PIECES = ("environment", "suite")
# How long a finished turn's publish may still be on its way.
PUBLISH_GRACE = timedelta(seconds=60)
DRAFT_SAVES_KEPT = 50
SET_ASIDE_SHOWN = 10


@dataclass(frozen=True)
class DraftRef:
    """A piece's open draft as a writer saw it; ``DraftRef(None, 0)`` is no draft."""

    id: str | None
    revision: int


NO_DRAFT = DraftRef(None, 0)


@dataclass(frozen=True)
class DraftWrite:
    environment: HarnessEnvironmentVersion | None
    suite: HarnessSuiteVersion | None
    written: frozenset[str]
    discarded: frozenset[str]


@dataclass(frozen=True)
class SaveRequest:
    expected_parent_id: Any
    expected_revision: int | None = None


@dataclass(frozen=True)
class ComposedWorkspace:
    files: dict[str, bytes]
    scenario_count: int
    refs: dict[str, DraftRef]
    token: str
    stale: dict[str, bool]


def _model(piece: str) -> Any:
    return HarnessEnvironmentVersion if piece == "environment" else HarnessSuiteVersion


def _piece_of(version: Any) -> str:
    return "environment" if isinstance(version, HarnessEnvironmentVersion) else "suite"


def _error(code: str, message: str, status_code: int, **details: Any):
    return HostedHarnessError(code, message, status_code=status_code, details=details)


# --- reading drafts -----------------------------------------------------------------


def drafts_apply(environment: HostedHarnessJob) -> bool:
    """Whether chat, Scenarios-tab and Extend edits go to drafts: the flag is on and
    the environment head has versions, read fresh."""
    if not versioning_enabled() or environment.environment_id is not None:
        return False
    head = environment_head(environment)
    return (
        head is not None
        and head.environment_version_id is not None
        and head.suite_version_id is not None
    )


def open_draft(
    environment: HostedHarnessJob, piece: str, *, lock: bool = False
) -> Any | None:
    """The piece's open draft, fresh or stale."""
    drafts = _model(piece).no_workspace_objects.filter(
        job_id=environment.id, status=VersionStatus.DRAFT
    )
    if lock:
        drafts = drafts.select_for_update()
    return drafts.first()


def is_stale(draft: Any, environment: HostedHarnessJob) -> bool:
    """A draft started from a default that has moved since."""
    return draft.parent_id != getattr(environment, f"{_piece_of(draft)}_version_id")


def fresh_draft(environment: HostedHarnessJob, piece: str, *, lock: bool = False):
    """The piece's open draft unless it is stale."""
    draft = open_draft(environment, piece, lock=lock)
    return draft if draft is not None and not is_stale(draft, environment) else None


def draft_refs(environment: HostedHarnessJob) -> dict[str, DraftRef]:
    """Each piece's fresh draft; a stale draft reads as no draft."""
    refs = {}
    for piece in DRAFT_PIECES:
        draft = fresh_draft(environment, piece)
        refs[piece] = (
            DraftRef(str(draft.id), draft.revision) if draft is not None else NO_DRAFT
        )
    return refs


def require_no_rebuild(
    environment: HostedHarnessJob, *, piece: str | None = None, owner: Any = None
):
    """Refuse an edit, a save or a default move while an update rebuilds the
    environment; ``owner``, the rebuild itself, may."""
    from simulate.services.harness_versioning.nudges import _IN_FLIGHT

    held = HarnessNudge.no_workspace_objects.filter(
        job_id=environment.id, state__in=_IN_FLIGHT
    )
    if owner is not None:
        held = held.exclude(id=owner.id)
    if held.exists():
        raise rebuild_running(piece)


def rebuild_running(piece: str | None) -> HostedHarnessError:
    return _error(
        "rebuild_running",
        "An update is running; try again when it finishes",
        409,
        piece=piece,
    )


# --- chat activity --------------------------------------------------------------------


def _conversations(environment: HostedHarnessJob):
    """The environment's chat and its runs' chats, which publish into it."""
    return HostedHarnessConversation.no_workspace_objects.filter(
        Q(job_id=environment.id) | Q(job__environment_id=environment.id)
    )


def _awaiting_answer(conversation: HostedHarnessConversation) -> bool:
    """The turn is paused on a question nobody has answered yet."""
    asked = (conversation.blocking_input or {}).get("message_id")
    return bool(
        conversation.state == HostedHarnessConversation.State.WAITING_FOR_USER
        and asked
        and not conversation.messages.filter(
            role=HostedHarnessConversationMessage.Role.USER, reply_to=asked
        ).exists()
    )


def _in_flight(
    conversation: HostedHarnessConversation, *, waiting_is_idle: bool = False
) -> bool:
    from simulate.services.hosted_harness_conversation import _unacked_commands

    running = bool(conversation.active_invocation_id) and not (
        waiting_is_idle and _awaiting_answer(conversation)
    )
    return running or _unacked_commands(conversation).exists()


def _publish_pending(conversation: HostedHarnessConversation, published: str) -> bool:
    """A turn finished a moment ago and its checkpoint is not published yet.

    A refused publish never stores its digest, so the wait is bounded.
    """
    events = HostedHarnessConversationEvent.no_workspace_objects.filter(
        conversation=conversation
    )
    turn = (
        events.filter(kind="turn_completed", payload__outcome="success")
        .order_by("-sequence")
        .first()
    )
    if turn is None or turn.created_at < timezone.now() - PUBLISH_GRACE:
        return False
    checkpoints = events.filter(
        kind="checkpoint_committed",
        sequence__lt=turn.sequence,
        payload__has_key="digest",
    )
    if turn.invocation_id:
        checkpoints = checkpoints.filter(invocation_id=turn.invocation_id)
    committed = (
        checkpoints.order_by("-sequence").values_list("payload", flat=True).first()
    )
    return committed is not None and str(committed.get("digest")) != published


def _busy_conversation(environment: HostedHarnessJob):
    published = ((environment.payload or {}).get("metadata") or {}).get(
        "conversation_checkpoints"
    ) or {}
    for conversation in _conversations(environment):
        digest = str((published.get(str(conversation.id)) or {}).get("digest") or "")
        # A question left unanswered would hold Save for good; once answered, its
        # turn publishes onto the moved drafts and is refused and reported.
        if _in_flight(conversation, waiting_is_idle=True) or _publish_pending(
            conversation, digest
        ):
            return conversation
    return None


def chat_busy(environment: HostedHarnessJob) -> bool:
    """Whether a chat turn is running, queued or still publishing into the environment."""
    return _busy_conversation(environment) is not None


def require_chat_idle(environment: HostedHarnessJob) -> None:
    conversation = _busy_conversation(environment)
    if conversation is not None:
        raise _error(
            "chat_working",
            "Chat is still working on this environment; save when it finishes",
            409,
            conversation_id=str(conversation.id),
        )


def retire_chat_runtime(
    environment: HostedHarnessJob,
    conversation: HostedHarnessConversation | None = None,
) -> int:
    """Expire the idle chat sandboxes of one conversation or of all the environment's,
    so the next turn starts from the current files. A running turn keeps its sandbox."""
    conversations = (
        HostedHarnessConversation.no_workspace_objects.filter(id=conversation.id)
        if conversation is not None
        else _conversations(environment)
    )
    idle = [one.id for one in conversations if not _in_flight(one)]
    if not idle:
        return 0
    return HostedHarnessConversationLease.no_workspace_objects.filter(
        conversation_id__in=idle,
        attempt__isnull=True,
        state__in=(
            HostedHarnessConversationLease.State.STARTING,
            HostedHarnessConversationLease.State.ACTIVE,
        ),
    ).update(
        state=HostedHarnessConversationLease.State.EXPIRED,
        updated_at=timezone.now(),
    )


# --- archives -----------------------------------------------------------------------


def _side(files: Mapping[str, bytes], piece: str) -> dict[str, bytes]:
    split = archives.split_authoring(files)
    return split.environment if piece == "environment" else split.suite


def _piece_files(environment: HostedHarnessJob, piece: str, version: Any):
    """A version's files; a backfilled version whose archive is gone reads the default
    pair's side instead."""
    try:
        return _side(
            archives.read_archive(archives.get_archive(version.archive_object_key)),
            piece,
        )
    except (S3Error, HostedHarnessError):
        key = str(
            ((environment.payload or {}).get("metadata") or {}).get(
                "authoring_object_key"
            )
            or ""
        )
        if not key:
            raise
        return _side(archives.read_archive(archives.get_archive(key)), piece)


def _sealed_files(key: str) -> dict[str, bytes] | None:
    try:
        files = archives.read_archive(archives.get_archive(key))
    except (S3Error, HostedHarnessError):
        return None
    return files if SEALED_MANIFEST in files else None


# What a chat reseal carries from the authoring tree into its bundle; ALK stamps their
# digest into the manifest as ``metadata.sealed_from``.
RESEAL_INPUTS = frozenset({"contract.json", "sub_goals.json", "world.sqlite"})
# The authoring world's own bookkeeping, which no v2 bundle carries.
UNBUNDLED = frozenset({"manifest.json", "world.py", "state.json"})


def sealed_from(files: Mapping[str, bytes]) -> str:
    """The digest ALK stamps for the reseal inputs among ``files``."""
    return canonical_digest(
        {
            path: archives.sha256_hex(data)
            for path, data in files.items()
            if (path in RESEAL_INPUTS or path.startswith("scenarios/"))
            and not {"__pycache__", "outbound-spool", "telemetry"}
            & set(path.split("/"))
        }
    )


def _sealed_metadata(split: archives.SplitArchive) -> dict[str, Any]:
    try:
        manifest = json.loads(split.sealed[SEALED_MANIFEST])
    except ValueError:
        return {}
    metadata = manifest.get("metadata") if isinstance(manifest, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _stamped(split: archives.SplitArchive) -> bool:
    """Whether the bundle says it was sealed from this tree's reseal inputs."""
    stamp = _sealed_metadata(split).get("sealed_from")
    return isinstance(stamp, str) and stamp == sealed_from(
        {**split.environment, **split.suite}
    )


def _resealed(
    head: HostedHarnessJob, split: archives.SplitArchive, hashes: tuple[str, str]
) -> bool:
    """Whether the chat sealed this bundle anew for ``hashes``.

    Only a bundle stamped with this tree's reseal inputs can be. A draft's bundle
    counts only as that draft recorded it for these hashes; any other must sit beside
    the default pair's other bundled files, with a manifest the default's is not (a
    reseal always rewrites it).
    """
    if not _stamped(split):
        return False
    manifest = archives.sha256_hex(split.sealed[SEALED_MANIFEST])
    for piece in DRAFT_PIECES:
        draft = fresh_draft(head, piece)
        kept = ((draft.documents or {}) if draft else {}).get("workspace_sealed") or {}
        if not kept:
            continue
        held = kept.get("manifest_sha256")
        if held is None:
            files = _sealed_files(str(kept.get("key") or ""))
            if files is None:
                # Unreadable: it may be this very bundle.
                return False
            held = archives.sha256_hex(files[SEALED_MANIFEST])
        if held == manifest:
            sealed_for = (kept.get("environment_hash"), kept.get("suite_hash"))
            return (
                bool(kept.get("resealed"))
                and sealed_for == hashes
                and _contract_carried(head, split)
            )
    default = _default_authoring(head)
    if default is None:
        return False
    # A reseal refreshes its inputs; every other file the bundle holds stays as built.
    if any(
        split.environment.get(path) != default.environment.get(path)
        for path in split.environment.keys() | default.environment.keys()
        if path not in RESEAL_INPUTS | UNBUNDLED
        and not hashing.is_environment_unhashed(path)
    ):
        return False
    if not _contract_carried(head, split, default):
        return False
    held = default.sealed.get(SEALED_MANIFEST)
    return held is None or archives.sha256_hex(held) != manifest


def _default_authoring(head: HostedHarnessJob) -> archives.SplitArchive | None:
    key = str(
        ((head.payload or {}).get("metadata") or {}).get("authoring_object_key") or ""
    )
    try:
        return archives.split_authoring(
            archives.read_archive(archives.get_archive(key))
        )
    except (S3Error, HostedHarnessError, ValueError):
        return None


def _contract_carried(
    head: HostedHarnessJob,
    split: archives.SplitArchive,
    default: archives.SplitArchive | None = None,
) -> bool:
    """Whether the bundle runs the tree's contract.json: the default pair's, or an edit
    the reseal compiled (tool handlers, traced tools); an older reseal copied the file
    alone."""
    contract = split.environment.get("contract.json")
    if contract is not None and _sealed_metadata(split).get(
        "contract_compiled"
    ) == archives.sha256_hex(contract):
        return True
    if default is None:
        default = _default_authoring(head)
    return default is not None and contract == default.environment.get("contract.json")


# --- writing drafts -----------------------------------------------------------------


def _token(document: Mapping[str, Any]) -> str:
    from simulate.services.hosted_harness_gateway import _scenario_token

    return _scenario_token(document.get("scenario_key") or document.get("name"))


def _exempt(draft: Any) -> set[str]:
    if draft is None:
        return set()
    return {str(one) for one in (draft.documents or {}).get("exempt_keys") or []}


def _refuse(
    locked: HostedHarnessJob,
    scenarios: list[dict[str, Any]],
    *,
    origin: str,
    exempt: set[str],
) -> None:
    """A person's edit may not reuse a dropped key or take another scenario's; a
    nudge's may not take one. Keys Extend placed in this draft are not refused again."""
    checked = [one for one in scenarios if _token(one) not in exempt]
    if origin in USER_ORIGINS:
        _refuse_retired_keys(locked, checked)
    if origin in USER_ORIGINS or origin == "nudge":
        _refuse_taken_keys(locked, checked)


def _extend_exempt(locked: HostedHarnessJob, scenarios: list[dict[str, Any]]) -> set:
    from simulate.services.hosted_harness_gateway import _scenario_token

    return {
        _scenario_token(key)
        for key in [*retired_keys(locked, scenarios), *taken_keys(locked, scenarios)]
    }


def _pairs(
    locked: HostedHarnessJob, scenarios: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Where each scenario lands at Save: its identity row, key and number."""
    from simulate.services.harness_scenarios import plan_pairs

    members = dict(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=locked.suite_version_id
        ).values_list("scenario_id", "content")
    )
    pairs = []
    for planned in plan_pairs(locked, scenarios, exact=True):
        row = planned.row
        if row is None or row.id not in members:
            change = "added"
        elif members[row.id] != _member_content(planned.doc):
            change = "changed"
        else:
            change = "unchanged"
        pairs.append(
            {
                "key": planned.key,
                "doc_key": str(
                    planned.doc.get("scenario_key") or planned.doc.get("name") or ""
                ),
                "number": planned.number,
                "row_id": str(row.id) if row is not None else None,
                "change": change,
            }
        )
    return pairs


def _content(
    locked: HostedHarnessJob,
    piece: str,
    prepared: Any,
    *,
    origin: str,
    default: Any,
) -> dict[str, Any]:
    """A draft row's content fields: archive, hash, documents and diff against the default."""
    if piece == "environment":
        diff, summary = environment_change(
            default, prepared.contract, prepared.environment_documents
        )
        return {
            **prepared.environment_fields,
            "documents": dict(prepared.environment_documents),
            "hash": prepared.environment_hash,
            "hash_kind": HashKind.TREE_V1,
            "summary": summary,
            "diff": diff,
            "agent_version_id": locked.agent_version_id,
        }
    diff, summary = suite_change(default, prepared.scenarios, origin)
    return {
        **prepared.suite_fields,
        "documents": dict(prepared.suite_documents),
        "hash": prepared.suite_hash,
        "hash_kind": HashKind.TREE_V1,
        "summary": summary,
        "diff": diff,
        "scenario_count": min(len(prepared.scenarios), SCENARIO_COUNT_MAX),
    }


def _prepared(environment: HostedHarnessJob, files: Mapping[str, bytes]):
    split = archives.split_authoring(files)
    if "contract.json" not in split.environment or not any(
        path.startswith(archives.SUITE_ROOTS) for path in split.suite
    ):
        raise HostedHarnessError(
            "authoring_incomplete",
            "the authoring archive has no contract or no scenarios to version",
            status_code=422,
        )
    pair_body = archives.pack_archive(archives.compose(split.environment, split.suite))
    prepared = prepare_pieces(
        environment, split, outputs=_stage_outputs(pair_body), authoring_revision=""
    )
    # A draft stands for no authoring revision until it is saved.
    prepared.environment_documents["authoring_revisions"] = []
    prepared.suite_documents["authoring_revisions"] = []
    return split, prepared


def _discard(draft: Any, *, user: Any = None, **documents: Any) -> None:
    draft.status = VersionStatus.DISCARDED
    draft.discard_reason = DiscardReason.USER
    fields = ["status", "discard_reason", "updated_at"]
    if user is not None:
        draft.last_edited_by = user
        fields.append("last_edited_by")
    if documents:
        draft.documents = {**(draft.documents or {}), **documents}
        fields.append("documents")
    draft.save(update_fields=fields)


def record_draft(
    environment: HostedHarnessJob,
    body: bytes,
    *,
    origin: str,
    user: Any = None,
    expected: Mapping[str, DraftRef] | None = None,
    pieces: set[str] | None = None,
    nudge: Any = None,
) -> DraftWrite | None:
    """Write an edited authoring tree into the environment's drafts.

    Each piece whose content differs from its default becomes, or updates, that
    piece's one draft; one equal to its default discards the draft. ``expected`` is the
    drafts the writer started from: another edit since refuses with ``draft_changed``.
    ``pieces`` limits the writes and the check (both by default); ``nudge`` tags a draft
    this call creates. ``None`` once drafts no longer apply: the caller saves as before.
    """
    from simulate.services.hosted_harness_conversation import (
        require_checkpoint_scenarios,
    )

    wanted = set(DRAFT_PIECES if pieces is None else pieces)
    split, prepared = _prepared(environment, archives.read_archive(body))
    require_checkpoint_scenarios(prepared.scenarios)
    hashes = {
        "environment": prepared.environment_hash,
        "suite": prepared.suite_hash,
    }
    if "suite" in wanted:
        # Refused before anything is uploaded; checked again under the lock below.
        _refuse(
            environment,
            prepared.scenarios,
            origin=origin,
            exempt=_exempt(fresh_draft(environment, "suite")),
        )
    bodies = {
        "environment": (prepared.environment_key, prepared.environment_body),
        "suite": (prepared.suite_key, prepared.suite_body),
    }
    head = HostedHarnessJob.no_workspace_objects.select_related(
        "environment_version", "suite_version"
    ).get(id=environment.id)
    stored: set[str] = set()
    for piece in wanted:
        default = getattr(head, f"{piece}_version")
        if default is None or default.hash != hashes[piece]:
            archives.put_archive(*bodies[piece])
            stored.add(piece)
    workspace_sealed = None
    if SEALED_MANIFEST in split.sealed:
        sealed_body = archives.pack_archive(split.sealed)
        sealed_key = archives.sealed_key(
            environment.organization_id, environment.id, sealed_body
        )
        archives.put_archive(sealed_key, sealed_body)
        kept = ((head.payload or {}).get("metadata") or {}).get(
            "sealed_bundle_object_key"
        )
        workspace_sealed = {
            "key": sealed_key,
            "sha256": archives.sha256_hex(sealed_body),
            "size": len(sealed_body),
            "environment_hash": prepared.environment_hash,
            "suite_hash": prepared.suite_hash,
            "manifest_sha256": archives.sha256_hex(split.sealed[SEALED_MANIFEST]),
            # The key is content-addressed: the same key is the default's own bundle.
            "resealed": sealed_key != kept
            and _resealed(
                head, split, (prepared.environment_hash, prepared.suite_hash)
            ),
        }

    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=environment.id
        )
        if not drafts_apply(locked):
            return None
        if expected is not None:
            current = draft_refs(locked)
            for piece in DRAFT_PIECES:
                if piece in wanted and expected.get(piece, NO_DRAFT) != current[piece]:
                    logger.info(
                        "harness_draft_changed environment=%s piece=%s origin=%s",
                        locked.id,
                        piece,
                        origin,
                    )
                    raise draft_changed(piece=piece)
        drafts = {piece: open_draft(locked, piece, lock=True) for piece in DRAFT_PIECES}
        suite_draft = drafts["suite"]
        if suite_draft is not None and is_stale(suite_draft, locked):
            suite_draft = None
        exempt = _exempt(suite_draft)
        if "suite" in wanted:
            _refuse(locked, prepared.scenarios, origin=origin, exempt=exempt)
            if origin == "extend":
                exempt |= _extend_exempt(locked, prepared.scenarios)
        rows: dict[str, Any] = {}
        written: set[str] = set()
        discarded: set[str] = set()
        for piece in DRAFT_PIECES:
            draft = drafts[piece]
            fresh = draft is not None and not is_stale(draft, locked)
            rows[piece] = draft if fresh else None
            if piece not in wanted:
                continue
            default = getattr(locked, f"{piece}_version")
            if default.hash == hashes[piece] and default.hash_kind == HashKind.TREE_V1:
                if fresh:
                    _discard(draft, user=user, discarded_by=origin)
                    discarded.add(piece)
                    rows[piece] = None
                    logger.info(
                        "harness_draft_discarded environment=%s piece=%s "
                        "reason=equals_default",
                        locked.id,
                        piece,
                    )
                continue
            if fresh and draft.hash == hashes[piece]:
                continue
            if piece not in stored:
                archives.put_archive(*bodies[piece])
            values = _content(locked, piece, prepared, origin=origin, default=default)
            if piece == "suite":
                values["documents"]["pairs"] = _pairs(locked, prepared.scenarios)
                values["documents"]["exempt_keys"] = sorted(exempt)
            if workspace_sealed is not None:
                values["documents"]["workspace_sealed"] = workspace_sealed
            replaced = draft if draft is not None and not fresh else None
            if replaced is not None:
                # A newer edit replaces changes made on an older default (kept in history).
                _discard(replaced)
                draft = None
            if draft is None:
                draft = _model(piece).no_workspace_objects.create(
                    job=locked,
                    organization_id=locked.organization_id,
                    workspace_id=locked.workspace_id,
                    status=VersionStatus.DRAFT,
                    parent=default,
                    origin=origin,
                    revision=1,
                    created_by=user,
                    last_edited_by=user,
                    nudge=nudge,
                    **values,
                )
            else:
                for name, value in values.items():
                    setattr(draft, name, value)
                draft.origin = origin
                draft.revision += 1
                draft.last_edited_by = user
                draft.save()
            if replaced is not None:
                _discard(replaced, replaced_by=str(draft.id))
                logger.info(
                    "harness_draft_stale environment=%s piece=%s replaced=true",
                    locked.id,
                    piece,
                )
            rows[piece] = draft
            written.add(piece)
            logger.info(
                "harness_draft_written environment=%s piece=%s origin=%s draft=%s "
                "revision=%s",
                locked.id,
                piece,
                origin,
                draft.id,
                draft.revision,
            )
    return DraftWrite(
        environment=rows["environment"],
        suite=rows["suite"],
        written=frozenset(written),
        discarded=frozenset(discarded),
    )


def write_suite_edit(
    environment: HostedHarnessJob, suite: list[dict[str, Any]], *, user: Any
) -> DraftWrite | None:
    """A Scenarios-tab edit, written into the suite draft on top of the unsaved changes."""
    from simulate.services.hosted_harness_gateway import (
        AuthoringArchiveKept,
        _rewritten_authoring_archive,
    )

    head = HostedHarnessJob.no_workspace_objects.select_related(
        "environment_version", "suite_version"
    ).get(id=environment.id)
    sides = {}
    for piece in DRAFT_PIECES:
        version = fresh_draft(head, piece) or getattr(head, f"{piece}_version")
        sides[piece] = _piece_files(head, piece, version)
    rewritten = _rewritten_authoring_archive(
        archives.pack_archive(sides["suite"]), suite
    )
    if rewritten is None:
        logger.error(
            "harness_amend_archive_kept job=%s: the rewrite would lose files a run needs",
            environment.id,
        )
        raise AuthoringArchiveKept("the change would lose files a run needs")
    body = archives.pack_archive(
        archives.compose(sides["environment"], archives.read_archive(rewritten))
    )
    write = record_draft(
        environment, body, origin="manual", user=user, pieces={"suite"}
    )
    transaction.on_commit(lambda: retire_chat_runtime(environment))
    return write


# --- composing a chat workspace -------------------------------------------------------


def compose_workspace(
    environment: HostedHarnessJob,
    conversation: HostedHarnessConversation,
    *,
    only_draft: str | None = None,
) -> ComposedWorkspace | None:
    """The chat's working files: each piece's fresh draft over the other's default.

    ``only_draft`` overlays that piece's draft only. The drafts it read are recorded for
    the conversation under a new token, which the workspace carries back on publish.
    """
    overlaid = DRAFT_PIECES if only_draft is None else (only_draft,)
    with transaction.atomic():
        locked = (
            HostedHarnessJob.no_workspace_objects.select_for_update(of=("self",))
            .select_related("environment_version", "suite_version")
            .get(id=environment.id)
        )
        if not drafts_apply(locked):
            return None
        effective = {}
        refs = {}
        stale = {}
        drafts = {}
        for piece in DRAFT_PIECES:
            draft = open_draft(locked, piece)
            fresh = draft is not None and not is_stale(draft, locked)
            stale[piece] = draft is not None and not fresh
            if fresh and piece in overlaid:
                drafts[piece] = draft
                effective[piece] = draft
                refs[piece] = DraftRef(str(draft.id), draft.revision)
            else:
                effective[piece] = getattr(locked, f"{piece}_version")
                if piece in overlaid:
                    refs[piece] = NO_DRAFT
        token = uuid.uuid4().hex
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        checkpoints = dict(metadata.get("conversation_checkpoints") or {})
        entry = dict(checkpoints.get(str(conversation.id)) or {})
        entry["draft_basis"] = draft_basis(token, refs)
        checkpoints[str(conversation.id)] = entry
        metadata["conversation_checkpoints"] = checkpoints
        payload["metadata"] = metadata
        locked.payload = payload
        locked.save(update_fields=["payload", "updated_at"])
    files = {
        piece: _piece_files(locked, piece, version)
        for piece, version in effective.items()
    }
    sealed = None
    for draft in drafts.values():
        kept = (draft.documents or {}).get("workspace_sealed") or {}
        if (kept.get("environment_hash"), kept.get("suite_hash")) == (
            effective["environment"].hash,
            effective["suite"].hash,
        ):
            sealed = _sealed_files(str(kept.get("key") or ""))
            if sealed is not None:
                break
    if sealed is None and metadata.get("default_pair_sealed") is not False:
        key = str(metadata.get("authoring_object_key") or "")
        if key:
            sealed = archives.split_authoring(
                archives.read_archive(archives.get_archive(key))
            ).sealed
    from simulate.services.harness_versioning.versions import suite_documents

    return ComposedWorkspace(
        files=archives.compose(files["environment"], files["suite"], sealed),
        scenario_count=len(suite_documents(files["suite"])),
        refs=refs,
        token=token,
        stale=stale,
    )


def draft_basis(token: str, refs: Mapping[str, DraftRef]) -> dict[str, Any]:
    """The drafts a composed workspace holds, as its conversation records them."""
    return {
        "token": token,
        **{
            piece: {"id": ref.id, "revision": ref.revision}
            for piece, ref in refs.items()
        },
    }


def expected_for(
    published: Mapping[str, Any] | None, token: str
) -> dict[str, DraftRef] | None:
    """The drafts a chat checkpoint was made on, from the token its workspace carries.

    No token: the workspace predates drafts, so it was made on none. A token the server
    no longer holds for this conversation: an outdated composition (``None``).
    """
    if not token:
        return dict.fromkeys(DRAFT_PIECES, NO_DRAFT)
    basis = (published or {}).get("draft_basis") or {}
    if basis.get("token") != token:
        return None
    return {
        piece: DraftRef(basis[piece].get("id"), int(basis[piece].get("revision") or 0))
        for piece in DRAFT_PIECES
        if isinstance(basis.get(piece), dict)
    }


def draft_changed(**details: Any) -> HostedHarnessError:
    return _error(
        "draft_changed",
        "the unsaved changes were edited elsewhere while this chat was working; ask "
        "again to make the change on top of them",
        409,
        **details,
    )


# --- Save, Discard and the step-14 helpers --------------------------------------------


def _twin(model: Any, locked: HostedHarnessJob, draft: Any) -> Any | None:
    return (
        model.no_workspace_objects.filter(
            job=locked,
            hash=draft.hash,
            hash_kind=draft.hash_kind,
            status=VersionStatus.ACTIVE,
        )
        .order_by("-version_number")
        .first()
    )


def _not_runnable(piece: str, *, agentless: bool = False) -> HostedHarnessError:
    # Without the agent's runtime facts a run can't prepare the environment itself.
    remedy = (
        "Discard them and make them in chat instead, which prepares them as it saves"
        if agentless
        else "Ask chat to save them again, or turn on bundle preparation"
    )
    return _error(
        "draft_not_runnable",
        "These changes can't be made live yet: the environment hasn't been prepared "
        f"for them. {remedy}",
        422,
        piece=piece,
    )


def _composed_pair(
    locked: HostedHarnessJob,
    environment_version: Any,
    suite_version: Any,
    sealed: Mapping[str, bytes],
) -> _Pair | None:
    from simulate.services.hosted_harness_gateway import authoring_content_digest

    try:
        tree = archives.compose(
            _piece_files(locked, "environment", environment_version),
            _piece_files(locked, "suite", suite_version),
            sealed,
        )
    except (S3Error, HostedHarnessError):
        return None
    sealed_body = archives.pack_archive(sealed)
    sealed_key = archives.sealed_key(locked.organization_id, locked.id, sealed_body)
    archives.put_archive(sealed_key, sealed_body)
    body = archives.pack_archive(tree)
    key = archives.pair_key(locked.organization_id, locked.id, body)
    archives.put_archive(key, body)
    return _Pair(
        key=key,
        revision=authoring_content_digest(body),
        sealed=True,
        sealed_key=sealed_key,
    )


def _save_pair(
    locked: HostedHarnessJob,
    metadata: Mapping[str, Any],
    environment_version: Any,
    suite_version: Any,
    drafts: list[Any],
    *,
    piece: str,
    agent_moved: bool = False,
) -> _Pair:
    """The runnable archive of the pair a Save makes the default.

    The chat's own bundle when it resealed it for exactly this pair, else the current
    pair's bundle when the Save changes nothing it seals, else the roll back ladder. A
    run replays the bundle and lays only overlay values over it, so any other change
    takes the ladder. A bundle is sealed for one agent, so a Save that moves the agent
    takes the ladder too.
    """
    if not agent_moved:
        for draft in drafts:
            kept = (draft.documents or {}).get("workspace_sealed") or {}
            if not kept.get("resealed") or (
                kept.get("environment_hash"),
                kept.get("suite_hash"),
            ) != (environment_version.hash, suite_version.hash):
                continue
            sealed = _sealed_files(str(kept.get("key") or ""))
            pair = sealed and _composed_pair(
                locked, environment_version, suite_version, sealed
            )
            if pair:
                return pair
        current = str(metadata.get("authoring_object_key") or "")
        default_suite = locked.suite_version
        if (
            current
            and metadata.get("default_pair_sealed") is not False
            and environment_version.id == locked.environment_version_id
            and default_suite is not None
            and bool(suite_version.certify_hash)
            and suite_version.certify_hash == default_suite.certify_hash
        ):
            try:
                sealed = _sealed(
                    archives.split_authoring(
                        archives.read_archive(archives.get_archive(current))
                    ),
                    metadata,
                )
            except (S3Error, HostedHarnessError):
                sealed = {}
            if SEALED_MANIFEST in sealed:
                pair = _composed_pair(
                    locked, environment_version, suite_version, sealed
                )
                if pair is not None:
                    return pair
    try:
        return _default_pair(
            locked, metadata, environment_version, suite_version, piece
        )
    except HostedHarnessError as exc:
        if exc.code != "rollback_unavailable":
            raise
        raise _not_runnable(piece, agentless=_default_runtime(locked) is None) from exc


def activate_drafts(
    locked: HostedHarnessJob,
    pieces: set[str],
    *,
    origin: str | None,
    user: Any,
    agent_version: Any = None,
    nudge: Any = None,
    hold_owner: Any = None,
) -> dict[str, tuple[Any, bool]]:
    """Make the locked environment's open drafts of ``pieces`` its defaults.

    A draft identical to an active version reuses that version. Returns each piece's
    new default and whether it was created. With ``agent_version`` the agent moves in
    the same default move. The caller holds the environment lock and has checked the
    drafts are fresh.
    """
    from simulate.services.harness_agent_versions import fill_agent_pin

    require_no_rebuild(locked, piece=next(iter(sorted(pieces))), owner=hold_owner)
    drafts = {}
    for piece in DRAFT_PIECES:
        if piece in pieces:
            draft = open_draft(locked, piece, lock=True)
            if draft is None:
                raise _error("draft_not_found", "No unsaved changes", 404, piece=piece)
            drafts[piece] = draft
    current = {piece: getattr(locked, f"{piece}_version") for piece in DRAFT_PIECES}
    twins = {
        piece: _twin(_model(piece), locked, draft) for piece, draft in drafts.items()
    }
    targets = {
        piece: twins.get(piece) or drafts.get(piece) or current[piece]
        for piece in DRAFT_PIECES
    }
    if agent_version is None:
        # A save made before its environment was pinned is the pin's version.
        fill_agent_pin(locked)
    outgoing_agent = locked.agent_version_id
    agent_moved = agent_version is not None and agent_version.id != outgoing_agent
    if agent_moved:
        locked.agent_version = agent_version
    payload = dict(locked.payload or {})
    metadata = dict(payload.get("metadata") or {})
    pair = _save_pair(
        locked,
        metadata,
        targets["environment"],
        targets["suite"],
        list(drafts.values()),
        piece=next(iter(drafts)),
        agent_moved=agent_moved,
    )
    result: dict[str, tuple[Any, bool]] = {}
    for piece, draft in drafts.items():
        twin = twins[piece]
        if twin is not None:
            _discard(draft, user=user, reused_version_id=str(twin.id))
            result[piece] = (twin, False)
            continue
        default = current[piece]
        documents = dict(draft.documents or {})
        if piece == "environment":
            diff, draft.summary = environment_change(
                default, documents.get("contract") or {}, documents
            )
            draft.agent_version_id = (
                agent_version.id if agent_version is not None else outgoing_agent
            )
        else:
            diff, draft.summary = suite_change(
                default, documents.get("scenarios") or [], origin or draft.origin
            )
            draft.authored_against = targets["environment"]
        draft.diff = diff
        draft.parent = default
        draft.status = VersionStatus.ACTIVE
        draft.version_number = _next_number(_model(piece), locked)
        draft.origin = origin or draft.origin
        if user is not None:
            draft.created_by = user
        if nudge is not None:
            draft.nudge = nudge
        draft.save()
        result[piece] = (draft, True)
    for version in (targets["environment"], targets["suite"]):
        note_revision(version, pair.revision)
    suite, suite_created = result.get("suite", (None, False))
    if suite_created:
        # Paired while the old suite is still the default, as a recorded save pairs.
        _add_members(
            suite,
            _member_rows(locked, (suite.documents or {}).get("scenarios") or []),
        )
    moved = {
        piece
        for piece in DRAFT_PIECES
        if targets[piece].id != getattr(current[piece], "id", None)
    }
    set_defaults(
        locked,
        payload,
        metadata,
        targets["environment"],
        targets["suite"],
        pair,
        pieces=moved,
        outgoing_agent_version_id=outgoing_agent,
    )
    return result


def _replayed(locked: HostedHarnessJob, saved: Mapping[str, Any]) -> dict[str, Any]:
    return {
        piece: _model(piece)._base_manager.get(job=locked, id=version_id)
        for piece, version_id in saved.items()
    }


def _saved_body(locked: HostedHarnessJob, saved: Mapping[str, Any]) -> dict[str, Any]:
    from simulate.services.harness_versioning import read

    return {
        "saved": {
            piece: read._piece_item(
                version,
                getattr(locked, f"{piece}_version_id"),
                suite=piece == "suite",
            )
            for piece, version in saved.items()
        },
        "versions": read.list_versions(locked),
    }


def save_drafts(
    environment: HostedHarnessJob,
    requests: Mapping[str, SaveRequest],
    *,
    idempotency_key: str,
    user: Any,
) -> dict[str, Any]:
    """Make every requested draft live together; the same key repeats the same Save."""
    pieces = set(requests)
    with transaction.atomic():
        locked = _lock_environment(environment)
        first = next(piece for piece in DRAFT_PIECES if piece in pieces)
        require_not_busy(locked, first)
        require_no_rebuild(locked, piece=first)
        require_chat_idle(locked)
        metadata = (locked.payload or {}).get("metadata") or {}
        earlier = (metadata.get("draft_saves") or {}).get(idempotency_key)
        if isinstance(earlier, dict):
            if set(earlier.get("pieces") or {}) != pieces:
                raise HostedHarnessError(
                    "idempotency_key_reused",
                    "This request key was already used for another save; save again",
                    status_code=409,
                )
            return _saved_body(locked, _replayed(locked, earlier["pieces"]))
        for piece in DRAFT_PIECES:
            if piece not in pieces:
                continue
            draft = open_draft(locked, piece, lock=True)
            if draft is None:
                raise _error("draft_not_found", "No unsaved changes", 404, piece=piece)
            if is_stale(draft, locked):
                logger.info(
                    "harness_draft_stale environment=%s piece=%s replaced=false",
                    locked.id,
                    piece,
                )
                raise _error(
                    "draft_stale",
                    "The environment changed since you started editing; discard "
                    "or re-apply in chat",
                    409,
                    piece=piece,
                )
            request = requests[piece]
            if str(request.expected_parent_id or "") != str(draft.parent_id or "") or (
                request.expected_revision is not None
                and request.expected_revision != draft.revision
            ):
                raise _error(
                    "draft_changed",
                    "Someone changed these edits a moment ago; review them and save "
                    "again",
                    409,
                    piece=piece,
                )
            if piece == "suite":
                _refuse(
                    locked,
                    (draft.documents or {}).get("scenarios") or [],
                    origin="manual",
                    exempt=_exempt(draft),
                )
        drafts = {
            piece: open_draft(locked, piece).id
            for piece in DRAFT_PIECES
            if piece in pieces
        }
        before = {piece: getattr(locked, f"{piece}_version_id") for piece in pieces}
        activated = activate_drafts(locked, pieces, origin=None, user=user)
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        saves = {
            key: entry
            for key, entry in (metadata.get("draft_saves") or {}).items()
            if isinstance(entry, dict)
        }
        saves[idempotency_key] = {
            "pieces": {
                piece: str(version.id) for piece, (version, _) in activated.items()
            },
            "at": timezone.now().isoformat(),
        }
        newest = sorted(saves.items(), key=lambda item: str(item[1].get("at") or ""))
        metadata["draft_saves"] = dict(newest[-DRAFT_SAVES_KEPT:])
        payload["metadata"] = metadata
        locked.payload = payload
        locked.save(update_fields=["payload", "updated_at"])
        transaction.on_commit(lambda: retire_chat_runtime(locked))
    actor = getattr(user, "id", None)
    for piece, (version, created) in activated.items():
        logger.info(
            "harness_draft_saved environment=%s piece=%s draft=%s version=%s reused=%s",
            locked.id,
            piece,
            drafts[piece],
            version.id,
            not created,
        )
        if created:
            logger.info(
                "harness_version_created environment=%s piece=%s version=%s",
                locked.id,
                piece,
                version.id,
            )
        if before[piece] != version.id:
            logger.info(
                "harness_default_moved piece=%s from=%s to=%s actor=%s",
                piece,
                before[piece],
                version.id,
                actor,
            )
    return _saved_body(locked, {piece: one for piece, (one, _) in activated.items()})


def discard_draft(environment: HostedHarnessJob, piece: str, *, user: Any) -> bool:
    """Set the piece's open draft aside; ``False`` when there was none."""
    with transaction.atomic():
        locked = _lock_environment(environment)
        require_no_rebuild(locked, piece=piece)
        require_chat_idle(locked)
        draft = open_draft(locked, piece, lock=True)
        if draft is None:
            return False
        _discard(draft, user=user)
        transaction.on_commit(lambda: retire_chat_runtime(locked))
    logger.info(
        "harness_draft_discarded environment=%s piece=%s reason=user", locked.id, piece
    )
    return True


def restore_draft(
    locked: HostedHarnessJob,
    piece: str,
    *,
    draft_id: Any,
    archive_object_key: str,
    revision: int,
    last_edited_by_id: Any,
    origin: str | None = None,
) -> Any:
    """Bring a set-aside draft back from its archive; the caller holds the lock. Its
    revision moves past both, so no workspace composed on either still matches."""
    draft = (
        _model(piece)
        ._base_manager.select_for_update()
        .get(job=locked, id=draft_id, deleted=False)
    )
    draft.origin = origin or draft.origin
    default = getattr(locked, f"{piece}_version")
    head = HostedHarnessJob.no_workspace_objects.get(id=locked.id)
    files = archives.read_archive(archives.get_archive(archive_object_key))
    other = "suite" if piece == "environment" else "environment"
    sides = {
        piece: _side(files, piece),
        other: _piece_files(head, other, getattr(head, f"{other}_version")),
    }
    tree = archives.compose(sides["environment"], sides["suite"])
    _split, prepared = _prepared(locked, tree)
    values = _content(
        locked, piece, prepared, origin=draft.origin, default=draft.parent or default
    )
    if piece == "suite":
        values["documents"]["pairs"] = _pairs(locked, prepared.scenarios)
        values["documents"]["exempt_keys"] = sorted(_exempt(draft))
    for name, value in values.items():
        setattr(draft, name, value)
    draft.status = VersionStatus.DRAFT
    draft.discard_reason = None
    draft.revision = max(draft.revision, revision) + 1
    draft.last_edited_by_id = last_edited_by_id
    draft.save()
    return draft


# --- reads ----------------------------------------------------------------------------


def _changes(draft: Any, piece: str) -> dict[str, int]:
    diff = draft.diff if isinstance(draft.diff, dict) else {}

    def size(*values: Any) -> int:
        return sum(len(one) for one in values if isinstance(one, (list, dict)))

    if piece == "suite":
        return {
            "added": size(diff.get("added")),
            "removed": size(diff.get("removed")),
            "changed": size(diff.get("changed")),
        }
    contract = diff.get("contract") or {}
    tools = contract.get("tools") or {}
    rules = contract.get("rules") or {}
    sub_goals = diff.get("sub_goals") or {}
    files = diff.get("files") or {}
    return {
        "tools": size(tools.get("added"), tools.get("removed"), tools.get("changed")),
        "rules": size(rules.get("added"), rules.get("removed")),
        "sub_goals": size(
            sub_goals.get("added"), sub_goals.get("removed"), sub_goals.get("changed")
        ),
        "world_files": size(
            files.get("added"), files.get("removed"), files.get("changed")
        ),
    }


def _draft_item(draft: Any, environment: HostedHarnessJob) -> dict[str, Any]:
    from simulate.services.harness_versioning import read

    piece = _piece_of(draft)
    default = getattr(environment, f"{piece}_version")
    return {
        **read._piece_item(
            draft,
            getattr(environment, f"{piece}_version_id"),
            suite=piece == "suite",
        ),
        "stale": is_stale(draft, environment),
        "revision": draft.revision,
        "last_edited_by": read._person(draft.last_edited_by),
        "updated_at": draft.updated_at.isoformat(),
        "parent_label": read._label(getattr(draft.parent, "version_number", None)),
        "default_label": read._label(getattr(default, "version_number", None)),
        "changes": _changes(draft, piece),
    }


def list_drafts(environment: HostedHarnessJob) -> dict[str, Any]:
    """The open drafts the Save bar shows, and drafts an update set aside."""
    environment = HostedHarnessJob.no_workspace_objects.select_related(
        "environment_version", "suite_version"
    ).get(id=environment.id)
    related = ("created_by", "last_edited_by", "parent")
    body: dict[str, Any] = {}
    set_aside = []
    for piece in DRAFT_PIECES:
        rows = _model(piece).no_workspace_objects.filter(job=environment)
        draft = rows.filter(status=VersionStatus.DRAFT).select_related(*related).first()
        body[piece] = _draft_item(draft, environment) if draft is not None else None
        set_aside.extend(
            rows.filter(
                status=VersionStatus.DISCARDED,
                discard_reason=DiscardReason.SET_ASIDE_BY_REBUILD,
            )
            .select_related(*related)
            .order_by("-updated_at")[:SET_ASIDE_SHOWN]
        )
    set_aside.sort(key=lambda one: one.updated_at, reverse=True)
    body["set_aside"] = [
        _draft_item(one, environment) for one in set_aside[:SET_ASIDE_SHOWN]
    ]
    return body


def draft_rows(environment: HostedHarnessJob):
    """The suite draft's scenarios as list rows, numbered as Save will number them;
    ``None`` without a fresh suite draft or once drafts no longer apply."""
    from simulate.services.harness_scenarios import (
        ScenarioRows,
        as_member,
        scenario_fields,
    )

    if not drafts_apply(environment):
        return None
    head = environment_head(environment)
    draft = fresh_draft(head, "suite")
    if draft is None:
        return None
    documents = draft.documents or {}
    by_token = {_token(one): one for one in documents.get("scenarios") or []}
    rows = {
        str(row.id): row
        for row in HostedHarnessScenario.all_objects.filter(
            job_id=environment.id
        ).select_related("scenario", "call_execution")
    }
    shown = ScenarioRows()
    for pair in documents.get("pairs") or []:
        document = by_token.get(_token({"scenario_key": pair.get("doc_key")}))
        if document is None:
            continue
        row = rows.get(str(pair.get("row_id") or ""))
        if row is not None:
            one = as_member(row, document)
            one.number = pair["number"]
        else:
            one = HostedHarnessScenario(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"{draft.id}:{pair['key']}"),
                job_id=environment.id,
                scenario_key=pair["key"],
                number=pair["number"],
                **scenario_fields(document, pair["key"]),
            )
        one.draft_change = pair.get("change")
        shown.append(one)
    shown.draft = {"id": str(draft.id), "revision": draft.revision}
    return shown


def draft_changes(environment: HostedHarnessJob) -> dict[str, Any]:
    """What the unsaved scenarios add, drop and change against the default, by number."""
    rows = draft_rows(environment)
    head = environment_head(environment)
    if rows is None:
        return {
            "scenario_count": HostedHarnessScenario.no_workspace_objects.filter(
                job_id=environment.id
            ).count(),
            "added": [],
            "removed": [],
            "changed": [],
        }
    kept = {
        str(row.id)
        for row in rows
        if getattr(row, "draft_change", None) in ("changed", "unchanged")
    }
    members = HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id=head.suite_version_id
    ).values_list("scenario_id", "number", "scenario_key", "content")

    def named(row: Any) -> dict[str, Any]:
        return {"number": row.number, "name": row.name or row.scenario_key}

    return {
        "scenario_count": len(rows),
        "added": [named(row) for row in rows if row.draft_change == "added"],
        "removed": sorted(
            (
                {
                    "number": number,
                    "name": (content.get("name") if isinstance(content, dict) else None)
                    or key,
                }
                for scenario_id, number, key, content in members
                if str(scenario_id) not in kept
            ),
            key=lambda one: one["number"] or 0,
        ),
        "changed": [named(row) for row in rows if row.draft_change == "changed"],
    }


def any_fresh_draft(environment: HostedHarnessJob) -> bool:
    if not drafts_apply(environment):
        return False
    head = environment_head(environment)
    return any(ref.id is not None for ref in draft_refs(head).values())


def command_author(payload: Mapping[str, Any], environment: HostedHarnessJob) -> Any:
    """The person a server-stamped command came from, if a member of the
    environment's organization."""
    author_id = (payload or {}).get("_author_id")
    if not author_id:
        return None
    try:
        author = get_user_model()._default_manager.filter(id=author_id).first()
    except (ValidationError, ValueError):
        return None
    if author is None or author.get_membership_level(environment.organization) is None:
        return None
    return author
