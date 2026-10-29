"""Apply an update, roll one back, and recover from an interrupted run.

Both apply and rollback change nothing but the image version keys of the env
file, then run Compose the way the install does. The sequence is:

  journal (fsync) -> env file -> resolved config check -> pull ->
  digest check -> restart (up --pull never) -> readiness -> running-image check

Until the restart starts, a failure or interrupt puts the version keys back
as this run found them: nothing was changed on the running stack. A stage
only counts once its journal is on disk, so `up` never runs before the
journal says so. Once containers are being replaced, the new keys stay (they
match what is running) and the receipt names the exact apply (finish) or
rollback (go back) command. A run killed outright leaves its journal; the
next run recovers from it.

`apply --stage` stops after the digest check: the env file selects the
release and its images are on the host, verified, but nothing restarted (a
production install runs the release's initialization jobs then). The next
plain `apply` of that release finishes it like a resume. Rolling back a
staged update the stack never started puts the version keys back and
verifies the previous images the same way, without restarting anything.

Only images that are not on the host with the expected digest yet are
pulled (the release's signed digest for apply, the digest or image that ran
before the update for rollback); every image is verified either way. So
finishing a staged or stopped update, and a rollback whose images are still
on the host, need no registry: an image that ran under another tag (an
install that followed :latest) is tagged with the version the rollback
restores instead of being pulled.

A rollback starts the previous release's images, which need that release's
deployment files (compose files, bind-mounted configuration): in a git
checkout it compares them with the release's tag and refuses to restart the
stack while they differ, naming the `git checkout` that restores them.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import (Any, Callable, Collection, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, TextIO,
                    Tuple)

from . import envfile
from .contracts import AutomationGuard, GuardBoundary, LockedContext, ReasonCode
from .install import COMPONENT_VARIABLES, PRODUCTION_ENV, Installation, component_for
from .manifest import (DEPLOYMENT_FILE_LIST, ManifestError, _skipped_in_directory_hash, component_tag, hash_path,
                       is_version, parse_image_ref, parse_version, read_deployment_file_list)
from .plan import (BLOCKED, UP_TO_DATE, _manifest_of, cd_prefix, classify_strays, command_prefix, install_command,
                   jobs_text, on_release, plan_text, selected_keys, stray_states, unfinished)
from .readiness import NotReady, Readiness, standalone_settings
from .receipt import IDENTITY_FIELDS, StateDir, StateError, scrub
from .settings import Settings

STAGES_BEFORE_ROLLOUT = ("prepare", "env_written", "pulled")
RESUMABLE_STATUSES = ("failed_during_rollout", "interrupted_during_rollout")
# An apply --stage that completed: the env file selects the release, its images are verified, nothing restarted.
STAGED = "staged"
ROLLBACKABLE = ("succeeded",) + RESUMABLE_STATUSES + (STAGED,)
# The receipt of a run that finished: any other status records a run that stopped (or was recovered).
FINISHED_STATUSES = ("succeeded", STAGED, "rolled_back")
JOURNAL_NOTE = "an interrupted bin/update run will be recovered by the next apply or rollback"
BACKUP_SETTING = "FUTURE_AGI_UPDATE_BACKUP_COMMAND"
# How long a stopped backup hook's process group gets between SIGTERM and SIGKILL.
HOOK_STOP_GRACE_SECONDS = 5.0
# How long Popen gets to collect the stopped hook before it is reaped directly (see _reap).
HOOK_REAP_SECONDS = 2.0
# Container states that run code (a stopped container of a service outside the profiles runs nothing).
_RUNNING = ("running", "restarting")
# Environment variables that point git at another repository than the checkout it runs in (`git -C`).
_GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                           "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_PREFIX")
GIT_TIMEOUT_SECONDS = 120
# A release's compose files, read at its tag: the paths they bind-mount or name as env files are its deployment
# files too, whether or not its deployment-files.txt lists them (a release from before the list has none).
RELEASE_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.distributed.yml", "deploy/docker-compose.production.yml")
# ${VAR:-default} and ${VAR-default}: Compose reads the default with the variable unset.
_COMPOSE_DEFAULT_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}")


class UpdateError(RuntimeError):
    exit_code = 2


class Blocked(UpdateError):
    exit_code = 3


class FailedBeforeRollout(UpdateError):
    exit_code = 4


class FailedDuringRollout(UpdateError):
    exit_code = 5


class DependencyImageChanged(UpdateError):
    """An automatic rollout left a dependency container on an image nobody certified."""
    reason_code = ReasonCode.IMAGE_DIGEST_MISMATCH


class Interrupted(BaseException):
    """SIGTERM or SIGHUP arrived; handled like Ctrl-C."""


class InterruptedRun(UpdateError):
    exit_code = 130


@contextmanager
def signals_as_interrupt() -> Iterator[None]:
    """SIGTERM and SIGHUP raise Interrupted inside the block, like Ctrl-C.

    A signal this process inherited as ignored stays ignored: `nohup
    ./bin/update apply &` must survive the SSH session closing, and so must
    the `docker compose` it runs (an ignored signal stays ignored across exec,
    a handler does not). Only what was changed is restored afterwards.
    """

    def handler(signum, frame):  # noqa: ARG001
        raise Interrupted(signal.Signals(signum).name)

    previous = {}
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)):
        if sig is None or signal.getsignal(sig) is signal.SIG_IGN:
            continue
        previous[sig] = signal.signal(sig, handler)
    try:
        yield
    finally:
        for sig, old in previous.items():
            # None: a handler not installed from Python, which cannot be put back; the default is closest.
            signal.signal(sig, signal.SIG_DFL if old is None else old)


def _reap(process: "subprocess.Popen[Any]") -> None:
    """Collect the stopped hook's exit status.

    Never with a bare process.wait(): an interrupt that arrived inside the
    interrupted wait can leave Popen's internal lock taken, and a bare wait
    would then block forever (from Python 3.15 even a wait with a timeout
    takes that lock without one, once the child has exited). A taken lock,
    or a bounded wait that runs out, means the child is reaped directly.
    """

    lock = getattr(process, "_waitpid_lock", None)
    if lock is not None:
        if not lock.acquire(False):
            _waitpid(process)
            return
        lock.release()
    try:
        process.wait(timeout=HOOK_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        _waitpid(process)


def _waitpid(process: "subprocess.Popen[Any]") -> None:
    """Reap the (killed) hook without Popen, and record its exit status there."""

    try:
        _, status = os.waitpid(process.pid, 0)
    except ChildProcessError:  # Popen collected it after all
        return
    try:
        process.returncode = os.waitstatus_to_exitcode(status)
    except (AttributeError, TypeError, ValueError):
        process.returncode = -1


def _stop_process_group(process: "subprocess.Popen[Any]", grace: float) -> None:
    """SIGTERM the hook's whole process group, then SIGKILL whatever is left after `grace` seconds."""

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:  # the group is gone already
        _reap(process)
        return
    deadline = time.monotonic() + grace
    gone = False
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                try:
                    os.killpg(process.pid, 0)
                except OSError:
                    gone = True
                    break
            time.sleep(0.05)
    finally:
        if not gone:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            _reap(process)


def run_hook(argv: Sequence[str], *, cwd: str, env: Mapping[str, str], check: bool = False,
             timeout: Optional[float] = None) -> "subprocess.CompletedProcess[Any]":
    """Run the backup hook like subprocess.run, in its own session and process group.

    A Ctrl-C at the terminal then reaches only bin/update. On a timeout or an
    interrupt the whole group (the hook and everything it started) is
    stopped before the error propagates, so no hook process outlives
    bin/update.
    """

    process = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), start_new_session=True)
    try:
        returncode = process.wait(timeout=timeout)
    except BaseException:  # TimeoutExpired, KeyboardInterrupt, Interrupted
        _stop_process_group(process, HOOK_STOP_GRACE_SECONDS)
        raise
    if check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, list(argv))
    return subprocess.CompletedProcess(list(argv), returncode)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())








def _digest_set(entries: List[str]) -> set:
    """RepoDigests entries as (normalized name, digest) pairs."""

    pairs = set()
    for entry in entries:
        try:
            ref = parse_image_ref(entry)
        except Exception:
            continue
        if ref.digest:
            pairs.add((ref.name, ref.digest))
    return pairs


def _is_release_tag(value: str) -> bool:
    """vX.Y.Z or vX.Y.Z-gpu: a tag that names exactly one published image."""

    return is_version(value) or (value.endswith("-gpu") and is_version(value[:-4]))


def _quote(path: Any) -> str:
    """A path as a shell word, for the commands bin/update prints."""

    return shlex.quote(str(path))


def _key(path: Any) -> str:
    """A receipt path in the one form receipts are compared in."""

    return str(Path(str(path)).resolve())


def _assignments(values: Mapping[str, Optional[str]]) -> str:
    return ", ".join(f"{key}={'unset' if value is None else value}" for key, value in sorted(values.items()))


def _describe_value(key: str, value: Optional[str], selects: Optional[str]) -> str:
    text = f"{key} unset" if value is None else f"{key}={value}"
    return text if selects in (None, value) else f"{text} (selects :{selects})"


def _release_of(tag: str) -> str:
    """The release a version tag names: vX.Y.Z for vX.Y.Z-gpu."""

    return tag[:-4] if tag.endswith("-gpu") and is_version(tag[:-4]) else tag


def _words(paths: Iterable[str]) -> str:
    """Paths as shell words, for a printed command."""

    return " ".join(shlex.quote(path) for path in paths)


def _run_id() -> str:
    """What names one bin/update run in its journal and its receipt (a recovery tells them apart by it)."""

    return uuid.uuid4().hex


def _extract_tar(data: bytes, into: Path) -> None:
    """The regular files and directories of a tar archive (`git archive`), below `into`.

    Links and anything naming a path outside `into` are left out: a
    release's deployment files are regular files, and nothing is ever
    written through a link.
    """

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not parts or member.name.startswith("/") or ".." in parts:
                continue
            target = into.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isreg():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                target.write_bytes(source.read() if source is not None else b"")


def _compose_path(value: str, *, bind: bool) -> Optional[str]:
    """One volume source or env_file entry as a path relative to the checkout (None: not one).

    `${VAR:-default}` resolves to its default. A bind-mount source must start
    with ./ or ../ (anything else names a volume, or an absolute path); an env
    file may be a bare relative path. A path that still names a variable, is
    absolute or leaves the checkout, and the checkout itself, are not one.
    """

    text = _COMPOSE_DEFAULT_RE.sub(r"\1", value.strip().strip("\"'"))
    if bind:
        text = text.split(":", 1)[0]
        if not text.startswith(("./", "../")):
            return None
    # A variable left, an absolute path, or YAML that is no path (an alias, a tag, a mapping).
    if not text or "$" in text or text.startswith(("/", "*", "&", "!", "{", "[")):
        return None
    path = posixpath.normpath(text)
    if path in (".", "..") or path.startswith("../"):
        return None
    return path


def compose_paths(text: str) -> List[str]:
    """The checkout paths a compose file names: its relative bind-mount sources and env_file paths.

    Read line by line (the standard library has no YAML parser): the items
    of every `volumes:` and `env_file:` key, in the short syntax (`- ./a:/b`,
    `- .env`, `env_file: .env`, `env_file: [a, b]`) and the long one
    (`source: ./a`, `path: .env`). Relative paths of every compose file of a
    release are relative to the checkout (Compose resolves them from the
    first file's directory). In the order found, each once.
    """

    found: List[str] = []

    def add(value: str, bind: bool) -> None:
        path = _compose_path(value, bind=bind)
        if path is not None and path not in found:
            found.append(path)

    block: Optional[Tuple[str, int]] = None  # the volumes: or env_file: key whose items are being read, its indent
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        # The block ends at the next line indented no deeper than its key, except a sequence item at the key's
        # own indent (YAML allows `volumes:` followed by `- ./a:/b` at the same indent).
        if block is not None and (indent < block[1] or (indent == block[1] and not stripped.startswith("- "))):
            block = None
        match = re.match(r"^(\s*)(volumes|env_file):(?:\s+(.*?))?\s*$", line)
        if match:
            key, value = match.group(2), (match.group(3) or "").split(" #", 1)[0].strip()
            if value.startswith("&"):  # an anchor: the items follow as usual
                value = value.split(None, 1)[1] if " " in value else ""
            if not value or value.startswith("#"):
                block = (key, indent)
            elif value.startswith("["):  # a flow sequence: env_file: [a, b]
                for item in value.strip("[]").split(","):
                    add(item, key == "volumes")
            elif key == "env_file":
                add(value, False)
            continue
        if block is None:
            continue
        bind = block[0] == "volumes"
        item = stripped[2:].strip() if stripped.startswith("- ") else stripped
        long_key = "source:" if bind else "path:"
        if item.startswith(long_key):
            add(item[len(long_key):].split(" #", 1)[0], bind)
        elif stripped.startswith("- ") and not re.match(r"^[A-Za-z_]+:(\s|$)", item):
            add(item.split(" #", 1)[0], bind)
    return found


def recover_elsewhere(theirs: str, kind: Optional[str], then: str = "") -> str:
    """How to recover the journal another install's run left (`theirs`: that install's bin/update prefix).

    The command that only recovers it comes first, then `then` (what to do
    once it is recovered), if anything. What that install's apply would do
    after recovering it depends on whether the run was an apply or a rollback.
    """

    if kind == "rollback":
        apply_too = ("would recover it too, but would then plan an update of that install, which can undo the "
                     "rollback")
    else:
        apply_too = "would recover it too, but would also continue that install's update"
    return (f"run `{theirs} rollback` (without --yes it only recovers that run and changes nothing else)"
            + (f", then {then}" if then else "")
            + f"; `{theirs} apply` {apply_too}")


