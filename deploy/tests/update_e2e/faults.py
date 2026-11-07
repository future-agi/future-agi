"""Fault injection for the bin/update Docker e2e tests (test_update_e2e_faults.py).

FaultDocker writes `docker` and `docker-compose` wrappers into a private bin
directory that goes first on the PATH of one bin/update process (never on the
harness's own). Without FAGI_FAULT_DIR in their environment they only run the
real binary. With it, they record every call bin/update makes (index,
program, the action taken, the process group, the words) and, at a chosen
call, act instead of (or after) running Docker:

  fail            exit with a realistic error (FAGI_FAULT_STDERR, _STDOUT, _EXIT)
  fail-after      run Docker, then report that error anyway (`up` that half worked)
  sigkill         SIGKILL bin/update's whole process group (power loss, kill -9)
  sigkill-after   run Docker, then SIGKILL the group (killed right after a change)
  sigterm         SIGTERM the group (Ctrl-C, a closing terminal)
  sigterm-after   run Docker, then SIGTERM the group
  pause           wait until the test releases the call, then run Docker

The call is chosen either by absolute invocation number (FAGI_FAULT_INDEX),
or as the FAGI_FAULT_AT-th call whose words match the shell glob
FAGI_FAULT_MATCH (default `*`, so AT alone counts every call), for
FAGI_FAULT_TIMES matching calls (default 1, or `all`). Calls matching a glob
of FAGI_FAULT_SKIP (one per line) are recorded but never acted on: a stopped
Docker daemon still lets `docker compose config` and `version` run.
`docker-compose ARGS` is recorded and matched as `compose ARGS`, so one glob
fits whichever way bin/update runs Compose.

A wrapper signals a process group only when its parent (bin/update) leads
that group, as it does when the harness spawns it in a session of its own
(Checkout.spawn), and never the group named in FAGI_FAULT_PROTECT (the test
runner's): a bin/update run with `run()` in the test runner's own group is
refused (exit 98) instead of taking the test runner down with it.

The rest of the module is plain logic over recorded calls, unit-tested
without Docker: what kind of call each one is (classify), a glob that
selects it in another run of the same command (signature, occurrence), the
fault points of a matrix (full_matrix, state_matrix), where in an update a
fault landed (phase_of), the realistic error for each kind of call
(error_for), and a reader of `bin/update status` (parse_status).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import harness as e2e

FAULTS_PREFIX_VAR = "FUTURE_AGI_UPDATE_E2E_FAULTS_PREFIX"
DEFAULT_FAULTS_PREFIX = "fagi-upd-e2e-flt-"
# How much of the matrix test_update_e2e_faults.py runs: `full` (every call of the Standalone sequences), or
# `state` (the state-changing calls and their neighbours everywhere, the cheapest meaningful matrix).
SCOPE_VAR = "FUTURE_AGI_UPDATE_E2E_FAULTS_SCOPE"

ACTIONS = ("fail", "fail-after", "sigkill", "sigkill-after", "sigterm", "sigterm-after", "pause")
# Calls that change the Docker host: an image pulled or tagged, containers replaced. The env file write is
# no Docker call; the calls next to it are config-check (the first after it) and the last planning call.
STATE_CHANGING = frozenset(("pull", "up", "tag"))
# The exit status of a process killed by SIGKILL, as subprocess reports it.
KILLED = -9

DAEMON_DOWN = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
REGISTRY_UNREACHABLE = ('Error response from daemon: Get "https://registry.invalid/v2/": dial tcp: lookup '
                        "registry.invalid on 127.0.0.53:53: no such host")
INVALID_COMPOSE_CONFIG = "yaml: line 17: did not find expected key"
NO_SPACE = ("Error response from daemon: failed to register layer: write "
            "/var/lib/docker/overlay2/7f3c2e9d/diff/start.sh: no space left on device")
# `docker compose` that is not installed (Compose v2 as a plugin), as the docker CLI says it.
NO_COMPOSE = "docker: 'compose' is not a docker command.\nSee 'docker --help'"

# Read from a separate process before the injected action, never from updater
# objects or its eventual exit/receipt. Only allowlisted header tokens leave it.
# This observes the published journal; it does not simulate fsync/power loss.
OBSERVER = r"""import json, os
from pathlib import Path
path = os.environ.get("FAGI_FAULT_JOURNAL")
header = ["unobserved"] * 3
if path:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        allowed = (("prepare", "env_written", "pulled", "rollout"), ("apply", "rollback"), ("running",))
        header = [data.get(key) if data.get(key) in values else "invalid"
                  for key, values in zip(("stage", "kind", "status"), allowed)]
    except FileNotFoundError:
        header = ["absent"] * 3
    except (OSError, ValueError, AttributeError):
        header = ["invalid"] * 3
