"""Nudges: offer to rebuild an environment or its suite when what it was built on changed.

A nudge is raised after its trigger commits and shows as a system card in the
environment's chat. Its facts carry names, paths, statuses, counts and redacted patches
only; never a value, a fingerprint or a ref.
"""

from __future__ import annotations

import copy
import difflib
import logging
import re
from collections.abc import Iterable, Mapping
from functools import partial
from pathlib import PurePosixPath
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessNudge,
    HarnessSuiteValidation,
    HostedHarnessJob,
)
from simulate.services.harness_versioning import diffs
from simulate.services.harness_versioning import read as reads
from simulate.services.hosted_harness import HostedHarnessError, agent_version_pickable

logger = logging.getLogger(__name__)

State = HarnessNudge.State
Target = HarnessNudge.Target

PATCH_MAX_BYTES = 20 * 1024
PATCHES_MAX_BYTES = 200 * 1024
# GitHub's compare lists at most this many files.
FILES_MAX = 300
TEXT_MEMBER_MAX_BYTES = 1024 * 1024
NUDGE_DIFF_MAX_COMPRESSED = 64 * 1024 * 1024
NUDGE_DIFF_MAX_INFLATED = 256 * 1024 * 1024
COMPARE_CACHE_SECONDS = 600
LIST_LIMIT = 100
CARD_NAMES = 5
# Values this short are redacted as whole tokens in patches; shorter ones not at all.
SHORT_SECRET_MIN = 4
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
# A base64 line's trailing ``=`` is padding, not a separator.
_BASE64_LINE = re.compile(r"^[A-Za-z0-9+/]+=*$")
_HUNK = re.compile(r"^@@ -(\S+) \+(\S+) @@")
_GITHUB_STATUS = {
    "added": "added",
    "removed": "removed",
    "modified": "modified",
    "renamed": "renamed",
    "copied": "copied",
    "changed": "modified",
}
_INVERTED_STATUS = {"added": "removed", "removed": "added"}
_SOURCE_ID = {
    "github": "commit_sha",
    "archive": "archive_artifact_id",
    "provider": "assistant_id",
    "phone": "phone_number",
    "livekit": "agent_name",
}
# Prompt changes are reported as the prompt, not as a setting.
_PROMPT_SETTINGS = frozenset({"config.target_system_prompt"})
_ADC = "GOOGLE_APPLICATION_CREDENTIALS"
_IN_FLIGHT = (State.QUEUED, State.RUNNING)


def nudges_enabled() -> bool:
    return reads.versioning_flags()["nudges_enabled"]


def nudge_not_found() -> HostedHarnessError:
    return HostedHarnessError(
        "nudge_not_found", "no such nudge in this environment", status_code=404
    )


# --- scheduling ---------------------------------------------------------------------


def _start(kind: str, environment_id: str, *args: Any) -> None:
    from simulate.tasks import harness_nudges as tasks

    try:
        if kind == "raise":
            tasks.schedule_nudge(environment_id, *args)
        elif kind == "check":
            tasks.schedule_nudge_check_task(environment_id)
        else:
            tasks.schedule_reconcile_task(environment_id)
    except Exception as exc:  # noqa: BLE001 - the next trigger or reconcile recovers it
        logger.warning(
            "harness_nudge_schedule_failed type=%s environment=%s error=%s",
            kind,
            environment_id,
            type(exc).__name__,
        )


def schedule_environment_nudge(
    environment_id: Any,
    agent_version_id: Any,
    *,
    default_moved: bool,
    outgoing_agent_version_id: Any = None,
) -> None:
    """Raise the environment nudge after the caller commits; never raises."""
    if not nudges_enabled():
        return
    transaction.on_commit(
        partial(
            _start,
            "raise",
            str(environment_id),
            str(agent_version_id),
            bool(default_moved),
            str(outgoing_agent_version_id) if outgoing_agent_version_id else None,
        )
    )


def schedule_environment_moved(
    environment_id: Any,
    *,
    from_environment_version_id: Any,
    environment_version_id: Any,
) -> None:
    """Run ``on_environment_default_moved`` after the caller commits; never raises."""
    transaction.on_commit(
        partial(
            on_environment_default_moved,
            environment_id,
            from_environment_version_id=from_environment_version_id,
            environment_version_id=environment_version_id,
        )
    )


def schedule_reconcile(environment_id: Any) -> None:
    """Reconcile the environment's nudges after the caller commits; never raises."""
    if not nudges_enabled():
        return
    transaction.on_commit(partial(_start, "reconcile", str(environment_id)))


# --- guards -------------------------------------------------------------------------


def _head(environment_id: Any, *, lock: bool = False) -> HostedHarnessJob | None:
    heads = HostedHarnessJob.no_workspace_objects.filter(
        id=environment_id, environment__isnull=True
    )
    return (heads.select_for_update() if lock else heads).first()


def _raisable(environment: HostedHarnessJob | None) -> bool:
    from simulate.services.harness_agent_versions import _busy

    return (
        environment is not None
        and environment.environment_version_id is not None
        and not _busy(environment)
        and nudges_enabled()
    )


def _superseded(
    environment: HostedHarnessJob, agent_version_id: Any, default_moved: bool
) -> bool:
    """A default move another move replaced, or an offer the environment took since,
    can't pick, has a newer offer standing, or holds until its default's pending card
    is answered."""
    is_default = str(environment.agent_version_id) == str(agent_version_id)
    if default_moved:
        return not is_default
    environment_nudges = HarnessNudge.no_workspace_objects.filter(
        job=environment, target=Target.ENVIRONMENT
    )
    offered = AgentVersion._base_manager.filter(id=agent_version_id).first()
    return (
        is_default
        or not agent_version_pickable(environment, agent_version_id)
        or environment_nudges.filter(
            state=State.PENDING,
            trigger_agent_version_id=environment.agent_version_id,
            facts__agent_default_moved=True,
        ).exists()
        or environment_nudges.filter(
            trigger_agent_version__agent_definition_id=offered.agent_definition_id,
            trigger_agent_version__version_number__gt=offered.version_number or 0,
        )
        .exclude(state=State.REPLACED)
        .exclude(facts__agent_default_moved=True)
        .exists()
    )


def _close(nudges: Any, state: str, *, code: str | None = None) -> list[str]:
    ids = [str(one) for one in nudges.values_list("id", flat=True)]
    if ids:
        HarnessNudge.no_workspace_objects.filter(id__in=ids).update(
            state=state,
            updated_at=timezone.now(),
            **({"error": {"code": code}} if code else {}),
        )
        logger.info(
            "harness_nudge_closed nudges=%s state=%s code=%s",
            ",".join(ids),
            state,
            code,
        )
    return ids


def _close_cards(locked: HostedHarnessJob, nudges: Any, state: str) -> None:
    """Close environment nudges; an offer held behind a default-move card among them
    can be raised now, so reconcile once."""
    held = nudges.filter(facts__agent_default_moved=True).exists()
    if _close(nudges, state) and held:
        schedule_reconcile(locked.id)