def finished_run(journal: Mapping[str, Any], history: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """The receipt `journal`'s run already wrote, if it did (it ended before removing its journal).

    That run's receipt is the install's newest one (`history`, oldest first:
    runs hold the lock, and every run recovers a journal first): the same
    kind, start and target, written at the stage its last journal recorded,
    and the same run id where both record one.
    """

    if not history or not journal.get("started_at"):
        return None
    newest = history[-1]
    if any(newest.get(field) != journal.get(field) for field in ("kind", "started_at", "target_version", "stage")):
        return None
    if journal.get("run_id") and newest.get("run_id") and journal["run_id"] != newest["run_id"]:
        return None
    return dict(newest)


def validate_journal_header(journal: Mapping[str, Any], journal_path: Any = 'journal') -> None:
    """Validate before interpreting stages, identity, or a matching receipt."""
    if (journal.get('stage') not in (*STAGES_BEFORE_ROLLOUT, 'rollout')
            or journal.get('kind') not in ('apply', 'rollback')
            or ('journal_version' in journal and (type(journal['journal_version']) is not int
                                                  or journal['journal_version'] not in (1, 2)))
            or ('production' in journal and journal['production'] is not None
                and type(journal['production']) is not bool)
            or any(journal.get(key) is not None and (not isinstance(journal[key], str) or not journal[key])
                   for key in ('project', 'mode', 'env_file'))):
        raise StateError(f"cannot recover {journal_path}: invalid or unknown run kind/stage or journal identity/version; "
                         "the journal was kept. Inspect it and recover the installation manually")


def check_journal_owner(installation: Installation, journal: Mapping[str, Any], *,
                        journal_path: Any = 'journal', again: str = '',
                        identity: Optional[Mapping[str, Any]] = None) -> None:
    """One identity comparison for recovery and read-only diagnostics (legacy fields may be absent)."""
    identity = installation.identity() if identity is None else identity
    env_file, production = journal.get('env_file'), journal.get('production')
    if ((env_file is None or env_file == identity['env_file'])
            and (production is None or production == identity['production'])):
        for field in ('project', 'mode'):
            current = identity.get(field)
            if current is not None and journal.get(field) is not None and journal[field] != current:
                raise Blocked(f"cannot recover {journal_path}: its {field} is {journal[field]!r}, "
                              f"not this installation's {current!r}. Restore the original project selection "
                              "and installation flags before retrying; the journal was kept")
        return
    env_name = identity['env_file'] if env_file is None else env_file
    production = identity['production'] if production is None else production
    theirs = cd_prefix(installation) + command_prefix(production, env_name)
    kind = journal.get('kind') or 'apply'
    raise Blocked(f"an interrupted bin/update {kind} for {env_name} must be recovered before this install can be "
                  f"updated or rolled back: {recover_elsewhere(theirs, kind, f'`{again}` again' if again else '')}")


def validate_recovery(installation: Installation, journal: Mapping[str, Any],
                      history: Sequence[Mapping[str, Any]] = (), *, journal_path: Any = 'journal',
                      again: str = '', durable: bool = False, check_provenance: bool = False,
                      identity: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Shared recoverability gate; read-only unless real recovery requests durable inspection.

    A preparing inverse and an armed inverse both inspect as 'restoring', but
    only the latter can replay. Validate that distinction before trusting a
    completed receipt or making any recovery/initialization promise.
    """
    validate_journal_header(journal, journal_path)
    check_journal_owner(installation, journal, journal_path=journal_path, again=again, identity=identity)
    if check_provenance:
        installation.containers()  # Reject foreign provenance before any recovery assumption.
    recovery = journal.get('env_recovery')
    path = getattr(installation, 'env_alias', installation.env_file)
    outcome = None
    try:
        if isinstance(recovery, Mapping) and recovery.get('commit') is not None:
            outcome = envfile.check_commit(path, recovery, durable=durable)
            if outcome == 'restoring':
                envfile.envcommit.check_resume(path, Path(recovery['target']), recovery['commit']['restore'],
                                              recovery['after_sha256'], recovery['before_sha256'])
        elif journal.get('journal_version') == 2:
            raise StateError('missing private env commit reference; keep the journal for manual recovery')
        finished = finished_run(journal, history)
        if finished is not None and outcome == 'restoring':
            raise StateError('unfinished inverse env operation cannot be cleared by a receipt; keep the journal '
                             'and retained files for manual recovery')
        if finished is None and journal.get('stage') in STAGES_BEFORE_ROLLOUT:
            envfile.recovery_text(path, recovery)
        return finished
    except (envfile.EnvFileError, envfile.envcommit.CommitError) as exc:
        raise StateError(f'{exc}. Kept {journal_path}; no guessed restoration was attempted. '
                         'Preserve the journal, current env and private retained entries. '
                         'An older or incomplete journal needs operator-assisted recovery from an independent '
                         'backup; found_values/previous_values cannot prove the original bytes') from exc


def preview_recovery(installation: Installation, journal: Mapping[str, Any],
                     history: Optional[Sequence[Mapping[str, Any]]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Plan as recovering `journal` (Updater.recover) would leave things, without changing anything.

    For check and apply --dry-run. A run stopped before the restart gets its
    version keys put back: they are overlaid (Installation.overlay_env), never
    written. The journal, with the status recovery records, stands in for the
    receipt the next plan reads. Returns that record and the plan notes.
    With `history` (the install's receipts), a journal whose run already
    wrote its receipt (finished_run) changes nothing: that receipt is the
    record, and recovery only removes the journal.
    """

    if journal.get('automatic_transaction'):
        from .orchestrator import GuardRefused
        raise GuardRefused(ReasonCode.AUTOMATIC_RECOVERY_PENDING)
    finished = validate_recovery(installation, journal, history or [], check_provenance=True)
    if finished is not None:
        # Only a final receipt is a run that finished; another one records how the run stopped (Updater.recover).
        run = (f"a finished bin/update {journal.get('kind', 'apply')}" if finished.get("status") in FINISHED_STATUSES
               else f"a bin/update {journal.get('kind', 'apply')} already recorded as {finished.get('status')}")
        return finished, [f"{run} left its journal; the next apply or rollback removes it and recovers nothing"]
    notes = [JOURNAL_NOTE]
    if journal.get("stage") in STAGES_BEFORE_ROLLOUT:
        # Validate the lexical inverse even on a dry run; a legacy last-value
        # map cannot establish that recovery would be safe.
        original = envfile.recovery_text(installation.env_file, journal.get("env_recovery"))
        parsed = {item.key: item.value for item in envfile._definitions(original)}
        values = {key: parsed.get(key) for key in journal.get("env_changes", {})}
        if values:
            installation.overlay_env(values)
            assumed = ", ".join(f"{key}={value or 'unset'}" for key, value in sorted(values.items()))
            notes.append(f"that run stopped before restarting anything; this plan assumes recovery has put back "
                         f"{assumed}")
        status = "interrupted_before_rollout"
    else:
        status = "interrupted_during_rollout"
    return dict(journal, status=status), notes


class Updater:
    def __init__(self, installation: Installation, settings: Settings, state: StateDir, *,
                 out: TextIO = sys.stderr, readiness: Optional[Readiness] = None,
                 hook_runner: Callable[..., Any] = run_hook, command_options: Sequence[str] = (),
                 automation_guard: Optional[AutomationGuard] = None,
                 guard_context: Optional[Callable[["Updater"], LockedContext]] = None,
                 built_in_backup=None, runtime_observer=None) -> None:
        self.installation = installation
        self.settings = settings
        self.state = state
        self.out = out
        self.readiness = readiness or Readiness(installation, out)
        self.hook_runner = hook_runner
        if built_in_backup is not None:
            from .standalone_backup import StandaloneBackup
            if not isinstance(built_in_backup, StandaloneBackup) or getattr(automation_guard, 'automatic', None) is None:
                raise ValueError('built-in backup requires an enrolled automatic lifecycle')
        self.built_in_backup = built_in_backup
        # Optional read-only instrumentation; the runtime still owns every guard.
        self.runtime_observer = runtime_observer
        if (automation_guard is None) != (guard_context is None):
            raise ValueError("automation guard and locked context must be supplied together")
        self.automation_guard = automation_guard
        self.guard_context = guard_context
        self._automation_context: Optional[LockedContext] = None
        self.active_record: Optional[Dict[str, Any]] = None
        self.active_plan: Optional[Dict[str, Any]] = None
        self.automatic_ledger = None
        self.automatic_outcome = None
        self.automatic_receipt = None
        # The release options this run was given (--manifest, --bundle, --policy, --channel), as argv words:
        # every apply (or check) command it prints carries them, so it plans with the same release.
        self.command_options = list(command_options)

    def _guard(self, boundary: GuardBoundary) -> None:
        """Only called under update.lock. Manual invocations take this no-op path.

        A guard never performs recovery. Before any recovery, scheduled calls
        refuse *all* journals, including a finished run's leftover journal.
        Guard errors are typed and sanitized; a missing guard fact is a refusal.
        """
        if self.automation_guard is None:
            return
        from .orchestrator import GuardRefused
        if self.installation.production:
            raise GuardRefused(ReasonCode.INITIALIZATION_REQUIRED)
        if boundary == 'before_recovery' and self.state.read_journal() is not None:
            raise GuardRefused(ReasonCode.RECOVERY_REQUIRED)
        try:
            context = self.guard_context(self)
            self.automation_guard.validate(boundary, context)
            self._automation_context = context
        except GuardRefused:
            raise
        except Exception as exc:
            reason = getattr(exc, 'reason_code', ReasonCode.CONTROL_INVALID)
            try:
                reason = ReasonCode(reason)
            except ValueError:
                reason = ReasonCode.CONTROL_INVALID
            raise GuardRefused(reason) from None

    def _automation_metadata(self) -> Dict[str, Any]:
        if self.automation_guard is None:
            return {}
        return dict(getattr(self._automation_context, 'receipt_metadata', {}))

    def say(self, text: str) -> None:
        self.out.write(text.rstrip("\n") + "\n")
        self.out.flush()

    def _command(self, args: str) -> str:
        """The bin/update command line for this install, e.g. `./bin/update --production apply`.

        Ready to paste in the shell that ran bin/update (plan.install_command:
        `cd <checkout> && ` first when that was another directory). An apply
        or check command carries this run's release options, so it plans with
        the same release; a rollback never takes them.
        """

        options = self.command_options if args.split(" ", 1)[0] in ("apply", "check") else ()
        return install_command(self.installation, args, options)

    def _rollback_command(self, receipt: Any) -> str:
        return self._command(f"rollback --receipt {_quote(receipt)} --yes")

    def _identity(self) -> Dict[str, Any]:
        return self.installation.identity()

    def _env_name(self) -> str:
        return self.installation.relative(self.installation.env_file)

    def _env_path(self) -> Path:
        path = getattr(self.installation, "env_alias", self.installation.env_file)
        target = getattr(self.installation, "env_target", self.installation.env_file if
                         hasattr(self.installation, "env_alias") else None)
        if target is not None and os.path.realpath(path) != str(target):
            raise envfile.EnvCommitError("the selected env target changed since installation discovery; "
                                         "keep any journal and retained files, and reconcile the alias manually")
        return path

    def _refuse_automatic_pending(self):
        if self.automation_guard is None:
            from .orchestrator import GuardRefused
            from .receipt import AutomaticRecoveryPending
            try:
                pending = self.state.automatic_pending(self.installation)
            except AutomaticRecoveryPending as exc:
                raise GuardRefused(ReasonCode.AUTOMATIC_RECOVERY_PENDING) from exc
            if pending:
                raise GuardRefused(ReasonCode.AUTOMATIC_RECOVERY_PENDING)

    # ------------------------------------------------------------- recovery --
    def recover(self, again: str = "") -> Optional[Dict[str, Any]]:
        """Turn a journal left by a killed run into a receipt.

        `again` is the command this run was started with, which another
        install's journal names as the one to run once that is recovered.
        """

        # A true no-op needs neither Docker nor a new state directory. Re-read
        # after taking the lock: another updater may have finished meanwhile.
        self._refuse_automatic_pending()
        if self.state.read_journal() is None:
            return None
        with self.state.lock(**({'automation': True} if self.automation_guard is not None else {})):
            self._guard('before_recovery')
            return self._recover_locked(again)

    def _recover_locked(self, again: str = "") -> Optional[Dict[str, Any]]:
        self._refuse_automatic_pending()
        journal = self.state.read_journal()
        if journal is None:
            return None
        self.installation.check_ownership()
        self._env_path()  # Retain the discovery-time alias/target guard on mutating recovery.
        finished = validate_recovery(self.installation, journal, self._history(),
                                     journal_path=self.state.journal_path, again=again, durable=True)
        if finished is not None:
            # The run wrote its final receipt and was stopped before it could remove its journal: there is nothing
            # to recover, and a second receipt for it (or putting back values it had finished with) would be wrong.
            # A receipt that records a stopped run (a recovery killed after writing it, or a run that failed) says
            # how that run stopped, as its recovery does; only a final one is a run that finished.
            self.state.sync_finished(journal, Path(finished['_path']))
            self.state.clear_journal()
            what = f"bin/update {journal.get('kind', 'apply')} to {journal.get('target_version')}"
            status = finished.get("status")
            if status in FINISHED_STATUSES:
                self.say(f"Removed the journal of a {what} that had finished (receipt {finished['_path']}): it was "
                         "stopped only before it could remove it, so there was nothing to recover.")
            else:
                how = ("it stopped while containers were being replaced" if journal.get("stage") not in
                       STAGES_BEFORE_ROLLOUT else "it stopped before restarting anything, and its version settings "
                       "were put back")
                self.say(f"Removed the journal of an interrupted {what} that was already recorded as {status} "
                         f"(receipt {finished['_path']}): {how}, so there was nothing more to recover.")
            return None
        if journal.get("stage") in STAGES_BEFORE_ROLLOUT:
            self._restore_env(journal)
            self.installation.reload_env()
            journal["status"] = "interrupted_before_rollout"
            note = "it stopped before restarting anything; its version settings were put back exactly"
        else:
            journal["status"] = "interrupted_during_rollout"
            note = "it stopped while containers were being replaced"
        journal["finished_at"] = _now()
        journal["error"] = journal.get("error") or "the bin/update process ended before finishing"
        path = self.state.write_receipt(journal)
        self.state.clear_journal()
        self.say(f"Recovered an interrupted {journal.get('kind', 'apply')} to {journal.get('target_version')}: {note} "
                 f"(receipt {path})")
        journal["_path"] = str(path)
        return journal

    def _restore_env(self, journal: Mapping[str, Any]) -> None:
        """Never reconstruct lexical bytes from last-value-wins receipt fields."""

        try:
            recovery = journal.get("env_recovery")
            envfile._check_recovery(self._env_path(), recovery)
            if "commit" not in recovery:
                # Upgrade a valid legacy inverse BEFORE its first preserving write.
                recovery["commit"] = envfile.envcommit.reference()
                recovery["commit_legacy"] = True
                journal["journal_version"] = 2
                self.state.write_journal(journal)
            envfile.restore_changes(self._env_path(), recovery)
        except envfile.EnvFileError as exc:
            raise StateError(
                f"{exc}. Kept {self.state.journal_path}; no guessed restoration was attempted. "
                "Preserve the journal, current env and private retained entries. Quiesce writers and follow "
                "the manual conflict recovery guidance in deploy/update/README.md. An older or incomplete "
                "journal without exact recovery data needs operator-assisted recovery from an independent backup; "
                "found_values/previous_values cannot prove the original bytes") from exc





    def _check_journal_owner(self, journal: Mapping[str, Any], again: str = "") -> None:
        """Only the install a run was for may recover it (its env file holds the keys to put back).

        Shared with check and status: env file, --production, project and
        mode, as far as the journal records them.
        The refusal names that install's command that only recovers the run
        (rollback without --yes), never one that would go on to update or roll
        back that install; then `again`, this run's own command.
        """

        validate_journal_header(journal, self.state.journal_path)
        check_journal_owner(self.installation, journal, journal_path=self.state.journal_path, again=again)

    # --------------------------------------------------------------- checks --
    def _check_readiness_settings(self) -> None:
        """A Standalone rollout waits with bin/install's settings: refuse bad ones before changing anything."""

        if self.installation.mode != "standalone":
            return
        try:
            standalone_settings(self.installation.env)
        except NotReady as exc:
            raise Blocked(f"{exc} in {self.installation.relative(self.installation.env_file)}. Fix it and run "
                          "bin/update again; nothing was changed") from exc

    # --------------------------------------------------------------- backup --
    def _backup_argv(self, command: Optional[str], allow_no_backup: bool) -> Optional[List[str]]:
        """The backup hook to run (None: --allow-no-backup and none configured); refuses one that cannot run.

        Decided before apply prints its plan, so a run refused for its backup
        never claims to be applying anything.
        """

        origin = " (from --backup-command)" if command else self.settings.origin(BACKUP_SETTING)
        command = command or self.settings.get(BACKUP_SETTING)
        if not command:
            if not allow_no_backup:
                raise Blocked(
                    "no backup is configured. Set FUTURE_AGI_UPDATE_BACKUP_COMMAND in update.conf (a command that "
                    "backs up Postgres, ClickHouse and object storage; INSTALLATION.md › Backups), pass "
                    "--backup-command, or pass --allow-no-backup to update without one")
            return None
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise Blocked(f"{BACKUP_SETTING}{origin} cannot be parsed ({exc}). Fix its quoting; nothing was "
                          "changed") from exc
        if not argv:
            raise Blocked(f"{BACKUP_SETTING} is empty")
        return argv

    def _backup_preflight(self, command, allow_no_backup):
        if self.built_in_backup is None:
            return self._backup_argv(command, allow_no_backup)
        from .orchestrator import require
        require(not command and not allow_no_backup,
                ReasonCode.CONTROL_INVALID)
        require(self.settings.automation_policy.probe_timeout_seconds >= 70, ReasonCode.BACKUP_REQUIRED)
        return None

    def _backup(self, command: Optional[str], allow_no_backup: bool, context: Mapping[str, str]) -> Dict[str, Any]:
        if self.built_in_backup is not None:
            from .standalone_backup import BackupError
            from .orchestrator import GuardRefused
            self._backup_preflight(command, allow_no_backup)
            guard = self.automation_guard
            started = time.monotonic()
            try:
                artifact = self.built_in_backup.run_quiesced(guard.expectation, self.automatic_ledger,
                    guard.automatic.source_observation.facts.runtime_observation,
                    proof_directory_fd=guard.proof_directory)
                guard.proof = artifact.proof
                guard.automatic.retain_cold_hold(artifact, self.automatic_ledger)
            except BackupError as exc:
                guard.recovery_required = exc.recovery_required
                raise GuardRefused(ReasonCode.RECOVERY_REQUIRED if exc.recovery_required
                                   else ReasonCode.BACKUP_FAILED) from exc
            return {'ran': True, 'command': 'standalone-cold-restore-v1',
                    'seconds': round(time.monotonic() - started, 1)}
        argv = self._backup_argv(command, allow_no_backup)
        if argv is None:
            self.say("Skipping the backup: --allow-no-backup was given.")
            return {"ran": False}
        timeout = self.settings.integer("FUTURE_AGI_UPDATE_BACKUP_TIMEOUT_SECONDS", 1, 86400)
        self.say(f"Running the backup hook: {argv[0]}")
        env = dict(os.environ) if self.automation_guard is None else dict(self.installation.environ)
        env.update(context)
        if self.automation_guard is not None:
            env.update(getattr(self._automation_context, 'backup_environment', {}))
        started = time.monotonic()
        try:
            result = self.hook_runner(argv, cwd=str(self.installation.root), env=env, check=False, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise FailedBeforeRollout(f"the backup hook did not finish within {timeout}s; nothing was changed") from exc
        except OSError as exc:
            raise FailedBeforeRollout(f"the backup hook could not run ({exc}); nothing was changed") from exc
        if result.returncode != 0:
            raise FailedBeforeRollout(
                f"the backup hook exited with code {result.returncode} (output above); nothing was changed")
        return {"ran": True, "command": argv[0], "seconds": round(time.monotonic() - started, 1)}

    # --------------------------------------------------------- shared steps --
    @staticmethod
    def _app_ids(containers: List[Dict[str, Any]]) -> List[str]:
        """The Standalone app containers that exist before `up` (readiness counts restarts against them)."""

        return [c["id"] for c in containers if c["service"] == "app" and c["id"] and not c.get("oneoff")]

    def _snapshot_images(self, services: List[str], containers: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        snapshot: Dict[str, Dict[str, Any]] = {}
        for container in containers:
            if container["service"] not in services or not container["image_id"] or container.get("oneoff"):
                continue
            info = self.installation.image(container["image_id"]) or {}
            snapshot[container["service"]] = {
                "image": container["image"], "image_id": container["image_id"],
                "repo_digests": info.get("repo_digests", []), "version": info.get("version", ""),
            }
        return snapshot

    def _check_config(self, expected_tags: Mapping[str, str]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Every Future AGI service must now resolve to exactly its expected tag; returns the model and them."""

        inst = self._automatic_inspection_installation()
        inst.compose_run("config", "--quiet")
        config = inst.config(refresh=True)
        services = inst.futureagi_services(config)
        for service in services:
            variable = COMPONENT_VARIABLES[service["component"]]
            want = expected_tags.get(variable)
            if want is None:
                continue
            ref = service["ref"]
            if ref.tag != want or ref.digest:
                raise UpdateError(
                    f"after the env change, service {service['service']} resolves to {service['image']}, "
                    f"not tag {want}. Something else (a compose override, or an exported variable) sets its image")
        return config, services

    def _pull_missing(self, config: Mapping[str, Any], services: List[Dict[str, Any]]) -> None:
        """Pull the other services' images (postgres, clickhouse, ...) that are not on this host yet.

        A release can move a third-party image. Pulled here, a failure (a
        registry rate limit, a full disk) stops the run before anything
        restarted, instead of inside `up`. An image already present is left
        alone, so no datastore restarts for a newer upstream build.
        """

        ours = {s["service"] for s in services}
        missing: List[Tuple[str, str]] = []
        for name, service in sorted((config.get("services") or {}).items()):
            if name in ours or not isinstance(service, Mapping) or "build" in service:
                continue
            image = service.get("image")
            if image and self.installation.image(image) is None:
                missing.append((name, image))
        if missing:
            self.say(f"Pulling {', '.join(sorted({image for _, image in missing}))} (not on this host yet)")
            self.installation.compose_run("pull", *[name for name, _ in missing], stream=True)

    def _stale(self, config: Mapping[str, Any], services: List[Dict[str, Any]], image_ids: Mapping[str, str],
               expected_tags: Mapping[str, str], changed: Collection[str] = ()) -> None:
        """After the restart, every container of the project must run what this run verified.

        That includes the running containers of services in a Compose profile
        that is not enabled (started earlier with another COMPOSE_PROFILES)
        whose version key this run changes (`changed`, to its tag in
        `expected_tags`): `up` leaves them alone, so they would keep running
        another release than the stack (the one this run replaced, or for a
        rollback the one it undoes). The plan applies the same rule
        (plan.classify_strays). Containers of services this release no longer
        defines (orphans), stopped containers and one-off `docker compose run`
        containers never count.
        """

        configured = config.get("services") or {}
        verified: Dict[str, set] = {}
        for service in services:
            if service["service"] in image_ids:
                verified.setdefault(service["component"], set()).add(image_ids[service["service"]])
        containers = [c for c in self.installation.containers() if not c.get("oneoff")]
        stale = sorted({c["service"] for c in containers
                        if c["service"] in image_ids and c["image_id"] != image_ids[c["service"]]})
        outside: List[Tuple[Dict[str, Any], str]] = []
        # Only a running container outside the active model can be one. classify_strays reads every profile's
        # services (Installation.service_model, another `compose config`), so it is asked only then.
        if any(c["service"] not in configured and c.get("status") in _RUNNING for c in containers):
            inactive = classify_strays(self.installation, containers, configured)["inactive"]
            services_off = {entry["service"] for entry in inactive}
            labels = {cid: entry.get("version") or "" for entry in inactive for cid in entry.get("ids") or []}
            for container in containers:
                label = labels.get(container["id"], "")
                if (container["service"] in services_off and container.get("status") in _RUNNING
                        and self._left_behind(container, label, verified, expected_tags, changed)):
                    outside.append((container, label))
        if stale:
            raise NotReady(f"containers still run the previous images: {', '.join(stale)}")
        if outside:
            runs = sorted({f"{c['service']} runs {label or c['image'] + ' (no version label)'}, not "
                           f"{_release_of(self._stray_want(c, expected_tags))}" for c, label in outside})
            docker = " ".join(shlex.quote(word) for word in self.installation.docker)
            ids = " ".join(c["id"][:12] for c, _ in outside)
            raise NotReady(
                "containers of services outside the active Compose profiles run another release than the stack now "
                f"runs: {'; '.join(runs)}. Enable their profile (COMPOSE_PROFILES in {self._env_name()}) so Compose "
                f"recreates them on the version {self._env_name()} selects, or remove them with "
                f"`{docker} rm -f {ids}`")

    @staticmethod
    def _stray_want(container: Mapping[str, Any], expected_tags: Mapping[str, str]) -> str:
        """The tag a stray container's version key selects now ("" when its image names no Future AGI component)."""

        try:
            component = component_for(parse_image_ref(container.get("image") or ""))
        except ManifestError:
            return ""
        return expected_tags.get(COMPONENT_VARIABLES[component], "") if component else ""

    @staticmethod
    def _left_behind(container: Mapping[str, Any], label: str, verified: Mapping[str, set],
                     expected_tags: Mapping[str, str], changed: Collection[str]) -> bool:
        """Whether a running container of an inactive profile's service runs another release than this run selects.

        `label` is its image's version label ("" without one). Only a key this
        run changes counts (`changed`), judged against the tag it now selects
        (`expected_tags`): for a rollback, the release it restores.
        """

        try:
            ref = parse_image_ref(container.get("image") or "")
        except ManifestError:
            return False
        component = component_for(ref)
        variable = COMPONENT_VARIABLES[component] if component else None
        want = expected_tags.get(variable) if variable in changed else None
        if want is None or container.get("image_id") in verified.get(component, set()):
            return False
        # The plan's judgement (plan.on_release), so check and apply agree: the image's version label decides;
        # the tag it was created from counts only for a component with nothing pulled and verified to compare
        # it with (an inactive profile's pin).
        return not on_release(container.get("image") or "", label, want, by_tag=not verified.get(component))

    def _approved_env_sha256(self) -> Optional[str]:
        """The sha256 of the exact env bytes the automation guard approved (None: a manual run, or a guard without one)."""

        if self.automation_guard is None:
            return None
        return getattr(self.automation_guard, "approved_env_sha256", None)

    def _dependencies_unchanged(self) -> None:
        """Automatic runs only: after the restart every dependency container still runs the image id the guard certified.

        The whole-stack `up` starts whatever a dependency's tag resolves to on this host, and `_stale` covers the
        Future AGI services only. The guard certified, immediately before the rollout, the image ids that the
        Compose configuration resolves to and the running replicas had; a dependency container on another id was
        replaced during this rollout, so the run never reports success (a failed rollout, as `_stale` raises).
        """

        certified = getattr(self.automation_guard, "dependency_image_ids", None) if self.automation_guard else None
        if certified is None:
            return
        observed = {name: [] for name in certified}
        for container in self._automatic_inspection_installation().containers():
            if not container.get("oneoff") and container["service"] in observed:
                observed[container["service"]].append(container.get("image_id"))
        replaced = sorted(name for name, ids in observed.items()
                          if not ids or any(image_id not in certified[name] for image_id in ids))
        if replaced:
            raise DependencyImageChanged(
                f"dependency containers are missing or run an uncertified image: {', '.join(replaced)}")

    def _execute(self, record: Dict[str, Any], expected_tags: Mapping[str, str],
                 verify_image: Callable[[Dict[str, Any], Dict[str, Any]], Optional[str]],
                 pull: Callable[[Dict[str, Any]], bool], app_ids: Iterable[str] = (), *,
                 on_host: Callable[[Dict[str, Any], Dict[str, Any]], bool],
                 stage_only: bool = False, resumed: Optional[Mapping[str, Any]] = None,
                 undoing: Optional[Mapping[str, Any]] = None,
                 strays: Optional[Mapping[str, str]] = None,
                 adopt: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None) -> Path:
        """Run the update sequence for `record`; returns the receipt path.

        `pull(service)` says whether a service's image may be pulled at all;
        one whose local image already carries what it must
        (`on_host(service, image)`) is not pulled again. A service whose tag
        is not on the host at all may name, with `adopt(service)`, the id of
        an image on the host to tag with it instead of pulling (the image a
        rollback restores, which ran under another tag). Every image is then
        verified with `verify_image`. With stage_only, it stops after the
        digest check: nothing is restarted, and an apply's receipt says
        "staged" (a rollback's "rolled_back", with restarted false).
        `resumed` is the unfinished update (or rollback) this run finishes, if
        any; `undoing` is the receipt of the update a rollback undoes.
        `strays` maps each version key this run changes to the tag it now
        selects: running containers of inactive profiles' services on those
        keys must run that release after the restart (default: the keys this
        update changes, at their expected tags).
        """

        installation = self.installation
        stage = "prepare"
        self.active_record = record
        self._guard('before_env_commit')
        if self.automation_guard is not None:
            record['automation'] = self._automation_metadata()
        installation.check_ownership()
        approved = self._approved_env_sha256()
        if approved is None:
            env_recovery = envfile.recovery_record(self._env_path(), record["env_changes"])
        else:
            # Automatic runs: the baseline of this transaction is exactly the env the guard approved. An operator
            # edit that landed after that approval is a typed conflict, never adopted (nothing is journaled).
            from .orchestrator import GuardRefused
            try:
                env_recovery = envfile.recovery_record(self._env_path(), record["env_changes"],
                                                       expected_before_sha256=approved)
            except envfile.EnvPreimageError:
                raise GuardRefused(ReasonCode.ENV_CONFLICT) from None
        record.update(stage=stage, status="running", journal_version=2, env_recovery=env_recovery)
        # The minimal lexical inverse and both hashes are durable before any
        # env bytes change, including a kill during write/rename/fsync itself.
        self.state.write_journal(record)

        def advance(name: str) -> None:
            # The stage counts only once its journal is on disk: a failure or
            # interrupt while writing it still belongs to the previous stage.
            nonlocal stage
            record["stage"] = name
            try:
                self.state.write_journal(record)
            except BaseException:
                record["stage"] = stage
                raise
            stage = name

        try:
            with signals_as_interrupt():
                envfile.write_changes(self._env_path(), record["env_changes"], recovery=record["env_recovery"])
                installation.reload_env()
                advance("env_written")
                config, services = self._check_config(expected_tags)
                names: List[str] = []
                present: List[str] = []
                tagged: List[str] = []
                for service in services:
                    if not pull(service):
                        continue
                    # Already on this host with the expected digest: verified below like the others, and needs no
                    # registry (finishing a staged update, or a rollback during an outage).
                    local = installation.image(service["image"])
                    if local is None and adopt is not None:
                        image_id = adopt(service)
                        if image_id:
                            self.say(f"Tagging {image_id[:19]}…, the image that ran before the update (on this host "
                                     f"under another tag), as {service['image']} instead of pulling it")
                            installation.docker_run("tag", image_id, service["image"])
                            tagged.append(service["image"])
                            local = installation.image(service["image"])
                    if local is not None and on_host(service, local):
                        if service["image"] not in tagged:
                            present.append(service["image"])
                        continue
                    names.append(service["service"])
                if present:
                    self.say(f"Already on this host with the expected digest, not pulled: "
                             f"{', '.join(sorted(set(present)))}")
                if names:
                    self.say(f"Pulling {', '.join(sorted({s['image'] for s in services if s['service'] in names}))}")
                    installation.compose_run("pull", *names, stream=True)
                self._pull_missing(config, services)
                advance("pulled")
                image_ids: Dict[str, str] = {}
                for service in services:
                    info = installation.image(service["image"])
                    if info is None:
                        raise UpdateError(f"{service['image']} is not on this host after the pull")
                    problem = verify_image(service, info)
                    if problem:
                        raise UpdateError(problem)
                    image_ids[service["service"]] = info["id"]
                record["target_image_ids"] = image_ids
                if not stage_only:
                    # Only the images just verified may start: `up` must not pull again (a pull_policy in an
                    # override would otherwise fetch whatever the tag points at by now).
                    up = ["up", "-d", "--no-build"]
                    if installation.supports_up_pull_never():
                        up += ["--pull", "never"]
                    self._guard('before_rollout')
                    advance("rollout")
                    self.say("Restarting the stack on the new images")
                    if installation.mode == "standalone":
                        installation.compose_run(*up, stream=True)
                        self.readiness.wait_standalone(previous_ids=list(app_ids))
                    else:
                        wait = self.settings.integer("FUTURE_AGI_UPDATE_WAIT_TIMEOUT_SECONDS", 60, 7200)
                        installation.compose_run(*up, "--wait", "--wait-timeout", str(wait), stream=True)
                        self.readiness.wait_api()
                    if strays is None:
                        # The keys this update changes: previous_values carries them through a resume (or the
                        # run finishing a staged update), whose own env_changes may be empty.
                        changed = set(record.get("env_changes") or {}) | set(record.get("previous_values") or {})
                        self._stale(config, services, image_ids, expected_tags, changed)
                    else:
                        self._stale(config, services, image_ids, strays, set(strays))
                    self._dependencies_unchanged()
        except envfile.EnvCommitError as exc:
            # Exchange may already have captured an operator entry. Never swap
            # back, write a completion receipt, or let tags alone authorize retry.
            raise UpdateError(f"{exc}. Kept {self.state.journal_path}") from exc
        except (Exception, Interrupted, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, (Interrupted, KeyboardInterrupt))
            secrets = self.state.secrets
            detail = scrub(str(exc) or exc.__class__.__name__, secrets)
            record["error"] = "interrupted" if interrupted and not str(exc) else detail
            record["finished_at"] = _now()
            if self.automation_guard is not None:
                from .processes import ProcessError
                from .orchestrator import GuardRefused
                if isinstance(exc, GuardRefused) and exc.reason_code in (
                        ReasonCode.RECOVERY_REQUIRED, ReasonCode.ENV_CONFLICT, ReasonCode.OWNERSHIP_UNCONFIRMED):
                    # Do not recover through or clear evidence that a foreign
                    # writer replaced while this run held its cooperative lock.
                    raise UpdateError(ReasonCode.RECOVERY_REQUIRED.value) from exc
                # A client timeout cannot prove the daemon stopped. Retain the
                # journal/inverse and forbid automatic fallback or next-tick recovery.
                cause = exc
                seen = set()
                uncertain = getattr(installation.runner, 'uncertain', False) is True
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    uncertain = uncertain or (isinstance(cause, ProcessError) and cause.uncertain)
                    cause = cause.__cause__
                if uncertain:
                    record['automation']['reason_code'] = (ReasonCode.OPERATION_INTERRUPTED if interrupted
                                                           else ReasonCode.OPERATION_TIMEOUT).value
                    self.state.write_journal(record)
                    raise (InterruptedRun if interrupted else UpdateError)(ReasonCode.RECOVERY_REQUIRED.value) from exc
            if stage in STAGES_BEFORE_ROLLOUT:
                try:
                    self._restore_env(record)
                    installation.reload_env()
                except Exception as restore_exc:  # leave the journal: the next run restores
                    again = (self._command("apply --stage" if stage_only else "apply") if record["kind"] == "apply"
                             else self._rollback_command(record["rolls_back"]))
                    problem = scrub(str(restore_exc) or restore_exc.__class__.__name__, secrets)
                    if isinstance(restore_exc.__cause__, envfile.EnvCommitError):
                        raise UpdateError(f"{detail}; exact restoration stopped: {problem}. "
                                          "The conflict requires manual reconciliation, not an automatic retry") from exc
                    raise UpdateError(
                        f"{detail}; restoring the previous version settings also failed ({problem}). "
                        f"Run `{again}` again: it restores them first") from exc
                record["status"] = "interrupted_before_rollout" if interrupted else "failed_before_rollout"
                path = self.state.write_receipt(record)
                self.state.clear_journal()
                message = self._before_rollout_text(record, expected_tags, resumed, path, undoing=undoing)
                raise (InterruptedRun if interrupted else FailedBeforeRollout)(message) from exc
            record["status"] = "interrupted_during_rollout" if interrupted else "failed_during_rollout"
            path = self.state.write_receipt(record)
            self.state.clear_journal()
            hint = installation.compose_hint()
            inspect = f"Inspect `{hint} ps -a` and `{hint} logs`"
            if record["kind"] == "apply":
                finish = self._command("apply")
                back = self._rollback_command(path)
                # The production bootstrap jobs only check the schema: one that failed most likely means the
                # release's initialization jobs did not run before this restart.
                jobs = (f" If a bootstrap or schema check job failed, the release's initialization jobs probably "
                        f"did not run: run them with {self._init_jobs()}, then `{finish}`."
                        if installation.production else "")
                next_steps = (
                    f"{inspect}. To finish, run `{finish}` again for {record['target_version']}.{jobs} To go back, "
                    f"run `{back}` (database migrations are not reverted: restore your backup if the previous "
                    "version cannot start)")
            else:
                retry = self._rollback_command(record["rolls_back"])
                next_steps = f"{inspect}, then rerun `{retry}`"
            message = (f"{record['kind']} to {record['target_version']} stopped while containers were being "
                       f"replaced: {record['error']}. The env file selects {record['target_version']}. {next_steps}. "
                       f"Receipt: {path}")
            raise (InterruptedRun if interrupted else FailedDuringRollout)(message) from exc
        if record["kind"] == "apply":
            record["status"] = STAGED if stage_only else "succeeded"
        else:
            record["status"] = "rolled_back"
            if stage_only:
                record["restarted"] = False
        record["finished_at"] = _now()
        path = self.state.write_receipt(record)
        self.state.clear_journal()
        return path

    def _before_rollout_text(self, record: Mapping[str, Any], expected_tags: Mapping[str, str],
                             resumed: Optional[Mapping[str, Any]], path: Path, extra: str = "",
                             undoing: Optional[Mapping[str, Any]] = None) -> str:
        """What a run that stopped before restarting anything left behind, and what to do next."""

        stopped = f"{record['kind']} stopped before restarting anything: {record['error']}."
        if record["kind"] == "rollback" and not resumed:
            return self._rollback_before_rollout_text(record, stopped, undoing, path, extra)
        if not resumed:
            unchanged = ("Nothing was restarted; the previous versions are running." if extra else
                         "Nothing was changed; the previous versions are configured and running.")
            return f"{stopped} {unchanged}{extra} Receipt: {path}"
        # A resume (or the finishing run of a staged update) found the env file selecting the update it was
        # finishing, and put exactly that back: that update is still the one to finish or roll back.
        target = record["target_version"]
        env_name = self._env_name()
        if resumed.get("kind") == "rollback":
            # A retried rollback found the env file selecting what the stopped rollback restored, while the
            # containers may be a mix: rerunning it finishes it.
            found = self._selects(record.get("found_values") or expected_tags) or target
            return (f"{stopped} The rollback to {target} that stopped while containers were being replaced is "
                    f"still unfinished: {env_name} selects {found}, and some containers may still run "
                    f"{record.get('from_version')}. To finish it, run `{self._rollback_command(record['rolls_back'])}`."
                    f"{extra} Receipt: {path}")
        staged = resumed.get("status") == STAGED
        how = "staged earlier" if staged else "that stopped while containers were being replaced"
        back = self._rollback_command(resumed.get("_path"))
        # The keys the update changed (this run's record carries the original run's), at the tags it wrote.
        wanted = selected_keys(record) or dict(expected_tags)
        if self._selects_all(wanted):
            if staged:
                state = (f"The update to {target} {how} is still pending: {env_name} selects it "
                         f"({self._selects(wanted)}), and the stack still runs the previous versions.")
            else:
                state = (f"The update to {target} {how} is still unfinished: {env_name} selects it "
                         f"({self._selects(wanted)}).")
            return (f"{stopped} {state} To finish it, {self._finish_text()}; to go back, run `{back}`.{extra} "
                    f"Receipt: {path}")
        # A value this run could not put back: the env file selects neither the update nor what ran before it.
        return (f"{stopped} {env_name} selects {self._selects(wanted)} now, not all of the update to {target} "
                f"{how}.{extra} To go back to the versions from before that update, run `{back}`; "
                f"`{self._command('check')}` shows what an update would do now. Receipt: {path}")

    def _selects(self, keys: Iterable[str]) -> str:
        """What the env file selects for these keys now: `KEY=value, ...` (`KEY=unset` for an unset one)."""

        return _assignments({key: self.installation.env.get(key) for key in keys})

    def _selects_all(self, values: Mapping[str, str]) -> bool:
        """Whether the env file holds every one of these values now (and there is at least one)."""

        return bool(values) and all(self.installation.env.get(key) == value for key, value in values.items())

    def _finish_text(self, undoing: Optional[Mapping[str, Any]] = None, first: str = "") -> str:
        """How to finish a staged or stopped update: production runs the release's initialization jobs first.

        From a rollback (`undoing`: the update it was undoing), the apply
        names that update's own manifest (_finish_command). `first` is a step
        that comes before everything else (bringing that release's deployment
        files back), if any.
        """

        apply = self._command("apply") if undoing is None else self._finish_command(undoing)
        if self.installation.production:
            if first:
                return (f"{first}, then run the release's initialization jobs if they have not run yet, with "
                        f"{self._init_jobs()}, then run `{apply}`")
            return (f"run the release's initialization jobs first if they have not run yet, with "
                    f"{self._init_jobs()}, then run `{apply}`")
        return f"{first}, then run `{apply}`" if first else f"run `{apply}`"

    def _files_back_step(self, version: Any) -> str:
        """The step that gives the checkout release `version`'s deployment files back ("" when they are its own).

        After a rollback's restore (`git checkout <previous> -- ...`) was
        applied, finishing the update the rollback was undoing needs that
        update's files again: its apply refuses deployment files that differ.
        Said only when git can compare them (_deployment_differences).
        """

        if not isinstance(version, str) or not is_version(version):
            return ""
        check = self._deployment_differences(version)
        if check["note"] or not check["differ"]:
            return ""
        return (f"bring back {version}'s deployment files with `{cd_prefix(self.installation)}git checkout {version} "
                f"-- {_words(check['differ'])}`")

    def _default_manifest(self) -> str:
        """The manifest a plain `apply` reads (FUTURE_AGI_UPDATE_MANIFEST_URL, a relative file in the checkout)."""

        source = self.settings.get("FUTURE_AGI_UPDATE_MANIFEST_URL")
        if "://" not in source and not Path(source).is_absolute():
            source = str(self.installation.root / source)
        return source

    def _finish_command(self, update: Mapping[str, Any]) -> str:
        """The apply that finishes `update` (an apply's receipt), printed by a run that is not about its release.

        It names that update's manifest (`--manifest`, GitHub's moving latest
        URL turned into the release's own) unless a plain apply reads that
        manifest anyway, and never this run's release options: --channel or
        --policy given to this run belong to another manifest.
        """

        source = str(update.get("manifest_source") or "")
        # The channel of that update's manifest (receipts record it), when this install follows another one: the
        # plan accepts a manifest only for the channel the install follows.
        channel = str(update.get("channel") or "")
        channel = f" --channel {_quote(channel)}" if channel and channel != self.settings.channel else ""
        if source and source != self._default_manifest():
            location = _manifest_of(update, self.settings)
            if location:
                return install_command(self.installation, f"apply --manifest {_quote(location)}{channel}")
        return install_command(self.installation, f"apply{channel}")

    @staticmethod
    def _running_versions(record: Mapping[str, Any]) -> List[str]:
        """The release versions a rollback found running (its previous_images, taken when it started)."""

        images = record.get("previous_images") or {}
        found = {str((entry or {}).get("version") or "") for entry in images.values() if isinstance(entry, Mapping)}
        return sorted((version for version in found if is_version(version)), key=parse_version)

    def _rollback_before_rollout_text(self, record: Mapping[str, Any], stopped: str,
                                      undoing: Optional[Mapping[str, Any]], path: Path, extra: str) -> str:
        """A rollback that stopped before restarting anything: the update it was undoing is as it was.

        Told from the env file as it is now (the values the rollback found,
        put back) and the containers the rollback found running. A staged
        update is still pending, and an update that stopped while containers
        were being replaced still unfinished, only while the env file selects
        it: then either can be finished or dropped. One whose values were set
        back by hand is not pending any more, and the message says what the
        env file selects instead. A staged update the stack already runs (it
        was restarted outside bin/update, as the plan judges it) is a finished
        update: only the retry is offered.
        """

        update = record.get("from_version")
        env_name = self._env_name()
        found = record.get("found_values") or {}
        selects = self._selects(found) or str(update)
        before = record.get("target_version")
        retry = self._rollback_command(record["rolls_back"])
        undoing = undoing or {}
        status = undoing.get("status")
        running = self._running_versions(record)
        runs = ", ".join(running) or str(update)
        runs_update = status == STAGED and bool(undoing.get("_already_runs"))
        if (status == STAGED and not runs_update) or status in RESUMABLE_STATUSES:
            staged = status == STAGED
            how = "staged earlier" if staged else "that stopped while containers were being replaced"
            wanted = selected_keys(undoing)
            # What the rollback found running: a staged update part of which was started by hand is still pending.
            stack = ("" if not staged else f"the stack still runs {', '.join(running) or before}"
                     if update not in running else "some of its containers were started on it outside bin/update")
            if self._selects_all(wanted):
                if staged:
                    state = (f"The update to {update} {how} is still pending: {env_name} selects it "
                             f"({self._selects(wanted)}), and {stack}.")
                    back = f"to drop it, run `{retry}`"
                else:
                    state = (f"The update to {update} {how} is still unfinished: {env_name} selects it "
                             f"({self._selects(wanted)}), and some containers may still run {before}.")
                    back = f"to go back, run `{retry}`"
                # The restore the rollback asked for may have given the checkout the previous release's files.
                finish = self._finish_text(undoing, self._files_back_step(update))
                return f"{stopped} {state} To finish it, {finish}; {back}.{extra} Receipt: {path}"
            mixed = stack if staged else f"some containers may run {update} and others {before}"
            return (f"{stopped} {env_name} selects {self._selects(found or wanted)} now, not the update to {update} "
                    f"{how}; {mixed}. To try the rollback again, run `{retry}`.{extra} Receipt: {path}")
        note = (f" The update to {update} staged earlier already runs: the stack was restarted on it outside "
                "bin/update." if runs_update else "")
        if extra:
            unchanged = f"Nothing was restarted; the stack still runs {runs}."
        elif not undoing or self._selects_all(selected_keys(undoing)):
            # The env file still holds what the update wrote.
            unchanged = (f"Nothing was changed: {env_name} still selects {selects}, "
                         + ("which the stack runs." if runs == update else f"and the stack runs {runs}."))
        else:
            # Set back by hand (some keys, or all): quoted as it is.
            unchanged = f"Nothing was changed: {env_name} selects {selects} now, while the stack runs {runs}."
        again = (f"To try the rollback again, which restarts the running stack on {before}, run `{retry}`."
                 if runs_update else f"To try the rollback again, run `{retry}`.")
        return f"{stopped}{note} {unchanged} {again}{extra} Receipt: {path}"

    # ---------------------------------------------------------------- apply --
    def _history(self) -> List[Dict[str, Any]]:
        """This install's receipts, oldest first: another install's in the same checkout never count."""

        return self.state.history(self._identity())

    @staticmethod
    def _unfinished_update(history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The update a resume (or the run finishing a staged update) continues: its receipt, with `_path`.

        Asked only when the plan resumes (plan.unfinished judged, with the env
        file, that the update is still the one it selects).
        """

        record = unfinished(history)
        return record if record and record.get("kind") == "apply" else None

    def _two_steps_needed(self, manifest: Mapping[str, Any], resumed: Optional[Mapping[str, Any]],
                          stage: bool) -> bool:
        """Whether this run must be `apply --stage`: a production release with migrations updates in two steps.

        The production overlay's bootstrap jobs only check the schema, so the
        release's jobs must run against its own images before the restart:
        `apply --stage` pins and verifies them, the operator runs the jobs, a
        plain `apply` restarts. An unfinished update is past that point.
        """

        if not self.installation.production or stage or resumed:
            return False
        migration = manifest.get("migration") if isinstance(manifest, Mapping) else None
        return isinstance(migration, Mapping) and bool(migration.get("requires_bootstrap"))

    def _check_two_steps(self, manifest: Mapping[str, Any], resumed: Optional[Mapping[str, Any]], stage: bool) -> None:
        """Refuse a plain apply that must be `apply --stage` (_two_steps_needed), before anything is changed."""

        if not self._two_steps_needed(manifest, resumed, stage):
            return
        raise Blocked(
            f"production installs update in two steps: {self._command('apply --stage')} (backs up, then pulls and "
            f"verifies the release; restarts nothing), run the release's initialization jobs with "
            f"{self._init_jobs()}, then {self._command('apply')}. Nothing was changed")

    def _refusal(self, plan: Mapping[str, Any], manifest: Mapping[str, Any], resumed: Optional[Mapping[str, Any]],
                 staged: Optional[Mapping[str, Any]], *, stage: bool, dry_run: bool, backup_command: Optional[str],
                 allow_no_backup: bool) -> Optional[Blocked]:
        """Why apply refuses this plan before changing anything (None: it goes ahead, or has nothing to do).

        In this order: a blocked plan, production's two steps, a stage after a
        stopped rollout, the readiness settings, then the backup (a fresh run
        needs one configured, or --allow-no-backup). The backup itself runs
        only once the plan was printed.
        """

        if plan["decision"] == BLOCKED:
            return Blocked("the update is blocked: " + "; ".join(plan["reasons"]))
        if plan["decision"] == UP_TO_DATE:
            return None
        try:
            self._check_two_steps(manifest, resumed, stage)
            if stage and resumed and not staged:
                self._refuse_stage(plan["target_version"], resumed)
            self._check_readiness_settings()
            if not dry_run and not resumed:
                if self.automation_guard is not None:
                    from .orchestrator import GuardRefused
                    if allow_no_backup:
                        raise GuardRefused(ReasonCode.BACKUP_REQUIRED)
                    try:
                        self._backup_preflight(backup_command, False)
                    except Blocked:
                        raise GuardRefused(ReasonCode.BACKUP_REQUIRED) from None
                else:
                    self._backup_preflight(backup_command, allow_no_backup)
        except Blocked as exc:
            return exc
        return None

    def _images_on_host(self, plan: Mapping[str, Any]) -> bool:
        """Whether every release image this run starts is on this host already with its signed digest.

        Then _execute pulls none of them (it verifies them again): finishing
        a staged update, or a run after one that pulled them and stopped.
        """

        items = [item for item in plan["components"] if not item.get("inactive") and item.get("services")]
        if not items:
            return False
        for item in items:
            info = self.installation.image(item["target_image"])
            if info is None:
                return False
            name = parse_image_ref(item["target_image"]).name
            if (name, item["target_digest"]) not in _digest_set(info.get("repo_digests") or []):
                return False
        return True

    def _refuse_stage(self, target: str, resumed: Mapping[str, Any]) -> None:
        """`apply --stage` restarts nothing, but an update that stopped mid-rollout already restarted some."""

        first = (f"run the release's initialization jobs first if they have not run, with {self._init_jobs()}, "
                 "then " if self.installation.production else "")
        path = resumed.get("_path")
        # A dry run after a killed run plans with the journal, which has no receipt yet: the last update is it.
        back = self._rollback_command(path) if path else self._command("rollback --yes")
        raise Blocked(
            f"the update to {target} already stopped while containers were being replaced"
            f"{f' ({path})' if path else ''}, so there is nothing left to stage: {first}run "
            f"`{self._command('apply')}` to finish it, or `{back}` to go back. Nothing was changed")

    def _dry_run_text(self, plan: Mapping[str, Any], resumed: Optional[Mapping[str, Any]], stage: bool) -> str:
        changes = ", ".join(f"{k}={v}" for k, v in plan["env_changes"].items()) or "no version keys"
        first = "" if resumed else "run the backup, "
        images = ("verify the images again (pulling only those no longer on this host)" if resumed
                  else "pull and verify the images")
        # A run that is still only a journal (killed, not recovered yet) has no receipt to name.
        path = (resumed or {}).get("_path")
        if stage:
            end = "and stop before restarting anything (staged)"
        elif resumed and resumed.get("status") == STAGED:
            end = f"and restart the stack, finishing the update staged{f' in {path}' if path else ''} (no new backup)"
        elif resumed:
            end = (f"and restart the stack, finishing the update that stopped while containers were being replaced"
                   f"{f' ({path})' if path else ''} (no new backup)")
        else:
            end = "and restart the stack"
        return f"Dry run: nothing was changed. Without --dry-run, bin/update would {first}set {changes}, {images}, {end}."

    def _skip_backup(self, resumed: Mapping[str, Any], target: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Finishing a staged update, or one that stopped mid-rollout, takes no second backup.

        The backup that ran before the update started covers it (if it ran:
        an update started with --allow-no-backup has none). So the commands
        printed for finishing an update never need the backup flags again.
        """

        path = resumed.get("_path")
        ran = self._backup_ran(resumed, history)
        if resumed.get("status") == STAGED:
            self.say(f"Skipping the backup: {'it ran when ' if ran else ''}the update to {target} was staged ({path}).")
            return {"ran": False, "staged": path}
        stopped = f"this run finishes the update to {target} that stopped while containers were being replaced ({path})"
        self.say(f"Skipping the backup: {stopped}, and the backup taken before that update started covers it." if ran
                 else f"Skipping the backup: {stopped}; that update started without a backup.")
        return {"ran": False, "resumes": path}

    @staticmethod
    def _backup_ran(record: Optional[Mapping[str, Any]], history: List[Dict[str, Any]]) -> bool:
        """Whether a backup ran before the update `record` belongs to (following the runs it resumed)."""

        receipts = {_key(r["_path"]): r for r in history if r.get("_path")}
        seen = set()
        while record is not None:
            backup = record.get("backup")
            if isinstance(backup, Mapping) and backup.get("ran"):
                return True
            link = record.get("resumes")
            if not link or _key(link) in seen:
                return False
            seen.add(_key(link))
            record = receipts.get(_key(link))
        return False

    def _init_jobs(self) -> str:
        """How to run a release's initialization jobs for this install, worded as the plan words it (jobs_text)."""

        return jobs_text(self.installation)

    def _staged_text(self, target: str, expected: Mapping[str, str], path: Path) -> str:
        steps = [f"Staged {target}: {self._env_name()} now selects it ({_assignments(expected)}) and its images are "
                 "pulled and verified. Nothing was restarted: the stack still runs the previous versions."]
        if self.installation.production:
            steps.append(f"Next, run the release's initialization jobs, which now use these images, with "
                         f"{self._init_jobs()}.")
        steps.append(f"Then run `{self._command('apply')}` to restart the stack on {target}. To drop the staged "
                     f"update instead, run `{self._rollback_command(path)}`. Receipt: {path}")
        return " ".join(steps)

    def _check_stage_binding(self, staged, binding, acknowledge, *, stage_only=False):
        """Validate identity on every resume; authorize freshness only for finish.

        New host/CLI adapters pass release_binding for both stage and finish.
        The legacy operator entry point (no binding argument) keeps its existing
        behavior. Acknowledgement excuses age/absent proof only, never identity.
        Restaging requires a complete stored identity, but neither uses nor
        refreshes backup freshness/proof: it cannot restart or authorize rollout.
        """
        from datetime import datetime, timezone
        from .orchestrator import GuardRefused
        saved = self.state.read_private('stage', staged['run_id']) if staged.get('run_id') else None
        if saved is None:
            if stage_only or not acknowledge:
                raise GuardRefused(ReasonCode.STALE_STAGE)
            return
        keys = ('manifest_hash', 'manifest_sha256', 'bundle_sha256', 'installation_fingerprint',
                'config_fingerprint', 'profile_fingerprint', 'trust_fingerprint')
        if any(not saved.get(key) or saved[key] != binding.get(key) for key in keys):
            raise GuardRefused(ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        if stage_only:
            return
        try:
            created = datetime.strptime(saved['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            # A manual hook without the attempt-bound attestation is explicitly
            # legacy/absent, not automatically upgraded by another successful hook.
            proof_time = saved.get('backup_completed_at')
            proof_age = None if proof_time is None else (datetime.now(timezone.utc) - datetime.strptime(
                proof_time, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)).total_seconds()
            stale = age < 0 or age > 86400 or proof_age is None or not 0 <= proof_age <= 3600
        except (ValueError, TypeError, KeyError):
            stale = True
        if stale and not acknowledge:
            raise GuardRefused(ReasonCode.STALE_STAGE)

    def _automatic_inspection_installation(self):
        """Automatic pure config/image/container reads have no late-writer risk."""
        if getattr(self.automation_guard, 'automatic', None) is None:
            return self.installation  # Unchanged legacy/manual supervision.
        from .docker_host import bounded_installation
        return bounded_installation(self.installation, min(30, self.settings.automation_policy.probe_timeout_seconds))

    def _automatic_runtime(self, lifecycle, ledger, backup):
        from .automatic_runtime import AutomaticRuntime, HostRuntimeObserver
        observer = self.runtime_observer or HostRuntimeObserver(self.installation, lifecycle.registration, backup.b,
            timeout=min(30, self.settings.automation_policy.probe_timeout_seconds))
        return AutomaticRuntime(self.installation, ledger, backup.b, observer=observer)

    def _open_automatic_ledger(self, ledger):
        from .recovery import RecoveryLedger
        from .backup_proof import BackupExpectation
        from datetime import datetime
        doc = ledger.snapshot
        expected = dict(doc['expectation'])
        expected['reserved_at'] = datetime.fromisoformat(expected['reserved_at'])
        expected['covered_domains'] = tuple(expected['covered_domains'])
        return RecoveryLedger.open(ledger.directory, expected=BackupExpectation(**expected),
            operation_token=doc['operation_token'], run_id=doc['run_id'], binding_sha256=doc['binding_sha256'])

    def _automatic_receipt_locked(self, record, ledger, key):
        """Exact idempotent receipt replay after namespace/fsync interruption."""
        from .orchestrator import require
        matches = [row for row in self._history() if row.get('run_id') == record['run_id']
                   and row.get('kind') == record['kind']]
        require(len(matches) <= 1, ReasonCode.RECOVERY_REQUIRED)
        if matches:
            saved = matches[0]
            from .receipt import _PUBLIC_RECEIPT_FIELDS
            require(all(saved.get(k) == v for k, v in record.items() if k in _PUBLIC_RECEIPT_FIELDS),
                    ReasonCode.RECOVERY_REQUIRED)
            path = Path(saved['_path'])
        else:
            path = self.state.write_receipt(record)
        self.state.sync_finished(record, path)
        ledger.checkpoint(**{key: path.name})
        self.automatic_receipt = path
        return path

    def finalize_automatic_success_locked(self, *, lifecycle, ledger) -> Path:
        """Metadata-only completion of a durable success decision.

        Caller holds runner.lock -> update.lock and hydrates lifecycle.intent,
        target and original attempt authority from retained baseline records.
        A succeeded receipt is deliberately NOT a precondition. Returns its
        exact Path (also on replay); the baseline runner may project it as
        (path, lifecycle.target.version). Never starts, pulls, re-extracts or
        chooses restoration, including on callback/receipt/CAS failure.
        """
        from .orchestrator import require
        require(self.state.lock_holder(automation=True) is not None, ReasonCode.RECOVERY_REQUIRED)
        ledger = self._open_automatic_ledger(ledger)
        self.automatic_ledger = lifecycle.ledger = ledger
        lifecycle.backup_seal = ledger.backup_seal
        require(ledger.direction == 'success_decided' and ledger.snapshot['pending'] is None,
                ReasonCode.RECOVERY_REQUIRED)
        from .baseline import identity
        require(ledger.snapshot['success_seal'].get('successor_intent_sha256') == identity(lifecycle.intent),
                ReasonCode.RECOVERY_REQUIRED)
        return self._finalize_automatic_success_locked(lifecycle, ledger)

    def _finalize_automatic_success_locked(self, lifecycle, ledger):
        from .orchestrator import require
        require(ledger.direction == 'success_decided', ReasonCode.RECOVERY_REQUIRED)
        seal = ledger.snapshot['success_seal']
        require(isinstance(seal, dict) and seal.get('run_id') == ledger.snapshot['run_id'],
                ReasonCode.RECOVERY_REQUIRED)
        record = seal['record']
        self.active_record = record
        path = self._automatic_receipt_locked(record, ledger, 'success_receipt')
        journal = self.state.read_journal()
        if journal is not None:
            require(journal.get('run_id') == record['run_id'] and journal.get('automatic_transaction'),
                    ReasonCode.RECOVERY_REQUIRED)
            self.state.clear_journal()  # immutable decision/record now owns replay
        lifecycle.complete_success(self, path)
        ledger.checkpoint('success_metadata_committed')
        ledger.checkpoint('succeeded')
        self.automatic_outcome = 'succeeded'
        return path

    def _reconcile_automatic_env_locked(self, ledger):
        """Only native envcommit can resolve an interrupted env exchange."""
        from .orchestrator import require
        pending = ledger.snapshot['pending']
        if pending is None or pending['kind'] != 'env_commit':
            return
        record = self.state.read_journal()
        require(record is not None and record.get('run_id') == ledger.snapshot['run_id']
                and record.get('automatic_transaction'), ReasonCode.RECOVERY_REQUIRED)
        recovery = record['env_recovery']
        outcome = envfile.check_commit(self._env_path(), recovery, durable=True)
        import hashlib
        actual = hashlib.sha256(self._env_path().read_bytes()).hexdigest()
        require(outcome in ('accepted', 'absent') and actual in
                (recovery['before_sha256'], recovery['after_sha256']), ReasonCode.ENV_CONFLICT)
        ledger.complete(pending['sequence'], {'env_sha256': actual, 'native_commit': outcome})

    def restore_backup_locked(self, *, lifecycle, ledger):
        """The ONLY automatic failure path, called without releasing update.lock.

        Storage restoration is the snapshot lane's StandaloneRestore. This
        method owns env-journal correlation, exact previous startup and receipt
        callbacks; it never changes runner state itself or pulls an image.
        """
        from .backup_proof import BackupExpectation
        from .orchestrator import require
        from .standalone_restore import StandaloneRestore
        from datetime import datetime
        require(ledger.direction == 'restore_decided', ReasonCode.RECOVERY_REQUIRED)
        self._reconcile_automatic_env_locked(ledger)
        backup = lifecycle.backup(self.installation)
        doc = ledger.snapshot
        expected = dict(doc['expectation'])
        expected['reserved_at'] = datetime.fromisoformat(expected['reserved_at'])
        expected['covered_domains'] = tuple(expected['covered_domains'])
        expected = BackupExpectation(**expected)
        record = self.state.read_journal() or doc.get('forward_record')
        if record is not None:
            require(record.get('run_id') == doc['run_id'] and record.get('automatic_transaction'),
                    ReasonCode.RECOVERY_REQUIRED)
        self.active_record = record
        inverse = record.get('env_recovery') if record else None
        lifecycle.ledger, lifecycle.backup_seal = ledger, ledger.backup_seal
        lifecycle.prepare_restore(self)
        runtime = self._automatic_runtime(lifecycle, ledger, backup)
        # Once armed, do NOT call any storage method, including a revalidation
        # that could clear/re-extract. Source startup itself may have written.
        if not doc['previous_start_armed']:
            runtime.retain_failed_logs()
            restore_policy = replace(backup.policy,
                timeout_seconds=self.settings.automation_policy.restore_timeout_seconds)
            restore = StandaloneRestore(self.installation, backup.binding, backup.destination,
                policy=restore_policy, clock=lifecycle.clock)
            try:
                restore.resume(expected, ledger, proof_raw=lifecycle.backup_raw, env_recovery=inverse)
                ledger.checkpoint(loss_window_start=ledger.backup_seal.snapshot_at.isoformat(),
                    loss_window_end=lifecycle.clock().isoformat(), loss_window_qualification='conservative')
            finally:
                restore.close()
            self.installation.reload_env()
            # Previous images are already on this host; the unchanged normal
            # Compose model must resolve to their exact retained immutable IDs.
            roster = ledger.snapshot.get('previous_roster')
            if roster is None:
                roster = runtime.create(dict(backup.b['images']), previous=True)
        else:
            roster = doc.get('previous_roster')
            require(isinstance(roster, dict) and set(roster) == {'app', 'postgres', 'clickhouse'},
                    ReasonCode.RECOVERY_REQUIRED)
        # Restore I/O and previous-container creation may outlast discovery's
        # short verification window. Reverify the retained signed source under
        # a new bounded window, still capped by the ORIGINAL recovery deadline.
        # Never turn an expired forward probe into permission to start unsigned A.
        lifecycle._verification_window(self.installation)
        with runtime.observation_session(lifecycle,
                lifecycle.baseline.evidence(lifecycle.head.release), 'restored',
                self.settings.automation_policy, previous=True) as session:
            runtime.start_and_wait(roster, self.settings.automation_policy, previous=True,
                ready_callback=lambda: session.ready(roster))
            session.check()
            return self._complete_automatic_restore_locked(lifecycle, ledger, expected, record, session.result)

    def _complete_automatic_restore_locked(self, lifecycle, ledger, expected, record, observation):
        from .contracts import RestorationResult
        from .schedule import utc
        doc = ledger.snapshot
        ledger.checkpoint('restore_verified', loss_window_start=ledger.backup_seal.snapshot_at.isoformat(),
            loss_window_end=ledger.snapshot.get('loss_window_end', lifecycle.clock().isoformat()),
            loss_window_qualification='conservative')
        failure_record = ledger.snapshot.get('failure_record')
        if failure_record is None and record is not None:
            failure = ledger.snapshot.get('forward_failure', {})
            stage = failure.get('stage', record.get('stage', 'prepare'))
            interrupted = failure.get('interrupted', not bool(failure))
            failure_record = dict(record, stage=stage,
                status=('interrupted_' if interrupted else 'failed_') +
                    ('during_rollout' if stage == 'rollout' else 'before_rollout'),
                finished_at=failure.get('failed_at', _now()))
            ledger.checkpoint(failure_record=failure_record)
        if failure_record is not None:
            self._automatic_receipt_locked(failure_record, ledger, 'forward_receipt')
        saved = ledger.snapshot.get('restore_record')
        if saved is None:
            saved = dict(kind='restore', status='restored', stage='rollout', run_id=doc['run_id'],
                # This receipt certifies the completed readiness boundary, not
                # the later time spent materializing metadata. The observation
                # is genuine validated evidence from the held runtime session.
                started_at=doc['expectation']['reserved_at'], finished_at=utc(observation.facts.now).strftime('%Y-%m-%dT%H:%M:%SZ'),
                from_version=expected.source_version, target_version=lifecycle.target.version,
                manifest_hash=expected.target_manifest_hash, **self._identity(),
                target_image_ids={'app': doc['previous_roster']['app']['Image']})
            ledger.checkpoint(restore_record=saved)
        path = self._automatic_receipt_locked(saved, ledger, 'restore_receipt')
        if self.state.read_journal() is not None:
            self.state.clear_journal()  # inverse retained in ledger + RestoreIntent
        lifecycle.complete_restore(self, path)
        ledger.checkpoint('restore_metadata_committed')
        ledger.checkpoint('restored')
        self.automatic_outcome = 'restored'
        return RestorationResult(expected.attempt_id, 'restored', expected.source_version,
            lifecycle.target.version, 'restored', ledger.backup_seal, observation.facts.runtime_observation,
            reason_codes=(ReasonCode.RESTORED_AFTER_UPDATE_FAILURE,))

    def resume_automatic_locked(self, *, lifecycle, ledger):
        """Resume retained authority ONLY. Caller holds runner.lock -> update.lock.

        No release discovery, controls/window test, forward apply or pull. A
        sealed interrupted forward attempt chooses restore; an accepted target
        can only finish metadata. Never reset a supervisor's uncertainty flag.
        """
        from .orchestrator import require
        from .contracts import RestorationResult
        require(self.state.lock_holder(automation=True) is not None, ReasonCode.RECOVERY_REQUIRED)
        policy = self.settings.automation_policy
        supervisor = self.installation.runner
        original_deadline = supervisor.deadline
        supervisor.deadline = min(original_deadline, time.monotonic() + policy.required_recovery_budget_seconds)
        try:
            ledger = self._open_automatic_ledger(ledger)
            self.automatic_ledger = ledger
            lifecycle.ledger, lifecycle.backup_seal = ledger, ledger.backup_seal
            if ledger.backup_seal is not None:
                # Sealing precedes publication into the engine's proof leaf.
                # An interruption in that gap still owns a genuine snapshot.
                import hashlib
                from .standalone_backup import _directory, _read
                with _directory(ledger.directory) as directory:
                    proof_raw = _read(directory, 'proof.json')
                require(hashlib.sha256(proof_raw).hexdigest() == ledger.backup_seal.proof_sha256,
                        ReasonCode.BACKUP_INVENTORY_MISMATCH)
                lifecycle.backup_raw = proof_raw
            if ledger.direction == 'success_decided':
                return self.finalize_automatic_success_locked(lifecycle=lifecycle, ledger=ledger)
            if ledger.backup_seal is None:
                backup = lifecycle.backup(self.installation)
                from .backup_proof import BackupExpectation
                from datetime import datetime
                e = dict(ledger.snapshot['expectation'])
                e['reserved_at'] = datetime.fromisoformat(e['reserved_at'])
                e['covered_domains'] = tuple(e['covered_domains'])
                backup.abort_quiescence(BackupExpectation(**e), ledger)
                observation = lifecycle.verify_current(self.installation)
                self.automatic_outcome = 'failed_available'
                return RestorationResult(e['attempt_id'], 'failed_available', e['source_version'],
                    lifecycle.target.version, 'failed_available', observation=observation.facts.runtime_observation)
            ledger.resume_direction()
            return self.restore_backup_locked(lifecycle=lifecycle, ledger=ledger)
        except (Exception, Interrupted, KeyboardInterrupt) as exc:
            self.automatic_outcome = 'recovery_required'
            if self.automation_guard is not None:
                self.automation_guard.recovery_required = True
            # A poisoned checkpoint must be re-opened; never replace its last
            # verified phase or erase an unresolved remote operation.
            try:
                self._open_automatic_ledger(ledger).block()
            except (Exception, Interrupted, KeyboardInterrupt):
                pass
            from .orchestrator import GuardRefused
            raise GuardRefused(ReasonCode.RECOVERY_REQUIRED) from exc
        finally:
            supervisor.deadline = original_deadline

    @contextmanager
    def start_automatic_target_locked(self, *, ledger, plan, estimate, observe):
        """Select/create/start B under an already-held automatic update lock.

        Shared by the enrolled transaction and bounded producer rehearsals.
        The caller owns admission and the sealed backup; this entry never
        invents either. It retains the shipping envcommit, rollout guard, exact
        images/model/stores, deadline, stall, watermark and stability checks.
        observe(runtime) supplies the read-only readiness session; acceptance
        and metadata remain with the caller while this session/lock stay held.
        """
        from .orchestrator import require, GuardRefused
        from .contracts import AutomaticUpdateEstimate
        import hashlib
        guard = self.automation_guard
        require(self.state.lock_holder(automation=True) is not None, ReasonCode.RECOVERY_REQUIRED)
        require(guard is not None and guard.automatic is not None and self.built_in_backup is not None,
                ReasonCode.CONTROL_INVALID)
        require(isinstance(estimate, AutomaticUpdateEstimate)
                and type(estimate.protected_restore_reserve_bytes) is int
                and estimate.protected_restore_reserve_bytes >= self.settings.automation_policy.disk_watermark_reserve_bytes,
                ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        require(isinstance(guard.approved_env_sha256, str)
                and re.fullmatch(r'[0-9a-f]{64}', guard.approved_env_sha256) is not None,
                ReasonCode.ENV_CONFLICT)
        record = self.active_record
        require(record is not None and record.get('automatic_transaction') is True
                and record['run_id'] == ledger.snapshot['run_id']
                and ledger.backup_seal is not None and ledger.direction == 'forward_pending'
                and ledger.snapshot['pending'] is None, ReasonCode.RECOVERY_REQUIRED)
        lifecycle, policy = guard.automatic, self.settings.automation_policy
        try:
            inverse = envfile.recovery_record(self._env_path(), record['env_changes'],
                expected_before_sha256=guard.approved_env_sha256)
        except envfile.EnvPreimageError:
            raise GuardRefused(ReasonCode.ENV_CONFLICT) from None
        record.update(journal_version=2, automation=self._automation_metadata(), env_recovery=inverse)
        self.state.write_journal(record)
        ledger.checkpoint('env_commit_pending', forward_record=record)
        op = ledger.begin('env_commit', identity={'run_id': record['run_id']},
            preconditions={'env_sha256': record['env_recovery']['before_sha256']},
            postconditions={'env_sha256': record['env_recovery']['after_sha256']})
        envfile.write_changes(self._env_path(), record['env_changes'], recovery=record['env_recovery'])
        self.installation.reload_env()
        record['stage'] = 'env_written'
        self.state.write_journal(record)
        actual = hashlib.sha256(self._env_path().read_bytes()).hexdigest()
        require(actual == record['env_recovery']['after_sha256'], ReasonCode.ENV_CONFLICT)
        ledger.complete(op, {'env_sha256': actual})
        ledger.checkpoint('env_selected', forward_record=record)
        expected = {i['variable']: i['target_tag'] for i in plan['components']}
        config, services = self._check_config(expected)
        ids = dict(self.built_in_backup.b['images'])
        inspector = self._automatic_inspection_installation()
        for service in services:
            local = inspector.image(service['image'])
            item = next(i for i in plan['components'] if i['component'] == service['component'])
            require(local is not None and (parse_image_ref(item['target_image']).name,
                item['target_digest']) in _digest_set(local['repo_digests']), ReasonCode.IMAGE_DIGEST_MISMATCH)
            ids[service['service']] = local['id']
        record['target_image_ids'] = {s['service']: ids[s['service']] for s in services}
        record['stage'] = 'pulled'  # prefetched, reverified, NEVER pulled cold
        self.state.write_journal(record)
        ledger.checkpoint(target_model=config)
        self._guard('before_rollout')
        record['stage'] = 'rollout'
        self.state.write_journal(record)
        ledger.checkpoint(forward_record=record)
        runtime = self._automatic_runtime(lifecycle, ledger, self.built_in_backup)
        roster = runtime.create(ids)
        with observe(runtime) as session:
            runtime.start_and_wait(roster, policy, estimate, ready_callback=lambda: session.ready(roster))
            self._dependencies_unchanged()
            yield runtime, roster, session

    def _apply_automatic_scope_locked(self, make_plan, manifest):
        """Reservation through success OR recovery inside the same update.lock."""
        from .orchestrator import require, GuardRefused
        from .contracts import AutomaticUpdateEstimate
        import hashlib
        guard, lifecycle = self.automation_guard, self.automation_guard.automatic
        supervisor, policy = self.installation.runner, self.settings.automation_policy
        original_deadline = supervisor.deadline
        try:
            self._guard('before_recovery')
            require(self.state.read_journal() is None, ReasonCode.AUTOMATIC_RECOVERY_PENDING)
            self.installation.check_ownership()
            plan = make_plan(self._history())
            self.active_plan = plan
            require(not plan.get('resume') and not plan.get('reasons'), ReasonCode.CONFIGURATION_CHANGED)
            if plan['decision'] == UP_TO_DATE:
                return None
            refusal = self._refusal(plan, manifest, None, None, stage=False, dry_run=False,
                backup_command=None, allow_no_backup=False)
            if refusal is not None:
                raise refusal
            require(original_deadline - time.monotonic() >= policy.required_cold_budget_seconds,
                    ReasonCode.INSUFFICIENT_WINDOW)
            changes = dict(plan['env_changes'])
            containers = self.installation.containers()
            record = dict(kind='apply', started_at=_now(), run_id=_run_id(), **self._identity(),
                target_version=plan['target_version'], from_versions=plan['current_versions'],
                manifest_hash=plan['manifest_hash'], manifest_source=plan['manifest_source'],
                channel=plan.get('channel'), signature=plan['signature'],
                components=self._component_records(plan['components'], None), env_changes=changes,
                found_values={k: self.installation.env.get(k) for k in changes},
                previous_values={k: self.installation.env.get(k) for k in changes},
                previous_images=self._snapshot_images(['app'], containers),
                automatic_transaction=True, stage='prepare', status='running')
            self.active_record = record
            self._guard('before_backup')  # reserves attempt + run + ledger BEFORE any stop
            ledger = self.automatic_ledger
            require(ledger is not None and ledger.snapshot['run_id'] == record['run_id'], ReasonCode.RECOVERY_REQUIRED)
            decision = lifecycle.preflight_decision
            require(decision is not None and decision.allowed and isinstance(decision.estimate, AutomaticUpdateEstimate),
                    ReasonCode.RESTORE_CAPACITY_UNKNOWN)
            # Slow signature/facts work and reservation do not preserve the
            # earlier tick allowance. Recheck immediately before the first stop.
            require(original_deadline - time.monotonic() >= max(policy.required_cold_budget_seconds,
                    decision.estimate.total_seconds), ReasonCode.INSUFFICIENT_WINDOW)
            # Independent protected reserve. Forward timeout never spends it.
            supervisor.deadline = original_deadline - policy.required_recovery_budget_seconds
            record['backup'] = self._backup(None, False, {})
            self.built_in_backup.retire_helpers(ledger)
            self._guard('after_backup')
            self._guard('before_env_commit')
            observe = lambda runtime: runtime.observation_session(lifecycle,
                lifecycle.baseline.evidence(lifecycle.intent.target), 'success', policy, decision.estimate)
            with self.start_automatic_target_locked(ledger=ledger, plan=plan,
                    estimate=decision.estimate, observe=observe) as (runtime, roster, session):
                observation = session.result
                lifecycle.baseline.validate_success_observation(lifecycle.intent, observation)
                # Retain the FULL validated observation before acceptance. A6
                # continues through slow validation/capture, not only startup.
                session.check()
                lifecycle.retain_success(self.installation, observation)
                session.check()
                record.update(status='succeeded', finished_at=_now())
                from .baseline import identity
                ledger.decide_success({'run_id': record['run_id'], 'record': record,
                    'observation_sha256': observation.facts.runtime_observation.sha256,
                    'successor_intent_sha256': identity(lifecycle.intent),
                    'env_sha256': observation.facts.runtime_observation.env_sha256,
                    'config_sha256': observation.facts.runtime_observation.config_sha256,
                    'storage_sha256': observation.facts.runtime_observation.storage_sha256})
            return self._finalize_automatic_success_locked(lifecycle, ledger)
        except (Exception, Interrupted, KeyboardInterrupt) as exc:
            ledger = self.automatic_ledger
            if ledger is not None:
                # Re-open authoritative immutable decisions even if a signal hit
                # between their fsync and replacement of the current checkpoint.
                supervisor.deadline = original_deadline
                try:
                    authority = self._open_automatic_ledger(ledger)
                    if authority.backup_seal is not None and authority.direction != 'success_decided':
                        reason = getattr(exc, 'reason_code', ReasonCode.READINESS_FAILED if isinstance(exc, NotReady)
                                         else ReasonCode.OPERATION_INTERRUPTED if isinstance(exc, (Interrupted, KeyboardInterrupt))
                                         else ReasonCode.PROCESS_FAILED)
                        try:
                            reason = ReasonCode(reason)
                        except (ValueError, TypeError):
                            reason = ReasonCode.PROCESS_FAILED
                        authority.decide_restore(reason.value)
                        authority.checkpoint(forward_failure={'reason_code': reason.value, 'failed_at': _now(),
                            'interrupted': isinstance(exc, (Interrupted, KeyboardInterrupt)),
                            'stage': (self.active_record or {}).get('stage', 'prepare')})
                    ledger = authority
                except (Exception, Interrupted, KeyboardInterrupt):
                    # Diagnostic/checkpoint failure cannot turn the safety
                    # action into env-only cleanup or erase the original seal.
                    pass
                try:
                    self.resume_automatic_locked(lifecycle=lifecycle, ledger=ledger)
                    if self.automatic_outcome == 'succeeded':
                        return self.automatic_receipt
                except (Exception, Interrupted, KeyboardInterrupt) as recovery_exc:
                    guard.recovery_required = True
                    raise GuardRefused(ReasonCode.RECOVERY_REQUIRED) from recovery_exc
            raise
        finally:
            supervisor.deadline = original_deadline

    def apply(self, make_plan: Callable[[List[Dict[str, Any]]], Dict[str, Any]], manifest: Mapping[str, Any],
              *, allow_no_backup: bool = False, backup_command: Optional[str] = None,
              dry_run: bool = False, stage: bool = False,
              release_binding: Optional[Mapping[str, Any]] = None,
              acknowledge_stale_stage: bool = False) -> Optional[Path]:
        """Plan with `make_plan(history)` (this install's receipts, oldest first) and carry the plan out.

        stage=True stops before restarting anything (see the module docstring).
        """

        # This run's own command, which a journal another install must recover first names to run again.
        words = (["apply"] + (["--dry-run"] if dry_run else []) + (["--stage"] if stage else [])
                 + (["--backup-command", backup_command] if backup_command else [])
                 + (["--allow-no-backup"] if allow_no_backup else []))
        again = self._command(" ".join(_quote(word) for word in words))
        with self.state.lock(**({'automation': True} if self.automation_guard is not None else {})):
            self._refuse_automatic_pending()
            if getattr(self.automation_guard, 'automatic', None) is not None:
                from .orchestrator import require
                require(not stage and not dry_run and not allow_no_backup and backup_command is None,
                        ReasonCode.CONTROL_INVALID)
                return self._apply_automatic_scope_locked(make_plan, manifest)
            notes: List[str] = []
            if not dry_run:
                self._guard('before_recovery')
                self.installation.check_ownership()
                self._recover_locked(again)
                if self.automation_guard is not None:
                    self.installation.reload_env()
            history = self._history()
            journal = self.state.read_journal() if dry_run else None
            if journal:
                # A dry run changes nothing, so it plans as the recovery the real run starts with would leave things.
                self._check_journal_owner(journal, again)
                preview, notes = preview_recovery(self.installation, journal, history)
                if finished_run(journal, history) is None:
                    history = history + [preview]
            plan = make_plan(history)
            self.active_plan = plan
            plan["notes"].extend(notes)
            # The unfinished update this run continues: one that stopped while containers were being replaced
            # (through any later run that stopped before restarting anything), or one staged earlier.
            resumed = self._unfinished_update(history) if plan["resume"] else None
            staged = resumed if resumed and resumed.get("status") == STAGED else None
            if resumed and resumed.get('manifest_hash') and resumed['manifest_hash'] != plan.get('manifest_hash'):
                from .orchestrator import GuardRefused
                raise GuardRefused(ReasonCode.IMMUTABLE_RELEASE_CHANGED)
            if staged and release_binding is not None:
                self._check_stage_binding(staged, release_binding, acknowledge_stale_stage, stage_only=stage)
            # Every refusal that needs no backup is decided before the plan is printed.
            refusal = self._refusal(plan, manifest, resumed, staged, stage=stage, dry_run=dry_run,
                                    backup_command=backup_command, allow_no_backup=allow_no_backup)
            # The plan this run carries out, worded as being done (not as the command to run next). A dry run
            # carries out nothing, and neither does a run refused here or one with nothing to do: their plan
            # names the command to run, as check's does.
            going_ahead = plan["decision"] != UP_TO_DATE and refusal is None and not dry_run
            # It says whether it reuses the release's images already on this host (verified again, never pulled).
            self.out.write(plan_text(plan, applying=going_ahead, stage=stage,
                                     on_host=self._images_on_host(plan) if going_ahead else None))
            if refusal is not None:
                raise refusal
            if plan["decision"] == UP_TO_DATE:
                self.say("Nothing to do: this install already runs the release.")
                return None
            if dry_run:
                self.say(self._dry_run_text(plan, resumed, stage))
                return None
            self._guard('before_backup')
            target = plan["target_version"]
            items = plan["components"]
            services = sorted({s for item in items for s in item["services"]})
            if resumed:
                backup = self._skip_backup(resumed, target, history)
            else:
                backup = self._backup(backup_command, allow_no_backup, {
                    "FUTURE_AGI_UPDATE_ROOT": str(self.installation.root),
                    "FUTURE_AGI_UPDATE_MODE": self.installation.mode,
                    "FUTURE_AGI_UPDATE_PROJECT": self.installation.project,
                    "FUTURE_AGI_UPDATE_ENV_FILE": str(self.installation.env_file),
                    "FUTURE_AGI_UPDATE_FROM_VERSION": ",".join(plan["current_versions"]),
                    "FUTURE_AGI_UPDATE_TO_VERSION": target,
                })
            self._guard('after_backup')
            env_changes = dict(plan["env_changes"])
            found_values = {var: self.installation.env.get(var) for var in env_changes}
            # A key the stopped update did not change was, before it, what this run found.
            previous_values = dict(found_values, **((resumed or {}).get("previous_values") or {}))
            containers = self.installation.containers()
            identity = self._identity()
            record: Dict[str, Any] = {
                "kind": "apply",
                "started_at": _now(),
                "run_id": _run_id(),
                **{field: identity[field] for field in IDENTITY_FIELDS},
                "target_version": target,
                "from_versions": plan["current_versions"],
                "manifest_hash": plan["manifest_hash"],
                "manifest_source": plan["manifest_source"],
                # The manifest's channel: a command that finishes this update later names it when the install
                # follows another channel by then (_finish_command, and the plan's reasons).
                "channel": plan.get("channel"),
                "signature": plan["signature"],
                "components": self._component_records(items, resumed),
                "env_changes": env_changes,
                # found_values: the keys as this run found them (a failure before the rollout puts them back);
                # previous_values: as they were before the original update (what rollback restores).
                "found_values": found_values,
                "previous_values": previous_values,
                "previous_images": (resumed or {}).get("previous_images") or self._snapshot_images(services, containers),
                "backup": backup,
            }
            # An empty mapping is the host adapter's "nothing to bind" (an ordinary apply): it writes no metadata,
            # while the staged-resume check above still ran for any non-None binding.
            if release_binding:
                saved_binding = (self.state.read_private('stage', resumed['run_id'])
                                 if resumed and resumed.get('run_id') else None)
                record['stage_binding'] = (saved_binding if saved_binding is not None else
                                           dict(release_binding, created_at=_now(), backup_proof_state='legacy_absent'))
            if resumed:
                record["resumes"] = resumed.get("_path")
            digests = {item["component"]: item["target_digest"] for item in items}
            names = {item["component"]: parse_image_ref(item["target_image"]).name for item in items}
            expected = {item["variable"]: item["target_tag"] for item in items}

            def verify(service: Dict[str, Any], info: Dict[str, Any]) -> Optional[str]:
                want = digests[service["component"]]
                found = set()
                for entry in info["repo_digests"]:
                    try:
                        ref = parse_image_ref(entry)
                    except Exception:
                        continue
                    if ref.name == names[service["component"]] and ref.digest:
                        found.add(ref.digest)
                if want not in found:
                    return (f"{service['image']} resolves to {', '.join(sorted(found)) or 'no registry digest'} on this "
                            f"host, not the signed {want}. Refusing to start it")
                return None

            # An image on the host already carrying the signed digest (pulled by the staging run, or the run that
            # stopped) is not pulled again.
            path = self._execute(record, expected, verify, pull=lambda s: True, app_ids=self._app_ids(containers),
                                 on_host=lambda s, info: verify(s, info) is None, stage_only=stage, resumed=resumed)
            if stage:
                self.say(self._staged_text(target, expected, path))
            else:
                self.say(f"Updated to {target}. Receipt: {path}")
            return path

    @staticmethod
    def _component_record(item: Mapping[str, Any]) -> Dict[str, Any]:
        record = {k: item[k] for k in ("component", "variable", "services", "target_tag", "target_digest",
                                       "current_version")}
        # The tags the services resolved to before the update: rollback restores an unset key only when
        # Compose's default selected exactly what ran (the locally built :local).
        record["current_tags"] = list(item.get("current_tags") or [])
        return record

    def _component_records(self, items: List[Mapping[str, Any]],
                           resumed: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        records = [self._component_record(item) for item in items]
        if resumed:
            # Like previous_values and previous_images: the tags from before the original update. The resume's
            # plan saw the env file already selecting the target. A receipt without them leaves [], so rollback
            # falls back to the images that ran (previous_images, copied from it).
            before = {c.get("component"): c.get("current_tags") or []
                      for c in resumed.get("components") or [] if isinstance(c, Mapping)}
            for record in records:
                record["current_tags"] = list(before.get(record["component"], []))
        return records

    # ------------------------------------------------------------- rollback --
    @staticmethod
    def _tags_that_ran(items: List[Mapping[str, Any]], previous_images: Mapping[str, Any]) -> List[str]:
        """The tags a variable's services resolved to before the update.

        From the receipt's current_tags; a receipt written without them falls
        back to the tags of the images its containers ran.
        """

        tags: List[str] = []
        for item in items:
            if item.get("current_tags"):
                tags.extend(item["current_tags"])
                continue
            for service in item.get("services") or []:
                image = (previous_images.get(service) or {}).get("image")
                try:
                    tags.append(parse_image_ref(image).tag or "latest")
                except ManifestError:
                    continue
        return tags

    def _restore_values(self, receipt: Mapping[str, Any]) -> Tuple[Dict[str, Optional[str]], Dict[str, str]]:
        """The env values that select what ran before `receipt`'s update, and the tag each active key resolves to."""

        components = receipt.get("components") or []
        previous_images = receipt.get("previous_images") or {}
        values: Dict[str, Optional[str]] = {}
        expected: Dict[str, str] = {}
        for variable, previous in (receipt.get("previous_values") or {}).items():
            items = [i for i in components if i["variable"] == variable]
            services = [s for i in items for s in i.get("services") or []]
            if not services:
                # Pinned for a profile that is not running: nothing ran from it, so put the value back as it was.
                values[variable] = previous
                continue
            if previous and (_is_release_tag(previous) or previous == "local"):
                values[variable] = expected[variable] = previous
                continue
            tags = self._tags_that_ran(items, previous_images)
            if not previous and tags and all(tag == "local" for tag in tags):
                # Compose's default selected the image built from this checkout: unset (or empty) selects it again.
                values[variable], expected[variable] = previous, "local"
                continue
            versions = sorted({(previous_images.get(s) or {}).get("version", "") for s in services} - {""})
            versions = [v for v in versions if is_version(v)]
            if len(versions) != 1:
                raise Blocked(
                    f"cannot tell which release {variable} ran before the update (it was "
                    f"{previous or 'unset'}); set it in the env file by hand instead")
            gpu = any(i["component"] == "serving_gpu" for i in items)
            values[variable] = expected[variable] = versions[0] + ("-gpu" if gpu else "")
        return values, expected

    def _rollback_target(self, receipt_path: Optional[Path],
                         receipts: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str, Dict[str, str], str]:
        """The update a rollback undoes: (its receipt, that receipt's key, apply key -> first run's key, the update).

        An update is named by the key of its first run's receipt: an apply and
        the applies that resumed it (or finished it when it was staged) are
        one update. Refuses (Blocked) what cannot be rolled back.
        """

        if receipt_path is not None:
            target = self.state.load_receipt(receipt_path)
        else:
            applied = [r for r in receipts if r.get("kind") == "apply" and r.get("status") in ROLLBACKABLE]
            if not applied:
                raise Blocked("there is no update to roll back in this checkout")
            target = applied[-1]
        if target.get("kind") != "apply":
            raise Blocked("that receipt is a rollback; to undo it, apply the release again")
        if target.get("status") not in ROLLBACKABLE:
            raise Blocked(f"that update ended {target.get('status')}: it changed nothing, so there is nothing to roll back")
        mine = self._identity()
        if any(target.get(field) != mine.get(field) for field in IDENTITY_FIELDS):
            env_file = str(target.get("env_file"))
            raise Blocked(
                f"that receipt is for the {target.get('mode')} install using {env_file} (project "
                f"{target.get('project')}); rerun with the same flags ("
                f"{'--production, --env-file ' + _quote(env_file) if target.get('production') else 'without --production'})")
        keys = [_key(r["_path"]) for r in receipts]
        here = _key(target["_path"])
        later_receipts = receipts[keys.index(here) + 1:] if here in keys else []
        first: Dict[str, str] = {}
        for record in receipts:
            if record.get("kind") == "apply":
                resumes = _key(record["resumes"]) if record.get("resumes") else None
                first[_key(record["_path"])] = first.get(resumes, resumes) if resumes else _key(record["_path"])
        update = first.get(here, here)
        # The updates rolled back after that receipt, each with the rollback that undid it. A later rollback of the
        # same update that stopped while containers were being replaced leaves it being rolled back (the plan
        # blocks on it and names this rollback to finish it): such a receipt is what a run killed between its
        # final receipt and removing its journal used to leave, recovered a second time.
        undone: Dict[str, str] = {}
        for record in later_receipts:
            if record.get("kind") != "rollback" or not record.get("rolls_back"):
                continue
            rolled = _key(record["rolls_back"])
            if record.get("status") == "rolled_back":
                undone.setdefault(first.get(rolled, rolled), _key(record["_path"]))
            elif record.get("status") in RESUMABLE_STATUSES:
                undone.pop(first.get(rolled, rolled), None)
        if update in undone:
            raise Blocked(f"that update was already rolled back ({undone[update]})")
        newer = [r for r in later_receipts if r.get("kind") == "apply" and r.get("status") in ROLLBACKABLE
                 and first.get(_key(r["_path"])) not in undone]
        continued = [r for r in newer if first.get(_key(r["_path"])) == update]
        if continued:
            # The newest run of this update is the one whose receipt undoes all of it, whatever it ended as.
            last = _key(continued[-1]["_path"])
            status = continued[-1].get("status")
            if status == STAGED:
                later = f"was staged again by a later run ({last})"
            elif status in RESUMABLE_STATUSES:
                later = f"was continued by a later run ({last}) that also stopped"
            else:
                later = f"was finished by a later run ({last})"
            raise Blocked(f"that update {later}; to undo it, run `{self._rollback_command(last)}`")
        if newer:
            raise Blocked(f"a newer update ({_key(newer[-1]['_path'])}) ran after that one; roll that back first: "
                          f"`{self._rollback_command(_key(newer[-1]['_path']))}`")
        return target, here, first, update

    def _refuse_strays(self, strays: Mapping[str, str], to: str, again: str,
                       restored: Optional[Mapping[str, Collection[str]]] = None) -> None:
        """Refuse a rollback that would leave containers of disabled profiles' services on another release.

        Before anything changes, with the rule the plan applies before an
        update (plan._judge_strays) and _stale after the restart: a running
        container of an inactive profile's service whose version key the
        rollback moves (`strays`: key -> the tag it restores) and that does not
        run that release (on_release; its tag counts only for a component with
        no active service). `up` would leave it as it is next to the stack.
        One already on an image the rollback restores (`restored`: component
        -> the ids of the images that ran before the update, which the
        rollback verifies) runs that release, as _stale accepts it.
        """

        if not strays:
            return
        installation = self.installation
        config = installation.config()
        configured = config.get("services") or {}
        containers = [c for c in installation.containers() if not c.get("oneoff")]
        # classify_strays reads every profile's services (another `compose config`): asked only when needed.
        if not any(c["service"] not in configured and c.get("status") in _RUNNING for c in containers):
            return
        active = {service["component"] for service in installation.futureagi_services(config)}
        image_ids = {c["id"]: c.get("image_id") or "" for c in containers}
        blocking = []
        for entry in classify_strays(installation, containers, configured)["inactive"]:
            component = component_for(parse_image_ref(entry["image"]))  # classify_strays parsed it already
            want = strays.get(COMPONENT_VARIABLES[component]) if component else None
            if (entry["running"] and want
                    and not on_release(entry["image"], entry["version"], want, by_tag=component not in active)
                    and not self._on_restored_image([image_ids.get(cid, "") for cid in entry["ids"]],
                                                    (restored or {}).get(component or ""))):
                blocking.append(entry)
        if not blocking:
            return
        # Only with an incomplete service model (Compose could not resolve every profile) can an inactive entry's
        # service be one no compose file defines: Compose rejects its name, so it is removed by container id.
        defined = set(installation.service_model().get("services") or {})
        known = [entry for entry in blocking if entry["service"] in defined]
        unknown = [entry for entry in blocking if entry["service"] not in defined]
        names = sorted({entry["service"] for entry in blocking})
        one = len(names) == 1
        maybe = sorted({entry["service"] for entry in unknown})
        alone = len(maybe) == 1
        if not unknown:
            why = " (a profile that is not enabled now)"
        elif not known:
            why = (f", and Compose could not resolve every profile, so bin/update cannot tell whether "
                   f"{', '.join(maybe)} {'belongs' if alone else 'belong'} to a profile that is not enabled now or "
                   f"{'is' if alone else 'are'} defined by no compose file any more")
        else:
            why = (f" ({', '.join(sorted({entry['service'] for entry in known}))}: a profile that is not enabled now; "
                   f"Compose could not resolve every profile, so bin/update cannot tell whether {', '.join(maybe)} "
                   f"{'belongs' if alone else 'belong'} to one or {'is' if alone else 'are'} defined by no compose "
                   "file any more)")
        remedies = []
        # A production Compose command run by hand carries the shell rule, as the plan's (plan._judge_strays).
        rule = f". {installation.shell_rule()}" if installation.production else ""
        if known:
            enable = sorted({entry["service"] for entry in known})
            them = "it" if len(enable) == 1 else "them"
            remedies.append(f"Enable the profile (COMPOSE_PROFILES in {self._env_name()}), or remove {them} with "
                            f"`{installation.compose_hint()} rm -sf {_words(enable)}`{rule}")
        if unknown:
            docker = " ".join(shlex.quote(word) for word in installation.docker)
            ids = _words(cid[:12] for entry in unknown for cid in entry["ids"])
            remove = (f"remove the containers of {', '.join(maybe)} with `{docker} rm -f {ids}`" if known and not rule
                      else f"Remove the containers of {', '.join(maybe)} with `{docker} rm -f {ids}`" if known else
                      f"Remove {'its' if one else 'their'} containers with `{docker} rm -f {ids}`")
            remedies.append(remove)
        remedy = (" " if rule else "; ").join(remedies)
        then = " Then run" if remedy.endswith(".") else ", then run"
        raise Blocked(
            f"{'service' if one else 'services'} {', '.join(names)} {'has' if one else 'have'} containers on Future "
            f"AGI images but {'is' if one else 'are'} not in the active Compose configuration{why}: "
            f"{stray_states(blocking)}. The rollback to {to} does not replace {'its' if one else 'their'} containers, "
            f"so they would keep running as they are next to it. {remedy}{then} `{again}` again. "
            "Nothing was changed")

    @staticmethod
    def _on_restored_image(image_ids: Sequence[str], restored: Optional[Collection[str]]) -> bool:
        """Whether every one of these containers runs an image the rollback restores (by image id, as _stale)."""

        return bool(restored) and bool(image_ids) and all(image_id in restored for image_id in image_ids)

    @staticmethod
    def _restored_images(target: Mapping[str, Any]) -> Dict[str, set]:
        """component -> the ids of the images its services ran before `target`'s update (what the rollback verifies)."""

        previous_images = target.get("previous_images") or {}
        restored: Dict[str, set] = {}
        for item in target.get("components") or []:
            if not isinstance(item, Mapping):
                continue
            ids = restored.setdefault(str(item.get("component")), set())
            for service in item.get("services") or []:
                image_id = (previous_images.get(service) or {}).get("image_id")
                if image_id:
                    ids.add(image_id)
        return restored

    def _recovered_only(self, receipt_path: Optional[Path], recovered: Mapping[str, Any]) -> None:
        """`rollback` without --yes that has just recovered an interrupted run (`recovered`): that was all it did.

        It names the rollback --yes would carry out only when there is one,
        after the same preview a rollback without --yes shows. When a rollback
        stopped while containers were being replaced (the run it recovered, or
        an earlier one a later run did not finish), finishing that rollback is
        needed: every update stays blocked until it is finished, and the
        message says so even when the rollback cannot go on now.
        """

        rolling = (recovered.get("kind") == "rollback" and recovered.get("status") in RESUMABLE_STATUSES
                   and bool(recovered.get("rolls_back")))
        history = self._history()

        def not_finished(rollback: Mapping[str, Any]) -> str:
            return (f"Nothing else was changed, but the rollback of {rollback.get('from_version')} is not finished: "
                    f"some containers may still run {rollback.get('from_version')} while others run "
                    f"{rollback.get('target_version')}, and bin/update refuses to update this install until it is "
                    "finished.")

        try:
            target, here, first, update = self._rollback_target(
                Path(str(recovered["rolls_back"])) if rolling else receipt_path, history)
            restore, expected = self._restore_values(target)
            self._check_readiness_settings()
        except Blocked as exc:
            # The stopped rollback, from the receipts as the plan reads them.
            pending = recovered if rolling else unfinished(history)
            if pending is not None and pending.get("kind") == "rollback" and pending.get("rolls_back"):
                self.say(f"{not_finished(pending)} It cannot be finished now: {exc}. Once that is resolved, run "
                         f"`{self._rollback_command(pending['rolls_back'])}`.")
            else:
                self.say("Nothing else was changed.")
            return
        command = self._rollback_command(here)
        stopped = self._stopped_rollback(history, first, update)
        quiet = (stopped is None and target.get("status") == STAGED
                 and self._never_started(target, self.installation.containers()))
        self._preview(target, restore, expected, quiet)
        refusal = None if quiet else self._check_deployment_files(target, expected, command, again=False)
        if rolling or stopped is not None:
            state = not_finished(recovered if rolling else stopped or {})
            self.say(f"{state} {refusal}" if refusal else f"{state} To finish it, run `{command}`.")
            return
        if refusal:
            self.say(f"Nothing else was changed. {refusal}")
            return
        values = ", ".join(_describe_value(k, v, expected.get(k)) for k, v in restore.items())
        if (recovered.get("kind") == "apply" and recovered.get("status") == "interrupted_during_rollout"
                and recovered.get("_path") and _key(recovered["_path"]) == _key(target.get("_path"))):
            # The update it recovered stopped while containers were being replaced: the stack may run a mix, and
            # either way out is a command (its own apply finishes it).
            version = target.get("target_version")
            self.say(f"Nothing else was changed, but the update to {version} is not finished: some containers may "
                     f"run {version} while others still run the versions from before it. To finish it, run "
                     f"`{self._finish_command(target)}`; to roll it back instead (to {values}), run `{command}`.")
            return
        self.say(f"Nothing else was changed. To roll back {target.get('target_version')} as well (to {values}), run "
                 f"`{command}`.")

    def _preview(self, target: Mapping[str, Any], restore: Mapping[str, Optional[str]], expected: Mapping[str, str],
                 quiet: bool) -> None:
        """What a rollback of `target` does, said before it asks for --yes (and before it starts)."""

        self.say(
            f"Rolling back {target.get('target_version')} to "
            + ", ".join(_describe_value(k, v, expected.get(k)) for k, v in restore.items())
            + f" ({self.installation.label}, project {self.installation.project}).")
        if quiet:
            self.say(f"The update to {target.get('target_version')} was staged and never started: the rollback puts "
                     "the version settings back and verifies the previous images, and restarts nothing.")
        self.say("Database migrations the update applied are not reverted. If the previous version cannot start "
                 "on the migrated data, restore the backup taken before the update.")

    @staticmethod
    def _stopped_rollback(receipts: List[Dict[str, Any]], first: Mapping[str, str],
                          update: str) -> Optional[Dict[str, Any]]:
        """A rollback of `update` that stopped while containers were being replaced (the run to finish), if any."""

        stopped = unfinished(receipts)
        if (stopped and stopped.get("kind") == "rollback" and stopped.get("rolls_back")
                and first.get(_key(stopped["rolls_back"]), _key(stopped["rolls_back"])) == update):
            return stopped
        return None

    @staticmethod
    def _never_started(target: Mapping[str, Any], containers: Iterable[Mapping[str, Any]]) -> bool:
        """Whether `target` is a staged update whose services all still run the images from before it.

        Then nothing of it ever ran, and its rollback restarts nothing: every
        container of each of its services runs the image id it ran when the
        update was staged.
        """

        if target.get("status") != STAGED:
            return False
        previous_images = target.get("previous_images") or {}
        by_service: Dict[str, List[Mapping[str, Any]]] = {}
        for container in containers:
            if not container.get("oneoff"):
                by_service.setdefault(str(container.get("service")), []).append(container)
        for item in target.get("components") or []:
            if not isinstance(item, Mapping):
                return False
            for service in item.get("services") or []:
                want = (previous_images.get(service) or {}).get("image_id")
                found = by_service.get(service) or []
                if not want or not found or any(c.get("image_id") != want for c in found):
                    return False
        return True

    def _runs_release(self, containers: Iterable[Mapping[str, Any]], components: Iterable[Any], version: str) -> bool:
        """Whether the stack already runs `version` for every service of `components`.

        The plan's judgement of a staged update restarted outside bin/update
        (plan.build_plan): by its images' version labels (on_release), each
        service either runs it (every container that runs or restarts is
        running on it, none unhealthy) or finished on it (none runs, and every
        one exited 0 on it: a one-shot job), and at least one runs it. A
        container that never started, crashed or restarts runs nothing.
        """

        by_service: Dict[str, List[Mapping[str, Any]]] = {}
        for container in containers:
            if not container.get("oneoff"):
                by_service.setdefault(str(container.get("service")), []).append(container)
        labels: Dict[str, str] = {}

        def on_it(container: Mapping[str, Any], tag: str) -> bool:
            image_id = str(container.get("image_id") or "")
            if image_id and image_id not in labels:
                labels[image_id] = str((self.installation.image(image_id) or {}).get("version") or "")
            return bool(image_id) and on_release(str(container.get("image") or ""), labels[image_id], tag,
                                                 by_tag=False)

        services = [(service, str(item.get("component"))) for item in components if isinstance(item, Mapping)
                    for service in item.get("services") or []]
        if not services or not is_version(version):
            return False
        runs = False
        for service, component in services:
            tag = component_tag({"version": version}, component)
            found = by_service.get(service) or []
            live = [c for c in found if c.get("status") in _RUNNING]
            if live:
                if not all(c.get("status") == "running" and c.get("health") != "unhealthy" and on_it(c, tag)
                           for c in live):
                    return False
                runs = True
            elif not found or not all(c.get("status") == "exited" and c.get("exit_code") == 0 and on_it(c, tag)
                                      for c in found):
                return False
        return runs

    # ------------------------------------------------- deployment files --
    @staticmethod
    def _previous_release(target: Mapping[str, Any], expected: Mapping[str, str]) -> Optional[str]:
        """The release a rollback of `target` goes back to, whose deployment files its images need.

        The version the FUTURE_AGI_VERSION component ran before the update
        (what the rollback restores: its previous value, or the version label
        of the image that ran), else the one version the update came from.
        """

        restored = expected.get("FUTURE_AGI_VERSION")
        if restored and is_version(restored):
            return restored
        value = (target.get("previous_values") or {}).get("FUTURE_AGI_VERSION")
        if isinstance(value, str) and is_version(value):
            return value
        previous_images = target.get("previous_images") or {}
        labels = {str((previous_images.get(service) or {}).get("version") or "")
                  for item in target.get("components") or []
                  if isinstance(item, Mapping) and item.get("variable") == "FUTURE_AGI_VERSION"
                  for service in item.get("services") or []}
        labels = {label for label in labels if is_version(label)}
        if len(labels) == 1:
            return labels.pop()
        versions = {str(version) for version in target.get("from_versions") or [] if is_version(str(version))}
        return versions.pop() if len(versions) == 1 else None

    def _git(self, *args: str) -> "subprocess.CompletedProcess[bytes]":
        """git in this checkout (never in a repository the environment points it at); raises OSError without git."""

        if self.automation_guard is not None:
            return self.installation.runner(['git', '-C', str(self.installation.root), '--literal-pathspecs', *args],
                                            capture_output=True, timeout=GIT_TIMEOUT_SECONDS, check=False,
                                            env=self.installation.environ)
        env = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_VARIABLES}
        return subprocess.run(["git", "-C", str(self.installation.root), "--literal-pathspecs", *args],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                              timeout=GIT_TIMEOUT_SECONDS, check=False)

    def _has_tag(self, version: Optional[str]) -> bool:
        """Whether this checkout has the release tag `version` (False when git cannot say)."""

        if not version or not is_version(version):
            return False
        try:
            return self._git("rev-parse", "--verify", "--quiet", f"refs/tags/{version}^{{commit}}").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _git_failed(result: "subprocess.CompletedProcess[bytes]") -> str:
        lines = [line.strip() for line in result.stderr.decode("utf-8", "replace").splitlines() if line.strip()]
        return f"`git {result.args[4]}` failed" + (f": {lines[-1]}" if lines else f" with exit code {result.returncode}")

    def _paths_at(self, ref: str,
                  paths: Sequence[str]) -> "Tuple[List[str], Optional[subprocess.CompletedProcess[bytes]]]":
        """Which of these paths (a file, or a directory with a file below it) exist at `ref`, in their order.

        The second value is the failed `git ls-tree`, if it failed (then no
        path is said to exist).
        """

        if not paths:
            return [], None
        listed = self._git("ls-tree", "-r", "-z", "--name-only", ref, "--", *paths)
        if listed.returncode != 0:
            return [], listed
        files = [os.fsdecode(name) for name in listed.stdout.split(b"\0") if name]
        return [path for path in paths if any(name == path or name.startswith(path + "/") for name in files)], None

    def _release_paths(self, tag: str) -> Tuple[List[str], str]:
        """The deployment files release `tag` names itself: its deployment-files.txt (when it has one), then
        the relative bind-mount sources and env files of its compose files (compose_paths).

        Returns them and "" (or [] and why git could not read them).
        """

        meta = [DEPLOYMENT_FILE_LIST, *RELEASE_COMPOSE_FILES]
        present, failed = self._paths_at(tag, meta)
        if failed is not None:
            return [], self._git_failed(failed)
        if not present:
            return [], ""
        archive = self._git("archive", "--format=tar", tag, "--", *present)
        if archive.returncode != 0:
            return [], self._git_failed(archive)
        paths: List[str] = []
        with tempfile.TemporaryDirectory(prefix="futureagi-rollback-") as temp:
            _extract_tar(archive.stdout, Path(temp))
            if DEPLOYMENT_FILE_LIST in present:
                try:
                    paths.extend(read_deployment_file_list(Path(temp)))
                except ManifestError:
                    pass  # an empty or unreadable list names nothing; the compose files still do
            for name in RELEASE_COMPOSE_FILES:
                if name in present:
                    paths.extend(compose_paths((Path(temp) / name).read_text(encoding="utf-8", errors="replace")))
        return paths, ""

    def _deployment_differences(self, previous: Optional[str]) -> Dict[str, Any]:
        """How this checkout's deployment files compare with release `previous`'s (its git tag).

        The paths compared: the checkout's own deployment-files.txt, then the
        deployment files the release names itself (_release_paths: its
        deployment-files.txt and what its compose files bind-mount or read as
        env files), never an install's env file. Each that exists at the tag
        is compared by its deployment hash (manifest.hash_path, what a release
        records) with the checkout's, from `git archive` of the tag: one
        missing or different here is in "differ". A path of the checkout's
        list the tag lacks counts only when it exists here and the compose
        files here reference it: "absent". "extra": the files this checkout's
        commit has below a differing directory that the tag lacks (`git
        checkout <tag> -- <directory>` leaves them). "note" says why nothing
        could be compared (no release, no git, not a work tree, no such tag).
        """

        result: Dict[str, Any] = {"previous": previous, "differ": [], "absent": [], "untracked": [], "extra": [],
                                  "note": ""}
        if not previous:
            result["note"] = "bin/update cannot tell which release ran before the update"
            return result
        root = self.installation.root
        try:
            entries = read_deployment_file_list(root)
        except ManifestError as exc:
            result["note"] = str(exc)
            return result
        try:
            # Git is used (and git commands printed) only in a work tree whose top is this checkout.
            inside = self._git("rev-parse", "--is-inside-work-tree")
            if inside.returncode != 0 or inside.stdout.strip() != b"true":
                refused = b"dubious ownership" in inside.stderr
                result["note"] = ("git refuses to work in this checkout, which another user owns" if refused else
                                  "this checkout is not a git work tree")
                return result
            top = self._git("rev-parse", "--show-toplevel")
            if top.returncode != 0 or Path(os.fsdecode(top.stdout.strip())).resolve() != root:
                result["note"] = "this checkout is not a git work tree"
                return result
            if not self._has_tag(previous):
                result["note"] = (f"this checkout has no tag {previous}; `{cd_prefix(self.installation)}git fetch "
                                  "--tags` fetches the release tags")
                return result
            tag = f"refs/tags/{previous}"
            theirs, failure = self._release_paths(tag)
            if failure:
                result["note"] = failure
                return result
            # An install's env file is never a release's file to restore, whichever install's it is.
            env_files = {self._env_name(), ".env", PRODUCTION_ENV}
            compared = list(entries)
            for path in theirs:
                if (path not in env_files and path not in compared
                        and not any(path.startswith(entry + "/") for entry in compared)):
                    compared.append(path)
            there, failed = self._paths_at(tag, compared)
            if failed is not None:
                result["note"] = self._git_failed(failed)
                return result
            if there:
                archive = self._git("archive", "--format=tar", tag, "--", *there)
                if archive.returncode != 0:
                    result["note"] = self._git_failed(archive)
                    return result
                with tempfile.TemporaryDirectory(prefix="futureagi-rollback-") as temp:
                    _extract_tar(archive.stdout, Path(temp))
                    result["differ"] = [entry for entry in there if hash_path(Path(temp), entry) != hash_path(root, entry)]
            directories = [entry for entry in result["differ"] if (root / entry).is_dir()]
            result["untracked"] = self._untracked(directories)
            # One git no longer tracks (`git rm --cached`) is untracked: the hint to move it out covers it.
            result["extra"] = [name for name in self._extra_tracked(tag, directories)
                               if name not in result["untracked"]]
        except FileNotFoundError:
            result["note"] = "git is not installed"
            return result
        except (OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
            result["note"] = f"they could not be compared ({exc})"
            return result
        compose = ""
        for entry in entries:
            name = PurePosixPath(entry).name
            if name.startswith("docker-compose") and name.endswith((".yml", ".yaml")) and (root / entry).is_file():
                try:
                    compose += (root / entry).read_text(encoding="utf-8", errors="replace") + "\n"
                except OSError:
                    continue
        result["absent"] = [entry for entry in entries
                            if entry not in there and (root / entry).exists() and entry in compose]
        return result

    def _restore_steps(self, check: Mapping[str, Any], undone: Optional[str]) -> Tuple[str, str, str]:
        """(the paths that differ, how to give the checkout the previous release's, the command that undoes that)."""

        cd = cd_prefix(self.installation)
        root = self.installation.root
        previous, differ, absent = check["previous"], list(check["differ"]), list(check["absent"])
        listed = ", ".join([path if (root / path).exists() else f"{path} (missing)" for path in differ]
                           + [f"{path} (not in {previous})" for path in absent])
        steps = []
        if differ:
            steps.append(f"`{cd}git checkout {previous} -- {_words(differ)}`")
        extra = list(check.get("extra") or [])
        if extra:
            # The checkout above only lays the release's files over a directory: a file it lacks stays.
            steps.append(f"remove what {previous} does not have with `{cd}git rm -q -f -- {_words(extra)}`")
        if absent:
            steps.append(f"move {', '.join(absent)} out of the checkout")
        restore = " and ".join(steps)
        back = undone if self._has_tag(undone) else "HEAD"
        # A path the undone release does not have (one only the previous release names) cannot be checked out
        # from it: the restore put it in git's index, and `git rm` takes it out again.
        paths = differ + absent
        try:
            kept, failed = self._paths_at(back, paths)
        except (OSError, subprocess.SubprocessError):
            kept, failed = paths, None
        if failed is not None:
            kept = paths
        gone = [path for path in paths if path not in kept]
        undo = []
        if kept:
            undo.append(f"`{cd}git checkout {back} -- {_words(kept)}`")
        if gone:
            undo.append(f"`{cd}git rm -r -f -q --ignore-unmatch -- {_words(gone)}`")
        return listed, restore, " and ".join(undo)

    def _untracked(self, directories: Sequence[str]) -> List[str]:
        """The files git does not track (ignored ones too) below these directories that their hash counts."""

        if not directories:
            return []
        listed = self._git("ls-files", "--others", "-z", "--", *directories)
        if listed.returncode != 0:
            return []
        found = []
        for name in (os.fsdecode(entry) for entry in listed.stdout.split(b"\0") if entry):
            for directory in directories:
                parts = tuple(PurePosixPath(name).parts)
                inside = tuple(PurePosixPath(directory).parts)
                if parts[:len(inside)] == inside and not _skipped_in_directory_hash(parts[len(inside):]):
                    found.append(name)
                    break
        return sorted(found)

    def _extra_tracked(self, tag: str, directories: Sequence[str]) -> List[str]:
        """The files below these directories that this checkout's commit has and release `tag` does not.

        `git checkout <tag> -- <directory>` writes the tag's files over the
        directory and leaves these (a file the undone release added, say), so
        they keep it differing: the restore removes them with `git rm` (the
        undone release's checkout brings them back). Only files that are here
        and that the directory's hash counts are named.
        """

        if not directories:
            return []
        here = self._git("ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *directories)
        there = self._git("ls-tree", "-r", "-z", "--name-only", tag, "--", *directories)
        if here.returncode != 0 or there.returncode != 0:
            return []
        theirs = {name for name in there.stdout.split(b"\0") if name}
        root = self.installation.root
        found = []
        for name in (os.fsdecode(entry) for entry in here.stdout.split(b"\0") if entry and entry not in theirs):
            if not ((root / name).exists() or (root / name).is_symlink()):
                continue
            parts = tuple(PurePosixPath(name).parts)
            for directory in directories:
                inside = tuple(PurePosixPath(directory).parts)
                if parts[:len(inside)] == inside and not _skipped_in_directory_hash(parts[len(inside):]):
                    found.append(name)
                    break
        return sorted(found)

    @staticmethod
    def _untracked_hint(check: Mapping[str, Any]) -> str:
        """Files git does not track inside a directory that differs: a checkout leaves them, so they keep it differing."""

        untracked = list(check.get("untracked") or [])
        if not untracked:
            return ""
        one = len(untracked) == 1
        return (f" {', '.join(untracked)} {'is' if one else 'are'} not tracked by git, so `git checkout` leaves "
                f"{'it' if one else 'them'} in place: move {'it' if one else 'them'} out of the checkout as well.")

    def _check_deployment_files(self, target: Mapping[str, Any], expected: Mapping[str, str], command: str, *,
                                again: bool = True) -> Optional[str]:
        """Before a rollback restarts the stack: why it may not (None: it may; a note says when it could not tell).

        The previous release's images need its deployment files: while the
        checkout's differ, the refusal names the `git checkout` that restores
        them, then `command`, and the one that brings the undone release's
        back before the next update.
        """

        check = self._deployment_differences(self._previous_release(target, expected))
        previous = check["previous"]
        if check["note"]:
            what = f"{previous}'s" if previous else "the previous release's"
            images = f"{previous}'s images" if previous else "the previous images"
            self.say(f"Note: this checkout's deployment files could not be checked against {what} ({check['note']}). "
                     f"The rollback starts {images} with the compose files in this checkout as they are.")
            return None
        if not (check["differ"] or check["absent"]):
            return None
        listed, restore, back = self._restore_steps(check, target.get("target_version"))
        steps = (f"Restore {previous}'s versions with {restore} (bin/update and your env file stay as they are)"
                 if check["differ"] else restore[:1].upper() + restore[1:])
        return (f"The rollback starts {previous}'s images, which need {previous}'s deployment files; these differ in "
                f"this checkout: {listed}. {steps}, then run `{command}`{' again' if again else ''}. Before updating "
                f"again, run {back}.{self._untracked_hint(check)}")

    def _never_started_text(self, record: Mapping[str, Any], target: Mapping[str, Any],
                            expected: Mapping[str, str], path: Path) -> str:
        """The rollback of a staged update that never started is done; whether the checkout needs older files.

        Nothing restarted, so nothing is refused: the comparison with the
        previous release's deployment files (or why it could not be made) is
        a note for the next restart. A production install's restart command
        is followed by the shell rule (Installation.shell_rule).
        """

        restored = record["target_version"]
        text = (f"Rolled back to {restored} without restarting anything: the update to {target.get('target_version')} "
                f"was staged and never started, so the stack never changed (it still runs {restored}), and "
                f"{self._env_name()} selects {self._selects(record.get('env_changes') or {})} again.")
        check = self._deployment_differences(self._previous_release(target, expected))
        previous = check["previous"]
        if not check["note"] and not (check["differ"] or check["absent"]):
            return f"{text} Receipt: {path}"
        if check["note"]:
            what = f"{previous}'s" if previous else "the previous release's"
            found = f"Note: this checkout's deployment files could not be checked against {what} ({check['note']})."
            steps, undo, untracked = f"make sure they are {what}", "", ""
        else:
            listed, restore, undo = self._restore_steps(check, target.get("target_version"))
            found = f"This checkout's deployment files differ from {previous}'s: {listed}."
            steps = f"restore {previous}'s versions with {restore}" if check["differ"] else restore
            untracked = self._untracked_hint(check)
        hint, cd = self.installation.compose_hint(), cd_prefix(self.installation)
        if self.installation.production:
            later = (f"Before you restart the stack, {steps}, then restart it with `{hint} up -d --no-build --wait`. "
                     f"{self.installation.shell_rule()}" + (f" Before updating again, run {undo}." if undo else ""))
        else:
            later = (f"Before you restart the stack (`{hint} up -d`) or run `{cd}./bin/install`, {steps}"
                     + (f"; before updating again, run {undo}." if undo else "."))
        return f"{text} {found} {later}{untracked} Receipt: {path}"

    def rollback(self, receipt_path: Optional[Path], *, yes: bool = False) -> Optional[Path]:
        """Undo an update (the last one, or the one `receipt_path` names); returns the rollback's receipt.

        Without `yes` it only says what it would do. One that recovered an
        interrupted run first stops there (returns None): recovering is all
        it did, which is what another install's journal asks for. So does one
        that only removed the journal of a run that had written its receipt
        (finished_run): a finished run's says nothing more, and a run that had
        stopped is treated as the run it recovered.

        A rollback that restarts the stack first compares the checkout's
        deployment files with the previous release's (_check_deployment_files)
        and refuses while they differ. A staged update that never started is
        rolled back without restarting anything.
        """

        words = (["rollback"] + (["--receipt", str(receipt_path)] if receipt_path is not None else [])
                 + (["--yes"] if yes else []))
        again = self._command(" ".join(_quote(word) for word in words))
        with self.state.lock(**({'automation': True} if self.automation_guard is not None else {})):
            self._refuse_automatic_pending()
            if self.automation_guard is not None:
                from .orchestrator import GuardRefused
                raise GuardRefused(ReasonCode.ROLLBACK_INELIGIBLE)
            self._guard('before_recovery')
            self.installation.check_ownership()
            # A journal whose run already wrote its receipt: recovering only removes it (recover). Another install's
            # journal is refused first, as recover refuses it.
            journal = self.state.read_journal()
            if journal:
                self._check_journal_owner(journal, again)
            history_before_recovery = self._history() if journal else []
            recovered = self._recover_locked(again)
            # Match only after real recovery's header/identity/operation gate.
            # Use the earlier history, not a receipt recovery may just have added.
            left = finished_run(journal, history_before_recovery) if journal else None
            if recovered is None and left is not None and not yes:
                if left.get("status") in FINISHED_STATUSES:
                    # That run finished: removing its journal was all there was to do.
                    self.say("Nothing else was changed.")
                    return None
                # A recovery (or a failed run) killed after writing its receipt: that receipt is the recovered run.
                recovered = left
            if recovered is not None and not yes:
                self._recovered_only(receipt_path, recovered)
                return None
            receipts = self._history()
            target, here, first, update = self._rollback_target(receipt_path, receipts)
            mine = self._identity()
            restore, expected = self._restore_values(target)
            self._check_readiness_settings()
            # Values with services behind them; an unset key that selects :local names no release.
            labels = sorted({v for k, v in restore.items() if v and k in expected}) or sorted(set(expected.values()))
            # A container of an inactive profile's service must run the restored release after the restart, for
            # each key the rollback moves off the update's release; an inactive pin counts at the value restored.
            selected = {item.get("variable"): item.get("target_tag") for item in target.get("components") or []
                        if isinstance(item, Mapping)}
            strays = dict(expected)
            strays.update({key: value for key, value in restore.items()
                           if key not in strays and value and _is_release_tag(value)})
            strays = {key: value for key, value in strays.items() if selected.get(key) != value}
            previous_images = target.get("previous_images") or {}
            # A rollback of this update that stopped while containers were being replaced: this run finishes it.
            stopped = self._stopped_rollback(receipts, first, update)
            staged = target.get("status") == STAGED
            # A staged update the stack never started: nothing to restart, and no deployment files to check.
            quiet = stopped is None and staged and self._never_started(target, self.installation.containers())
            self._preview(target, restore, expected, quiet)
            if not quiet:
                refusal = self._check_deployment_files(target, expected, again)
                if refusal:
                    raise Blocked(refusal)
            if not yes:
                raise Blocked("rollback not started: rerun with --yes to confirm")
            self._refuse_strays(strays, ",".join(labels), again, self._restored_images(target))
            services = sorted({s for item in target.get("components") or [] for s in item["services"]})
            found_values = {var: self.installation.env.get(var) for var in restore}
            containers = self.installation.containers()
            record: Dict[str, Any] = {
                "kind": "rollback",
                "started_at": _now(),
                "run_id": _run_id(),
                **{field: mine[field] for field in IDENTITY_FIELDS},
                "rolls_back": here,
                "from_version": target.get("target_version"),
                "target_version": ",".join(labels),
                "components": target.get("components") or [],
                "env_changes": restore,
                "found_values": found_values,
                "previous_values": dict(found_values),
                "previous_images": self._snapshot_images(services, containers),
            }

            def verify(service: Dict[str, Any], info: Dict[str, Any]) -> Optional[str]:
                before = previous_images.get(service["service"])
                if not before:
                    return None
                same_digest = bool(_digest_set(before.get("repo_digests") or []) & _digest_set(info["repo_digests"]))
                if service["ref"].tag == "local":
                    # Built on this host: no registry digest (or, with the containerd image store, one of its
                    # own). Either the digest or the image id must be the one that ran.
                    if same_digest or before.get("image_id") == info["id"]:
                        return None
                    return f"{service['image']} is not the image that ran before the update ({before.get('image_id')})"
                if before.get("repo_digests"):
                    if same_digest:
                        return None
                    return (f"{service['image']} does not resolve to the digest that ran before the update "
                            f"({', '.join(before['repo_digests'])}). Refusing to start it")
                if before.get("image_id") and before["image_id"] != info["id"]:
                    return f"{service['image']} is not the image that ran before the update ({before['image_id']})"
                return None

            def ran_before(service: Dict[str, Any], info: Dict[str, Any]) -> bool:
                # The image that ran before the update is still on the host (its digest, or its id when it had
                # none): not pulled again, so a rollback works while the registry cannot be reached.
                before = previous_images.get(service["service"]) or {}
                return bool(before.get("repo_digests") or before.get("image_id")) and verify(service, info) is None

            def adopt(service: Dict[str, Any]) -> Optional[str]:
                # The image that ran before the update, on the host under another tag (an install that followed
                # :latest, pinned by the update): tagged with the version restored, then verified like a pulled one.
                before = previous_images.get(service["service"]) or {}
                image_id = before.get("image_id")
                if not image_id or not before.get("repo_digests") or before.get("image") == service["image"]:
                    return None
                info = self.installation.image(image_id)
                if info is None or not (_digest_set(before["repo_digests"]) & _digest_set(info.get("repo_digests") or [])):
                    return None
                return str(info.get("id") or image_id)

            # The message of a rollback that stops before its restart tells a staged update the stack already runs
            # (restarted outside bin/update) from one still pending, as the plan does.
            undoing = dict(target, _already_runs=staged and not quiet and self._runs_release(
                containers, target.get("components") or [], str(target.get("target_version") or "")))
            path = self._execute(record, expected, verify, pull=lambda s: s["ref"].tag != "local",
                                 app_ids=self._app_ids(containers), on_host=ran_before, resumed=stopped,
                                 undoing=undoing, strays=strays, adopt=adopt, stage_only=quiet)
            if quiet:
                self.say(self._never_started_text(record, target, expected, path))
            else:
                self.say(f"Rolled back to {record['target_version']}. Receipt: {path}")
            return path
