"""New agent versions from changes made outside the credentials editor.

A push to a watched public GitHub repository (by webhook or by the poller), an
uploaded folder, a prompt edit or a settings edit each make the next agent version
through ``create_agent_version``; its check and activation follow as for any version.
"""

from __future__ import annotations

import copy
import logging
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HarnessAgentVersionRuntime,
    HostedHarnessJob,
)
from simulate.services.harness_agent_versions import (
    _agent_versions_unavailable,
    _busy,
    _default_lineage,
    _moved_by_hand_since,
    _outstanding_request,
    _requested_at,
    _side_row,
    agent_definition_owner,
    create_agent_version,
    environment_owned,
    next_version_number,
    requested_here,
)
from simulate.services.harness_github import (
    GitHubResponse,
    github_get,
    rate_limited_now,
    remaining,
    safe_ref,
    safe_repository,
    short_ref,
    within_share,
)
from simulate.services.hosted_harness import HostedHarnessError

logger = logging.getLogger(__name__)

Status = HarnessAgentVersionRuntime.PreflightStatus
Trigger = HarnessAgentVersionRuntime.Trigger
SourceKind = HarnessAgentVersionRuntime.SourceKind

_SHA = re.compile(r"^[0-9a-f]{40}$")
_PINNED_REF = re.compile(r"^[0-9a-fA-F]{40}$")
_DAY = 86400
# Calls an hour the poller leaves to push re-reads, creation checks and comparisons.
_POLL_RESERVE = 20
# A passed version an environment asked for from where it stands is observed, unless
# its move was held back by one of these: asking again moves it.
_ASK_AGAIN = (
    "environment_busy",
    "environment_not_ready",
    "rollback_unavailable",
    "failed",
)
# A move refused this many times in a row may never be possible: no more asks.
_ROLLBACK_ASKS = 3
# A pair read for a push is not read again for another this soon: the poller is.
_PUSH_COOLDOWN = 60
# Agent settings a person may change, by connector; anything else is refused.
# TODO: call_direction (read from the bundle contract, step 14), a different
# assistant_id / agent_id (a different agent) and the LiveKit URL (a credential).
_SETTINGS = {
    "phone": ("phone_number", "target_speaks_first"),
    "livekit": ("agent_name", "target_speaks_first"),
    "vapi": ("target_speaks_first",),
    "retell": ("target_speaks_first",),
}
_PROMPT_KINDS = (SourceKind.PROVIDER, SourceKind.PHONE, SourceKind.LIVEKIT)
# Environment metadata: the head a pre-versioning environment was first seen at.
_SEEN_KEY = "agent_source_seen"
# Environment metadata: GitHub's id for a source whose version doesn't record one.
_REPOSITORY_ID_KEY = "agent_source_repository_id"
_PENDING_KEY = "agent_source_pending"
_SKIPPED_KEY = "agent_source_skipped"


@dataclass(frozen=True)
class WatchedSource:
    environment_id: UUID
    definition_id: UUID
    parent_version_id: UUID
    repository: str
    ref: str | None
    commit_sha: str | None
    organization_id: Any = None
    # GitHub's id for the repository: its head is read by id, never by a name a
    # later owner could take.
    repository_id: int | None = None


@dataclass(frozen=True)
class HeadResult:
    status: Literal[
        "ok", "not_modified", "not_found", "rate_limited", "budget", "error"
    ]
    sha: str | None = None


def _facts(environment: HostedHarnessJob) -> Any:
    """The facts the environment's default agent version stands for in its runs."""
    from simulate.services.harness_versioning.backfill import effective_runtime

    return effective_runtime(
        environment,
        _side_row(environment.agent_version_id),
        version=environment.agent_version,
    )