# --- facts --------------------------------------------------------------------------


def _effective(environment: HostedHarnessJob, version: AgentVersion | None) -> Any:
    from simulate.services.harness_agent_versions import _side_row
    from simulate.services.harness_versioning.backfill import effective_runtime

    if version is None:
        return None
    return effective_runtime(environment, _side_row(version.id), version=version)


def _source(base: Any, new: Any) -> dict[str, Any]:
    kind = str(new.source_kind or "")
    changed = base is None or (base.source_kind, base.source or {}) != (
        new.source_kind,
        new.source or {},
    )
    field = _SOURCE_ID.get(kind)
    facts: dict[str, Any] = {"kind": kind, "changed": changed}
    if field is not None:
        facts["from"] = (
            str((base.source or {}).get(field) or "") or None
            if base is not None
            else None
        )
        facts["to"] = str((new.source or {}).get(field) or "") or None
    return facts


def _visibility(runtime: Any) -> str | None:
    access = (runtime.agent_payload or {}).get("source_access") or {}
    return access.get("visibility")


def _fingerprints_of(environment: HostedHarnessJob, runtime: Any) -> dict[str, str]:
    """A side's fingerprints by name; a legacy side's from this environment's own
    stored secrets, compared and dropped."""
    from simulate.services.harness_agent_versions import (
        _fingerprints,
        _legacy_values,
        own_credential_set,
    )

    if own_credential_set(runtime):
        return dict(runtime.credential_fingerprints or {})
    secret_set, provider_secrets = _legacy_values(environment, environment.organization)
    return _fingerprints(environment.organization_id, secret_set, provider_secrets)


def _variables(
    environment: HostedHarnessJob, base: Any, new: Any
) -> dict[str, list[str]]:
    from simulate.services.harness_agent_versions import version_credential_names

    new_names = set(version_credential_names(new))
    base_names = set(version_credential_names(base)) if base is not None else set()
    both = base_names & new_names
    variables = {
        **diffs._added_removed(base_names, new_names),
        "changed": [],
        "unknown": [],
    }
    if not both:
        return variables
    try:
        base_fingerprints = _fingerprints_of(environment, base)
        new_fingerprints = _fingerprints_of(environment, new)
    except Exception as exc:  # noqa: BLE001 - names only; the values stay unread
        logger.warning(
            "harness_nudge_values_unreadable environment=%s error=%s",
            environment.id,
            type(exc).__name__,
        )
        variables["unknown"] = sorted(both)
        return variables
    known = {
        name for name in both if name in base_fingerprints and name in new_fingerprints
    }
    variables["changed"] = diffs.keyed_diff(
        {name: base_fingerprints[name] for name in known},
        {name: new_fingerprints[name] for name in known},
    )["changed"]
    variables["unknown"] = sorted(both - known)
    return variables


def _settings(base: Any, new: Any) -> list[str]:
    from simulate.services.harness_agent_versions import _OWN_PAYLOAD_KEYS

    if base is None:
        return []

    def kept(runtime: Any) -> dict[str, Any]:
        return {
            key: value
            for key, value in (runtime.agent_payload or {}).items()
            if key not in _OWN_PAYLOAD_KEYS
        }

    before, after = kept(base), kept(new)
    changed = {
        key
        for key in set(before) | set(after)
        if key != "config" and before.get(key) != after.get(key)
    }
    before_config = before.get("config") or {}
    after_config = after.get("config") or {}
    changed |= {
        f"config.{key}"
        for key in set(before_config) | set(after_config)
        if before_config.get(key) != after_config.get(key)
    }
    return sorted(changed - _PROMPT_SETTINGS)


def _prompt_text(version: AgentVersion, runtime: Any) -> str:
    config = (runtime.agent_payload or {}).get("config") or {}
    prompt = config.get("target_system_prompt")
    return reads._prompt_or_description(version, prompt) or ""


def _unified(before: str, after: str, path: str = "prompt") -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )


def _secret_path(path: str, secret_adc_names: set[str]) -> bool:
    name = PurePosixPath(path).name
    return (
        (name.startswith(".env") and name != ".env.example")
        or name.endswith((".pem", ".key"))
        or name in secret_adc_names
    )


def _file(
    path: str,
    status: str,
    *,
    previous_path: str | None = None,
    additions: int | None = None,
    deletions: int | None = None,
    patch: str | None = None,
    patch_omitted: str | None = None,
) -> dict[str, Any]:
    return {
        "path": path,
        "status": status,
        "previous_path": previous_path,
        "additions": additions,
        "deletions": deletions,
        "patch": patch,
        "patch_omitted": patch_omitted,
    }


# --- GitHub -------------------------------------------------------------------------


def _compare(
    repository: str, base_sha: str, new_sha: str, organization_id: Any
) -> tuple[str | None, Any]:
    """``(unavailable, body)`` of one public compare, cached for a few minutes; within
    the organization's share of the hourly comparisons."""
    from simulate.services import harness_github

    key = f"harness:gh-compare:{repository.lower()}:{base_sha}:{new_sha}"
    cached = cache.get(key)
    if cached is not None:
        return None, cached
    response = harness_github.github_get(
        f"/repos/{repository}/compare/{base_sha}...{new_sha}",
        accept="application/vnd.github+json",
        purpose="compare",
        owner=str(organization_id),
    )
    if response.status in ("rate_limited", "budget"):
        return "rate_limited", None
    if response.status != "ok" or not isinstance(response.body, Mapping):
        return "github_error", None
    files = [
        one for one in response.body.get("files") or [] if isinstance(one, Mapping)
    ]
    body = {
        "status": str(response.body.get("status") or ""),
        "files": [
            {
                "filename": str(one.get("filename") or ""),
                "previous_filename": one.get("previous_filename"),
                "status": str(one.get("status") or ""),
                "additions": int(one.get("additions") or 0),
                "deletions": int(one.get("deletions") or 0),
                "patch": (
                    one.get("patch") if isinstance(one.get("patch"), str) else None
                ),
            }
            for one in files[:FILES_MAX]
        ],
        "truncated": len(files) >= FILES_MAX,
    }
    cache.set(key, body, COMPARE_CACHE_SECONDS)
    return None, body


def _invert_patch(patch: str) -> str:
    lines = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("@@"):
            line = _HUNK.sub(r"@@ -\2 +\1 @@", line)
        elif line.startswith("+"):
            line = "-" + line[1:]
        elif line.startswith("-"):
            line = "+" + line[1:]
        lines.append(line)
    return "".join(lines)


