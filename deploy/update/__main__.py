"""bin/update: check for, apply and roll back signed Future AGI releases.

Exit codes:
  0  done (or nothing to do)
  2  error: bad input, missing tool, unreadable state
  3  blocked: the plan refuses the update, confirmation is missing, or a short metadata lock stays busy
  4  the update failed before anything was restarted; nothing changed
  5  the update failed while containers were being replaced
  130 interrupted
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import hashlib
import pwd
import uuid
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .apply import JOURNAL_NOTE  # noqa: F401  (part of this module's interface)
from .apply import (FINISHED_STATUSES, Blocked, Interrupted, InterruptedRun, UpdateError, Updater, finished_run,
                    preview_recovery, recover_elsewhere, signals_as_interrupt, validate_journal_header,
                    validate_recovery)
from .enrollment import SETUP_POLICY_OPTIONS
from .envfile import EnvFileError
from .install import COMPONENT_VARIABLES, PRODUCTION_ENV, VERSION_VARIABLES, InstallError
from .manifest import ManifestError
from .plan import BLOCKED, build_plan, cd_prefix, command_prefix, install_command, plan_json, plan_text
from .readiness import NotReady
from .receipt import JOURNAL, STATE_DIR, StateDir, StateError, secret_values
from .settings import SettingsError, load_settings
from .trust import Verifier, VerificationError, fetch_manifest, remove_workdir

from .contracts import ReasonCode, VerificationSnapshot
from .discovery import DiscoveredRelease, DiscoveryResult
from .download import Downloader
from .host_facts import (EnrolledFacts, HostInstallation as Installation, binding_status_overlay,
                         check_status_deployment, daemon_space_available, installation_binding,
                         overlay_discovery, production_status_overlay, registered_status_overlay,
                         safe_path, selected_store, valid_docker_context)
from .orchestrator import GuardRefused, fingerprints, prepare_update, registered_installation, require
from .processes import ProcessError, ProcessSupervisor
from .public_status import PublicationConflict, PublicStatusPublisher, build_status, retry_busy as _retry_busy, stamp
from .receipt import StateBusy
from .runner import HostRunner
from .runner_state import LauncherConfig, Registration, StoreBusy, StoreError, TargetIdentity
from .scheduler_assets import render_scheduler
from .status import observe_status

SUPPRESS = argparse.SUPPRESS
COMMANDS = ("check", "apply", "rollback", "status")
AUTOMATION_COMMANDS = ("run", "download", "enable", "disable", "pause", "resume", "retry", "schedule", "reporting", "setup")
ALL_COMMANDS = COMMANDS + AUTOMATION_COMMANDS
# The commands that verify a manifest's signature (load_settings checks the cosign settings for them only).
VERIFYING_COMMANDS = ("check", "apply", "download")
# The directory a verifying run fetches its manifest into, in the state directory (verify-<pid>).
STALE_WORKDIR = re.compile(r"verify-([0-9]+)")

# Every option: the commands that take it, its help group (None: the command's
# own options), its flag and its argparse keywords. Each one may come before
# or after the command; one the command does not take is a usage error.
OPTIONS: Tuple[Tuple[Tuple[str, ...], Optional[str], str, Dict[str, Any]], ...] = (
    (ALL_COMMANDS, "installation", "--production", {
        "action": "store_true",
        "help": "a production install (deploy/setup.sh): deploy/.env.production and the production overlay"}),
    (ALL_COMMANDS, "installation", "--env-file", {
        "metavar": "PATH", "help": "production env file, if not deploy/.env.production"}),
    (("check", "apply", "download"), "release", "--manifest", {
        "metavar": "PATH_OR_URL",
        "help": "release manifest (default: FUTURE_AGI_UPDATE_MANIFEST_URL, the latest GitHub release)"}),
    (("check", "apply", "download"), "release", "--bundle", {
        "metavar": "PATH_OR_URL", "help": "its signature bundle (default: next to the manifest)"}),
    (("check", "apply", "download", "setup"), "release", "--policy", {
        "choices": ("patch", "minor", "major"),
        "help": "largest version jump to allow (default: FUTURE_AGI_UPDATE_POLICY or minor)"}),
    (("check", "apply", "download", "setup"), "release", "--channel", {
        "choices": ("stable", "beta"),
        "help": "release channel to follow (default: FUTURE_AGI_UPDATE_CHANNEL or stable). Releases publish "
                "stable manifests only: beta needs --manifest naming a beta manifest"}),
    (("check", "status"), None, "--format", {
        "choices": ("text", "json"), "help": "how to print the plan (default: text)"}),
    (("apply",), None, "--backup-command", {
        "metavar": "COMMAND", "help": "backup to run first (default: FUTURE_AGI_UPDATE_BACKUP_COMMAND)"}),
    (("apply",), None, "--allow-no-backup", {"action": "store_true", "help": "update without running a backup"}),
    (("apply",), None, "--dry-run", {"action": "store_true", "help": "show the plan and stop"}),
    (("apply",), None, "--stage", {
        "action": "store_true",
        "help": "back up, pin, pull and verify the release, then stop before restarting anything; a later apply "
                "finishes it (production: run the release's initialization jobs in between)"}),
    (("rollback",), None, "--receipt", {
        "metavar": "PATH", "help": "receipt of the update to undo (default: the last update)"}),
    (("rollback", "enable", "retry", "setup"), None, "--yes", {"action": "store_true", "help": "confirm this explicit action"}),
    (("apply",), None, "--acknowledge-stale-stage", {"action": "store_true", "help": "acknowledge an actual stale staged resume; not a new backup"}),
    (("status",), None, "--export-public", {"action": "store_true", "help": "export to the registered public slot"}),
    (("pause",), None, "--until", {"metavar": "RFC3339", "help": "pause until a future timestamp with UTC offset"}),
    (("pause",), None, "--indefinite", {"action": "store_true", "help": "pause until explicit resume"}),
    (("retry",), None, "--target", {"metavar": "VERSION", "help": "exact failed target"}),
    (("retry",), None, "--manifest-hash", {"metavar": "HEX", "help": "exact failed manifest SHA256"}),
    (("schedule", "setup"), None, "--docker-context", {"metavar": "NAME", "help": "explicit context to register, never set globally"}),
    (("schedule",), None, "--backend", {"choices": ("cron", "systemd"), "help": "render only; never install"}),
    (("schedule",), None, "--status-overlay", {"action": "store_true",
        "help": "production init only: enroll the fixed signed Distributed host-status overlay; no path"}),
    (("reporting",), None, "--directory", {"metavar": "ABSOLUTE_PATH", "help": "dedicated public parent directory"}),
    (("setup",), None, "--mode", {"choices": ("manual", "notify", "download", "apply"),
        "help": "one-time opt-in; manual disables without installing a schedule"}),
    (("setup",), None, "--deactivate", {"action": "store_true",
        "help": "disable first, then remove only the owned scheduler; preserve backups and audit records"}),
    (("setup",), None, "--window", {"metavar": "HH:MM-HH:MM_OR_ALWAYS",
        "help": "apply window (default: 02:00-06:00); never an empty apply window"}),
    (("setup",), None, "--timezone", {"metavar": "IANA_ZONE", "help": "window timezone (default: UTC)"}),
    (("setup",), None, "--check-interval", {"metavar": "SECONDS", "type": int,
        "help": "discovery interval, at least 300 seconds (default: 21600)"}),
    (("setup",), None, "--scheduler", {"choices": ("cron", "systemd"),
        "help": "install and verify this scheduler (default: current-user cron)"}),
    (("setup",), None, "--scheduler-scope", {"choices": ("system", "user"),
        "help": "systemd scope; user requires preconfigured linger (default: system)"}),
    (("setup",), None, "--cron-service", {"choices": ("cron.service", "crond.service", "cron", "crond", "com.vix.cron"),
        "help": "existing cron daemon to verify; never started by setup"}),
    (("setup",), None, "--backup-destination", {"metavar": "ABSOLUTE_PATH",
        "help": "private local directory OUTSIDE checkout/build contexts (default: named directory under HOME)"}),
    (("setup",), None, "--release-source", {"metavar": "HTTPS_ORIGIN",
        "help": "pin host-controlled HTTPS release API/assets origin once (default: canonical GitHub repository)"}),
    (("setup",), None, "--public-key", {"metavar": "PATH", "help": "pin this host public key once; default remains keyless"}),
    (("setup",), None, "--ca-file", {"metavar": "PATH", "help": "pin an additional HTTPS CA; hostname/certificate validation stays on"}),
)
# Explicit setup-only budget options. Older manual flag abbreviations remain
# governed by LEGACY_FLAGS, and these never affect manual apply/rollback.
OPTIONS += tuple((("setup",), None, "--" + name.replace('_', '-'), {
    'metavar': 'INTEGER', 'type': int, 'help': 'pin host-only ' + name.replace('_', ' ') + ' once',
}) for name in SETUP_POLICY_OPTIONS)
# The options (and hidden top-level ones) bin/update accepted before the automation commands existed. argparse
# lets an unambiguous prefix stand for an option (`--sta`, `--ba`, `--man`), and scripts use them: a prefix that
# named exactly one of these must keep naming it, whatever options the automation commands added since.
LEGACY_FLAGS = frozenset((
    "--production", "--env-file", "--manifest", "--bundle", "--policy", "--channel", "--format", "--backup-command",
    "--allow-no-backup", "--dry-run", "--stage", "--receipt", "--yes", "--root", "--cwd", "--help"))
# A command's own options when they are not given.
DEFAULTS = {"format": "text", "allow_no_backup": False, "dry_run": False, "stage": False, "receipt": None,
            "yes": False}
# The options that choose the release: the check and apply commands bin/update prints repeat the ones given.
RELEASE_OPTIONS = ("--manifest", "--bundle", "--policy", "--channel")


def _dest(flag: str) -> str:
    return flag[2:].replace("-", "_")


def _a(kind: Any) -> str:
    """A journal's kind with its article: "an apply", "a rollback", or "a run" when the journal names none."""

    word = str(kind or "run")
    article = "an" if word.startswith(("a", "e", "i", "o", "u")) else "a"
    return f"{article} {word}"


