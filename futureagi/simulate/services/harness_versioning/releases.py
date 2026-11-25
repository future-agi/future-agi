"""Lock an environment's default versions into a release with train and test splits.

A release names a pair of immutable versions, so locking freezes nothing new: it records
the pair and gives each scorable scenario its split. A scenario keeps the split it got at
the first lock that included it, so a growing suite never moves a test task into train.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import structlog
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from simulate.models.harness_versioning import (
    HarnessRelease,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessTaskSplit,
    TaskSplit,
)
from simulate.models.hosted_harness import HostedHarnessJob
from simulate.selectors.agent_fix import sub_goal_names
from simulate.services.harness_versioning.drafts import require_no_rebuild
from simulate.services.harness_versioning.versions import _conflict, _lock_environment
from simulate.services.hosted_harness import HostedHarnessError, ResolvedPins
from tfc.constants.levels import Level
from tfc.permissions.utils import get_effective_workspace_level

logger = structlog.get_logger(__name__)

# A test split this small can't tell a better agent from luck.
MIN_TEST_TASKS = 4


@dataclass(frozen=True)
class Task:
    """One scorable scenario of a suite version."""

    scenario_id: Any
    key: str
    category: str


@dataclass(frozen=True)
class SplitPlan:
    """The splits a lock gives: ``assign`` holds only scenarios that have none yet."""

    train: int
    test: int
    test_target: int
    assign: Mapping[Any, str]


def target_test_size(scorable: int) -> int:
    """How many tasks the test split aims for: 30% of a small suite, 20% of a large
    one, and never more than 30%."""
    return min(math.ceil(0.3 * scorable), max(60, math.ceil(0.2 * scorable)))


def _order(seed: str, value: str) -> str:
    return sha256(f"{seed}:{value}".encode()).hexdigest()


def _category(use_case: Any) -> str:
    return str(use_case or "").strip().casefold()


def scorable_tasks_by_suite(suite_version_ids: Iterable[Any]) -> dict[Any, list[Task]]:
    """Each suite version's scenarios that have checks to score them. A member's content
    never changes, so neither do these lists. Only the two keys read leave the database."""
    members = (
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id__in=list(suite_version_ids)
        )
        .order_by("number")
        .values_list(
            "suite_version_id",
            "scenario_id",
            "scenario_key",
            "content__use_case",
            "content__sub_goals",
        )
    )
    tasks: dict[Any, list[Task]] = defaultdict(list)
    for suite_version_id, scenario_id, key, use_case, sub_goals in members:
        if sub_goal_names(sub_goals):
            tasks[suite_version_id].append(Task(scenario_id, key, _category(use_case)))
    return tasks


def scorable_tasks(suite_version_id: Any) -> list[Task]:
    return scorable_tasks_by_suite([suite_version_id]).get(suite_version_id, [])


def _quotas(sizes: Mapping[str, int], target: int, seed: str) -> dict[str, int]:
    """Each category's share of the test split, in proportion to its size. A category
    always keeps one task for train, and one with none in test yet gets a spare seat
    first, so every category with two tasks or more is in both splits when seats allow."""
    total = sum(sizes.values())
    exact = {name: target * size / total for name, size in sizes.items()}
    quotas = {
        name: min(sizes[name] - 1, math.floor(share)) for name, share in exact.items()
    }
    spare = target - sum(quotas.values())
    while spare > 0:
        open_ = [name for name in sizes if quotas[name] < sizes[name] - 1]
        if not open_:
            break
        open_.sort(
            key=lambda name: (
                quotas[name] > 0,
                -(exact[name] - math.floor(exact[name])),
                _order(seed, name),
            )
        )
        for name in open_[:spare]:
            quotas[name] += 1
        spare = target - sum(quotas.values())
    return quotas


def plan_splits(
    tasks: Iterable[Task], assigned: Mapping[Any, str], seed: str
) -> SplitPlan:
    """The split of every task: the one it already has, or a new one.

    New tasks fill each category's test seats left after its existing test tasks, in a
    stable hash order of ``seed`` and the key. Nobody picks them. A category with one
    task, or with none named, shares one group with the others like it.
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
    quotas = (
        _quotas(
            {name: len(members) for name, members in by_group.items()}, target, seed
        )
        if tasks
        else {}
    )
    assign: dict[Any, str] = {}
    for name, members in by_group.items():
        kept = [
            assigned[task.scenario_id]
            for task in members
            if task.scenario_id in assigned
        ]
        new = sorted(
            (task for task in members if task.scenario_id not in assigned),
            key=lambda task: _order(seed, task.key),
        )
        seats = max(0, quotas[name] - kept.count(TaskSplit.TEST))
        if TaskSplit.TRAIN not in kept:
            # The category's only train tasks are new ones: keep one of them in train.
            seats = min(seats, len(new) - 1)
        for index, task in enumerate(new):
            assign[task.scenario_id] = (
                TaskSplit.TEST if index < seats else TaskSplit.TRAIN
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


def _assigned(tasks: list[Task]) -> dict[Any, str]:
    return dict(
        HarnessTaskSplit.no_workspace_objects.filter(
            scenario_id__in=[task.scenario_id for task in tasks]
        ).values_list("scenario_id", "split")
    )


def _locked_release(environment_version_id: Any, suite_version_id: Any):
    return HarnessRelease.no_workspace_objects.filter(
        environment_version_id=environment_version_id,
        suite_version_id=suite_version_id,
        unlocked_at__isnull=True,
    ).first()


def split_scenario_keys(
    pins: ResolvedPins | None, split: str, scenario_keys: list[str] | None
) -> list[str]:
    """The scenarios a run of ``pins`` and ``split`` selects: ``scenario_keys``, each of
    which must be in the split, or else every scenario of the split in suite order.
    Only a pair of versions a release locks has splits to run."""
    release = (
        _locked_release(pins.environment_version.id, pins.suite_version.id)
        if pins is not None
        else None
    )
    if release is None:
        raise HostedHarnessError(
            "split_needs_release",
            "Lock a release on these versions to run its train or test split.",
            status_code=422,
        )
    tasks = scorable_tasks(pins.suite_version.id)
    in_split = set(
        HarnessTaskSplit.no_workspace_objects.filter(
            scenario_id__in=[task.scenario_id for task in tasks], split=split
        ).values_list("scenario_id", flat=True)
    )
    keys = [task.key for task in tasks if task.scenario_id in in_split]
    if not scenario_keys:
        return keys
    outside = [key for key in scenario_keys if key not in keys]
    if outside:
        raise HostedHarnessError(
            "split_scenario_outside",
            ", ".join(outside)
            + (" is" if len(outside) == 1 else " are")
            + f" not in the {split} split of release {release.label}.",
            status_code=422,
            details={"split": split, "scenario_keys": outside},
        )
    return scenario_keys


def _plan_lock(job: HostedHarnessJob) -> SplitPlan:
    """What locking the default pair would give, or the reason it can't be locked."""
    from simulate.services.harness_agent_versions import _busy

    if job.environment_version_id is None or job.suite_version_id is None:
        raise HostedHarnessError(
            "release_not_ready",
            "Build this environment before you lock a release.",
            status_code=409,
        )
    if _busy(job):
        raise HostedHarnessError(
            "release_not_ready",
            "This environment is building or its last build failed. Lock it once a "
            "build finishes.",
            status_code=409,
        )
    current = _locked_release(job.environment_version_id, job.suite_version_id)
    if current is not None:
        raise HostedHarnessError(
            "release_already_locked",
            f"Release {current.label} already locks these versions.",
            status_code=409,
        )
    tasks = scorable_tasks(job.suite_version_id)
    plan = plan_splits(tasks, _assigned(tasks), str(job.id))
    if plan.test < MIN_TEST_TASKS:
        raise HostedHarnessError(
            "release_too_few_test",
            f"Add scenarios to lock: a test split needs at least {MIN_TEST_TASKS}.",
            status_code=422,
            details={"train": plan.train, "test": plan.test},
        )
    if plan.train == 0:
        raise HostedHarnessError(
            "release_too_few_train",
            "Add scenarios to lock: the train split needs at least one.",
            status_code=422,
            details={"train": plan.train, "test": plan.test},
        )
    return plan


def _next_label(environment: HostedHarnessJob, now) -> str:
    """The month's next label. Unlocked releases count too, so a label is never reused."""
    month = f"{now.year}.{now.month}"
    count = HarnessRelease._base_manager.filter(
        environment=environment, label__startswith=f"{month}."
    ).count()
    return f"{month}.{count + 1}"


def lock_release(
    environment: HostedHarnessJob,
    *,
    environment_version_id: Any,
    suite_version_id: Any,
    user: Any = None,
) -> HostedHarnessJob:
    """Lock the default pair the caller saw. A newer default refuses the lock."""
    with transaction.atomic():
        locked = _lock_environment(environment)
        require_no_rebuild(locked)
        for piece, seen in (
            ("environment", environment_version_id),
            ("suite", suite_version_id),
        ):
            if getattr(locked, f"{piece}_version_id") != seen:
                raise _conflict(piece, getattr(locked, f"{piece}_version"))
        plan = _plan_lock(locked)
        now = timezone.now()
        release = HarnessRelease.no_workspace_objects.create(
            environment=locked,
            # The pair the caller saw, which the checks above proved is the default.
            environment_version_id=environment_version_id,
            suite_version_id=suite_version_id,
            label=_next_label(locked, now),
            locked_at=now,
            locked_by=user if getattr(user, "is_authenticated", False) else None,
        )
        HarnessTaskSplit.no_workspace_objects.bulk_create(
            [
                HarnessTaskSplit(
                    scenario_id=scenario_id, split=split, first_release=release
                )
                for scenario_id, split in plan.assign.items()
            ]
        )
    logger.info(
        "harness_release_locked",
        environment_id=str(locked.id),
        release=release.label,
        train=plan.train,
        test=plan.test,
        actor=getattr(user, "id", None),
    )
    return locked


def require_release_admin(environment: HostedHarnessJob, user: Any) -> None:
    """Only an owner or an admin of the organization or of the environment's workspace
    may unlock a release."""
    level = get_effective_workspace_level(user, environment.workspace_id)
    if level is None or level < Level.WORKSPACE_ADMIN:
        raise HostedHarnessError(
            "release_admin_only",
            "Only a workspace admin or owner can unlock a release.",
            status_code=403,
        )


def unlock_release(
    environment: HostedHarnessJob, release_id: Any, *, user: Any
) -> HostedHarnessJob:
    """Unlock a release. Its versions, its splits and the scores on it stay as they are."""
    require_release_admin(environment, user)
    with transaction.atomic():
        locked = _lock_environment(environment)
        release = (
            HarnessRelease.no_workspace_objects.select_for_update()
            .filter(environment=locked, id=release_id)
            .first()
        )
        if release is None:
            raise HostedHarnessError(
                "release_not_found", "This release doesn't exist.", status_code=404
            )
        if release.unlocked_at is None:
            release.unlocked_at = timezone.now()
            release.unlocked_by = user
            release.save(update_fields=["unlocked_at", "unlocked_by", "updated_at"])
            logger.info(
                "harness_release_unlocked",
                environment_id=str(locked.id),
                release=release.label,
                actor=getattr(user, "id", None),
            )
    return locked


def require_no_locked_release(environment: HostedHarnessJob) -> None:
    """Refuse to delete an environment while one of its releases is locked."""
    release = (
        HarnessRelease.no_workspace_objects.filter(
            environment=environment, unlocked_at__isnull=True
        )
        .order_by("-locked_at")
        .first()
    )
    if release is not None:
        raise HostedHarnessError(
            "release_locked",
            f"Unlock release {release.label} first.",
            status_code=409,
            details={"release_id": str(release.id)},
        )


# --- reads -------------------------------------------------------------------------


def _counts(tasks: list[Task], assigned: Mapping[Any, str]) -> dict[str, int]:
    splits = [assigned.get(task.scenario_id) for task in tasks]
    return {
        "train": splits.count(TaskSplit.TRAIN),
        "test": splits.count(TaskSplit.TEST),
    }


def release_items(job: HostedHarnessJob) -> list[dict[str, Any]]:
    """Every release of the environment, newest first, with its split counts. Each one
    counts the scenarios scorable in its own suite version, so a later lock never
    changes an earlier release's counts."""
    from simulate.services.harness_versioning.read import _label, _person

    releases = list(
        HarnessRelease.no_workspace_objects.filter(environment=job)
        # Labels only: a version row carries large documents the read never needs.
        .select_related("locked_by", "unlocked_by")
        .annotate(
            environment_number=F("environment_version__version_number"),
            suite_number=F("suite_version__version_number"),
        )
        .order_by("-locked_at")
    )
    assigned = dict(
        HarnessTaskSplit.no_workspace_objects.filter(scenario__job=job).values_list(
            "scenario_id", "split"
        )
    )
    tasks = scorable_tasks_by_suite({release.suite_version_id for release in releases})
    return [
        {
            "id": str(release.id),
            "label": release.label,
            "environment_version_id": str(release.environment_version_id),
            "environment_label": _label(release.environment_number),
            "suite_version_id": str(release.suite_version_id),
            "suite_label": _label(release.suite_number),
            "locked_at": release.locked_at,
            "locked_by": _person(release.locked_by),
            "unlocked_at": release.unlocked_at,
            "unlocked_by": _person(release.unlocked_by),
            **_counts(tasks.get(release.suite_version_id, []), assigned),
        }
        for release in releases
    ]


def lock_preview(job: HostedHarnessJob) -> dict[str, Any]:
    """Whether the default pair can be locked now, why not, and the splits it would get."""
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
        plan = _plan_lock(job)
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
