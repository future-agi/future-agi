"""Lock an environment's default versions, with train and test splits.

A locked version names a pair of immutable versions, so locking freezes nothing new: it
records the pair and gives each scorable scenario its split. A scenario keeps the split it
got at the first lock that included it, so a growing suite never moves a test task into
train. Each lock records its roster, the scenarios it covers with their splits, since
which tasks can run follows the default bundle, which a lock doesn't freeze.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

import structlog
from django.db import transaction
from django.db.models import F, QuerySet
from django.utils import timezone

from simulate.models.harness_versioning import (
    HarnessEnvironmentVersion,
    HarnessLockedVersion,
    HarnessLockedVersionMember,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessTaskSplit,
    TaskSplit,
)
from simulate.models.hosted_harness import HostedHarnessJob
from simulate.selectors.environment_documents import registration_key
from simulate.services.harness_tool_reach import blocked_tools
from simulate.services.harness_versioning.drafts import require_no_rebuild
from simulate.services.harness_versioning.versions import (
    _conflict,
    actors_enabled,
    lock_environment,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    ResolvedPins,
    grades_nothing,
    resolve_run_pins,
    sub_goal_catalogue,
)
from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations

    from accounts.models.user import User

logger = structlog.get_logger(__name__)

# A test split this small can't tell a better agent from luck.
MIN_TEST_TASKS = 4

# How many unlocked ones a versions read lists. Locked ones are always listed.
UNLOCKED_HISTORY = 20


@dataclass(frozen=True)
class Task:
    """One scorable scenario of a suite version."""

    scenario_id: UUID
    key: str
    category: str


@dataclass(frozen=True)
class SplitPlan:
    """The splits a lock gives: ``splits`` holds every runnable task's, its roster;
    ``assign`` only those of scenarios that have none yet."""

    train: int
    test: int
    test_target: int
    assign: Mapping[UUID, str]
    splits: Mapping[UUID, str]


# The test split never takes more than this share of the runnable tasks.
MAX_TEST_SHARE = 0.3


def target_test_size(size: int) -> int:
    """How many tasks the test split aims for: 30% of a small suite, 20% of a large
    one. New test seats never take the split past 30%, but retained test tasks never
    move, so a suite that shrinks can keep more than 30% in test."""
    return min(math.ceil(MAX_TEST_SHARE * size), max(60, math.ceil(0.2 * size)))


def _order(seed: str, value: str) -> str:
    return sha256(f"{seed}:{value}".encode()).hexdigest()


def _category(use_case: Any) -> str:
    return str(use_case or "").strip().casefold()


def default_run_pins(job: HostedHarnessJob) -> ResolvedPins | None:
    """The pins the default run would use, as default_bundle_key reads them, or the
    reason the pair can't be locked: a run resolves its pins whatever the actors
    flag, so a pair the run refuses has no task that could run. The agent's credential
    blob is never read, nor, while actors are off, the version rows' documents."""
    try:
        return resolve_run_pins(job, credential_blob=False, documents=actors_enabled())
    except HostedHarnessError as refused:
        raise HostedHarnessError(
            "lock_not_ready",
            f"{refused.message.rstrip('.')}, so they can't be locked.",
            status_code=409,
        ) from refused


def production_bound(
    job: HostedHarnessJob, pins: ResolvedPins | None
) -> frozenset[str]:
    """The keys of the tasks the default run would refuse as production-only: those
    that need a tool the default run's contract says reaches production. That contract
    is the one its sealed bundle carries, so it can depend on the agent the bundle
    binds. Read only while actors are on: off, no tool reaches production, and the
    read stays off the contract and the suite documents. ``pins`` are the default
    run's, from default_run_pins."""
    if not actors_enabled():
        return frozenset()
    return frozenset(
        registration_key(scenario["key"])
        for entry in blocked_tools(job, pins)
        for scenario in entry["scenarios"]
    )


