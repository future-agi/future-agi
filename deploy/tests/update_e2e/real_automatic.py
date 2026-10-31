"""Real Standalone installation, A4 witnesses and acceptance fault support.

Retained deployment files are data; updater code comes from this worktree.
Only one-time setup/deactivation may invoke bin/update: the installed scheduler
owns every upgrade and restore. No host facts or compatibility are manufactured.
Local component fixtures use the dedicated Colima context and HOME binds; full
product acceptance runs only in native amd64 CI through acceptance.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

CONTEXT = "colima-fagi-update"
PREFIX = "fagi-upd-e2e-real-"
HOME = Path.home().resolve()


def native_ci():
    return sys.platform == 'linux' and os.environ.get('GITHUB_ACTIONS') == 'true'


class SetupIncomplete(RuntimeError):
    """Setup's own closed refusal code; no other setup output leaves the private log."""
    def __init__(self, name, reason):
        self.reason = reason
        super().__init__(name + ' failed: setup incomplete: ' + reason)


def setup_failure(name, stdout):
    match = re.search(rb'^Setup incomplete: ([a-z_]{1,80})\. ', stdout or b'', re.M)
    if match is None:
        return RuntimeError(name + " failed; see private command logs")
    return SetupIncomplete(name, match.group(1).decode())
READINESS_SECONDS = 4200
CRON_PERIOD_SECONDS = 300
FAULT_SEAM = 'post-migration-stop-v1'  # Defined ONLY in deploy/tests, never in shipped runtime.


def acceptance_policy(rows, *, source_boot_seconds=None):
    """Size CI timeouts from real signed rehearsal measurements, never fixtures.

    Runtime host-size admission remains mandatory; these are configured ceilings,
    not a replacement estimate or permission to exceed the job's wall clock.
    """
    from dataclasses import replace
    from fractions import Fraction
    from math import ceil
    from deploy.update.compatibility import transition_measurements
    from deploy.update.contracts import (AUTOMATIC_POLICY_BOUNDS, POLICY_BOUNDS,
                                         AutomationPolicy, BootstrapEnvelopeMeasurement)
    measurements = [transition_measurements(row) for row in rows]
    if not measurements:
        raise ValueError('measured transition rows required')
    def transfer(name):
        # The installed A keeps running (ClickHouse system logs, WAL) for an hour
        # or more past the qualification snapshot. The estimator scales by host
        # logical bytes per role, so leave room for that growth; it still
        # refuses a host that has outgrown even this ceiling.
        return max(60, max(ceil(2 * HOST_GROWTH_ALLOWANCE * Fraction(sum(getattr(m, name).reference_volume_bytes.values())) /
            Fraction(str(getattr(m, name).throughput_bytes_per_second))) + 60 for m in measurements))
    def migration_seconds(measured):
        if isinstance(measured.migration_measurements, BootstrapEnvelopeMeasurement):
            return ceil(2*Fraction(str(measured.migration_measurements.duration_seconds)))+60
        total = Fraction(0)
        for item in measured.migration_measurements:
            work = sum(t.reference_rows if item.throughput.unit == 'rows_per_second' else t.reference_bytes
                       for t in item.tables)
            total += max(Fraction(str(item.duration_seconds)), Fraction(work)/Fraction(str(item.throughput.value)))
        return ceil(2*total)+60
    migration = max(300, max(migration_seconds(m) for m in measurements))
    startup = READINESS_SECONDS
    if source_boot_seconds is not None:
        import math
        if (isinstance(source_boot_seconds,bool) or not isinstance(source_boot_seconds,(int,float)) or
                not math.isfinite(source_boot_seconds) or not 0 < source_boot_seconds <= READINESS_SECONDS):
            raise ValueError('genuine bounded source boot measurement required')
        # The source's first boot includes ALL migrations (about 280 s on native
        # runners). Twice that measured cold boot plus 60 s is conservative for
        # startup/recovery; signed pending-migration work has its own budget.
        # The qualify CLI still gets its independent full 4200 s boot ceiling.
        # Keep a slow but valid measurement within both product policy fields'
        # documented maxima instead of delegating an over-limit value to the
        # generic AutomationPolicy malformed-policy error.
        startup_limit = min(POLICY_BOUNDS['wait_timeout_seconds'][1],
                            AUTOMATIC_POLICY_BOUNDS['recovery_startup_timeout_seconds'][1])
        startup = min(startup_limit, max(300, ceil(2*source_boot_seconds)+60))
    policy = AutomationPolicy(qualification_policy='standalone-cold-restore-v1',
        wait_timeout_seconds=startup, recovery_startup_timeout_seconds=startup,
        migration_timeout_seconds=migration, backup_timeout_seconds=transfer('backup_measurement'),
        restore_timeout_seconds=transfer('restore_measurement'), check_interval_seconds=CRON_PERIOD_SECONDS,
        pull_timeout_seconds=300, stability_period_seconds=STABILITY_SECONDS)
    # required_tick_budget covers pull plus the cold forward/recovery reserves
    # only. Every healthy-source observation before apply's pre-stop gate also
    # runs on the tick clock, each bounded by the installed probe timeout plus
    # the full stability period. Reserve them; both cold-budget gates remain.
    return replace(policy, mode='apply', enabled=True,
                   tick_timeout_seconds=max(900, policy.required_tick_budget_seconds +
                                            source_observation_seconds(policy, PRE_COLD_SOURCE_OBSERVATIONS)))


def pending_migration_names(measurements):
    """Identifiers from an already validated legacy list or bootstrap envelope."""
    if isinstance(measurements, dict):
        if measurements.get('measurement_basis') != 'normal-bootstrap-envelope-v1':
            raise ValueError('unknown migration measurement basis')
        plan = measurements.get('pending_plan')
    else:
        plan = measurements
    if not isinstance(plan, list) or any(not isinstance(row, dict) or
            not all(isinstance(row.get(key), str) and row[key] for key in ('app_label', 'name')) for row in plan):
        raise ValueError('invalid measured migration identities')
    names = {(row['app_label'], row['name']) for row in plan}
    if len(names) != len(plan):
        raise ValueError('duplicate measured migration identity')
    return names


# Native ubuntu-24.04 first boot of retained v1.53.0 including ALL migrations:
# 314 s in CI run 40965747774 (M2 startup-result.json), about 280 s in M1 runs.
# Used only to reserve acceptance time BEFORE A exists; once A boots, the job's
# own measurement sizes every updater policy (acceptance_policy above).
MEASURED_FIRST_BOOT_SECONDS = 314
STABILITY_SECONDS = 180
# Host logical growth per volume role the acceptance backup/restore ceilings allow.
HOST_GROWTH_ALLOWANCE = 3
# enrollment.configuration installs this probe timeout for every setup.
SETUP_PROBE_TIMEOUT_SECONDS = 90
# Healthy-source StandaloneStateObserver passes before the pre-stop cold gate:
# select, the before_recovery guard (two facts), apply's repeated guard (two)
# and the before_backup guard (two). Fresh setup runs four: two enrollment
# observations and two _revalidate passes.
PRE_COLD_SOURCE_OBSERVATIONS = 7
SETUP_SOURCE_OBSERVATIONS = 4
# Signature checks, capacity/backup-binding probes, pin normalization and the
# scheduler install/inspection around setup's observations.
SETUP_ANCILLARY_SECONDS = 600


def source_observation_seconds(policy, passes):
    """Upper bound on passes of the bounded healthy-source observer."""
    return passes * (max(policy.probe_timeout_seconds, SETUP_PROBE_TIMEOUT_SECONDS)
                     + policy.stability_period_seconds)


def setup_timeout_seconds(policy):
    return source_observation_seconds(policy, SETUP_SOURCE_OBSERVATIONS) + SETUP_ANCILLARY_SECONDS


def acceptance_reserve_seconds(mode):
    """LOWER bound on job time qualification must leave for the scenarios.

    Not an updater budget and not a guard waiver: it only bounds each in-job
    qualify so a hopeless overrun fails as qualification, with its diagnostics,
    instead of the acceptance stage being killed by the job clock. It is a lower
    bound on purpose: it must never cut short a qualification that could still
    be followed by a passing acceptance. The stricter per-update guard
    (scheduler_wait_budget, full measured tick plus cron delivery) still runs
    before every wait. Per fresh A install: one measured first boot (2x+60).
    Per update: a cron pickup (2 periods+60), a target start (2x boot+60) and
    the stability period. A restore adds the restored A start and the later
    latched cron wake.
    """
    # Each fresh A also needs setup; each update its pre-stop source passes.
    # Only their full stability periods are certain, which keeps this a LOWER bound.
    boot = 2*MEASURED_FIRST_BOOT_SECONDS+60 + SETUP_SOURCE_OBSERVATIONS*STABILITY_SECONDS
    update = (2*CRON_PERIOD_SECONDS+60 + 2*MEASURED_FIRST_BOOT_SECONDS+60 + STABILITY_SECONDS
              + PRE_COLD_SOURCE_OBSERVATIONS*STABILITY_SECONDS)
    if mode == 'a-b-c':
        return boot + 2*update
    if mode == 'upgrade':
        return (boot + update) + (boot + update + 2*MEASURED_FIRST_BOOT_SECONDS+60 + 2*CRON_PERIOD_SECONDS+60)
    raise ValueError('unknown acceptance mode')