def _github_file(one: Mapping[str, Any], *, inverted: bool) -> dict[str, Any] | None:
    status = _GITHUB_STATUS.get(one["status"])
    if status is None:
        return None
    path, previous = one["filename"], one.get("previous_filename") or None
    additions, deletions, patch = one["additions"], one["deletions"], one["patch"]
    if inverted:
        status = _INVERTED_STATUS.get(status, status)
        if status == "renamed" and previous:
            path, previous = previous, path
        additions, deletions = deletions, additions
        patch = _invert_patch(patch) if patch is not None else None
    omitted = None
    if patch is None and status not in ("renamed", "copied"):
        # GitHub leaves out the patch of a binary or very large file.
        omitted = "too_large" if additions or deletions else "binary"
    return _file(
        path,
        status,
        previous_path=previous,
        additions=additions,
        deletions=deletions,
        patch=patch,
        patch_omitted=omitted,
    )


def _github_files(
    base: Any, new: Any, organization_id: Any
) -> tuple[str | None, list, bool, bool]:
    """``(unavailable, files, truncated, reversed)`` between two commits."""
    from simulate.services.harness_github import safe_repository

    if "private" in (_visibility(base), _visibility(new)):
        return "private_repository", [], False, False
    base_source, new_source = base.source or {}, new.source or {}
    repository = str(new_source.get("repository") or "")
    if str(base_source.get("repository") or "").lower() != repository.lower():
        return "repository_changed", [], False, False
    base_sha = str(base_source.get("commit_sha") or "")
    new_sha = str(new_source.get("commit_sha") or "")
    if not (_SHA.match(base_sha) and _SHA.match(new_sha)):
        return "no_commit", [], False, False
    if not safe_repository(repository):
        return "github_error", [], False, False
    unavailable, body = _compare(repository, base_sha, new_sha, organization_id)
    if unavailable is not None:
        return unavailable, [], False, False
    inverted = body["status"] == "behind"
    if inverted:
        # A roll back to an ancestor: the forward compare of the two, read backwards.
        unavailable, body = _compare(repository, new_sha, base_sha, organization_id)
        if unavailable is not None:
            return unavailable, [], False, False
    elif body["status"] not in ("ahead", "identical"):
        return "not_linear", [], False, False
    files = [
        found
        for one in body["files"]
        if (found := _github_file(one, inverted=inverted)) is not None
    ]
    return None, files, bool(body["truncated"]), inverted


# --- uploads ------------------------------------------------------------------------


def _strip_root(path: str) -> str:
    return path[len("source/") :] if path.startswith("source/") else path


def _archive_body(organization_id: Any, archive_id: str) -> bytes | str:
    """The upload's bytes, or why they can't be compared."""
    from minio.error import S3Error

    from simulate.services.harness_versioning import archives
    from simulate.services.hosted_harness_gateway import _source_object_key

    key = _source_object_key(organization_id, archive_id)
    size = archives.archive_size(key)
    if size is None:
        return "archive_missing"
    if size > NUDGE_DIFF_MAX_COMPRESSED:
        return "archive_too_large"
    try:
        body = archives.get_archive(key)
    except S3Error:
        return "archive_missing"
    if archives.archive_stats(body)["inflated_bytes"] > NUDGE_DIFF_MAX_INFLATED:
        return "archive_too_large"
    return body


def _archive_files(
    environment: HostedHarnessJob, base: Any, new: Any
) -> tuple[str | None, list]:
    """``(unavailable, files)`` between two uploads, by member digest."""
    from simulate.services.harness_versioning import archives

    bodies = []
    for runtime in (base, new):
        archive_id = str((runtime.source or {}).get("archive_artifact_id") or "")
        if not archive_id:
            return "archive_missing", []
        found = _archive_body(environment.organization_id, archive_id)
        if isinstance(found, str):
            return found, []
        bodies.append(found)
    before = archives.member_digests(bodies[0])
    after = archives.member_digests(bodies[1])
    changes = sorted(
        (path, status)
        for path, status in (
            *((path, "added") for path in set(after) - set(before)),
            *((path, "removed") for path in set(before) - set(after)),
            *(
                (path, "modified")
                for path in set(before) & set(after)
                if before[path][0] != after[path][0]
            ),
        )
    )
    paths = {path for path, _status in changes}
    old_text = archives.read_members(bodies[0], paths, TEXT_MEMBER_MAX_BYTES)
    new_text = archives.read_members(bodies[1], paths, TEXT_MEMBER_MAX_BYTES)
    files = []
    for path, status in changes:
        size = max(before.get(path, ("", 0))[1], after.get(path, ("", 0))[1])
        shown = _strip_root(path)
        if size > TEXT_MEMBER_MAX_BYTES:
            files.append(_file(shown, status, patch_omitted="too_large"))
            continue
        try:
            old = old_text.get(path, b"").decode("utf-8")
            new_body = new_text.get(path, b"").decode("utf-8")
        except UnicodeDecodeError:
            files.append(_file(shown, status, patch_omitted="binary"))
            continue
        patch = _unified(old, new_body, shown)
        lines = patch.splitlines()
        files.append(
            _file(
                shown,
                status,
                additions=sum(
                    1
                    for line in lines
                    if line.startswith("+") and not line.startswith("+++")
                ),
                deletions=sum(
                    1
                    for line in lines
                    if line.startswith("-") and not line.startswith("---")
                ),
                patch=patch or None,
            )
        )
    return None, files


# --- redaction ----------------------------------------------------------------------


def _agent_secrets(
    environment: HostedHarnessJob, agent_version_ids: Iterable[Any]
) -> dict[str, set[str]]:
    """Every value the versions hold, by name; a legacy one's from this environment's
    stored secrets. Kept in memory only."""
    from simulate.services.harness_agent_versions import (
        _flat_values,
        _legacy_values,
        _version_values,
    )

    secret_values: dict[str, set[str]] = {}
    legacy_read = False
    # No version named: the sandbox ran with this environment's stored secrets.
    for version_id in {str(one) for one in agent_version_ids if one} or {None}:
        version_secrets = version_id and _version_values(
            environment.organization_id, version_id
        )
        if version_secrets is None:
            if legacy_read:
                continue
            legacy_read = True
            secret_set, provider_secrets = _legacy_values(
                environment, environment.organization
            )
            version_secrets = _flat_values(secret_set, provider_secrets, "replace")
        for name, secret_value in version_secrets.items():
            secret_values.setdefault(name, set()).add(str(secret_value))
    return secret_values


def _secret_line_parts(secret_value: str) -> list[str]:
    """What follows each line's first ``=`` or ``:``, as a diff shows a file one line
    at a time; of a multi-line value, also each line without one (a PEM body line)."""
    secret_lines = [line.strip() for line in secret_value.strip().splitlines()]
    secret_parts = []
    for secret_line in secret_lines:
        if re.search("[=:]", secret_line) and not _BASE64_LINE.match(secret_line):
            rhs = re.split("[=:]", secret_line, maxsplit=1)[1].strip()
            quoted = re.match(r"([\"'])(.*?)\1", rhs)
            secret_parts.append(quoted.group(2) if quoted else rhs.strip("\"'"))
            # An inline ``# comment`` or a trailing ``;`` is not part of the value.
            secret_parts.append(re.sub(r"\s+#.*|\s*;$", "", rhs).strip("\"'"))
        elif len(secret_lines) > 1:
            secret_parts.append(secret_line)
    return [secret_part for secret_part in secret_parts if secret_part]


