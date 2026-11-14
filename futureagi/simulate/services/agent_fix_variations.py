"""The first step of a fix whose run had too few failing cases: the environment's builder
writes variations of each failing case into a suite version private to the fix, one run of
the base version on them keeps the variations it fails, and the fix goes on with those as
targets. The engine never sees this step; its brief holds the result.

AgentFixWorkflow calls ``advance`` until the step is over. The builder's save reaches
``land``, never the environment's drafts or defaults.
"""

import json
import re
import uuid
from collections.abc import Mapping
from datetime import timedelta
from functools import partial
from pathlib import PurePosixPath
from typing import Any

import structlog
from django.conf import settings
from django.db import InterfaceError, OperationalError, transaction
from django.utils import timezone
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from simulate.models import (
    AgentFixRun,
    HarnessSuiteMember,
    HarnessSuiteVersion,
    HostedHarnessConversation,
    HostedHarnessConversationEvent,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    HostedHarnessScenario,
)
from simulate.models.harness_versioning import HashKind, VersionOrigin, VersionStatus
from simulate.pydantic_schemas.agent_fix import (
    VARIATIONS_PER_CASE,
    CheckRunRequest,
    FixRequest,
    FixStep,
)
from simulate.selectors.agent_fix import (
    FINISHED_RUN_STATES,
    VARIATIONS_LABEL,
    scenario_results,
    variations_minutes,
    variations_source_moved,
    written_variations,
)
from simulate.services.agent_fix_engine import start_check_run
from simulate.services.agent_fix_runs import close_fix_run, split_held_back
from simulate.services.agent_fix_sandbox import claim
from simulate.services.harness_scenarios import pair_scenarios
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.rebuild import hand_back_chat
from simulate.services.harness_versioning.versions import _add_members, suite_documents
from simulate.services.hosted_harness import HostedHarnessError
from simulate.services.hosted_harness_conversation import (
    enqueue_message,
    require_checkpoint_scenarios,
)
from simulate.services.hosted_harness_gateway import _scenario_token
from simulate.services.hosted_harness_ingress import _public_base_url
from simulate.temporal.constants import AGENT_FIX_VARIATIONS_WRITE_MINUTES
from simulate.temporal.types.agent_fix import Observation, VariationsStep

logger = structlog.get_logger(__name__)

# What the builder message is from, in the platform's part of its payload.
FIX_ORIGIN = "fix"
# A variation's key as the builder is asked to write it, compared as a token. A builder
# that finds the key taken writes it with a suffix ("-var1-2").
_VARIATION_TOKEN = re.compile(r"(?P<parent>.+)_var(?P<number>[0-9]+)(?:_[0-9]+)?")
_CLAIMED = {
    Observation.WAITING: VariationsStep.WAITING,
    Observation.ENDED: VariationsStep.ENDED,
    Observation.STOPPING: VariationsStep.STOPPING,
}
# How long after the builder's turn ends its save may take to land.
_LANDING_GRACE = timedelta(minutes=3)
# What a later look can outlive: the database or object storage briefly unavailable.
_TRANSIENT = (OperationalError, InterfaceError, S3Error, HTTPError)


def _without_variations(
    parents: Mapping[str, list[str]], variations: Mapping[str, str]
) -> list[str]:
    """The failing cases the builder wrote no usable variation of."""
    varied = set(variations.values())
    return [key for key in parents if key not in varied]


def is_fix_command(command: HostedHarnessConversationMessage | None) -> bool:
    """Whether a builder turn was a fix's request for variations."""
    return command is not None and (command.payload or {}).get("_origin") == FIX_ORIGIN


def failing_targets(fix_run: AgentFixRun) -> dict[str, list[str]]:
    """The fix's targets that failed in its source run, with the sub-goals that did not
    hold: the cases its variations vary."""
    request = FixRequest.model_validate(fix_run.request)
    ran = {
        one.key: one.failing_sub_goals for one in scenario_results(fix_run.source_run)
    }
    return {key: ran[key] for key in request.target_scenario_keys if ran.get(key)}