def scheduler_wait_budget(policy, deadline, *, now=None):
    """One real cron delivery, a possible not-due delivery, and protected recovery."""
    now = time.monotonic() if now is None else now
    required = policy.tick_timeout_seconds + 2*CRON_PERIOD_SECONDS + 60
    if deadline-now <= required:
        raise TimeoutError('measured acceptance budgets exceed remaining native job time')
    return required


def native_launcher_path(environ):
    """Short, explicit host PATH; cron rejects commands >=1000 bytes.

    Actions' kitchen-sink PATH can be both too long and contain irrelevant
    untrusted directories. Keep the actual installed tools and system utilities,
    not links/shims in a permissive scratch directory. Production path/ancestry
    validation and scheduler installation remain untouched.
    """
    paths = []
    for name in ('docker-compose','docker','cosign'):
        executable = shutil.which(name, path=environ.get('PATH',os.defpath))
        if executable is None:
            raise ValueError('native acceptance host tool missing: '+name)
        directory = str(Path(executable).resolve(strict=True).parent)
        if directory not in paths:
            paths.append(directory)
    return os.pathsep.join(dict.fromkeys([*paths, '/usr/bin', '/bin', '/usr/sbin', '/sbin']))


def policy_cli_arguments(policy):
    names = ('wait_timeout_seconds', 'migration_timeout_seconds', 'backup_timeout_seconds',
             'restore_timeout_seconds', 'recovery_startup_timeout_seconds', 'pull_timeout_seconds',
             'tick_timeout_seconds', 'migration_stall_timeout_seconds', 'stability_period_seconds')
    return tuple(value for name in names for value in ('--'+name.replace('_','-'), str(getattr(policy,name))))