def _cut_value_start(text: str, secret_values: Iterable[str]) -> int:
    """Where ``text``'s end starts a value, or its length: a cut text may end inside
    one, which then matches nothing."""
    secret_start = len(text)
    for secret_value in secret_values:
        secret_head = secret_value[:SHORT_SECRET_MIN]
        if len(secret_head) < SHORT_SECRET_MIN:
            continue
        at = text.find(secret_head, max(0, len(text) - len(secret_value)))
        while 0 <= at < secret_start:
            if text.startswith(secret_value[: len(text) - at], at):
                secret_start = at
                break
            at = text.find(secret_head, at + 1)
    # Never inside a whole value: the front would keep only part of it, unmatched.
    moved = secret_start < len(text)
    while moved:
        moved = False
        for secret_value in secret_values:
            # Only occurrences straddling the start; a run of them is followed back.
            while True:
                at = text.find(
                    secret_value,
                    max(0, secret_start - len(secret_value) + 1),
                    secret_start + len(secret_value) - 1,
                )
                if not 0 <= at < secret_start:
                    break
                secret_start, moved = at, True
    return secret_start


def _redact_texts(
    texts: list[str], secret_values: Iterable[str], *, cut: Iterable[bool] = ()
) -> list[str]:
    from simulate.services.hosted_harness_diagnostics import (
        _normalize_log_text,
        _redact,
        _secret_fragments,
    )

    # Locals holding values are named for the error tracker's scrubber (``secret``).
    secret_values = list(secret_values)
    secret_whole = {
        secret_part
        for secret_value in secret_values
        for secret_part in (secret_value, secret_value.strip())
    }
    # Parts taken from lines are masked only at 8 or more characters, so a config
    # file's short words (``json``, ``default``) stay; a longer non-secret one
    # (``us-east-1``) is masked, as nothing tells it from a secret.
    secret_fragments = _secret_fragments(
        [
            *secret_whole,
            *(
                secret_part
                for secret_value in secret_values
                for secret_part in _secret_line_parts(secret_value)
            ),
        ]
    )
    # ``_secret_fragments`` skips values under 8 characters.
    secret_short = sorted(
        {
            secret_value
            for secret_value in secret_whole
            if SHORT_SECRET_MIN <= len(secret_value) < 8
        },
        key=len,
        reverse=True,
    )
    secret_patterns = [
        re.compile(rf"(?<![A-Za-z0-9_]){re.escape(secret_value)}(?![A-Za-z0-9_])")
        for secret_value in secret_short
    ]
    cut = list(cut)
    redacted = []
    for index, text in enumerate(texts):
        secret_end = ""
        if index < len(cut) and cut[index]:
            # Before masking, which may rewrite part of the cut value's start.
            text = _normalize_log_text(text)
            at = _cut_value_start(text, [*secret_fragments, *secret_short])
            if at < len(text):
                text, secret_end = text[:at], "[REDACTED]"
        text = _redact(text, secret_fragments)
        for secret_pattern in secret_patterns:
            text = secret_pattern.sub("[REDACTED]", text)
        redacted.append(text + secret_end)
    return redacted


def _every_value(secret_values: Mapping[str, set[str]]) -> list[str]:
    return [
        secret_value
        for secret_group in secret_values.values()
        for secret_value in secret_group
    ]


def redact_with_agent_values(
    environment: HostedHarnessJob,
    texts: list[str],
    agent_version_ids: Iterable[Any],
    *,
    cut: Iterable[bool] = (),
) -> list[str]:
    """``texts`` with every value of these agent versions masked; values of 8 or more
    characters anywhere, of 4 to 7 as whole tokens. A text flagged in ``cut`` also
    loses an end that starts a value. Raises ``redaction_unavailable`` when the values
    can't be read."""
    try:
        secret_values = _agent_secrets(environment, agent_version_ids)
    except Exception:  # noqa: BLE001 - nothing of a value may reach the error
        raise HostedHarnessError(
            "redaction_unavailable", "the agent's values can't be read", status_code=409
        ) from None
    return _redact_texts(texts, _every_value(secret_values), cut=cut)


def _mark_secret_paths(files: list[dict[str, Any]], secret_adc_names: set[str]) -> None:
    for one in files:
        if _secret_path(one["path"], secret_adc_names) or (
            one["previous_path"]
            and _secret_path(one["previous_path"], secret_adc_names)
        ):
            one.update(patch=None, patch_omitted="secret_path")


def _cap_patches(
    environment: HostedHarnessJob,
    facts: dict[str, Any],
    agent_version_ids: list[Any],
) -> None:
    """Redact, cap and drop patches in place; names and counts stay."""
    files = facts["changed_files"]
    if not facts.get("prompt_patch") and all(one["patch"] is None for one in files):
        # Nothing to redact, so no value is read.
        _mark_secret_paths(files, set())
        return
    try:
        secret_values = _agent_secrets(environment, agent_version_ids)
    except Exception as exc:  # noqa: BLE001 - patches go, names stay
        logger.warning(
            "harness_nudge_redaction_unavailable environment=%s error=%s",
            environment.id,
            type(exc).__name__,
        )
        for one in files:
            if one["patch"] is not None:
                one.update(patch=None, patch_omitted="redaction_unavailable")
        facts["prompt_patch"] = None
        return
    secret_adc_names = {
        PurePosixPath(secret_path).name
        for secret_path in secret_values.get(_ADC, ())
        if secret_path
    }
    _mark_secret_paths(files, secret_adc_names)
    texts = [one["patch"] for one in files if one["patch"] is not None]
    if facts.get("prompt_patch"):
        texts.append(facts["prompt_patch"])
    redacted = iter(_redact_texts(texts, _every_value(secret_values)))
    spent = 0
    for one in files:
        if one["patch"] is None:
            continue
        patch = next(redacted)
        size = len(patch.encode("utf-8"))
        if size > PATCH_MAX_BYTES:
            one.update(patch=None, patch_omitted="too_large")
        elif spent + size > PATCHES_MAX_BYTES:
            one.update(patch=None, patch_omitted="budget")
        else:
            spent += size
            one["patch"] = patch
    if facts.get("prompt_patch"):
        prompt_patch = next(redacted)
        facts["prompt_patch"] = (
            prompt_patch
            if len(prompt_patch.encode("utf-8")) <= PATCH_MAX_BYTES
            else None
        )


def _no_facts(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "changed_files": [],
        # Set only once a file list was read.
        "files_total": None,
        "files_truncated": False,
        "reversed": False,
        "files_unavailable": None,
        "prompt_changed": False,
        "prompt_patch": None,
        "variables": {"added": [], "removed": [], "changed": [], "unknown": []},
        "settings_changed": [],
    }


