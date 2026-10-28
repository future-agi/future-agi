"""Explicit one-time host enrollment; consent is never structural evidence.

Lock order is runner -> update -> short metadata locks. Scheduler adapters own
runner.lock themselves and are called only after BOTH outer locks are released.
The immutable configuration is host-private, not a script or an app endpoint.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import ssl
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlsplit

from .automatic import BackupConfiguration
from .baseline import BaselineStore, sha
from .compatibility import decode_document
from .contracts import AUTOMATIC_POLICY_BOUNDS, QUALIFICATION_POLICY, ReasonCode
from .discovery import ReleaseDiscovery
from .envfile import render_changes
from . import envcommit
from .host_facts import HostInstallation, installation_binding, safe_path, valid_docker_context
from .httpcache import HttpCache
from .install import COMPONENT_VARIABLES, ComposeCommand, resolve_compose_command
from .manifest import canonical_json, is_version, parse_image_ref
from .orchestrator import check_binding, check_deployment, require, registered_installation
from .processes import ProcessSupervisor
from .receipt import StateDir
from .runner_state import LauncherConfig, Registration, StoreError, StaleGeneration, _wire
from .schedule import next_open
from .scheduler_install import CRON_SERVICES, inspect_activation, install_scheduler, remove_scheduler
from .settings import DEFAULTS, Settings, SettingsError, load_settings
from .standalone_backup import BackupPolicy, capture_binding, create_destination
from .trust import Verifier, http_get, https_url

CONFIG = 'setup-configuration.json'
PROGRESS = 'setup-progress.json'
NORMALIZATION = 'setup-pins.json'
KEY = 'setup-key.pub'
CA = 'setup-ca.pem'
MAX_ANCHOR = 16384

# A5: the only copy of the enrollment disclosure. --yes acknowledges THIS text,
# not merely scheduler installation; interactive setup asks after displaying it.
AUTOMATIC_UPDATE_DISCLOSURE = (
    "Automatic updates stop Future AGI, take a cold backup, and start the new version, "
    "which may migrate the database. Future AGI is unavailable while this happens "
    "(usually several minutes, longer for large databases). If the new version doesn't "
    "pass its health checks, the updater restores that backup and the previous version "
    "automatically. Anything written to the new version before a failed update is rolled "
    "back is discarded, in-flight jobs and the Redis cache are reset, and calls already "
    "made to outside services (webhooks, model providers) can't be undone. Some failures "
    "still need manual recovery: the updater stops and says so instead of guessing."
)
SETUP_POLICY_OPTIONS = tuple(AUTOMATIC_POLICY_BOUNDS) + (
    'backup_timeout_seconds', 'wait_timeout_seconds', 'pull_timeout_seconds', 'tick_timeout_seconds',
)


def qualify_restart_policies(installation):
    """A2 setup preflight, using the gate-owned layout read-only. No rewrites.

    The full mount/volume/identity qualification remains the observer's job.
    Unknown contract/model/inspection data is unsupported, never a default pass.
    """
    try:
        layout = decode_document((Path(__file__).parent / 'automatic-layout.json').read_bytes())
        require(layout['schema_version'] == 1
                and layout['runtime_activation_contract'] == 'standalone-compose-direct-v1'
                and layout['automatic_profiles'] == [], ReasonCode.UNSUPPORTED_INSTALLATION)
        supported = layout['services']
        model = installation.config()['services']
        require(bool(model) and set(model) <= set(supported), ReasonCode.UNSUPPORTED_INSTALLATION)
        require(all(row['restart'] in ('unless-stopped', 'no') for row in supported.values()),
                ReasonCode.UNSUPPORTED_INSTALLATION)
        require(all(row.get('restart', 'no') in ('unless-stopped', 'no') for row in model.values()),
                ReasonCode.UNSUPPORTED_INSTALLATION)
        containers = installation.containers()
        require(bool(containers), ReasonCode.UNSUPPORTED_INSTALLATION)
        # HostInstallation's public container summaries do not include Docker's
        # HostConfig. Read exact installed IDs through its fixed inspection API.
        for container in containers:
            require(container.get('service') in model and not container.get('oneoff'),
                    ReasonCode.UNSUPPORTED_INSTALLATION)
            result = installation.docker_run('inspect', container['id'], check=False)
            require(result.returncode == 0, ReasonCode.UNSUPPORTED_INSTALLATION)
            records = json.loads(result.stdout)
            require(type(records) is list and len(records) == 1, ReasonCode.UNSUPPORTED_INSTALLATION)
            inspected = records[0]
            require(inspected['Id'] == container['id'] and
                    inspected['HostConfig']['RestartPolicy']['Name'] in ('unless-stopped', 'no'),
                    ReasonCode.UNSUPPORTED_INSTALLATION)
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        require(False, ReasonCode.UNSUPPORTED_INSTALLATION)


def now():
    return datetime.now(timezone.utc)


def _need(condition):
    if not condition:
        raise StoreError(ReasonCode.CONTROL_INVALID)


def _hash(value):
    _need(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)


def _path(value):
    _need(isinstance(value, Path) and value.is_absolute() and '..' not in value.parts
          and len(str(value)) <= 4096 and all(ord(c) >= 32 and ord(c) != 127 for c in str(value)))


def _identity(value):
    return sha(canonical_json(_wire(value)))


@dataclass(frozen=True)
class Setting:
    name: str
    value: str

    def __post_init__(self):
        _need(self.name in DEFAULTS and type(self.value) is str and len(self.value) <= 8192)


@dataclass(frozen=True)
class SetupConfiguration:
    schema_version: int
    selection_hash: str
    settings: Tuple[Setting, ...]
    repository: str
    api_origin: str
    asset_origin: str
    source_identity: str
    public_key_sha256: Optional[str]
    ca_sha256: Optional[str]
    docker_context: str
    backend: str
    scope: str
    cron_service: Optional[str]
    backup_destination: Optional[Path]

    def __post_init__(self):
        _need(type(self.schema_version) is int and self.schema_version == 1)
        _hash(self.selection_hash)
        names = {s.name for s in self.settings}
        added = {'FUTURE_AGI_UPDATE_QUALIFICATION_POLICY'} | {
            'FUTURE_AGI_UPDATE_' + name.upper() for name in AUTOMATIC_POLICY_BOUNDS}
        _need(len(names) == len(self.settings) and set(DEFAULTS) - added <= names <= set(DEFAULTS))
        _need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*', self.repository) is not None)
        for origin in (self.api_origin, self.asset_origin):
            _need(len(origin) <= 2048 and https_url(origin) == origin + '/' and urlsplit(origin).path == '')
        _need(self.source_identity in ('', self.api_origin))
        for digest in (self.public_key_sha256, self.ca_sha256):
            if digest is not None:
                _hash(digest)
        _need(valid_docker_context(self.docker_context) and len(self.docker_context) <= 256)
        _need(self.backend in ('cron', 'systemd') and self.scope in ('system', 'user'))
        _need(self.cron_service is None or self.backend == 'cron' and self.cron_service in CRON_SERVICES)
        if self.backup_destination is not None:
            _path(self.backup_destination)
        policy = self.as_settings().automation_policy
        _need(policy.mode in ('notify', 'download', 'apply') and not policy.enabled)
        _need(policy.mode != 'apply' or (self.backup_destination is not None
              and policy.maintenance_window is not None and policy.probe_timeout_seconds >= 90))

    def as_settings(self):
        # Older immutable enrollments retain policy=None; new defaults cannot
        # silently manufacture the new restore/migration consent.
        return Settings({**DEFAULTS, **{s.name: s.value for s in self.settings}},
                        {s.name: 'one-time setup' for s in self.settings})

    @property
    def policy(self):
        return self.as_settings().automation_policy

    @property
    def scheduler_options(self):
        return dict(backend=self.backend, scope=self.scope, cron_service=self.cron_service)


@dataclass(frozen=True)
class SetupProgress:
    schema_version: int
    configuration_sha256: str
    control_generation: int
    phase: str
    registration_sha256: Optional[str] = None
    initial_head: Optional[str] = None
    storage_helper_image_id: Optional[str] = None

    def __post_init__(self):
        _need(type(self.schema_version) is int and self.schema_version == 1)
        _hash(self.configuration_sha256)
        _need(type(self.control_generation) is int and 0 <= self.control_generation < 2**63)
        _need(self.phase in ('preparing', 'prepared', 'activated', 'removed'))
        for value in (self.registration_sha256, self.initial_head):
            if value is not None:
                _hash(value)
        _need(self.storage_helper_image_id is None or re.fullmatch(r'sha256:[0-9a-f]{64}', self.storage_helper_image_id))
        _need(self.phase == 'preparing' or self.registration_sha256 is not None and self.initial_head is not None)


@dataclass(frozen=True)
class VersionPin:
    name: str
    value: str

    def __post_init__(self):
        _need(self.name in set(COMPONENT_VARIABLES.values()) and is_version(self.value))


@dataclass(frozen=True)
class PinNormalization:
    schema_version: int
    release_sha256: str
    before_sha256: str
    after_sha256: str
    expected_config_fingerprint: str
    operation_id: str
    pins: Tuple[VersionPin, ...]

    def __post_init__(self):
        _need(type(self.schema_version) is int and self.schema_version == 1)
        for value in (self.release_sha256, self.before_sha256, self.after_sha256, self.expected_config_fingerprint):
            _hash(value)
        _need(re.fullmatch(r'[0-9a-f]{32}', self.operation_id) is not None)
        _need(len({p.name for p in self.pins}) == len(self.pins) <= len(COMPONENT_VARIABLES))


@dataclass(frozen=True)
class SetupResult:
    state: str
    scheduler: str
    policy_state: str
    reason: str = ''
    action: str = ''
    next_window: Optional[datetime] = None

    @property
    def code(self):
        # Configured-but-disabled/paused is NOT successful activation. Preserve
        # the operator's control while keeping installer/automation callers honest.
        return 0 if self.state in ('active', 'manual', 'deactivated') else 3


def _read(store, name, kind):
    safe_path(store.checkout, directory=True)
    try:
        with store._directory() as directory:
            return store._read(directory, name, kind)
    except FileNotFoundError:
        return None


def _save(store, name, value, *, expected=None):
    with store._lock('state.lock') as directory:
        old = _read(store, name, type(value))
        if old != expected:
            raise StaleGeneration()
        store._write(directory, name, value)


def _anchor(path):
    path = Path(path)
    safe_path(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_ANCHOR)
        raw = handle.read(MAX_ANCHOR + 1)
        _need(0 < len(raw) <= MAX_ANCHOR)
        return raw


def _retain_anchor(store, name, raw):
    if raw is None:
        return
    with store._lock('state.lock') as directory:
        try:
            old = store._read_raw(directory, name, MAX_ANCHOR)
        except FileNotFoundError:
            store._write_raw(directory, name, raw)
        else:
            _need(old == raw)


def read_setup_configuration(store):
    """Read even a pending configuration. None means legacy/unconfigured only."""
    config = _read(store, CONFIG, SetupConfiguration)
    if config is None:
        return None
    _need(config.selection_hash == store.selection_hash)
    for name, digest in ((KEY, config.public_key_sha256), (CA, config.ca_sha256)):
        if digest is not None:
            with store._directory() as directory:
                _need(sha(store._read_raw(directory, name, MAX_ANCHOR)) == digest)
    values = config.as_settings()
    _need(values.get('FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY') ==
          (str(store.path / KEY) if config.public_key_sha256 else ''))
    return config


def load_setup_configuration(store):
    """Concrete CLI runtime API. Never return a partially activated configuration.

    Controls are separate: disabled/paused configured installs remain readable
    for pending safety recovery, while preventing new forward work. No automatic re-enrollment.
    """
    config = read_setup_configuration(store)
    if config is None:
        return None
    progress = _read(store, PROGRESS, SetupProgress)
    require(progress is not None and progress.configuration_sha256 == _identity(config)
            and progress.phase == 'activated', ReasonCode.INITIALIZATION_REQUIRED)
    require(_identity(store.read_registration()) == progress.registration_sha256, ReasonCode.CONFIGURATION_CHANGED)
    require(store.read_state().baseline_head is not None, ReasonCode.INITIALIZATION_REQUIRED)
    return config


def configured_discovery(store, settings, registration, supervisor, clock):
    """HostRunner factory using once-pinned TLS/source/trust, never ambient env."""
    config = load_setup_configuration(store)
    require(config is not None, ReasonCode.INITIALIZATION_REQUIRED)
    return make_discovery(store, config, registration, supervisor,
                          clock=lambda: clock.sample().wall, monotonic=clock.monotonic)


def make_discovery(store, config, registration, supervisor, *, clock=now, monotonic=time.monotonic):
    verifier = Verifier(config.as_settings(), runner=supervisor, probe=supervisor,
        which=lambda name: shutil.which(name, path=registration.launcher.path),
        environ=supervisor.environment, host_only=True)
    # Fail before any network request; there is no containerized verifier fallback.
    verifier.require_host()
    transport = http_get
    if config.ca_sha256:
        with store._directory() as directory:
            raw = store._read_raw(directory, CA, MAX_ANCHOR)
        _need(sha(raw) == config.ca_sha256)
        context = ssl.create_default_context(cadata=raw.decode('ascii'))
        transport = partial(http_get, ssl_context=context)
    cache = HttpCache(store.path / 'cache', transport=transport,
                      clock=lambda: clock().timestamp(), monotonic=monotonic)
    return ReleaseDiscovery(cache, verifier, repository=config.repository, api_origin=config.api_origin,
        asset_origin=config.asset_origin, source_identity=config.source_identity, monotonic=monotonic)


def configured_capacity(store, installation):
    from .docker_host import observe_daemon_storage
    config = load_setup_configuration(store)
    require(config is not None, ReasonCode.INITIALIZATION_REQUIRED)
    progress = _read(store, PROGRESS, SetupProgress)
    helper = progress.storage_helper_image_id
    baseline = BaselineStore(store)
    raw = baseline.backup_state()
    if raw is not None:
        backup = BackupConfiguration.load(raw)
        helper = decode_document(backup.binding.raw)['images']['app']
    return observe_daemon_storage(installation, store.read_registration(), helper_image_id=helper,
                                  timeout=config.policy.probe_timeout_seconds)


def _source(value):
    canonical = re.fullmatch(r'https://github.com/([^/]+/[^/]+)/releases/latest/download/future-agi-update-manifest.json', value)
    if canonical:
        return canonical[1], 'https://api.github.com', 'https://github.com', ''
    require(https_url(value) == value + '/' and urlsplit(value).path == '', ReasonCode.MANIFEST_UNAVAILABLE)
    return 'future-agi/future-agi', value, value, value


def configuration(store, options, context):
    """Snapshot host-only settings once. CLI values win; process updater env does not."""
    def get(key, default=None):
        return getattr(options, key, default)
    overrides = {'FUTURE_AGI_UPDATE_AUTOMATION_MODE': get('mode'), 'FUTURE_AGI_UPDATE_ENABLED': 'false',
        'FUTURE_AGI_UPDATE_PROBE_TIMEOUT_SECONDS': '90', 'FUTURE_AGI_UPDATE_BACKUP_COMMAND': '',
        'FUTURE_AGI_UPDATE_POLICY': get('policy'), 'FUTURE_AGI_UPDATE_CHANNEL': get('channel'),
        'FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW': get('window', '02:00-06:00' if get('mode') == 'apply' else ''),
        'FUTURE_AGI_UPDATE_TIMEZONE': get('timezone'),
        'FUTURE_AGI_UPDATE_INTERVAL_SECONDS': str(get('check_interval')) if get('check_interval') is not None else None}
    if get('mode') == 'apply':
        overrides['FUTURE_AGI_UPDATE_QUALIFICATION_POLICY'] = QUALIFICATION_POLICY
    for name in SETUP_POLICY_OPTIONS:
        if get(name) is not None:
            overrides['FUTURE_AGI_UPDATE_' + name.upper()] = str(get(name))
    try:
        safe_path(store.checkout / 'update.conf')
    except FileNotFoundError:
        pass
    settings = load_settings(store.checkout, overrides, environ={}, verify=False)
    key_path = get('public_key') or settings.get('FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY')
    if key_path and not Path(key_path).is_absolute():
        key_path = str(store.checkout / key_path)
    key = _anchor(key_path) if key_path else None
    ca = _anchor(get('ca_file')) if get('ca_file') else None
    if ca:
        ssl.create_default_context(cadata=ca.decode('ascii'))  # Validate before saving anything.
    values = {k: settings.get(k) for k in DEFAULTS}
    values['FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY'] = str(store.path / KEY) if key else ''
    if get('public_key'):
        values['FUTURE_AGI_UPDATE_COSIGN_OFFLINE'] = 'true'  # Pinned-key signatures, not an insecure TLS switch.
    if not key and settings.boolean('FUTURE_AGI_UPDATE_COSIGN_OFFLINE'):
        raise SettingsError('Keyless setup requires online verification; configure a host public key or turn offline off.')
    source = get('release_source') or settings.get('FUTURE_AGI_UPDATE_MANIFEST_URL')
    repository, api, assets, identity = _source(source)
    destination = None
    if get('mode') == 'apply':
        destination = Path(get('backup_destination') or (Path.home() / ('.futureagi-backups-' + store.selection_hash[:16])))
        _path(destination)
    elif get('backup_destination') is not None:
        raise SettingsError('--backup-destination is only used by setup --mode apply')
    config = SetupConfiguration(1, store.selection_hash, tuple(Setting(k, values[k]) for k in sorted(values)),
        repository, api, assets, identity, sha(key) if key else None, sha(ca) if ca else None,
        context, get('scheduler', 'cron'), get('scheduler_scope', 'system'), get('cron_service'), destination)
    return config, key, ca


def check_options(config, options):
    """Repeated setup cannot change source/policy/ownership or silently ignore flags."""
    expected = {'mode': config.policy.mode, 'policy': config.policy.version_policy, 'channel': config.policy.channel,
        'timezone': config.policy.timezone, 'check_interval': config.policy.check_interval_seconds,
        'window': config.as_settings().get('FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW'),
        'docker_context': config.docker_context, 'scheduler': config.backend, 'scheduler_scope': config.scope,
        'cron_service': config.cron_service, 'backup_destination': str(config.backup_destination)}
    expected.update({name: getattr(config.policy, name) for name in SETUP_POLICY_OPTIONS})
    for key, value in expected.items():
        if hasattr(options, key):
            require(getattr(options, key) == value, ReasonCode.CONFIGURATION_CHANGED)
    if hasattr(options, 'release_source'):
        require(_source(options.release_source) == (config.repository, config.api_origin, config.asset_origin,
                                                    config.source_identity), ReasonCode.CONFIGURATION_CHANGED)
    for option, digest in (('public_key', config.public_key_sha256), ('ca_file', config.ca_sha256)):
        if hasattr(options, option):
            require(sha(_anchor(getattr(options, option))) == digest, ReasonCode.CONFIGURATION_CHANGED)


def backup_destination(path, installation, policy):
    """Backups may never be sent in a Docker build context or app bind mount."""
    _path(path)
    root = installation.root
    require(path != root and root not in path.parents and path not in root.parents,
            ReasonCode.BACKUP_REQUIRED)
    for service in installation.config().get('services', {}).values():
        build = service.get('build')
        if build:
            context = Path(build if isinstance(build, str) else build['context'])
            context = context if context.is_absolute() else root / context
            require(path != context and context not in path.parents, ReasonCode.BACKUP_REQUIRED)
        for mount in service.get('volumes', []):
            if mount.get('type') == 'bind':
                source = Path(mount['source'])
                require(path != source and source not in path.parents and path not in source.parents,
                        ReasonCode.BACKUP_REQUIRED)
    safe_path(path.parent, directory=True)
    create_destination(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        space = os.fstatvfs(fd)
        require(space.f_bavail * space.f_frsize >= policy.reserve_bytes + 4 * 1024 * 1024, ReasonCode.BACKUP_REQUIRED)
    finally:
        os.close(fd)


def _images(installation):
    services = installation.futureagi_services()
    require(bool(services) and all(not s['buildable'] for s in services), ReasonCode.UNSUPPORTED_INSTALLATION)
    actual = installation.containers()
    rows = []
    for service in services:
        containers = [c for c in actual if c['service'] == service['service'] and not c.get('oneoff')]
        require(len(containers) == 1 and containers[0]['status'] == 'running', ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        image = installation.image(containers[0]['image_id'])
        require(image is not None and image['id'] == containers[0]['image_id'], ReasonCode.IMAGE_DIGEST_MISMATCH)
        rows.append((service, image))
    return rows


def current_release(installation, discovery, channel):
    """Labels select a release to VERIFY, never authorize a version or an alias."""
    hints = set()
    for service, image in _images(installation):
        hint = image.get('version') or parse_image_ref(service['image']).tag
        hint = hint[:-4] if hint and hint.endswith('-gpu') else hint
        require(is_version(hint or ''), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        hints.add(hint)
    require(len(hints) == 1, ReasonCode.RUNNING_VERSIONS_UNKNOWN)
    result = discovery.refresh(next(iter(hints)), channel=channel)
    require(result.complete and result.candidate is not None,
            result.reason_codes[0] if result.reason_codes else ReasonCode.MANIFEST_UNAVAILABLE)
    release = result.candidate
    require(release.verification.verified and release.version in hints, ReasonCode.SIGNATURE_REJECTED)
    match_images(installation, release)
    check_deployment(installation, release.manifest)
    return release


def match_images(installation, release):
    rows = _images(installation)
    for service, image in rows:
        published = release.manifest['components'][service['component']]
        wanted = parse_image_ref(published['image'])
        actual = parse_image_ref(service['image'])
        digests = {parse_image_ref(v).digest for v in image['repo_digests'] if parse_image_ref(v).name == wanted.name}
        require(actual.name == wanted.name and digests == {published['digest']}, ReasonCode.IMAGE_DIGEST_MISMATCH)
    return rows


def normalize_pins(store, installation, release, *, render_config=None):
    """Only approved same-image aliases/pins. Native preserving env commit, no recreate."""
    if render_config is None:
        from .standalone_state import fingerprint_for_env
        render_config = fingerprint_for_env
    old = _read(store, NORMALIZATION, PinNormalization)
    before = _anchor_env(store.env_target)
    if old is not None:
        require(old.release_sha256 == release.manifest_sha256, ReasonCode.CONFIGURATION_CHANGED)
        outcome = envcommit.inspect(installation.env_file, store.env_target, old.operation_id, durable=True)
        if sha(before) == old.after_sha256:
            require(old.before_sha256 == old.after_sha256 or outcome == 'accepted', ReasonCode.ENV_CONFLICT)
            return old.expected_config_fingerprint
        require(sha(before) == old.before_sha256 and outcome == 'absent', ReasonCode.ENV_CONFLICT)
    changes = {}
    for service, image in match_images(installation, release):
        variable = COMPONENT_VARIABLES[service['component']]
        value = installation.env.get(variable, '')
        require(value in ('', 'latest', release.version), ReasonCode.CONFIGURATION_CHANGED)
        approved = release.manifest['components'][service['component']]['image']
        # Never overwrite an unrelated tag, even with explicit setup consent.
        alias = installation.image(approved)
        require(alias is None or alias['id'] == image['id'], ReasonCode.IMAGE_DIGEST_MISMATCH)
        if alias is None:
            require(parse_image_ref(approved).digest is None, ReasonCode.IMAGE_DIGEST_MISMATCH)
            installation.docker_run('image', 'tag', image['id'], approved)
            alias = installation.image(approved)
            require(alias is not None and alias['id'] == image['id'], ReasonCode.IMAGE_DIGEST_MISMATCH)
        if value != release.version:
            changes[variable] = release.version
    after = render_changes(before.decode('utf-8', 'surrogateescape'), changes).encode('utf-8', 'surrogateescape')
    predicted = render_config(installation, after)
    _hash(predicted)
    if old is None:
        old = PinNormalization(1, release.manifest_sha256, sha(before), sha(after), predicted,
                               envcommit.reference()['forward'], tuple(VersionPin(k, v) for k, v in sorted(changes.items())))
        _save(store, NORMALIZATION, old)
    require(old.before_sha256 == sha(before) and old.after_sha256 == sha(after)
            and old.expected_config_fingerprint == predicted
            and {p.name: p.value for p in old.pins} == changes, ReasonCode.ENV_CONFLICT)
    if before != after:
        envcommit.commit(installation.env_file, store.env_target, before, after,
                         store.env_target.stat().st_mode & 0o7777, old.operation_id)
    require(_anchor_env(store.env_target) == after, ReasonCode.ENV_CONFLICT)
    return predicted


def _anchor_env(path):
    safe_path(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and envcommit._known_links(path, info) and info.st_size <= 1024 * 1024, ReasonCode.ENV_CONFLICT)
        raw = handle.read(1024 * 1024 + 1)
        after = os.fstat(handle.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_uid', 'st_nlink', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) <= 1024 * 1024 and all(getattr(info, k) == getattr(after, k) for k in fields),
                ReasonCode.ENV_CONFLICT)
        return raw


class SetupHost:
    """Fixed production adapters. Tests inject only these external boundaries."""
    def context(self, options):
        # The registered launcher intentionally has no alternate Docker config
        # field. Refuse rather than enroll a different daemon/auth namespace.
        require(not os.environ.get('DOCKER_CONFIG'), ReasonCode.OWNERSHIP_UNCONFIRMED)
        selected = getattr(options, 'docker_context', None) or os.environ.get('DOCKER_CONTEXT')
        if selected:
            require(valid_docker_context(selected), ReasonCode.OWNERSHIP_UNCONFIRMED)
            return selected
        # Do not silently replace a DOCKER_HOST-selected daemon with a context.
        require(not os.environ.get('DOCKER_HOST'), ReasonCode.OWNERSHIP_UNCONFIRMED)
        path = os.environ.get('PATH', os.defpath)
        docker = shutil.which('docker', path=path)
        require(docker is not None, ReasonCode.OWNERSHIP_UNCONFIRMED)
        # No forced DOCKER_CONTEXT here: that would make context show always say
        # default rather than read the user's actual configured current context.
        # This fixed local command is bounded and never contacts the daemon.
        try:
            result = subprocess.run([docker, 'context', 'show'], capture_output=True, text=True, timeout=15,
                env={'HOME': str(Path.home()), 'PATH': path}, check=False)
        except (subprocess.SubprocessError, OSError, UnicodeError):
            require(False, ReasonCode.OWNERSHIP_UNCONFIRMED)
        selected = result.stdout.strip()
        require(result.returncode == 0 and valid_docker_context(selected), ReasonCode.OWNERSHIP_UNCONFIRMED)
        return selected

    def open(self, store, config, registration=None):
        if registration is None:
            launcher = LauncherConfig(Path(sys.executable).absolute(), Path.home().resolve(),
                                      os.environ.get('PATH', os.defpath), pwd.getpwuid(os.geteuid()).pw_name)
        else:
            launcher = registration.launcher
        safe_path(launcher.home, directory=True)
        require(os.access(launcher.python, os.X_OK) and os.access(store.checkout / 'bin/update', os.X_OK),
                ReasonCode.SCHEDULER_UNREGISTERED)
        supervisor = ProcessSupervisor(config.policy, docker_context=config.docker_context,
            environment={'HOME': str(launcher.home), 'PATH': launcher.path, 'USER': launcher.user, 'LOGNAME': launcher.user})
        if registration is not None:
            installation = registered_installation(registration.binding, supervisor)
            check_binding(installation, registration.binding)
        else:
            def which(name):
                return shutil.which(name, path=launcher.path)
            docker = which('docker')
            require(docker is not None, ReasonCode.OWNERSHIP_UNCONFIRMED)
            compose = resolve_compose_command(supervisor, which=which)
            if os.path.basename(compose[0]) == 'docker':
                compose = ComposeCommand([docker, '--context', config.docker_context, *compose[1:]], compose.version)
            installation = HostInstallation(store.checkout, env_file=store.env_target, environ=supervisor.environment,
                runner=supervisor, docker=[docker, '--context', config.docker_context], compose=compose)
            binding = installation_binding(installation, store, config.docker_context)
            registration = Registration(1, binding, launcher, now())
        for path in registration.binding.compose_files:
            safe_path(path)
        return installation, registration

    def qualify_layout(self, installation):
        qualify_restart_policies(installation)

    def discovery(self, store, config, registration, installation):
        return make_discovery(store, config, registration, installation.runner)

    def render_config(self, installation, raw):
        from .standalone_state import fingerprint_for_env
        return fingerprint_for_env(installation, raw)

    def observe(self, installation, registration, release, execution, policy):
        from .standalone_state import StandaloneStateObserver
        return StandaloneStateObserver(registration, timeout=policy.probe_timeout_seconds).observe(
            installation, current_release=release, execution_deployment=execution, boundary='enrollment')

    def capacity(self, installation, registration, helper, policy):
        from .docker_host import observe_daemon_storage
        available = observe_daemon_storage(installation, registration, helper_image_id=helper,
                                           timeout=policy.probe_timeout_seconds)
        require(type(available) is int and available >= 64 * 1024 * 1024, ReasonCode.DISK_SPACE_INSUFFICIENT)

    def backup(self, installation, registration, observation, config, *, previous=None):
        policy = BackupPolicy(timeout_seconds=config.policy.backup_timeout_seconds,
                              readiness_seconds=min(600, config.policy.wait_timeout_seconds))
        backup_destination(config.backup_destination, installation, policy)
        images = {}
        for service, replicas in {**observation.facts.product_images, **observation.facts.dependency_images}.items():
            ids = {image.image_id for image in replicas}
            require(len(ids) == 1, ReasonCode.IMAGE_DIGEST_MISMATCH)
            images[service] = next(iter(ids))
        binding = registration.binding
        captured = capture_binding(installation, installation_fingerprint=binding.installation_fingerprint,
            docker_context=binding.docker_context, daemon_id=binding.daemon_identity, project=binding.compose_project,
            profiles=binding.profiles, verified_images=images, deployment_hashes=observation.facts.deployment_files,
            previous=previous)
        return BackupConfiguration(captured, config.backup_destination, policy).raw

    install = staticmethod(install_scheduler)
    inspect = staticmethod(inspect_activation)
    remove = staticmethod(remove_scheduler)


def _revalidate(store, config, progress, host, baseline):
    registration = store.read_registration()
    require(_identity(registration) == progress.registration_sha256, ReasonCode.CONFIGURATION_CHANGED)
    installation, _ = host.open(store, config, registration)
    if config.policy.mode == 'apply':
        host.qualify_layout(installation)
    head = baseline.current()
    # Retained verification is a new bounded operation, not the expired network
    # discovery deadline from before slow state/backup prerequisite probes.
    baseline.verifier.begin_discovery(time.monotonic() + config.policy.probe_timeout_seconds)
    release = baseline.evidence(head.release)
    observation = host.observe(installation, registration, release,
                               decode_document(baseline.blob(head.execution_deployment)), config.policy)
    baseline._observe(observation, release)
    require((observation.facts.config_fingerprint, observation.facts.profile_fingerprint, sha(observation.env_bytes),
             observation.structure_sha256) == (head.config_fingerprint, head.profile_fingerprint, head.env_sha256,
                                                head.structure_sha256), ReasonCode.CONFIGURATION_CHANGED)
    require({'files': dict(observation.facts.deployment_files)} == decode_document(baseline.blob(head.execution_deployment)),
            ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    if config.policy.mode in ('download', 'apply'):
        host.capacity(installation, registration, progress.storage_helper_image_id, config.policy)
    if config.policy.mode == 'apply':
        backup = BackupConfiguration.load(baseline.backup_state(head))
        require(backup.destination == config.backup_destination, ReasonCode.BACKUP_REQUIRED)
        backup_destination(backup.destination, installation, backup.policy)
        require(host.backup(installation, registration, observation, config, previous=backup.binding) == backup.raw,
                ReasonCode.BACKUP_REQUIRED)
    return installation


def _result(store, config, activation, *, reason='', action=''):
    effective = store.read_control().effective(config.policy, now())
    state = 'active' if activation.active and effective == 'enabled' else 'configured'
    if not activation.active or reason:
        state = 'incomplete'
    if state == 'configured' and not action:
        action = 'Existing disable/pause retained; use explicit enable --yes/resume only if intended. Setup did not reactivate it.'
    return SetupResult(state, activation.state, effective, reason or (activation.reason.value if activation.reason else ''),
                       action or activation.action, next_open(config.policy.maintenance_window, now()))


def setup_automation(store, options, *, host=None):
    """Caller already obtained explicit consent. Never invoked by ordinary run."""
    host = host or SetupHost()
    require(getattr(options, 'yes', False) is True, ReasonCode.CONTROL_INVALID)
    safe_path(store.checkout, directory=True)
    safe_path(store.env_target)
    require(sys.platform != 'win32' and store.topology == 'standalone' and not store.production,
            ReasonCode.UNSUPPORTED_INSTALLATION)
    if getattr(options, 'deactivate', False):
        # Disable BEFORE waiting for runner.lock or invoking any OS manager.
        store.change_control('disable', now())
        config = read_setup_configuration(store)
        require(config is not None, ReasonCode.INITIALIZATION_REQUIRED)
        # Fast inspection even when the running recovery owns runner.lock.
        # remove_scheduler repeats this under that lock before touching assets.
        try:
            pending = (store.read_state().active_attempt_id is not None
                       or StateDir(store.checkout).read_journal() is not None)
        except Exception:
            pending = True
        if pending:
            return SetupResult('incomplete', 'retained', 'disabled', 'automatic_recovery_pending',
                'Forward starts are disabled. Keep the safety scheduler and private evidence until recovery is terminal; then repeat deactivation.')
        activation = host.remove(store, config.policy, **config.scheduler_options)
        if activation.state == 'removed':
            with store.runner_lock(), StateDir(store.checkout).lock(automation=True):
                progress = _read(store, PROGRESS, SetupProgress)
                if progress is not None:
                    _save(store, PROGRESS, replace(progress, phase='removed'), expected=progress)
            return SetupResult('deactivated', 'removed', 'disabled', action=activation.action)
        return _result(store, config, activation)
    if getattr(options, 'mode', None) == 'manual':
        store.change_control('disable', now())
        return SetupResult('manual', 'unchanged', 'disabled', action='No scheduler installed. Existing owned schedule, if any, remains disabled.')

    with store.runner_lock(), StateDir(store.checkout).lock(automation=True):
        config = read_setup_configuration(store)
        progress = _read(store, PROGRESS, SetupProgress)
        if config is None:
            require(progress is None, ReasonCode.RECOVERY_REQUIRED)
            control = store.change_control('disable', now())
            config, key, ca = configuration(store, options, host.context(options))
            _retain_anchor(store, KEY, key)
            _retain_anchor(store, CA, ca)
            _save(store, CONFIG, config)
            progress = SetupProgress(1, _identity(config), control.generation, 'preparing')
            _save(store, PROGRESS, progress)
        else:
            check_options(config, options)
            if progress is None:
                # Config durable, progress write interrupted: no scheduler was installed.
                control = store.read_control()
                require(control.enabled_override is False, ReasonCode.CONTROL_INVALID)
                progress = SetupProgress(1, _identity(config), control.generation, 'preparing')
                _save(store, PROGRESS, progress)
        require(progress.configuration_sha256 == _identity(config), ReasonCode.CONFIGURATION_CHANGED)
        if progress.phase == 'removed':
            return SetupResult('incomplete', 'removed', 'disabled', 'scheduler_removed',
                'Explicit deactivation is terminal for this registration. Retained audit records are not reset; enable cannot reinstall it.')
        if progress.phase == 'activated' and store.read_control().generation != progress.control_generation:
            # A repeated setup is inspection, NOT opt-in again. Preserve disable/pause and baseline B/C.
            load_setup_configuration(store)
            return _result(store, config, host.inspect(store, config.policy, **config.scheduler_options))
        # A crash after verified activation but before enable is retryable ONLY
        # if the original disabled control generation remains exactly unchanged.
        require(store.read_control().generation == progress.control_generation
                and store.read_control().enabled_override is False, ReasonCode.CONTROL_INVALID)
        require(StateDir(store.checkout).read_journal() is None, ReasonCode.RECOVERY_REQUIRED)
        try:
            registration = store.read_registration()
        except StoreError as exc:
            if exc.reason_code != ReasonCode.SCHEDULER_UNREGISTERED:
                raise
            registration = None
        installation, registration = host.open(store, config, registration)
        if config.policy.mode == 'apply':
            host.qualify_layout(installation)
        if progress.registration_sha256 is not None:
            require(progress.registration_sha256 == _identity(registration), ReasonCode.CONFIGURATION_CHANGED)
        store.register(registration)
        discovery = host.discovery(store, config, registration, installation)
        baseline = BaselineStore(store, verifier=discovery.verifier)
        if store.read_state().baseline_head is None:
            release = current_release(installation, discovery, config.policy.channel)
            # A missing real probe must refuse BEFORE normalizing even an approved pin.
            host.observe(installation, registration, release, release.manifest['deployment'], config.policy)
            if config.policy.mode == 'apply':
                backup_destination(config.backup_destination, installation, BackupPolicy())
            predicted = normalize_pins(store, installation, release, render_config=host.render_config)
            installation = installation.fresh()
            observation = host.observe(installation, registration, release, release.manifest['deployment'], config.policy)
            require(observation.facts.config_fingerprint == predicted, ReasonCode.CONFIGURATION_CHANGED)
            helper = observation.facts.product_images['app'][0].image_id
            if config.policy.mode in ('download', 'apply'):
                host.capacity(installation, registration, helper, config.policy)
            backup = host.backup(installation, registration, observation, config) if config.policy.mode == 'apply' else None
            baseline.verifier.begin_discovery(time.monotonic() + config.policy.probe_timeout_seconds)
            head = baseline.enroll(release, observation, backup_state=backup)
        else:
            head = baseline.current()  # Crash after enrollment; NEVER enroll over a successor head.
            require(progress.initial_head is None or progress.initial_head == head.identity, ReasonCode.RECOVERY_REQUIRED)
            helper = progress.storage_helper_image_id
            if helper is None:
                release = baseline.evidence(head.release)
                observation = host.observe(installation, registration, release,
                    decode_document(baseline.blob(head.execution_deployment)), config.policy)
                helper = observation.facts.product_images['app'][0].image_id
        prepared = replace(progress, phase='prepared', registration_sha256=_identity(store.read_registration()),
                           initial_head=head.identity, storage_helper_image_id=helper)
        _save(store, PROGRESS, prepared, expected=progress)
        progress = prepared
        _revalidate(store, config, progress, host, baseline)

    # install_scheduler acquires runner.lock. Never call inside either lock above.
    activation = host.install(store, config.policy, **config.scheduler_options)
    if not activation.active:
        return _result(store, config, activation)
    with store.runner_lock(), StateDir(store.checkout).lock(automation=True):
        require(read_setup_configuration(store) == config and _read(store, PROGRESS, SetupProgress) == progress,
                ReasonCode.CONFIGURATION_CHANGED)
        _revalidate(store, config, progress, host, baseline)
        activation = host.inspect(store, config.policy, **config.scheduler_options)
        if not activation.active:
            return _result(store, config, activation)
        # Durable complete metadata BEFORE enable. Crash here remains disabled;
        # a later explicit enable works, but repeat setup never overrides controls.
        _save(store, PROGRESS, replace(progress, phase='activated'), expected=progress)
        try:
            store.change_control('enable', now(), expected_generation=progress.control_generation)
        except StaleGeneration:
            return _result(store, config, activation, reason='control_changed',
                           action='A concurrent disable/pause won. Inspect controls; use explicit enable/resume only if intended.')
        effective = store.read_control().effective(config.policy, now())
        return _result(store, config, activation, reason='' if effective == 'enabled' else 'control_paused',
                       action='' if effective == 'enabled' else 'Existing pause retained; use explicit resume when ready.')


def setup_summary(store, options):
    """Before confirmation; no Docker/network or mutable state required."""
    mode = getattr(options, 'mode', 'unchanged')
    if mode == 'manual':
        return 'Manual mode: disable future work only; keep any existing owned scheduler and private evidence.\n'
    configured = read_setup_configuration(store)
    context = getattr(options, 'docker_context', os.environ.get('DOCKER_CONTEXT') or 'current-context')
    config = configured or configuration(store, options, context)[0]
    if configured:
        check_options(config, options)
    policy = config.policy
    window = config.as_settings().get('FUTURE_AGI_UPDATE_MAINTENANCE_WINDOW') or 'not applicable'
    return ('One-time host setup: mode=' + policy.mode + ', channel=' + policy.channel
        + ', version policy=' + policy.version_policy + '.\n'
        + 'Check cadence: ' + str(policy.check_interval_seconds) + ' seconds; scheduler opportunity every 5 minutes.\n'
        + 'Window: ' + window + ' (' + policy.timezone + ').\n'
        + (AUTOMATIC_UPDATE_DISCLOSURE + '\n' if policy.mode == 'apply' else '')
        + ('This legacy enrollment has no restore-backed authorization. Repeating setup does not rewrite its immutable consent or qualify new automatic applies.\n'
           if policy.mode == 'apply' and policy.qualification_policy is None else '')
        + ('Forward budget: ' + str(policy.required_forward_budget_seconds) + ' seconds; protected recovery budget: '
           + str(policy.required_recovery_budget_seconds) + ' seconds.\n' if policy.mode == 'apply' else '')
        + 'Private backups (retained, never pruned): ' + str(config.backup_destination or 'not used in this mode') + '\n'
        + 'Docker target: ' + (config.docker_context if configured or context != 'current-context' else 'current context (verified during setup)')
        + '; scheduler=' + config.backend + '; owner: current installation user ' + pwd.getpwuid(os.geteuid()).pw_name + '.\n'
        + 'Only owned private config/scheduler and verified same-image version pins/aliases are authorized.\n'
        + 'Setup installs no packages, Docker/global permissions or source code, and runs no migrations itself. Missing prerequisites leave setup incomplete.\n'
        + ('--yes explicitly acknowledges the disclosure above and mandatory backup restoration, including for migration releases.\n'
           if policy.mode == 'apply' else '')
        + 'Disable: bin/update disable. Pause/resume retain the schedule. Teardown: bin/update setup --deactivate --yes.\n')