def advance(fix_run_id: str) -> VariationsStep:
    """One look at a fix's first step, which also takes it as far as it can go now. Each
    part is done once and found done on the next look, so a look is safe to repeat.
    Raises only what a later look can outlive; any other error ends the fix as failed,
    since looking again would hit it again."""
    try:
        return _advance(fix_run_id)
    except Exception as exc:
        if isinstance(exc, _TRANSIENT) or (
            isinstance(exc, HostedHarnessError) and exc.retryable
        ):
            raise
        logger.exception("agent_fix_variations_failed", fix_run=str(fix_run_id))
        close_fix_run(
            fix_run_id,
            AgentFixRun.State.FAILED,
            failure={
                "code": "fix_variations_failed",
                "message": "the platform could not write variations of the failing"
                " cases",
            },
        )
        return VariationsStep.ENDED


def step_minutes(fix_run_id: str, step: VariationsStep) -> int:
    """How long the first step may have taken by a look that found it at ``step``: the
    builder's cap while it writes, then the whole step's time (variations_minutes); 0 at
    any other step, which has no cap."""
    if step == VariationsStep.WRITING:
        return AGENT_FIX_VARIATIONS_WRITE_MINUTES
    if step != VariationsStep.CHECKING:
        return 0
    return variations_minutes(
        AgentFixRun.no_workspace_objects.select_related("environment").get(
            id=fix_run_id
        )
    )


def _advance(fix_run_id: str) -> VariationsStep:
    fix_run = AgentFixRun.no_workspace_objects.get(id=fix_run_id)
    if not FixRequest.model_validate(fix_run.request).variations_pending:
        return VariationsStep.DONE
    if fix_run.state != AgentFixRun.State.RUNNING:
        # Room under the running-fix caps, or why there is none.
        refusal = claim(fix_run_id)
        if refusal is not None:
            return _CLAIMED[refusal]
        fix_run.refresh_from_db()
    if fix_run.step != FixStep.VARIATIONS:
        AgentFixRun.no_workspace_objects.filter(id=fix_run.id).update(
            step=FixStep.VARIATIONS, updated_at=timezone.now()
        )
    if fix_run.suite_version_id is None:
        return _write(fix_run)
    variations = written_variations(fix_run.suite_version)
    run = (
        HostedHarnessJob.no_workspace_objects.filter(
            fix_run=fix_run, fix_label=VARIATIONS_LABEL
        )
        .order_by("-created_at")
        .first()
    )
    try:
        # Once; a retry reads the run its failed fix finished.
        run = run or start_check_run(
            fix_run,
            CheckRunRequest(
                label=VARIATIONS_LABEL,
                agent_version_id=fix_run.base_agent_version_id,
                scenario_keys=[*failing_targets(fix_run), *variations],
                trials=1,
            ),
        )
    except HostedHarnessError as exc:
        if exc.retryable:
            raise
        state = (
            AgentFixRun.State.OUT_OF_BUDGET
            if exc.code == "fix_budget_exhausted"
            else AgentFixRun.State.FAILED
        )
        return _end(fix_run, state, exc.code, exc.message)
    if run.state not in FINISHED_RUN_STATES:
        return VariationsStep.CHECKING
    if run.state != HostedHarnessJob.State.COMPLETED:
        return _end(
            fix_run,
            AgentFixRun.State.FAILED,
            "fix_variations_run_failed",
            "the current version's run on the variations did not finish",
        )
    return _settle(fix_run, run, variations)