def diff_facts(
    environment: HostedHarnessJob, base: AgentVersion | None, new: AgentVersion
) -> dict[str, Any]:
    """What changed from ``base`` to ``new`` as this environment runs them; never
    raises for a network, storage or decrypt failure, which becomes a reason."""
    new_runtime = _effective(environment, new)
    if base is not None and base.id == new.id:
        # The same version: nothing changed, and no value is read.
        return _no_facts({"kind": str(new_runtime.source_kind or ""), "changed": False})
    base_runtime = _effective(environment, base)
    source = _source(base_runtime, new_runtime)
    facts: dict[str, Any] = {
        **_no_facts(source),
        "variables": _variables(environment, base_runtime, new_runtime),
        "settings_changed": _settings(base_runtime, new_runtime),
    }
    if base_runtime is None:
        return facts
    if (
        base_runtime.prompt_hash
        and new_runtime.prompt_hash
        and base_runtime.prompt_hash != new_runtime.prompt_hash
    ):
        facts["prompt_changed"] = True
        facts["prompt_patch"] = (
            _unified(_prompt_text(base, base_runtime), _prompt_text(new, new_runtime))
            or None
        )
    # TODO: provider setup facts once the agent layer's agent summary lands.
    kind = source["kind"]
    if source["changed"] and kind == base_runtime.source_kind:
        try:
            if kind == "github":
                unavailable, files, truncated, inverted = _github_files(
                    base_runtime, new_runtime, environment.organization_id
                )
                facts.update(files_truncated=truncated, reversed=inverted)
            elif kind == "archive":
                unavailable, files = _archive_files(
                    environment, base_runtime, new_runtime
                )
            else:
                unavailable, files = None, []
        except Exception as exc:  # noqa: BLE001 - a reason, never a retry
            logger.warning(
                "harness_nudge_files_failed environment=%s kind=%s error=%s",
                environment.id,
                kind,
                type(exc).__name__,
            )
            unavailable = "github_error" if kind == "github" else "archive_unreadable"
            files = []
        facts["files_unavailable"] = unavailable
        facts["changed_files"] = files
        if unavailable is None and kind in ("github", "archive"):
            facts["files_total"] = len(files)
    _cap_patches(environment, facts, [base.id, new.id])
    logger.info(
        "harness_nudge_facts environment=%s source_kind=%s files=%s unavailable=%s",
        environment.id,
        kind,
        len(facts["changed_files"]),
        facts["files_unavailable"],
    )
    return facts


def facts_empty(facts: Mapping[str, Any]) -> bool:
    """Nothing a rebuild would act on. A source change counts unless its file list was
    read and is empty (an empty commit)."""
    variables = facts.get("variables") or {}
    if (
        facts.get("changed_files")
        or facts.get("files_unavailable") is not None
        or facts.get("prompt_changed")
        or any(variables.values())
        or facts.get("settings_changed")
    ):
        return False
    return (
        not (facts.get("source") or {}).get("changed") or facts.get("files_total") == 0
    )


def _suite_facts(
    environment: HostedHarnessJob,
    base_environment_version_id: Any,
    environment_version_id: Any,
) -> dict[str, Any]:
    try:
        found = reads.version_diff(
            environment,
            "environment",
            environment_version_id,
            against_id=base_environment_version_id,
        )
    except HostedHarnessError:
        found = reads.version_diff(environment, "environment", environment_version_id)
    return {
        "environment_diff": found["diff"],
        "environment_summary": found["summary"],
        "from_environment_version_id": (
            str(base_environment_version_id) if base_environment_version_id else None
        ),
    }


def _base_agent(
    environment: HostedHarnessJob,
    new: AgentVersion,
    *,
    default_moved: bool,
    outgoing_agent_version_id: Any = None,
) -> AgentVersion | None:
    """What the environment was built with; else what it ran before; else the parent."""
    environment_version = environment.environment_version
    base_id = (
        environment_version.agent_version_id
        if environment_version is not None
        else None
    )
    if base_id is None:
        base_id = (
            outgoing_agent_version_id if default_moved else environment.agent_version_id
        )
    if base_id is None:
        base_id = (
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version_id=new.id
            )
            .values_list("parent__agent_version_id", flat=True)
            .first()
        )
    if base_id is None:
        return None
    return AgentVersion._base_manager.filter(id=base_id).first()


def nudge_facts(environment: HostedHarnessJob, nudge: HarnessNudge) -> dict[str, Any]:
    """The facts a nudge was raised with, computed again."""
    if nudge.target == Target.SUITE:
        return _suite_facts(
            environment,
            (nudge.facts or {}).get("from_environment_version_id"),
            nudge.base_environment_version_id,
        )
    facts = diff_facts(
        environment, nudge.base_agent_version, nudge.trigger_agent_version
    )
    facts["agent_default_moved"] = bool((nudge.facts or {}).get("agent_default_moved"))
    return facts


# --- the card -----------------------------------------------------------------------


def _names(names: list[str]) -> str:
    shown = ", ".join(names[:CARD_NAMES])
    more = len(names) - CARD_NAMES
    return f"{shown} and {more} more" if more > 0 else shown


def _changes(facts: Mapping[str, Any]) -> str:
    variables = facts.get("variables") or {}
    names = sorted({name for values in variables.values() for name in values or []})
    parts = [
        _names([one["path"] for one in facts.get("changed_files") or []]),
        "prompt" if facts.get("prompt_changed") else "",
        f"variables {_names(names)}" if names else "",
        (
            f"settings {_names(list(facts.get('settings_changed') or []))}"
            if facts.get("settings_changed")
            else ""
        ),
    ]
    text = "; ".join(part for part in parts if part)
    return text or "source"


def _card(target: str, facts: Mapping[str, Any], label: str) -> str:
    if target == Target.SUITE:
        summary = facts.get("environment_summary") or "it changed"
        return f"The environment changed ({label}): {summary}. Rebuild the scenarios?"
    if facts.get("agent_default_moved"):
        return f"Agent {label} changed: {_changes(facts)}. Rebuild the environment?"
    return (
        f"Agent {label} is available ({_changes(facts)} changed). Rebuild this"
        " environment with it?"
    )