class _Abbreviations:
    """Command-aware abbreviation of long options (a mixin of the top-level and the command parsers).

    Every option is registered on every parser (so a full flag the command does not take gets its usage
    error), which would make the options the automation commands added ambiguous with the older ones. So:
    in a command's parser an abbreviation only ever names an option THAT COMMAND TAKES, and an abbreviation
    that still matches several of them names the one that predates the automation commands, if exactly one
    does (`apply --a` is --allow-no-backup, not --acknowledge-stale-stage). The top-level parser discovers the
    command without treating option values as commands, so both positions use the same matching rules.
    Full flags are never affected; ambiguity among the command's older options still is a usage error.
    """

    command: Optional[str] = None

    def _get_option_tuples(self, option_string):
        matches = super()._get_option_tuples(option_string)
        if self.command is not None:
            takes = {flag for takers, _, flag, _ in OPTIONS if self.command in takers}
            known = {flag for _, _, flag, _ in OPTIONS}
            matches = [match for match in matches if match[1] in takes or match[1] not in known]
        if len(matches) > 1:
            older = [match for match in matches if match[1] in LEGACY_FLAGS]
            if len(older) == 1:
                return older
        return matches


class _CommandParser(_Abbreviations, argparse.ArgumentParser):
    """One command's parser."""


class _Parser(_Abbreviations, argparse.ArgumentParser):
    """The top-level parser: checks that the command takes every option given."""

    commands: Dict[str, argparse.ArgumentParser]

    def _command_hint(self, words):
        # All top-level flags take zero or one value. Try command-specific scans
        # rather than searching for a bare command word (a manifest/path can be
        # named "apply"). argparse still owns validation and diagnostics.
        for candidate in dict.fromkeys(word for word in words if word in self.commands):
            self.command = candidate
            index, seen, subaction = 0, False, False
            while index < len(words):
                word = words[index]
                if not word.startswith('-') or word == '-':
                    if not seen and word == candidate:
                        seen = True
                    elif seen and candidate in ('schedule', 'reporting') and not subaction:
                        subaction = True
                    else:
                        break
                    index += 1
                    continue
                flag, equal, _ = word.partition('=')
                action = self._option_string_actions.get(flag)
                if action is None:
                    matches = self._get_option_tuples(word)
                    if len(matches) != 1:
                        break
                    action = matches[0][0]
                index += 1 if equal or action.nargs == 0 else 2
            else:
                if seen:
                    return candidate
        return None

    def parse_known_args(self, args=None, namespace=None):
        words = list(sys.argv[1:] if args is None else args)
        self.command = self._command_hint(words)
        try:
            namespace, extras = super().parse_known_args(words, namespace)
        finally:
            self.command = None
        command = getattr(namespace, "command", None)
        if command in self.commands:
            for takers, _, flag, _ in OPTIONS:
                dest = _dest(flag)
                if command not in takers and hasattr(namespace, dest):
                    # Preserve legacy usage diagnostics byte-for-byte.
                    named = tuple(c for c in takers if c in COMMANDS) if command in COMMANDS else takers
                    named = named or takers
                    self.commands[command].error(
                        f"argument {flag}: not allowed with {command} (only {' and '.join(named)} "
                        f"{'takes' if len(named) == 1 else 'take'} it)")
                if command in takers and dest in DEFAULTS and not hasattr(namespace, dest):
                    if command != 'status' or dest != 'format':
                        setattr(namespace, dest, DEFAULTS[dest])
            def present(name):
                return hasattr(namespace, name)
            error = self.commands[command].error
            if command == 'pause' and present('until') == present('indefinite'):
                error('choose exactly one of --until or --indefinite')
            if command == 'retry' and not (present('target') and present('manifest_hash')):
                error('--target and --manifest-hash are required')
            if command == 'schedule':
                required, forbidden = ('docker_context', 'backend') if namespace.action == 'init' else ('backend', 'docker_context')
                if not present(required) or present(forbidden):
                    error('init requires only --docker-context; render requires only --backend')
                if present('status_overlay') and (namespace.action != 'init' or not getattr(namespace, 'production', False)):
                    error('--status-overlay requires --production schedule init; it takes no path')
            if command == 'setup':
                if present('mode') == present('deactivate'):
                    error('choose exactly one of --mode or --deactivate')
                if present('deactivate'):
                    forbidden = ('policy', 'channel', 'window', 'timezone', 'check_interval', 'docker_context',
                                 'scheduler', 'scheduler_scope', 'cron_service', 'backup_destination',
                                 'release_source', 'public_key', 'ca_file') + SETUP_POLICY_OPTIONS
                    if any(present(name) for name in forbidden):
                        error('--deactivate uses the existing configuration; setup choices cannot accompany it')
                if present('window') and not namespace.window:
                    error('--window must not be empty')
                if present('check_interval') and not 300 <= namespace.check_interval <= 604800:
                    error('--check-interval must be between 300 and 604800 seconds')
                if present('scheduler_scope') and getattr(namespace, 'scheduler', None) != 'systemd':
                    error('--scheduler-scope requires --scheduler systemd')
                if present('cron_service') and getattr(namespace, 'scheduler', 'cron') != 'cron':
                    error('--cron-service requires --scheduler cron')
            if command == 'reporting' and not present('directory'):
                error('--directory is required')
            if command == 'status' and present('export_public') and present('format'):
                error('--export-public and --format are distinct operations')
            if command == 'apply' and present('acknowledge_stale_stage') and (namespace.stage or namespace.dry_run):
                error('--acknowledge-stale-stage requires a staged resume, not --stage or --dry-run')
        return namespace, extras


