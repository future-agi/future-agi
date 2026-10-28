"""Built-in cold-volume backups for the shipped Standalone layout (host only).

Automatic restore-backed v2 callers MUST use run_quiesced with the reserved
RecoveryLedger: it returns BackupArtifact(proof, seal, hold) and leaves A stopped.
verify_hold rechecks cold ownership; abort_quiescence is pre-seal only. The run()
API documented below is retained solely for legacy/manual v1 callers.


Integration contract (no CLI, scheduler, baseline store or legacy hook changes):
* At opt-in, create_destination() once; capture_binding() takes VERIFIED current
  image IDs and the qualified execution-deployment hashes, not a release's
  command/path. Persist BackupBinding.raw privately. At a verified successor,
  capture a successor binding with previous=old (retains physical volume IDs).
* Under runner.lock -> update.lock, AFTER reserving BackupExpectation, call
  StandaloneBackup(...).run(expected, proof_directory_fd=engine_owned_fd).
  The original containers are healthy again before it returns BackupProof;
  the engine must still perform its ordinary after_backup/proof checks on A.
* A repeated attempt always refuses, even after success. Never refresh a proof.
  Recovery only restores availability, never emits a proof or authorizes rollout.
  inspect_recovery/recover_availability require the same engine locks and a new
  bounded ProcessSupervisor. An unacknowledged daemon operation is ambiguous:
  neither recovery method guesses that it is safe to start anything.

Only local Docker named volumes, default config binds and ml/sandbox (no extra
mounts) qualify. External storage, custom config/credential paths, other Compose
files and Distributed --production are refused. Host destination must already
be owned 0700 on local APFS/ext4 with safe ancestry. No retention/deletion daemon.
The caller supplies a registered ProcessSupervisor (probe budget >=70 seconds).

Archive helper: the VERIFIED INSTALLED postgres image ID, /bin/tar (GNU), with
its sole declared volume remounted read-only; network=none, readonly root, no
socket, no extra host mounts, no pull, bounded CPU/memory/PIDs. A missing tool or
incompatible vendor image is a setup prerequisite failure, not a helper pull.

Restore procedure standalone-cold-volumes-v1: verify inventory and archive/member
hashes; extract numeric ownership, permissions, ACLs and xattrs into NEW, empty,
owned disposable volumes at their ORIGINAL logical mount roots (preserving
ClickHouse's internal absolute symlinks) using the same GNU tar image; start the exact original
images with the retained private config and read domain records. Never restore
into an existing installation. deploy/tests/test_update_e2e_backup.py exercises
that procedure. Checksums/healthy originals alone are NOT a restore guarantee
or real-product/Temporal qualification. Release rehearsals must supply those.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import posixpath
import re
import stat
import tarfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Tuple

from .backup_proof import BackupExpectation, BackupProof, read_backup_proof, validate_backup_proof
from .contracts import BackupSeal, ColdHold
from .envcommit import CommitError, _filesystem, _plain_metadata, qualify_retained_env, sync_dir, sync_file
from .manifest import canonical_json
from .processes import ProcessSupervisor

DOMAINS = ('clickhouse', 'object_store', 'postgres_app', 'shared_files', 'temporal')
RESTORE_PROCEDURE = 'standalone-cold-volumes-v1'
VOLUMES = {'app-data': ('app', '/data'),
           'postgres-data': ('postgres', '/var/lib/postgresql/data'),
           'clickhouse-data': ('clickhouse', '/var/lib/clickhouse')}
CONFIG_FILES = ('.env', 'docker-compose.yml', 'agentcc-gateway/config.example.yaml',
                'deploy/clickhouse/users.d/zz-default-password.xml',
                'futureagi/.ci/clickhouse-storage-policy.xml',
                'deploy/standalone/clickhouse/config.d/zz-small-host.xml',
                'deploy/standalone/clickhouse/users.d/zz-small-host.xml')
BINDS = {'app': {'/etc/futureagi/secrets/agentcc.yaml': CONFIG_FILES[2],
                 '/etc/futureagi/secrets/vertex.json': '/dev/null'},
         'postgres': {},
         'clickhouse': {'/etc/clickhouse-server/users.d/zz-default-password.xml': CONFIG_FILES[3],
                        '/etc/clickhouse-server/config.d/storage-policy.xml': CONFIG_FILES[4],
                        '/etc/clickhouse-server/config.d/zz-small-host.xml': CONFIG_FILES[5],
                        '/etc/clickhouse-server/users.d/zz-small-host.xml': CONFIG_FILES[6]}}
LOCAL_ENV = {'STORAGE_BACKEND': 'minio', 'S3_ENDPOINT_URL': 'http://127.0.0.1:9005',
             'TEMPORAL_HOST': '127.0.0.1:7233', 'PG_HOST': 'postgres', 'PG_PORT': '5432',
             'PGBOUNCER_HOST': 'postgres', 'PGBOUNCER_PORT': '5432', 'CH_HOST': 'clickhouse',
             'CH_PORT': '9000', 'CH_HTTP_PORT': '8123', 'PROPERTY_CATALOG_CH_HOST': 'clickhouse',
             'PROPERTY_CATALOG_CH_PORT': '9000', 'FI_CH_URL': 'http://clickhouse:8123',
             'FI_DEAD_LETTER_FILE': '/data/collector/dead_letter.jsonl',
             'FI_OBSERVED_CATALOG_MODE': 'direct', 'FI_OBSERVED_CATALOG_CH_URL': 'http://clickhouse:8123',
             'FI_OBSERVED_CATALOG_SPOOL_DIR': '/data/collector/observed-catalog',
             'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': '6379', 'FI_AUTH_REDIS_ADDR': '127.0.0.1:6379'}
MAX_METADATA = 4 * 1024 * 1024
_LABEL = 'ai.futureagi.cold-backup'
_ID = re.compile(r'[0-9a-f]{64}')
_IMAGE = re.compile(r'sha256:[0-9a-f]{64}')


class BackupError(RuntimeError):
    """Sanitized classification only; private paths/daemon output never escape."""

    def __init__(self, code: str, *, recovery_required: bool = False):
        self.code, self.recovery_required = code, recovery_required
        super().__init__('recovery_required' if recovery_required else code)


def _need(ok, code='unsupported_backup_layout'):
    if not ok:
        raise BackupError(code)


@dataclass(frozen=True, repr=False)
class BackupBinding:
    """Immutable private machine observation; serialize raw in host-owned state."""

    raw: bytes


@dataclass(frozen=True)
class BackupPolicy:
    max_archive_bytes: int = 256 * 1024 ** 3
    max_inventory_bytes: int = 256 * 1024 ** 2
    max_members: int = 1000000
    reserve_bytes: int = 64 * 1024 ** 2
    timeout_seconds: int = 3600
    readiness_seconds: int = 600

    def __post_init__(self):
        for name in ('max_archive_bytes', 'max_inventory_bytes', 'max_members',
                     'reserve_bytes', 'timeout_seconds', 'readiness_seconds'):
            value = getattr(self, name)
            _need(type(value) is int and value > 0, 'invalid_backup_policy')
        _need(self.timeout_seconds <= 86400 and self.readiness_seconds <= 7200, 'invalid_backup_policy')


@dataclass(frozen=True)
class RecoveryInspection:
    state: str
    can_restore_availability: bool


def _stamp(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _file_identity(info):
    # Reading may update atime; it must not invalidate our own verification.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _private(fd, directory=False):
    info = os.fstat(fd)
    _need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
          and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
          and (directory or info.st_nlink == 1), 'unsafe_backup_path')
    _plain_metadata(fd)


@contextmanager
def _directory(path, *, private=True):
    path = Path(path)
    _need(path.is_absolute() and '..' not in path.parts, 'unsafe_backup_path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            info = os.fstat(fd)
            _need(info.st_uid in (0, os.geteuid()) and
                  (not info.st_mode & 0o022 or (info.st_uid == 0 and info.st_mode & stat.S_ISVTX)),
                  'unsafe_backup_path')
        if private:
            _private(fd, True)
            _filesystem(fd)
        yield fd
    finally:
        os.close(fd)


def create_destination(path: Path) -> None:
    """One-time explicit host setup. Create exclusively, or validate; never chmod."""
    with _directory(path.parent, private=False) as parent:
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
            sync_dir(parent)
        except FileExistsError:
            pass
    with _directory(path):
        pass


def _new(fd, name):
    return os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)


def _write_all(fd, raw):
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        _need(count > 0, 'backup_write_failed')
        view = view[count:]


def _read(fd, name, maximum=MAX_METADATA):
    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        _private(child)
        before = os.fstat(child)
        _need(before.st_size <= maximum, 'backup_metadata_invalid')
        chunks, count = [], 0
        while count <= maximum:
            chunk = os.read(child, min(65536, maximum + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        _need(_file_identity(os.fstat(child)) == _file_identity(before) and count == before.st_size, 'backup_metadata_changed')
        return b''.join(chunks)
    finally:
        os.close(child)


def _atomic(fd, name, raw, *, replace=False):
    """Create-only proof/inventory; replacement is ONLY for the private ledger."""
    temp = '.write-' + uuid.uuid4().hex
    child = _new(fd, temp)
    try:
        _write_all(child, raw)
        sync_file(child)
    finally:
        os.close(child)
    try:
        if replace:
            _read(fd, name)  # existing ledger must still be private/single-linked
            os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            os.link(temp, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.unlink(temp, dir_fd=fd)
        sync_dir(fd)
    finally:
        try:
            os.unlink(temp, dir_fd=fd)
        except FileNotFoundError:
            pass


def _files(root):
    result = {}
    for name in CONFIG_FILES:
        path = root / name
        try:
            with _directory(path.parent, private=False) as parent:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    before = os.fstat(fd)
                    _need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                          and (before.st_nlink == 1 or name == '.env' and before.st_nlink == 2)
                          and not before.st_mode & 0o022
                          and before.st_size <= MAX_METADATA, 'unsafe_configuration')
                    with os.fdopen(os.dup(fd), 'rb') as stream:
                        raw = stream.read(MAX_METADATA + 1)
                    _need(_file_identity(os.fstat(fd)) == _file_identity(before) and len(raw) == before.st_size, 'configuration_changed')
                    if before.st_nlink == 2:
                        # Only selected .env may have the native commit's audit
                        # anchor. A name/inode match alone is not qualification.
                        qualify_retained_env(path, fd, before, raw, maximum=MAX_METADATA)
                    _need(_file_identity(os.fstat(fd)) == _file_identity(before)
                          and _file_identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == _file_identity(before),
                          'configuration_changed')
                    result[name] = raw
                finally:
                    os.close(fd)
        except (OSError, CommitError):
            raise BackupError('unsafe_configuration') from None
    return result


def _command(installation, context, *args, timeout=None):
    runner = installation.runner
    from .docker_host import ReadOnlyRunner
    if isinstance(runner, ReadOnlyRunner):
        # Successor enrollment only needs fixed inspections. This adapter must
        # never run a mutator or mark the mutation supervisor uncertain when a
        # pure read times out. Context selection remains explicit and pinned.
        read = (args[:2] in (('context', 'inspect'), ('image', 'inspect'), ('volume', 'inspect'))
                or args == ('info', '--format', '{{json .ID}}')
                or args == ('ps', '-aq', '--no-trunc') or (args[:1] == ('inspect',) and len(args) > 1)
                or (len(args) == 4 and args[:1] == ('exec',) and args[2:] == ('/bin/tar', '--version'))
                or (len(args) == 6 and args[:1] == ('exec',) and args[2:5] == ('/bin/stat', '-c%d:%i', '--')
                    and args[5] in {target for _, target in VOLUMES.values()}))
        _need(read and installation.docker[-2:] == ['--context', context], 'backup_supervisor_required')
    else:
        _need(isinstance(runner, ProcessSupervisor) and runner.docker_context == context,
              'backup_supervisor_required')
    result = runner([*installation.docker, *args], cwd=str(installation.root),
                    capture_output=True, timeout=timeout)
    _need(result.returncode == 0, 'backup_command_failed')
    return result.stdout.decode() if isinstance(result.stdout, bytes) else result.stdout


def _json(installation, context, *args):
    return json.loads(_command(installation, context, *args))


def _all_containers(installation, context):
    ids = _command(installation, context, 'ps', '-aq', '--no-trunc').split()
    _need(len(ids) <= 1024 and all(_ID.fullmatch(cid) for cid in ids), 'daemon_inventory_unbounded')
    result = []
    for offset in range(0, len(ids), 16):
        result.extend(_json(installation, context, 'inspect', *ids[offset:offset + 16]))
    _need(len(result) == len(ids) and {c['Id'] for c in result} == set(ids), 'daemon_inventory_changed')
    return result


def _identity(container):
    # Engine Mounts is a map projected as an array; order can change on each
    # inspect. Retain every mount field but compare by its unique destination.
    value = {key: container[key] for key in ('Id', 'Image', 'Created', 'Config', 'HostConfig')}
    value['HostConfig'] = dict(value['HostConfig'])
    # Docker/containerd canonicalizes disabled=false to null on first start.
    # Both mean OOM killing remains enabled; true must still compare different.
    if value['HostConfig'].get('OomKillDisable') is None:
        value['HostConfig']['OomKillDisable'] = False
    value['Mounts'] = sorted(container['Mounts'], key=lambda m: m['Destination'])
    return value


def _ready(container, service):
    state = container['State']
    if not state.get('Running') or state.get('Paused') or state.get('Restarting') or state.get('Dead'):
        return False
    health = state.get('Health', {}).get('Status')
    return health == 'healthy' if service in ('app', 'postgres', 'clickhouse') or health else True


def _volume(raw):
    return {key: raw.get(key) for key in ('Name', 'Driver', 'Mountpoint', 'CreatedAt', 'Labels', 'Options', 'Scope')}


def _observe(installation, context, daemon, project, profiles, images, *, running=True):
    _need(installation.mode == 'standalone' and not installation.production)
    _need(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project))
    _need(set(profiles) <= {'ml', 'sandbox'} and len(set(profiles)) == len(profiles))
    services = {'app', 'postgres', 'clickhouse'}
    services.update({'serving'} if 'ml' in profiles else set())
    services.update({'code-executor'} if 'sandbox' in profiles else set())
    _need(set(images) == services and all(_IMAGE.fullmatch(v) for v in images.values()))
    endpoint = _json(installation, context, 'context', 'inspect', context)[0]
    _need(endpoint.get('Name') == context
          and endpoint.get('Endpoints', {}).get('docker', {}).get('Host', '').startswith('unix:///'),
          'remote_backup_daemon_unsupported')
    _need(_json(installation, context, 'info', '--format', '{{json .ID}}') == daemon, 'daemon_changed')
    model = installation.config(refresh=True)
    _need(model.get('name') == project and set(model['services']) == services)
    installed = _json(installation, context, 'image', 'inspect', *sorted(set(images.values())))
    _need(len(installed) == len(set(images.values()))
          and {i['Id'] for i in installed} == set(images.values()) and all(i.get('Os') == 'linux' for i in installed))
    image_config = {i['Id']: i['Config'] for i in installed}
    root = str(installation.root)
    _need(all(model['services']['app']['environment'].get(k, v if k == 'PGBOUNCER_PORT' else None) == v
              for k, v in LOCAL_ENV.items()))
    _need(set(model.get('volumes', {})) == set(VOLUMES))
    volumes = {}
    for key in VOLUMES:
        entry = model['volumes'][key]
        _need(entry.get('name') == project + '_' + key and not entry.get('external')
              and not entry.get('driver_opts') and entry.get('driver', 'local') == 'local')
        raw = _json(installation, context, 'volume', 'inspect', project + '_' + key)[0]
        _need(raw['Name'] == project + '_' + key and raw['Driver'] == 'local'
              and raw['Scope'] == 'local' and not raw.get('Options') and raw.get('CreatedAt')
              and raw.get('Mountpoint') and raw.get('Labels', {}).get('com.docker.compose.project') == project
              and raw['Labels'].get('com.docker.compose.volume') == key)
        volumes[key] = _volume(raw)
    all_containers = _all_containers(installation, context)
    enrolled = {}
    for container in all_containers:
        labels = container['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.project') != project:
            continue
        service = labels.get('com.docker.compose.service')
        _need(service in services and service not in enrolled
              and labels.get('com.docker.compose.oneoff', '').lower() == 'false'
              and labels.get('com.docker.compose.project.working_dir') == root
              and labels.get('com.docker.compose.project.config_files') == root + '/docker-compose.yml')
        _need(container['Image'] == images[service], 'original_image_changed')
        desired = model['services'][service]
        _need(not desired.get('volumes_from') and not desired.get('devices') and not desired.get('tmpfs'))
        defaults = image_config[images[service]]
        for runtime_key, model_key in (('Cmd', 'command'), ('Entrypoint', 'entrypoint')):
            selected = desired.get(model_key)
            if selected is None:  # Compose emits explicit null for inherited image defaults
                selected = defaults.get(runtime_key)
                if runtime_key == 'Cmd' and desired.get('entrypoint') is not None:
                    selected = []  # an explicit entrypoint discards the image's default command
            selected = ([selected] if selected else []) if isinstance(selected, str) else selected
            _need((container['Config'].get(runtime_key) or []) == (selected or []), 'runtime_configuration_changed')
        for runtime_key, model_key in (('User', 'user'), ('WorkingDir', 'working_dir')):
            selected = desired.get(model_key)
            selected = defaults.get(runtime_key) if selected is None else selected
            _need((container['Config'].get(runtime_key) or '') == (selected or ''), 'runtime_configuration_changed')
        actual_env = dict(item.split('=', 1) for item in container['Config'].get('Env', []) if '=' in item)
        expected_env = dict(item.split('=', 1) for item in defaults.get('Env', []) or [] if '=' in item)
        for key, value in desired.get('environment', {}).items():
            if value is None:
                expected_env.pop(key, None)
            else:
                expected_env[key] = str(value)
        _need(actual_env == expected_env, 'runtime_configuration_changed')
        if service == 'postgres':
            _need(actual_env.get('PGDATA') == '/var/lib/postgresql/data')
        bindings = {target: (source if source == '/dev/null' else root + '/' + source)
                    for target, source in BINDS.get(service, {}).items()}
        data = {target: volumes[key] for key, (owner, target) in VOLUMES.items() if owner == service}
        wanted = {target: ('volume', key, False) for key, (owner, target) in VOLUMES.items() if owner == service}
        wanted.update({target: ('bind', source, True) for target, source in bindings.items()})
        declared = desired.get('volumes', [])
        _need(len(declared) == len(wanted) and {m['target']: (m['type'], m.get('source'), m.get('read_only', False))
                                               for m in declared} == wanted)
        mounts = container['Mounts']
        _need(len(mounts) == len(data) + len(bindings))
        for mount in mounts:
            target = mount['Destination']
            if target in data:
                vol = data.pop(target)
                _need(mount['Type'] == 'volume' and mount.get('Name') == vol['Name']
                      and mount['Source'] == vol['Mountpoint'] and mount.get('RW') is True)
            else:
                _need(target in bindings and mount['Type'] == 'bind'
                      and mount['Source'] == bindings.pop(target) and mount.get('RW') is False)
        _need(not data and not bindings)
        if running:
            _need(_ready(container, service), 'original_unavailable')
        enrolled[service] = container
    _need(set(enrolled) == services)
    own = {c['Id'] for c in enrolled.values()}
    names = {v['Name'] for v in volumes.values()}
    sources = {v['Mountpoint'] for v in volumes.values()}
    for container in all_containers:
        if container['Id'] not in own:
            _need(not any(m.get('Name') in names or any(
                m.get('Source') and (m['Source'] == source or m['Source'].startswith(source + '/')
                                    or source.startswith(m['Source'].rstrip('/') + '/'))
                for source in sources) for m in container['Mounts']), 'foreign_volume_user')
    return model, enrolled, volumes


def _automatic_layout():
    """Consume the gate owner's reviewed layout; fail closed on coverage drift."""
    with Path(__file__).with_name('automatic-layout.json').open('rb') as stream:
        raw = stream.read(65537)
    _need(len(raw) <= 65536, 'unsupported_installation')
    value = json.loads(raw)
    _need(value.get('schema_version') == 1 and value.get('runtime_activation_contract') == 'standalone-compose-direct-v1'
          and value.get('automatic_profiles') == [] and value.get('compose_file') == 'docker-compose.yml',
          'unsupported_installation')
    active = {s: entry for s, entry in value['services'].items() if not entry.get('profiles')}
    _need(set(active) == {owner for owner, _ in VOLUMES.values()}
          and set(value['volumes']) == set(value['backup_coverage']) == set(VOLUMES), 'unsupported_installation')
    covered = []
    for key, (owner, target) in VOLUMES.items():
        coverage = value['backup_coverage'][key]
        _need(coverage['method'] == 'cold_archive' and coverage['restore'] == 'same_named_volume'
              and any(m == dict(type='volume', source=key, target=target, read_only=False)
                      for m in active[owner]['mounts']), 'unsupported_installation')
        covered.extend(coverage['domains'])
    _need(sorted(covered) == sorted(DOMAINS), 'unsupported_installation')
    for service, entry in active.items():
        binds = {m['target']: m['source'].removeprefix('./') for m in entry['mounts'] if m['type'] == 'bind'}
        _need(binds == BINDS[service] and entry['restart'] in ('unless-stopped', 'no'), 'unsupported_installation')
    return _hash(raw)