def upsert_nudge(
    locked_env: HostedHarnessJob,
    target: str,
    *,
    facts: dict[str, Any],
    trigger_agent_version_id: Any = None,
    base_agent_version_id: Any = None,
    base_environment_version_id: Any = None,
    base_suite_version_id: Any = None,
) -> HarnessNudge:
    """A pending nudge with its card; it replaces the target's open one. The caller
    holds the environment's lock."""
    from simulate.services.hosted_harness_conversation import post_system_message

    replaced = HarnessNudge.no_workspace_objects.filter(
        job=locked_env, target=target, state__in=(State.PENDING, State.FAILED)
    ).update(state=State.REPLACED, updated_at=timezone.now())
    nudge = HarnessNudge.no_workspace_objects.create(
        job=locked_env,
        organization_id=locked_env.organization_id,
        workspace_id=locked_env.workspace_id,
        target=target,
        state=State.PENDING,
        facts=facts,
        trigger_agent_version_id=trigger_agent_version_id,
        base_agent_version_id=base_agent_version_id,
        base_environment_version_id=base_environment_version_id,
        base_suite_version_id=base_suite_version_id,
    )
    label = _trigger_label(nudge)
    nudge.conversation_message = post_system_message(
        locked_env,
        content=_card(target, facts, reads._message_label(label)),
        payload={
            "nudge_id": str(nudge.id),
            "nudge_target": target,
            "trigger_label": label,
        },
    )
    nudge.save(update_fields=["conversation_message", "updated_at"])
    logger.info(
        "harness_nudge_raised nudge=%s target=%s environment=%s trigger=%s replaced=%s",
        nudge.id,
        target,
        locked_env.id,
        trigger_agent_version_id,
        replaced,
    )
    return nudge


# --- raise --------------------------------------------------------------------------


def _blocking(nudges: Any, default_moved: bool) -> HarnessNudge | None:
    """The row that stands for this raise, if any; a pending one learns that the
    default moved."""
    open_row = nudges.filter(state__in=(State.PENDING, *_IN_FLIGHT)).first()
    if open_row is not None:
        if (
            open_row.state == State.PENDING
            and default_moved
            and not (open_row.facts or {}).get("agent_default_moved")
        ):
            open_row.facts = {**(open_row.facts or {}), "agent_default_moved": True}
            open_row.save(update_fields=["facts", "updated_at"])
        return open_row
    final = (
        nudges.filter(
            Q(state__in=(State.ANSWERED_NO, State.APPLIED))
            | Q(state=State.NO_CHANGE, answered_at__isnull=False)
        )
        .order_by("-created_at")
        .first()
    )
    if (
        final is not None
        and final.state == State.ANSWERED_NO
        and default_moved
        and not (final.facts or {}).get("agent_default_moved")
    ):
        # A skipped offer, then the version made the default by hand: ask again.
        return None
    return final


class _EnvironmentMoved(Exception):
    """The environment version moved while a raise read its facts."""


def raise_environment_nudge(
    environment_id: Any,
    agent_version_id: Any,
    *,
    default_moved: bool,
    outgoing_agent_version_id: Any = None,
) -> HarnessNudge | None:
    """Offer to rebuild the environment for an agent version made its default (or
    available to it); ``None`` when nothing changed or nothing is to be raised. Read
    again once if the environment version moves meanwhile; a second move raises, so
    the activity retries."""
    options = {
        "default_moved": default_moved,
        "outgoing_agent_version_id": outgoing_agent_version_id,
    }
    try:
        return _raise_environment_nudge(environment_id, agent_version_id, **options)
    except _EnvironmentMoved:
        return _raise_environment_nudge(environment_id, agent_version_id, **options)


def _raise_environment_nudge(
    environment_id: Any,
    agent_version_id: Any,
    *,
    default_moved: bool,
    outgoing_agent_version_id: Any = None,
) -> HarnessNudge | None:
    environment = _head(environment_id)
    if not _raisable(environment) or _superseded(
        environment, agent_version_id, default_moved
    ):
        return None
    new = AgentVersion._base_manager.filter(id=agent_version_id).first()
    if new is None:
        return None
    base = _base_agent(
        environment,
        new,
        default_moved=default_moved,
        outgoing_agent_version_id=outgoing_agent_version_id,
    )
    # Outside any transaction: it may read storage or GitHub.
    facts = diff_facts(environment, base, new)
    with transaction.atomic():
        locked = _head(environment_id, lock=True)
        if not _raisable(locked) or _superseded(locked, new.id, default_moved):
            return None
        if locked.environment_version_id != environment.environment_version_id:
            raise _EnvironmentMoved
        open_nudges = HarnessNudge.no_workspace_objects.filter(
            job=locked, target=Target.ENVIRONMENT, state=State.PENDING
        )
        stale_default = Q(facts__agent_default_moved=True) & ~Q(
            trigger_agent_version_id=locked.agent_version_id
        )
        if facts_empty(facts):
            _close_cards(
                locked,
                open_nudges.filter(Q(trigger_agent_version_id=new.id) | stale_default),
                State.NO_CHANGE,
            )
            empty = {
                "job": locked,
                "target": Target.ENVIRONMENT,
                "state": State.NO_CHANGE,
                "trigger_agent_version_id": new.id,
            }
            if (
                not default_moved
                and not HarnessNudge.no_workspace_objects.filter(**empty).exists()
            ):
                # An offer found empty is not diffed again by _missed_offer.
                HarnessNudge.no_workspace_objects.create(
                    **empty,
                    organization_id=locked.organization_id,
                    workspace_id=locked.workspace_id,
                    facts={**facts, "agent_default_moved": False},
                    base_environment_version_id=locked.environment_version_id,
                )
            logger.info(
                "harness_nudge_raised environment=%s trigger=%s state=no_change",
                locked.id,
                new.id,
            )
            return None
        same = HarnessNudge.no_workspace_objects.filter(
            job=locked,
            target=Target.ENVIRONMENT,
            trigger_agent_version_id=new.id,
            base_environment_version_id=locked.environment_version_id,
        ).order_by("-created_at")
        existing = _blocking(same, default_moved)
        if existing is not None:
            if default_moved:
                _close_cards(locked, open_nudges.filter(stale_default), State.REPLACED)
            return existing
        return upsert_nudge(
            locked,
            Target.ENVIRONMENT,
            facts={**facts, "agent_default_moved": bool(default_moved)},
            trigger_agent_version_id=new.id,
            base_agent_version_id=base.id if base is not None else None,
            base_environment_version_id=locked.environment_version_id,
            base_suite_version_id=locked.suite_version_id,
        )


def raise_suite_nudge(
    environment_id: Any,
    *,
    base_environment_version_id: Any,
    environment_version_id: Any,
    facts: dict[str, Any] | None = None,
) -> HarnessNudge | None:
    """Offer to rebuild the suite for a new default environment version, unless the
    default suite is already proved against it."""
    environment = _head(environment_id)
    if not _raisable(environment):
        return None
    if facts is None:
        facts = _suite_facts(
            environment, base_environment_version_id, environment_version_id
        )
    with transaction.atomic():
        locked = _head(environment_id, lock=True)
        if not _raisable(locked) or str(locked.environment_version_id) != str(
            environment_version_id
        ):
            return None
        open_nudges = HarnessNudge.no_workspace_objects.filter(
            job=locked, target=Target.SUITE, state=State.PENDING
        )
        if HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version_id=locked.suite_version_id,
            environment_version_id=locked.environment_version_id,
            status=HarnessSuiteValidation.Status.PASSED,
        ).exists():
            _close(open_nudges, State.NO_CHANGE)
            return None
        same = HarnessNudge.no_workspace_objects.filter(
            job=locked,
            target=Target.SUITE,
            base_environment_version_id=locked.environment_version_id,
        ).order_by("-created_at")
        existing = _blocking(same, False)
        if existing is not None:
            return existing
        return upsert_nudge(
            locked,
            Target.SUITE,
            facts=facts,
            base_environment_version_id=locked.environment_version_id,
            base_suite_version_id=locked.suite_version_id,
        )