def _write(fix_run: AgentFixRun) -> VariationsStep:
    """Ask the builder for the variations once, then wait for its save to land."""
    command = HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation__job_id=fix_run.environment_id,
        client_request_id=_client_request_id(fix_run),
    ).first()
    if command is None:
        # The builder writes from the environment as it is now: it must be as it ran.
        moved = variations_source_moved(
            HostedHarnessJob.no_workspace_objects.get(id=fix_run.environment_id),
            fix_run.source_run,
        )
        if moved is not None:
            return _end(fix_run, AgentFixRun.State.FAILED, moved.code, moved.message)
        _ask_builder(fix_run)
        return VariationsStep.WRITING
    turn = HostedHarnessConversationEvent.no_workspace_objects.filter(
        conversation_id=command.conversation_id,
        kind="turn_completed",
        payload__command_message_id=str(command.id),
    ).first()
    if command.state == HostedHarnessConversationMessage.State.FAILED or (
        turn is not None and (turn.payload or {}).get("outcome") != "success"
    ):
        return _end(
            fix_run,
            AgentFixRun.State.FAILED,
            "fix_variations_failed",
            "the environment's builder could not write the variations",
        )
    # The turn's save lands just after it ends; a turn that saved nothing never lands.
    if turn is not None and timezone.now() - turn.emitted_at > _LANDING_GRACE:
        return _end(
            fix_run,
            AgentFixRun.State.FAILED,
            "fix_variations_failed",
            "the environment's builder finished without saving the variations",
        )
    return VariationsStep.WRITING


def _client_request_id(fix_run: AgentFixRun) -> str:
    return f"fix-{fix_run.id}-variations"


def _ask_builder(fix_run: AgentFixRun) -> None:
    """Send the environment's builder the cases to vary. The platform sends it, so the
    builder asks nobody to confirm a change and changes no existing scenario."""
    from simulate.tasks.hosted_harness_conversation import (
        schedule_conversation_runtime,
    )

    # Before the message: a platform without a callback address cannot run the builder.
    base_url = _public_base_url(None)
    conversation, _command, created = enqueue_message(
        fix_run.environment,
        content=_message(failing_targets(fix_run)),
        client_request_id=_client_request_id(fix_run),
        payload={"from_platform": True},
        server_payload={"_origin": FIX_ORIGIN, "_fix_run_id": str(fix_run.id)},
    )
    if not created:
        return
    try:
        schedule_conversation_runtime(str(conversation.id), base_url)
    except Exception:  # noqa: BLE001 - the queued message waits for chat recovery
        logger.exception(
            "agent_fix_variations_not_scheduled",
            fix_run=str(fix_run.id),
            conversation=str(conversation.id),
        )


def _message(parents: Mapping[str, list[str]]) -> str:
    count = len(parents)
    keys = ", ".join(
        f"<parent key>-var{number}" for number in range(1, VARIATIONS_PER_CASE + 1)
    )
    listed = "\n".join(
        f"{number}. {key}: {', '.join(names)}"
        for number, (key, names) in enumerate(parents.items(), start=1)
    )
    return (
        f"Add {count * VARIATIONS_PER_CASE} new scenarios: {VARIATIONS_PER_CASE}"
        f" variations of each of the {count} scenarios listed below. These {count}"
        " failed on the current agent. Each variation must test the same behaviour as"
        " its parent, so that the same failure can show again.\n\n"
        "For each variation:\n"
        f"- Key: {keys}.\n"
        "- Keep these fields identical to the parent: sub_goals, use_case, max_turns,"
        " keywords, and the coverage levels of task, disposition, overlay,"
        " overlay_vector and overlay_intensity.\n"
        "- Give each variation a coverage cell of its own: set interface, interaction"
        " or counterparty to another level the suite's grid declares, a different"
        " one for each variation where the grid allows. The suite refuses a scenario"
        " whose coverage and sub-goals both equal another scenario's.\n"
        "- Change the persona (name, location, age group, occupation, personality,"
        " communication style), the wording of the instruction and the initial"
        " message, and the data values (names, dates, ids, amounts, counts).\n"
        "- When the parent relies on world data, take the new values from the seeded"
        " world, so that the same tools and the same checks apply.\n"
        "- Keep the same kind of request as the parent: a nonexistent id stays"
        " nonexistent, an unsupported request stays unsupported, a fraud or"
        " social-engineering attempt stays one.\n\n"
        "Do not change any sub-goal, the world, or any existing scenario. Do not run"
        " the scenarios.\n\n"
        "Write every variation in this turn, even if you remember writing variations"
        " before: none of them is in this workspace. Before you finish, check that each"
        " variation's folder is in the workspace's scenarios/ directory.\n\n"
        f"Parents (key: the sub-goals that did not hold):\n{listed}"
    )