def _root_ids(installation, context, containers):
    values = {}
    for key, (service, target) in VOLUMES.items():
        value = _command(installation, context, 'exec', containers[service]['Id'],
                         '/bin/stat', '-c%d:%i', '--', target).strip()
        _need(re.fullmatch(r'[0-9]+:[0-9]+', value), 'volume_identity_unknown')
        values[key] = value
    return values


def capture_binding(installation, *, installation_fingerprint: str, docker_context: str,
                    daemon_id: str, project: str, profiles: Tuple[str, ...],
                    verified_images: Mapping[str, str], deployment_hashes: Mapping[str, str],
                    previous: Optional[BackupBinding] = None) -> BackupBinding:
    """Observe a qualified baseline; never derives trust from mutable image tags.

    deployment_hashes MUST be from the host-verified execution layout, not newly
    hashed unapproved files. previous is mandatory when advancing an enrollment;
    it prevents adopting replacement stores. Not a substitute for state/release
    qualification. This routine does not stop/start containers or emit a proof.
    """
    _need(_ID.fullmatch(installation_fingerprint))
    model, containers, volumes = _observe(installation, docker_context, daemon_id, project,
                                          profiles, verified_images)
    files = _files(installation.root)
    _need(all(deployment_hashes.get(k) == _hash(files[k]) for k in CONFIG_FILES[1:]),
          'unqualified_execution_files')
    helper = _json(installation, docker_context, 'image', 'inspect', verified_images['postgres'])[0]
    _need(helper['Id'] == verified_images['postgres'] and helper.get('Os') == 'linux'
          and set(helper['Config'].get('Volumes') or {}) == {'/var/lib/postgresql/data'},
          'archive_helper_prerequisite')
    version = _command(installation, docker_context, 'exec', containers['postgres']['Id'], '/bin/tar', '--version')
    _need(version.startswith('tar (GNU tar)'), 'archive_helper_prerequisite')
    doc = dict(schema_version=1, installation_fingerprint=installation_fingerprint,
               root=str(installation.root), context=docker_context, daemon=daemon_id, project=project,
               profiles=sorted(profiles), images=dict(verified_images), volumes=volumes,
               volume_root_ids=_root_ids(installation, docker_context, containers),
               files={k: _hash(v) for k, v in files.items()}, compose_sha256=_hash(canonical_json(model)))
    if previous is not None:
        old = json.loads(previous.raw)
        _need(all(doc[k] == old[k] for k in ('root', 'context', 'daemon', 'project', 'profiles',
                                           'volumes', 'volume_root_ids')), 'enrolled_storage_changed')
    return BackupBinding(canonical_json(doc))