def scorable_tasks(
    suite_version_id: UUID,
    *,
    environment_version_id: UUID,
    blocked: Collection[str] = frozenset(),
) -> list[Task]:
    """The suite version's scenarios a run pinned to both versions can score, less the
    ones a run can't play: ``blocked`` holds their keys as the suite documents give
    them. Both versions are immutable, so neither is this. Only the keys read leave
    the database."""
    catalogue = sub_goal_catalogue(
        HarnessEnvironmentVersion._base_manager.filter(id=environment_version_id)
        .values_list("documents__sub_goals", flat=True)
        .first()
    )
    members = list(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=suite_version_id
        ).values_list(
            "scenario_id",
            "scenario_key",
            "content__name",
            "content__use_case",
            "content__sub_goals",
        )
    )
    excluded = _blocked_members(
        [(scenario_id, key, name) for scenario_id, key, name, _, _ in members], blocked
    )
    return [
        Task(scenario_id, key, _category(use_case))
        for scenario_id, key, _, use_case, sub_goals in members
        if not grades_nothing(sub_goals, catalogue) and scenario_id not in excluded
    ]


def _blocked_members(
    members: Iterable[tuple[UUID, str, object]], blocked: Collection[str]
) -> set[UUID]:
    """The scenario ids of the members whose document key is ``blocked``. A document
    keys a scenario by its own key, else by its name, and a backfilled member keeps
    its row's key, so a member pairs by its key first, then by its name, as the
    backfill paired them."""
    members = list(members)
    keys = {registration_key(key) for _, key, _ in members}
    excluded: set[UUID] = set()
    for scenario_id, key, name in members:
        by_name = registration_key(name)
        if registration_key(key) in blocked or (
            by_name and by_name in blocked and by_name not in keys
        ):
            excluded.add(scenario_id)
    return excluded


def _seats(
    sizes: Mapping[str, int],
    kept_test: Mapping[str, int],
    room: Mapping[str, int],
    target: int,
    seed: str,
) -> dict[str, int]:
    """New test seats for each category: the seats ``target`` leaves after the test
    tasks the categories already have.

    A category with no test task yet gets a seat first, larger ones first, and the
    pooled single-task group last, even when the target is already met, while the
    split stays within ``MAX_TEST_SHARE`` of the tasks. Retained test tasks may
    already exceed that share; new seats never add to it past the share. Then each
    seat goes to the category furthest below its share of the target, counting the
    test tasks it already has. ``room`` caps a category, so it keeps a train task.
    """
    total = sum(sizes.values())
    kept = sum(kept_test.values())
    share = {name: target * size / total for name, size in sizes.items()}
    seats = dict.fromkeys(sizes, 0)
    uncovered = sorted(
        (name for name in sizes if not kept_test[name] and room[name] > 0),
        key=lambda name: (name == "", -sizes[name], _order(seed, name)),
    )
    for name in uncovered[: max(0, math.ceil(MAX_TEST_SHARE * total) - kept)]:
        seats[name] = 1
    budget = target - kept - sum(seats.values())
    while budget > 0:
        open_ = [name for name in sizes if seats[name] < room[name]]
        if not open_:
            break
        best = min(
            open_,
            key=lambda name: (
                kept_test[name] + seats[name] - share[name],
                _order(seed, name),
            ),
        )
        seats[best] += 1
        budget -= 1
    return seats


def plan_splits(
    tasks: Iterable[Task],
    assigned: Mapping[UUID, str],
    seed: str,
) -> SplitPlan:
    """The split of every task: the one it already has, or a new one.

    The test split aims for the suite's target. Test tasks it already has count
    towards it, so new tasks take only the seats left, and no task ever moves. New
    tasks take their category's seats in a stable hash order of ``seed`` and the key.
    Nobody picks them. A category with one task, or with none named, shares one group
    with the others like it. N counts runnable tasks only; unrunnable tasks get no
    split.
    """
    tasks = list(tasks)
    named: dict[str, int] = defaultdict(int)
    for task in tasks:
        named[task.category] += 1
    group = {name: name if name and count > 1 else "" for name, count in named.items()}
    by_group: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        by_group[group[task.category]].append(task)
    target = target_test_size(len(tasks))
    kept_test: dict[str, int] = {}
    room: dict[str, int] = {}
    new: dict[str, list[Task]] = {}
    for name, members in by_group.items():
        kept = [
            assigned[task.scenario_id]
            for task in members
            if task.scenario_id in assigned
        ]
        new[name] = sorted(
            (task for task in members if task.scenario_id not in assigned),
            key=lambda task: _order(seed, task.key),
        )
        kept_test[name] = kept.count(TaskSplit.TEST)
        # The category's only train tasks are new ones: keep one of them in train.
        room[name] = max(0, len(new[name]) - (TaskSplit.TRAIN not in kept))
    seats = _seats(
        {name: len(members) for name, members in by_group.items()},
        kept_test,
        room,
        target,
        seed,
    )
    assign: dict[UUID, str] = {}
    for name, members in new.items():
        for index, task in enumerate(members):
            assign[task.scenario_id] = (
                TaskSplit.TEST if index < seats[name] else TaskSplit.TRAIN
            )
    splits = {
        task.scenario_id: assigned.get(task.scenario_id) or assign[task.scenario_id]
        for task in tasks
    }
    counts = list(splits.values())
    return SplitPlan(
        train=counts.count(TaskSplit.TRAIN),
        test=counts.count(TaskSplit.TEST),
        test_target=target,
        assign=assign,
        splits=splits,
    )