def redacted_tail(value, secret_values=(), *, limit=8192):
    """Bounded CI diagnostics only; never raw env/config/credential inventories."""
    text = value.decode('utf-8', 'replace') if isinstance(value, bytes) else str(value)
    for secret in sorted({str(v) for v in secret_values if len(str(v)) >= 6}, key=len, reverse=True):
        text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9_.~+/=-]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:password|secret|token|api[_-]?key)["\s:=]+)[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(https?://)[^/\s:@]+:[^/\s@]+@', r'\1[REDACTED]@', text)
    return ''.join(c for c in text if c in '\n\r\t' or ord(c) >= 32)[-limit:]


def _leaf_identities(paths):
    result = []
    for path in paths:
        try:
            info = os.stat(path, follow_symlinks=False)
            result.append((info.st_dev, info.st_ino))
        except FileNotFoundError:
            result.append(None)
    return tuple(result)


def replacement_tolerant(read, paths, *, attempts=5):
    """Re-run a strict read-only oracle ONLY when a watched leaf was atomically
    replaced while it was being read (the opened old inode then has no link).

    The installed scheduler publishes state.json and recovery.json by
    temp+rename while the test polls them. An unchanged corrupt, unsafe or
    wrongly owned file still fails: its identity is the same before and after.
    """
    from deploy.update.runner_state import StoreError
    from deploy.update.standalone_backup import BackupError
    for attempt in range(attempts):
        before = _leaf_identities(paths)
        try:
            return read()
        except (StoreError, BackupError):
            if attempt + 1 == attempts or _leaf_identities(paths) == before:
                raise
            time.sleep(0.05)


def scheduler_history(store, backup_destination, containers=(), *, activation=None):
    """Closed, token-free projection of what the installed scheduler did.

    Cron discards tick output, so after a scheduler wait times out this is the
    only record of each tick's outcome. Versions, states, phases and reason
    codes only: no operation tokens, paths, env or logs. Read only after the
    wait, never while asserting; never qualification evidence.
    """
    from deploy.update.baseline import BaselineStore
    from deploy.update.receipt import StateDir
    from deploy.update.runner_state import StoreBusy
    when = lambda value: None if value is None else value.isoformat()
    version = lambda target: None if target is None else target.version
    closed = lambda values: [v for v in values if isinstance(v, str) and re.fullmatch(r'[a-z_]{1,80}', v)]
    result = dict(schema_version=1, qualification_evidence=False, errors=[])

    def read(label, function):
        for _ in range(10):
            try:
                return replacement_tolerant(function, [store.path / 'state.json'])
            except StoreBusy:
                time.sleep(1)
            except Exception as exc:
                result['errors'].append(label + ':' + type(exc).__name__)
                return None
        result['errors'].append(label + ':busy')
        return None
    state = read('state', store.read_state)
    if state is not None:
        result['state'] = dict(last_tick_at=when(state.last_tick_at), last_attempt_at=when(state.last_attempt_at),
            last_successful_check_at=when(state.last_successful_check_at), next_check_at=when(state.next_check_at),
            retry_count=state.retry_count, retry_not_before=when(state.retry_not_before),
            cached_candidate=version(state.cached_candidate_identity), download=version(state.download_identity),
            active_attempt=state.active_attempt_id is not None, failed_target_latch=version(state.failed_target_latch))
    events = read('events', store.read_events)
    if events is not None:
        result['events'] = [dict(sequence=r.sequence, target=version(r.target), state=r.event.state, kind=r.event.kind,
                                 stage=r.event.stage, phase=r.event.phase, reported_at=when(r.event.reported_at),
                                 reason_codes=[c.value for c in r.event.reason_codes]) for r in events]
    control = read('control', store.read_control)
    if control is not None:
        result['control'] = dict(enabled_override=control.enabled_override, pause_until=when(control.pause_until),
                                 pause_indefinite=control.pause_indefinite)
    if activation is not None:
        result['activation'] = dict(state=activation.state, reason=getattr(activation.reason, 'value', None))
    head = read('baseline', BaselineStore(store).current)
    result['baseline_version'] = None if head is None else head.release.version
    try:
        state_dir = StateDir(store.checkout)
        result['receipts'] = [dict(kind=r.get('kind'), status=r.get('status'), target_version=r.get('target_version'),
                                   reason_codes=closed(r.get('reason_codes') or r.get('reasons') or []))
                              for r in (state_dir.load_receipt(path) for path in state_dir.receipts())]
    except Exception as exc:
        result['errors'].append('receipts:' + type(exc).__name__)
    attempts = []
    for ledger in sorted(Path(backup_destination).glob('*/recovery.json')):
        try:
            document, leaves = read_attempt_ledger(ledger.parent)
            attempts.append(dict(direction=document.get('direction'), phase=document.get('phase'),
                                 leaves=sorted(name for name in leaves if re.fullmatch(r'[a-z0-9._-]{1,64}', name))))
        except Exception as exc:
            attempts.append(dict(error=type(exc).__name__))
    result['attempts'] = attempts
    result['running'] = {c['Config'].get('Labels', {}).get('com.docker.compose.service'):
                         dict(version=c['Config'].get('Labels', {}).get('org.opencontainers.image.version'),
                              running=bool(c.get('State', {}).get('Running')),
                              health=(c.get('State', {}).get('Health') or {}).get('Status'))
                         for c in containers}
    return result


REPLAY = r"""
import importlib.util, json, os, sys, traceback
root = sys.argv[1]
package = os.path.join(root, "deploy", "update")
spec = importlib.util.spec_from_file_location(
    "futureagi_update", os.path.join(package, "__init__.py"), submodule_search_locations=[package])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
import futureagi_update.runner as runner
original = runner.HostRunner._refusal
def refusal(exc):
    frames = [(os.path.basename(f.filename), f.lineno, f.name) for f in traceback.extract_tb(exc.__traceback__)
              if os.path.abspath(f.filename).startswith(package + os.sep)]
    code = getattr(getattr(exc, "reason_code", None), "value", None)
    facts = {}
    trace = exc.__traceback__
    try:
        facts.update(collect(trace))
    except Exception:
        facts["error"] = "facts_unavailable"
    print("FAGI-REFUSAL " + json.dumps({"reason_code": code, "exception_type": type(exc).__name__,
                                        "frames": frames[-12:], "facts": facts}, default=str), flush=True)
    return original(exc)
def collect(trace):
    facts = {}
    while trace is not None:
        local = trace.tb_frame.f_locals
        plan = getattr(local.get("prepared"), "plan", None)
        if isinstance(plan, dict):
            # Names and counts only: env key NAMES, deployment file names, never values or reason text.
            drift = plan.get("deployment_drift") or {}
            facts["plan"] = {"decision": plan.get("decision"), "resume": bool(plan.get("resume")),
                             "missing_env_keys": sorted(map(str, plan.get("missing_env_keys") or []))[:40],
                             "deployment_drift": sorted(map(str, drift if isinstance(drift, (dict, list)) else []))[:40],
                             "reasons": len(plan.get("reasons") or [])}
        if trace.tb_frame.f_code.co_name == "observe" and "head" in local and "observation" in local:
            head, observation = local["head"], local["observation"]
            obs = observation.facts
            from futureagi_update.baseline import sha
            facts["baseline"] = {"config": obs.config_fingerprint == local.get("config", head.config_fingerprint),
                                 "profile": obs.profile_fingerprint == head.profile_fingerprint,
                                 "env": sha(observation.env_bytes) == local.get("env_hash", head.env_sha256),
                                 "structure": observation.structure_sha256 == head.structure_sha256}
        trace = trace.tb_next
    return facts
runner.HostRunner._refusal = staticmethod(refusal)
import time
try:
    import futureagi_update.automatic as automatic
    original_preflight = automatic.AutomaticLifecycle.preflight
    ESTIMATE = ("backup_seconds", "migration_seconds", "restore_seconds", "extra_disk_bytes",
                "backup_required_free_bytes", "docker_required_free_bytes", "protected_restore_reserve_bytes",
                "forward_seconds", "recovery_seconds", "total_seconds", "migration_deadline_seconds",
                "migration_stall_timeout_seconds")
    def preflight(self, installation):
        decision = original_preflight(self, installation)
        try:
            estimate = decision.estimate
            print("FAGI-PREFLIGHT " + json.dumps({"allowed": decision.allowed,
                "reason_codes": [c.value for c in decision.reason_codes],
                "estimate": {} if estimate is None else {k: getattr(estimate, k) for k in ESTIMATE},
                "tick_remaining_seconds": int(installation.runner.deadline - time.monotonic())}), flush=True)
        except Exception:
            print("FAGI-PREFLIGHT " + json.dumps({"error": "facts_unavailable"}), flush=True)
        return decision
    automatic.AutomaticLifecycle.preflight = preflight
    import futureagi_update.standalone_state as standalone_state
    original_measure = standalone_state.StandaloneStateObserver.measure_update_sizes
    def measure(self, *args, **kwargs):
        try:
            return original_measure(self, *args, **kwargs)
        except Exception as exc:
            frames = [(os.path.basename(f.filename), f.lineno, f.name) for f in traceback.extract_tb(exc.__traceback__)
                      if os.path.abspath(f.filename).startswith(package + os.sep)]
            print("FAGI-SIZES " + json.dumps({"exception_type": type(exc).__name__,
                "reason_code": getattr(getattr(exc, "reason_code", None), "value", None), "frames": frames[-12:]}), flush=True)
            raise
    standalone_state.StandaloneStateObserver.measure_update_sizes = measure
    original_sizes = standalone_state.StandaloneStateObserver._measure_sizes
    def sizes(self, *args, **kwargs):
        result = original_sizes(self, *args, **kwargs)
        try:
            print("FAGI-VOLUMES " + json.dumps({v.role: {"logical": v.logical_bytes, "used": v.used_bytes,
                "members": v.members} for v in result[1].volumes}), flush=True)
        except Exception:
            pass
        return result
    standalone_state.StandaloneStateObserver._measure_sizes = sizes
except (ImportError, AttributeError):
    pass  # optional facts; the refusal hook alone still applies
from futureagi_update.__main__ import main
sys.exit(main(sys.argv[2:]))
"""


def closed_replay_result(returncode, output):
    """Keep only closed tokens from a diagnostic replay's output."""
    from deploy.update.contracts import ReasonCode
    codes = {code.value for code in ReasonCode}
    refusals = []
    for line in output.splitlines():
        if not line.startswith('FAGI-REFUSAL '):
            continue
        try:
            row = json.loads(line[len('FAGI-REFUSAL '):])
            frames = [(name, number, function) for name, number, function in row['frames']
                      if re.fullmatch(r'[a-z0-9_]{1,64}\.py', name) and type(number) is int
                      and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}|<[a-z]+>', function)]
            facts = {}
            plan = (row.get('facts') or {}).get('plan')
            if isinstance(plan, dict):
                name = lambda v: isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_./-]{1,128}', v) and '..' not in v
                facts['plan'] = dict(decision=plan.get('decision') if name(plan.get('decision')) else None,
                    resume=plan.get('resume') is True, reasons=plan.get('reasons') if type(plan.get('reasons')) is int else None,
                    missing_env_keys=[k for k in plan.get('missing_env_keys') or [] if name(k) and '/' not in k],
                    deployment_drift=[k for k in plan.get('deployment_drift') or [] if name(k) and not k.startswith('/')])
            baseline = (row.get('facts') or {}).get('baseline')
            if isinstance(baseline, dict):
                facts['baseline'] = {k: baseline.get(k) is True for k in ('config', 'profile', 'env', 'structure')}
            refusals.append(dict(reason_code=row['reason_code'] if row['reason_code'] in codes else None,
                                 exception_type=row['exception_type']
                                 if re.fullmatch(r'[A-Za-z_]\w{0,63}', str(row['exception_type'])) else None,
                                 frames=frames, facts=facts))
        except (ValueError, KeyError, TypeError):
            refusals.append(dict(error='unparsed'))
    result = dict(exit_code=returncode, refusals=refusals[:5])
    estimate_fields = ('backup_seconds', 'migration_seconds', 'restore_seconds', 'extra_disk_bytes',
                       'backup_required_free_bytes', 'docker_required_free_bytes', 'protected_restore_reserve_bytes',
                       'forward_seconds', 'recovery_seconds', 'total_seconds', 'migration_deadline_seconds',
                       'migration_stall_timeout_seconds')
    frame_ok = lambda name, number, function: (re.fullmatch(r'[a-z0-9_]{1,64}\.py', str(name)) and type(number) is int
                                               and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}|<[a-z]+>', str(function)))
    for line in output.splitlines():
        try:
            if line.startswith('FAGI-PREFLIGHT '):
                row = json.loads(line[len('FAGI-PREFLIGHT '):])
                estimate = row.get('estimate') or {}
                result.setdefault('preflight', []).append(dict(allowed=row.get('allowed') is True,
                    reason_codes=[c for c in row.get('reason_codes') or [] if c in codes],
                    estimate={k: estimate[k] for k in estimate_fields if type(estimate.get(k)) is int},
                    tick_remaining_seconds=row.get('tick_remaining_seconds')
                    if type(row.get('tick_remaining_seconds')) is int else None))
            elif line.startswith('FAGI-VOLUMES '):
                row = json.loads(line[len('FAGI-VOLUMES '):])
                result['volumes'] = {role: {k: row[role][k] for k in ('logical', 'used', 'members')
                                            if type(row[role].get(k)) is int}
                                     for role in ('app-data', 'postgres-data', 'clickhouse-data')
                                     if isinstance(row.get(role), dict)}
            elif line.startswith('FAGI-SIZES '):
                row = json.loads(line[len('FAGI-SIZES '):])
                result.setdefault('sizes', []).append(dict(
                    exception_type=row.get('exception_type') if re.fullmatch(r'[A-Za-z_]\w{0,63}', str(
                        row.get('exception_type'))) else None,
                    reason_code=row.get('reason_code') if row.get('reason_code') in codes else None,
                    frames=[tuple(f) for f in row.get('frames') or [] if len(f) == 3 and frame_ok(*f)]))
        except (ValueError, TypeError, AttributeError):
            result.setdefault('errors', []).append('unparsed')
    for line in output.splitlines():
        if line.startswith('{'):
            try:
                summary = json.loads(line)
            except ValueError:
                continue
            if isinstance(summary, dict) and 'disposition' in summary:
                result['disposition'] = summary['disposition'] if re.fullmatch(r'[a-z_]{1,40}', str(
                    summary['disposition'])) else None
                result['reason_codes'] = [c for c in summary.get('reason_codes') or [] if c in codes]
                result['skip_reason'] = summary.get('skip_reason') if re.fullmatch(r'[a-z_]{1,40}', str(
                    summary.get('skip_reason'))) else None
    types = re.findall(r'^([A-Za-z_][\w.]*(?:Error|Exception|Refused|Blocked|Failure|Interrupted))(?::|$)', output, re.M)
    result['exception_types'] = sorted(set(types))[:20]
    return result


def diagnostic_scheduler_replay(store, *, timeout=900, wait_seconds=900):
    """Replay ONE scheduled run, exactly as cron launches it, after a failed wait.

    Same registered launcher environment (env -i HOME/PATH/PYTHON/DOCKER_CONTEXT)
    and the same bin/update package loader; the only addition reports which
    updater frames raised the run's refusal. Never part of acceptance: it runs
    only after the scheduler assertion already failed, so it cannot make a
    journey pass. Output is reduced to closed tokens.
    """
    import signal
    registration = store.read_registration()
    binding, launch = registration.binding, registration.launcher
    root = str(binding.checkout)
    argv = ['/usr/bin/env', '-i', 'HOME=' + str(launch.home), 'PATH=' + launch.path, 'PYTHON=' + str(launch.python),
            'DOCKER_CONTEXT=' + binding.docker_context, str(launch.python), '-I', '-c', REPLAY, root,
            '--root', root, '--cwd', root, 'run', '--env-file', str(binding.env_alias)]
    result = dict(error='busy')
    # A cron run holds runner.lock for minutes; retry between deliveries.
    give_up = time.monotonic() + wait_seconds
    while True:
        process = subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return dict(error='timeout', seconds=timeout)
        result = closed_replay_result(process.returncode, output.decode('utf-8', errors='replace'))
        if result.get('disposition') != 'busy' or time.monotonic() >= give_up:
            return result
        time.sleep(15)


def read_attempt_ledger(directory):
    return replacement_tolerant(lambda: _read_attempt_ledger(directory), [Path(directory) / 'recovery.json'])