class _BoundedTarInfo(tarfile.TarInfo):
    def _bound_metadata(self, archive):
        archive._backup_metadata_bytes = getattr(archive, '_backup_metadata_bytes', 0) + self.size + 512
        _need(0 <= self.size and archive._backup_metadata_bytes <= MAX_METADATA, 'archive_metadata_unbounded')

    def _proc_pax(self, archive):
        _need(self.type != tarfile.XGLTYPE, 'unsupported_archive_format')  # helper emits per-file PAX only
        self._bound_metadata(archive)
        return super()._proc_pax(archive)

    def _proc_gnulong(self, archive):
        self._bound_metadata(archive)
        return super()._proc_gnulong(archive)

    def _proc_sparse(self, archive):
        raise BackupError('unsupported_archive_format')  # helper emits PAX, not legacy GNU sparse headers

    def _proc_gnusparse_10(self, member, headers, archive):
        # Stdlib's parser trusts an unbounded count from a payload header.
        buf = archive.fileobj.read(512)
        fields, buf = buf.split(b'\n', 1)
        _need(len(fields) <= 8 and fields.isdigit() and int(fields) <= 65536, 'archive_metadata_unbounded')
        sparse = []
        for _ in range(int(fields) * 2):
            if b'\n' not in buf:
                buf += archive.fileobj.read(512)
            number, buf = buf.split(b'\n', 1)
            _need(len(number) <= 24 and number.isdigit(), 'archive_metadata_unbounded')
            sparse.append(int(number))
        member.offset_data = archive.fileobj.tell()
        member.sparse = list(zip(sparse[::2], sparse[1::2]))


