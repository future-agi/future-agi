"""Decide whether, and how, an installation can move to a release.

`check` prints this plan and `apply` executes it, so both always agree.
Building a plan never changes anything: it reads the env file, the resolved
Compose model (and the service model: every profile's services and the
version key each image follows), container and image metadata, the update
history and the checkout's files.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any, Collection, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .envfile import _VALUE_RE as TAG_VALUE_RE
from .envfile import example_keys
from .install import COMPONENT_VARIABLES, PRODUCTION_ENV, VERSION_VARIABLES, Installation, component_for
from .manifest import (ManifestError, _skipped_in_directory_hash, component_tag, deployment_hashes, is_version,
                       manifest_hash, parse_image_ref, parse_version)
from .readiness import NotReady, standalone_settings
from .settings import Settings
from .trust import Verification

UP_TO_DATE = "up_to_date"
UPDATE_AVAILABLE = "update_available"
BLOCKED = "blocked"
RESUMABLE_STATUSES = ("failed_during_rollout", "interrupted_during_rollout")
BEFORE_ROLLOUT_STATUSES = ("failed_before_rollout", "interrupted_before_rollout")
# `apply --stage`: everything up to the digest check done, nothing restarted.
STAGED = "staged"
PRODUCTION_UPGRADE_DOC = "deploy/README.md › Upgrading a production install"
IDENTITY_KEYS = ("mode", "production", "env_file", "project")

# A Compose variable reference: $VAR or ${VAR...}; $$ is a literal dollar sign.
_REFERENCE_RE = re.compile(r"\$(?:\$|\{([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*))")
# Definitions ./bin/install's merge (awk -F=, keyed on the text before the first '=') reads correctly.
_DEFINITION_LINE_RE = re.compile(r"(?P<indent>[ \t]*)(?P<export>export[ \t]+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
                                 r"(?P<before>[ \t]*)=(?P<after>[ \t]*)")
_BOM = "\ufeff"
_GITHUB_LATEST_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases/latest/download/([^/?#]+)$")
# Compose pull_policy values that make `up` pull an image again even when it is on the host.
_REPULL_POLICY_RE = re.compile(r"^(?:always|daily|weekly|every_.+)$", re.IGNORECASE)
# Container states in which a container runs its image (`docker inspect` State.Status).
_RUNNING_STATUSES = ("running", "restarting")
# How a stray container's state is worded (a paused container is still up: `docker unpause` resumes it).
_STATE_ORDER = {"running": 0, "paused": 1, "stopped": 2}
# Components whose images run the database migrations: a newer one may have migrated the schema.
MIGRATING_COMPONENTS = ("standalone", "backend")
INSTALL_UPGRADE_DOC = "INSTALLATION.md › Upgrading"
# The release options a printed command repeats (__main__ passes them as given, local files made absolute):
# --manifest and --bundle name one release's manifest, so a command for another release leaves them out.
_MANIFEST_OPTIONS = ("--manifest", "--bundle")
# Variables that point git at another repository or index than the checkout's own.
_GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE")
# How many paths a drift reason names before it points at the git command that lists them all.
_NAMED_PATHS = 8


def unfinished(history: Optional[Iterable[Optional[Mapping[str, Any]]]],
               env: Optional[Mapping[str, str]] = None) -> Optional[Dict[str, Any]]:
    """The update or rollback this install has not finished, from its receipts (oldest first).

    Walking from the newest: a run that stopped before restarting anything
    changed nothing on the running stack, so it is skipped. The first other
    receipt decides. An apply that stopped while containers were being
    replaced, or one that was staged (`apply --stage`), is an unfinished
    update; a rollback that stopped while containers were being replaced is
    an unfinished rollback; anything else means nothing is unfinished.

    A history that holds only skipped runs, the newest of them a resume of a
    stopped update (`resumes`) whose own receipt is not in it, stands for that
    update: the resume's receipt is returned with the resumed one as `_path`.

    With `env` (the env file's values), an unfinished apply counts only while
    the env file still selects its release for every key it changed
    (selected_keys): one whose keys were put back by hand is abandoned, and
    nothing is unfinished (build_plan notes it and plans the update afresh).
    """

    return _walk_history(history, env)[0]


def selected_keys(record: Mapping[str, Any]) -> Dict[str, str]:
    """The version keys an apply set, each with the tag it set: what the env file selects while it is unfinished.

    Its env_changes, and the keys it recorded as changed before it
    (previous_values: a resume that found them already set) with the tag its
    components name for them.
    """

    wanted: Dict[str, str] = {}
    previous = record.get("previous_values")
    keys = set(previous) if isinstance(previous, Mapping) else set()
    for item in record.get("components") or []:
        if (isinstance(item, Mapping) and item.get("variable") in keys and isinstance(item.get("target_tag"), str)
                and item["target_tag"]):
            wanted.setdefault(str(item["variable"]), item["target_tag"])
    changes = record.get("env_changes")
    if isinstance(changes, Mapping):
        wanted.update({str(key): value for key, value in changes.items() if isinstance(value, str)})
    return wanted


def _not_selected(record: Mapping[str, Any], env: Mapping[str, str]) -> List[str]:
    """The keys of selected_keys(record) the env file no longer sets to the record's tag."""

    return sorted(key for key, value in selected_keys(record).items() if (env.get(key) or "") != value)


def _walk_history(history: Optional[Iterable[Optional[Mapping[str, Any]]]],
                  env: Optional[Mapping[str, str]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """(unfinished(history, env), the apply it passed over because the env file no longer selects it)."""

    def judged(record: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if env is not None and (record.get("kind") or "apply") == "apply" and _not_selected(record, env):
            return None, record
        return record, None

    fallback: Optional[Dict[str, Any]] = None
    for record in reversed([r for r in (history or []) if r]):
        status = record.get("status")
        if status in BEFORE_ROLLOUT_STATUSES:
            if fallback is None and record.get("resumes"):
                fallback = dict(record, _path=record["resumes"])
            continue
        kind = record.get("kind") or "apply"
        if status in RESUMABLE_STATUSES or (kind == "apply" and status == STAGED):
            return judged(dict(record))
        return None, None
    return judged(fallback) if fallback is not None else (None, None)


def stopped_rollout(last_receipt: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """unfinished() of a history that holds only `last_receipt`."""

    return unfinished([last_receipt] if last_receipt else [])


def policy_allows(current: str, target: str, policy: str) -> bool:
    """Whether moving from `current` to a newer `target` stays within `policy`."""

    if policy not in ("patch", "minor", "major"):
        raise ValueError(f"unknown update policy {policy!r}")
    cur, tgt = parse_version(current), parse_version(target)
    if policy == "patch":
        return tgt[:2] == cur[:2]
    if policy == "minor":
        return tgt[0] == cur[0]
    return True


def _strip_variant(tag: str, component: str) -> Optional[str]:
    """The release version a tag stands for, or None if it names none."""

    suffix = "-gpu" if component == "serving_gpu" else ""
    if suffix:
        if not tag.endswith(suffix):
            return None
        tag = tag[: -len(suffix)]
    return tag if is_version(tag) else None


def command_prefix(production: bool, env_file: str) -> str:
    """How to run bin/update for an install in the checkout, for the commands hints print (paths shell-quoted).

    Run from the checkout: install_command() adds the `cd <checkout> && `
    a command needs when bin/update was run from another directory.
    """

    if not production:
        return "./bin/update"
    return "./bin/update --production" + ("" if env_file == PRODUCTION_ENV else f" --env-file {shlex.quote(env_file)}")


def cd_prefix(installation: Any) -> str:
    """`cd <checkout> && ` when bin/update was run from another directory (Installation.caller_cwd), else ""."""

    method = getattr(installation, "cd_prefix", None)
    if callable(method):
        return str(method())
    caller = getattr(installation, "caller_cwd", None)
    root = Path(installation.root)
    if caller is None or Path(caller).resolve() == root.resolve():
        return ""
    return f"cd {shlex.quote(str(root))} && "


def install_command(installation: Any, args: str = "", options: Sequence[str] = ()) -> str:
    """This install's bin/update command line, ready to paste in the shell that ran bin/update.

    cd_prefix(), ./bin/update with this install's flags, `args` as written,
    then `options` (argv words, such as the release options a check or apply
    was given) shell-quoted.
    """

    prefix = cd_prefix(installation) + command_prefix(installation.production,
                                                      installation.relative(installation.env_file))
    return " ".join([prefix, *([args] if args else []), *(shlex.quote(str(word)) for word in options)])


def _without_manifest(options: Sequence[str]) -> List[str]:
    """`options` without --manifest and --bundle (and their values): for a command about another release."""

    kept: List[str] = []
    skip = False
    for word in options:
        if skip:
            skip = False
        elif word in _MANIFEST_OPTIONS:
            skip = True
        elif not str(word).startswith(tuple(f"{flag}=" for flag in _MANIFEST_OPTIONS)):
            kept.append(word)
    return kept


def _receipt_argument(path: Optional[str]) -> str:
    return shlex.quote(path) if path else "<receipt>"


def _inactive_component(variable: str, value: str, mode: str) -> str:
    """The manifest component a version key selects when no active service tells."""

    if variable == "FUTURE_AGI_VERSION":
        return "standalone" if mode == "standalone" else "backend"
    if variable == "SERVING_VERSION":
        return "serving_gpu" if value.endswith("-gpu") else "serving"
    return next(name for name, key in COMPONENT_VARIABLES.items() if key == variable)


def referenced_variables(text: str) -> Set[str]:
    """The variables a Compose value interpolates ($VAR, ${VAR}, ${VAR:-default}, nested defaults)."""

    return {m.group(1) or m.group(2) for m in _REFERENCE_RE.finditer(text) if m.group(1) or m.group(2)}


def _image_name(image: str) -> str:
    """An image reference without its tag and digest."""

    rest = image.split("@", 1)[0]
    head, _, last = rest.rpartition("/")
    return (f"{head}/" if head else "") + last.split(":", 1)[0]


def _same_install(record: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
    """Whether a receipt (or journal) was written for this install; one that records no identity counts."""

    return all(record.get(key) is None or record.get(key) == identity.get(key) for key in IDENTITY_KEYS)


def _install_cannot_merge(path: Path) -> List[str]:
    """What in this .env ./bin/install's merge misreads (its keys come out wrong, so it adds empty duplicates)."""

    try:
        text = path.read_bytes().decode("utf-8", "surrogateescape")
    except OSError:
        return []
    found = ["a byte order mark"] if text.startswith(_BOM) else []
    for line in text.lstrip(_BOM).split("\n"):
        match = _DEFINITION_LINE_RE.match(line)
        if not match:
            continue
        for group, kind in (("export", "an export prefix"), ("before", "spaces around ="),
                            ("after", "spaces around ="), ("indent", "leading indentation")):
            if match.group(group) and kind not in found:
                found.append(kind)
    return found


def _release_manifest(source: str, version: str) -> str:
    """A manifest location that keeps naming `version` (GitHub's latest/download URL moves on)."""

    latest = _GITHUB_LATEST_RE.fullmatch(source or "")
    if latest:
        owner, repo, asset = latest.groups()
        return f"https://github.com/{owner}/{repo}/releases/download/{version}/{asset}"
    return source


def _join(names: Sequence[str]) -> str:
    return ", ".join(names)


def _manifest_of(stopped: Mapping[str, Any], settings: Settings) -> str:
    """Where the manifest of the release `stopped` was going to can be read again ("" when unknown)."""

    version = str(stopped.get("target_version") or "")
    source = str(stopped.get("manifest_source") or "")
    if not source and _GITHUB_LATEST_RE.fullmatch(settings.get("FUTURE_AGI_UPDATE_MANIFEST_URL")):
        source = settings.get("FUTURE_AGI_UPDATE_MANIFEST_URL")
    return _release_manifest(source, version) if source and version else ""


def classify_strays(installation: Installation, containers: Iterable[Mapping[str, Any]],
                    active_services: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """The project's containers on Future AGI images whose service the active Compose configuration leaves out.

    One-off `docker compose run` containers are not counted. A service that
    `installation.service_model()` lists (it lists every profile's services)
    is "inactive": its profile is not enabled now. Any other is an "orphan":
    this release's compose files no longer define it (deploy/README.md keeps
    such workloads until their retirement is approved). When the model is
    not complete (Compose could not resolve every profile), a service it
    does not list may still be a profile's, so it counts as inactive: the
    side that can block an update. `up` touches neither.

    Each entry stands for the containers of one service that share an image,
    a version label and a state: {"service", "ids", "running" (running or
    restarting), "state" ("running", "paused" or "stopped"), "image",
    "version" (the image's OCI version label, or "")}. Entries are sorted by
    service, running ones first, then paused ones.
    """

    candidates: Dict[str, List[Mapping[str, Any]]] = {}
    for container in containers:
        name = container.get("service") or ""
        if container.get("oneoff") or not name or name in active_services:
            continue
        if _futureagi_image(container.get("image") or ""):
            candidates.setdefault(name, []).append(container)
    strays: Dict[str, List[Dict[str, Any]]] = {"inactive": [], "orphans": []}
    if not candidates:
        return strays
    model = installation.service_model()
    defined = set(model.get("services") or {})
    complete = bool(model.get("complete", True))
    labels: Dict[str, str] = {}
    for name in sorted(candidates):
        groups: Dict[Any, List[str]] = {}
        for container in candidates[name]:
            image_id = container.get("image_id") or ""
            if image_id and image_id not in labels:
                info = installation.image(image_id)
                labels[image_id] = str((info or {}).get("version") or "")
            key = (container_state(container.get("status")), str(container.get("image") or ""),
                   labels.get(image_id, ""))
            groups.setdefault(key, []).append(str(container.get("id") or ""))
        for (state, image, version), ids in sorted(groups.items(),
                                                   key=lambda group: (_STATE_ORDER[group[0][0]], group[0][1:])):
            strays["inactive" if name in defined or not complete else "orphans"].append(
                {"service": name, "ids": ids, "running": state == "running", "state": state, "image": image,
                 "version": version})
    return strays


def container_state(status: Optional[str]) -> str:
    """How a container's `docker inspect` State.Status is worded: "running" (or restarting), "paused" or "stopped"."""

    if status in _RUNNING_STATUSES:
        return "running"
    return "paused" if status == "paused" else "stopped"


def stray_states(entries: Sequence[Mapping[str, Any]]) -> str:
    """`service (running, v1.51.0)` for each service of `entries`, in order."""

    states: Dict[str, List[str]] = {}
    for entry in entries:
        state = entry.get("state") or ("running" if entry["running"] else "stopped")
        text = f"{state}, {entry['version'] or 'no version label'}"
        if text not in states.setdefault(entry["service"], []):
            states[entry["service"]].append(text)
    return _join([f"{service} ({'; '.join(found)})" for service, found in states.items()])


def on_release(image: str, label: str, tag: str, *, by_tag: bool) -> bool:
    """Whether a container already runs the release a version key selects with `tag`.

    `image` is the reference the container was created from, `label` its
    image's OCI version label ("" without one), `tag` the tag the key selects
    for the release (component_tag: the version, with -gpu for CUDA serving;
    either variant names the same release). A label always decides, whatever
    tag the container was created from: the release's version, or for a CUDA
    serving image that version with its -gpu variant. Without a label, and
    only with `by_tag`, the tag the container was created from (no digest)
    decides when it names a version (either serving variant): for a
    component with nothing pulled and verified to compare it with (no active
    service of it: an inactive profile's pin). The plan judges strays with
    it and apply its check after the restart (Updater._left_behind), so both
    agree.
    """

    try:
        ref = parse_image_ref(image)
    except ManifestError:
        return False
    release = _release_of_tag(tag)
    if label:
        return label == release or (component_for(ref) == "serving_gpu" and label == f"{release}-gpu")
    if not by_tag or ref.digest:
        return False
    created = _release_of_tag(ref.tag or "latest")
    return is_version(created) and created == release


def _release_of_tag(tag: str) -> str:
    """The release a version tag names: the tag without the -gpu of a CUDA serving image."""

    return tag[:-4] if tag.endswith("-gpu") else tag


def _stray_on_target(entry: Mapping[str, Any], component: str, target: str, *, by_tag: bool = True) -> bool:
    """on_release() for a classify_strays entry of `component`'s image, `target` being the release's version."""

    return on_release(entry["image"], entry["version"], component_tag({"version": target}, component), by_tag=by_tag)


def _judge_strays(strays: Mapping[str, List[Dict[str, Any]]], installation: Installation,
                  planned: Sequence[Mapping[str, Any]], resumed: Optional[Mapping[str, Any]], target: str,
                  env_name: str, updating: bool, reasons: List[str], notes: List[str],
                  active_components: Collection[str] = ()) -> None:
    """What classify_strays' containers mean for this plan: reasons and notes.

    A container of an inactive profile's service stands in the way only when
    it runs, the update moves its component's version key, and it does not
    run the release already (on_release against the tag the plan sets its key
    to, as apply judges it after the restart; its tag counts only for a
    component with no active service): `up` would leave it as it is next to
    the new release. Every other one, and every orphan, is a note; an install
    with nothing to update is never blocked by them. A service an incomplete
    service model does not list may be defined by no compose file: its
    containers are removed by id, never by a service name Compose may reject.
    """

    # What each key selects once the update is done: apply judges the strays against these (expected_tags).
    wants = {str(item["variable"]): str(item["target_tag"]) for item in planned}
    moved: Set[str] = set()
    if updating:
        moved = {str(item["variable"]) for item in planned if item["action"] != "keep"}
        if resumed:
            # The update being finished changed its keys before this run: the env file selects the release.
            previous = resumed.get("previous_values")
            moved |= (set(previous) if isinstance(previous, Mapping) and previous else
                      {str(item["variable"]) for item in planned if item["configured"] == item["target_tag"]})
    blocking: List[Dict[str, Any]] = []
    idle: List[Dict[str, Any]] = []
    for entry in strays.get("inactive") or []:
        component = _image_component(entry["image"])
        variable = COMPONENT_VARIABLES[component] if component else ""
        if (entry["running"] and component and variable in moved
                and not on_release(entry["image"], entry["version"],
                                   wants.get(variable) or component_tag({"version": target}, component),
                                   by_tag=component not in active_components)):
            blocking.append(entry)
        else:
            idle.append(entry)
    docker = " ".join(shlex.quote(word) for word in (getattr(installation, "docker", None) or ["docker"]))
    # Only with an incomplete service model (Compose could not resolve every profile) can an inactive entry's service
    # be one no compose file defines: Compose rejects its name, so it is removed by container id (as the rollback
    # words it, Updater._refuse_strays).
    defined: Optional[Set[str]] = None
    if blocking or idle:
        model = installation.service_model()
        defined = None if model.get("complete", True) else set(model.get("services") or {})

    def listed(entries: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
        return [entry for entry in entries if defined is None or entry["service"] in defined]

    def unlisted(entries: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
        return [entry for entry in entries if defined is not None and entry["service"] not in defined]

    def by_id(entries: Sequence[Mapping[str, Any]]) -> str:
        return f"`{docker} rm -f {' '.join(shlex.quote(cid[:12]) for entry in entries for cid in entry['ids'])}`"

    # A production Compose command run by hand carries the shell rule.
    rule = f". {shell_rule(installation)}" if installation.production else ""
    if blocking:
        names = sorted({entry["service"] for entry in blocking})
        one = len(names) == 1
        known = sorted({entry["service"] for entry in listed(blocking)})
        unknown = unlisted(blocking)
        maybe = sorted({entry["service"] for entry in unknown})
        alone = len(maybe) == 1
        if not unknown:
            why = " (a profile that is not enabled now)"
        elif not known:
            why = (f", and Compose could not resolve every profile, so bin/update cannot tell whether {_join(maybe)} "
                   f"{'belongs' if alone else 'belong'} to a profile that is not enabled now or "
                   f"{'is' if alone else 'are'} defined by no compose file any more")
        else:
            why = (f" ({_join(known)}: a profile that is not enabled now; Compose could not resolve every profile, so "
                   f"bin/update cannot tell whether {_join(maybe)} {'belongs' if alone else 'belong'} to one or "
                   f"{'is' if alone else 'are'} defined by no compose file any more)")
        remedy = ""
        if known:
            remedy = (f"Enable the profile (COMPOSE_PROFILES in {env_name}), or remove "
                      f"{'it' if len(known) == 1 else 'them'} with `{installation.compose_hint()} rm -sf "
                      f"{' '.join(shlex.quote(name) for name in known)}`{rule}")
        if unknown:
            remedy += (f"Remove {'its' if one else 'their'} containers with {by_id(unknown)}" if not known else
                       f" Remove the containers of {_join(maybe)} with {by_id(unknown)}" if rule else
                       f"; remove the containers of {_join(maybe)} with {by_id(unknown)}")
        reasons.append(
            f"{'service' if one else 'services'} {_join(names)} {'has' if one else 'have'} containers on Future "
            f"AGI images but {'is' if one else 'are'} not in the active Compose configuration{why}: "
            f"{stray_states(blocking)}. The update to {target} does not replace {'its' if one else 'their'} "
            f"containers, so they would keep running as they are next to it. {remedy}"
            + (" Then check again" if remedy.endswith(".") else ", then check again"))
        idle = [entry for entry in idle if entry["service"] not in names]
    if listed(idle):
        names = sorted({entry["service"] for entry in listed(idle)})
        notes.append(
            "bin/update leaves these containers of services outside the active Compose profiles as they are: "
            f"{stray_states(listed(idle))}. Enabling their profile (COMPOSE_PROFILES in {env_name}) lets Compose "
            f"recreate them on the version {env_name} selects; to remove them, run "
            f"`{installation.compose_hint()} rm -sf {' '.join(shlex.quote(name) for name in names)}`{rule}")
    if unlisted(idle):
        names = sorted({entry["service"] for entry in unlisted(idle)})
        one = len(names) == 1
        notes.append(
            f"bin/update leaves these containers of {'a service' if one else 'services'} outside the active Compose "
            f"configuration as they are: {stray_states(unlisted(idle))}. Compose could not resolve every profile, so "
            f"bin/update cannot tell whether {'it belongs' if one else 'they belong'} to a profile that is not enabled "
            f"now or {'is' if one else 'are'} defined by no compose file any more; to remove them, run "
            f"{by_id(unlisted(idle))}")
    orphans = strays.get("orphans") or []
    if orphans:
        names = sorted({entry["service"] for entry in orphans})
        one = len(names) == 1
        ids = " ".join(shlex.quote(cid[:12]) for entry in orphans for cid in entry["ids"])
        notes.append(
            f"{'service' if one else 'services'} {stray_states(orphans)} {'is' if one else 'are'} not defined by "
            f"this release's compose files any more; bin/update leaves {'its' if one else 'their'} containers as "
            f"they are. Once {'its' if one else 'their'} retirement is approved, remove them with "
            f"`{docker} rm -f {ids}`")


def _image_component(image: str) -> Optional[str]:
    try:
        return component_for(parse_image_ref(image))
    except ManifestError:
        return None


def _undo_command(command: str, stopped: Mapping[str, Any]) -> str:
    """The rollback of an unfinished update.

    Its receipt names it. A run that is still only a journal (check and apply
    --dry-run plan with it) gets its receipt when the rollback recovers it
    first, and is then the newest update, which a plain rollback undoes.
    """

    path = stopped.get("_path")
    return f"{command} rollback --receipt {_receipt_argument(path)} --yes" if path else f"{command} rollback --yes"


def _service_runs(containers: Sequence[Mapping[str, Any]], labels: Mapping[str, str], tag: str) -> Optional[str]:
    """How a service's containers (no one-off ones) run the release `tag` names, by their images' version labels.

    "running": a container runs it (State.Status running) and every one that
    runs or restarts does, none of them restarting or unhealthy. "finished":
    none runs, and every one exited with code 0 on it (a one-shot job that
    finished, such as the bootstrap job). None: they do not run it; a
    container that was created but never started, exited with an error, is
    paused or restarts runs nothing.
    """

    def on_it(container: Mapping[str, Any]) -> bool:
        return on_release(str(container.get("image") or ""), labels.get(str(container.get("id") or ""), ""), tag,
                          by_tag=False)

    live = [container for container in containers if container.get("status") in _RUNNING_STATUSES]
    if live:
        healthy = all(container.get("status") == "running" and container.get("health") != "unhealthy"
                      and on_it(container) for container in live)
        return "running" if healthy else None
    if containers and all(container.get("status") == "exited" and container.get("exit_code") == 0
                          and on_it(container) for container in containers):
        return "finished"
    return None


def _stack_runs(services: Sequence[Mapping[str, Any]], running: Mapping[str, Sequence[Mapping[str, Any]]],
                labels: Mapping[str, str], tags: Mapping[str, str]) -> bool:
    """Whether every one of `services` runs the tag `tags` names for it (_service_runs), and at least one runs."""

    found = [_service_runs(running.get(service["service"]) or [], labels, tags[service["service"]])
             for service in services]
    return bool(found) and None not in found and "running" in found


def _settled_rollback(record: Mapping[str, Any], services: Sequence[Mapping[str, Any]],
                      running: Mapping[str, Sequence[Mapping[str, Any]]], labels: Mapping[str, str],
                      env: Mapping[str, str]) -> Optional[bool]:
    """Whether a rollback that stopped part-way was settled by hand: True finished, False abandoned, None neither.

    Settled: every active service of a key the rollback changed (its
    env_changes) runs, healthy, the version tag the env file selects for it
    now (_stack_runs). Finished when the env file selects the rollback's
    values for all those keys; otherwise abandoned (the values were put back
    and the stack restarted on them). A rollback that recorded no keys, or
    whose services are not all running a version tag, is neither.
    """

    changes = record.get("env_changes")
    if not isinstance(changes, Mapping) or not changes:
        return None
    mine = [service for service in services if COMPONENT_VARIABLES[service["component"]] in changes]
    tags: Dict[str, str] = {}
    for service in mine:
        ref = service["ref"]
        if ref.digest or not _strip_variant(ref.tag or "latest", service["component"]):
            return None
        tags[service["service"]] = ref.tag
    if not mine or not _stack_runs(mine, running, labels, tags):
        return None
    return all((env.get(str(key)) or "") == (value or "") for key, value in changes.items())


def execution_files(manifest, authorization=None, raw_manifest=None):
    """Manual: published target bytes. Automatic: one authenticated retained-layout row."""
    if authorization is None:
        return manifest['deployment']['files']
    import hashlib
    from .compatibility import DeploymentAuthorization, decode_document, plain, require
    from .contracts import AuthorisedTransition, ReasonCode
    require(isinstance(authorization, (DeploymentAuthorization, AuthorisedTransition)) and type(raw_manifest) is bytes,
            ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    expected_hash = (authorization.prepared.manifest_sha256 if isinstance(authorization, AuthorisedTransition)
                     else authorization.target_manifest_sha256)
    require(hashlib.sha256(raw_manifest).hexdigest() == expected_hash
            and manifest_hash(decode_document(raw_manifest, 1024 * 1024)) == manifest_hash(plain(manifest)),
            ReasonCode.IMMUTABLE_RELEASE_CHANGED)
    return authorization.execution_deployment['files']


def build_plan(manifest: Mapping[str, Any], verification: Verification, installation: Installation,
               settings: Settings, *, manifest_source: str = "",
               last_receipt: Optional[Mapping[str, Any]] = None,
               history: Optional[Sequence[Mapping[str, Any]]] = None,
               fallback_note: Optional[str] = None,
               journal_reason: Optional[str] = None,
               newer_release: Optional[str] = None,
               command_options: Sequence[str] = (), deployment_authorization=None,
               raw_manifest: Optional[bytes] = None) -> Dict[str, Any]:
    """The plan for moving `installation` to `manifest`'s release.

    `history` is this install's receipts, oldest first (StateDir.history);
    `last_receipt` alone stands for a history of one. `fallback_note` says why
    this manifest was chosen (a note, nothing more). `newer_release` is the
    tag of a newer platform release that has no manifest yet
    (FetchedManifest.newer_release): only then is this manifest not the
    newest release's. `journal_reason` is why an interrupted run of another
    install blocks. `command_options` are the release options this run was
    given, as argv words (--manifest and --bundle with local files made
    absolute): the apply commands the plan prints repeat them, so a pasted
    command plans this same release; rollback commands never carry them.
    """

    reasons: List[str] = []
    notes: List[str] = [fallback_note] if fallback_note else []
    downgrades: List[str] = []  # dropped when the install already runs this release or newer (see `ahead`)
    target = manifest["version"]
    env_name = installation.relative(installation.env_file)
    options = [str(word) for word in command_options]
    # Every printed bin/update command starts with this: it works pasted in the shell that ran bin/update.
    command = install_command(installation)

    def apply_command(*extra: str, release_options: Sequence[str] = options) -> str:
        return install_command(installation, " ".join(["apply", *extra]), release_options)

    def other_release(record: Mapping[str, Any]) -> Tuple[str, str, str]:
        """How to finish `record`, an update of another release than this manifest's: (checkout, jobs, apply).

        Its images run with its own compose files, so the checkout goes back
        to that release first. A production install runs its initialization
        jobs next: this manifest does not tell whether that release has any,
        so they are named anyway. The apply names that release's manifest
        (GitHub's latest/download URL turned into the release's own) and,
        when this install follows another channel, the channel its receipt
        records; none of this run's release options, which name this manifest.
        """

        version = str(record.get("target_version") or "") or "?"
        source = _manifest_of(record, settings)
        channel = str(record.get("channel") or "")
        own_channel = ("--channel", shlex.quote(channel)) if channel and channel != settings.channel else ()
        finish = apply_command("--manifest", shlex.quote(source) if source else f"<the manifest of {version}>",
                               *own_channel, release_options=())
        checkout = (f"check out {version} with `{cd_prefix(installation)}git checkout {shlex.quote(version)}`, or at "
                    "least its deployment files (your env file stays as it is), ")
        jobs = (f"run its initialization jobs with {jobs_text(installation)} if they have not run yet, "
                if installation.production else "")
        return checkout, jobs, finish

    if history is None:
        history = [last_receipt] if last_receipt else []
    records = [record for record in history if record]
    if records:
        identity = installation.identity()
        records = [record for record in records if _same_install(record, identity)]
    # An unfinished apply counts only while the env file still selects it: one whose keys were put back by hand
    # is abandoned, and this plan is a fresh update with every guard (the backup, production's two steps). What
    # the plan says about it waits for the containers (below): part of the stack may still run its release.
    stopped, dropped = _walk_history(records, installation.env)

    if manifest["channel"] != settings.channel:
        reasons.append(
            f"the manifest is for the {manifest['channel']} channel, but this install follows "
            f"{settings.channel} (FUTURE_AGI_UPDATE_CHANNEL)")
    if not verification.verified:
        reasons.append(f"the release signature is not verified: {verification.error}")

    config = installation.config()
    services = installation.futureagi_services(config)
    containers = installation.containers()
    stack_reasons = installation.check_stack(containers)
    reasons.extend(stack_reasons)
    if journal_reason:
        reasons.append(journal_reason)
    # One-off containers (`docker compose run`) are not part of the running stack.
    running: Dict[str, List[Dict[str, Any]]] = {}
    for container in containers:
        if not container.get("oneoff"):
            running.setdefault(container["service"], []).append(container)

    active_services = config.get("services") or {}
    # Containers of Future AGI services the active configuration leaves out: `up` never touches them. Whether
    # one stands in the update's way is decided once the plan knows which version keys it changes (below).
    strays = ({"inactive": [], "orphans": []} if stack_reasons
              else classify_strays(installation, containers, active_services))

    components: Dict[str, Dict[str, Any]] = {}
    digest_services: Set[str] = set()
    digest_variables: Set[str] = set()
    # container id -> its image's OCI version label ("" without one): each image is inspected once. Only running
    # (or restarting) containers count as running (live_versions).
    container_labels: Dict[str, str] = {}
    for service in services:
        name, ref = service["component"], service["ref"]
        info = components.setdefault(name, {
            "component": name, "variable": COMPONENT_VARIABLES[name], "services": [], "tags": [],
            "running_versions": [], "live_versions": [], "labelled": [], "by_version": {},
            "configured": installation.env.get(COMPONENT_VARIABLES[name], ""), "states": set(),
        })
        info["services"].append(service["service"])
        if (ref.tag or "latest") not in info["tags"]:
            info["tags"].append(ref.tag or "latest")
        expected = parse_image_ref(manifest["components"][name]["image"])
        if ref.name != expected.name:
            reasons.append(
                f"service {service['service']} pulls {ref.name}, but the release names {expected.name} for {name}. "
                f"bin/update verifies images only from the registry the release publishes to ({expected.registry}) "
                "and does not support registry mirrors yet: update this install by hand ("
                f"{production_procedure(installation) if installation.production else INSTALL_UPGRADE_DOC}), or "
                f"point {service['service']} at {expected.name}, then check again")
        if ref.digest:
            digest_services.add(service["service"])
            variable = info["variable"]
            if "@" in info["configured"]:
                if variable not in digest_variables:
                    digest_variables.add(variable)
                    reasons.append(
                        f"{variable} in {env_name} is {info['configured']}, which pins a digest; bin/update writes "
                        "plain version tags and checks every image against the release's signed digest itself. "
                        f"Set {variable}={ref.tag or 'vX.Y.Z'} (without @{ref.digest}), then check again")
            else:
                reasons.append(
                    f"service {service['service']} pins {service['image']} by digest in a compose file; bin/update "
                    "only changes the version tags in the env file. Remove that digest pin (deploy/README.md "
                    "› Pin a release) before updating")
        for container in running.get(service["service"], []):
            image = installation.image(container["image_id"]) if container["image_id"] else None
            container_labels[str(container.get("id") or "")] = str((image or {}).get("version") or "")
            info["states"].add(container_state(container.get("status")))
            if image and image["version"] and image["version"] not in info["running_versions"]:
                info["running_versions"].append(image["version"])
            if image and is_version(image["version"]):
                if service["service"] not in info["labelled"]:
                    info["labelled"].append(service["service"])
                by_version = info["by_version"].setdefault(image["version"], [])
                if service["service"] not in by_version:
                    by_version.append(service["service"])
                if container.get("status") in _RUNNING_STATUSES:
                    if image["version"] not in info["live_versions"]:
                        info["live_versions"].append(image["version"])

    # A staged update the stack already runs (every service runs its version, healthy, or is a one-shot job that
    # finished on it) was restarted outside bin/update, by deploy/README.md's manual steps say: there is nothing
    # left to finish. Containers that never started, crashed or restart do not run it: then the staged update
    # (its jobs, then the apply that waits for the app) is still to finish.
    if stopped and (stopped.get("kind") or "apply") == "apply" and stopped.get("status") == STAGED:
        version = str(stopped.get("target_version") or "")
        if version and _stack_runs(services, running, container_labels,
                                   {service["service"]: component_tag({"version": version}, service["component"])
                                    for service in services}):
            notes.append(
                f"the update to {version} staged earlier"
                + (f" (receipt {stopped['_path']})" if stopped.get("_path") else "")
                + " already runs: the stack was restarted on it outside bin/update, so there is nothing of it to "
                "finish")
            stopped = None
    # A rollback that stopped part-way blocks until it is finished, unless the stack was made consistent by hand:
    # every service of the keys it changed runs what the env file selects (healthy). Either the env file selects
    # the rollback's release (finished outside bin/update) or the values from before it (abandoned).
    elif stopped and stopped.get("kind") == "rollback":
        finished_by_hand = _settled_rollback(stopped, services, running, container_labels, installation.env)
        if finished_by_hand is not None:
            receipt_text = f" (receipt {stopped['_path']})" if stopped.get("_path") else ""
            version = stopped.get("target_version") or "?"
            if finished_by_hand:
                notes.append(
                    f"the rollback to {version} that stopped while containers were being replaced{receipt_text} was "
                    f"finished outside bin/update: {env_name} selects it and the stack runs it, so there is nothing "
                    "of it to finish")
            else:
                # Only the keys put back by hand: one the env file still sets to the rollback's value selects it.
                keys = sorted(str(key) for key, value in (stopped.get("env_changes") or {}).items()
                              if (installation.env.get(str(key)) or "") != (value or ""))
                one = len(keys) == 1
                notes.append(
                    f"the rollback to {version} that stopped while containers were being replaced{receipt_text} is "
                    f"abandoned: {_join(keys)} in {env_name} no longer {'selects' if one else 'select'} it and the "
                    f"stack runs what {env_name} selects, so this plan does not finish it")
            stopped = None

    # The unfinished apply the env file no longer selects. While part of the stack runs its release, the way out
    # is to finish it (bin/update's guards, its jobs, the wait for the app), never a restart by hand.
    dropped_keys: Set[str] = set()  # version keys whose mismatch the reason below explains
    if dropped:
        keys = _not_selected(dropped, installation.env)
        one = len(keys) == 1
        dropped_version = str(dropped.get("target_version") or "")
        wanted = selected_keys(dropped)
        what = ("staged earlier" if dropped.get("status") == STAGED
                else "that stopped while containers were being replaced")
        receipt_text = f" (receipt {dropped['_path']})" if dropped.get("_path") else ""
        on_it = sorted(service["service"] for service in services
                       if dropped_version and COMPONENT_VARIABLES[service["component"]] in wanted
                       and any(container.get("status") in _RUNNING_STATUSES
                               and on_release(str(container.get("image") or ""),
                                              container_labels.get(str(container.get("id") or ""), ""),
                                              component_tag({"version": dropped_version}, service["component"]),
                                              by_tag=False)
                               for container in running.get(service["service"], [])))
        if on_it:
            dropped_keys = set(wanted)
            if dropped_version == target:
                checkout, finish = "", apply_command()
                jobs_step = (f"run its initialization jobs with {jobs_text(installation)} if they have not run yet, "
                             if installation.production else "")
            else:
                checkout, jobs_step, finish = other_release(dropped)
            # Staging restarts nothing: what runs its release was started outside bin/update.
            runs = (f"{_join(on_it)} {'runs' if len(on_it) == 1 else 'run'} {dropped_version}, the release of the "
                    f"update staged earlier{receipt_text}" if dropped.get("status") == STAGED else
                    f"the update to {dropped_version} {what}{receipt_text} left {_join(on_it)} running "
                    f"{dropped_version}")
            reasons.append(
                f"{runs}, but {_join(keys)} in {env_name} no longer {'selects' if one else 'select'} it. Finish that "
                f"update rather than restarting the stack by hand: {checkout}set "
                f"{', '.join(f'{key}={wanted[key]}' for key in keys)} in {env_name} again, {jobs_step}then run "
                f"`{finish}`, which finishes it; or undo it with `{_undo_command(command, dropped)}`")
        else:
            notes.append(
                f"the update to {dropped_version or '?'} {what}{receipt_text} is abandoned: {_join(keys)} in "
                f"{env_name} no longer {'selects' if one else 'select'} it, so this plan does not finish it and plans "
                "the update afresh")

    stopped_kind = (stopped or {}).get("kind") or "apply"
    staged = bool(stopped) and stopped_kind == "apply" and (stopped or {}).get("status") == STAGED
    finishing = bool(stopped) and stopped_kind == "apply" and (stopped or {}).get("target_version") == target
    how = ""
    if stopped:
        how = (f" ({stopped.get('status')})" if stopped.get("status") in RESUMABLE_STATUSES else
               f", and the run that resumed it stopped before restarting anything ({stopped.get('status')})")
    # A rollout stopped midway (or a staged update) leaves the env file selecting its target while containers
    # may still run the images from before; the resume logic below handles that, so it is not a hand-edited
    # env file.
    stopped_values = set(str((stopped or {}).get("target_version") or "").split(",")) - {""}
    staged_version: Optional[str] = (str((stopped or {}).get("target_version") or "") or None) if staged else None
    stopped_version: Optional[str] = (str((stopped or {}).get("target_version") or "") or None
                                      if stopped and not staged else None)
    # How the messages below name the unfinished run whose release the env file selects.
    unfinished_run = ("the rollback that stopped part-way" if stopped_kind == "rollback" else
                      "the update that stopped part-way")

    if not components:
        reasons.append("the resolved Compose configuration has no Future AGI services")

    # The restart may start only the images this run pulled and verified. A pull_policy that pulls again would
    # let `up` fetch whatever the tag points at by then; `up --pull never` overrides it, where Compose has it.
    repulling = sorted(service["service"] for service in services
                       if _REPULL_POLICY_RE.fullmatch(str((active_services.get(service["service"]) or {})
                                                          .get("pull_policy") or "")))
    if repulling and not installation.supports_up_pull_never():
        one = len(repulling) == 1
        policies = _join(sorted({str(active_services[name].get("pull_policy")) for name in repulling}))
        reasons.append(
            f"{'service' if one else 'services'} {_join(repulling)} {'sets' if one else 'set'} pull_policy: "
            f"{policies}, so the restart would pull {'its image' if one else 'their images'} again after "
            f"bin/update verified {'it' if one else 'them'}, and this Docker Compose (`{_compose_program(installation)}`) "
            "has no `up --pull never` to prevent that. Update Docker Compose, or remove that pull_policy (a "
            "docker-compose.override.yml, say), then check again")

    # Which version key selects each service's image (Installation.service_model): a key bin/update changes must
    # select only images it verifies, and every image it verifies must follow its key. Only the services of the
    # active model count here: a key only an inactive profile's services use is a pin of its own (below).
    model: Mapping[str, Any] = installation.service_model() if components else {}
    model_services: Mapping[str, Any] = model.get("services") or {}
    followed: Dict[str, Optional[str]] = {name: (entry or {}).get("variable") for name, entry in model_services.items()
                                          if name in active_services}
    # The keys the services of profiles that are not enabled follow. Without every profile resolved (not
    # complete) they are not all known: then any key no active service uses counts as theirs.
    inactive_keys: Optional[Set[str]] = ({str((entry or {}).get("variable")) for name, entry in model_services.items()
                                          if name not in active_services and (entry or {}).get("variable")}
                                         if model.get("complete", True) else None)
    release_registry = parse_image_ref(next(iter(manifest["components"].values()))["image"]).registry
    recognised = {service["service"] for service in services}
    for name in sorted(followed):
        key = followed[name]
        if key and name not in recognised:
            resolved = (active_services.get(name) or {}).get("image") or (model_services[name] or {}).get("image")
            reasons.append(
                f"service {name} takes its image from {key}, but that resolves to {resolved}, which is not a Future "
                "AGI image bin/update knows, so the update would change it without verifying it against the "
                "release's signed digests. bin/update verifies images only from the registry the release publishes "
                f"to ({release_registry}) and does not support registry mirrors yet: stop {name} using {key} (set its "
                "image in a docker-compose.override.yml, say) and update it by hand, then check again")
    for service in services:
        name, variable = service["service"], COMPONENT_VARIABLES[service["component"]]
        found = followed.get(name)
        if name in digest_services or found == variable:
            continue
        fix = (f"Make {name} use it (image: {_image_name(service['image'])}:${{{variable}}}) or remove that image "
               "setting (a docker-compose.override.yml, say), then check again")
        if found:
            reasons.append(
                f"service {name}'s image ({service['image']}) follows {found}, not {variable}: bin/update changes "
                f"{variable} in {env_name} for {service['component']}. {fix}")
        else:
            reasons.append(
                f"service {name}'s image is set directly in a compose file ({service['image']}), not through "
                f"{variable}: bin/update can only change {variable} in {env_name}. {fix}")

    planned: List[Dict[str, Any]] = []
    env_changes: Dict[str, str] = {}
    # What the install runs (reported, and recorded as the update's from_versions), and the versions the
    # version rules judge. They differ only while an update is unfinished (staged, or stopped part-way): the
    # env file selects its release, which that run already checked, and the running containers still run (some
    # of) the one before it. Only running (or restarting) containers count as running.
    current_versions: List[str] = []
    checked_versions: List[str] = []
    unfinished_components: Set[str] = set()
    selected_keys_now: Set[str] = set()  # version keys that already select the unfinished run's release
    production_jobs = jobs_text(installation) if installation.production else ""
    # component -> (the version the env file selects while a run is unfinished, its tag, what its containers run,
    # which containers that is: "running", "paused", "stopped" or "" for none): a plan with nothing to do (`ahead`)
    # reports the version selected.
    unfinished_shown: Dict[str, Tuple[str, str, Optional[str], str]] = {}
    for name in sorted(components):
        info = components[name]
        tag_versions = {t: _strip_variant(t, name) for t in info["tags"]}
        suffix = "-gpu" if name == "serving_gpu" else ""
        labels = sorted({v for v in info["running_versions"] if is_version(v)}, key=parse_version)
        live = sorted(set(info["live_versions"]), key=parse_version)
        current, source = None, ""
        shown: Optional[str] = None
        is_unfinished = False
        # Whether the tag names a version: only a tag that does not (:latest) is pinned to the release's tag.
        names_version = len(info["tags"]) == 1 and all(tag_versions.values())
        if len(info["tags"]) > 1:
            reasons.append(f"{name} services resolve to different tags ({', '.join(info['tags'])})")
        elif all(tag_versions.values()):
            tag = info["tags"][0]
            current, source = tag_versions[tag], f"{info['variable']} in {env_name}"
            if stopped and {tag, current} & stopped_values:
                # An unfinished run (`apply --stage`, or a rollout that stopped part-way) wrote this tag: the
                # containers run what they ran before, or part of it.
                is_unfinished = True
                unfinished_components.add(name)
                selected_keys_now.add(info["variable"])
                states = info["states"]
                # What its containers run: a running one's image (it may carry no version label), else a paused or
                # stopped one's, else nothing.
                shown = live[0] if live else (None if "running" in states else (labels[0] if labels else None))
                selects = (f"{info['variable']} in {env_name} selects the staged {tag}" if staged else
                           f"{info['variable']} in {env_name} selects {tag}, {unfinished_run}")
                found = ("running image" if "running" in states else
                         "image of its paused containers" if "paused" in states else
                         "image of its stopped containers" if "stopped" in states else "no container")
                source = f"{found} ({selects})"
                current_versions.extend(live)
                unfinished_shown[name] = (current, tag, shown, next((state for state in ("running", "paused", "stopped")
                                                                     if state in states), ""))
            elif labels and info["variable"] not in dropped_keys and not stack_reasons:
                # (With the stack reasons, the env file may be another stack's: their remedy comes first.)
                _env_mismatch(info, name, tag, current, labels, suffix, env_name, installation, apply_command,
                              reasons)
        elif info["tags"] == ["local"]:
            if name == "fi_collector" and installation.mode == "distributed":
                source = "built from this checkout"
                notes.append(
                    "fi-collector is built from this checkout today (FI_COLLECTOR_VERSION unset); the update "
                    "switches it to the published futureagi/fi-collector image of the release")
            else:
                reasons.append(
                    f"{name} runs images built from this checkout ({info['variable']}=local). bin/update manages "
                    f"published releases only; keep using `{cd_prefix(installation)}./bin/install --from-source`")
        else:
            if len(labels) == 1:
                current, source = labels[0], f"running image (follows :{info['tags'][0]})"
            elif len(labels) > 1:
                reasons.append(f"{name} containers run different versions ({', '.join(sorted(labels))})")
            elif not stack_reasons:
                # A version key to set is no remedy while the containers are another stack's (check_stack): the
                # env file this run read may not be the install's (a production install checked without
                # --production, say).
                reasons.append(
                    f"cannot tell which version of {name} is installed: {info['variable']} is "
                    f"{info['configured'] or 'unset'} and no running container carries a version label. "
                    f"Set {info['variable']}=vX.Y.Z{suffix} to the release you run, then check again")
        target_tag = component_tag(manifest, name)
        if not is_unfinished:
            shown = current
            if current:
                current_versions.append(current)
        if current:
            checked_versions.append(current)
        # Neither the configured version nor any container may be newer than the release.
        newest_label = labels[-1] if labels else None
        if newest_label and parse_version(newest_label) > parse_version(target):
            downgrades.append(f"{name} runs {newest_label}, newer than this release ({target}); bin/update never "
                              "downgrades")
        elif current and parse_version(current) > parse_version(target):
            if staged and is_unfinished:
                downgrades.append(f"{name} has {current} staged, newer than this release ({target}); bin/update "
                                  "never downgrades")
            elif is_unfinished:
                downgrades.append(f"{info['variable']} in {env_name} selects {current} for {name} ({unfinished_run}), "
                                  f"newer than this release ({target}); bin/update never downgrades")
            else:
                # No container runs anything newer than the release (or none carries a label): the env file selects it.
                downgrades.append(f"{info['variable']} in {env_name} selects {current} for {name}, newer than this "
                                  f"release ({target}); bin/update never downgrades")
        if shown == target and info["tags"] == [target_tag]:
            action = "keep"
        elif shown == target and not names_version:
            # It follows :latest (or another tag that names no version) and runs the release: pinned to its tag.
            action = "pin"
        else:
            # Includes an unfinished run's component that still runs the release while the env file selects
            # another one: the env file is set back to the release's tag.
            action = "update"
        planned.append({
            "component": name, "variable": info["variable"], "services": info["services"],
            "configured": info["configured"], "current_version": shown, "current_source": source,
            "current_tags": info["tags"], "target_tag": target_tag,
            "target_image": manifest["components"][name]["image"],
            "target_digest": manifest["components"][name]["digest"], "action": action,
        })

    # The manifest is an older release than the newest one (`newer_release`, which has no manifest yet), and this
    # install already runs it or something newer: there is nothing to do with it, and nothing to fix.
    ahead = (bool(newer_release) and not finishing and bool(checked_versions)
             and all(parse_version(v) >= parse_version(target) for v in checked_versions))
    if not ahead:
        reasons.extend(downgrades)

    # While a run is unfinished the env file, not the running containers, names the versions the rules judge.
    runs_or_selects = f"{env_name} selects" if unfinished_components else "this install runs"
    if checked_versions and not ahead:
        oldest = min(checked_versions, key=parse_version)
        if parse_version(oldest) < parse_version(manifest["minimum_version"]):
            if installation.production:
                upgrade = f"Upgrade with the release's own steps first ({production_procedure(installation)})"
            else:
                floor = manifest["minimum_version"]
                upgrade = (f"Upgrade first: check out {floor}, set the version keys in .env to {floor}, then run "
                          f"`{cd_prefix(installation)}./bin/install` (it keeps pinned keys as they are; "
                          f"{INSTALL_UPGRADE_DOC})")
            reasons.append(
                f"{runs_or_selects} {oldest}, older than {manifest['minimum_version']}, the oldest release {target} "
                f"can be applied to in place. {upgrade}")
        if parse_version(oldest) < parse_version(target) and not policy_allows(oldest, target, settings.policy):
            reasons.append(
                f"{oldest} -> {target} is outside the '{settings.policy}' update policy "
                f"(FUTURE_AGI_UPDATE_POLICY, from {settings.sources['FUTURE_AGI_UPDATE_POLICY']}); "
                "rerun with --policy major to allow it")

    # Version keys of services outside the active Compose profiles: `docker compose config` omits those
    # services, but the env file still pins them (production pins all seven). The pin moves with the
    # release, so enabling the profile later starts the release's image; nothing of theirs is pulled now.
    # A key that any active service's image follows is not such a pin, and neither is one no service follows.
    used = {COMPONENT_VARIABLES[name] for name in components} | {key for key in followed.values() if key}
    for variable in VERSION_VARIABLES if components else ():
        if variable in used or (inactive_keys is not None and variable not in inactive_keys):
            continue
        value = installation.env.get(variable) or ""
        name = _inactive_component(variable, value, installation.mode)
        version = _strip_variant(value, name)
        if not version:
            continue
        target_tag = component_tag(manifest, name)
        if ahead:
            pass
        elif parse_version(version) > parse_version(target):
            reasons.append(
                f"{variable} in {env_name} pins {value} for a service outside the active Compose profiles, newer "
                f"than this release ({target}); bin/update never downgrades")
        elif version != target:
            notes.append(f"{variable} pins a service outside the active Compose profiles; it is set to {target_tag} "
                         "without pulling it")
        planned.append({
            "component": name, "variable": variable, "services": [], "configured": value,
            "current_version": version, "current_source": f"{variable} in {env_name} (no active service)",
            "current_tags": [value], "target_tag": target_tag,
            "target_image": manifest["components"][name]["image"],
            "target_digest": manifest["components"][name]["digest"],
            "action": "keep" if version == target else "update", "inactive": True,
        })

    if ahead:
        # Nothing is written or pulled: every component stays as it is. An unfinished run's component stays on what
        # the env file selects (the version the rules judged), which its row reports next to what runs.
        for item in planned:
            item["action"] = "keep"
            if item["component"] in unfinished_shown and not item.get("inactive"):
                selected, tag, runs, state = unfinished_shown[item["component"]]
                item["current_version"] = selected
                # What was found, as the row of a plan with something to do words it: only a running container runs.
                if not state:
                    seen = "no container"
                elif state == "running":
                    seen = f"its containers run {runs}" if runs else "its running containers carry no version label"
                else:
                    seen = (f"its {state} containers are on {runs}" if runs else
                            f"its {state} containers carry no version label")
                item["current_source"] = (
                    f"{item['variable']} in {env_name} ("
                    + (f"the staged {tag}" if staged else f"{tag}, {unfinished_run}") + f"; {seen})")
        notes.append(
            f"{f'{env_name} already selects' if unfinished_components else 'this install already runs'} "
            f"{_join(sorted(set(checked_versions), key=parse_version))}, the same as or newer than {target}, the "
            "newest release that has an update manifest: there is nothing to update until the newer release's "
            "manifest is published")

    # Every version key ends with one value: what each component needs (a kept one its current tag).
    # Kept components alone write nothing, so only a key that changes can conflict.
    needed: Dict[str, List[str]] = {}
    for item in planned:
        tag = item["current_tags"][0] if item["action"] == "keep" else item["target_tag"]
        if tag not in needed.setdefault(item["variable"], []):
            needed[item["variable"]].append(tag)
        # A key the unfinished run already set to the release: finishing it writes nothing.
        if item["action"] != "keep" and not (item["variable"] in selected_keys_now
                                              and item["configured"] == item["target_tag"]):
            env_changes[item["variable"]] = item["target_tag"]
    for variable, tags in needed.items():
        if variable in env_changes and len(tags) > 1:
            reasons.append(f"{variable} would need two values ({', '.join(tags)})")

    # bin/update writes plain tags only: a key it changes must hold one already, or the update would silently
    # replace whatever the operator wrote there (a ${...} reference Compose expands, a quoted or commented value).
    for variable in sorted(env_changes):
        value = installation.env.get(variable) or ""
        if value and variable not in digest_variables and not TAG_VALUE_RE.fullmatch(value):
            tags = [item["current_tags"][0] for item in planned if item["variable"] == variable
                    and item["current_tags"] and TAG_VALUE_RE.fullmatch(item["current_tags"][0])]
            reasons.append(
                f"{variable} in {env_name} is {value}, not a literal image tag: bin/update writes plain tags only, so "
                f"updating it would replace that value outright. Set {variable}={tags[0] if tags else 'vX.Y.Z'} (the "
                "tag it selects now), then check again")

    drift = []
    files = execution_files(manifest, deployment_authorization, raw_manifest)
    local = deployment_hashes(installation.root, files)
    for path, expected in sorted(files.items()):
        if local.get(path) != expected:
            drift.append(path)
    if drift and not ahead:
        reasons.append(_drift_reason(installation, drift, target, newer_release))

    # The example names the release's settings only once the checkout matches the release. With the stack reasons
    # the env file may be another stack's (a production install checked without --production, say): their remedy
    # comes alone, never ./bin/install's, which would write that file.
    example = installation.root / ("deploy/.env.production.example" if installation.production else ".env.example")
    missing = ([] if drift or ahead or stack_reasons
               else [key for key in example_keys(example) if key not in installation.env])
    if missing:
        if installation.production:
            notes.append(
                f"{env_name} lacks keys that deploy/.env.production.example sets: {', '.join(missing)}. "
                "Compose uses their defaults; add them explicitly if you need other values")
        else:
            unmergeable = _install_cannot_merge(installation.env_file)
            if unmergeable:
                reasons.append(
                    f".env lacks settings this release's .env.example adds ({', '.join(missing)}). Copy them from "
                    ".env.example into .env by hand, then check again: ./bin/install cannot merge this .env (it "
                    f"misreads definitions written with {_join(unmergeable)} and would add empty duplicates of them)")
            else:
                reasons.append(
                    f".env lacks settings this release's .env.example adds ({', '.join(missing)}). Run "
                    f"`{cd_prefix(installation)}./bin/install --no-up -y` to add them (it keeps every existing value "
                    "and starts nothing), then check again")

    production_steps = (installation.production
                        and bool((manifest.get("migration") or {}).get("requires_bootstrap")))
    # The production initialization jobs read the same env file and compose files as the stack: this prefix,
    # not deploy/README.md's $COMPOSE, which names deploy/.env.production.
    jobs = production_jobs if production_steps else ""
    resume = False
    # Notes that name the commands to run next (check prints them; apply's echo of the plan it carries out leaves
    # them out, its Decision line says what it does).
    step_notes: List[str] = []

    def step(note: str) -> None:
        notes.append(note)
        step_notes.append(note)

    if stopped:
        if stopped_kind == "rollback":
            reasons.append(
                "a rollback stopped while containers were being replaced. Finish it first: "
                f"{command} rollback --receipt {_receipt_argument(stopped.get('rolls_back'))} --yes")
        elif staged and finishing:
            resume = True
            step(
                f"the update to {target} is staged (its images are pulled and verified, nothing was restarted); "
                + (f"if the release's initialization jobs have not run yet, run them with {jobs}; then "
                   if production_steps else "")
                + f"{apply_command()} finishes it")
        elif staged:
            version = stopped.get("target_version") or "?"
            checkout, jobs_step, finish = other_release(stopped)
            reasons.append(
                f"the update to {version} is staged (its images are pulled and verified, {env_name} selects them, "
                f"and nothing was restarted). Finish it first ({checkout}{jobs_step}then {finish}) or undo it "
                f"({command} rollback --receipt {_receipt_argument(stopped.get('_path'))} --yes), then check again")
        elif finishing:
            resume = True
            step(
                f"the previous update to {target} stopped while containers were starting{how}; apply finishes it")
            if production_steps:
                step(
                    f"{env_name} already selects {target}: if the release's initialization jobs have not run yet "
                    f"(the production bootstrap jobs only check the schema), run them first with {jobs}")
        else:
            checkout, jobs_step, finish = other_release(stopped)
            reasons.append(
                f"the previous update to {stopped.get('target_version')} stopped while containers were starting"
                f"{how}. Finish it ({checkout}{jobs_step}then {finish}) or roll it back "
                f"({_undo_command(command, stopped)}) first")

    if any(item["action"] == "pin" for item in planned):
        notes.append("images that follow :latest are pinned to the release's version tag when an update runs")

    something_to_do = not ahead and (resume or any(item["action"] == "update" for item in planned))
    _judge_strays(strays, installation, planned, stopped if resume else None, target, env_name,
                  something_to_do, reasons, notes, active_components=set(components))
    if installation.mode == "standalone" and something_to_do:
        # apply waits for the app the way bin/install does, with these settings: it refuses bad ones.
        try:
            standalone_settings(installation.env)
        except NotReady as exc:
            reasons.append(f"{env_name}: {exc}. bin/update waits for the app with it as bin/install does; fix it, "
                           "then check again")

    next_command: Optional[str] = None
    before_next_command: Optional[str] = None
    if reasons:
        decision = BLOCKED
    elif something_to_do:
        decision = UPDATE_AVAILABLE
        next_command = apply_command()
        if production_steps and not resume:
            next_command = apply_command("--stage")
            step(
                f"production installs update in two steps: {apply_command('--stage')} (backs up, then pulls and "
                f"verifies the release; restarts nothing), run the release's initialization jobs with {jobs}, "
                f"then {apply_command()}")
        elif production_steps:
            # Finishing a staged or stopped update restarts on the release: its jobs must have run by then.
            before_next_command = f"if the release's initialization jobs have not run yet, run them first with {jobs}"
    else:
        decision = UP_TO_DATE
        env_changes = {}
    return {
        "schema_version": 2,
        "decision": decision,
        "resume": resume and decision == UPDATE_AVAILABLE,
        "next_command": next_command,
        "before_next_command": before_next_command,
        # The release a staged update (`apply --stage`) selects while the stack still runs current_versions.
        "staged_version": staged_version,
        # The release an update or rollback that stopped part-way selects (stopped_kind: "apply" or "rollback")
        # while the running containers run current_versions.
        "stopped_version": stopped_version,
        "stopped_kind": stopped_kind if stopped_version else None,
        "target_version": target,
        "channel": manifest["channel"],
        "published_at": manifest["published_at"],
        "minimum_version": manifest["minimum_version"],
        "policy": settings.policy,
        "manifest_hash": manifest_hash(manifest),
        "manifest_source": manifest_source,
        "command_options": options,
        "installation": {"mode": installation.mode, "project": installation.project,
                         "env_file": installation.relative(installation.env_file), "root": str(installation.root)},
        "current_versions": sorted(set(current_versions), key=parse_version),
        "signature": verification.as_dict(),
        "components": planned,
        "env_changes": env_changes,
        "deployment_drift": drift,
        "missing_env_keys": missing,
        "reasons": reasons,
        "notes": notes,
        # The notes (each also in `notes`) that name the commands to run next: the staged or stopped update's
        # finishing command, its initialization jobs, production's two steps. plan_text(applying=True) leaves
        # them out: apply's echo says what that run does instead.
        "step_notes": [note for note in step_notes if note in notes],
    }


def jobs_text(installation: Installation) -> str:
    """How to run a production release's initialization jobs for this install (plan and apply print it).

    They read the same env file and compose files as the stack: this
    install's Compose prefix, not deploy/README.md's $COMPOSE (which names
    deploy/.env.production). The env file decides only where the shell does
    not export its keys: Compose prefers an exported value (shell_rule).
    """

    return f"`{installation.compose_hint()} run --rm ...` ({PRODUCTION_UPGRADE_DOC}. {shell_rule(installation)})"


def shell_rule(installation: Installation, what: str = "it") -> str:
    """The sentence that follows every Compose command printed for a production install to run by hand.

    Compose run by hand prefers a value the shell exports over the env
    file's, so an exported FUTURE_AGI_VERSION would start another release
    than the env file names. The sentence is the same wherever bin/update
    prints such a command (`what` stays "it").
    """

    env_name = installation.relative(installation.env_file)
    return (f"Run {what} from a shell that does not export any key of {env_name} (Compose prefers your shell over "
            "the env file).")


def production_procedure(installation: Installation) -> str:
    """deploy/README.md's production upgrade procedure, with this install's Compose command for its $COMPOSE.

    The procedure's $COMPOSE names deploy/.env.production: an install with
    another env file (or a standalone Compose binary) runs it with its own.
    """

    return (f"{PRODUCTION_UPGRADE_DOC}, with `{installation.compose_hint()} ...` for its $COMPOSE commands. "
            f"{shell_rule(installation)}")


def _git(root: Path, *args: str) -> Optional[str]:
    """`git -C root ARGS`'s output, or None when git is missing or fails (not a work tree, say).

    Pathspecs are literal, and git's optional locks are off: reading the
    status never rewrites the checkout's index. Variables that would point
    git at another repository are dropped.
    """

    env = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_VARIABLES}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(["git", "--literal-pathspecs", "-C", str(root), *args], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "surrogateescape")


def _git_records(output: str, *, renames: bool) -> List[Tuple[str, str]]:
    """(code, path) of each record of git's NUL-separated `status --porcelain=v1 -z` or `diff --name-status -z`."""

    fields = output.split("\0")
    records: List[Tuple[str, str]] = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if renames:
            # status: "XY path"; a rename or copy is followed by the path it came from.
            if len(field) < 4:
                continue
            code, path = field[:2], field[3:]
            if "R" in code or "C" in code:
                index += 1
        else:
            # diff --name-status: the status, then the path, as two fields.
            if not field or index >= len(fields):
                continue
            code, path = field, fields[index]
            index += 1
        records.append((code, path))
    return records


def _git_drift(root: Path, drift: Sequence[str], target: str) -> Optional[Dict[str, Any]]:
    """What git says about the deployment entries that differ from `target`'s, or None without git.

    None unless `root` is the top level of a git work tree git can read.
    Otherwise {"at_target": HEAD is the commit of tag `target`, "tag": that
    tag is in this checkout, "changed": tracked files that differ from
    `target`'s (at the target, from `git diff <target>`; elsewhere, the ones
    `git status` shows changed here, which checking out the target keeps),
    "gone": elsewhere, files changed here that `target` does not have (the
    checkout of the target refuses to remove them), "added": files added to
    git's index here (`git add`, or `git add -N`) and not committed,
    "extras": files `git status` shows untracked or ignored (or, at the
    target, files the target does not have) that the directory digest
    counts (manifest.hash_path skips editor and OS litter)}.
    """

    # The top level of a work tree holds its .git (a directory, or a file for a linked work tree): without one,
    # git is not asked at all.
    if not (Path(root) / ".git").exists():
        return None
    top = _git(root, "rev-parse", "--show-toplevel")
    try:
        if top is None or Path(top.strip()).resolve() != Path(root).resolve():
            return None
    except (OSError, RuntimeError):
        return None
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored", "--", *drift)
    if status is None:
        return None
    head = (_git(root, "rev-parse", "-q", "--verify", "HEAD^{commit}") or "").strip()
    tag = (_git(root, "rev-parse", "-q", "--verify", f"refs/tags/{target}^{{commit}}") or "").strip()
    local_changed: Set[str] = set()
    local_new: Set[str] = set()
    local_added: Set[str] = set()
    # Tracked files whose content (or type) was changed here: HEAD has them, so `git checkout HEAD -- X` puts them back.
    edited: Set[str] = set()
    for code, path in _git_records(status, renames=True):
        if code in ("??", "!!"):
            local_new.add(path)
        elif "A" in code and "U" not in code and code != "AA":
            # In git's index, not in HEAD: `git add` ("A ", "AM"), or `git add -N` (" A"); not a merge conflict.
            local_added.add(path)
        else:
            local_changed.add(path)
            if not set(code) - {" ", "M", "T"}:
                edited.add(path)
    at_target = bool(tag) and head == tag
    differs: Optional[Dict[str, str]] = None
    if tag:
        diff = _git(root, "diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", tag, "--", *drift)
        if diff is not None:
            differs = {path: code[:1] for code, path in _git_records(diff, renames=False)}
    gone: Set[str] = set()
    if differs is None:
        changed = set(local_changed)
    else:
        changed = {path for path in (set(differs) if at_target else local_changed)
                   if differs.get(path) not in (None, "A")}
        if at_target:
            local_new |= {path for path, code in differs.items() if code == "A"} - local_added
        else:
            gone = {path for path in edited if differs.get(path) == "A"}

    def counted(path: str) -> bool:
        for entry in drift:
            entry = entry.rstrip("/")
            if path == entry:
                return True
            if path.startswith(entry + "/"):
                return not _skipped_in_directory_hash(tuple(part for part in path[len(entry) + 1:].split("/") if part))
        return False

    added = {path for path in local_added if counted(path)}
    extras = {path for path in local_new if counted(path)} - changed - added
    return {"at_target": at_target, "tag": bool(tag), "changed": sorted(changed), "gone": sorted(gone),
            "added": sorted(added), "extras": sorted(extras), "local_changed": sorted(local_changed)}


def _git_hidden(root: Path, drift: Sequence[str]) -> Optional[List[str]]:
    """The files below `drift` git does not compare with the work tree, or None when git cannot tell.

    `git ls-files -v` tags a file marked assume-unchanged with a lowercase
    letter and one marked skip-worktree (as a sparse checkout marks the
    files it leaves out) with S: git status and git diff show no change to
    either.
    """

    listing = _git(root, "ls-files", "-v", "-z", "--", *drift)
    if listing is None:
        return None
    return sorted(record[2:] for record in listing.split("\0")
                  if len(record) > 2 and record[1] == " " and (record[0] == "S" or record[0].islower()))


def _git_work_tree(root: Path) -> bool:
    """Whether `git -C root rev-parse --is-inside-work-tree` succeeds: only then does a printed git command work."""

    return (_git(root, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"


def _named(paths: Sequence[str]) -> str:
    """Up to _NAMED_PATHS of `paths`, and how many more there are."""

    if len(paths) <= _NAMED_PATHS:
        return _join(paths)
    return f"{_join(paths[:_NAMED_PATHS])} and {len(paths) - _NAMED_PATHS} more"


def _drift_reason(installation: Installation, drift: Sequence[str], target: str, newer_release: Optional[str]) -> str:
    """Why this checkout's deployment files block the update to `target`, and the commands that clear it.

    In a git work tree, git names what differs (_git_drift): files changed
    here are put back with `git checkout <target> -- <paths>`, files git
    does not track are moved away; at the target itself there is nothing to
    check out. Without git, a drifted directory may hold a file the release
    does not have, which the reason says may be the case (never that it is).
    A git command is printed only where git works in the checkout
    (_git_work_tree); otherwise the reason says the files come from the
    release.
    """

    # git runs in the checkout: a command printed for a shell elsewhere starts with `cd <checkout> && `.
    in_checkout = cd_prefix(installation)
    where = "" if in_checkout else f" in {installation.root}"
    listed = ", ".join(drift)
    found = _git_drift(installation.root, drift, target)
    parts: List[str] = []

    def entries_of(paths: Sequence[str]) -> List[str]:
        # Many files: their entries (a directory puts back every tracked file below it).
        return list(paths) if len(paths) <= _NAMED_PATHS else [
            entry for entry in drift if any(path == entry or path.startswith(entry.rstrip("/") + "/")
                                            for path in paths)]

    def words(paths: Sequence[str]) -> str:
        return " ".join(shlex.quote(path) for path in paths)

    if found:
        changed, extras, added = found["changed"], found["extras"], found["added"]
        # With a newer release waiting, this checkout is likely on that one: what it changed is put back from its
        # own commit, never from the older release.
        own = bool(newer_release) and not found["at_target"]
        gone = [] if own else found["gone"]
        if own:
            changed = found["local_changed"]
        if changed:
            one = len(changed) == 1
            quoted = words(entries_of(changed))
            if own:
                parts.append(
                    f"{_named(changed)} {'was' if one else 'were'} changed (`{in_checkout}git checkout HEAD -- "
                    f"{quoted}`{where} puts back this checkout's {'version' if one else 'versions'})")
            else:
                fetch = "" if found["tag"] else "git fetch --tags && "
                parts.append(
                    f"{_named(changed)} {'differs' if one else 'differ'} from {target}'s "
                    f"(`{in_checkout}{fetch}git checkout {target} -- {quoted}`{where} puts back {target}'s "
                    f"{'version' if one else 'versions'}, discarding the changes made here)")
        if gone:
            # The checkout of the target refuses to remove a file changed here.
            one = len(gone) == 1
            parts.append(
                f"{_named(gone)} {'was' if one else 'were'} changed here and {'is' if one else 'are'} not part of "
                f"{target} (`{in_checkout}git checkout HEAD -- {words(entries_of(gone))}`{where} discards the "
                f"{'change' if one else 'changes'}; the checkout of {target} then removes {'it' if one else 'them'})")
        if added:
            # In git's index, not committed: a checkout carries the entry over, so it leaves the index first.
            one = len(added) == 1
            parts.append(
                f"{_named(added)} {'is' if one else 'are'} added to git's index here but not committed "
                f"(`{in_checkout}git reset -q -- {words(added)}`{where} takes {'it' if one else 'them'} out of the "
                f"index): move {'it' if one else 'them'} out of the checkout")
        if extras:
            one = len(extras) == 1
            what = f"{'is' if one else 'are'} not part of {target}" if found["at_target"] else (
                f"{'is' if one else 'are'} not tracked by git")
            parts.append(f"{_named(extras)} {what}: move {'it' if one else 'them'} out of the checkout")
        if max(len(changed), len(gone), len(added), len(extras)) > _NAMED_PATHS:
            # Explicit flags: the listing must not depend on the operator's status.showUntrackedFiles.
            parts.append(f"`{in_checkout}git status --untracked-files=all --ignored -- {words(drift)}`{where} lists "
                         "every one")
    local = "; ".join(parts)
    if newer_release:
        # Without git nothing names a file the release does not have: this checkout may hold a newer release's own.
        made = ""
        if local and found and found["at_target"]:
            made = f". This checkout is at {target}, so they were made here: {local}"
        elif local:
            made = f". git shows changes made in this checkout: {local}"
        return (f"this checkout's deployment files differ from {target}'s ({listed}), but {target} is not the newest "
                "release: the newest one has no update manifest yet. Wait until its manifest is published and check "
                f"again, or pass --manifest with the manifest URL of the release this checkout is on{made}")
    hint = ""
    directories = [path for path in drift if (installation.root / path).is_dir()]
    if found is None and directories:
        one = len(directories) == 1
        hint = (f". {_join(directories)} also {'differs' if one else 'differ'} when "
                f"{'it holds' if one else 'they hold'} a file that is not part of {target} (a log or an editor's "
                "backup, say): move any such file out of the checkout")
    if found and found["at_target"]:
        if local:
            return (f"this checkout's deployment files differ from {target}'s ({listed}). This checkout is at "
                    f"{target}, so the differences were made here: {local}. Then check again")
        start = (f"this checkout's deployment files differ from {target}'s ({listed}). This checkout is at {target}, "
                 "yet git shows no change in them")
        hidden = _git_hidden(installation.root, drift)
        if hidden:
            one = len(hidden) == 1
            # One flag per command: `git update-index` given both clears only the first. Many files are read
            # from git's own listing.
            clear = " && ".join(
                f"git update-index --no-{flag} -- {words(hidden)}" if len(hidden) <= _NAMED_PATHS else
                f"git ls-files -z -- {words(drift)} | git update-index -z --no-{flag} --stdin"
                for flag in ("assume-unchanged", "skip-worktree"))
            return (f"{start}: git does not compare {_named(hidden)} with the {'file' if one else 'files'} here "
                    f"({'it is' if one else 'they are'} marked assume-unchanged or skip-worktree, as a sparse checkout "
                    f"marks the files it leaves out). Run `{in_checkout}{clear}`{where} so git compares "
                    f"{'it' if one else 'them'} again, then check again: it names what differs")
        return (f"{start}. Possible causes: git converts their line endings (core.autocrlf, or .gitattributes), or "
                "git does not compare a file with the work tree (marked assume-unchanged or skip-worktree, as a "
                f"sparse checkout marks the files it leaves out): `{in_checkout}git ls-files -v -- {words(drift)}`"
                f"{where} shows such a file with a lowercase letter or S. Fix the cause, then check again")
    if found is None and not _git_work_tree(installation.root):
        one = len(drift) == 1
        return (f"this checkout's deployment files differ from {target}'s ({listed}). New images must run with their "
                f"own compose files, and git cannot be used in this checkout ({installation.root}) to put "
                f"{'it' if one else 'them'} back: replace {'it' if one else 'them'} with release {target}'s "
                f"{'version' if one else 'versions'} (your env file stays as it is), then check again{hint}")
    # A checkout of the release keeps the changes made here (or refuses to run over them): those come first.
    first = f"First undo the changes made here, which a checkout keeps: {local}. " if local else ""
    return (f"this checkout's deployment files differ from {target}'s ({listed}). {first}New images must run with "
            f"their own compose files: run `{in_checkout}git fetch --tags && git checkout {target}`{where} (your env "
            f"file is not tracked and stays as it is), then check again{hint}")


def _compose_program(installation: Installation) -> str:
    """The Compose command (compose_hint) without the `cd <checkout> && ` that makes it a command to paste."""

    hint = installation.compose_hint()
    cd = cd_prefix(installation)
    return hint[len(cd):] if cd and hint.startswith(cd) else hint


def _env_mismatch(info: Mapping[str, Any], name: str, tag: str, current: str, labels: Sequence[str], suffix: str,
                  env_name: str, installation: Installation, apply_command: Any, reasons: List[str]) -> None:
    """Why containers that run another version than the env file's tag block, and how to make them agree.

    The remedy never restarts onto a version that could do harm: not an older
    tag when a newer image of a component that runs the database migrations
    already ran (it may have migrated the schema), and in production not a
    newer tag whose initialization jobs may not have run (its images would
    start on the old schema; bin/update's two steps run them in between). A
    production stack never migrates on start (its bootstrap job only checks
    the schema): only a newer release's initialization jobs change the
    schema, so the remedy there never claims a newer image did, and never
    sends the operator to a backup. Every Compose command it prints for
    production carries the shell rule (shell_rule).
    """

    variable = info["variable"]
    newer = labels[-1] if parse_version(labels[-1]) > parse_version(current) else None
    migrates = name in MIGRATING_COMPONENTS
    production = installation.production
    keep_off = (f"Do not restart the stack on {tag}: {newer} may have migrated the database, and an older release "
                f"does not run on a newer schema. To go back to {tag}, restore the backup taken before {newer} "
                "started")
    if production:
        keep_off = (f"Do not restart the stack on {tag} if the initialization jobs of {newer} have run: an older "
                    "release does not run on a newer schema")
    two_steps = (f"a production install updates in two steps around the release's initialization jobs "
                 f"({apply_command('--stage')}, the jobs, then {apply_command()})")
    # Restarting the stack as Compose would by hand: in production, from a shell that exports no key.
    restart = f"`{installation.compose_hint()} up -d`" + (f". {shell_rule(installation)}" if production else "")
    if len(labels) == 1 and labels[0] != current:
        one = len(info["labelled"]) == 1
        changed = (
            f"{'service' if one else 'services'} {', '.join(info['labelled'])} {'runs' if one else 'run'} "
            f"{labels[0]}, but {variable} in {env_name} is {tag}: the env file was changed without restarting the "
            "stack.")
        if newer and migrates:
            reasons.append(f"{changed} Set {variable} back to {labels[0]}{suffix}, the version that runs, then check "
                           f"again. {keep_off}")
        elif newer:
            reasons.append(f"{changed} Set {variable} back to {labels[0]}{suffix}, the version that runs, or restart "
                           f"the stack so it runs {tag}, then check again")
        elif installation.production:
            reasons.append(f"{changed} Set {variable} back to {labels[0]}{suffix}, the version that runs, then check "
                           f"again: {two_steps}. Do not restart the stack on {tag} before its initialization jobs "
                           "have run: its images would start on the old schema")
        else:
            reasons.append(f"{changed} Set {variable} back to {labels[0]}{suffix} (bin/update then updates it), or "
                           f"restart the stack so it runs {tag}, then check again")
    elif len(labels) > 1:
        running_now = ", ".join(f"{version} ({', '.join(info['by_version'][version])})" for version in labels)
        mixed = (f"{name} containers run different versions ({running_now}) while {variable} in {env_name} is "
                 f"{tag}: part of the stack was restarted on another version.")
        if newer and production:
            # The stack never migrated on start: restarting it as it is (on the tag) is safe unless the newer
            # release's initialization jobs ran. The newer tag comes only after its jobs: bin/update's two steps.
            if migrates:
                reasons.append(
                    f"{mixed} Make them agree before updating. If the initialization jobs of {newer} have not run, "
                    f"restart the stack as it is ({restart}) so every service runs {tag}, then check again: "
                    f"{two_steps}. If they have run, set {variable}={newer}{suffix}, then restart the stack the same "
                    f"way so every service runs {newer}, and check again")
            else:
                reasons.append(
                    f"{mixed} Make them agree before updating: restart the stack as it is ({restart}) so every "
                    f"service runs {tag}, then check again: {two_steps}")
        elif newer:
            # A newer image of a component that runs no migrations can also be restarted onto the older tag.
            reasons.append(
                f"{mixed} Set {variable}={newer}{suffix}, then restart the stack (`{installation.compose_hint()} up "
                f"-d`) so every service runs {newer}, and check again. "
                + (keep_off if migrates else f"Or restart the stack as it is, so every service runs {tag}"))
        elif production:
            # The tag is the newest: its initialization jobs may not have run. Without them, the stack goes back
            # to the newest version below the tag that it runs.
            below = [version for version in labels if parse_version(version) < parse_version(current)]
            back = below[-1] if below else labels[0]
            reasons.append(
                f"{mixed} Make them agree before updating. If the initialization jobs of {tag} have run, restart the "
                f"stack ({restart}) so every service runs {tag}; if they have not, set {variable} back to "
                f"{back}{suffix} and restart the stack the same way so every service runs it, then check again: "
                f"{two_steps}")
        else:
            reasons.append(
                f"{mixed} Make them agree before updating: restart the stack (`{installation.compose_hint()} up -d`) "
                f"so every service runs {tag}, or first set {variable} to the version they should all run; then "
                "check again")


def _futureagi_image(image: str) -> bool:
    return _image_component(image) is not None


def plan_json(plan: Mapping[str, Any]) -> str:
    return json.dumps(plan, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def plan_text(plan: Mapping[str, Any], *, applying: bool = False, stage: bool = False,
              on_host: Optional[bool] = None) -> str:
    """The plan as check prints it; `applying`: as apply prints the plan it is carrying out.

    Then the Decision line says what this run does, not which command to run:
    with `stage` (`apply --stage`), that it stages the release and restarts
    nothing. The notes that name the commands to run next (step_notes: a
    staged or stopped update's finishing command and jobs, production's two
    steps) are left out. `on_host` is True when every image the run starts
    is already on this host with its signed digest, so it pulls none (it
    verifies them again); False or None (not known): the run pulls them.
    """

    inst = plan["installation"]
    versions = ", ".join(plan["current_versions"])
    current = versions or "unknown"
    # Only running (or restarting) containers count as running; none may carry a version label.
    running = f"{versions or 'no labelled container'} running"
    if plan.get("staged_version"):
        # `apply --stage` changed the env file and restarted nothing: the stack still runs current_versions.
        current = f"{running}, {plan['staged_version']} staged"
    elif plan.get("stopped_version"):
        # The env file selects what that run was moving to; the containers run current_versions (part of it).
        what = "rollback" if plan.get("stopped_kind") == "rollback" else "update"
        current = f"{running}, {what} to {plan['stopped_version']} stopped part-way"
    lines = [
        "Future AGI update check",
        f"  Installation  {inst['mode']} (Compose project {inst['project']}, {inst['env_file']})",
        f"  Installed     {current}",
        f"  Release       {plan['target_version']} ({plan['channel']}, published {plan['published_at']})",
    ]
    sig = plan["signature"]
    lines.append("  Signature     " + (f"verified ({sig['method']}: {sig['signer']})" if sig["verified"]
                                       else f"NOT verified: {sig['error']}"))
    # A plan built elsewhere (a test's, say) may not name its command: apply is the one that moves forward.
    command = plan.get("next_command") or " ".join(
        [f"{command_prefix(inst['mode'] == 'production', inst['env_file'])} apply",
         *(shlex.quote(str(word)) for word in plan.get("command_options") or [])])
    try:
        first_step = "--stage" in shlex.split(command)
    except ValueError:
        first_step = " --stage" in command
    if applying:
        target = plan["target_version"]
        reuses = "reuses its images already on this host, verified again, not pulled"
        if stage:
            doing = (f"staging {target} ({reuses}; restarts nothing)" if on_host else
                     f"staging {target} (pulls and verifies it, restarts nothing)")
        else:
            if plan["resume"]:
                doing = (f"finishing the staged update to {target}" if plan.get("staged_version") else
                         f"finishing the stopped update to {target}")
            else:
                doing = f"applying {target}"
            if on_host:
                doing += f" ({reuses})"
    else:
        why = ""
        if plan["resume"]:
            why = " (finishes the staged update)" if plan.get("staged_version") else " (finishes the stopped update)"
        elif first_step:
            why = " (the first of two steps, see below)"
        doing = f"run {command}{why}"
        if plan.get("before_next_command"):
            doing = f"{plan['before_next_command']}; then {doing}"
    verdict = {
        UP_TO_DATE: "up to date, nothing to do",
        UPDATE_AVAILABLE: f"update available: {doing}",
        BLOCKED: "blocked (see below)",
    }[plan["decision"]]
    lines.append(f"  Decision      {verdict}")
    if plan["components"]:
        lines.append("  Components")
        for item in plan["components"]:
            arrow = {"keep": "keep", "pin": "pin ", "update": "->  "}[item["action"]]
            # An inactive profile's pin is only written to the env file: no image is pulled or verified.
            detail = "not pulled (profile not active)" if item.get("inactive") else f"{item['target_digest'][:19]}…"
            lines.append(
                f"    {item['component']:<18} {item['current_version'] or '?':<10} {arrow} {item['target_tag']:<14} "
                f"{detail}")
    for reason in plan["reasons"]:
        lines.append(f"  ✗ {reason}")
    # What to run next is check's business: the run carrying the plan out says what it does (Decision).
    skipped = set(plan.get("step_notes") or []) if applying else set()
    for note in plan["notes"]:
        if note not in skipped:
            lines.append(f"  · {note}")
    return "\n".join(lines) + "\n"