print("\t".join(header))
"""

# Observe only the output facts that distinguish optional model discovery and
# third-party cache repair. The full Compose model/environment is NEVER saved:
# stdout/stderr go back to the updater unchanged, and the sidecar holds only a
# profile-result enum and hashes of (service, image) pairs, excluding builds.
RESULT_OBSERVER = r"""import hashlib, json, os, re, signal, subprocess, sys
from pathlib import Path
index, real, *args = sys.argv[1:]
result = subprocess.run([real, *args], capture_output=True)
evidence = {"profiles": "unobserved", "service_images": []}
if args[-2:] == ["config", "--profiles"]:
    names = [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    evidence["profiles"] = ("failed" if result.returncode else
                            "invalid" if not all(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", n) for n in names) else
                            "nonempty" if names else "empty")
elif args[-3:] == ["config", "--format", "json"] and result.returncode == 0:
    try:
        model = json.loads(result.stdout)
        services = model.get("services") or {}
        def digest(value):
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
        evidence["service_images"] = [(digest(name), digest(service["image"]))
            for name, service in sorted(services.items()) if isinstance(service, dict) and "build" not in service
            and isinstance(service.get("image"), str) and service["image"]]
    except (ValueError, AttributeError, TypeError):
        pass
Path(os.environ["FAGI_FAULT_DIR"], index + ".evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.stdout.flush()
sys.stderr.flush()
if result.returncode < 0:
    signal.signal(-result.returncode, signal.SIG_DFL)
    os.kill(os.getpid(), -result.returncode)
sys.exit(result.returncode)
"""

WRAPPER = r"""#!/bin/sh
# Fault injector of the bin/update Docker e2e tests (deploy/tests/update_e2e/faults.py wrote it).
# It stands first on one bin/update's PATH as @NAME@; deploy/tests/update_e2e/faults.py says how it works.
real=@REAL@
name=@NAME@
[ -n "${FAGI_FAULT_DIR:-}" ] || exec "$real" "$@"
dir=$FAGI_FAULT_DIR
# Opt-in stable environment for staged finishes. The CLI binds its complete
# shell environment at stage time; fault choices live in this private test file
# so changing the selected call does not change the installation identity.
if [ -f "$dir/settings" ]; then . "$dir/settings"; fi
if [ "$name" = docker-compose ]; then words="compose $*"; else words="$*"; fi

# One call at a time: the index and the match count are read and written under this lock.
tries=0
while ! mkdir "$dir/lock" 2>/dev/null; do
  tries=$((tries + 1))
  if [ "$tries" -gt 3000 ]; then
    echo "fault injector: $dir/lock stays taken" >&2
    exit 97
  fi
  sleep 0.01
done
index=1
if [ -f "$dir/calls" ]; then index=$(( $(wc -l < "$dir/calls") + 1 )); fi
action=run
if [ -n "${FAGI_FAULT_INDEX:-}" ]; then
  if [ "$index" -eq "$FAGI_FAULT_INDEX" ]; then action=${FAGI_FAULT_ACTION:-fail}; fi
elif [ -n "${FAGI_FAULT_AT:-}" ]; then
  skip=no
  if [ -n "${FAGI_FAULT_SKIP:-}" ]; then
    set -f
    saved=$IFS
    IFS='
'
    for pattern in $FAGI_FAULT_SKIP; do
      case $words in $pattern) skip=yes ;; esac
    done
    IFS=$saved
    set +f
  fi
  hit=no
  match=${FAGI_FAULT_MATCH:-*}
  if [ "$skip" = no ]; then
    case $words in $match) hit=yes ;; esac
  fi
  if [ "$hit" = yes ]; then
    echo "$index" >> "$dir/matched"
    seen=$(( $(wc -l < "$dir/matched") + 0 ))
    times=${FAGI_FAULT_TIMES:-1}
    if [ "$seen" -ge "$FAGI_FAULT_AT" ]; then
      if [ "$times" = all ] || [ "$seen" -lt $((FAGI_FAULT_AT + times)) ]; then
        action=${FAGI_FAULT_ACTION:-fail}
      fi
    fi
  fi
fi
pgid=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ')
joined=$(printf '%s\037' "$@")
header=$(@PYTHON@ -I @OBSERVER@) || exit 95
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$index" "$name" "$action" "${pgid:-?}" "$header" "$joined" >> "$dir/calls"
rmdir "$dir/lock"

fail() {
  if [ -n "${FAGI_FAULT_STDOUT:-}" ]; then printf '%s\n' "$FAGI_FAULT_STDOUT"; fi
  printf '%s\n' "${FAGI_FAULT_STDERR:-fault injected by the bin/update e2e tests}" >&2
  exit "${FAGI_FAULT_EXIT:-1}"
}

signal_group() {
  # Only bin/update's own group: its python (this script's parent) leads it when it was spawned in a
  # session of its own. Never the protected group (the test runner's).
  if [ -z "$pgid" ] || [ "$pgid" != "$PPID" ]; then
    echo "fault injector: not sending SIG$1: process group ${pgid:-?} is not led by bin/update (pid $PPID)" >&2
    exit 98
  fi
  case ",${FAGI_FAULT_PROTECT:-}," in
    *",$pgid,"*)
      echo "fault injector: not sending SIG$1 to the protected process group $pgid" >&2
      exit 98
      ;;
  esac
  echo "$index $1 $pgid" >> "$dir/signalled"
  kill -s "$1" -- "-$pgid"
  exit 99
}

pause() {
  : > "$dir/paused-$index"
  waited=0
  while [ ! -e "$dir/release-$index" ] && [ ! -e "$dir/release-all" ]; do
    kill -0 "$PPID" 2>/dev/null || exit 143
    waited=$((waited + 1))
    if [ "$waited" -ge 9000 ]; then break; fi
    sleep 0.1
  done
  : > "$dir/resumed-$index"
}