def _member_path(name):
    _need(isinstance(name, str) and bool(name) and '\0' not in name and len(name) <= 4096 and not name.startswith('/')
          and posixpath.normpath(name) != '..' and not posixpath.normpath(name).startswith('../'),
          'unsafe_archive_member')


def verify_archive(fd: int, inventory_fd: int, policy: BackupPolicy, *, deadline: float,
                   volume_root: Optional[str] = None, complete_tree: bool = False) -> dict:
    from .archive_inventory import verify_archive as verify
    return verify(fd, inventory_fd, policy, deadline=deadline, volume_root=volume_root,
                  complete_tree=complete_tree, allow_legacy_absolute_links=not complete_tree)


@dataclass(frozen=True, repr=False)
class BackupArtifact:
    proof: BackupProof
    seal: BackupSeal
    hold: ColdHold


class StandaloneBackup:
    def __init__(self, installation, binding: BackupBinding, destination: Path, *,
                 policy: BackupPolicy = BackupPolicy(), clock=None):
        self.installation, self.binding, self.destination = installation, binding, Path(destination)
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        _need(isinstance(binding.raw, bytes) and len(binding.raw) <= MAX_METADATA, 'invalid_backup_binding')
        self.b = json.loads(binding.raw)
        _need(self.b.get('schema_version') == 1 and self.b['root'] == str(installation.root))
        runner = installation.runner
        _need(isinstance(runner, ProcessSupervisor) and runner.docker_context == self.b['context']
              and runner.policy.probe_timeout_seconds >= 70, 'backup_supervisor_required')

    @contextmanager
    def _budget(self, seconds):
        runner = self.installation.runner
        previous = runner.deadline
        self.deadline = runner.deadline = min(previous, time.monotonic() + seconds)
        try:
            yield
        finally:
            runner.deadline = previous  # only narrow; never extend the registered tick budget

    @contextmanager
    def _locked(self):
        with _directory(self.destination) as directory:
            lock = os.open('backup.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=directory)
            try:
                _private(lock)
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise BackupError('backup_busy') from None
                yield directory
            finally:
                os.close(lock)

    def _observe(self, *, running):
        model, containers, volumes = _observe(self.installation, self.b['context'], self.b['daemon'],
                                              self.b['project'], self.b['profiles'], self.b['images'], running=running)
        files = _files(self.installation.root)
        _need(_hash(canonical_json(model)) == self.b['compose_sha256']
              and {k: _hash(v) for k, v in files.items()} == self.b['files'], 'configuration_changed')
        _need(volumes == self.b['volumes'], 'enrolled_storage_changed')
        if running:
            _need(_root_ids(self.installation, self.b['context'], containers) == self.b['volume_root_ids'],
                  'enrolled_storage_changed')
        return model, containers, files

    def _save(self, directory, ledger):
        raw = canonical_json(ledger)
        _need(len(raw) <= MAX_METADATA, 'backup_metadata_unbounded')
        _atomic(directory, 'ledger.json', raw, replace=True)

    def _exact(self, ledger):
        _, containers, _ = self._observe(running=False)
        _need({s: _identity(c) for s, c in containers.items()} == ledger['containers'], 'originals_changed')
        for service, container in containers.items():
            state = container['State']
            _need(not state.get('Paused') and not state.get('Restarting') and not state.get('Dead'))
            _need(state.get('Running') or service in ledger['stopped'], 'unrecorded_stop')
            if ledger['phase'] in ('prepared', 'quiescing', 'quiesced'):
                _need(state.get('StartedAt') == ledger['start_times'][service]
                      and container.get('RestartCount') == ledger['restart_counts'][service], 'original_restarted')
            if ledger['phase'] == 'quiesced':
                _need(not state.get('Running'), 'original_not_quiescent')
        return containers

    def _op(self, directory, ledger, operation, *args):
        ledger['pending'] = operation
        self._save(directory, ledger)  # durable BEFORE sending any mutation to Docker
        result = _command(self.installation, self.b['context'], *args)
        ledger['pending'] = None
        self._save(directory, ledger)
        return result

    def _wait(self, ledger, services):
        deadline = min(self.deadline, time.monotonic() + self.policy.readiness_seconds)
        while time.monotonic() < deadline:
            containers = self._exact(ledger)
            if all(_ready(containers[s], s) for s in services):
                return
            time.sleep(0.2)
        raise BackupError('original_readiness_failed')

    def _restart(self, directory, ledger):
        _need(ledger['pending'] is None and not self.installation.runner.uncertain, 'uncertain_backup_child')
        self._exact(ledger)  # all-or-nothing preflight BEFORE starting any original
        ledger['phase'] = 'restoring_availability'
        self._save(directory, ledger)
        for service in ('postgres', 'clickhouse', 'app', 'serving', 'code-executor'):
            if service not in ledger['containers']:
                continue
            containers = self._exact(ledger)
            if not containers[service]['State']['Running']:
                self._op(directory, ledger, {'kind': 'start', 'service': service},
                         'start', ledger['containers'][service]['Id'])
            self._wait(ledger, [service])
            # Once this original is healthy again, our stop is undone. Retire
            # that responsibility durably: a later operator stop is NOT ours
            # to undo on a repeated recovery call or a subsequent crash.
            if service in ledger['stopped']:
                ledger['stopped'].remove(service)
                self._save(directory, ledger)
        self._observe(running=True)
        ledger['phase'] = 'available'
        self._save(directory, ledger)

    def _archive(self, directory, ledger, key):
        # No --rm: retain/inspect an exact helper after an acknowledged exit.
        helper_name = self.b['project'] + '-backup-' + ledger['expectation']['attempt_id'] + '-' + key
        volume = self.b['volumes'][key]['Name']
        args = ['run', '--name', helper_name, '--label', _LABEL + '=' + ledger['expectation']['attempt_id'],
                '--pull', 'never', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                '--cap-add', 'DAC_READ_SEARCH', '--security-opt', 'no-new-privileges',
                '--pids-limit', '32', '--memory', '128m', '--cpus', '1', '--user', '0:0',
                '--mount', 'type=volume,source=' + volume + ',target=/var/lib/postgresql/data,readonly,volume-nocopy',
                '--entrypoint', '/bin/tar', self.b['images']['postgres'],
                '--format=pax', '--numeric-owner', '--acls', '--xattrs', '--sparse',
                '-C', '/var/lib/postgresql/data', '-cpf', '-', '.']
        ledger['pending'] = {'kind': 'archive', 'name': helper_name, 'volume': key}
        self._save(directory, ledger)
        fd = _new(directory, key + '.tar')
        try:
            result = self.installation.runner.stream_to(
                [*self.installation.docker, *args], fd, max_bytes=self.policy.max_archive_bytes,
                reserve_bytes=self.policy.reserve_bytes, timeout=max(0, self.deadline - time.monotonic()))
            helper = _json(self.installation, self.b['context'], 'inspect', helper_name)[0]
            _need(helper['Name'] == '/' + helper_name and _ID.fullmatch(helper['Id'])
                  and helper['Image'] == self.b['images']['postgres']
                  and helper['Config'].get('Labels', {}).get(_LABEL) == ledger['expectation']['attempt_id']
                  and helper['Config'].get('Entrypoint') == ['/bin/tar']
                  and helper['Config'].get('Cmd') == args[args.index(self.b['images']['postgres']) + 1:]
                  and helper['HostConfig'].get('NetworkMode') == 'none'
                  and helper['HostConfig'].get('ReadonlyRootfs') is True
                  and len(helper['Mounts']) == 1 and helper['Mounts'][0].get('Name') == volume
                  and helper['Mounts'][0].get('Source') == self.b['volumes'][key]['Mountpoint']
                  and helper['Mounts'][0].get('Destination') == '/var/lib/postgresql/data'
                  and helper['Mounts'][0].get('RW') is False
                  and not helper['State']['Running'] and helper['State']['Status'] == 'exited'
                  and helper['State']['ExitCode'] == result.returncode, 'archive_child_unknown')
            ledger['pending'] = None
            ledger['helpers'].append({'id': helper['Id'], 'name': helper_name, 'exit_code': result.returncode})
            self._save(directory, ledger)
            self._op(directory, ledger, {'kind': 'remove_helper', 'id': helper['Id']}, 'rm', helper['Id'])
            _need(result.returncode == 0 and not result.stderr, 'archive_failed')
            sync_file(fd)
            member_fd = _new(directory, key + '.members.jsonl')
            try:
                entry = verify_archive(fd, member_fd, self.policy, deadline=self.deadline, volume_root=VOLUMES[key][1])
            finally:
                os.close(member_fd)
            _need(entry['size'] == result.size and entry['sha256'] == result.sha256, 'archive_changed')
            entry['volume'] = self.b['volumes'][key]
            return entry
        finally:
            os.close(fd)

    def _expectation(self, expected):
        _need(isinstance(expected, BackupExpectation)
              and expected.installation_fingerprint == self.b['installation_fingerprint']
              and set(expected.covered_domains) == set(DOMAINS), 'backup_expectation_mismatch')
        return dict(attempt_id=expected.attempt_id, installation_fingerprint=expected.installation_fingerprint,
                    target_manifest_hash=expected.target_manifest_hash, source_version=expected.source_version,
                    reserved_at=expected.reserved_at.isoformat(), covered_domains=list(expected.covered_domains))

    def run(self, expected: BackupExpectation, *, proof_directory_fd: int,
            proof_filename: str = 'proof.json') -> BackupProof:
        """Legacy v1 backup; the restore-backed lifecycle must use run_quiesced."""
        _need(getattr(self.installation.runner.policy, 'qualification_policy', None) is None,
              'automatic_recovery_pending')
        expectation = self._expectation(expected)
        _need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', proof_filename), 'unsafe_backup_path')
        _private(proof_directory_fd, True)
        _filesystem(proof_directory_fd)
        _need(proof_filename not in os.listdir(proof_directory_fd), 'backup_proof_exists')
        with self._locked() as parent, self._budget(self.policy.timeout_seconds):
            try:
                os.mkdir(expected.attempt_id, 0o700, dir_fd=parent)
            except FileExistsError:
                raise BackupError('backup_attempt_already_exists') from None
            sync_dir(parent)
            directory = os.open(expected.attempt_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            ledger = None
            try:
                _private(directory, True)
                started = self.clock().astimezone(timezone.utc).replace(microsecond=0)
                _need(started >= expected.reserved_at, 'backup_clock_skew')
                model, containers, files = self._observe(running=True)
                space = os.fstatvfs(directory)
                _need(space.f_bavail * space.f_frsize >= self.policy.reserve_bytes + 4 * MAX_METADATA
                      + sum(map(len, files.values())), 'backup_destination_full')
                for index, (name, raw) in enumerate(files.items()):
                    _atomic(directory, 'config-' + str(index), raw)
                _atomic(directory, 'compose.json', canonical_json(model))
                ledger = dict(schema_version=1, binding_sha256=_hash(self.binding.raw), expectation=expectation,
                              started_at=_stamp(started), phase='prepared', pending=None, stopped=[], helpers=[],
                              containers={s: _identity(c) for s, c in containers.items()},
                              start_times={s: c['State']['StartedAt'] for s, c in containers.items()},
                              restart_counts={s: c['RestartCount'] for s, c in containers.items()})
                _atomic(directory, 'ledger.json', canonical_json(ledger))
                for service in ('app', 'serving', 'code-executor', 'postgres', 'clickhouse'):
                    if service not in containers:
                        continue
                    self._exact(ledger)
                    ledger['phase'] = 'quiescing'
                    ledger['pending'] = {'kind': 'stop', 'service': service}
                    self._save(directory, ledger)
                    _command(self.installation, self.b['context'], 'stop', '--time', '60', containers[service]['Id'])
                    observed = _json(self.installation, self.b['context'], 'inspect', containers[service]['Id'])[0]
                    _need(_identity(observed) == ledger['containers'][service]
                          and not observed['State']['Running'] and observed['State']['Status'] == 'exited',
                          'original_stop_unconfirmed')
                    ledger['stopped'].append(service)
                    ledger['pending'] = None
                    self._save(directory, ledger)
                    _need(observed['State'].get('ExitCode') in (0, 143) and not observed['State'].get('OOMKilled'),
                          'original_stop_unclean')
                ledger['phase'] = 'quiesced'
                self._save(directory, ledger)
                archives = {}
                for key in VOLUMES:
                    current = self._exact(ledger)
                    _need(all(not c['State']['Running'] for c in current.values()), 'original_not_quiescent')
                    archives[key] = self._archive(directory, ledger, key)
                self._restart(directory, ledger)
                completed = self.clock().astimezone(timezone.utc).replace(microsecond=0)
                proof_value = {k: expectation[k] for k in ('attempt_id', 'installation_fingerprint',
                                                         'target_manifest_hash', 'source_version', 'covered_domains')}
                proof_value.update(schema_version=1, backup_id='standalone-' + expected.attempt_id,
                                   started_at=_stamp(started), completed_at=_stamp(completed),
                                   verification='passed', restore_procedure_id=RESTORE_PROCEDURE)
                raw = canonical_json(proof_value)
                validate_backup_proof(raw, expected, now=self.clock())
                inventory = dict(schema_version=1, binding=json.loads(self.binding.raw), expectation=expectation,
                                 containers=ledger['containers'], archives=archives, config_files=[{'name': k, 'file': 'config-' + str(i),
                                                                  'sha256': _hash(v), 'size': len(v)}
                                                                 for i, (k, v) in enumerate(files.items())],
                                 started_at=_stamp(started), completed_at=_stamp(completed),
                                 restore_procedure_id=RESTORE_PROCEDURE, proof_sha256=_hash(raw))
                inventory_raw = canonical_json(inventory)
                _atomic(directory, 'inventory.json', inventory_raw)
                ledger.update(phase='complete', inventory_sha256=_hash(inventory_raw), proof_sha256=_hash(raw))
                self._save(directory, ledger)
                _atomic(proof_directory_fd, proof_filename, raw)
                return read_backup_proof(proof_directory_fd, proof_filename, expected,
                                         now=self.clock(), expected_uid=os.geteuid())
            except BaseException as exc:
                recovery = ledger is not None and bool(ledger['stopped'] or ledger['pending'])
                if recovery and ledger['pending'] is None and not self.installation.runner.uncertain:
                    try:
                        self._restart(directory, ledger)
                        ledger['phase'] = 'failed_available'
                        self._save(directory, ledger)
                        recovery = False
                    except BaseException:
                        recovery = True
                code = exc.code if isinstance(exc, BackupError) else 'backup_failed'
                raise BackupError(code, recovery_required=recovery) from None
            finally:
                os.close(directory)

    def _cold_hold(self, ledger, source_observation, *, env_recovery=None, check_roots=True):
        from .contracts import ColdHold
        from .standalone_restore import _StorageHelpers
        doc = ledger.snapshot
        _need(doc['pending'] is None and not self.installation.runner.uncertain, 'restore_child_ambiguous')
        _need(doc['binding_sha256'] == source_observation.binding_sha256
              and doc['source_observation_sha256'] == source_observation.sha256, 'backup_inventory_mismatch')
        _need(_json(self.installation, self.b['context'], 'info', '--format', '{{json .ID}}') == self.b['daemon'],
              'daemon_changed')
        helpers = _StorageHelpers(self.installation, self.b, ledger, policy=self.policy)
        current = _all_containers(self.installation, self.b['context'])
        by_id = {c['Id']: c for c in current}
        counts, policies = {}, {}
        for service, identity in doc['containers'].items():
            cid = identity['Id']
            _need(cid in by_id and _identity(by_id[cid]) == identity, 'originals_changed')
            container = by_id[cid]
            _need(not any(container['State'].get(k) for k in ('Running', 'Paused', 'Restarting', 'Dead'))
                  and container['State']['Status'] == 'exited'
                  and container['RestartCount'] == doc['restart_counts'][service]
                  and container['State']['StartedAt'] == doc['start_times'][service], 'original_not_quiescent')
            counts[cid] = container['RestartCount']
            policies[cid] = container['HostConfig']['RestartPolicy']['Name']
            _need(policies[cid] in ('unless-stopped', 'no'), 'unsupported_installation')
        for key in VOLUMES:
            volume = _json(self.installation, self.b['context'], 'volume', 'inspect', self.b['volumes'][key]['Name'])[0]
            _need(_volume(volume) == self.b['volumes'][key], 'enrolled_storage_changed')
            helpers.assert_no_running_consumers(key)
            if check_roots:
                helpers.inspect(key)
        current_files = {k: _hash(v) for k, v in _files(self.installation.root).items()}
        if env_recovery is not None:
            from . import envfile
            original = envfile.recovery_text(self.installation.root / '.env', env_recovery)
            _need(_hash(original.encode('utf-8', 'surrogateescape')) == self.b['files']['.env'], 'configuration_changed')
            current_files['.env'] = self.b['files']['.env']
        _need(current_files == self.b['files'], 'configuration_changed')
        snapshot = datetime.fromisoformat(doc['snapshot_at'].replace('Z', '+00:00'))
        return ColdHold(attempt_id=doc['attempt_id'], operation_token=doc['operation_token'], run_id=doc['run_id'],
            binding_sha256=doc['binding_sha256'], source_observation_sha256=source_observation.sha256,
            quiescence_evidence_sha256=doc['quiescence_evidence_sha256'], snapshot_at=snapshot,
            verified_at=self.clock(), daemon_identity=self.b['daemon'], storage_sha256=source_observation.storage_sha256,
            stopped_container_ids=tuple(sorted(counts)), restart_counts=counts, restart_policies=policies)

    def verify_hold(self, ledger, source_observation, *, env_recovery=None):
        """Fresh stopped-state check, retaining the original snapshot/observation time."""
        try:
            with self._budget(self.policy.timeout_seconds):
                return self._cold_hold(ledger, source_observation, env_recovery=env_recovery)
        except BackupError as exc:
            raise BackupError(exc.code, recovery_required=True) from None

    def run_quiesced(self, expected, ledger, source_observation, *, proof_directory_fd,
                     proof_filename='proof.json'):
        """Automatic v2 path: one sealed snapshot, originals NEVER restarted.

        Ledger.reserve precedes this call and ALL stops. Source observation is
        retained healthy A. On failure, caller reconciles pre-seal abort or
        mandatory post-seal restore under its still-held engine locks.
        """
        from .contracts import BackupSeal, RuntimeObservationV2, RESTORE_PROCEDURE_ID
        from .recovery import RecoveryLedger
        from .standalone_restore import _StorageHelpers
        expectation = self._expectation(expected)
        layout_sha256 = _automatic_layout()
        _need(isinstance(ledger, RecoveryLedger) and isinstance(source_observation, RuntimeObservationV2))
        doc = ledger.snapshot
        _need(not self.b['profiles'] and ledger.directory == self.destination / expected.attempt_id
              and doc['expectation'] == expectation and doc['phase'] == 'attempt_reserved'
              and doc['direction'] == 'forward_pending' and doc['backup_seal'] is None
              and doc['pending'] is None, 'backup_expectation_mismatch')
        _need(source_observation.ready and source_observation.stable
              and source_observation.release_version == expected.source_version
              and source_observation.binding_sha256 == doc['binding_sha256'], 'backup_expectation_mismatch')
        _need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', proof_filename)
              and proof_filename not in os.listdir(proof_directory_fd), 'unsafe_backup_path')
        _private(proof_directory_fd, True)
        _filesystem(proof_directory_fd)
        with self._locked(), self._budget(self.policy.timeout_seconds), _directory(ledger.directory) as directory:
            started = self.clock().astimezone(timezone.utc).replace(microsecond=0)
            _need(started >= expected.reserved_at, 'backup_clock_skew')
            model, containers, files = self._observe(running=True)
            timeout_version = _command(self.installation, self.b['context'], 'exec', containers['postgres']['Id'],
                                       '/usr/bin/timeout', '--version')
            _need(timeout_version.startswith('timeout (GNU coreutils)'), 'archive_helper_prerequisite')
            counts = {c['Id']: c['RestartCount'] for c in containers.values()}
            policies = {c['Id']: c['HostConfig']['RestartPolicy']['Name'] for c in containers.values()}
            _need(counts == dict(source_observation.restart_counts)
                  and policies == dict(source_observation.restart_policies)
                  and all(p in ('unless-stopped', 'no') for p in policies.values()), 'unsupported_installation')
            space = os.fstatvfs(directory)
            _need(space.f_bavail * space.f_frsize >= self.policy.reserve_bytes + 4 * MAX_METADATA
                  + sum(map(len, files.values())) and space.f_favail >= 1024, 'backup_destination_full')
            records = []
            for index, (name, raw) in enumerate(files.items()):
                _atomic(directory, 'config-' + str(index), raw)
                info = (self.installation.root / name).stat()
                records.append(dict(name=name, file='config-' + str(index), sha256=_hash(raw), size=len(raw),
                    metadata=dict(uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode),
                                  identity=[info.st_dev, info.st_ino, info.st_mtime_ns])))
            _atomic(directory, 'compose.json', canonical_json(model))
            ledger.checkpoint('quiescing', source_observation_sha256=source_observation.sha256,
                containers={s: _identity(c) for s, c in containers.items()},
                start_times={s: c['State']['StartedAt'] for s, c in containers.items()},
                restart_counts={s: c['RestartCount'] for s, c in containers.items()}, stopped_container_ids=[])
            for service in ('app', 'postgres', 'clickhouse'):
                identity = _identity(containers[service])
                observed = _json(self.installation, self.b['context'], 'inspect', identity['Id'])[0]
                _need(_identity(observed) == identity and observed['RestartCount'] == counts[identity['Id']]
                      and observed['State']['StartedAt'] == containers[service]['State']['StartedAt']
                      and observed['State']['Running'] and not observed['State'].get('Restarting')
                      and not observed['State'].get('Paused'), 'originals_changed')
                op = ledger.begin('stop', identity=identity)
                _command(self.installation, self.b['context'], 'stop', '--time', '60', identity['Id'])
                stopped = _json(self.installation, self.b['context'], 'inspect', identity['Id'])[0]
                _need(_identity(stopped) == identity and not stopped['State']['Running']
                      and stopped['State']['Status'] == 'exited', 'original_stop_unconfirmed')
                # Completion atomically records this proven source stop too;
                # immutable-result replay reconstructs both after a crash.
                ledger.complete(op, dict(id=identity['Id'], stopped=True))
                _need(stopped['State'].get('ExitCode') in (0, 143) and not stopped['State'].get('OOMKilled'),
                      'original_stop_unclean')
            snapshot = self.clock().astimezone(timezone.utc).replace(microsecond=0)
            quiescence = dict(containers=ledger.snapshot['containers'], start_times=ledger.snapshot['start_times'],
                restart_counts=counts, restart_policies=policies, daemon=self.b['daemon'],
                volumes=self.b['volumes'], roots=self.b['volume_root_ids'], snapshot_at=_stamp(snapshot))
            ledger.checkpoint('quiesced', snapshot_at=_stamp(snapshot),
                              quiescence_evidence_sha256=_hash(canonical_json(quiescence)))
            self._cold_hold(ledger, source_observation)
            helpers = _StorageHelpers(self.installation, self.b, ledger, policy=self.policy)
            archives = {}
            ledger.checkpoint('archiving')
            for key in VOLUMES:
                self._cold_hold(ledger, source_observation, check_roots=False)
                helpers.inspect(key)
                archive_fd = _new(directory, key + '.tar')
                try:
                    result = helpers.execute(key, 'archive', tar=True, output_fd=archive_fd,
                                              max_output=self.policy.max_archive_bytes)
                    sync_file(archive_fd)
                    member_fd = _new(directory, key + '.members.jsonl')
                    try:
                        entry = verify_archive(archive_fd, member_fd, self.policy, deadline=self.deadline,
                                               volume_root=VOLUMES[key][1], complete_tree=True)
                    finally:
                        os.close(member_fd)
                    _need(result.sha256 == entry['sha256'] and result.size == entry['size'], 'archive_changed')
                    actual = helpers.inventory(key, directory)
                    _need(actual['sha256'] == entry['members_sha256'] and actual['size'] == entry['members_bytes'],
                          'backup_inventory_mismatch')
                    entry['volume'] = self.b['volumes'][key]
                    archives[key] = entry
                finally:
                    os.close(archive_fd)
            hold = self._cold_hold(ledger, source_observation)
            completed = self.clock().astimezone(timezone.utc).replace(microsecond=0)
            inventory = dict(schema_version=2, inventory_version=2, binding=self.b,
                binding_sha256=doc['binding_sha256'], expectation=expectation,
                automatic_layout_sha256=layout_sha256, source_observation_sha256=source_observation.sha256,
                quiescence_evidence_sha256=hold.quiescence_evidence_sha256,
                quiescence=quiescence, containers=ledger.snapshot['containers'], archives=archives,
                config_files=records, started_at=_stamp(started), snapshot_at=_stamp(snapshot),
                completed_at=_stamp(completed), restore_procedure_id=RESTORE_PROCEDURE_ID)
            inventory_raw = canonical_json(inventory)
            _atomic(directory, 'inventory.json', inventory_raw)
            value = {k: expectation[k] for k in ('attempt_id', 'installation_fingerprint',
                'target_manifest_hash', 'source_version', 'covered_domains')}
            value.update(schema_version=2, backup_id='standalone-' + expected.attempt_id,
                started_at=_stamp(started), completed_at=_stamp(completed), verification='passed',
                restore_procedure_id=RESTORE_PROCEDURE_ID, inventory_version=2,
                binding_sha256=doc['binding_sha256'], inventory_sha256=_hash(inventory_raw),
                source_observation_sha256=source_observation.sha256,
                quiescence_evidence_sha256=hold.quiescence_evidence_sha256, snapshot_at=_stamp(snapshot))
            raw = canonical_json(value)
            proof = validate_backup_proof(raw, expected, now=self.clock(),
                                          max_age_seconds=self.installation.runner.policy.backup_max_age_seconds)
            _atomic(directory, 'proof.json', raw)
            seal = BackupSeal(attempt_id=expected.attempt_id, operation_token=doc['operation_token'],
                run_id=doc['run_id'], binding_sha256=doc['binding_sha256'],
                target_manifest_hash=expected.target_manifest_hash, source_version=expected.source_version,
                proof_sha256=proof.sha256, inventory_sha256=_hash(inventory_raw),
                source_observation_sha256=source_observation.sha256,
                quiescence_evidence_sha256=hold.quiescence_evidence_sha256,
                snapshot_at=snapshot, authorised_at=self.clock())
            ledger.seal_backup(seal)
            _atomic(proof_directory_fd, proof_filename, raw)
            return BackupArtifact(proof, seal, hold)

    def retire_helpers(self, ledger):
        """Detach only exact completed helpers; retain every ID/config/log as evidence."""
        from .standalone_restore import _StorageHelpers
        _need(ledger.directory == self.destination / ledger.snapshot['attempt_id']
              and not self.installation.runner.uncertain, 'restore_child_ambiguous')
        _need(_json(self.installation, self.b['context'], 'info', '--format', '{{json .ID}}') == self.b['daemon'],
              'daemon_changed')
        with self._budget(self.policy.readiness_seconds):
            _StorageHelpers(self.installation, self.b, ledger, policy=self.policy).retire_helpers()

    def abort_quiescence(self, expected, ledger):
        """Only a conclusively quiesced PRE-SEAL attempt may restore availability.

        No ambiguous client, changed original, late helper, env change or sealed
        snapshot is ever converted into an independent availability decision.
        """
        _need(ledger.backup_seal is None and ledger.direction == 'forward_pending'
              and not self.installation.runner.uncertain, 'restore_child_ambiguous')
        _need(ledger.snapshot['expectation'] == self._expectation(expected))
        self.retire_helpers(ledger)
        _need(ledger.snapshot['pending'] is None, 'restore_child_ambiguous')
        with self._budget(self.policy.readiness_seconds):
            doc = ledger.snapshot
            with _directory(ledger.directory) as directory:
                _need('backup-seal.json' not in os.listdir(directory), 'automatic_recovery_pending')
            if doc['phase'] in ('attempt_reserved', 'failed_available'):
                self._observe(running=True)
                ledger.checkpoint('failed_available')
                return RecoveryInspection('failed_available', False)
            _, containers, _ = self._observe(running=False)
            _need({s: _identity(c) for s, c in containers.items()} == doc['containers'], 'originals_changed')
            # A prior partial availability restart may have accepted writes or
            # been stopped by the operator. Never blindly replay its starts.
            if doc['phase'] == 'abort_quiescence':
                _need(all(_ready(c, s) for s, c in containers.items()), 'restore_child_ambiguous')
                ledger.checkpoint('failed_available')
                return RecoveryInspection('failed_available', False)
            _need(all(c['State']['Running'] or c['Id'] in doc.get('stopped_container_ids', [])
                      for c in containers.values()), 'restore_child_ambiguous')
            ledger.checkpoint('abort_quiescence')
            for service in ('postgres', 'clickhouse', 'app'):
                cid = doc['containers'][service]['Id']
                container = _json(self.installation, self.b['context'], 'inspect', cid)[0]
                _need(_identity(container) == doc['containers'][service])
                if not container['State']['Running']:
                    op = ledger.begin('start', identity=doc['containers'][service])
                    _command(self.installation, self.b['context'], 'start', cid)
                    current = _json(self.installation, self.b['context'], 'inspect', cid)[0]
                    _need(_identity(current) == doc['containers'][service] and current['State']['Running'])
                    ledger.complete(op, {'id': cid, 'running': True})
            self._observe(running=True)
            ledger.checkpoint('failed_available')
            return RecoveryInspection('failed_available', False)

    @contextmanager
    def _recovery(self, expected):
        expectation = self._expectation(expected)
        with self._locked() as parent:
            directory = os.open(expected.attempt_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                _private(directory, True)
                ledger = json.loads(_read(directory, 'ledger.json'))
                _need(ledger['expectation'] == expectation and ledger['binding_sha256'] == _hash(self.binding.raw),
                      'recovery_binding_mismatch')
                yield directory, ledger
            finally:
                os.close(directory)

    def inspect_recovery(self, expected: BackupExpectation) -> RecoveryInspection:
        """Read-only bounded inspection; pending commands remain explicitly ambiguous."""
        try:
            with self._budget(self.policy.readiness_seconds), self._recovery(expected) as (_, ledger):
                _need(ledger['pending'] is None and not self.installation.runner.uncertain)
                self._exact(ledger)
                return RecoveryInspection(ledger['phase'], ledger['phase'] not in ('complete', 'failed_available'))
        except Exception:
            return RecoveryInspection('recovery_required', False)

    def recover_availability(self, expected: BackupExpectation) -> RecoveryInspection:
        """Start only proven exact originals; never archive, refresh or emit proof."""
        try:
            with self._budget(self.policy.readiness_seconds), self._recovery(expected) as (directory, ledger):
                _need(ledger['pending'] is None and ledger['phase'] != 'complete')
                if ledger['phase'] == 'failed_available':
                    self._exact(ledger)
                    return RecoveryInspection('failed_available', False)
                self._restart(directory, ledger)
                ledger['phase'] = 'failed_available'
                self._save(directory, ledger)
                return RecoveryInspection('failed_available', False)
        except Exception:
            return RecoveryInspection('recovery_required', False)