def _recheck_environment_nudges(environment_id: Any) -> None:
    """An open environment nudge against an older environment version: closed when the
    new one was built with its trigger, else raised again against the new one."""
    with transaction.atomic():
        locked = _head(environment_id, lock=True)
        if not _raisable(locked):
            return
        built_with = locked.environment_version.agent_version_id
        for nudge in HarnessNudge.no_workspace_objects.filter(
            job=locked, target=Target.ENVIRONMENT, state=State.PENDING
        ):
            if nudge.trigger_agent_version_id == built_with:
                _close_cards(
                    locked,
                    HarnessNudge.no_workspace_objects.filter(id=nudge.id),
                    State.NO_CHANGE,
                )
            elif nudge.base_environment_version_id != locked.environment_version_id:
                schedule_environment_nudge(
                    locked.id,
                    nudge.trigger_agent_version_id,
                    default_moved=bool((nudge.facts or {}).get("agent_default_moved")),
                )


def on_environment_default_moved(
    environment_id: Any,
    *,
    from_environment_version_id: Any,
    environment_version_id: Any,
) -> None:
    """After an environment default move commits: the suite nudge, and a re-check of
    the open environment nudge. Never raises into the caller."""
    try:
        raise_suite_nudge(
            environment_id,
            base_environment_version_id=from_environment_version_id,
            environment_version_id=environment_version_id,
        )
    except Exception:  # noqa: BLE001 - a save never fails for a nudge
        logger.exception("harness_nudge_suite_failed environment=%s", environment_id)
    try:
        _recheck_environment_nudges(environment_id)
    except Exception:  # noqa: BLE001 - a save never fails for a nudge
        logger.exception("harness_nudge_recheck_failed environment=%s", environment_id)


def _missed_offer(environment: HostedHarnessJob) -> Any:
    """The one offer a reconcile may raise. At most one environment nudge is pending,
    and a pending default-move card wins: the raise holds an offer behind it. Else
    the candidate is the newest passed version above the default that this
    environment can pick and whose recorded activation is waiting; older ones are
    never offered. It is offered only if it has no environment nudge other than
    replaced ones: a skipped, open or empty (no_change) one stands. A direct offer
    holds while such an offer row stands for a newer version. Closing a pending
    default-move card schedules one reconcile; a reconcile that closes none
    schedules none."""
    from simulate.services.harness_agent_versions import _OFFERED, lineage_version_ids

    default = AgentVersion._base_manager.filter(id=environment.agent_version_id).first()
    if default is None:
        return None
    waiting = ("environment_busy", "not_requested", *_OFFERED)
    allowed = lineage_version_ids(environment, default.agent_definition_id)
    for runtime in (
        HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_definition_id=default.agent_definition_id,
            preflight_status=HarnessAgentVersionRuntime.PreflightStatus.PASSED,
            agent_version__version_number__gt=default.version_number or 0,
        )
        .defer("credential_blob")
        .order_by("-agent_version__version_number")
    ):
        if allowed is not None and str(runtime.agent_version_id) not in allowed:
            continue
        outcome = reads._activation_outcome(runtime.preflight_report, environment.id)
        if outcome in waiting and agent_version_pickable(
            environment, runtime.agent_version_id
        ):
            break
    else:
        return None
    if outcome == "environment_busy":
        # A busy environment's activation is retried, not offered.
        return None
    offered = (
        HarnessNudge.no_workspace_objects.filter(
            job=environment,
            target=Target.ENVIRONMENT,
            trigger_agent_version_id=runtime.agent_version_id,
        )
        .exclude(state=State.REPLACED)
        .exists()
    )
    return None if offered else runtime.agent_version_id


def _reconcile_suite(environment: HostedHarnessJob) -> None:
    """The suite nudge a busy environment skipped, or a stale one closed or replaced;
    none while the environment has only its first version."""
    from simulate.models import HarnessEnvironmentVersion

    if (
        HarnessEnvironmentVersion.no_workspace_objects.filter(job=environment)[
            :2
        ].count()
        < 2
    ):
        return
    pending = (
        HarnessNudge.no_workspace_objects.filter(
            job=environment, target=Target.SUITE, state=State.PENDING
        )
        .order_by("-created_at")
        .first()
    )
    raise_suite_nudge(
        environment.id,
        base_environment_version_id=(
            pending.base_environment_version_id if pending is not None else None
        ),
        environment_version_id=environment.environment_version_id,
    )


def reconcile_environment_nudges(environment_id: Any) -> str:
    """Raise what busy guards, flag timing or a lost schedule skipped; the one offer
    is ``_missed_offer``'s. It schedules another reconcile only when it closes a
    pending default-move card, which the next one can't raise again."""
    environment = _head(environment_id)
    if not _raisable(environment):
        return "skipped"
    if environment.environment_version.agent_version_id is not None:
        raise_environment_nudge(
            environment.id, environment.agent_version_id, default_moved=True
        )
    _recheck_environment_nudges(environment.id)
    _reconcile_suite(environment)
    offered = _missed_offer(environment)
    if offered is not None:
        raise_environment_nudge(environment.id, offered, default_moved=False)
    # TODO: also run this for environments whose schedule failed, and once when the
    # nudges flag is turned on; a sweep of every versioned environment reads GitHub and
    # storage, so it needs its own budget.
    logger.info("harness_nudge_reconciled environment=%s outcome=done", environment.id)
    return "reconciled"


# --- close --------------------------------------------------------------------------


def close_environment_nudges(locked: HostedHarnessJob, *, code: str) -> list[str]:
    """Close a deleted environment's open nudges as failed with ``code``."""
    from simulate.services.harness_versioning.rebuild import close_open_nudges

    close_open_nudges(locked, code=code)
    return _close(
        HarnessNudge.no_workspace_objects.filter(
            job=locked, state__in=(State.PENDING, State.FAILED)
        ),
        State.FAILED,
        code=code,
    )


# --- read and answer ----------------------------------------------------------------


def _trigger_label(nudge: HarnessNudge) -> str | None:
    if nudge.target == Target.SUITE:
        version = nudge.base_environment_version
        return reads._label(version.version_number) if version is not None else None
    version = nudge.trigger_agent_version
    if version is None:
        return None
    return reads._agent_label(version.version_name, version.version_number)