def _assigned(tasks: list[Task]) -> dict[UUID, str]:
    return dict(
        HarnessTaskSplit.no_workspace_objects.filter(
            scenario_id__in=[task.scenario_id for task in tasks]
        ).values_list("scenario_id", "split")
    )


class _PairNumbers(TypedDict):
    """A locked version's pair, by version number."""

    environment_number: int | None
    suite_number: int | None


def _with_pair_numbers(
    rows: QuerySet[HarnessLockedVersion],
) -> QuerySet[WithAnnotations[HarnessLockedVersion, _PairNumbers]]:
    """The rows with their pair's version numbers, for its labels. A version row
    carries large documents no lock read needs, so only the numbers are joined."""
    return rows.annotate(
        environment_number=F("environment_version__version_number"),
        suite_number=F("suite_version__version_number"),
    )


def _locked_version(
    environment_version_id: UUID, suite_version_id: UUID
) -> WithAnnotations[HarnessLockedVersion, _PairNumbers] | None:
    return _with_pair_numbers(
        HarnessLockedVersion.no_workspace_objects.filter(
            environment_version_id=environment_version_id,
            suite_version_id=suite_version_id,
            unlocked_at__isnull=True,
        )
    ).first()


@dataclass(frozen=True)
class SplitSelection:
    """The locked version a run of a split records, and the scenarios it runs."""

    locked_version: HarnessLockedVersion
    scenario_keys: list[str]


def select_split(
    environment: HostedHarnessJob,
    pins: ResolvedPins | None,
    split: str,
    scenario_keys: list[str] | None,
) -> SplitSelection:
    """What a run of ``pins`` and ``split`` runs: ``scenario_keys``, each of which must
    be in the split, or else every scenario of the split in suite order. Only a pair
    of versions that is locked now has splits to run. The split holds the lock's
    roster members of it, less the ones the run can't play now: which tools reach
    production follows the run's bundle, which the lock doesn't freeze."""
    environment_version = pins.environment_version if pins is not None else None
    suite_version = pins.suite_version if pins is not None else None
    locked_version = (
        _locked_version(environment_version.id, suite_version.id)
        if environment_version is not None and suite_version is not None
        else None
    )
    if locked_version is None:
        from simulate.services.harness_versioning.read import _label, _message_label

        pair = "these versions"
        if environment_version is not None and suite_version is not None:
            environment_label = _label(environment_version.version_number)
            suite_label = _label(suite_version.version_number)
            pair = (
                f"environment {_message_label(environment_label)}, "
                f"scenarios {_message_label(suite_label)}"
            )
        raise HostedHarnessError(
            "split_needs_locked_version",
            f"Lock {pair} to run their train and test splits.",
            status_code=422,
        )
    in_split = [
        scenario_id
        for scenario_id, member_split in locked_version_members(
            locked_version.id
        ).items()
        if member_split == split
    ]
    members = list(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=locked_version.suite_version_id,
            scenario_id__in=in_split,
        )
        .order_by("number")
        .values_list("scenario_id", "scenario_key", "content__name")
    )
    blocked = _blocked_members(members, production_bound(environment, pins))
    keys = [key for scenario_id, key, _ in members if scenario_id not in blocked]
    if not scenario_keys:
        return SplitSelection(locked_version, keys)
    outside = [key for key in scenario_keys if key not in keys]
    if outside:
        raise HostedHarnessError(
            "split_scenario_outside",
            ", ".join(outside)
            + (" is" if len(outside) == 1 else " are")
            + f" not in the {split} split of lock {locked_version.label}.",
            status_code=422,
            details={"split": split, "scenario_keys": outside},
        )
    return SplitSelection(locked_version, list(scenario_keys))