def land(
    environment: HostedHarnessJob,
    conversation: HostedHarnessConversation,
    archive: bytes,
    command: HostedHarnessConversationMessage,
) -> None:
    """Make the builder's save of a fix's variations the fix's private suite version: the
    source run's suite and the variations of its failing cases, under keys of the fix's
    own, each with the sub-goals of the case it varies. The environment's suite, drafts
    and scenarios do not change. A save that cannot be used ends the fix.

    Only the finished turn's save lands: the builder also saves while it works, and those
    are refused as not saved. Every save of the turn outdates the chat's workspace, so a
    later chat save from that sandbox never carries the variations into a draft. A save
    that arrives once the fix has landed or ended leaves the workspace alone: that
    outdated it already, and the chat may have moved on since."""
    fix_run = (
        AgentFixRun.no_workspace_objects.select_related(
            "source_run__suite_version", "source_run__environment_version"
        )
        .filter(id=(command.payload or {}).get("_fix_run_id"))
        .first()
    )
    writing = (
        fix_run is not None
        and fix_run.state == AgentFixRun.State.RUNNING
        and fix_run.suite_version_id is None
    )
    if writing:
        _outdate_workspace(environment, conversation)
    if not HostedHarnessConversationEvent.no_workspace_objects.filter(
        conversation=conversation,
        kind="turn_completed",
        payload__command_message_id=str(command.id),
    ).exists():
        raise HostedHarnessError(
            "fix_variations_not_final",
            "Kept for the fix, not saved to the environment: the fix takes these"
            " variations when this turn ends",
            status_code=409,
        )
    if not writing:
        logger.info("agent_fix_variations_save_dropped", command=str(command.id))
        return
    try:
        prepared, variations, documents = _private_suite(
            environment, fix_run, archives.read_archive(archive)
        )
    except HostedHarnessError as exc:
        if exc.retryable:
            raise
        logger.warning(
            "agent_fix_variations_refused", fix_run=str(fix_run.id), code=exc.code
        )
        _end(
            fix_run,
            AgentFixRun.State.FAILED,
            "fix_variations_failed",
            f"the builder's variations could not be used: {exc.message}",
        )
        return
    archives.put_archive(prepared.suite_key, prepared.suite_body)
    source_run = fix_run.source_run
    parents = failing_targets(fix_run)
    without = _without_variations(parents, variations)
    with transaction.atomic():
        # The fix first, then the environment, as a check run of the fix takes them.
        locked = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run.id)
        locked_environment = (
            HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
        )
        if locked.state != AgentFixRun.State.RUNNING or locked.suite_version_id:
            return
        # Written from the environment as it was when asked; it may have moved since.
        moved = variations_source_moved(locked_environment, source_run)
        if moved is not None:
            transaction.on_commit(
                partial(
                    _end, fix_run, AgentFixRun.State.FAILED, moved.code, moved.message
                )
            )
            return
        version = HarnessSuiteVersion.no_workspace_objects.create(
            job=locked_environment,
            organization_id=locked_environment.organization_id,
            workspace_id=locked_environment.workspace_id,
            # Unnumbered: it is never listed or made the default.
            status=VersionStatus.ACTIVE,
            origin=VersionOrigin.FIX,
            hash=prepared.suite_hash,
            hash_kind=HashKind.TREE_V1,
            parent=source_run.suite_version,
            authored_against=source_run.environment_version,
            fix_run=locked,
            created_by=locked.created_by,
            summary=f"{len(variations)} variations of {len(parents) - len(without)}"
            f" of {len(parents)} failing cases, for a fix",
            scenario_count=len(prepared.scenarios),
            **{
                **prepared.suite_fields,
                "documents": {**prepared.suite_documents, "variations": variations},
            },
        )
        HarnessSuiteMember.no_workspace_objects.bulk_create(
            [
                HarnessSuiteMember(
                    suite_version=version,
                    scenario_id=member.scenario_id,
                    scenario_key=member.scenario_key,
                    number=member.number,
                    content=member.content,
                )
                for member in HarnessSuiteMember.no_workspace_objects.filter(
                    suite_version_id=source_run.suite_version_id
                )
            ]
        )
        # Not the default's pairing: no other scenario of the environment is touched.
        paired = pair_scenarios(locked_environment, documents, prune=False, exact=True)
        _add_members(version, paired)
        # Hidden from the environment's scenarios; a run of the version still loads them.
        HostedHarnessScenario.no_workspace_objects.filter(
            id__in=[row.id for row, _document in paired]
        ).update(deleted=True, deleted_at=timezone.now())
        locked.suite_version = version
        locked.save(update_fields=["suite_version", "updated_at"])
    logger.info(
        "agent_fix_variations_landed",
        fix_run=str(fix_run.id),
        suite_version=str(version.id),
        variations=len(variations),
        cases=len(parents),
        cases_without_variations=without,
    )