run_real() {
  case $words in
    *"config --profiles"|*"config --format json") @PYTHON@ -I @RESULT_OBSERVER@ "$index" "$real" "$@" ;;
    *) "$real" "$@" ;;
  esac
}

case $action in
  run) run_real "$@"; exit $? ;;
  pause) pause; run_real "$@"; exit $? ;;
  fail) fail ;;
  fail-after) run_real "$@"; fail ;;
  sigkill) signal_group KILL ;;
  sigkill-after) run_real "$@"; signal_group KILL ;;
  sigterm) signal_group TERM ;;
  sigterm-after) run_real "$@"; signal_group TERM ;;
  *) echo "fault injector: unknown action $action" >&2; exit 96 ;;
esac
"""


def faults_prefix() -> str:
    prefix = os.environ.get(FAULTS_PREFIX_VAR) or DEFAULT_FAULTS_PREFIX
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*-", prefix):
        raise ValueError(f"{FAULTS_PREFIX_VAR} must be lower-case letters, digits and dashes ending in '-', "
                         f"not {prefix!r}")
    return prefix


def scope() -> str:
    value = (os.environ.get(SCOPE_VAR) or "full").strip().lower()
    if value not in ("full", "state"):
        raise ValueError(f"{SCOPE_VAR} must be full or state, not {value!r}")
    return value


# ------------------------------------------------------------------ calls --

# Compose's global options that take a value (`compose --env-file X -f A -f B config ...`).
COMPOSE_GLOBAL_VALUE_OPTIONS = frozenset(("-f", "--file", "-p", "--project-name", "--env-file", "--profile",
                                          "--project-directory", "--ansi", "--progress", "--parallel"))
_HEX_ID = re.compile(r"[0-9a-f]{64}")


def compose_words(words: Sequence[str]) -> Tuple[List[str], List[str]]:
    """(the global options of a `compose ...` call, its subcommand and arguments)."""

    index = 1
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in COMPOSE_GLOBAL_VALUE_OPTIONS else 1
    return list(words[1:index]), list(words[index:])


def classify(words: Sequence[str]) -> str:
    """What a Docker call does, in the words recorded for it (`compose ...` for either Compose)."""

    words = list(words)
    if not words:
        return "other"
    if words[0] == "compose":
        _, sub = compose_words(words)
        if not sub:
            return "compose-other"
        name, rest = sub[0], sub[1:]
        if name == "version":
            return "compose-version"
        if name == "config":
            if "--quiet" in rest or "-q" in rest:
                return "config-check"
            if "--services" in rest:
                return "config-services"
            if "--profiles" in rest:
                return "config-profiles"
            return "config"
        if name == "up":
            return "up-help" if "--help" in rest or "-h" in rest else "up"
        if name in ("pull", "logs"):
            return name
        return f"compose-{name}"
    if words[0] == "image" and words[1:2] == ["inspect"]:
        return "image-inspect"
    if words[0] in ("ps", "inspect", "run", "tag", "info", "version", "rmi"):
        return words[0]
    return "other"


def _literal(word: str) -> str:
    """`word` as a glob that matches only itself, in sh `case` and fnmatch alike."""

    if "\\" in word:
        return "*"
    return re.sub(r"([*?\[])", r"[\1]", word)


def signature(words: Sequence[str]) -> str:
    """A glob that matches this call (and the same call of another run of the same command).

    What differs from one run or checkout to the next becomes `*`: Compose's
    global options (the production env file and compose files), container
    ids, the project in a label filter, file paths, the arguments of `docker
    run` (cosign) and `docker tag`. Image references stay: the call that
    inspects the release's image is not the one that inspects a datastore's.
    """

    words = list(words)
    kind = classify(words)
    if kind in ("run", "tag", "inspect"):
        return f"{words[0]} *"
    parts: List[str] = []
    rest = words
    if words and words[0] == "compose":
        options, sub = compose_words(words)
        parts.append("compose *" if options else "compose")
        rest = sub
    elif kind == "image-inspect":
        parts.append("image inspect")
        rest = words[2:]
    for word in rest:
        if word.startswith("label=com.docker.compose.project="):
            parts.append("label=com.docker.compose.project=*")
        elif word.startswith("sha256:") or _HEX_ID.fullmatch(word):
            parts.append("sha256:*" if word.startswith("sha256:") else "*")
        elif word.startswith("/") or "/." in word:
            parts.append("*")
        else:
            parts.append(_literal(word))
    return " ".join(parts)


@dataclass(frozen=True)
class Call:
    """One call a wrapper recorded."""

    index: int
    program: str
    action: str
    pgid: str
    args: Tuple[str, ...]
    # (stage, kind, status), observed before this call runs or is faulted.
    transaction: Tuple[str, str, str] = ("unobserved", "unobserved", "unobserved")
    # Output evidence is separate from the pre-call journal observation. Old
    # traces remain readable, but missing evidence cannot authorize a fallback.
    profiles: str = "unobserved"
    service_images: Tuple[Tuple[str, str], ...] = ()

    @property
    def words(self) -> List[str]:
        return ["compose", *self.args] if self.program == "docker-compose" else list(self.args)

    @property
    def text(self) -> str:
        return " ".join(self.words)

    @property
    def kind(self) -> str:
        return classify(self.words)

    @property
    def glob(self) -> str:
        return signature(self.words)

    @property
    def ran(self) -> bool:
        """Whether the real Docker command ran (before any fault, or not faulted at all)."""

        return self.action in ("run", "pause") or self.action.endswith("-after")

    def __str__(self) -> str:
        shown = self.text if len(self.text) <= 140 else self.text[:137] + "..."
        return f"#{self.index} [{self.action}; {'/'.join(self.transaction)}] {shown}"


def parse_calls(text: str) -> List[Call]:
    calls = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t", 7)
        if len(fields) != 8 or not fields[0].isdigit():
            raise ValueError("not a recorded call with a transaction observation")
        # Each word is followed by \x1f (printf '%s\037' "$@"); no words, no separator.
        joined = fields[7]
        args = tuple(joined.split("\x1f")[:-1]) if joined else ()
        calls.append(Call(int(fields[0]), fields[1], fields[2], fields[3], args, tuple(fields[4:7])))
    return calls


def occurrence(calls: Sequence[Call], position: int, glob: Optional[str] = None) -> int:
    """How many of calls[0..position] (inclusive) match `glob` (default: calls[position]'s own)."""

    glob = glob or calls[position].glob
    return sum(1 for call in calls[:position + 1] if fnmatch.fnmatchcase(call.text, glob))


# ------------------------------------------------------------- the matrix --

@dataclass(frozen=True)
class FaultPoint:
    """One fault: `action` at the `occurrence`-th call matching `glob` (call `index` of the recording)."""

    index: int
    kind: str
    glob: str
    occurrence: int
    action: str
    error: str = ""

    @property
    def label(self) -> str:
        return f"#{self.index} {self.action} at {self.kind} ({self.glob} #{self.occurrence})"


PULL_ERRORS = ("registry-unreachable", "no-space", "image-not-found")


def error_name(kind: str, variant: int = 0) -> str:
    """The realistic failure a matrix gives this kind of call; `variant` cycles through the ways a pull fails."""

    if kind == "compose-version":
        return "no-compose"
    if kind == "pull":
        return PULL_ERRORS[variant % len(PULL_ERRORS)]
    if kind == "up":
        return "no-space"
    if kind in ("image-inspect", "tag", "run"):
        return "image-not-found"
    if kind in ("config", "config-check", "config-services", "config-profiles", "up-help"):
        return "compose-invalid-config"
    return "daemon-down"


def error_for(kind: str, words: Sequence[str] = (), name: Optional[str] = None) -> Tuple[str, str, str, int]:
    """(name, stdout, stderr, exit code) of a realistic failure of this kind of call (`name`: which one).

    The text is what Docker or Compose print for it, so bin/update reports
    it as it would a real one (a stopped daemon gets its hint, say).
    """

    name = name or error_name(kind)
    last = words[-1] if words else ""
    if name == "no-compose":
        return name, "", NO_COMPOSE, 1
    if name == "compose-invalid-config":
        return name, "", INVALID_COMPOSE_CONFIG, 1
    if name == "registry-unreachable":
        return name, "", REGISTRY_UNREACHABLE, 1
    if name == "no-space":
        if kind == "up":
            return name, "", ("Error response from daemon: failed to create task for container: failed to create "
                              "shim task: OCI runtime create failed: write /run/containerd/io.containerd.runtime.v2"
                              ".task/moby/config.json: no space left on device"), 1
        return name, "", NO_SPACE, 1
    if name == "image-not-found":
        if kind == "image-inspect":
            return name, "", f"Error response from daemon: No such image: {last}", 1
        if kind == "tag":
            return name, "", f"Error response from daemon: No such image: {words[1] if len(words) > 1 else last}", 1
        if kind == "run":
            image = next((w for w in words if "cosign" in w), last)
            return name, "", (f"Unable to find image '{image}' locally\ndocker: Error response from daemon: "
                              "manifest unknown.\nSee 'docker run --help'."), 125
        return name, "", ("Error response from daemon: manifest for localhost/futureagi/standalone:v0 not found: "
                          "manifest unknown: manifest unknown"), 1
    if name == "daemon-down":
        return name, "", DAEMON_DOWN, 1
    raise ValueError(f"unknown error {name!r}")


def actions_for(call: Call, *, extended: bool = False) -> List[str]:
    """The fault actions a matrix tries at `call`: a failure and a kill, and (extended) the variants that
    act after a state-changing call ran and a SIGTERM."""

    actions = ["fail", "sigkill"]
    if extended and call.kind in STATE_CHANGING:
        actions += ["fail-after", "sigkill-after", "sigterm", "sigterm-after"]
    return actions


def points_at(calls: Sequence[Call], positions: Iterable[int], actions: Iterable[str]) -> List[FaultPoint]:
    """A FaultPoint per (position in `calls`, action)."""

    points = []
    wanted = list(actions)
    pull_variant = 0
    for position in sorted(set(positions)):
        call = calls[position]
        for action in wanted:
            error = ""
            if action.startswith("fail"):
                error = error_name(call.kind, pull_variant)
            points.append(FaultPoint(call.index, call.kind, call.glob, occurrence(calls, position), action, error))
        if call.kind == "pull":
            pull_variant += 1
    return points


def restart_position(calls: Sequence[Call]) -> Optional[int]:
    """Where the restart (`compose up`, not `up --help`) is in `calls`, if anywhere."""

    return next((i for i, call in enumerate(calls) if call.kind == "up"), None)


def journal_position(calls: Sequence[Call]) -> Optional[int]:
    """The first call after the env file was written: `compose config --quiet` (apply.Updater._check_config)."""

    return next((i for i, call in enumerate(calls) if call.kind == "config-check"), None)


def full_matrix(calls: Sequence[Call], actions: Iterable[str] = ("fail", "sigkill"), *,
                before_restart: Optional[bool] = None) -> List[FaultPoint]:
    """Every call of a recorded sequence × `actions`; before_restart=True/False keeps one side of the restart."""

    restart = restart_position(calls)
    positions = range(len(calls))
    if before_restart is not None and restart is not None:
        positions = [i for i in positions if (i < restart) == before_restart]
    elif before_restart is False:
        positions = []
    return points_at(calls, positions, actions)


def state_positions(calls: Sequence[Call]) -> List[int]:
    """The calls that change state, their neighbours, the calls around the env file write, and the last call."""

    chosen = set()
    for i, call in enumerate(calls):
        if call.kind in STATE_CHANGING:
            chosen.update((i - 1, i, i + 1))
    env_write = journal_position(calls)
    if env_write is not None:
        chosen.update((env_write - 1, env_write, env_write + 1))
    restart = restart_position(calls)
    if restart is not None:
        # The health checks after the restart: the first readiness or stale-image check.
        chosen.update(range(restart + 1, min(restart + 3, len(calls))))
    chosen.add(len(calls) - 1)
    return sorted(i for i in chosen if 0 <= i < len(calls))


def state_matrix(calls: Sequence[Call], actions: Iterable[str] = ("fail", "sigkill")) -> List[FaultPoint]:
    return points_at(calls, state_positions(calls), actions)


def validate_transactions(calls: Sequence[Call], kind: str) -> None:
    """Check observed headers against independent Docker-command landmarks.

    Config-check follows the accepted env exchange; pulls/tags prepare images
    (they do NOT replace application containers). The up-help capability probe
    is the last preparation call. Its following ownership reads, and every up,
    must already see the published rollout marker. A missing, late, premature,
    regressing or foreign marker fails even if the updater's exit agrees with it.
    The wrapper observes publication, not physical power-loss durability.
    """

    prepared = rollout = False
    previous = "absent"
    ranks = {"absent": 0, "env_written": 1, "pulled": 2, "rollout": 3}
    for call in calls:
        if call.kind == "config-check":
            prepared = True
        if rollout:
            allowed = ("rollout",)
        elif call.kind in ("config-check", "pull", "tag"):
            allowed = ("env_written",)
        elif call.kind == "up-help":
            allowed = ("pulled",)
        elif prepared:
            allowed = ("env_written", "pulled")
        else:
            allowed = ("absent",)
        stage, owner, status = call.transaction
        if stage not in allowed or ranks.get(stage, -1) < ranks[previous]:
            raise AssertionError(f"call #{call.index} {call.kind}: expected stage {allowed}, observed {stage}")
        expected = ("absent", "absent") if stage == "absent" else (kind, "running")
        if (owner, status) != expected:
            raise AssertionError(f"call #{call.index}: wrong journal kind/status {(owner, status)}, expected {expected}")
        words = call.words
        command = compose_words(words)[1] if words[:1] == ["compose"] else words
        mutates_containers = bool(command and command[0] in (
            "up", "start", "stop", "restart", "create", "rm", "kill", "pause", "unpause", "exec", "down", "run"))
        if call.kind == "up-help":
            mutates_containers = False
        # Signature verification's read-only, disposable cosign container is
        # not a mutation of the installation. Do not exempt arbitrary `run`s.
        if (call.kind == "run" and "--rm" in words and "--read-only" in words
                and any("cosign" in word for word in words)):
            mutates_containers = False
        if mutates_containers and stage != "rollout":
            raise AssertionError(f"call #{call.index}: container mutation without rollout publication")
        previous = stage
        if call.kind == "up-help":
            rollout = True


def phase_of(calls: Sequence[Call]) -> Tuple[str, Optional[Call]]:
    """Phase at the injection, observed BEFORE Docker or the injected action.

    Call validate_transactions too: a self-consistent but misplaced journal
    must not redefine the expected exit or permit an unsafe automatic inverse.
    """

    faulted = next((c for c in calls if c.action not in ("run", "pause")), None)
    if faulted is None:
        return "none", None
    stage = faulted.transaction[0]
    phases = {"absent": "plan", "env_written": "prepare", "pulled": "prepare", "rollout": "rollout"}
    if stage not in phases:
        raise AssertionError(f"call #{faulted.index}: missing/invalid transaction observation ({stage})")
    return phases[stage], faulted


def validate_survived_failures(calls: Sequence[Call], recorded: Sequence[Call] = ()) -> None:
    """Exit zero is allowed only for a known optional probe or an observed fallback.

    These are call-site rules, not a whitelist of Docker verbs. In particular,
    config/config-services/ps/inspect in an ownership check are mandatory, even
    before `up` runs. The current updater does not retry those failures. A later
    readiness poll, matching env or success receipt cannot replace that proof.
    `run` records invocation, not Docker's return code, so do not infer a new
    ownership retry policy from a repeated call in a fabricated trace.
    """

    def clean(call: Call) -> bool:
        return call.action in ("run", "pause")

    def owned_pull(window: Sequence[Call], transaction: Tuple[str, str, str]) -> bool:
        # compose_run("pull") has its own fresh ownership check. A bare pull,
        # an earlier pull, or one across the pulled/rollout boundary is not repair.
        if ([c.kind for c in window] != ["config", "config-services", "ps", "inspect", "pull"]
                or not all(clean(c) and c.transaction == transaction and c.pgid == window[0].pgid for c in window)):
            return False
        options, command = compose_words(window[0].words)
        return (command == ["config", "--format", "json"]
                and compose_words(window[1].words) == (options, ["config", "--services"])
                and compose_words(window[4].words)[0] == options
                and window[0].program == window[1].program == window[4].program)

    def third_party(call: Call) -> bool:
        # Independent unfaulted trace: never reclassify a release target just
        # because a fabricated faulted trace omitted its digest verification.
        return (call.kind == "image-inspect" and len(call.words) == 3
                and any(c.words == call.words and c.transaction == call.transaction for c in recorded)
                and not any(c.words == call.words and c.transaction[0] == "pulled" for c in recorded))

    def batch_repaired(position: int) -> bool:
        call = calls[position]
        start, end = position, position + 1
        while start and calls[start - 1].transaction == call.transaction and third_party(calls[start - 1]):
            start -= 1
        while end < len(calls) and calls[end].transaction == call.transaction and third_party(calls[end]):
            end += 1
        batch, window = calls[start:end], calls[end:end + 5]
        if (not owned_pull(window, call.transaction)
                or any(c.pgid != call.pgid for c in [*batch, *window])):
            return False
        # _pull_missing iterates sorted non-buildable services, then issues ONE
        # pull for the missing service names. Preserve that ordering, including
        # two services using the same image; merely finding a later pull is not
        # evidence that it repairs this particular miss.
        model = next((c.service_images for c in reversed(calls[:start])
                      if c.kind == "config" and clean(c) and c.transaction == call.transaction), ())
        if not model or window[0].service_images != model:
            return False
        def digest(value):
            return hashlib.sha256(value.encode("utf-8")).hexdigest()
        references = {digest(c.words[2]) for c in recorded if c.transaction == call.transaction and third_party(c)}
        services = [(name, image) for name, image in model if image in references]
        if [image for _, image in services] != [digest(c.words[2]) for c in batch]:
            return False
        targets = compose_words(window[-1].words)[1][1:]
        pulled = {digest(name) for name in targets}
        missing = {name for (name, _), c in zip(services, batch) if not clean(c)}
        if (not missing <= pulled <= {name for name, _ in services}
                or len(pulled) != len(targets)):
            return False
        # Third-party probes have no digest verification of their own. That
        # exemption must not erase any RELEASE image's mandatory verification.
        return all(any(c.words == required.words and clean(c) and c.pgid == call.pgid
                       and c.transaction == ("pulled", *call.transaction[1:]) for c in calls[end + 5:])
                   for required in recorded if required.kind == "image-inspect"
                   and required.transaction == ("pulled", *call.transaction[1:]))

    for position, call in enumerate(calls):
        if clean(call):
            continue
        before, after = calls[:position], calls[position + 1:]
        stage = call.transaction[0]
        allowed = False
        if call.action in ("fail", "fail-after"):
            if call.kind == "compose-version" and stage == "absent":
                # resolve_compose_command: plugin failure -> standalone v2 probe.
                allowed = (call.program == "docker" and call.words == ["compose", "version", "--short"]
                           and bool(after) and after[0].program == "docker-compose"
                           and after[0].args == ("version", "--short") and clean(after[0])
                           and after[0].transaction == call.transaction
                           and any(c.program == "docker-compose" and c.kind == "config" and clean(c) for c in after[1:]))
            elif call.kind == "up-help" and stage == "pulled":
                # supports_up_pull_never treats unreadable help as no capability.
                allowed = any(c.kind == "up" and clean(c) and c.transaction[0] == "rollout" for c in after)
            elif call.kind == "logs" and stage == "rollout":
                # Readiness._migrations_applied is optional; container reads are not.
                allowed = (compose_words(call.words)[1] == ["logs", "--no-color", "--tail", "2000", "app"]
                           and any(c.kind == "up" and clean(c) for c in before)
                           and any(c.kind == "inspect" and clean(c) for c in after))
            elif call.kind in ("config-profiles", "config") and stage == "absent":
                # Empty/invalid/unknown profile discovery goes directly to a
                # STRICT model. Only an observed nonempty VALID result proves
                # that _probe_services tries the non-strict all-profiles model.
                # An invented config retry cannot supply that missing fact.
                options, command = compose_words(call.words)
                fallback = bool(after) and clean(after[0]) and after[0].program == call.program \
                    and after[0].transaction == call.transaction and after[0].pgid == call.pgid \
                    and compose_words(after[0].words) == (options, ["config", "--format", "json"])
                allowed = fallback and (call.kind == "config-profiles" or (
                    command == ["config", "--format", "json"] and bool(before) and clean(before[-1])
                    and before[-1].profiles == "nonempty" and before[-1].program == call.program
                    and before[-1].pgid == call.pgid and before[-1].transaction == call.transaction
                    and compose_words(before[-1].words) == (options, ["config", "--profiles"])))
            elif call.kind == "image-inspect" and len(call.words) == 3:
                reference = call.words[2]
                verified = any(c.words == call.words and clean(c) and c.transaction[0] == "pulled" for c in after)
                if stage == "absent":
                    # Running-image labels and rollback snapshot metadata are
                    # best effort; tagged target probes must later be verified.
                    metadata = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", reference)) \
                        and any(c.kind == "inspect" and clean(c) for c in before)
                    allowed = metadata or verified
                elif stage == "env_written":
                    # _transact's cache miss: pull then verify the target, or
                    # adopt its prior image by an explicitly matching tag.
                    pulled = any(owned_pull(after[i:i + 5], call.transaction) for i in range(len(after)))
                    tagged = any(c.words[:1] == ["tag"] and len(c.words) == 3 and c.words[-1] == reference
                                 and clean(c) and c.transaction == call.transaction for c in after)
                    # Other third-party probes may precede the single batch
                    # pull, but no other work or transaction boundary may.
                    allowed = (verified and (pulled or tagged)) or (third_party(call) and batch_repaired(position))
                # A pulled-stage image inspect is mandatory digest verification.
        if not allowed:
            raise AssertionError(f"call #{call.index} {call.kind} ({call.action}, {stage}): "
                                 "success after a mandatory failure or without an observed fallback")


def restarted(calls: Sequence[Call]) -> bool:
    """Whether Compose's `up` really ran in this run."""

    return any(c.kind == "up" and c.ran for c in calls)


def prefix_matches(recorded: Sequence[Call], run: Sequence[Call]) -> Optional[str]:
    """None when `run`'s calls up to its faulted one are the recording's (by glob, in order); else what differs."""

    _, faulted = phase_of(run)
    upto = [c for c in run if faulted is None or c.index <= faulted.index]
    for position, call in enumerate(upto):
        if position >= len(recorded):
            return f"call {call} is past the end of the recording ({len(recorded)} calls)"
        if not fnmatch.fnmatchcase(call.text, recorded[position].glob):
            return f"call {position + 1} is `{call.text}`; the recording has `{recorded[position].text}`"
    return None


# ----------------------------------------------------------------- status --

_SERVICE_RE = re.compile(r"^  (\S+)\s+(\S+)\s+(?:running (\S+)  )?\(([A-Z0-9_]+)=(.*)\)$")
_KEY_RE = re.compile(r"^  ([A-Z0-9_]+)=(.*)$")


@dataclass
class Status:
    installation: str
    services: Dict[str, Tuple[str, Optional[str], str, str]]  # service -> (image, running, variable, value)
    keys: Dict[str, str]  # Docker down: variable -> value
    in_progress: Optional[str]
    interrupted: Optional[str]
    journal: Optional[str]
    last_run: Optional[str]
    unknown: Optional[str]  # "running: unknown (...)" when Docker cannot say


def parse_status(stdout: str) -> Status:
    """What `bin/update status` printed, line by line (deploy/update/__main__.py _status)."""

    status = Status("", {}, {}, None, None, None, None, None)
    for line in stdout.splitlines():
        if line.startswith("Installation  "):
            status.installation = line[len("Installation  "):]
        elif line.startswith("In progress   "):
            status.in_progress = line[len("In progress   "):]
        elif line.startswith("Interrupted   "):
            status.interrupted = line[len("Interrupted   "):]
        elif line.startswith("Journal       "):
            status.journal = line[len("Journal       "):]
        elif line.startswith("Last run      "):
            status.last_run = line[len("Last run      "):]
        elif line.startswith("  running: unknown"):
            status.unknown = line.strip()
        else:
            match = _SERVICE_RE.match(line)
            if match:
                service, image, running, variable, value = match.groups()
                status.services[service] = (image, running, variable, value)
                continue
            match = _KEY_RE.match(line)
            if match:
                status.keys[match.group(1)] = match.group(2)
    return status


# ---------------------------------------------------------------- wrapper --

class FaultDocker:
    """The docker / docker-compose wrappers of one test (see the module docstring)."""

    def __init__(self, directory: Path, *, docker: Optional[str] = None, compose: Optional[str] = None,
                 shell: str = "/bin/sh", stable_environment: bool = False) -> None:
        self.stable_environment = stable_environment
        self.directory = directory
        self.shell = shell  # the wrappers' interpreter (Ubuntu's /bin/sh is dash, macOS's bash)
        self.bin = directory / "bin"
        self.state = directory / "state"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        path = e2e.search_path()
        real_docker = docker or shutil.which("docker", path=path)
        real_compose = compose or shutil.which("docker-compose", path=path)
        if not real_docker:
            raise RuntimeError("docker is not on PATH")
        self._write("docker", real_docker)
        if real_compose:
            self._write("docker-compose", real_compose)

    def _write(self, name: str, real: str) -> None:
        if Path(real).resolve().parent == self.bin.resolve():
            raise RuntimeError(f"the real {name} would be the wrapper itself ({real})")
        text = WRAPPER.replace("#!/bin/sh\n", f"#!{self.shell}\n", 1)
        text = text.replace("@REAL@", shlex.quote(real)).replace("@NAME@", name)
        observer = self.bin / "observe.py"
        observer.write_text(OBSERVER, encoding="utf-8")
        result_observer = self.bin / "observe_result.py"
        result_observer.write_text(RESULT_OBSERVER, encoding="utf-8")
        text = text.replace("@PYTHON@", shlex.quote(sys.executable)).replace("@OBSERVER@", shlex.quote(str(observer)))
        text = text.replace("@RESULT_OBSERVER@", shlex.quote(str(result_observer)))
        path = self.bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    # ------------------------------------------------------------ runs --
    def reset(self) -> None:
        """Forget the previous run's calls, matches, pauses and signals."""

        for child in self.state.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()

    def env(self, *, at: Optional[int] = None, index: Optional[int] = None, match: str = "*",
            action: str = "fail", times: str = "1", stdout: str = "", stderr: str = "", exit_code: int = 1,
            skip: Iterable[str] = (), path: Optional[str] = None) -> Dict[str, str]:
        """The environment of a bin/update run that records its calls, and faults by absolute `index` or the
        `at`-th matching `match` glob (with `times` matching calls).

        Exactly one of `index` or `at` chooses the fault; neither records calls
        without acting. It resets the record first: one FaultDocker records
        one run at a time. With stable_environment=True the returned environment
        is identical for every selection; the private settings file carries the
        controls instead. Use that same environment for stage creation too, so
        a recorded finish tests faults rather than a changed stage identity.
        """

        if action not in ACTIONS:
            raise ValueError(f"unknown fault action {action!r}")
        if at is not None and index is not None:
            raise ValueError("choose at or index, not both")
        if at is not None and (not isinstance(at, int) or at < 1):
            raise ValueError("at must be a positive matching-call number")
        if index is not None and (not isinstance(index, int) or index < 1):
            raise ValueError("index must be a positive absolute call number")
        self.reset()
        env = {"PATH": f"{self.bin}{os.pathsep}{path if path is not None else e2e.search_path()}",
               "FAGI_FAULT_DIR": str(self.state), "FAGI_FAULT_PROTECT": str(os.getpgrp())}
        if index is not None:
            env.update(FAGI_FAULT_INDEX=str(index), FAGI_FAULT_ACTION=action, FAGI_FAULT_EXIT=str(exit_code))
        elif at is not None:
            env.update(FAGI_FAULT_AT=str(at), FAGI_FAULT_MATCH=match, FAGI_FAULT_ACTION=action,
                       FAGI_FAULT_TIMES=str(times), FAGI_FAULT_EXIT=str(exit_code))
            skip = list(skip)
            if skip:
                env["FAGI_FAULT_SKIP"] = "\n".join(skip)
        if at is not None or index is not None:
            if stdout:
                env["FAGI_FAULT_STDOUT"] = stdout
            if stderr:
                env["FAGI_FAULT_STDERR"] = stderr
        if self.stable_environment:
            controls = {key: value for key, value in env.items()
                        if key not in ('PATH', 'FAGI_FAULT_DIR', 'FAGI_FAULT_PROTECT')}
            settings = self.state / 'settings'
            settings.write_text(''.join(f'{key}={shlex.quote(value)}\n' for key, value in controls.items()),
                                encoding='utf-8')
            settings.chmod(0o600)
            env = {key: value for key, value in env.items() if key not in controls}
        return env

    def point_env(self, point: FaultPoint, words: Sequence[str] = ()) -> Dict[str, str]:
        """env() for a FaultPoint, with the realistic error of its kind of call (`words`: that call's)."""

        stdout = stderr = ""
        code = 1
        if point.action.startswith("fail"):
            _, stdout, stderr, code = error_for(point.kind, words, point.error or None)
        return self.env(at=point.occurrence, match=point.glob, action=point.action, stdout=stdout, stderr=stderr,
                        exit_code=code)

    def calls(self) -> List[Call]:
        path = self.state / "calls"
        calls = parse_calls(path.read_text(encoding="utf-8")) if path.exists() else []
        observed = []
        for call in calls:
            evidence = self.state / f"{call.index}.evidence.json"
            if evidence.exists():
                data = json.loads(evidence.read_text(encoding="utf-8"))
                call = replace(call, profiles=data["profiles"],
                               service_images=tuple(tuple(pair) for pair in data["service_images"]))
            observed.append(call)
        return observed

    def signalled(self) -> List[str]:
        path = self.state / "signalled"
        return path.read_text(encoding="utf-8").split("\n")[:-1] if path.exists() else []

    # ---------------------------------------------------------- pauses --
    def paused(self) -> List[int]:
        """Indexes of the calls that paused so far (resumed ones included)."""

        return sorted(int(p.name.split("-", 1)[1]) for p in self.state.glob("paused-*"))

    def resumed(self, index: int) -> bool:
        return (self.state / f"resumed-{index}").exists()

    def release(self, index: int) -> None:
        (self.state / f"release-{index}").touch()

    def release_all(self) -> None:
        if self.state.is_dir():
            (self.state / "release-all").touch()


def skip_without_daemon() -> List[str]:
    """FAGI_FAULT_SKIP globs of the calls a stopped Docker daemon does not stop: Compose's own work."""

    return ["compose version*", "compose config*", "compose * config*", "compose up --help", "compose * up --help"]