def _add_options(parser: argparse.ArgumentParser, command: Optional[str]) -> None:
    """Add every option to `parser`; only the ones `command` takes show in its help.

    default=SUPPRESS keeps an option that was not given off the namespace, so
    one given before the command survives the command's parser, and the
    command's defaults are filled in afterwards (DEFAULTS).
    """

    groups: Dict[str, Any] = {}
    for takers, group, flag, keywords in OPTIONS:
        shown = command in takers if command else group == "installation"
        target: Any = parser
        if shown and group:
            if group not in groups:
                groups[group] = parser.add_argument_group(group)
            target = groups[group]
        keywords = dict(keywords, default=SUPPRESS)
        if command == 'setup' and flag == '--channel':
            keywords['help'] = 'channel to follow after setup (default: host update.conf or stable); source is pinned once'
        if not shown:
            keywords["help"] = SUPPRESS
        target.add_argument(flag, **keywords)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="bin/update",
        description="Check for, apply and roll back signed Future AGI releases on a Docker Compose install.",
        epilog="Options may come before or after COMMAND; `bin/update COMMAND --help` lists the ones it takes. "
               "Settings: update.conf in the checkout (see deploy/update/README.md). Exit codes are listed in "
               "deploy/update/README.md.")
    _add_options(parser, None)
    parser.add_argument("--root", default=SUPPRESS, help=SUPPRESS)
    parser.add_argument("--cwd", default=SUPPRESS, help=SUPPRESS)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_CommandParser)
    sub.required = True
    helps = {
        "check": "show what an update would do (changes nothing)",
        "apply": "back up, update and verify",
        "rollback": "go back to the versions before an update",
        "status": "installed versions and the last update",
        "run": "one registered scheduled tick",
        "download": "cache verified active product images only",
        "enable": "enable policy only after confirmation",
        "disable": "disable future scheduled work",
        "pause": "pause future scheduled work",
        "resume": "clear pause only",
        "retry": "clear the exact failed-target latch after confirmation",
        "schedule": "register identity or render scheduler assets",
        "reporting": "bind a dedicated read-only public status slot",
        "setup": "opt in once: verify current installation, configure backups and activate an owned scheduler",
    }
    parser.commands = {}
    for command in ALL_COMMANDS:
        parser.commands[command] = sub.add_parser(command, help=helps[command])
        parser.commands[command].command = command
        _add_options(parser.commands[command], command)
        if command in ('schedule', 'reporting'):
            parser.commands[command].add_argument('action', choices=('init', 'render') if command == 'schedule' else ('init',))
    return parser


def _running_versions(installation: Installation) -> Dict[str, List[str]]:
    """service -> the versions its containers run; a one-off `docker compose run` container is not one of them."""

    running: Dict[str, List[str]] = {}
    for container in installation.containers():
        if container["status"] == "running" and container["image_id"] and not container.get("oneoff"):
            info = installation.image(container["image_id"]) or {}
            running.setdefault(container["service"], []).append(info.get("version") or "?")
    return running


def _status(installation: Installation, state: StateDir) -> Optional[str]:
    """Print the status to stdout; returns why Docker could not say what runs, if it could not.

    The env file, the journal and the receipts are read even then: they are
    what an operator needs when the daemon is down.
    """

    problem: Optional[str] = None
    project: Optional[str] = None
    services: Optional[List[Dict[str, Any]]] = None
    running: Optional[Dict[str, List[str]]] = None
    try:
        project = installation.project
        services = installation.futureagi_services()
        running = _running_versions(installation)
    except InstallError as exc:
        problem = str(exc)
    env_name = installation.relative(installation.env_file)
    # Who this install is, as receipts and journals record it; without Docker the project is not compared.
    identity = {"mode": installation.mode, "production": installation.production, "env_file": env_name,
                "project": project}
    print(f"Installation  {installation.label} (Compose project {project or 'unknown'}, {env_name})")
    if services is None:
        for variable in VERSION_VARIABLES:
            print(f"  {variable}={installation.env.get(variable, '') or 'unset'}")
    else:
        for service in services:
            variable = COMPONENT_VARIABLES[service["component"]]
            versions = "" if running is None else f"running {','.join(running.get(service['service'], ['-']))}  "
            print(f"  {service['service']:<28} {service['image']:<52} {versions}"
                  f"({variable}={installation.env.get(variable, '') or 'unset'})")
    if problem is not None:
        print(f"  running: unknown ({problem})")
    holder, journal = _lock_and_journal(state)
    # This install's last run: another install in the same checkout keeps receipts here too.
    last = state.latest_receipt(identity)
    conflict = None
    finished = None
    if not holder and journal:
        try:
            finished = validate_recovery(installation, journal, [last] if last else [],
                                         journal_path=state.journal_path, identity=identity)
        except Blocked as exc:
            problem = problem or str(exc)
            # Keep the existing cross-env command guidance, but it is a blocker,
            # not a successful recovery preview for the selected installation.
            if not _elsewhere(journal, identity):
                conflict = str(exc)
        except (StateError, EnvFileError) as exc:
            conflict = str(exc)
            problem = problem or conflict
    if conflict:
        print(f"Env conflict  {conflict}. Journal retained at {state.journal_path}")
    elif holder:
        # Its journal, if any, is the live record of a run that is still going.
        doing = (f" (now: {journal.get('kind')} to {journal.get('target_version')}, stage {journal.get('stage')})"
                 if journal else "")
        print(f"In progress   a bin/update run is in progress ({holder}); wait for it to finish{doing}")
    elif journal and finished is not None:
        # Its run wrote its receipt (Last run, below) and stopped before removing the journal: nothing to recover.
        # Only a final receipt is a run that finished; another one records how the run stopped (a recovery killed
        # after writing it), and check says how to go on from there.
        if last.get("status") in FINISHED_STATUSES:
            print(f"Journal       left by {_a(journal.get('kind'))} to {journal.get('target_version')} that finished; "
                  "the next apply or rollback removes it")
        else:
            print(f"Journal       left by {_a(journal.get('kind'))} to {journal.get('target_version')} that was already "
                  f"recorded as {last.get('status')} (Last run, below); the next apply or rollback removes it, and "
                  f"`{install_command(installation, 'check')}` says how to go on")
    elif journal and _elsewhere(journal, identity):
        # Another install's run (another env file, or the other side of --production): only its flags recover it.
        print(f"Interrupted   {_a(journal.get('kind'))} to {journal.get('target_version')} for "
              f"{journal.get('env_file')} stopped at stage {journal.get('stage')}; this install cannot be updated "
              f"until it is recovered: {_recover_elsewhere(installation, journal)}")
    elif journal:
        print(f"Interrupted   {_a(journal.get('kind'))} to {journal.get('target_version')} stopped at stage "
              f"{journal.get('stage')}; the next apply or rollback recovers it")
    if last:
        print(f"Last run      {last.get('kind')} to {last.get('target_version')}: {last.get('status')} "
              f"({last.get('finished_at', '?')}) {last['_path']}")
    else:
        print("Last run      none")
    return problem


