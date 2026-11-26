"""Lock an environment's default versions, with train and test splits.

A locked version names a pair of immutable versions, so locking freezes nothing new: it
records the pair and gives each scorable scenario its split. A scenario keeps the split it
got at the first lock that included it, so a growing suite never moves a test task into
train.
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
from django.db.models import F
from django.utils import timezone

from simulate.models.harness_versioning import (
    HarnessEnvironmentVersion,
    HarnessLockedVersion,
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
    _lock_environment,
    actors_enabled,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    grades_nothing,
    sub_goal_catalogue,
)
from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level

if TYPE_CHECKING:
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
    """The splits a lock gives: ``assign`` holds only scenarios that have none yet."""

    train: int
    test: int
    test_target: int
    assign: Mapping[UUID, str]


# The test split never takes more than this share of the runnable tasks.
MAX_TEST_SHARE = 0.3


def target_test_size(size: int) -> int:
    """How many tasks the test split aims for: 30% of a small suite, 20% of a large
    one, and never more than 30%."""
    return min(math.ceil(MAX_TEST_SHARE * size), max(60, math.ceil(0.2 * size)))


def _order(seed: str, value: str) -> str:
    return sha256(f"{seed}:{value}".encode()).hexdigest()


def _category(use_case: Any) -> str:
    return str(use_case or "").strip().casefold()


def production_bound(job: HostedHarnessJob) -> frozenset[str]:
    """The keys of the default suite's scenarios that need a production-only tool of
    the default environment version, as a run of that pair refuses them. Read only
    while actors are on: off, no tool reaches production, and the read stays off the
    version documents."""
    if not actors_enabled():
        return frozenset()
    # No pins: the default pair, which is the pair every lock and preview names.
    return frozenset(
        registration_key(scenario["key"])
        for entry in blocked_tools(job)
        for scenario in entry["scenarios"]
    )


def scorable_tasks(
    suite_version_id: UUID,
    *,
    environment_version_id: UUID,
    blocked: Collection[str] = frozenset(),
) -> list[Task]:
    """The suite version's scenarios a run pinned to both versions can score, less the
    ``blocked`` keys a run can't play. Both versions are immutable, so neither is this.
    Only the keys read leave the database."""
    catalogue = sub_goal_catalogue(
        HarnessEnvironmentVersion._base_manager.filter(id=environment_version_id)
        .values_list("documents__sub_goals", flat=True)
        .first()
    )
    members = HarnessSuiteMember.no_workspace_objects.filter(
        suite_version_id=suite_version_id
    ).values_list(
        "scenario_id", "scenario_key", "content__use_case", "content__sub_goals"
    )
    tasks = [
        Task(scenario_id, key, _category(use_case))
        for scenario_id, key, use_case, sub_goals in members
        if not grades_nothing(sub_goals, catalogue)
        and registration_key(key) not in blocked
    ]
    return tasks


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
    split stays within ``MAX_TEST_SHARE`` of the tasks. Then each
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
    splits = [
        assigned.get(task.scenario_id) or assign[task.scenario_id] for task in tasks
    ]
    return SplitPlan(
        train=splits.count(TaskSplit.TRAIN),
        test=splits.count(TaskSplit.TEST),
        test_target=target,
        assign=assign,
    )


def _assigned(tasks: list[Task]) -> dict[UUID, str]:
    return dict(
        HarnessTaskSplit.no_workspace_objects.filter(
            scenario_id__in=[task.scenario_id for task in tasks]
        ).values_list("scenario_id", "split")
    )


def _locked_version(
    environment_version_id: UUID, suite_version_id: UUID
) -> HarnessLockedVersion | None:
    return HarnessLockedVersion.no_workspace_objects.filter(
        environment_version_id=environment_version_id,
        suite_version_id=suite_version_id,
        unlocked_at__isnull=True,
    ).first()


def _plan_lock(
    job: HostedHarnessJob, locked_versions: list[LockedVersionItem] | None = None
) -> SplitPlan:
    """What locking the default pair would give, or the reason it can't be locked.
    ``locked_versions``, a read's own list, says which pair is locked, so the read's preview
    and history agree; without it the database says."""
    from simulate.services.harness_agent_versions import _busy
    from simulate.services.harness_versioning.read import require_versioning

    # An unlock still answers with versioning off: its preview says why nothing locks.
    require_versioning()
    if job.environment_version_id is None or job.suite_version_id is None:
        raise HostedHarnessError(
            "lock_not_ready",
            "Build this environment before you lock a version.",
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
    if locked_versions is None:
        current = _locked_version(job.environment_version_id, job.suite_version_id)
        current_label = current.label if current is not None else None
    else:
        current_label = next(
            (
                locked_version["label"]
                for locked_version in locked_versions
                if locked_version["unlocked_at"] is None
                and locked_version["environment_version_id"]
                == str(job.environment_version_id)
                and locked_version["suite_version_id"] == str(job.suite_version_id)
            ),
            None,
        )
    if current_label is not None:
        raise HostedHarnessError(
            "version_already_locked",
            f"These versions are already locked as {current_label}.",
            status_code=409,
        )
    tasks = scorable_tasks(
        job.suite_version_id,
        environment_version_id=job.environment_version_id,
        blocked=production_bound(job),
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
        locked = _lock_environment(environment)
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
    logger.info(
        "harness_version_locked",
        environment_id=str(locked.id),
        locked_version=locked_version.label,
        train=plan.train,
        test=plan.test,
        actor=getattr(user, "id", None),
    )
    return locked


def require_unlock_admin(environment: HostedHarnessJob, user: User) -> None:
    """Only an owner or an admin of the organization or of the environment's workspace
    may unlock a locked version."""
    level = get_effective_workspace_level(user, environment.workspace_id)
    if level is None or level < Level.WORKSPACE_ADMIN:
        raise HostedHarnessError(
            "unlock_admin_only",
            "Only a workspace admin or owner can unlock a version.",
            status_code=403,
        )


def unlock_version(
    environment: HostedHarnessJob, locked_version_id: UUID, *, user: User
) -> HostedHarnessJob:
    """Unlock a locked version. Its versions, its splits and the scores on it stay as they are."""
    require_unlock_admin(environment, user)
    with transaction.atomic():
        locked = _lock_environment(environment)
        locked_version = (
            HarnessLockedVersion.no_workspace_objects.select_for_update()
            .filter(environment=locked, id=locked_version_id)
            .first()
        )
        if locked_version is None:
            raise HostedHarnessError(
                "locked_version_not_found",
                "This locked version doesn't exist.",
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
    locked_version = (
        HarnessLockedVersion.no_workspace_objects.filter(
            environment=environment, unlocked_at__isnull=True
        )
        .order_by("-locked_at")
        .first()
    )
    if locked_version is not None:
        raise HostedHarnessError(
            "version_locked",
            f"A workspace admin or owner must unlock version {locked_version.label} first.",
            status_code=409,
            # What an admin's Unlock in the refusal names and sends.
            details={
                "locked_version_id": str(locked_version.id),
                "label": locked_version.label,
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

    rows = (
        HarnessLockedVersion.no_workspace_objects.filter(environment=job)
        # Labels only: a version row carries large documents the read never needs.
        .select_related("locked_by", "unlocked_by")
        .annotate(
            environment_number=F("environment_version__version_number"),
            suite_number=F("suite_version__version_number"),
        )
        .order_by("-locked_at")
    )
    locked_versions = sorted(
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


def lock_preview(
    job: HostedHarnessJob, locked_versions: list[LockedVersionItem] | None = None
) -> dict[str, Any]:
    """Whether the default pair can be locked now, why not, and the splits it would
    get. ``locked_versions`` is the same read's history, so both name the same lock."""
    proved = HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version_id=job.suite_version_id,
        environment_version_id=job.environment_version_id,
        status=HarnessSuiteValidation.Status.PASSED,
    ).exists()
    preview: dict[str, Any] = {
        "environment_version_id": job.environment_version_id,
        "suite_version_id": job.suite_version_id,
        "proved": proved,
        "can_lock": False,
        "refusal": None,
        "train": None,
        "test": None,
        "test_target": None,
    }
    try:
        plan = _plan_lock(job, locked_versions)
    except HostedHarnessError as refusal:
        preview["refusal"] = {"code": refusal.code, "message": refusal.message}
        preview.update(
            {key: (refusal.details or {}).get(key) for key in ("train", "test")}
        )
        return preview
    preview.update(
        can_lock=True,
        train=plan.train,
        test=plan.test,
        test_target=plan.test_target,
    )
    return preview