def _public_facts(facts: Mapping[str, Any] | None) -> dict[str, Any]:
    """The facts the response serializers declare; anything else stays in the row."""
    from simulate.serializers.harness_environment import (
        HarnessNudgeFactsSerializer,
        HarnessNudgeFileSerializer,
    )

    public = {
        key: copy.deepcopy(value)
        for key, value in (facts or {}).items()
        if key in HarnessNudgeFactsSerializer._declared_fields
        and key != "changed_files"
    }
    public["changed_files"] = [
        {
            **{
                key: one.get(key)
                for key in HarnessNudgeFileSerializer._declared_fields
                if key != "has_patch"
            },
            "has_patch": one.get("patch") is not None,
        }
        for one in (facts or {}).get("changed_files") or []
        if isinstance(one, Mapping)
    ]
    return public


def _public_error(error: Any) -> dict[str, Any] | None:
    if not isinstance(error, Mapping) or not error.get("code"):
        return None
    return {
        "code": str(error["code"]),
        **({"message": str(error["message"])} if error.get("message") else {}),
        **({"problems": list(error["problems"])} if error.get("problems") else {}),
    }


def _queued_behind_input(nudge: HarnessNudge) -> bool:
    from simulate.models import HostedHarnessConversation

    return (
        nudge.state == State.QUEUED
        and HostedHarnessConversation.no_workspace_objects.filter(
            job_id=nudge.job_id, blocking_input__isnull=False
        ).exists()
    )


def nudge_item(
    nudge: HarnessNudge, *, replaced_by: HarnessNudge | None = None
) -> dict[str, Any]:
    def ref(agent: Any = None, environment_version: Any = None, suite: Any = None):
        refs = reads.version_refs(agent, environment_version, suite)
        return refs["agent"] or refs["environment"] or refs["suite"]

    return {
        "id": str(nudge.id),
        "target": nudge.target,
        "state": nudge.state,
        "facts": _public_facts(nudge.facts),
        "trigger_agent_version": ref(agent=nudge.trigger_agent_version),
        "base_agent_version": ref(agent=nudge.base_agent_version),
        "base_environment_version": ref(
            environment_version=nudge.base_environment_version
        ),
        "base_suite_version": ref(suite=nudge.base_suite_version),
        "result_environment_version": ref(
            environment_version=nudge.result_environment_version
        ),
        "result_suite_version": ref(suite=nudge.result_suite_version),
        "summary": nudge.summary,
        "error": _public_error(nudge.error),
        "closed_at": nudge.closed_at.isoformat() if nudge.closed_at else None,
        "queued_behind_input": _queued_behind_input(nudge),
        "can_stop": nudge.state in _IN_FLIGHT,
        "replaced_by": (
            {"id": str(replaced_by.id), "trigger_label": _trigger_label(replaced_by)}
            if replaced_by is not None
            else None
        ),
        "card_message_id": (
            str(nudge.conversation_message_id)
            if nudge.conversation_message_id
            else None
        ),
        "answered_by": reads._person(nudge.answered_by),
        "answered_at": nudge.answered_at.isoformat() if nudge.answered_at else None,
        "created_at": nudge.created_at.isoformat(),
    }


def _nudges(environment: HostedHarnessJob) -> Any:
    agents = ("trigger_agent_version", "base_agent_version")
    pieces = (
        "base_environment_version",
        "base_suite_version",
        "result_environment_version",
        "result_suite_version",
    )
    return (
        HarnessNudge.no_workspace_objects.filter(job=environment)
        .select_related(*agents, *pieces, "answered_by")
        .defer(
            *(f"{one}__{field}" for one in agents for field in reads._AGENT_DEFERRED),
            *(f"{one}__{field}" for one in pieces for field in reads._PIECE_DEFERRED),
        )
        .order_by("-created_at", "-id")
    )


def list_nudges(
    environment: HostedHarnessJob, *, states: list[str] | None = None
) -> list[dict[str, Any]]:
    """The environment's nudges, newest first. A replaced one names the next newer
    nudge of its target, whichever states are listed."""
    items: list[dict[str, Any]] = []
    newer: dict[str, HarnessNudge] = {}
    for nudge in _nudges(environment).iterator(chunk_size=LIST_LIMIT):
        if not states or nudge.state in states:
            replaced_by = newer.get(nudge.target)
            items.append(
                nudge_item(
                    nudge,
                    replaced_by=replaced_by if nudge.state == State.REPLACED else None,
                )
            )
            if len(items) >= LIST_LIMIT:
                break
        # An empty offer's no_change row has no card and replaced nothing.
        if not (
            nudge.state == State.NO_CHANGE and nudge.conversation_message_id is None
        ):
            newer[nudge.target] = nudge
    return items


def _closed(nudge: HarnessNudge) -> HostedHarnessError:
    return HostedHarnessError(
        "nudge_closed",
        "This nudge was already answered or closed",
        status_code=409,
        details={"state": nudge.state},
    )


def answer_nudge(
    environment: HostedHarnessJob, nudge_id: Any, *, answer: str, user: Any
) -> dict[str, Any]:
    """Skip ("no") a pending nudge, or Rebuild ("yes") a pending or failed one."""
    if answer == "yes":
        from simulate.services.harness_versioning.rebuild import answer_rebuild

        nudge = answer_rebuild(environment, nudge_id, user=user)
        return nudge_item(_nudges(environment).get(id=nudge.id))
    with transaction.atomic():
        # The environment first, as a raise takes it, so a Skip and a replace queue.
        _head(environment.id, lock=True)
        nudge = (
            HarnessNudge.no_workspace_objects.select_for_update()
            .filter(job=environment, id=nudge_id)
            .first()
        )
        if nudge is None:
            raise nudge_not_found()
        if nudge.state != State.PENDING:
            raise _closed(nudge)
        nudge.state = State.ANSWERED_NO
        nudge.answered_by = user
        nudge.answered_at = timezone.now()
        nudge.save(update_fields=["state", "answered_by", "answered_at", "updated_at"])
        if (nudge.facts or {}).get("agent_default_moved"):
            # An offer held behind this card can be raised now.
            schedule_reconcile(environment.id)
    logger.info("harness_nudge_answered nudge=%s answer=no", nudge.id)
    nudge = _nudges(environment).get(id=nudge.id)
    return nudge_item(nudge)


def open_nudge_for_run(
    environment_id: Any, agent_version_id: Any, environment_version_id: Any
) -> HarnessNudge | None:
    """The latest pending or skipped environment nudge of this agent against this
    environment version."""
    return (
        HarnessNudge.no_workspace_objects.filter(
            job_id=environment_id,
            target=Target.ENVIRONMENT,
            trigger_agent_version_id=agent_version_id,
            base_environment_version_id=environment_version_id,
            state__in=(State.PENDING, State.ANSWERED_NO),
        )
        .order_by("-created_at")
        .first()
    )