@dataclass(frozen=True)
class LockedVersionSplits:
    """A locked version's roster: its members' splits, by scenario key."""

    id: UUID
    label: str
    splits: Mapping[str, str]

    @property
    def test_keys(self) -> frozenset[str]:
        return frozenset(
            key for key, split in self.splits.items() if split == TaskSplit.TEST
        )


def run_locked_version(run: HostedHarnessJob) -> LockedVersionSplits | None:
    """The locked version ``run`` ran under, with its splits. A run of a split names its
    own, which wins even once unlocked or relocked: the Epoch must hold back the split
    that run used. Any other run takes its pair's locked version; None when the pair
    isn't locked, and an unlocked one doesn't count. An Epoch from the run holds back
    its test split: its check runs use the same pair. The launch reads it once and
    records it, so an unlock mid-Epoch changes nothing."""
    if run.locked_version_id is not None:
        locked_version = HarnessLockedVersion.no_workspace_objects.get(
            id=run.locked_version_id
        )
        suite_version_id = locked_version.suite_version_id
    else:
        if run.environment_version_id is None or run.suite_version_id is None:
            return None
        found = _locked_version(run.environment_version_id, run.suite_version_id)
        if found is None:
            return None
        locked_version = found
        suite_version_id = run.suite_version_id
    # The lock's roster, as it locked them: a later rule, an edit or a new default
    # never moves a scenario into or out of its test split.
    members = locked_version_members(locked_version.id)
    keys = HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id=suite_version_id, scenario_id__in=members
    ).values_list("scenario_id", "scenario_key")
    return LockedVersionSplits(
        id=locked_version.id,
        label=locked_version.label,
        splits={key: members[scenario_id] for scenario_id, key in keys},
    )


def _plan_lock(job: HostedHarnessJob) -> SplitPlan:
    """What locking the default pair would give, or the reason it can't be locked. The
    lock and its preview both plan here, so a lock never disagrees with its preview."""
    from simulate.services.harness_agent_versions import _busy
    from simulate.services.harness_versioning.read import _label, require_versioning

    require_versioning()
    if job.environment_version_id is None or job.suite_version_id is None:
        raise HostedHarnessError(
            "lock_not_ready",
            "Build this environment before you lock it.",
            status_code=409,
        )
    if _busy(job):
        raise HostedHarnessError(
            "lock_not_ready",
            "This environment is building or its last build failed. Lock it once a "
            "build finishes.",
            status_code=409,
        )
    # An update's rebuild moves the default when it lands.
    require_no_rebuild(job)
    current = _locked_version(job.environment_version_id, job.suite_version_id)
    if current is not None:
        raise HostedHarnessError(
            "version_already_locked",
            f"Lock {current.label} already covers "
            f"environment {_label(current.environment_number)}, "
            f"scenarios {_label(current.suite_number)}.",
            status_code=409,
        )
    pins = default_run_pins(job)
    tasks = scorable_tasks(
        job.suite_version_id,
        environment_version_id=job.environment_version_id,
        blocked=production_bound(job, pins),
    )
    plan = plan_splits(
        tasks,
        _assigned(tasks),
        str(job.id),
    )
    if plan.test < MIN_TEST_TASKS:
        raise HostedHarnessError(
            "lock_too_few_test",
            f"Add scenarios to lock: a test split needs at least {MIN_TEST_TASKS}.",
            status_code=422,
            details={"train": plan.train, "test": plan.test},
        )
    if plan.train == 0:
        raise HostedHarnessError(
            "lock_too_few_train",
            "Add scenarios to lock: the train split needs at least one.",
            status_code=422,
            details={"train": plan.train, "test": plan.test},
        )
    return plan