def _private_suite(
    environment: HostedHarnessJob, fix_run: AgentFixRun, files: Mapping[str, bytes]
) -> tuple[Any, dict[str, str], list[dict[str, Any]]]:
    """The fix's suite from the builder's save: the source run's suite as it ran, plus
    each variation the builder wrote of a failing case, renamed into the fix's keys. Its
    prepared pieces, the variations with the cases they vary, and their documents."""
    source_run = fix_run.source_run
    suite = archives.split_authoring(files).suite
    base = drafts._piece_files(environment, "suite", source_run.suite_version)
    parents = {_scenario_token(key): key for key in failing_targets(fix_run)}
    sub_goals = {
        _scenario_token(one.get("scenario_key") or one.get("name")): one.get(
            "sub_goals"
        )
        for one in suite_documents(base)
    }
    tag = fix_run.id.hex[:8]
    written: dict[str, bytes] = {}
    documents: list[dict[str, Any]] = []
    variations: dict[str, str] = {}
    for document in suite_documents(suite):
        written_key = str(document.get("scenario_key") or document.get("name") or "")
        # The first of its key, name or folder that names a variation of a failing case.
        match = next(
            (
                found
                for spelling in (
                    written_key,
                    document.get("name"),
                    document.get("folder"),
                )
                if (found := _VARIATION_TOKEN.fullmatch(_scenario_token(spelling)))
                and found["parent"] in parents
            ),
            None,
        )
        if match is None:
            continue
        number = int(match["number"])
        parent = parents[match["parent"]]
        key = f"{parent[:240]}-fix{tag}-v{number}"
        if not 1 <= number <= VARIATIONS_PER_CASE or key in variations:
            continue
        folder = PurePosixPath(str(document.get("folder") or written_key)).name
        folder = f"scenarios/{folder}/"
        # The builder is told to keep its case's sub-goals, and does not always.
        fields = {"scenario_key": key, "name": key, "folder": f"scenarios/{key}"}
        if goals := sub_goals.get(match["parent"]):
            fields["sub_goals"] = goals
        moved = {
            f"scenarios/{key}/{path.removeprefix(folder)}": data
            for path, data in suite.items()
            if path.startswith(folder)
        }
        scenario = moved.get(f"scenarios/{key}/scenario.json")
        if scenario is None:
            continue
        try:
            moved[f"scenarios/{key}/scenario.json"] = _with_fields(scenario, fields)
        except ValueError:
            continue
        written |= moved
        documents.append({**document, **fields})
        variations[key] = parent
    if not variations:
        raise HostedHarnessError(
            "fix_variations_none",
            "the builder wrote no variations of the failing cases",
            status_code=422,
        )
    tree = {**base, **written}
    if "scenarios.json" in base:
        tree["scenarios.json"] = json.dumps(
            [*json.loads(base["scenarios.json"]), *documents], indent=2
        ).encode()
    _split, prepared = drafts._prepared(
        environment,
        archives.compose(
            drafts._piece_files(
                environment, "environment", source_run.environment_version
            ),
            tree,
        ),
    )
    require_checkpoint_scenarios(prepared.scenarios)
    return prepared, variations, documents