def watched_sources(
    *, repository: str | None = None, definition_id: Any = None
) -> list[WatchedSource]:
    """Live, pinned, built environments whose effective default agent version is a
    public GitHub source; optionally only those on ``repository`` (any case)."""
    jobs = HostedHarnessJob.no_workspace_objects.filter(
        environment__isnull=True,
        deleted=False,
        state=HostedHarnessJob.State.COMPLETED,
        agent_version__isnull=False,
    ).select_related("agent_version")
    # Narrows only, as below: a GitHub source by the payload (a legacy or unrecorded
    # default) or by the default's own side row.
    jobs = jobs.filter(
        Q(payload__source__kind=SourceKind.GITHUB)
        | Q(agent_version__harness_runtime__source_kind=SourceKind.GITHUB)
    )
    if definition_id is not None:
        jobs = jobs.filter(agent_version__agent_definition_id=definition_id)
    if repository is not None:
        # The facts come from each environment's effective version.
        jobs = jobs.filter(
            Q(payload__source__repository__iexact=repository)
            | Q(agent_version__harness_runtime__source__repository__iexact=repository)
        )
    found = []
    private = 0
    for job in jobs.order_by("created_at"):
        if _busy(job) or agent_definition_owner(job) == "connected":
            # Connected agents change through their sync only.
            continue
        facts = _facts(job)
        if facts is None or facts.source_kind != SourceKind.GITHUB:
            continue
        source = facts.source or {}
        built = (job.payload or {}).get("source") or {}
        access = (facts.agent_payload or {}).get("source_access") or {}
        visibility = access.get("visibility") or built.get("visibility")
        if visibility == "private":
            private += 1
            continue
        name = str(source.get("repository") or "")
        stored_ref = str(source.get("ref") or "")
        if not safe_repository(name) or _PINNED_REF.fullmatch(stored_ref):
            # A ref that is a commit never moves.
            continue
        # TODO: an API request that sent commit_sha with a branch ref is watched: the
        # pin can't be told from the resolved baseline; record it at creation.
        if repository is not None and name.lower() != repository.lower():
            continue
        ref = short_ref(stored_ref)
        if ref is not None and not safe_ref(ref):
            continue
        recorded = ((job.payload or {}).get("metadata") or {}).get(
            _REPOSITORY_ID_KEY
        ) or {}
        if str(recorded.get("repository") or "").lower() != name.lower():
            recorded = {}
        found.append(
            WatchedSource(
                environment_id=job.id,
                definition_id=job.agent_version.agent_definition_id,
                parent_version_id=job.agent_version_id,
                repository=name,
                ref=ref,
                # A version still naming the branch alone (a shared one is never
                # stamped) stands for the commit this environment's build checked out.
                commit_sha=source.get("commit_sha")
                or (
                    built.get("commit_sha")
                    if str(built.get("repository") or "").lower() == name.lower()
                    else None
                ),
                organization_id=job.organization_id,
                repository_id=access.get("repository_id") or recorded.get("id"),
            )
        )
    if private:
        logger.info("harness_agent_watch_private_skipped count=%d", private)
    return found


def _pair(watched: WatchedSource) -> tuple[str, str | None]:
    """The (repository, ref) a head is read for: by GitHub's id once it is known."""
    name = watched.repository.lower()
    if watched.repository_id is not None:
        name = f"{name}#{watched.repository_id}"
    return name, watched.ref


def _head_key(
    repository: str, ref: str | None, repository_id: int | None = None
) -> str:
    return f"harness:gh-head:{repository_id or repository.lower()}:{ref or 'HEAD'}"


def github_head(
    repository: str,
    ref: str | None,
    *,
    purpose: str,
    reserve: int = 0,
    repository_id: int | None = None,
) -> HeadResult:
    """The commit ``ref`` (the default branch when ``None``) points at now; read by
    GitHub's id for the repository when given."""
    key = _head_key(repository, ref, repository_id)
    cached = cache.get(key) or {}
    path = (
        f"/repositories/{int(repository_id)}"
        if repository_id is not None
        else f"/repos/{repository}"
    )
    response = github_get(
        f"{path}/commits/{quote(ref or 'HEAD', safe='/')}",
        accept="application/vnd.github.sha",
        etag=cached.get("etag"),
        purpose=purpose,
        reserve=reserve,
        owner=repository.split("/")[0],
    )
    if response.status == "not_modified" and cached.get("sha"):
        return HeadResult("not_modified", cached["sha"])
    if response.status == "not_modified":
        return HeadResult("error")
    if response.status != "ok":
        return HeadResult(response.status)
    sha = str(response.body or "").strip()
    if not _SHA.fullmatch(sha):
        return HeadResult("error")
    # Kept past the poll interval, so the next tick can ask with its ETag.
    cache.set(key, {"etag": response.etag, "sha": sha}, _DAY)
    return HeadResult("ok", sha)


