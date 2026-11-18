"""Direct Compose runtime adapter for the lock-held automatic transaction.

No trial, alternate ports, restart-policy rewrite, pull, volume replacement or
release-supplied command. Compose creates the ordinary retained model WITHOUT
starting it. Every subsequent start names an independently inspected full ID.
An unresolved remote operation is evidence to stop, not permission to retry.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .contracts import ReasonCode
from .manifest import canonical_json
from .readiness import AutomaticReadiness, RuntimeSample


class RuntimeRefused(RuntimeError):
    def __init__(self, reason=ReasonCode.RECOVERY_REQUIRED):
        self.reason_code = ReasonCode(reason)
        super().__init__(self.reason_code.value)


def need(ok, reason=ReasonCode.RECOVERY_REQUIRED):
    if not ok:
        raise RuntimeRefused(reason)


# Fixed queries only. Activity timestamps/elapsed durations are deliberately NOT
# progress: a stuck statement must not reset the stall timer on every sample.
# Query contents are hashed immediately and never enter receipts/public status.
PROGRESS_SQL = """SELECT json_build_object(
'index', (SELECT coalesce(json_agg(row_to_json(p)), '[]') FROM pg_stat_progress_create_index p),
'cluster', (SELECT coalesce(json_agg(row_to_json(p)), '[]') FROM pg_stat_progress_cluster p),
'vacuum', (SELECT coalesce(json_agg(row_to_json(p)), '[]') FROM pg_stat_progress_vacuum p),
'analyze', (SELECT coalesce(json_agg(row_to_json(p)), '[]') FROM pg_stat_progress_analyze p),
'copy', (SELECT coalesce(json_agg(row_to_json(p)), '[]') FROM pg_stat_progress_copy p),
'activity', (SELECT coalesce(json_agg(json_build_array(pid, state, wait_event_type,
wait_event, query_start, md5(query)) ORDER BY pid), '[]') FROM pg_stat_activity
WHERE datname = current_database() AND pid <> pg_backend_pid()
AND application_name <> 'futureagi-update-progress'))"""


class HostRuntimeObserver:
    """Injectable read-only facilities; no Django import or mutable probe SQL.

    The host-observation lane owns daemon routing, bounded subprocesses and the
    filesystem helper. This adapter only selects fixed reads and exact IDs.
    Product status currently exposes phase but not always a migration name;
    bounded product logs supply the current Applying app.name in that case.
    """
    def __init__(self, installation, registration, backup_binding, *, timeout=30):
        self.installation, self.registration = installation, registration
        self.binding, self.timeout = backup_binding, timeout

    def free_bytes(self):
        from .docker_host import inspect_volume_storage
        observation = inspect_volume_storage(self.installation, self.registration,
            helper_image_id=self.binding['images']['app'], timeout=self.timeout, measure_used_bytes=False)
        need(observation.status == 'available' and len(observation.volumes) == 3
             and {v.role for v in observation.volumes} == {'app-data', 'postgres-data', 'clickhouse-data'},
             ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        need(all(v.root_identity == self.binding['volume_root_ids'][v.role]
                 and v.name == self.binding['volumes'][v.role]['Name'] for v in observation.volumes))
        return min(v.free_bytes for v in observation.volumes)

    def progress(self, roster):
        from .docker_host import bounded_installation
        from .compatibility import decode_document
        inst = bounded_installation(self.installation, self.timeout)
        # Fixed path in the verified container, never a path from release data.
        result = inst.docker_run('exec', roster['app']['Id'], 'cat', '/run/futureagi/status.json', check=False)
        need(result.returncode == 0, ReasonCode.MIGRATION_STALLED)
        status = decode_document(result.stdout.encode(), 16384)
        phase = status.get('phase')
        need(isinstance(phase, str) and re.fullmatch(r'[a-z_]{1,64}', phase), ReasonCode.MIGRATION_STALLED)
        migration = status.get('current_migration') or status.get('migration') or ''
        if not migration:
            logs = inst.docker_run('logs', '--tail', '100', roster['app']['Id']).stdout
            names = re.findall(r'Applying\s+([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_]+)', logs)
            migration = names[-1] if names else ''
        need(isinstance(migration, str) and len(migration) <= 257, ReasonCode.MIGRATION_STALLED)
        result = inst.docker_run('exec', '-e',
            'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=3000',
            '-e', 'PGAPPNAME=futureagi-update-progress', roster['postgres']['Id'],
            '/bin/sh', '-c',
            'exec psql -X -A -t --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="$1"',
            'progress', PROGRESS_SQL)
        document = decode_document(result.stdout.strip().encode(), 262144)
        need(set(document) == {'index', 'cluster', 'vacuum', 'analyze', 'copy', 'activity'},
             ReasonCode.MIGRATION_STALLED)
        # PostgreSQL does not promise view row ordering. Sort serialized rows so
        # incidental enumeration order cannot pretend a migration made progress.
        normalized = {key: sorted((canonical_json(row).decode() for row in rows))
                      for key, rows in document.items()}
        return phase, migration, hashlib.sha256(canonical_json(normalized)).hexdigest()


class AutomaticRuntime:
    """Storage-free runtime operations. Updater owns direction and finalization."""
    def __init__(self, installation, ledger, backup_binding, *, observer=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.installation, self.ledger, self.binding = installation, ledger, backup_binding
        self.clock, self.sleep = clock, sleep
        self.observer = observer

    def _read_installation(self):
        """Bounded pure reads never poison the mutation supervisor.

        This is NOT an uncertainty reset: real create/start timeouts stay on
        installation.runner and their durable pending operation still blocks
        restoration. A read-only client has no authority to start a writer.
        """
        from .docker_host import bounded_installation
        policy = getattr(self.installation.runner, 'policy', None)
        return bounded_installation(self.installation, min(30, getattr(policy, 'probe_timeout_seconds', 30)))

    def _safe(self):
        need(not getattr(self.installation.runner, 'uncertain', False), ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        need(self.ledger.snapshot['pending'] is None, ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        inst = self._read_installation()
        inst.check_ownership()
        daemon = inst.docker_run('info', '--format', '{{.ID}}').stdout.strip()
        need(daemon == self.binding['daemon'])

    def _stores(self):
        """A reused Docker name is never the original physical volume object."""
        from .standalone_backup import _volume
        expected = {row['Name']: row for row in self.binding['volumes'].values()}
        need(len(expected) == 3, ReasonCode.RESTORE_VERIFICATION_FAILED)
        rows = json.loads(self._read_installation().docker_run('volume', 'inspect', *sorted(expected)).stdout)
        need(len(rows) == len(expected) and {row['Name'] for row in rows} == set(expected)
             and all(_volume(row) == _volume(expected[row['Name']]) for row in rows),
             ReasonCode.RESTORE_VERIFICATION_FAILED)

    def _inspect(self, cid):
        need(re.fullmatch(r'[0-9a-f]{64}', cid) is not None, ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        rows = json.loads(self._read_installation().docker_run('inspect', cid).stdout)
        need(len(rows) == 1 and rows[0]['Id'] == cid, ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        return rows[0]

    def _exact(self, identity):
        from .standalone_backup import _identity
        row = self._inspect(identity['Id'])
        need(_identity(row) == identity, ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        return row

    def qualify_model(self, expected_images):
        """No transformation: qualify the actual env-selected normal model."""
        inst = self._read_installation()
        model = inst.config(refresh=True)
        services = model.get('services', {})
        need(model.get('name') == self.binding['project']
             and set(services) == set(expected_images) == {'app', 'postgres', 'clickhouse'},
             ReasonCode.UNSUPPORTED_INSTALLATION)
        need(all(not s.get('build') and s.get('restart', 'no') in ('unless-stopped', 'no')
                 and isinstance(s.get('image'), str) for s in services.values()), ReasonCode.UNSUPPORTED_INSTALLATION)
        for name, service in services.items():
            local = inst.image(service['image'])
            need(local is not None and local['id'] == expected_images[name], ReasonCode.IMAGE_DIGEST_MISMATCH)
        return model

    def _source_cold(self):
        """Last read barrier before Compose can erase the original hold evidence.

        Do not adopt an unexpectedly restarted source. Recovery owns stopping
        exact identities and checking consumers before any clear or extraction.
        """
        doc = self.ledger.snapshot
        need(doc['backup_seal'] is not None and doc['direction'] == 'forward_pending')
        expected = doc['containers']
        rows = self._read_installation().containers()
        need({row['id'] for row in rows} == {row['Id'] for row in expected.values()},
             ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        for service, identity in expected.items():
            row = self._exact(identity)
            state = row['State']
            need(not any(state.get(key) for key in ('Running', 'Paused', 'Restarting', 'Dead', 'OOMKilled'))
                 and state.get('Status') == 'exited' and state.get('ExitCode') in (0, 143)
                 and state.get('StartedAt') == doc['start_times'][service]
                 and row['RestartCount'] == doc['restart_counts'][service], ReasonCode.RECOVERY_REQUIRED)

    def create(self, expected_images, *, previous=False):
        self._safe()
        need(not self.ledger.snapshot['previous_start_armed'])
        model = self.qualify_model(expected_images)
        prefix = 'previous' if previous else 'target'
        need(model == self.ledger.snapshot.get('source_model' if previous else 'target_model'),
             ReasonCode.CONFIGURATION_CHANGED)
        if not previous:
            self._source_cold()
        self.ledger.checkpoint(prefix + '_create_pending')
        op = self.ledger.begin('compose_create', identity={'model_sha256': hashlib.sha256(canonical_json(model)).hexdigest(),
            'images': expected_images}, postconditions={'all_stopped': True})
        # Retain ports, restart policies, commands, mounts and normal Compose
        # behavior. create never starts a dependency (unlike up --no-start).
        self.installation.compose_run('create', '--no-build', '--pull', 'never', '--force-recreate')
        containers = self._read_installation().containers()
        need(len(containers) == 3 and {c['service'] for c in containers} == set(expected_images),
             ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        from .standalone_backup import _identity
        roster = {}
        for container in containers:
            row = self._inspect(container['id'])
            service = container['service']
            need(row['Image'] == expected_images[service] and not container.get('oneoff')
                 and not row['State']['Running'] and row['State']['Status'] in ('created', 'exited')
                 and row['HostConfig']['RestartPolicy']['Name'] == model['services'][service].get('restart', 'no')
                 and row['Config']['Labels'].get('com.docker.compose.project') == self.binding['project']
                 and row['Config']['Labels'].get('com.docker.compose.service') == service,
                 ReasonCode.RESTORE_CHILD_AMBIGUOUS)
            expected_mounts = {key: volume['Name'] for key, volume in self.binding['volumes'].items()}
            allowed_roles = {'app': {'app-data'}, 'postgres': {'postgres-data'}, 'clickhouse': {'clickhouse-data'}}[service]
            actual_volumes = {mount.get('Name') for mount in row['Mounts'] if mount.get('Type') == 'volume'}
            need(actual_volumes == {expected_mounts[key] for key in allowed_roles},
                 ReasonCode.RESTORE_CHILD_AMBIGUOUS)
            roster[service] = _identity(row)
        # Compare the selected model and image IDs AGAIN after Compose: a local
        # tag race never authorizes starting the wrong image.
        need(self.qualify_model(expected_images) == model, ReasonCode.CONFIGURATION_CHANGED)
        known = self.ledger.snapshot.get('target_containers', {})
        known.update({row['Id']: row for row in roster.values()})
        self.ledger.checkpoint(target_containers=known, **{prefix + '_roster': roster})
        self.ledger.complete(op, {'roster': roster, 'all_stopped': True})
        self.ledger.checkpoint(prefix + '_created')
        self._stores()
        return roster

    def _start(self, identity):
        self._safe()
        row = self._exact(identity)
        need(not row['State'].get('Paused') and not row['State'].get('Restarting'))
        if row['State']['Running']:
            # Only an acknowledged start is replayable. An unexpected running
            # original/target must not be adopted as our own successful start.
            need(identity['Id'] in self.ledger.snapshot.get('acknowledged_starts', []),
                 ReasonCode.RESTORE_CHILD_AMBIGUOUS)
            return
        need(identity['Id'] not in self.ledger.snapshot.get('acknowledged_starts', []),
             ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        op = self.ledger.begin('start', identity=identity, postconditions={'running': True})
        self.installation.docker_run('start', identity['Id'])
        row = self._exact(identity)
        need(row['State']['Running'] and not row['State'].get('Restarting'), ReasonCode.READINESS_FAILED)
        acknowledged = self.ledger.snapshot.get('acknowledged_starts', [])
        acknowledged.append(identity['Id'])
        self.ledger.checkpoint(acknowledged_starts=acknowledged)
        self.ledger.complete(op, {'id': identity['Id'], 'running': True})

    def retain_failed_logs(self):
        """Best-effort private, create-once diagnostics, never restore authority.

        At most three 16-KiB combined streams and five seconds TOTAL. Output
        overflow/timeouts are recorded as unavailable rather than growing the
        archive or spending the protected recovery budget on a second copy of
        failed data. No automatic pruning, public log text or arbitrary IDs.
        """
        import base64
        import os
        from .docker_host import bounded_installation
        from .standalone_backup import _directory, _read, _atomic, _identity
        leaf = 'failed-target-logs.json'
        try:
            with _directory(self.ledger.directory) as directory:
                try:
                    _read(directory, leaf, 128 * 1024)
                    return  # Retained even after a crash; never replace evidence.
                except FileNotFoundError:
                    pass
                space = os.fstatvfs(directory)
                if space.f_bavail * space.f_frsize < 1024 * 1024:
                    return  # Optional diagnostics must not consume the last space.
                inst = bounded_installation(self.installation, 5)
                inst.runner.max_output = 16 * 1024
                rows = {}
                for service, expected in sorted(self.ledger.snapshot.get('target_roster', {}).items()):
                    if service not in ('app', 'postgres', 'clickhouse'):
                        continue
                    entry = {'id': expected['Id'], 'status': 'unavailable'}
                    try:
                        observed = json.loads(inst.docker_run('inspect', expected['Id']).stdout)
                        need(len(observed) == 1 and _identity(observed[0]) == expected)
                        result = inst.docker_run('logs', '--timestamps', '--tail', '100', expected['Id'], check=False)
                        if result.returncode == 0:
                            # Base64 bounds JSON expansion even for control bytes.
                            entry.update(status='retained', stdout_b64=base64.b64encode(
                                result.stdout.encode()).decode(), stderr_b64=base64.b64encode(
                                result.stderr.encode()).decode())
                    except Exception:
                        pass  # Fixed status only; exception text can contain secrets.
                    rows[service] = entry
                raw = canonical_json({'schema_version': 1, 'run_id': self.ledger.snapshot['run_id'],
                                      'attempt_id': self.ledger.snapshot['attempt_id'], 'services': rows})
                need(len(raw) <= 128 * 1024)
                _atomic(directory, leaf, raw)
        except Exception:
            # No optional log/read/write failure can cancel a mandatory restore.
            return

    def _watermark(self, reserve):
        need(self.observer is not None and reserve is not None, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        free = self.observer.free_bytes()
        need(type(free) is int and free >= 0, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        need(free >= reserve, ReasonCode.DISK_WATERMARK_ABORT)
        return free

    def _dependencies(self, roster, deadline, reserve):
        for service in ('postgres', 'clickhouse'):
            self._watermark(reserve)
            need(self.clock() < deadline, ReasonCode.READINESS_FAILED)
            self._start(roster[service])
        while True:
            self._watermark(reserve)
            ready = True
            for service in ('postgres', 'clickhouse'):
                row = self._exact(roster[service])
                state = row['State']
                need(state['Running'] and not state.get('Restarting') and not state.get('OOMKilled'),
                     ReasonCode.READINESS_FAILED)
                need(row['RestartCount'] == 0, ReasonCode.READINESS_FAILED)
                ready = ready and state.get('Health', {}).get('Status') == 'healthy'
            # Inspection itself can consume time/capacity. Check both guards
            # before accepting even the first healthy dependency sample.
            self._watermark(reserve)
            need(self.clock() < deadline, ReasonCode.READINESS_FAILED)
            if ready:
                return
            self.sleep(min(1, max(0, deadline - self.clock())))

    def sample(self, roster, *, progress=True):
        need(self.observer is not None, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        free = self.observer.free_bytes()  # watermark checked even if a health/progress read fails
        containers = {}
        for service, identity in roster.items():
            row = self._exact(identity)
            state = row['State']
            containers[row['Id']] = (state['Running'] and not any(state.get(k) for k in
                ('Paused', 'Restarting', 'Dead', 'OOMKilled')), state.get('Health', {}).get('Status') == 'healthy',
                row['RestartCount'], state.get('StartedAt', ''))
        phase, migration, pg = self.observer.progress(roster) if progress else ('ready', '', '')
        # Container/progress inspections may be slow. Never authorize success
        # with the capacity that preceded them; retain the lower of both reads.
        final_free = self.observer.free_bytes()
        need(type(free) is int and free >= 0 and type(final_free) is int and final_free >= 0,
             ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        return RuntimeSample(phase, migration, pg, min(free, final_free), containers)

    def observation_session(self, lifecycle, release, boundary, policy, estimate=None, *, previous=False):
        return RuntimeObservationSession(self, lifecycle, release, boundary, policy, estimate, previous=previous)

    def start_and_wait(self, roster, policy, estimate=None, *, previous=False, ready_callback=None):
        self._safe()
        prefix = 'previous' if previous else 'target'
        need(self.qualify_model({s: row['Image'] for s, row in roster.items()}) ==
             self.ledger.snapshot.get('source_model' if previous else 'target_model'),
             ReasonCode.CONFIGURATION_CHANGED)
        self._stores()
        need(self.observer is not None, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        reserve = policy.disk_watermark_reserve_bytes if previous else (
            estimate.protected_restore_reserve_bytes if estimate is not None else None)
        self._watermark(reserve)  # verify original roots BEFORE a database can start
        if previous and not self.ledger.snapshot['previous_start_armed']:
            self.ledger.arm_previous_start()  # before EVEN a database can write
        start = self.clock()
        startup_budget = policy.recovery_startup_timeout_seconds if previous else policy.wait_timeout_seconds
        deadline = min(self.installation.runner.deadline, start + startup_budget)
        # Keep the phase at the durable barrier until dependencies are started.
        self.ledger.checkpoint(prefix + '_starting')
        self._dependencies(roster, deadline, reserve)
        # A dependency can exhaust the reserve during its last health read.
        # Never begin application writes on the strength of an earlier sample.
        self._watermark(reserve)
        phase_started = self.clock()
        remaining_startup = startup_budget - (phase_started - start)
        need(remaining_startup > 0, ReasonCode.READINESS_FAILED)
        self._start(roster['app'])
        self.ledger.checkpoint(prefix + '_verifying')
        readiness = AutomaticReadiness(clock=self.clock, sleep=self.sleep)
        readiness.wait(lambda: self.sample(roster, progress=not previous), policy=policy,
            estimate=estimate, deadline=self.installation.runner.deadline,
            previous=previous, expected_ids={r['Id'] for r in roster.values()},
            startup_timeout_seconds=remaining_startup, phase_started_at=phase_started,
            ready_callback=ready_callback)
        self._safe()
        return roster


class RuntimeObservationSession:
    """One live attempt/roster, one full stability observation, no durable cache.

    Runtime readiness and the full host observer share the SAME interval. The
    latter still runs all migration/storage/config/health/identity checks and
    its entire configured stability period. Every observer sleep/probe edge
    also enforces the runtime watermark, deadline and exact healthy roster.
    Later lifecycle metadata consumers may reuse only this same observation
    while these locks and this session remain held and identity is unchanged.
    A fresh engine/process/start always has to obtain new evidence.
    """
    def __init__(self, runtime, lifecycle, release, boundary, policy, estimate, *, previous=False):
        self.runtime, self.lifecycle, self.release, self.boundary = runtime, lifecycle, release, boundary
        self.policy, self.estimate, self.previous = policy, estimate, previous
        self.original = getattr(lifecycle, 'observer', None)
        self.roster = self.signature = self.result = self.cached = self.key = self.fingerprint = None
        self.next_check = 0
        self.reserve = policy.disk_watermark_reserve_bytes if previous else estimate.protected_restore_reserve_bytes
        self.direction = runtime.ledger.direction
        self.authority = tuple(runtime.ledger.snapshot.get(key) for key in ('attempt_id', 'operation_token', 'run_id'))

    def __enter__(self):
        self.lifecycle.observer = self
        return self

    def __exit__(self, *exc):
        self.lifecycle.observer = self.original
        # Evidence is never reusable outside this lock-held invocation.
        self.cached = self.result = None

    def check(self, *, force=True):
        runtime = self.runtime
        need(runtime.clock() < runtime.installation.runner.deadline, ReasonCode.READINESS_FAILED)
        need(runtime.ledger.direction == self.direction and self.authority == tuple(runtime.ledger.snapshot.get(key)
             for key in ('attempt_id', 'operation_token', 'run_id'))
             and runtime.ledger.snapshot['pending'] is None, ReasonCode.RESTORE_CHILD_AMBIGUOUS)
        if not force and runtime.clock() < self.next_check:
            return
        sample = runtime.sample(self.roster, progress=False)
        need(type(sample.free_bytes) is int and sample.free_bytes >= 0, ReasonCode.RESTORE_CAPACITY_UNKNOWN)
        need(sample.free_bytes >= self.reserve, ReasonCode.DISK_WATERMARK_ABORT)
        need(runtime.clock() < runtime.installation.runner.deadline, ReasonCode.READINESS_FAILED)
        signature = tuple(sorted((cid, *row) for cid, row in sample.containers.items()))
        need(set(sample.containers) == {row['Id'] for row in self.roster.values()}
             and all(row[0] and row[1] and type(row[2]) is int and row[2] == 0
                     for row in sample.containers.values()), ReasonCode.READINESS_FAILED)
        if self.signature is None:
            self.signature = signature
        need(signature == self.signature, ReasonCode.READINESS_FAILED)
        self.next_check = runtime.clock() + 1

    def ready(self, roster):
        self.roster = roster
        self.check()
        self.result = self.lifecycle._observation(self.runtime.installation, self.release, self.boundary)
        self.check()

    def observe(self, installation, *, current_release, execution_deployment, boundary):
        from .compatibility import plain
        from .orchestrator import fingerprints
        from .standalone_state import StandaloneStateObserver
        key = (current_release.manifest_sha256, canonical_json(plain(execution_deployment)), boundary)
        self.check()
        if self.cached is not None:
            need(key == self.key and fingerprints(installation) == self.fingerprint,
                 ReasonCode.CONFIGURATION_CHANGED)
            self.runtime._safe()
            self.runtime._stores()
            self.check()
            return self.cached
        observer = self.original
        if observer is None:
            from .automatic import default_observer
            observer = default_observer(self.lifecycle.registration, self.policy, self.lifecycle.clock)
        if isinstance(observer, StandaloneStateObserver):
            need(observer.stability_period_seconds in (None, self.policy.stability_period_seconds),
                 ReasonCode.READINESS_FAILED)
            session, delegate = self, observer
            def checked(*, force=True):
                # The full observer sanitizes unknown RuntimeError instances.
                # Preserve these closed runtime reasons across that boundary.
                from .orchestrator import GuardRefused
                try:
                    session.check(force=force)
                except RuntimeRefused as exc:
                    raise GuardRefused(exc.reason_code) from None
            class GuardedStateObserver(StandaloneStateObserver):
                def _probe(self, *args):
                    checked()
                    result = super()._probe(*args)
                    checked()
                    return result
            def monotonic():
                checked(force=False)
                return delegate.monotonic()
            def sleeper(seconds):
                checked(force=False)
                delegate.sleeper(seconds)
                checked(force=False)
            observer = GuardedStateObserver(observer.registration, timeout=observer.timeout,
                stability_period_seconds=observer.stability_period_seconds, clock=observer.clock,
                monotonic=monotonic, sleeper=sleeper)
        self.cached = observer.observe(installation, current_release=current_release,
            execution_deployment=execution_deployment, boundary=boundary)
        self.key, self.fingerprint = key, fingerprints(installation)
        self.check()
        return self.cached