def _with_fields(body: bytes, fields: Mapping[str, Any]) -> bytes:
    """A scenario folder's document with the variation's own key, name and sub-goals; a
    key or folder field only where the builder wrote one."""
    document = json.loads(body)
    if not isinstance(document, dict):
        raise ValueError("a scenario document is an object")
    kept = {
        name: value
        for name, value in fields.items()
        if name not in ("scenario_key", "folder") or name in document
    }
    return json.dumps({**document, **kept}, indent=2).encode()


def _outdate_workspace(
    environment: HostedHarnessJob, conversation: HostedHarnessConversation
) -> None:
    """The chat's sandbox holds the variations. A new token for its composed workspace
    makes any later save from that sandbox an outdated one, refused as ``draft_changed``,
    so they never reach the environment's drafts; once idle, the sandbox is retired."""
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=environment.id
        )
        payload = dict(locked.payload or {})
        metadata = dict(payload.get("metadata") or {})
        checkpoints = dict(metadata.get("conversation_checkpoints") or {})
        entry = dict(checkpoints.get(str(conversation.id)) or {})
        if isinstance(entry.get("draft_basis"), dict):
            entry["draft_basis"] = {**entry["draft_basis"], "token": uuid.uuid4().hex}
            checkpoints[str(conversation.id)] = entry
            metadata["conversation_checkpoints"] = checkpoints
            payload["metadata"] = metadata
            locked.payload = payload
            locked.save(update_fields=["payload", "updated_at"])
        transaction.on_commit(lambda: drafts.retire_chat_runtime(locked, conversation))


def _settle(
    fix_run: AgentFixRun, run: HostedHarnessJob, variations: Mapping[str, str]
) -> VariationsStep:
    """Keep the variations the base version failed as targets, held back with the cases
    they vary, and start the fix's time. Too few failing cases even so ends the fix, with
    the variations it found."""
    request = FixRequest.model_validate(fix_run.request)
    parents = failing_targets(fix_run)
    results = {one.key: one.failing_sub_goals for one in scenario_results(run)}
    kept = {key: parent for key, parent in variations.items() if results.get(key)}
    failing = {**parents, **{key: results[key] for key in kept}}
    floor = settings.AGENT_FIX_MIN_FAILING_CASES
    settled = request.model_copy(
        update={
            "target_scenario_keys": [*request.target_scenario_keys, *kept],
            "held_back_scenario_keys": split_held_back(
                str(fix_run.source_run_id), failing, kept
            ),
            "failing_sub_goals": list(
                dict.fromkeys(name for names in failing.values() for name in names)
            ),
            "variation_parents": kept,
            "variations_pending": False,
        }
    )
    without = _without_variations(parents, variations)
    too_few = len(failing) < floor
    # One transaction: a fix whose request says the step is over is never left running
    # below the floor, where its next look would start the engine.
    with transaction.atomic():
        locked = AgentFixRun.no_workspace_objects.select_for_update().get(id=fix_run.id)
        if locked.state != AgentFixRun.State.RUNNING:
            return (
                VariationsStep.STOPPING
                if locked.state == AgentFixRun.State.STOPPING
                else VariationsStep.ENDED
            )
        locked.request = settled.model_dump(mode="json")
        # The fix's minutes start now: the step's time is its own.
        locked.started_at = timezone.now()
        locked.save(update_fields=["request", "started_at", "updated_at"])
        if too_few:
            unvaried = (
                f" ({len(without)} of them got no usable variation)" if without else ""
            )
            _end(
                fix_run,
                AgentFixRun.State.TOO_FEW_CASES,
                "fix_targets_too_few",
                f"The run had {_count(len(parents), 'failing case')}{unvaried}. The"
                f" builder wrote {_count(len(variations), 'variation')} of them, and"
                f" {len(kept)} failed on the current version:"
                f" {_count(len(failing), 'failing case')}, and a fix needs {floor}.",
            )
    logger.info(
        "agent_fix_variations_settled",
        fix_run=str(fix_run.id),
        cases=len(parents),
        cases_without_variations=without,
        written=len(variations),
        kept=len(kept),
        failing=len(failing),
    )
    return VariationsStep.ENDED if too_few else VariationsStep.DONE