def _next_label(environment: HostedHarnessJob, now) -> str:
    """The month's next label. Unlocked ones count too, so a label is never reused."""
    month = f"{now.year}.{now.month}"
    count = HarnessLockedVersion._base_manager.filter(
        environment=environment, label__startswith=f"{month}."
    ).count()
    return f"{month}.{count + 1}"


def lock_version(
    environment: HostedHarnessJob,
    *,
    environment_version_id: UUID,
    suite_version_id: UUID,
    user: User | None = None,
) -> HostedHarnessJob:
    """Lock the default pair the caller saw. A newer default refuses the lock."""
    with transaction.atomic():
        locked = lock_environment(environment)
        for piece, seen in (
            ("environment", environment_version_id),
            ("suite", suite_version_id),
        ):
            if getattr(locked, f"{piece}_version_id") != seen:
                raise _conflict(piece, getattr(locked, f"{piece}_version"))
        plan = _plan_lock(locked)
        now = timezone.now()
        locked_version = HarnessLockedVersion.no_workspace_objects.create(
            environment=locked,
            # The pair the caller saw, which the checks above proved is the default.
            environment_version_id=environment_version_id,
            suite_version_id=suite_version_id,
            label=_next_label(locked, now),
            locked_at=now,
            locked_by=user if getattr(user, "is_authenticated", False) else None,
            train_count=plan.train,
            test_count=plan.test,
        )
        HarnessTaskSplit.no_workspace_objects.bulk_create(
            [
                HarnessTaskSplit(
                    scenario_id=scenario_id,
                    split=split,
                    first_locked_version=locked_version,
                )
                for scenario_id, split in plan.assign.items()
            ]
        )
        # The lock's own roster: which tasks can run follows the default bundle,
        # which the lock doesn't freeze, so its members are recorded here.
        HarnessLockedVersionMember.no_workspace_objects.bulk_create(
            [
                HarnessLockedVersionMember(
                    locked_version=locked_version,
                    scenario_id=scenario_id,
                    split=split,
                )
                for scenario_id, split in plan.splits.items()
            ]
        )
    logger.info(
        "harness_version_locked",
        environment_id=str(locked.id),
        locked_version=locked_version.label,
        train=plan.train,
        test=plan.test,
        actor=getattr(user, "id", None),
    )
    return locked


def locked_version_members(locked_version_id: UUID) -> dict[UUID, str]:
    """A lock's members, scenario id to split, as it locked them. A later rule
    change, an edit or a new default never moves them."""
    return dict(
        HarnessLockedVersionMember.no_workspace_objects.filter(
            locked_version_id=locked_version_id
        ).values_list("scenario_id", "split")
    )


def require_unlock_admin(environment: HostedHarnessJob, user: User) -> None:
    """Only an owner or an admin of the organization or of the environment's workspace
    may unlock a locked version."""
    level = get_effective_workspace_level(user, environment.workspace_id)
    if level is None or level < Level.WORKSPACE_ADMIN:
        raise HostedHarnessError(
            "unlock_admin_only",
            "Only a workspace admin or owner can unlock versions.",
            status_code=403,
        )


def unlock_version(
    environment: HostedHarnessJob, locked_version_id: UUID, *, user: User
) -> HostedHarnessJob:
    """Unlock a locked version. Its versions, its splits and the scores on it stay as they are."""
    require_unlock_admin(environment, user)
    with transaction.atomic():
        locked = lock_environment(environment)
        locked_version = (
            HarnessLockedVersion.no_workspace_objects.select_for_update()
            .filter(environment=locked, id=locked_version_id)
            .first()
        )
        if locked_version is None:
            raise HostedHarnessError(
                "locked_version_not_found",
                "This lock doesn't exist.",
                status_code=404,
            )
        if locked_version.unlocked_at is None:
            locked_version.unlocked_at = timezone.now()
            locked_version.unlocked_by = user
            locked_version.save(
                update_fields=["unlocked_at", "unlocked_by", "updated_at"]
            )
            logger.info(
                "harness_version_unlocked",
                environment_id=str(locked.id),
                locked_version=locked_version.label,
                actor=getattr(user, "id", None),
            )
    return locked