def _observed(environment: HostedHarnessJob, watched: WatchedSource, head: str) -> bool:
    """Whether the environment already asked for ``head`` from where it stands now,
    or set it aside."""
    from simulate.services.harness_versioning.read import _activation_entry

    seen = _seen(environment)
    # A recorded first sight holds only while the environment stands where it did.
    if watched.commit_sha == head or (
        seen.get("commit_sha") == head and seen.get("from") == watched.commit_sha
    ):
        return True
    environment_id = str(environment.id)
    now_on = str(environment.agent_version_id)
    # When the environment asked for its default: a head asked for before that is
    # asked for again (a branch pushed back to it).
    default_asked = parse_datetime(
        str(_requested_at(environment.agent_version_id).get(environment_id) or "")
    )
    rows = (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=watched.definition_id,
            source__repository__iexact=watched.repository,
            source__commit_sha=head,
        )
        # A legacy row is shared: its stored source may be another environment's.
        .exclude(preflight_status=Status.LEGACY).values(
            "preflight_status",
            "agent_payload__requested_by_environments",
            "agent_payload__created_for_environment",
            "agent_payload__requested_defaults",
            "agent_payload__requested_at",
            "agent_payload__discarded_by",
            "preflight_report",
        )
    )
    for row in rows:
        if not requested_here(
            row["agent_payload__requested_by_environments"],
            row["agent_payload__created_for_environment"],
            environment_id,
        ):
            continue
        status = row["preflight_status"]
        asked_from = (row["agent_payload__requested_defaults"] or {}).get(
            environment_id
        )
        asked_at = (row["agent_payload__requested_at"] or {}).get(environment_id)
        asked = parse_datetime(str(asked_at or ""))
        entry = _activation_entry(row["preflight_report"], environment_id) or {}
        # Asked from where it stands now, its default not asked for since, and final.
        settled_here = (
            asked_from == now_on
            and not (
                default_asked is not None
                and asked is not None
                and default_asked > asked
            )
            and _settled(entry)
        )
        if status == Status.PENDING:
            return True
        if status == Status.DISCARDED:
            # Set aside here: only a newer head is asked for. Another environment's
            # discard leaves this one asking, for a version of its own.
            discarded_by = row["agent_payload__discarded_by"] or []
            if (
                not discarded_by
                or environment_id in discarded_by
                or _moved_by_hand_since(environment, asked_at)
            ):
                return True
            # Unless this one's own ask on it had already settled.
            if settled_here and entry.get("outcome") not in (None, "discarded"):
                return True
            continue
        if status == Status.FAILED and asked_from == now_on:
            return True
        if status != Status.PASSED:
            continue
        if _moved_by_hand_since(environment, asked_at) or settled_here:
            return True
    return False


def _seen(environment: HostedHarnessJob) -> Mapping[str, Any]:
    metadata = (environment.payload or {}).get("metadata") or {}
    return metadata.get(_SEEN_KEY) or {}


def pending_commit(environment: HostedHarnessJob) -> Mapping[str, Any]:
    """The new commit offered to the environment and not yet answered, else empty."""
    metadata = (environment.payload or {}).get("metadata") or {}
    return metadata.get(_PENDING_KEY) or {}


def pending_commit_item(environment: HostedHarnessJob) -> dict[str, Any] | None:
    """What the environment shows of its offered commit, or None."""
    notice = pending_commit(environment)
    if not notice:
        return None
    return {
        name: notice.get(name) for name in ("repository", "ref", "commit_sha", "at")
    }


def _skipped(environment: HostedHarnessJob) -> Mapping[str, Any]:
    metadata = (environment.payload or {}).get("metadata") or {}
    return metadata.get(_SKIPPED_KEY) or {}


def _record_metadata(
    environment: HostedHarnessJob,
    key: str,
    value: Any,
    *,
    unless: Callable[[HostedHarnessJob], Any] | None = None,
) -> bool | None:
    """Write ``value`` under ``key`` in a pre-versioning environment's metadata, under
    its row lock. None when the row is gone, False when ``unless`` holds for it."""
    with transaction.atomic():
        locked = (
            HostedHarnessJob.no_workspace_objects.select_for_update()
            .filter(id=environment.id, environment__isnull=True, deleted=False)
            .first()
        )
        if locked is None:
            return None
        if unless is not None and unless(locked):
            return False
        payload = dict(locked.payload or {})
        payload["metadata"] = {**(payload.get("metadata") or {}), key: value}
        locked.payload = payload
        locked.save(update_fields=["payload", "updated_at"])
    return True