def stop_writing(fix_run: AgentFixRun) -> None:
    """For a fix that ended before its variations landed: its message to the builder is
    withdrawn while it waits, the turn the builder runs for it is ended as a stopped
    rebuild's is, and the chat's workspace is outdated, so nothing written for the fix
    reaches the environment's drafts. Fail open: the fix's ending stands."""
    if fix_run.suite_version_id is not None:
        return
    command = (
        HostedHarnessConversationMessage.no_workspace_objects.select_related(
            "conversation"
        )
        .filter(
            conversation__job_id=fix_run.environment_id,
            client_request_id=_client_request_id(fix_run),
        )
        .first()
    )
    if command is None:
        return
    try:
        # Delivered and not over: the builder's sandbox is running the fix's turn.
        running = (
            command.state == HostedHarnessConversationMessage.State.DELIVERED
            and not HostedHarnessConversationEvent.no_workspace_objects.filter(
                conversation_id=command.conversation_id,
                kind__in=("turn_completed", "turn_interrupted"),
                payload__command_message_id=str(command.id),
            ).exists()
        )
        HostedHarnessConversationMessage.no_workspace_objects.filter(
            id=command.id,
            state__in=(
                HostedHarnessConversationMessage.State.QUEUED,
                HostedHarnessConversationMessage.State.DELIVERED,
            ),
        ).update(
            state=HostedHarnessConversationMessage.State.FAILED,
            updated_at=timezone.now(),
        )
        _outdate_workspace(fix_run.environment, command.conversation)
        if running:
            transaction.on_commit(partial(_hand_back, str(fix_run.environment_id)))
    except Exception:
        logger.exception("agent_fix_variations_turn_kept", fix_run=str(fix_run.id))


def _hand_back(environment_id: str) -> None:
    """Stop the builder's sandbox that ran the withdrawn turn, and start one for the
    messages that wait behind it."""
    from simulate.tasks.harness_nudges import schedule_sandbox_stop
    from simulate.tasks.hosted_harness_conversation import (
        schedule_conversation_runtime,
    )

    try:
        handed = hand_back_chat(environment_id)
        if handed is None:
            return
        conversation_id, ref, waiting = handed
        if ref:
            schedule_sandbox_stop(conversation_id, ref)
        if waiting:
            schedule_conversation_runtime(conversation_id, _public_base_url(None))
    except Exception:  # noqa: BLE001 - the chat's recovery sweep restarts it
        logger.exception(
            "agent_fix_variations_hand_back_failed", environment=environment_id
        )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def _end(
    fix_run: AgentFixRun, state: AgentFixRun.State, code: str, message: str
) -> VariationsStep:
    close_fix_run(fix_run.id, state, failure={"code": code, "message": message})
    return VariationsStep.ENDED