def _elsewhere(journal: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
    """Whether `journal` was left by a run for another install in this checkout.

    Selects cross-env command guidance only. The shared validate_recovery
    gate also checks project/mode and header types before promising recovery.
    Only what the journal records counts.
    """

    return any(journal.get(key) is not None and journal.get(key) != identity[key] for key in ("env_file", "production"))


def _lock_and_journal(state: StateDir) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Who holds the update lock (None: nobody), and the journal, read between two probes of the lock.

    A run writes its receipt, clears its journal and only then lets the lock
    go, so a journal read before the probe can be the live record of a run
    that finished in between: the journal is read after the lock is probed.
    A run takes the lock before it writes its first journal, so a journal
    read with the lock free can also be the first record of a run that
    started just after the probe: the lock is probed again. Free both times,
    the journal was left by a run that stopped; held either time, the journal
    is the newest record of the run that holds it.
    """

    holder = state.lock_holder()
    journal = state.read_journal()
    if holder is None and journal is not None:
        holder = state.lock_holder()
    return holder, journal


def _recover_elsewhere(installation: Installation, journal: Mapping[str, Any], then: str = "") -> str:
    """How to recover a journal another install's run left, in the words apply refuses with (recover_elsewhere).

    The command that only recovers it comes first; `then` is what to do once
    it is recovered, if anything. What that install's apply would do is
    worded by the journal's kind. Its commands start with `cd <checkout> && `
    when bin/update was run from another directory, as this install's do.
    """

    production = bool(journal.get("production"))
    env_file = str(journal.get("env_file") or (PRODUCTION_ENV if production else ".env"))
    return recover_elsewhere(cd_prefix(installation) + command_prefix(production, env_file), journal.get("kind"), then)


def _check_inputs(installation: Installation, state: StateDir,
                  options: Sequence[str] = ()) -> Tuple[List[Dict[str, Any]], List[str], Optional[str]]:
    """For check: this install's receipts as the next apply will find them, plan notes, and a blocking reason.

    check never takes the lock and never recovers: the env file is not
    touched. A journal left by a killed run of this install is planned as
    recovering it (Updater.recover) will leave things: the version keys a
    run stopped before the restart had changed are overlaid as they will be
    put back, and the record recovery writes joins the history. A run that is
    still going, or a journal another install must recover first, blocks
    apply, so it blocks check too.

    The receipts are read after the journal (_lock_and_journal): a run that
    finished just before is then seen whole, by its receipt and not as an
    interrupted run. `options` are the release options check was given: the
    check command it names repeats them.
    """

    identity = installation.identity()
    holder, journal = _lock_and_journal(state)
    history = state.history(identity)
    check = install_command(installation, "check", options)
    if holder:
        # Its journal, if any, is live: nothing about how that run ends is assumed.
        return history, [], (f"a bin/update run is in progress ({holder}); wait for it to finish, then run "
                             f"`{check}` again")
    if not journal:
        return history, [], None
    validate_journal_header(journal, state.journal_path)
    if _elsewhere(journal, identity):
        return history, [], (f"an interrupted bin/update run for {journal.get('env_file')} must be recovered before "
                             f"apply or rollback can run for this install: "
                             f"{_recover_elsewhere(installation, journal, then=f'`{check}` again')}")
    record, notes = preview_recovery(installation, journal, history)
    if finished_run(journal, history) is not None:
        # Its run already wrote its receipt: recovery only removes the journal.
        return history, notes, None
    return history + [record], notes, None


def _receipt_paths(state: StateDir) -> List[Path]:
    """The receipts there are now, best effort: an unreadable state directory is the update run's to report."""

    try:
        return state.receipts()
    except (OSError, StateError):
        return []


def _interrupted(state: Optional[StateDir], before: Optional[Sequence[Path]], installation: Optional[Installation],
                 options: Sequence[str] = ()) -> str:
    """What to say about a Ctrl-C, SIGTERM or SIGHUP that no update run reported itself.

    A run reports its own interrupt once it has changed something
    (InterruptedRun). Anything else stopped bin/update before an update run
    started (`before` is None: reading the install, the manifest download,
    its verification, check, status), or inside one before it recorded
    anything: neither changed the install. An interrupt that left a journal
    or a new receipt behind says where to look. A journal is only this run's
    when the lock is free and it is this install's: with the lock held it is
    the live record of another run (this one was still waiting for the
    lock), and another install's journal is recovered by that install's
    command. `options` are the release options this apply was given.
    """

    if state is None or before is None or installation is None:
        return "interrupted before anything was changed"
    status = install_command(installation, "status")
    try:
        holder, journal = _lock_and_journal(state)
        recorded = [path for path in state.receipts() if path not in before]
    except (OSError, StateError):
        return f"interrupted; run `{status}` to see whether a run was left unfinished"
    if holder:
        return (f"interrupted; another bin/update run is in progress ({holder}): wait for it to finish, then run "
                f"`{status}` to see what it did")
    here = {"env_file": installation.relative(installation.env_file), "production": installation.production}
    if journal and _elsewhere(journal, here):
        return (f"interrupted; {_a(journal.get('kind'))} for {journal.get('env_file')} stopped earlier and "
                f"must be recovered before apply or rollback can run for this install: "
                f"{_recover_elsewhere(installation, journal)}")
    if journal:
        apply, rollback = install_command(installation, "apply", options), install_command(installation, "rollback")
        try:
            last = state.latest_receipt(here)
        except (OSError, StateError):
            last = None
        if last is not None and finished_run(journal, [last]) is not None:
            # Stopped after its receipt was written, before its journal was removed: that receipt is the record.
            done = ("finished" if last.get("status") in FINISHED_STATUSES
                    else f"was recorded as {last.get('status')}")
            return (f"interrupted after the {journal.get('kind') or 'run'} {done}: receipt {last['_path']} records "
                    f"it (`{status}` shows it); the next `{apply}` or `{rollback}` removes the journal it left and "
                    "recovers nothing")
        return (f"interrupted before the {journal.get('kind') or 'run'} finished; the next `{apply}` or `{rollback}` "
                f"recovers it first (`{status}` shows where it stopped)")
    if recorded:
        newest = recorded.pop()
        return f"interrupted; what the run did is recorded in {newest} (`{status}` shows it)"
    return "interrupted before anything was changed"


def _os_error(exc: OSError) -> str:
    """An OSError as a sentence: what could not be done to which file, and why."""

    reason = exc.strerror or str(exc) or exc.__class__.__name__
    if exc.filename is not None:
        return f"cannot use {exc.filename}: {reason}"
    return reason


def _tolerant_output() -> None:
    """Printing never fails: under a non-UTF-8 locale, what the terminal cannot show is escaped (\\u2026)."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):  # detached or closed: nothing will be printed there
            pass


def _remove_stale_workdirs(state: StateDir) -> None:
    """Remove the verify-<pid> directories of runs that are gone.

    A run removes its own on the way out, but one killed outright (kill -9,
    power loss) cannot. A directory whose process still exists (a check
    running next to this one, or a pid since reused) is left alone.
    """

    try:
        entries = list(state.path.iterdir())
    except OSError:
        return
    for entry in entries:
        match = STALE_WORKDIR.fullmatch(entry.name)
        if match is None or entry.is_symlink() or not entry.is_dir():
            continue
        pid = int(match.group(1))
        if pid <= 0 or pid == os.getpid():
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            remove_workdir(entry)
        except (OSError, OverflowError):  # alive under another user, or not a pid at all
            pass


@dataclass(frozen=True)
class ReportingRegistration:
    schema_version: int
    directory: Path
    installation_fingerprint: str


def _now():
    return datetime.now(timezone.utc)


@contextmanager
def _short_lock(store, name):
    with ExitStack() as stack:
        directory = _retry_busy(lambda: stack.enter_context(store._lock(name)))
        yield directory


class _ReportBusy(StoreBusy):
    """The primary mutation landed, but its optional public report did not."""
    def __init__(self, recorded):
        super().__init__()
        self.message = (recorded + ' recorded; public report busy; nothing recorded in public slot; '
                        'retry status --export-public')


def _reporting(store):
    try:
        with store._directory() as directory:
            record = store._read(directory, 'reporting.json', ReportingRegistration)
    except FileNotFoundError:
        return None
    binding = store.read_registration().binding
    require(record.schema_version == 1 and record.installation_fingerprint == binding.installation_fingerprint,
            ReasonCode.STATUS_REPORTING_UNAVAILABLE)
    return PublicStatusPublisher(store, record.directory)


def _reporting_init(store, path, settings):
    parent = Path(path)
    binding = store.read_registration().binding
    require(parent.is_absolute() and '..' not in parent.parts and '.futureagi-updates' not in parent.parts,
            ReasonCode.STATUS_REPORTING_UNAVAILABLE)
    require(parent != store.checkout and parent not in store.checkout.parents and store.checkout not in parent.parents
            and parent != store.env_target and parent not in store.env_target.parents,
            ReasonCode.STATUS_REPORTING_UNAVAILABLE)
    safe_path(parent if parent.exists() else parent.parent, directory=True)
    with _short_lock(store, 'reporting.lock') as directory:
        old = _reporting(store)
        require(old is None or old.parent == parent, ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        if old is None and parent.exists():
            entries = list(parent.iterdir())
            slot = parent / binding.slot_id
            require(not entries or entries == [slot] and not any(safe_path(slot, directory=True).iterdir()),
                    ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        publisher = old or PublicStatusPublisher(store, parent)
        # Capture before recording anything: a busy public lock is a refusal,
        # not a falsely successful initialization. Register before publishing,
        # so interruption at either write can be retried with the same parent.
        _retry_busy(publisher.capture)
        store._write(directory, 'reporting.json', ReportingRegistration(1, parent, binding.installation_fingerprint))
        try:
            _retry_busy(lambda: publisher.publish_controls(policy=settings.automation_policy, now=_now()))
        except StoreBusy:
            raise _ReportBusy('reporting registration') from None
    print('Public status directory: ' + str(publisher.path))
    print('Opt in to deploy/docker-compose.host-status.' + binding.topology + '.yml; mount this slot read-only.')


class _Publication:
    """Capture before slow work. Never retry a stale report or change an outcome."""
    def __init__(self, store, settings, *, required=False, controls=False):
        self.store, self.settings, self.required = store, settings, required
        self.publisher = None
        self.previous = None
        self.token = uuid.uuid4().hex
        self.started = _now()
        try:
            self.publisher = _reporting(store)
            if required:
                require(self.publisher is not None, ReasonCode.STATUS_REPORTING_UNAVAILABLE)
            if self.publisher is not None and not controls:
                self.ticket, self.previous = _retry_busy(self.publisher.capture)
        except Exception as exc:
            self.publisher = None
            if required:
                raise
            self.warning(exc, registration=True)

    @staticmethod
    def warning(error=None, *, registration=False):
        if isinstance(error, PublicationConflict):
            message = 'status_reporting_unavailable; newer operation report retained (publication conflict)'
        elif isinstance(error, StoreBusy):
            message = 'status_reporting_unavailable; public report busy; nothing recorded in public slot'
        elif registration:
            message = 'status_reporting_unavailable; inspect host reporting registration and public slot'
        else:
            message = 'status_reporting_unavailable; public report not recorded; inspect host status'
        try:
            sys.stderr.write('bin/update: ' + message + '\n')
        except (Exception, Interrupted, KeyboardInterrupt):
            pass

    def controls(self):
        if self.publisher is not None:
            try:
                _retry_busy(lambda: self.publisher.publish_controls(policy=self.settings.automation_policy, now=_now()))
            except StoreBusy:
                raise _ReportBusy('control') from None
            except Exception as exc:
                self.warning(exc)

    def finish(self, *, installation=None, kind=None, code=0, discovery=None, download=None, receipt=None):
        if self.publisher is None:
            return
        try:
            current = _now()
            engine_state = StateDir(self.store.checkout)
            observation = None if installation is None else observe_status(installation, engine_state, now=current)
            outcome = 'succeeded' if code == 0 else 'interrupted' if code == 130 else 'blocked' if code in (2, 3) else 'failed'
            stage = None
            if code == 0 and kind in ('apply', 'rollback'):
                if receipt is None:
                    kind = 'check'  # A dry run, up-to-date apply or unconfirmed rollback did no transaction.
                else:
                    record = engine_state.load_receipt(receipt)
                    outcome = {'succeeded': 'succeeded', 'rolled_back': 'rolled_back', 'staged': 'staged'}.get(
                        record.get('status'), 'unknown')
                    if outcome == 'staged' and self.publisher.binding.production:
                        outcome, stage = 'awaiting_initialization', 'production_initialization'
            operation = None if kind is None else {
                'state': outcome, 'kind': kind, 'stage': stage, 'started_at': stamp(self.started),
                'finished_at': stamp(current), 'reported_at': stamp(current), 'reason_codes': []}
            state = self.store.read_state()
            if discovery is not None:
                # Presentation evidence for THIS manual check, not a scheduler
                # deadline/backoff mutation or a renewed cached observation.
                state = replace(state, last_attempt_at=self.started)
            snapshot = build_status(self.publisher.binding, self.settings.automation_policy,
                self.store.read_control(), state, now=current, previous=self.previous,
                observation=observation, discovery=discovery, download=download, operation=operation)
            if outcome == 'awaiting_initialization':
                snapshot['safety']['operator_action'] = 'initialize_then_apply'
            self.ticket = _retry_busy(lambda: self.publisher.publish(
                snapshot, expected=self.ticket, operation_token=self.token, observation_only=kind is None))
            self.previous = snapshot
        except (Exception, Interrupted, KeyboardInterrupt) as exc:
            if self.required:
                raise
            # Reporting is not part of an update's success/exit contract.
            self.warning(exc)


def _pause_until(value):
    require(isinstance(value, str) and re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value) is not None,
        ReasonCode.CONTROL_INVALID)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        # OverflowError: a representable local time whose UTC form is outside datetime's range (9999-12-31 at -23:59).
        raise GuardRefused(ReasonCode.CONTROL_INVALID) from None
    require(result > _now(), ReasonCode.CONTROL_INVALID)
    return result


def _enroll(store, context, settings, *, status_overlay=False):
    # Before anything builds the supervisor: it rejects an empty/odd context with an untyped ValueError.
    require(valid_docker_context(context), ReasonCode.OWNERSHIP_UNCONFIRMED)
    require(not status_overlay or store.production, ReasonCode.UNSUPPORTED_INSTALLATION)
    overlay = production_status_overlay(store.checkout) if status_overlay else None
    safe_path(store.checkout, directory=True)
    safe_path(store.env_target)
    launcher = LauncherConfig(Path(sys.executable).absolute(), Path.home().resolve(),
                              os.environ.get('PATH', os.defpath), pwd.getpwuid(os.geteuid()).pw_name)
    require(os.access(launcher.python, os.X_OK) and os.access(store.checkout / 'bin/update', os.X_OK),
            ReasonCode.SCHEDULER_UNREGISTERED)
    supervisor = ProcessSupervisor(settings.automation_policy, docker_context=context,
        environment={'HOME': str(launcher.home), 'PATH': launcher.path, 'USER': launcher.user, 'LOGNAME': launcher.user})
    import shutil
    from .install import ComposeCommand, resolve_compose_command
    def which(name):
        return shutil.which(name, path=launcher.path)
    docker = which('docker')
    require(docker is not None, ReasonCode.OWNERSHIP_UNCONFIRMED)
    compose = resolve_compose_command(supervisor, which=which)
    if os.path.basename(compose[0]) == 'docker':
        compose = ComposeCommand([docker, '--context', context, *compose[1:]], getattr(compose, 'version', None))
    installation = Installation(store.checkout, production=store.production, env_file=store.env_target,
        environ=supervisor.environment, runner=supervisor, docker=[docker, '--context', context], compose=compose,
        status_overlay=overlay)
    binding = installation_binding(installation, store, context)
    binding_status_overlay(binding)
    for path in binding.compose_files:
        safe_path(path)
    registration = Registration(1, binding, launcher, _now())
    _retry_busy(lambda: store.register(registration))


def _setup(args, store):
    from .enrollment import setup_automation, setup_summary
    from .standalone_backup import BackupError
    from .envcommit import CommitError
    from .httpcache import DiscoveryError
    print('Disable automation and remove only its owned scheduler; preserve private evidence.'
          if getattr(args, 'deactivate', False) else setup_summary(store, args))
    if not args.yes:
        confirmed = False
        if sys.stdin.isatty():
            try:
                prompt = ('Acknowledge the disclosure and authorize automatic migration updates with mandatory restore? [y/N] '
                          if getattr(args, 'mode', None) == 'apply' else 'Authorize this one-time host action? [y/N] ')
                confirmed = input(prompt).strip().lower() in ('y', 'yes')
            except EOFError:
                pass
        if not confirmed:
            print('Setup declined; no configuration or scheduler changed.')
            return 3
        args.yes = True
    try:
        result = setup_automation(store, args)
    except (StoreError, GuardRefused, ProcessError, SettingsError, InstallError, EnvFileError, StateError,
            VerificationError, DiscoveryError, BackupError, CommitError, OSError, ValueError) as exc:
        reason = getattr(exc, 'reason_code', 'setup_prerequisite_unavailable')
        reason = reason.value if isinstance(reason, ReasonCode) else str(reason)
        print('Setup incomplete: ' + reason + '. Unattended work was not activated by this action.')
        print('Check host Python 3.9+, Compose v2, host cosign v3+, signed current image/digest, fixed state probes, '
              'local Docker storage and private backup destination. Extra mounts/status overlays, source builds '
              'and unsupported profiles require manual updates. Keep all private evidence; retry the same setup '
              'after resolving the prerequisite. No packages or host permissions were changed.')
        return 3
    print('Setup ' + result.state + '; scheduler=' + result.scheduler + '; policy=' + result.policy_state + '.')
    if result.reason:
        print('Reason: ' + result.reason)
    if result.action:
        print(result.action)
    if result.next_window is not None:
        print('Next apply window: ' + result.next_window.isoformat())
    try:
        binding = store.read_registration().binding
        print('Verified Docker target: ' + binding.docker_context + '; daemon=' + binding.daemon_identity
              + '; scheduler owner UID=' + str(binding.host_uid))
    except StoreError:
        pass
    return result.code


def _automation(args, root, env_file):
    store = selected_store(root, production=getattr(args, 'production', False), env_file=env_file)
    if args.command == 'setup':
        if hasattr(args, 'root'):
            safe_path(Path(args.root), directory=True)
        for name in ('public_key', 'ca_file'):
            if hasattr(args, name):
                path = Path(getattr(args, name)).expanduser()
                setattr(args, name, str(path if path.is_absolute() else Path(getattr(args, 'cwd', os.getcwd())) / path))
        return _setup(args, store)
    if args.command == 'run':
        status_overlay = registered_status_overlay(store)
        reporting_failed = False
        try:
            publisher = _reporting(store)
        except Exception:
            publisher = None
            reporting_failed = True
        from .enrollment import load_setup_configuration, configured_capacity, configured_discovery
        configured = load_setup_configuration(store)
        if configured is not None:
            result = HostRunner(store, settings=configured.as_settings(), publisher=publisher,
                discovery_factory=configured_discovery,
                space_available=lambda install: configured_capacity(store, install)).tick()
        else:
            result = HostRunner(store, publisher=publisher, facts=EnrolledFacts(store), status_overlay=status_overlay,
                                **({'discovery_factory': overlay_discovery} if status_overlay is not None else {}),
                                space_available=lambda install: daemon_space_available(install, store.read_registration().binding)).tick()
        warnings = list(result.warnings)
        if reporting_failed:
            warnings.append(ReasonCode.STATUS_REPORTING_UNAVAILABLE)
        # Explicit host-only fields, never a dataclass dump (receipt is private).
        print(json.dumps({'code': result.code, 'disposition': result.disposition,
            'reason_codes': [r.value for r in result.reason_codes], 'skip_reason': result.skip_reason,
            'transaction_started': result.transaction_started, 'rollout_started': result.rollout_started,
            'forward_result': result.forward_result, 'fallback_result': result.fallback_result,
            'warnings': list(dict.fromkeys(r.value for r in warnings))}, sort_keys=True))
        return result.code
    settings = load_settings(root, environ={}, verify=False)
    if args.command == 'schedule':
        if args.action == 'init':
            # Plain input validation first: a bad spelling is a refusal whatever the filesystem looks like.
            require(valid_docker_context(args.docker_context), ReasonCode.OWNERSHIP_UNCONFIRMED)
            if hasattr(args, 'root'):
                safe_path(Path(args.root), directory=True)
            if hasattr(args, 'env_file'):
                require(Path(args.env_file).is_absolute(), ReasonCode.OWNERSHIP_UNCONFIRMED)
            safe_path(env_file or root / (PRODUCTION_ENV if store.production else '.env'))
            _enroll(store, args.docker_context, settings, status_overlay=getattr(args, 'status_overlay', False))
            print('Registered; automation controls and scheduler installation unchanged.')
        else:
            assets = render_scheduler(store, settings.automation_policy, backend=args.backend)
            for name, text in assets.files.items():
                print('# ' + name)
                print(text, end='' if text.endswith('\n') else '\n')
            print(assets.removal_instructions)
        return 0
    if args.command == 'reporting':
        try:
            _reporting_init(store, args.directory, settings)
        except (Exception, Interrupted, KeyboardInterrupt):
            sys.stderr.write('bin/update: reporting init incomplete; retry with the same directory after resolving '
                             'the refusal; do not remove foreign content or rebind\n')
            raise
        return 0
    # Disable/pause must remain available even if setup metadata is damaged.
    # Enable cannot make an incomplete/removed setup appear operational.
    from .enrollment import read_setup_configuration, load_setup_configuration
    if args.command == 'enable':
        configured = read_setup_configuration(store)
        if configured is not None:
            from .scheduler_install import inspect_activation
            configured = load_setup_configuration(store)
            require(inspect_activation(store, configured.policy, **configured.scheduler_options).active,
                    ReasonCode.SCHEDULER_UNREGISTERED)
            settings = configured.as_settings()
    else:
        try:
            configured = read_setup_configuration(store)
            if configured is not None:
                settings = configured.as_settings()
        except (StoreError, OSError, ValueError):
            pass  # Do not prevent an operator's stop control.
    publication = _Publication(store, settings, controls=True)
    if args.command in ('enable', 'retry'):
        require(args.yes, ReasonCode.CONTROL_INVALID)
    if args.command == 'retry':
        target = TargetIdentity(args.target, args.manifest_hash)
        _retry_busy(lambda: store.retry_target(target))
    else:
        until = _pause_until(args.until) if args.command == 'pause' and hasattr(args, 'until') else None
        _retry_busy(lambda: store.change_control(args.command, _now(), until=until))
    print(args.command + ': recorded; no update or scheduler started.')
    publication.controls()
    return 0


def _host_status(installation, state):
    observation = observe_status(installation, state)
    # Independent host schema. These host-private paths MUST NOT go to the app.
    result = {'host_schema_version': 1, 'checkout': str(installation.root),
              'env_file': str(installation.env_file), 'production': installation.production,
              'versions': observation.versions, 'pending_transaction': observation.pending_transaction,
              'last_run': observation.last_run, 'reason_codes': [r.value for r in observation.reason_codes]}
    print(json.dumps(result, sort_keys=True))
    return 2 if observation.reason_codes else 0


def _manual_discovery(fetched, verification, settings):
    from .manifest import manifest_hash
    from .trust import Verifier as TrustPolicy
    if not verification.verified:
        return DiscoveryResult(reason_codes=(ReasonCode.SIGNATURE_REJECTED,))
    if getattr(fetched, 'newer_release', None) or fetched.bundle_path is None:
        return DiscoveryResult(reason_codes=(ReasonCode.RELEASE_INCOMPLETE,))
    release = DiscoveredRelease(fetched.raw, fetched.bundle_path.read_bytes(), manifest_hash(fetched.data),
        TrustPolicy(settings).trust_fingerprint(fetched), VerificationSnapshot(verification.verified,
        verification.method, verification.signer, verification.issuer, verification.tool, _now()))
    return DiscoveryResult(release, checked_at=_now())


def _manual_stage_binding(installation, fetched, settings):
    from .manifest import manifest_hash
    from .trust import Verifier as TrustPolicy
    fresh = installation.fresh()
    fresh.overlay_env({key: 'stage-binding-pin' for key in VERSION_VARIABLES})
    config, profile, _ = fingerprints(fresh)
    return {'manifest_hash': manifest_hash(fetched.data),
            'manifest_sha256': hashlib.sha256(fetched.raw).hexdigest(),
            'bundle_sha256': hashlib.sha256(fetched.bundle_path.read_bytes() if fetched.bundle_path else b'').hexdigest(),
            'installation_fingerprint': manifest_hash({'root': str(installation.root), **installation.identity()}),
            'config_fingerprint': config, 'profile_fingerprint': profile,
            'trust_fingerprint': TrustPolicy(settings).trust_fingerprint(fetched)}


def main(argv: Optional[List[str]] = None) -> int:
    _tolerant_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    option = lambda name, default=None: getattr(args, name, default)  # noqa: E731
    root = Path(option("root") or Path(__file__).resolve().parents[2]).resolve()
    cwd = Path(option("cwd") or os.getcwd())

    def from_cwd(value: Optional[str]) -> Optional[Path]:
        if value is None:
            return None
        path = Path(value).expanduser()
        return path if path.is_absolute() else cwd / path

    def located(value: str) -> str:
        """A --manifest or --bundle as fetched: a URL as given, a file by its absolute path."""

        return value if "://" in value else str(from_cwd(value))

    # The release options as given (local files made absolute): the check and apply commands this run prints
    # repeat them, so a pasted command plans the release this one did.
    command_options: List[str] = []
    for flag in RELEASE_OPTIONS:
        value = option(flag[2:])
        if value:
            command_options += [flag, located(value) if flag in ("--manifest", "--bundle") else value]

    out = sys.stderr
    result_code = 0
    publication = None
    discovered = downloaded = receipt = None
    busy = False

    def fail(message: str, code: int) -> int:
        nonlocal result_code
        result_code = code
        # What went to stdout so far comes first, even when both streams go to one file.
        try:
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        out.write(f"bin/update: {message}\n")
        return code

    workdir = None
    state: Optional[StateDir] = None
    installation: Optional[Installation] = None
    # The receipts there were when an update run (Updater.apply or rollback) started; None outside one.
    before: Optional[List[Path]] = None
    try:
        with signals_as_interrupt():
            if args.command in AUTOMATION_COMMANDS and args.command != 'download':
                return _automation(args, root, from_cwd(option('env_file')))
            # status and rollback verify no signature: a cosign setting only check and apply use (offline
            # verification without a public key) must not stop them.
            settings = load_settings(root, {
                "FUTURE_AGI_UPDATE_POLICY": option("policy"),
                "FUTURE_AGI_UPDATE_CHANNEL": option("channel"),
                "FUTURE_AGI_UPDATE_BACKUP_COMMAND": option("backup_command"),
            }, verify=args.command in VERIFYING_COMMANDS)
            store = None
            try:
                store = selected_store(root, production=bool(option('production', False)),
                                       env_file=from_cwd(option('env_file')))
                if args.command != 'status' or option('export_public'):
                    publication = _Publication(store, settings, required=bool(option('export_public')))
            except Exception:
                if option('export_public') or args.command == 'download':
                    raise
                # Legacy unregistered operation remains usable, even with damaged reporting.
                publication = None
            # Identity failures must not be swallowed as nonfatal reporting failures.
            status_overlay = registered_status_overlay(store) if store is not None else None
            # Where the operator ran bin/update (without --cwd: the checkout): the commands it prints, even
            # in a refusal to build the installation, start with `cd <checkout> && ` elsewhere. The release
            # options go in before any plan or stack check: the bin/update commands the installation prints
            # itself (check_stack's `./bin/update --production check`, its own refusal to start without
            # --production) repeat them too.
            try:
                if args.command == 'download':
                    registration = store.read_registration()
                    launcher = registration.launcher
                    supervisor = ProcessSupervisor(settings.automation_policy,
                        docker_context=registration.binding.docker_context,
                        environment={'HOME': str(launcher.home), 'PATH': launcher.path,
                                     'USER': launcher.user, 'LOGNAME': launcher.user})
                    installation = registered_installation(registration.binding, supervisor,
                                                           status_overlay=binding_status_overlay(registration.binding))
                else:
                    installation = Installation(root, production=bool(option("production", False)),
                                                env_file=from_cwd(option("env_file")),
                                                caller_cwd=Path(option("cwd")).resolve() if option("cwd") else None,
                                                command_options=list(command_options),
                                                **({'status_overlay': status_overlay} if status_overlay is not None else {}))
            except (InstallError, EnvFileError):
                # Read-only bootstrap diagnostics; never construct mutable state
                # or hide ordinary preflight remedies behind an unrelated journal.
                pending = root / STATE_DIR / JOURNAL
                try:
                    evidence = json.loads(pending.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    evidence = None
                if isinstance(evidence, dict) and evidence.get("journal_version") == 2:
                    raise StateError(
                        f"Cannot open the installation with an unfinished run. Keep {pending} and "
                        "the selected env target's .futureagi-updates/env-commits directory. The env may already "
                        "have changed; use the README's manual conflict recovery guidance. This diagnostic is "
                        "read-only and does not authorize recovery without ownership") from None
                raise
            state = StateDir(root, secret_values(installation.env))
            if args.command == "status":
                if option('export_public'):
                    exporting, publication = publication, None
                    exporting.finish(installation=installation)
                    return 0
                publication = None  # Ordinary status is strictly read-only.
                if option('format') == 'json':
                    return _host_status(installation, state)
                problem = _status(installation, state)
                return 0 if problem is None else fail(problem, 2)
            if option('acknowledge_stale_stage'):
                staged = Updater._unfinished_update(state.history(installation.identity()))
                require(staged is not None and staged.get('status') == 'staged', ReasonCode.STALE_STAGE)
            updater = Updater(installation, settings, state, out=out, command_options=command_options)
            if args.command == "rollback":
                before = _receipt_paths(state)
                receipt = updater.rollback(from_cwd(option("receipt")), yes=bool(option("yes", False)))
                return 0

            if option("manifest"):
                source = located(option("manifest"))
            else:
                source = settings.get("FUTURE_AGI_UPDATE_MANIFEST_URL")
                if "://" not in source and not Path(source).is_absolute():
                    source = str(root / source)
            bundle = located(option("bundle")) if option("bundle") else option("bundle")
            state.ensure()
            _remove_stale_workdirs(state)
            workdir = state.path / f"verify-{os.getpid()}"
            remove_workdir(workdir)
            fetched = fetch_manifest(source, workdir, cwd=cwd, bundle=bundle)
            if fetched.note and option("format") != "json":
                # Said before verification, which may fail; the plan's notes repeat it.
                out.write(f"Note: {fetched.note}\n")
                out.flush()
            if args.command == 'download':
                import shutil
                verifier = Verifier(settings, runner=supervisor, probe=supervisor,
                    which=lambda name: shutil.which(name, path=launcher.path),
                    environ=supervisor.environment, host_only=True)
            else:
                verifier = Verifier(settings, docker=installation.docker)
            verification = verifier.verify(fetched)
            if status_overlay is not None:
                require(verification.verified, ReasonCode.SIGNATURE_REJECTED)
                check_status_deployment(installation, fetched.data)
            if args.command == 'download':
                discovered = _manual_discovery(fetched, verification, settings)
            elif publication is not None and publication.publisher is not None:
                try:
                    discovered = _manual_discovery(fetched, verification, settings)
                except Exception:
                    publication.warning()
            stage_identity = {}

            def make_plan(history: Optional[List[Dict[str, Any]]],
                          journal_reason: Optional[str] = None) -> Dict[str, Any]:
                """The plan for this install, given its receipts (StateDir.history), oldest first.

                newer_release is set only when a release newer than this manifest's has no manifest yet:
                fallback_note alone does not say that (the latest release may be an older backport or an
                ad-hoc build).
                """

                plan = build_plan(fetched.data, verification, installation, settings, manifest_source=fetched.source,
                                  history=list(history or []), fallback_note=fetched.note,
                                  newer_release=getattr(fetched, "newer_release", None),
                                  journal_reason=journal_reason, command_options=command_options)
                # Why this manifest was used rather than the latest release's, and why cosign ran from its
                # image (an old cosign on PATH), when that happened.
                details = getattr(verification, "details", None)
                for note in (fetched.note, details.get("note") if isinstance(details, dict) else None):
                    if isinstance(note, str) and note and note not in plan["notes"]:
                        plan["notes"].append(note)
                if args.command == 'apply':
                    staged = Updater._unfinished_update(list(history or [])) if plan.get('resume') else None
                    actual_stage = staged is not None and staged.get('status') == 'staged'
                    if option('acknowledge_stale_stage'):
                        require(actual_stage, ReasonCode.STALE_STAGE)
                    if option('stage') or actual_stage:
                        stage_identity.update(_manual_stage_binding(installation, fetched, settings))
                return plan

            if args.command == 'download':
                require(discovered.complete and discovered.candidate is not None,
                        discovered.reason_codes[0] if discovered.reason_codes else ReasonCode.RELEASE_INCOMPLETE)
                release = discovered.candidate
                prepared = prepare_update(release, installation, registration.binding,
                    lambda fresh, history: build_plan(fetched.data, verification, fresh, settings, history=list(history)),
                    state.history(installation.identity()))
                downloaded = Downloader(installation, state,
                    space_available=lambda: daemon_space_available(installation.fresh(), registration.binding)).download(prepared)
                print('Downloaded ' + release.version + '; live env and containers unchanged.')
                return 0
            if args.command == "check":
                history, notes, reason = _check_inputs(installation, state, command_options)
                plan = make_plan(history, journal_reason=reason)
                plan["notes"].extend(notes)
                sys.stdout.write(plan_json(plan) if option("format") == "json" else plan_text(plan))
                result_code = 3 if plan["decision"] == BLOCKED else 0
                return result_code
            before = _receipt_paths(state)
            receipt = updater.apply(make_plan, fetched.data, allow_no_backup=bool(option("allow_no_backup", False)),
                          backup_command=option("backup_command"), dry_run=bool(option("dry_run", False)),
                          stage=bool(option("stage", False)),
                          release_binding=None if option('dry_run') else stage_identity,
                          acknowledge_stale_stage=bool(option('acknowledge_stale_stage')))
            return 0
    except StoreBusy as exc:
        busy = True
        if args.command == 'run':
            return 0
        return fail(exc.message if isinstance(exc, _ReportBusy) else 'busy; nothing recorded; retry the command', 3)
    except StateBusy:
        busy = True
        return 0  # Existing engine-lock busy contract (not a short metadata lock).
    except PublicationConflict as exc:
        _Publication.warning(exc)
        result_code = 2
        return result_code
    except (StoreError, ProcessError) as exc:
        return fail(exc.reason_code.value, 2)
    except (Blocked, InterruptedRun, UpdateError) as exc:
        return fail(str(exc), exc.exit_code)
    except (KeyboardInterrupt, Interrupted):
        if args.command == 'setup':
            return fail('setup interrupted; retain partial setup/activation evidence and inspect durable control '
                        'and scheduler before retrying; no rollback or cleanup was attempted', 130)
        return fail(_interrupted(state, before, installation, command_options), 130)
    except (ManifestError, SettingsError, InstallError, EnvFileError, StateError, VerificationError,
            NotReady) as exc:
        return fail(str(exc), 2)
    except OSError as exc:
        # Whatever the modules above do not turn into their own errors (the verify directory on a full
        # disk, a docker binary that cannot be executed): an error, never a traceback.
        return fail(_os_error(exc), 2)
    finally:
        if publication is not None and not busy:
            try:
                if state is None or state.lock_holder() is None:
                    kind = 'check' if args.command == 'apply' and option('dry_run') else args.command
                    publication.finish(installation=installation, kind=kind if kind != 'status' else None,
                                       code=result_code, discovery=discovered, download=downloaded, receipt=receipt)
            except (Exception, Interrupted, KeyboardInterrupt):
                pass
        if workdir is not None:
            remove_workdir(workdir)


if __name__ == "__main__":
    raise SystemExit(main())