def _seed(environment: HostedHarnessJob, watched: WatchedSource, head: str) -> bool:
    """Record the head a pre-versioning environment first sees, moving nothing: its
    repository may have moved long before versioning watched it."""
    if _seen(environment):
        return False
    default = _side_row(environment.agent_version_id)
    if (
        default is not None
        and default.preflight_status != Status.LEGACY
        and default.parent_id is None
    ):
        # A first version made with versioning on: nothing before it to skip.
        return False
    lineage = _default_lineage(environment)
    statuses = list(
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id__in=lineage
        ).values_list("preflight_status", flat=True)
    )
    if len(statuses) == len(lineage) and Status.LEGACY not in statuses:
        return False
    recorded = _record_metadata(
        environment,
        _SEEN_KEY,
        {
            "repository": watched.repository,
            "ref": watched.ref,
            "commit_sha": head,
            "from": watched.commit_sha,
            "at": timezone.now().isoformat(),
        },
        unless=_seen,
    )
    if recorded is None:
        return False
    if recorded:
        logger.info(
            "harness_agent_head_seeded environment=%s head=%s",
            environment.id,
            head[:12],
        )
    # Not recorded: seen first by a call running alongside; the next look decides.
    return True


def github_repository(
    repository: str, *, purpose: str, reserve: int = 0, owner: str | None = None
) -> GitHubResponse:
    """GitHub's record of a public repository, by its checked name."""
    return github_get(
        f"/repos/{repository}",
        accept="application/vnd.github+json",
        purpose=purpose,
        reserve=reserve,
        owner=owner,
    )