def require_no_locked_version(environment: HostedHarnessJob) -> None:
    """Refuse to delete an environment while one of its versions is locked."""
    from simulate.services.harness_versioning.read import _label

    locked_version = (
        _with_pair_numbers(
            HarnessLockedVersion.no_workspace_objects.filter(
                environment=environment, unlocked_at__isnull=True
            )
        )
        .order_by("-locked_at")
        .first()
    )
    if locked_version is not None:
        raise HostedHarnessError(
            "version_locked",
            f"Lock {locked_version.label} covers this version. A workspace admin or "
            "owner must unlock it first.",
            status_code=409,
            # What an admin's Unlock in the refusal sends, and the pair its
            # confirmation names.
            details={
                "locked_version_id": str(locked_version.id),
                "label": locked_version.label,
                "environment_label": _label(locked_version.environment_number),
                "suite_label": _label(locked_version.suite_number),
            },
        )


# --- reads -------------------------------------------------------------------------


class LockedVersionItem(TypedDict):
    id: str
    label: str
    environment_version_id: str
    environment_label: str | None
    suite_version_id: str
    suite_label: str | None
    locked_at: datetime
    locked_by: dict[str, str] | None
    unlocked_at: datetime | None
    unlocked_by: dict[str, str] | None
    train: int
    test: int


def locked_version_items(job: HostedHarnessJob) -> list[LockedVersionItem]:
    """The environment's locked versions, newest first: every locked one, since only its
    Unlock clears the delete guard, and the newest unlocked ones."""
    from simulate.services.harness_versioning.read import _label, _person

    rows = _with_pair_numbers(
        HarnessLockedVersion.no_workspace_objects.filter(environment=job)
        .select_related("locked_by", "unlocked_by")
        .order_by("-locked_at")
    )
    locked_versions: list[WithAnnotations[HarnessLockedVersion, _PairNumbers]] = sorted(
        [
            *rows.filter(unlocked_at__isnull=True),
            *rows.filter(unlocked_at__isnull=False)[:UNLOCKED_HISTORY],
        ],
        key=lambda locked_version: locked_version.locked_at,
        reverse=True,
    )
    return [
        {
            "id": str(locked_version.id),
            "label": locked_version.label,
            "environment_version_id": str(locked_version.environment_version_id),
            "environment_label": _label(locked_version.environment_number),
            "suite_version_id": str(locked_version.suite_version_id),
            "suite_label": _label(locked_version.suite_number),
            "locked_at": locked_version.locked_at,
            "locked_by": _person(locked_version.locked_by),
            "unlocked_at": locked_version.unlocked_at,
            "unlocked_by": _person(locked_version.unlocked_by),
            "train": locked_version.train_count,
            "test": locked_version.test_count,
        }
        for locked_version in locked_versions
    ]


class LockRefusal(TypedDict):
    """Why the default pair can't be locked now."""

    code: str
    message: str


class LockPreview(TypedDict):
    """Whether the default pair can be locked now, and the splits it would get."""

    environment_version_id: UUID | None
    suite_version_id: UUID | None
    proved: bool
    can_lock: bool
    refusal: LockRefusal | None
    train: int | None
    test: int | None
    test_target: int | None


def lock_preview(job: HostedHarnessJob) -> LockPreview:
    """Whether the default pair can be locked now, why not, and the splits it would
    get. It reads what the lock reads, the default run's contract and scenario
    documents included, so it has its own read, apart from the versions list. The
    agent version's credential blob is never read."""
    proved = HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version_id=job.suite_version_id,
        environment_version_id=job.environment_version_id,
        status=HarnessSuiteValidation.Status.PASSED,
    ).exists()
    preview = LockPreview(
        environment_version_id=job.environment_version_id,
        suite_version_id=job.suite_version_id,
        proved=proved,
        can_lock=False,
        refusal=None,
        train=None,
        test=None,
        test_target=None,
    )
    try:
        plan = _plan_lock(job)
    except HostedHarnessError as refused:
        details = refused.details or {}
        preview["refusal"] = LockRefusal(code=refused.code, message=refused.message)
        preview["train"] = details.get("train")
        preview["test"] = details.get("test")
        return preview
    preview["can_lock"] = True
    preview["train"] = plan.train
    preview["test"] = plan.test
    preview["test_target"] = plan.test_target
    return preview