def _read_attempt_ledger(directory):
    """Bounded read-only test oracle. RecoveryLedger.open() may reconcile writes.

    Do not let a test finalize the scheduler's transaction just by inspecting
    it. Read its checkpoint and immutable decision leaves without calling any
    mutation/recovery API; ambiguity fails the acceptance instead of repairing it.
    """
    from deploy.update.compatibility import decode_document
    from deploy.update.recovery import MAX_LEDGER_BYTES
    from deploy.update.standalone_backup import _directory, _read
    with _directory(Path(directory)) as fd:
        doc = decode_document(_read(fd, 'recovery.json', MAX_LEDGER_BYTES), MAX_LEDGER_BYTES)
        leaves = set(os.listdir(fd))
    if doc.get('schema_version') != 2 or doc.get('attempt_id') != Path(directory).name:
        raise AssertionError('automatic ledger identity differs')
    return doc, leaves


def read_event_records(store):
    """Lock-free, read-only oracle for the installed scheduler's event leaves.

    RunnerStateStore.read_events() takes state.lock NON-blocking. Polling it
    while the real updater runs could make the updater's own state write fail
    StoreBusy (a busy tick, or a contended restore commit), so the test would
    perturb the transaction it observes. Event leaves are written atomically
    (temp + rename, dot-prefixed temps), so read them without any lock; a leaf
    pruned between listing and opening is skipped, never invented.
    """
    from deploy.update.runner_state import MAX_DOCUMENT_BYTES, EventRecord, _decode
    directory = Path(store.path) / 'events'
    try:
        names = sorted(name for name in os.listdir(directory) if re.fullmatch(r'[0-9]{20}\.json', name))
    except FileNotFoundError:
        return ()
    records = []
    for name in names:
        try:
            fd = os.open(str(directory / name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        except FileNotFoundError:
            continue
        with os.fdopen(fd, 'rb') as handle:
            raw = handle.read(MAX_DOCUMENT_BYTES + 1)
        record = _decode(EventRecord, raw)
        if name != '%020d.json' % record.sequence:
            raise AssertionError('scheduler event leaf identity differs')
        records.append(record)
    return tuple(records)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def payload_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def home_path(path: Path) -> Path:
    resolved = Path(path).resolve()
    if HOME not in resolved.parents or resolved == HOME:
        raise ValueError("real product files and bind mounts must be below " + str(HOME))
    return resolved


def resource_name(name: str) -> str:
    if not re.fullmatch(r"fagi-upd-e2e-real-[a-z0-9][a-z0-9_-]{0,100}", name):
        raise ValueError("resource outside real-product lane")
    return name


def validate_model(model: Mapping[str, Any], project: str) -> None:
    resource_name(project)
    services = model.get("services", {})
    if set(services) != {"app", "postgres", "clickhouse"}:
        raise ValueError("only the three-service base Standalone is supported")
    if set(model.get("volumes", {})) != {"app-data", "postgres-data", "clickhouse-data"}:
        raise ValueError("unexpected persistent stores")
    for volume in model["volumes"].values():
        if (volume.get("external") or volume.get("driver_opts")
                or volume.get("driver", "local") != "local"
                or not volume.get("name", "").startswith(project + "_")):
            raise ValueError("foreign or nonlocal named volume")
    for service in services.values():
        if (service.get("build") or service.get("privileged") or service.get("container_name")
                or service.get("network_mode") or service.get("volumes_from")):
            raise ValueError("unsupported service override")
        if service.get("restart", "no") not in ("no", "unless-stopped"):
            raise ValueError("unsupported restart policy")
        for mount in service.get("volumes", []):
            if mount.get("type") == "bind":
                # Native Actions keeps the historical product's exact /dev/null
                # bind. Local Docker tests never mount anything outside HOME.
                if not (native_ci() and mount['source'] == '/dev/null'):
                    home_path(Path(mount["source"]))
                if not mount.get("read_only"):
                    raise ValueError("product configuration binds must be read-only")
            elif mount.get("type") != "volume" or mount.get("source") not in model["volumes"]:
                raise ValueError("unexpected volume mount")
    for network in model.get("networks", {}).values():
        if network.get("external") or not network.get("name", "").startswith(project + "_"):
            raise ValueError("foreign network")


def inspect_archive(path: Path, inventory: Path, *, max_members=1000000,
                    max_logical_bytes=16 * 1024 ** 3, timeout=900) -> dict:
    """Inventory a cold fixture as data, without extraction or product claims.

    Complete stream hashes plus every member's payload/metadata are retained.
    This is NOT the shipping restoration verifier or a qualification proof.
    """
    count = logical = 0
    deadline = time.monotonic() + timeout
    seen = set()
    with tarfile.open(path, mode="r|") as archive, inventory.open("x", encoding="utf-8") as out:
        for member in archive:
            if count >= max_members or time.monotonic() >= deadline:
                raise ValueError("archive inventory budget exceeded")
            name = member.name
            parts = Path(name).parts
            normalized = str(Path(name))
            if len(name) > 4096 or name.startswith("/") or ".." in parts or normalized in seen:
                raise ValueError("unsafe or duplicate archive member")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError("unsupported archive member type")
            seen.add(normalized)
            digest = None
            if member.isfile():
                if member.size < 0 or logical + member.size > max_logical_bytes:
                    raise ValueError("archive logical size budget exceeded")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("missing member payload")
                h = hashlib.sha256()
                read = 0
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    if time.monotonic() >= deadline:
                        raise ValueError("archive inventory budget exceeded")
                    h.update(chunk)
                    read += len(chunk)
                if read != member.size:
                    raise ValueError("truncated member")
                digest = h.hexdigest()
                logical += read
            out.write(canonical(dict(name=name, type=member.type.decode("ascii"),
                                     size=member.size, uid=member.uid, gid=member.gid,
                                     mode=member.mode, mtime=member.mtime,
                                     link=member.linkname, pax=member.pax_headers,
                                     sha256=digest)).decode() + "\n")
            count += 1
            archive.members.clear()
        out.flush()
        os.fsync(out.fileno())
    if not count or path.stat().st_size % 512:
        raise ValueError("incomplete archive")
    with path.open("rb") as stream:
        stream.seek(-1024, os.SEEK_END)
        if stream.read() != b"\0" * 1024:
            raise ValueError("missing tar end marker")
    return dict(path=str(path), sha256=file_hash(path), archive_bytes=path.stat().st_size,
                logical_bytes=logical, members=count, inventory_path=str(inventory),
                inventory_sha256=file_hash(inventory))


class ProductHarness:
    """Own one fresh home-backed installation; never clean other runs by prefix."""

    def __init__(self, evidence: Path, source: Path, project: str, *,
                 context: str = CONTEXT, compose: str = "/opt/homebrew/bin/docker-compose"):
        if context != CONTEXT:
            raise ValueError("dedicated Docker context required")
        self.evidence = home_path(evidence)
        self.source = Path(source).resolve()
        self.project = resource_name(project)
        self.context = context
        self.root = self.evidence / "installation"
        self.compose = ([compose] if isinstance(compose, str) else list(compose or ['docker', 'compose']))
        self.source_manifest = None
        # Shared ProductWitnesses records generated secrets for bounded driver
        # diagnostics; acceptance keeps the same driver interface.
        self._secret_values = set()
        # Never inherit an operator's provider keys, Compose overlays or profiles.
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
        self.env["DOCKER_CONTEXT"] = CONTEXT
        self.sequence = max((int(p.stem.split("-")[1]) for p in self.evidence.glob("command-*.json")), default=0)
        self.model: Optional[dict] = None
        self.ports: dict = {}

    def retain_deployment(self, directory, manifest):
        from scripts.update_rehearsal_shipping import copy_retained_files
        from deploy.update.manifest import validate_manifest
        if self.root.exists():
            raise ValueError('new product installation required')
        self.source_manifest = validate_manifest(manifest)
        self.root.mkdir(mode=0o700)
        copy_retained_files(Path(directory), self.root, manifest['deployment'])
        # Native binds retain host modes. Only the shipped ClickHouse XML data
        # needs uid-101 readability; no env, generated secret or evidence file
        # is made public, and retained deployment bytes/hashes do not change.
        from deploy.update.standalone_backup import BINDS
        for name in BINDS['clickhouse'].values():
            (self.root/name).chmod(0o644)

    def install(self):
        if self.source_manifest is None:
            raise ValueError('retained source manifest required')
        from deploy.update.manifest import parse_image_ref
        component = self.source_manifest['components']['standalone']
        self.prepare(component['digest'], version=self.source_manifest['version'])
        # Enrollment normalizes an installer-shaped VERSION, not VERSION@digest.
        # Pull the immutable native leaf first (real RepoDigest), then establish
        # only its ordinary installer alias. Never pull the index/plain app tag
        # over it: an index digest would change the observed signed identity.
        pinned = component['image']+'@'+component['digest']
        self.docker('pull','--platform','linux/amd64',pinned,timeout=1800)
        image = json.loads(self.docker('image','inspect',pinned).stdout)[0]
        expected = parse_image_ref(component['image'])
        if (image.get('Os'), image.get('Architecture')) != ('linux','amd64') or not any(
                parse_image_ref(ref).name == expected.name and parse_image_ref(ref).digest == component['digest']
                for ref in image.get('RepoDigests', [])):
            raise AssertionError('source pull lacks the frozen native RepoDigest')
        app_image = self.model['services']['app']['image']
        if parse_image_ref(app_image).name != expected.name:
            raise AssertionError('retained app repository differs from source release')
        self.docker('image','tag',pinned,app_image)
        alias = json.loads(self.docker('image','inspect',app_image).stdout)[0]
        if alias['Id'] != image['Id']:
            raise AssertionError('source installer alias differs from frozen image')
        for name, service in self.model['services'].items():
            if name != 'app':
                self.docker('pull', service['image'], timeout=1800)
        return self.boot_source()

    def bin_update(self, *args, check=True, timeout=600):
        # Tests may enroll/deactivate, never drive scheduler run/tick/apply.
        if not args or args[0] not in ('setup', 'reporting'):
            raise ValueError('acceptance may only call one-time setup/deactivation or status reporting')
        # Resolve real launcher prerequisites at the setup boundary, not when a
        # daemon-free unit fixture merely constructs a harness. Native acceptance
        # also probes this cleared environment before expensive qualification.
        if native_ci():
            self.env['PATH'] = native_launcher_path(self.env)
        previous = self.env.get('PYTHON')
        self.env['PYTHON'] = sys.executable
        try:
            result = self.command([str(self.root/'bin/update'), *args], timeout=timeout, check=False)
        finally:
            if previous is None:
                self.env.pop('PYTHON', None)
            else:
                self.env['PYTHON'] = previous
        if check and result.returncode:
            # A CI run cannot be inspected afterwards: keep the refusal code.
            raise setup_failure('command-{:04d}'.format(self.sequence), result.stdout)
        return result

    def receipts(self):
        from deploy.update.receipt import StateDir
        state = StateDir(self.root)
        return [state.load_receipt(path) for path in state.receipts()]

    def app_python(self, code, data):
        # The exact invocation and strict final-line decoder that seeded the
        # witnesses on a real runner in milestone 1 (shipping driver): isolated
        # interpreter, -c joined to its source (a bare -c is Docker's context
        # flag to the production process guard), JSON on stdin.
        from scripts.update_rehearsal_shipping import decode_product_output
        result = self.docker('exec', '-i', self.service_id('app'), 'python', '-I', '-B', '-c' + code,
                              input=canonical(data), timeout=180)
        return decode_product_output(result.stdout)

    def clickhouse(self, sql, *, params=None):
        from deploy.update.envfile import read_env
        env = read_env(self.root/'.env')
        args = ['exec', self.service_id('clickhouse'), 'clickhouse-client',
                '--password', env['CH_PASSWORD'], '--query', sql]
        args.extend('--param_'+key+'='+str(value) for key, value in (params or {}).items())
        return [json.loads(line) for line in self.docker(*args).stdout.splitlines() if line]

    def migration_inventory(self):
        from scripts.update_rehearsal_shipping import DJANGO_PREFIX
        return self.app_python(DJANGO_PREFIX + '''
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from pathlib import Path
import hashlib, inspect
loader=MigrationLoader(connection,ignore_no_migrations=True)
planned=sorted([list(node) for node in loader.graph.nodes])
applied=sorted([list(node) for node in loader.applied_migrations])
inventory=[dict(app_label=app,name=name,source_sha256=hashlib.sha256(
    Path(inspect.getsourcefile(migration.__class__)).read_bytes()).hexdigest())
    for (app,name),migration in sorted(loader.disk_migrations.items())]
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
print(json.dumps({'planned':planned,'applied':applied,'pending':[n for n in planned if tuple(n) not in loader.applied_migrations],
    'inventory_sha256':digest(inventory),'applied_sha256':digest(applied)}))
''', {})

    def verify_installed_release(self, manifest, receipt, migration_evidence):
        """Independent product oracle, not a baseline/receipt-only success check."""
        from deploy.update.manifest import manifest_hash, parse_image_ref
        rows = self.containers()
        services = {c['Config']['Labels'].get('com.docker.compose.service'): c for c in rows}
        if len(rows) != 3 or set(services) != {'app', 'postgres', 'clickhouse'}:
            raise AssertionError('installed service roster differs')
        if any(not c['State']['Running'] or c['State'].get('Health', {}).get('Status') != 'healthy'
               for c in rows):
            raise AssertionError('installed target is not healthy')
        target = manifest['components']['standalone']
        image = json.loads(self.docker('image', 'inspect', services['app']['Image']).stdout)[0]
        expected = parse_image_ref(target['image'])
        if not any(parse_image_ref(ref).name == expected.name and parse_image_ref(ref).digest == target['digest']
                   for ref in image.get('RepoDigests', [])):
            raise AssertionError('installed app is not the signed target image')
        if (receipt.get('kind') != 'apply' or receipt.get('status') != 'succeeded'
                or receipt.get('target_version') != manifest['version']
                or receipt.get('manifest_hash') != manifest_hash(manifest)
                or receipt.get('target_image_ids', {}).get('app') != services['app']['Image']):
            raise AssertionError('success receipt does not identify the installed target')
        migrations = self.migration_inventory()
        if (not migrations['planned'] or migrations['pending'] or migrations['applied'] != migrations['planned']
                or migrations['inventory_sha256'] != migration_evidence['target_inventory_sha256']
                or migrations['applied_sha256'] != migration_evidence['target_applied_set_sha256']):
            raise AssertionError('installed target migration inventory differs from qualification')
        status, _ = self.request(self.ports['BACKEND_PORT'], '/health/')
        if status != 200:
            raise AssertionError('installed target API is not ready')
        self.record('installed-'+manifest['version']+'.json', dict(
            version=manifest['version'], run_id=receipt['run_id'], images={s: c['Image'] for s,c in services.items()},
            manifest_sha256=manifest_hash(manifest), target_digest=target['digest'],
            migration_inventory_sha256=migrations['inventory_sha256'],
            applied_set_sha256=migrations['applied_sha256'], applied_count=len(migrations['applied']), pending_count=0))
        return migrations

    def seed_and_check(self, generation='acceptance-a'):
        from scripts.update_rehearsal_shipping import ProductWitnesses
        from scripts.rehearse_update_compatibility import ManualRelease
        if not hasattr(self, '_witnesses'):
            self.corpus, self._witness_generations = {}, {}
            self._witnesses = ProductWitnesses(self)
        witnesses = self._witnesses
        try:
            initial = witnesses.create(generation)
        except ManualRelease as error:
            # A4 permits only the explicit natural-spool operation gap. Core
            # API/collector/S3/workflow failures still fail acceptance.
            if str(error) != 'witness_operation_unavailable' or not witnesses.namespaces.get(generation):
                raise
            initial = witnesses.read(generation)
        domains = {row['domain'] for row in initial}
        if not {'postgres_app','clickhouse','object_store','temporal'} <= domains:
            raise AssertionError('incomplete real product witness corpus')
        self._witness_generations[generation] = initial
        self.record('witness-inventory.private.json', dict(generations=self._witness_generations,
                    gaps=self.corpus.get('witness_gaps', [])))
        def check():
            for label, expected in self._witness_generations.items():
                if witnesses.read(label) != expected:
                    raise AssertionError('exact product witness identities/payloads changed: '+label)
            migrations = self.migration_inventory()
            if not migrations['planned'] or migrations['pending'] or migrations['applied'] != migrations['planned']:
                raise AssertionError('bootstrap migration set is not exact')
            return migrations
        check()
        return check

    def create_candidate_only_witness(self):
        """A real local write made only after B bootstrap, outside A's snapshot."""
        import base64
        import uuid
        from scripts.update_rehearsal_shipping import S3_OPERATION
        body = os.urandom(4096)
        data = dict(action='create',bucket='failed-update-'+uuid.uuid4().hex,key='candidate-only',
                    body=base64.b64encode(body).decode())
        observed = self.app_python(S3_OPERATION, data)
        expected = dict(identity=data['bucket']+'/'+data['key'],payload_sha256=hashlib.sha256(body).hexdigest())
        if observed != expected:
            raise AssertionError('candidate-only S3 write was not independently read back')
        self._candidate_only = dict(bucket=data['bucket'],key=data['key'],**expected)
        self.record('candidate-only-witness.private.json', self._candidate_only)
        return expected

    def verify_candidate_only_absent(self):
        # Accept only the service's explicit NoSuchKey/NoSuchBucket/404 response;
        # unavailable credentials, a timeout, or a broken S3 server are NOT absence.
        data = self._candidate_only
        observed = self.app_python('''
import sys,os,json,boto3
from botocore.exceptions import ClientError
a=json.load(sys.stdin)
s=boto3.client('s3',endpoint_url='http://127.0.0.1:9005',
    aws_access_key_id=os.environ['MINIO_ROOT_USER'],aws_secret_access_key=os.environ['MINIO_ROOT_PASSWORD'],
    region_name='us-east-1')
absent=False
try:
    s.head_object(Bucket=a['bucket'],Key=a['key'])
except ClientError as error:
    if str(error.response.get('Error',{}).get('Code')) not in ('NoSuchKey','NoSuchBucket','404'):
        raise
    absent=True
print(json.dumps({'identity':a['identity'],'absent':absent}))
''', data)
        if observed != dict(identity=data['identity'],absent=True):
            raise AssertionError('candidate-only local write survived snapshot restoration')
        self.record('candidate-only-restored-absence.json', observed)

    def pending_forward_attempt(self, manifest):
        """Read the enrolled transaction before an external acceptance fault.

        A healthy B that has already crossed success_decided is not an eligible
        fault-injection boundary. Never claim restore acceptance for an ordinary
        outage after success or write a fake readiness/ledger outcome.
        """
        from deploy.update.enrollment import load_setup_configuration
        from deploy.update.host_facts import selected_store
        from deploy.update.manifest import manifest_hash
        store = selected_store(self.root)
        current = store.read_state()
        if current.active_attempt_id is None:
            raise AssertionError('target has no active pre-success automatic attempt')
        attempt = store.read_attempt(current.active_attempt_id)
        config = load_setup_configuration(store)
        ledger, leaves = read_attempt_ledger(config.backup_destination/attempt.attempt_id)
        if (attempt.target.manifest_hash != manifest_hash(manifest)
                or ledger['operation_token'] != attempt.operation_token
                or ledger['expectation']['target_manifest_hash'] != manifest_hash(manifest)
                or ledger['direction'] != 'forward_pending'
                or {'success-decision.json', 'restore-decision.json'} & leaves):
            raise AssertionError('fault boundary is not the exact pending target')
        return ledger['run_id']

    def fail_after_migrations(self, manifest, *, timeout=5400):
        """Induce a real outage on the exact B app after bootstrap, not a tick.

        No env/model/proof/ledger edits and no manual migration. The installed
        scheduler must detect this within the configured stability period and
        execute the mandatory restore itself.
        """
        from deploy.update.manifest import parse_image_ref
        target = manifest['components']['standalone']
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            app = [c for c in self.containers() if c['Config']['Labels'].get('com.docker.compose.service') == 'app'
                   and c['State']['Running']]
            if len(app) == 1:
                image = json.loads(self.docker('image','inspect',app[0]['Image']).stdout)[0]
                if any(parse_image_ref(ref).digest == target['digest'] and
                       parse_image_ref(ref).repository == parse_image_ref(target['image']).repository
                       for ref in image.get('RepoDigests', [])):
                    # /health is exposed only after the real bootstrap returns.
                    try:
                        status, _ = self.request(self.ports['BACKEND_PORT'], '/health/', timeout=2)
                    except (OSError, URLError):
                        status = 0
                    if status == 200:
                        migrations = self.migration_inventory()
                        if migrations['planned'] and not migrations['pending']:
                            run_id = self.pending_forward_attempt(manifest)
                            candidate_only = self.create_candidate_only_witness()
                            if self.pending_forward_attempt(manifest) != run_id:
                                raise AssertionError('automatic attempt changed before fault')
                            current = json.loads(self.docker('inspect',app[0]['Id']).stdout)[0]
                            labels = current['Config']['Labels']
                            if (current['Id'] != app[0]['Id'] or current['Image'] != app[0]['Image'] or
                                    not current['State']['Running'] or
                                    labels.get('com.docker.compose.project') != self.project or
                                    labels.get('com.docker.compose.service') != 'app'):
                                raise AssertionError('target identity changed before fault')
                            if self.pending_forward_attempt(manifest) != run_id:
                                raise AssertionError('target crossed the success decision before fault')
                            self.record('failure-after-migrations.json', dict(fault_seam=FAULT_SEAM,
                                target=manifest['version'], run_id=run_id,
                                image_id=current['Image'], container_id=current['Id'], migrations=migrations,
                                candidate_only=candidate_only))
                            self.docker('stop','--time','10',current['Id'],timeout=60)
                            return migrations
            time.sleep(1)
        raise TimeoutError('installed scheduler did not reach real target bootstrap boundary')

    def record(self, name: str, value: Any) -> Path:
        path = self.evidence / name
        path.write_bytes(canonical(value) + b"\n")
        path.chmod(0o600)
        return path

    def command(self, args, *, timeout=120, check=True, output=None, input=None,
                max_output_bytes=None):
        self.sequence += 1
        name = "command-{:04d}".format(self.sequence)
        started = time.monotonic()
        limiter = None
        if max_output_bytes is not None:
            if output is None or max_output_bytes <= 0:
                raise ValueError("output limit requires a regular output file")
            import resource

            def limiter():
                resource.setrlimit(resource.RLIMIT_FSIZE, (max_output_bytes, max_output_bytes))

        with (self.evidence / (name + ".stderr")).open("wb") as err:
            result = subprocess.run(args, cwd=str(self.source), env=self.env,
                                    input=input, stdout=output or subprocess.PIPE,
                                    stderr=err, timeout=timeout, preexec_fn=limiter)
        if output is None:
            (self.evidence / (name + ".stdout")).write_bytes(result.stdout)
        self.record(name + ".json", dict(args=args, seconds=time.monotonic() - started,
                                         returncode=result.returncode))
        if check and result.returncode:
            raise RuntimeError(name + " failed; see private command logs")
        return result

    def docker(self, *args, **kwargs):
        return self.command(["docker", "--context", CONTEXT, *args], **kwargs)

    def dc(self, *args, **kwargs):
        command = self.compose[:]
        if Path(command[0]).name == 'docker':
            command[1:1] = ['--context', CONTEXT]
        return self.command([*command, "--project-name", self.project,
                             "--project-directory", str(self.root), "--env-file", str(self.root / ".env"),
                             "-f", str(self.root / "docker-compose.yml"), *args], **kwargs)

    def install_updater(self):
        # This is the fixed trusted host implementation closure, not B's
        # runtime deployment. Preserve every retained A deployment hash. Host
        # observation and the producer independently hash this same closure.
        fixed = ('bin/update', 'scripts/rehearse_update_compatibility.py',
                 'scripts/update_rehearsal_shipping.py', 'scripts/build_update_compatibility_proof.py',
                 'scripts/update_rehearsal_milestone.py', 'scripts/update_rehearsal_qualification.py',
                 'scripts/update_rehearsal_resources.py', 'scripts/update_rehearsal_spool.py',
                 'scripts/generate_update_manifest.py', 'deploy/standalone/bin/start',
                 'deploy/standalone/bin/bootstrap.py', 'deploy/standalone/nginx.conf')
        deployment = self.source_manifest['deployment']['files'] if self.source_manifest else {}
        for name in fixed:
            for retained in deployment:
                if name == retained or name.startswith(retained+'/'):
                    from deploy.update.manifest import hash_path
                    if hash_path(self.source,retained) != deployment[retained]:
                        raise ValueError('host updater implementation overlaps retained A deployment')
        shutil.copytree(self.source/'deploy/update',self.root/'deploy/update',dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name in fixed:
            path=self.root/name
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            shutil.copy2(self.source/name,path)
        # Missing historical overlays are inert files, never activated as model
        # overrides. Existing retained overlays must never be overwritten.
        for name in ('docker-compose.host-status.standalone.yml','docker-compose.host-status.distributed.yml'):
            path=self.root/'deploy'/name
            if not path.exists():
                shutil.copy2(self.source/'deploy'/name,path)
        from deploy.update.manifest import hash_path
        if any(hash_path(self.root,name) != digest for name,digest in deployment.items()):
            raise ValueError('installing the updater changed retained A deployment')
        paths=[*(self.root/name for name in fixed), *sorted((self.root/'deploy/update').rglob('*'))]
        self.record('updater-code-inventory.json',{str(p.relative_to(self.root)):file_hash(p) for p in paths if p.is_file()})

    def prepare(self, digest: str, *, version='v1.53.0') -> dict:
        from deploy.update.manifest import parse_image_ref, parse_version
        parse_version(version)
        parse_image_ref('futureagi/standalone:'+version+'@'+digest)
        if self.source_manifest is not None and (version != self.source_manifest['version'] or
                digest != self.source_manifest['components']['standalone']['digest']):
            raise ValueError('source pin differs from retained manifest')
        if not (self.root / "docker-compose.yml").is_file():
            raise ValueError("extract the tag deployment into a new installation first")
        if (self.root / ".env").exists():
            raise ValueError("prepare requires a fresh, not already configured installation")
        for args in (("ps", "-aq"), ("volume", "ls", "-q"), ("network", "ls", "-q")):
            if self.docker(*args, "--filter", "label=com.docker.compose.project=" + self.project).stdout.strip():
                raise ValueError("project already owns resources; do not reuse or remove them")
        self.install_updater()
        (self.root / "private").mkdir(mode=0o700)
        credentials = self.root / "private/vertex.json"
        credentials.write_bytes(b"")
        credentials.chmod(0o600)
        keys = ("FRONTEND_PORT", "BACKEND_PORT", "FI_COLLECTOR_OTLP_PORT",
                "FI_COLLECTOR_OTLP_HTTP_PORT", "AGENTCC_GATEWAY_PORT", "MINIO_API_PORT")
        sockets = []
        try:
            for key in keys:
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                sockets.append(sock)
                self.ports[key] = sock.getsockname()[1]
        finally:
            for sock in sockets:
                sock.close()
        values = dict(self.ports, COMPOSE_PROJECT_NAME=self.project,
                      FUTURE_AGI_VERSION=version,
                      GOOGLE_APPLICATION_CREDENTIALS='/dev/null' if native_ci() else str(credentials),
                      FUTURE_AGI_TELEMETRY_DISABLED="true", COMPOSE_PROFILES="")
        import secrets
        import base64
        values["INTEGRATION_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        # The secrets bin/install generates (bin/lib/secrets.sh), including the webhook secret.
        for key in ("SECRET_KEY", "REDIS_PASSWORD", "PG_PASSWORD", "CH_PASSWORD", "MINIO_ROOT_PASSWORD",
                    "AGENTCC_INTERNAL_API_KEY", "AGENTCC_ADMIN_TOKEN", "AGENTCC_WEBHOOK_SECRET"):
            values[key] = secrets.token_hex(32)
        self._secret_values.update(str(value) for key, value in values.items()
                                   if re.search(r'PASSWORD|SECRET|TOKEN|KEY', key))
        # Like bin/install: start from the release's own .env.example, then set this
        # harness's values. A partial .env lacks example keys, which the update plan
        # rightly refuses ("run ./bin/install --no-up") on every scheduled run.
        example = self.root / ".env.example"
        lines = example.read_text().splitlines() if example.is_file() else []
        rendered, seen = [], set()
        for line in lines:
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=", line)
            if match and match.group(1) in values:
                seen.add(match.group(1))
                rendered.append("{}={}".format(match.group(1), values[match.group(1)]))
            else:
                rendered.append(line)
        rendered += ["{}={}".format(k, v) for k, v in values.items() if k not in seen]
        (self.root / ".env").write_text("\n".join(rendered) + "\n")
        (self.root / ".env").chmod(0o600)
        self.model = json.loads(self.dc("config", "--format", "json").stdout)
        validate_model(self.model, self.project)
        self.record("compose-resolved.private.json", self.model)
        self.record("ports.json", self.ports)
        return self.model

    def containers(self) -> list:
        ids = self.docker("ps", "-aq", "--filter", "label=com.docker.compose.project=" + self.project).stdout.split()
        return json.loads(self.docker("inspect", *[s.decode() for s in ids]).stdout) if ids else []

    def service_id(self, service: str) -> str:
        matches = [c["Id"] for c in self.containers()
                   if c["Config"].get("Labels", {}).get("com.docker.compose.service") == service]
        if len(matches) != 1:
            raise ValueError("service identity is not unique")
        return matches[0]

    def request(self, port: int, path: str, *, method="GET", value=None, headers=None, timeout=10):
        data = canonical(value) if value is not None else None
        request = Request("http://127.0.0.1:{}{}".format(port, path), data=data, method=method,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError("HTTP response too large")
            try:
                value = json.loads(body)
            except ValueError:
                value = {"body": body.decode("utf-8", "replace")}
            return response.status, value

    def boot_source(self, *, budget=READINESS_SECONDS) -> dict:
        if not 0 < budget <= READINESS_SECONDS:
            raise ValueError("startup budget must be bounded by 4200 seconds")
        started = time.monotonic()
        self.dc("up", "-d", "--pull", "never", timeout=180)
        phases = []
        last = None
        deadline = started + budget
        while time.monotonic() < deadline:
            try:
                status, body = self.request(self.ports["FRONTEND_PORT"], "/starting-status.json", timeout=5)
                phase = body.get("phase")
            except (OSError, URLError):
                status, body, phase = None, {}, "unreachable"
            if phase != last:
                phases.append(dict(phase=phase, seconds=time.monotonic() - started,
                                   http_status=status, response=body))
                self.record("startup-phases.json", phases)
                last = phase
            try:
                healthy, health_body = self.request(self.ports["BACKEND_PORT"], "/health/", timeout=5)
            except (OSError, URLError):
                healthy, health_body = None, {}
            if healthy == 200 and time.monotonic() < deadline:
                containers = self.containers()
                if (len(containers) == 3 and time.monotonic() < deadline
                        and all(c["State"].get("Health", {}).get("Status") == "healthy" for c in containers)):
                    result = dict(status="ready", seconds=time.monotonic() - started,
                                  phases=phases, health=health_body)
                    self.record("startup-result.json", result)
                    self.record("containers-ready.private.json", containers)
                    return result
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        result = dict(status="timeout", seconds=time.monotonic() - started, phases=phases, last_phase=last)
        self.record("startup-result.json", result)
        raise TimeoutError("A exceeded 4200-second readiness budget; no retry")

    def seed_api_witnesses(self, namespace: str) -> dict:
        """Create an owner/org/workspace and a real collector-ingested trace.

        No ORM writes, fake files, SQL inserts, or image-derived substitutes.
        Responses and identities remain private; callers add S3/Temporal evidence.
        """
        if not re.fullmatch(r"[a-z0-9-]{1,60}", namespace):
            raise ValueError("unsafe witness namespace")
        import secrets
        import uuid
        if (self.evidence / "witness-seed-intent.private.json").exists():
            raise ValueError("seed already attempted; inspect retained operations instead of reseeding")
        password = "Spike-" + secrets.token_hex(16)
        email = namespace + "@example.test"
        self.record("witness-seed-intent.private.json", dict(namespace=namespace, email=email, password=password))
        port = self.ports["BACKEND_PORT"]
        operations = []

        def call(path, *, method="GET", value=None, headers=None):
            status, body = self.request(port, path, method=method, value=value, headers=headers, timeout=60)
            operations.append(dict(method=method, path=path, status=status, response=body))
            self.record("witness-api-operations.private.json", operations)
            if not 200 <= status < 300:
                raise RuntimeError("product API witness operation failed: " + path)
            return body

        signup = call("/accounts/signup/", method="POST",
                      value=dict(email=email, full_name="Real update witness", company_name=namespace,
                                 password=password))
        login = call("/accounts/token/", method="POST", value=dict(email=email, password=password))
        tokens = login.get("result", login)
        if "access" not in tokens:
            tokens = login.get("data", {})
        headers = {"Authorization": "Bearer " + tokens["access"]}
        info = call("/accounts/user-info/", headers=headers)
        info = info.get("result", info)
        if not info.get("default_workspace_id"):
            info = call("/accounts/user-info/", headers=headers)
            info = info.get("result", info)
        org = info["organization"]["id"]
        workspace = info["default_workspace_id"]
        if not workspace:
            raise RuntimeError("supported user-info operation did not create a default workspace")
        headers.update({"X-Organization-Id": org, "X-Workspace-Id": workspace})
        keys = call("/accounts/keys/", headers=headers)["data"]
        trace = uuid.uuid4()
        root, child = secrets.token_hex(8), secrets.token_hex(8)
        nanos = time.time_ns()
        project = namespace + "-traces"

        def attribute(key, value):
            return {"key": key, "value": {"stringValue": value}}

        spans = []
        for sid, parent, name in ((root, None, namespace + ".root"), (child, root, namespace + ".child")):
            span = dict(traceId=trace.hex, spanId=sid, name=name, kind=1,
                        startTimeUnixNano=str(nanos), endTimeUnixNano=str(nanos + 50000000),
                        attributes=[attribute("spike.namespace", namespace)], status={"code": 1})
            if parent:
                span["parentSpanId"] = parent
            spans.append(span)
        payload = {"resourceSpans": [{"resource": {"attributes": [attribute("project_name", project),
                    attribute("service.name", project)]}, "scopeSpans": [{"scope": {"name": "real-update-spike"},
                    "spans": spans}]}]}
        status, body = self.request(self.ports["FI_COLLECTOR_OTLP_HTTP_PORT"], "/v1/traces", method="POST",
                                    value=payload, headers={"X-Api-Key": keys["api_key"],
                                    "X-Secret-Key": keys["secret_key"]}, timeout=60)
        self.record("witness-collector-request.json", payload)
        self.record("witness-collector-response.json", dict(status=status, response=body))
        if not 200 <= status < 300:
            raise RuntimeError("real collector trace ingestion failed")
        identities = dict(namespace=namespace, email=email, organization_id=org, workspace_id=workspace,
                          user_id=info.get("id"), trace_id=str(trace), span_ids=[root, child], project_name=project,
                          collector_payload_sha256=payload_hash(payload), signup_response_sha256=payload_hash(signup))
        self.record("witness-api-identities.json", identities)
        self.record("witness-credentials.private.json", dict(email=email, password=password, tokens=tokens, keys=keys))
        return identities

    def stop_cold(self) -> dict:
        started = time.monotonic()
        before = self.containers()
        for service in ("app", "postgres", "clickhouse"):
            rows = [c for c in before if c["Config"]["Labels"].get("com.docker.compose.service") == service]
            if len(rows) != 1:
                raise ValueError("missing source service")
            self.docker("stop", "--time", "60", rows[0]["Id"], timeout=90)
        after = self.containers()
        if len(after) != 3 or any(c["State"]["Running"] for c in after):
            raise ValueError("cold stop unconfirmed")
        clean = all(c["State"]["ExitCode"] in (0, 143) and not c["State"].get("OOMKilled") for c in after)
        result = dict(seconds=time.monotonic() - started, clean=clean, containers=after)
        self.record("cold-stop.private.json", result)
        if not clean:
            raise RuntimeError("unclean product stop")
        return result

    def seal_cold_volumes(self, postgres_image: str, *, timeout=900,
                          max_archive_bytes=8 * 1024 ** 3) -> dict:
        """Bounded read-only tar fallback; never a shipping restore rehearsal.

        StandaloneBackup.run currently restarts A; use no private backup internals
        and leave A stopped. All image-declared PG storage is explicitly mounted.
        """
        original = self.containers()
        if len(original) != 3 or any(c["State"]["Running"] for c in original):
            raise ValueError("archive requires all source containers stopped")
        witness = self.evidence / "witness-inventory.private.json"
        if not witness.is_file():
            raise ValueError("cannot seal seeded A without its real witness inventory")
        details = json.loads(self.docker("image", "inspect", postgres_image).stdout)[0]
        if details["Architecture"] != "arm64" or set(details["Config"].get("Volumes", {})) != {"/var/lib/postgresql/data"}:
            raise ValueError("unqualified archive helper image")
        image_id = details["Id"]
        sealed = self.evidence / "sealed-A"
        sealed.mkdir(mode=0o700)
        stores = {}
        for c in original:
            for mount in c["Mounts"]:
                if mount["Type"] == "volume":
                    name = mount["Name"]
                    resource_name(name)
                    if not name.startswith(self.project + "_"):
                        raise ValueError("foreign source volume")
                    stores[name] = mount["Destination"]
        if set(stores) != {self.project + "_" + key for key in ("app-data", "postgres-data", "clickhouse-data")}:
            raise ValueError("incomplete source stores")
        archives = {}
        for name, mount_root in stores.items():
            space = os.statvfs(sealed)
            if space.f_bavail * space.f_frsize < max_archive_bytes + 1024 ** 3:
                raise ValueError("insufficient host archive headroom")
            consumers = self.docker("ps", "-q", "--filter", "volume=" + name).stdout.strip()
            if consumers:
                raise ValueError("running source volume consumer")
            metadata = json.loads(self.docker("volume", "inspect", name).stdout)[0]
            helper = resource_name(self.project + "-archive-" + name.rsplit("_", 1)[1])
            args = ["create", "--name", helper, "--label", "com.docker.compose.project=" + self.project,
                    "--pull", "never", "--network", "none", "--read-only", "--cap-drop", "ALL",
                    "--cap-add", "DAC_READ_SEARCH", "--security-opt", "no-new-privileges",
                    "--pids-limit", "32", "--memory", "128m", "--cpus", "1", "--user", "0:0",
                    "--mount", "type=volume,source=" + name + ",target=/var/lib/postgresql/data,readonly,volume-nocopy",
                    "--entrypoint", "/bin/tar", image_id, "--format=pax", "--numeric-owner", "--acls",
                    "--xattrs", "--sparse", "-C", "/var/lib/postgresql/data", "-cpf", "-", "."]
            helper_id = self.docker(*args).stdout.decode().strip()
            inspection = json.loads(self.docker("inspect", helper_id).stdout)[0]
            if (inspection["Image"] != image_id or len(inspection["Mounts"]) != 1
                    or inspection["Mounts"][0]["Name"] != name or inspection["Mounts"][0]["RW"]):
                raise ValueError("archive helper identity mismatch")
            self.record(helper + "-created.json", inspection)
            path = sealed / (name.rsplit("_", 1)[1] + ".tar")
            started = time.monotonic()
            try:
                with path.open("xb") as out:
                    self.docker("start", "--attach", helper_id, timeout=timeout,
                                output=out, max_output_bytes=max_archive_bytes)
                    out.flush()
                    os.fsync(out.fileno())
                archive_seconds = time.monotonic() - started
                final = json.loads(self.docker("inspect", helper_id).stdout)[0]
                self.record(helper + "-exited.json", final)
                if final["State"]["Running"] or final["State"]["ExitCode"] != 0:
                    raise ValueError("archive helper failed")
                entry = inspect_archive(path, path.with_suffix(".members.jsonl"))
                entry.update(volume=metadata, mount_root=mount_root, archive_seconds=archive_seconds,
                             verify_seconds=time.monotonic() - started - archive_seconds)
                archives[name] = entry
            finally:
                final = json.loads(self.docker("inspect", helper_id).stdout)[0]
                if final["State"]["Running"]:
                    self.docker("stop", "--time", "10", helper_id, timeout=30)
                self.docker("rm", helper_id)
        config = {}
        private = sealed / "configuration"
        private.mkdir(mode=0o700)
        for c in original:
            for mount in c["Mounts"]:
                if mount["Type"] == "bind":
                    path = home_path(Path(mount["Source"]))
                    rel = path.relative_to(self.root)
                    target = private / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
                    config[str(rel)] = dict(sha256=file_hash(target), mode=path.stat().st_mode & 0o777)
        shutil.copy2(self.root / ".env", private / ".env")
        config[".env"] = dict(sha256=file_hash(private / ".env"), mode=0o600)
        shutil.copy2(witness, sealed / witness.name)
        result = dict(kind="seeded-A-cold-fixture-not-product-qualification", archives=archives,
                      configuration=config, helper_image_id=image_id,
                      source_images={c["Config"]["Labels"]["com.docker.compose.service"]: c["Image"] for c in original},
                      witness_inventory_path=str(sealed / witness.name), witness_inventory_sha256=file_hash(witness))
        self.record("sealed-A/inventory.json", result)
        self.record("sealed-A/seal.json", dict(inventory_sha256=file_hash(sealed / "inventory.json")))
        for path in sealed.rglob("*"):
            if path.is_file():
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
                path.chmod(0o400)
        directories = [p for p in sealed.rglob("*") if p.is_dir()]
        for path in sorted(directories, key=lambda p: len(p.parts), reverse=True) + [sealed]:
            fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            path.chmod(0o500)
        return result

    def collect_diagnostics(self, failure=None):
        """Read-only, bounded CI artifact; never a qualification/restore oracle.

        Inspect only this exact project's services, not private Docker configs,
        database/witness data or host env. Raw logs stay private; tails are
        scrubbed against every generated credential and common token formats.
        Diagnostic failure cannot replace a failed test or authorize cleanup.
        """
        from deploy.update.contracts import AutomationPolicy, ReasonCode
        from deploy.update.processes import ProcessSupervisor
        result = dict(schema_version=1, qualification_evidence=False, services={}, collection_errors=[])
        if failure is not None:
            result['exception_type'] = type(failure).__name__[:80]
            code = getattr(failure, 'reason_code', None)
            if isinstance(code, ReasonCode):
                result['reason_code'] = code.value
            if isinstance(failure, SetupIncomplete):
                result['setup_reason'] = failure.reason
        runner = ProcessSupervisor(AutomationPolicy(), docker_context=CONTEXT,
                                   environment=self.env, output_limit=65536, grace_seconds=.2)
        runner.deadline = time.monotonic()+30
        def read(*args):
            completed = runner(['docker', '--context', CONTEXT, *args], capture_output=True, timeout=10)
            if completed.returncode:
                raise RuntimeError('diagnostic command failed')
            return completed.stdout
        try:
            ids = read('ps', '-aq', '--filter', 'label=com.docker.compose.project='+self.project).decode().split()
            if len(ids) > 3 or any(not re.fullmatch(r'[0-9a-f]{12,64}', cid) for cid in ids):
                raise ValueError('diagnostic roster differs')
            for cid in ids:
                row = json.loads(read('inspect', cid))[0]
                labels = row['Config']['Labels']
                service = labels.get('com.docker.compose.service')
                if (labels.get('com.docker.compose.project') != self.project
                        or service not in ('app', 'postgres', 'clickhouse') or service in result['services']):
                    raise ValueError('diagnostic ownership differs')
                state = row['State']
                summary = dict(status=state['Status'], health=state.get('Health', {}).get('Status'),
                    exit_code=state['ExitCode'], oom_killed=state.get('OOMKilled'), restart_count=row['RestartCount'])
                result['services'][service] = summary
                logs = runner(['docker', '--context', CONTEXT, 'logs', '--timestamps', '--tail', '60', cid],
                              capture_output=True, timeout=5)
                summary['logs'] = redacted_tail(logs.stdout+logs.stderr, self._secret_values)
        except Exception as error:
            result['collection_errors'].append(type(error).__name__[:80])
        try:
            self.record('acceptance-diagnostics.json', result)
        except OSError:
            pass  # evidence I/O is not permission to change the transaction
        return result

    def collect_logs(self) -> None:
        with (self.evidence / "product.log").open("wb") as output:
            self.dc("logs", "--no-color", "--timestamps", "--tail", "20000", output=output, check=False)

    def cleanup(self) -> dict:
        # Only exact fresh project resources, never global or broad-prefix pruning.
        self.dc("down", "--volumes", "--remove-orphans", timeout=180)
        remaining = {}
        for kind, args in (("containers", ("ps", "-aq")), ("volumes", ("volume", "ls", "-q")),
                           ("networks", ("network", "ls", "-q"))):
            result = self.docker(*args, "--filter", "label=com.docker.compose.project=" + self.project)
            remaining[kind] = result.stdout.decode().split()
        self.record("cleanup.json", remaining)
        if any(remaining.values()):
            raise RuntimeError("project cleanup incomplete")
        return remaining