def _repository_id(found: GitHubResponse) -> int | None:
    value = found.body.get("id") if isinstance(found.body, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _identified(
    environment: HostedHarnessJob, watched: WatchedSource, purpose: str
) -> bool:
    """Whether GitHub's id for the watched repository is known. Without it, it is
    looked up and recorded once (the seed tick of a pre-versioning environment), and
    nothing is made until a look that reads the head by that id."""
    if watched.repository_id is not None:
        return True
    key = f"harness:gh-repo-id:{watched.repository.lower()}"
    found = cache.get(key)
    if found is None:
        found = _repository_id(
            github_repository(
                watched.repository,
                purpose=purpose,
                reserve=_POLL_RESERVE if purpose == "poll" else 0,
                owner=watched.repository.split("/")[0],
            )
        )
        if found is None:
            return False
        # Environments on the same repository this tick share the one lookup.
        cache.set(key, found, _PUSH_COOLDOWN * 10)
    if _record_metadata(
        environment,
        _REPOSITORY_ID_KEY,
        {"repository": watched.repository, "id": found},
    ):
        logger.info(
            "harness_agent_repository_identified environment=%s", environment.id
        )
    return False


def _settled(entry: Mapping[str, Any]) -> bool:
    """Whether an activation outcome is final: asking again would not move it."""
    outcome = entry.get("outcome")
    return outcome not in _ASK_AGAIN or (
        outcome == "rollback_unavailable"
        and int(entry.get("attempts") or 1) >= _ROLLBACK_ASKS
    )


def _observe_one(
    watched: WatchedSource, head: str, purpose: str
) -> tuple[str, AgentVersion | None]:
    from simulate.services.harness_versioning.assembly import certify_enabled

    environment = (
        HostedHarnessJob.no_workspace_objects.select_related(
            "agent_version__agent_definition"
        )
        .filter(id=watched.environment_id, environment__isnull=True, deleted=False)
        .first()
    )
    if environment is None or environment.agent_version_id != (
        watched.parent_version_id
    ):
        # Moved or deleted since it was read: the next look starts from there.
        return "default_moved", None
    # First sight of a pre-versioning environment records its head, moved or not:
    # only a later push makes a version.
    seeded = _seed(environment, watched, head)
    if not _identified(environment, watched, purpose):
        return ("seeded" if seeded else "skipped_unidentified"), None
    if _observed(environment, watched, head):
        return "unchanged", None
    if seeded:
        return "seeded", None
    if not certify_enabled():
        logger.info(
            "harness_agent_head_skipped environment=%s reason=certify_off",
            environment.id,
        )
        return "skipped_not_runnable", None
    if head in (
        pending_commit(environment).get("commit_sha"),
        _skipped(environment).get("commit_sha"),
    ):
        return "unchanged", None
    recorded = _record_metadata(
        environment,
        _PENDING_KEY,
        {
            "repository": watched.repository,
            "ref": watched.ref,
            "commit_sha": head,
            "repository_id": watched.repository_id,
            "at": timezone.now().isoformat(),
        },
    )
    if recorded is None:
        return "default_moved", None
    logger.info(
        "harness_agent_head_offered environment=%s head=%s",
        environment.id,
        head[:12],
    )
    return "offered", None


def _push_version(
    environment: HostedHarnessJob, notice: Mapping[str, Any]
) -> tuple[str, AgentVersion]:
    """The agent version for an offered commit, made as a push always made it."""
    # Built on a change still on its way to the default: neither undoes the other.
    parent = (
        _outstanding_request(environment, environment.agent_version)
        or environment.agent_version
    )
    first_new = next_version_number(parent.agent_definition_id)
    # No transaction or lock around it: its activation takes the environment's lock
    # after releasing the definition's.
    version = create_agent_version(
        parent.agent_definition,
        parent=parent,
        env=environment,
        source={
            "kind": "github",
            "repository": notice["repository"],
            "ref": notice.get("ref"),
            "commit_sha": notice["commit_sha"],
            "visibility": "public",
            "repository_id": notice.get("repository_id"),
        },
        trigger=Trigger.PUSH,
        user=None,
        explicit_reuse=False,
    )
    return ("created" if version.version_number >= first_new else "reused"), version


def answer_pending_commit(
    environment_id: Any, *, sync: bool
) -> tuple[str, AgentVersion | None]:
    """Sync the commit offered to an environment as its next agent version, or skip
    it so it is not offered again. Returns ``created``, ``reused`` or ``skipped``
    and the version a sync made or reused."""
    from simulate.services.harness_agent_versions import _busy
    from simulate.services.harness_versioning.assembly import certify_enabled

    environment = (
        HostedHarnessJob.no_workspace_objects.select_related(
            "agent_version__agent_definition"
        )
        .filter(id=environment_id, environment__isnull=True, deleted=False)
        .first()
    )
    if environment is None:
        raise HostedHarnessError(
            "environment_not_found", "Environment not found", status_code=404
        )
    notice = pending_commit(environment)
    head = notice.get("commit_sha")
    if not head:
        raise HostedHarnessError(
            "no_pending_commit", "There is no new commit to sync", status_code=409
        )
    outcome, version = "skipped", None
    if sync:
        if _busy(environment):
            raise HostedHarnessError(
                "environment_busy",
                "The environment is being built; sync once it finishes",
                status_code=409,
            )
        if not certify_enabled() or environment.agent_version_id is None:
            raise HostedHarnessError(
                "sync_unavailable",
                "This environment can't take a new agent version right now",
                status_code=409,
            )
        outcome, version = _push_version(environment, notice)
    with transaction.atomic():
        locked = (
            HostedHarnessJob.no_workspace_objects.select_for_update()
            .filter(id=environment.id, environment__isnull=True, deleted=False)
            .first()
        )
        if locked is not None and pending_commit(locked).get("commit_sha") == head:
            metadata = dict((locked.payload or {}).get("metadata") or {})
            metadata.pop(_PENDING_KEY, None)
            if not sync:
                metadata[_SKIPPED_KEY] = {
                    "commit_sha": head,
                    "at": timezone.now().isoformat(),
                }
            locked.payload = {**(locked.payload or {}), "metadata": metadata}
            locked.save(update_fields=["payload", "updated_at"])
    logger.info(
        "harness_agent_head_answered environment=%s head=%s answer=%s version=%s",
        environment.id,
        head[:12],
        "sync" if sync else "skip",
        getattr(version, "id", None),
    )
    return outcome, version


def observe_repository_head(
    definition: AgentDefinition | Any,
    *,
    repository: str | None = None,
    heads: dict[tuple[str, str | None], HeadResult] | None = None,
    purpose: str = "poll",
) -> dict[str, str]:
    """Offer the new head to each environment watching this definition's GitHub
    source that has not observed it; returns {environment_id: outcome}. A version is
    made only when the environment's offer is answered with a sync.

    With ``heads`` only the (repository, ref) pairs it holds are looked at; without,
    each pair's head is read once.
    """
    from simulate.services.harness_versioning.versions import versioning_enabled

    if not versioning_enabled():
        return {}
    definition_id = getattr(definition, "id", definition)
    only_given = heads is not None
    heads = {} if heads is None else heads
    outcomes: dict[str, str] = {}
    for watched in watched_sources(repository=repository, definition_id=definition_id):
        pair = _pair(watched)
        head = heads.get(pair)
        if head is None:
            if only_given:
                continue
            head = heads[pair] = github_head(
                watched.repository,
                watched.ref,
                purpose=purpose,
                repository_id=watched.repository_id,
            )
        if head.status not in ("ok", "not_modified") or not head.sha:
            outcomes[str(watched.environment_id)] = "head_unavailable"
            if head.status in ("rate_limited", "budget"):
                break
            continue
        try:
            outcome, version = _observe_one(watched, head.sha, purpose)
        except HostedHarnessError as exc:
            outcome, version = f"error:{exc.code}", None
        except Exception as exc:  # noqa: BLE001 - one environment never stops another
            logger.warning(
                "harness_agent_head_failed environment=%s error=%s",
                watched.environment_id,
                type(exc).__name__,
            )
            outcome, version = "error", None
        outcomes[str(watched.environment_id)] = outcome
        logger.info(
            "harness_agent_head_observed definition=%s environment=%s outcome=%s"
            " head=%s purpose=%s version=%s",
            definition_id,
            watched.environment_id,
            outcome,
            head.sha[:12],
            purpose,
            getattr(version, "id", None),
        )
    return outcomes


def _checked_key(repository: str, ref: str | None) -> str:
    return f"harness:gh-checked:{repository}:{ref or 'HEAD'}"


def _poll_first(pair: tuple[str, str | None]) -> None:
    """Have the poller read a pair first, within its owner's hourly share and the
    hourly cap: pushes can't hold every poll slot."""
    if within_share("poll-first", pair[0].split("/")[0]):
        cache.set(_checked_key(*pair), -1, _DAY)


def poll_repository_heads(*, budget: int | None = None) -> dict[str, int]:
    """One poller tick: the oldest-checked (repo, ref) pairs first, within the shared
    GitHub budget; returns counts by outcome."""
    from django.conf import settings

    from simulate.services.harness_versioning.versions import versioning_enabled

    if not versioning_enabled() or rate_limited_now():
        return {}
    groups: dict[tuple[str, str | None], list[WatchedSource]] = {}
    for watched in watched_sources():
        groups.setdefault(_pair(watched), []).append(watched)
    limit = (
        budget
        if budget is not None
        else int(getattr(settings, "HARNESS_GITHUB_POLL_BUDGET", 5))
    )
    left = remaining()
    if left is not None:
        limit = min(limit, max(left - _POLL_RESERVE, 0))
    checked = {pair: float(cache.get(_checked_key(*pair)) or 0) for pair in groups}
    order = sorted(groups, key=checked.__getitem__)
    # Organizations take turns, each oldest-checked first, so one with many pairs
    # can't hold every read; pairs a push asked for still go first.
    taken: Counter[Any] = Counter()
    turn: dict[tuple[str, str | None], int] = {}
    for pair in order:
        turn[pair] = taken[groups[pair][0].organization_id]
        taken[groups[pair][0].organization_id] += 1
    order.sort(key=lambda pair: (checked[pair] >= 0, turn[pair]))
    counts: Counter[str] = Counter()
    for pair in order[:limit]:
        sources = groups[pair]
        head = github_head(
            sources[0].repository,
            pair[1],
            purpose="poll",
            reserve=_POLL_RESERVE,
            repository_id=sources[0].repository_id,
        )
        if head.status in ("rate_limited", "budget"):
            counts[head.status] += 1
            break
        cache.set(_checked_key(*pair), time.time(), _DAY)
        for definition_id in dict.fromkeys(one.definition_id for one in sources):
            for outcome in observe_repository_head(
                definition_id,
                repository=sources[0].repository,
                heads={pair: head},
                purpose="poll",
            ).values():
                counts[outcome] += 1
    return dict(counts)


def receive_push(
    repository: str,
    ref: str,
    default_branch: str | None,
    repository_id: int | None = None,
) -> dict[str, int]:
    """A signed push: observe the definitions watching this repository and ref now.
    The pushed commit is a hint only; the head is read again. A push from another
    repository by the same name (GitHub's id differs) is ignored."""
    from simulate.services.harness_versioning.versions import versioning_enabled

    if not versioning_enabled():
        return {}
    pushed = short_ref(ref)
    if not safe_repository(repository) or pushed is None or not safe_ref(pushed):
        logger.info("harness_webhook_push_refused reason=unsafe_name")
        return {}
    on_default = bool(default_branch) and ref == f"refs/heads/{default_branch}"
    matched = [
        watched
        for watched in watched_sources(repository=repository)
        if (watched.ref == pushed or (watched.ref is None and on_default))
        and (repository_id is None or watched.repository_id in (None, repository_id))
    ]
    heads: dict[tuple[str, str | None], HeadResult] = {}
    seen: set[tuple[str, str | None]] = set()
    for watched in matched:
        pair = _pair(watched)
        if pair not in seen:
            seen.add(pair)
            if not cache.add(
                f"harness:gh-push-seen:{pair[0]}:{pair[1] or 'HEAD'}",
                1,
                _PUSH_COOLDOWN,
            ):
                # Read for a push just now: the poller reads it first.
                _poll_first(pair)
                continue
            cache.delete(
                _head_key(watched.repository, watched.ref, watched.repository_id)
            )
            heads[pair] = github_head(
                watched.repository,
                watched.ref,
                purpose="webhook",
                repository_id=watched.repository_id,
            )
            if heads[pair].status in ("rate_limited", "budget"):
                _poll_first(pair)
    counts: Counter[str] = Counter()
    for definition_id in dict.fromkeys(one.definition_id for one in matched):
        for outcome in observe_repository_head(
            definition_id, repository=repository, heads=heads, purpose="webhook"
        ).values():
            counts[outcome] += 1
    return dict(counts)


def validate_public_github_source(
    payload: Mapping[str, Any], *, organization_id: Any = None, lookup: bool = True
) -> int | None:
    """Refuse a new environment on a private or unknown GitHub repository: only
    public ones are watched. GitHub being unreachable never blocks a creation.
    Without ``lookup`` only what the payload says is checked. Returns GitHub's id
    for the repository when it was looked up."""
    source = payload.get("source") or {}
    if source.get("kind") != "github":
        return None
    private = HostedHarnessError(
        "github_private_unsupported",
        "Private GitHub repositories can't be versioned yet; use a public repository"
        " or upload the folder",
        status_code=422,
    )
    if source.get("visibility") == "private":
        raise private
    if not lookup:
        return None
    repository = str(source.get("repository") or "")
    found = (
        # One organization's creations take only its share of their own cap.
        github_repository(
            repository,
            purpose="create",
            owner=str(organization_id) if organization_id else None,
        )
        if safe_repository(repository)
        else None
    )
    if found is None or found.status == "not_found":
        raise HostedHarnessError(
            "github_repository_not_found",
            "That GitHub repository wasn't found; check its name and that it's public",
            status_code=422,
        )
    if found.status != "ok":
        # TODO: creations past the hourly cap fail open without a repository_id,
        # and are identified by name on the first tick.
        logger.info("harness_github_check_skipped reason=%s", found.status)
        return None
    if isinstance(found.body, Mapping) and found.body.get("private"):
        raise private
    return _repository_id(found)


# --- changes made in the environment --------------------------------------------------


def _unsupported(field: str) -> HostedHarnessError:
    return HostedHarnessError(
        "agent_change_unsupported",
        "This agent's source doesn't take that change",
        status_code=422,
        details={"piece": "agent", "field": field},
    )


def _unchanged() -> HostedHarnessError:
    return HostedHarnessError(
        "agent_unchanged",
        "Nothing changed",
        status_code=422,
        details={"piece": "agent"},
    )


def change_agent_source(
    environment: HostedHarnessJob,
    *,
    archive_artifact_id: UUID | None = None,
    prompt: str | None = None,
    agent_payload: Mapping[str, Any] | None = None,
    user: Any = None,
) -> AgentVersion:
    """The environment's next agent version from a new upload, prompt or settings;
    checked next, then made this environment's default."""
    from simulate.services.harness_versioning.archives import archive_exists
    from simulate.services.harness_versioning.assembly import certify_enabled
    from simulate.services.harness_versioning.versions import versioning_enabled
    from simulate.services.hosted_harness_gateway import _source_object_key

    if (
        not versioning_enabled()
        or environment.environment_id is not None
        or not environment.agent_version_id
    ):
        raise _agent_versions_unavailable()
    if agent_definition_owner(environment) == "connected":
        raise HostedHarnessError(
            "agent_connected",
            "This agent is managed on the Agents page; its changes arrive from there",
            status_code=422,
        )
    if not certify_enabled():
        raise HostedHarnessError(
            "version_not_runnable",
            "A changed agent can't run until preparing new combinations is turned on",
            status_code=422,
            details={"piece": "agent"},
        )
    parent = (
        AgentVersion.no_workspace_objects.select_related("agent_definition")
        .filter(
            id=environment.agent_version_id,
            organization_id=environment.organization_id,
        )
        .first()
    )
    if parent is None:
        raise _agent_versions_unavailable()
    environment.agent_version = parent
    facts = _facts(environment)
    kind = facts.source_kind
    current = copy.deepcopy(facts.agent_payload or {})
    env_source = dict((environment.payload or {}).get("source") or {})
    access = current.get("source_access") or {}
    given = (
        "archive_artifact_id"
        if archive_artifact_id is not None
        else ("prompt" if prompt is not None else "agent_payload")
    )
    if (
        env_source.get("kind") == "remote"
        or access.get("kind") == "remote"
        or (facts.source or {}).get("provider") == "remote"
    ):
        # A remote target owns its source, prompt and settings.
        raise _unsupported(given)

    if archive_artifact_id is not None:
        if kind != SourceKind.ARCHIVE:
            raise _unsupported("archive_artifact_id")
        if str(archive_artifact_id) == str(
            (facts.source or {}).get("archive_artifact_id") or ""
        ):
            raise _unchanged()
        if not archive_exists(
            _source_object_key(environment.organization_id, str(archive_artifact_id))
        ):
            raise HostedHarnessError(
                "archive_source_not_found",
                "The upload expired; upload the folder again",
                status_code=422,
                details={"piece": "agent"},
            )
        return _made(
            parent,
            environment,
            user,
            source={"kind": "archive", "archive_artifact_id": str(archive_artifact_id)},
            trigger=Trigger.UPLOAD,
        )

    config = dict(current.get("config") or {})
    before = copy.deepcopy(config)
    prompt_changed = False
    if prompt is not None:
        if kind not in _PROMPT_KINDS:
            # A code agent's prompt belongs to its source.
            raise _unsupported("prompt")
        in_config = kind in (SourceKind.PHONE, SourceKind.LIVEKIT) or (
            "target_system_prompt" in config
        )
        now = (
            str(config.get("target_system_prompt") or "")
            if in_config
            else (parent.description or "")
        )
        prompt_changed = prompt != now
        if in_config:
            config["target_system_prompt"] = prompt
    connector = (
        str(kind) if kind != SourceKind.PROVIDER else str(current.get("connector"))
    )
    allowed = _SETTINGS.get(connector, ())
    for name, value in ((agent_payload or {}).get("config") or {}).items():
        if kind not in _PROMPT_KINDS or name not in allowed:
            raise _unsupported(f"agent_payload.config.{name}")
        config[name] = value
    if not prompt_changed and config == before:
        raise _unchanged()
    changes: dict[str, Any] = {}
    if config != before:
        # The environment's own source, so how it is reached stays as it was.
        changes["agent_payload"] = {**current, "config": config}
        changes["source"] = env_source
    if prompt_changed:
        changes["prompt"] = prompt
    return _made(
        parent,
        environment,
        user,
        trigger=Trigger.PROMPT if prompt_changed else Trigger.AGENT_PAYLOAD,
        **changes,
    )


def _made(
    parent: AgentVersion, environment: HostedHarnessJob, user: Any, **changes: Any
) -> AgentVersion:
    version = create_agent_version(
        parent.agent_definition, parent=parent, env=environment, user=user, **changes
    )
    if version.id == parent.id:
        # The same facts as the default: nothing to make.
        raise _unchanged()
    return version


# --- versions saved elsewhere ---------------------------------------------------------


def on_agent_version_saved(
    version_id: Any, *, definition_id: Any, status: str, created: bool
) -> None:
    """An agent version was saved (run on commit): restore drafts an activation
    elsewhere archived, and freeze a version the agent layer made."""
    from simulate.services.harness_versioning.versions import versioning_enabled

    if not versioning_enabled():
        return
    try:
        bound = (
            environment_owned(definition_id)
            and HostedHarnessJob.no_workspace_objects.filter(
                environment__isnull=True,
                deleted=False,
                agent_version__agent_definition_id=definition_id,
            ).exists()
        )
        if not bound:
            return
        if status == AgentVersion.StatusChoices.ACTIVE:
            restored = AgentVersion._base_manager.filter(
                agent_definition_id=definition_id,
                status=AgentVersion.StatusChoices.ARCHIVED,
                harness_runtime__preflight_status__in=(Status.PENDING, Status.FAILED),
            ).update(status=AgentVersion.StatusChoices.DRAFT, updated_at=timezone.now())
            if restored:
                logger.info(
                    "harness_agent_drafts_restored definition=%s count=%d",
                    definition_id,
                    restored,
                )
        if not created or _side_row(version_id) is not None:
            return
        try:
            from simulate.services.agent_setup import stored_agent  # noqa: F401
        except ImportError:
            return
        # TODO: freeze the provider facts from stored_agent with every binding live
        # head asking (explicit_reuse=False), then check it (agent layer).
    except Exception:  # noqa: BLE001 - never raised into the save
        logger.exception(
            "harness_agent_version_saved_failed version=%s definition=%s",
            version_id,
            definition_id,
        )
