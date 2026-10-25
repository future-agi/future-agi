"""Read-only host adapters. Enrollment is identity, NEVER state approval.

An operator-supplied, private facts.json attestation and exact release evidence
are required for stateful automation. Missing evidence is a refusal. Capacity
is observable only for a directly connected native Linux dockerd; remote/VM
contexts intentionally return unknown, without SSH, containers or client-disk
substitution.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import struct
import sys
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .compatibility import Observations, RuntimeImage, decode_document, topology_profile
from .contracts import InstallationBinding, ReasonCode, VerificationSnapshot
from .install import Installation, DISTRIBUTED_FILE, PRODUCTION_ENV, PRODUCTION_OVERLAY
from .manifest import ManifestError, hash_path, manifest_hash, parse_image_ref, read_deployment_file_list, validate_manifest
from .orchestrator import GuardRefused, check_binding, check_deployment, fingerprints, profiles, require
from .runner_state import RunnerStateStore, StoreError
from .trust import FetchedManifest, Verifier


def now():
    return datetime.now(timezone.utc)


def safe_path(path, *, directory=False):
    """Reject alias ancestry and writable/foreign objects; never repair them."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, ReasonCode.OWNERSHIP_UNCONFIRMED)
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, os.geteuid())
                and not info.st_mode & 0o022, ReasonCode.OWNERSHIP_UNCONFIRMED)
        if current != path or directory:
            require(stat.S_ISDIR(info.st_mode), ReasonCode.OWNERSHIP_UNCONFIRMED)
    return path


def selected_store(root, *, production=False, env_file=None):
    """Local selection only: no Installation constructor, tools or daemon."""
    env = Path(env_file or root / (PRODUCTION_ENV if production else '.env'))
    if not production:
        require(env.resolve() == (root / '.env').resolve(), ReasonCode.CONFIGURATION_CHANGED)
    mode = 'distributed' if production else Installation._detect_mode(
        SimpleNamespace(env_file=env, environ={}))
    return RunnerStateStore(root, env, production, mode)


STATUS_OVERLAY = 'deploy/docker-compose.host-status.distributed.yml'


class HostInstallation(Installation):
    """CLI-only remedy; never infer an overlay from untrusted provenance."""
    def _ownership_error(self, detail):
        error = super()._ownership_error(detail)
        if self.production and self.status_overlay is None and 'config_files' in detail:
            return type(error)(str(error) + ' If this production install uses the host-status overlay, explicitly '
                'enroll with `bin/update --production schedule init --docker-context NAME --status-overlay` '
                '(keep the same --env-file selection). See the README for re-enrollment; labels are never adopted.')
        return error


def production_status_overlay(root):
    """One fixed inventory member, not a path supplied by flags or labels.

    Enrollment is identity, not signature verification. Release operations also
    require this entry in the verified manifest and check its bytes below.
    """
    try:
        require(STATUS_OVERLAY in read_deployment_file_list(root), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        path = safe_path(root / STATUS_OVERLAY)
        require(path.is_file(), ReasonCode.DEPLOYMENT_FILES_MISMATCH)
        return path
    except (OSError, ManifestError):
        raise GuardRefused(ReasonCode.DEPLOYMENT_FILES_MISMATCH) from None


def _binding_fingerprint(binding):
    return manifest_hash({'checkout': str(binding.checkout), 'env_alias': str(binding.env_alias),
        'env_target': str(binding.env_target), 'production': binding.production, 'topology': binding.topology,
        'project': binding.compose_project, 'files': [str(p) for p in binding.compose_files],
        'profiles': binding.profiles, 'context': binding.docker_context,
        'daemon': binding.daemon_identity, 'uid': binding.host_uid})


def binding_status_overlay(binding):
    """Decode only the two fixed production identities; COMPOSE_FILE is unchanged."""
    if not binding.production:
        return None
    base = (binding.checkout / DISTRIBUTED_FILE, binding.checkout / PRODUCTION_OVERLAY)
    overlay = binding.checkout / STATUS_OVERLAY
    require(binding.compose_files in (base, (*base, overlay))
            and binding.installation_fingerprint == _binding_fingerprint(binding), ReasonCode.CONFIGURATION_CHANGED)
    return production_status_overlay(binding.checkout) if len(binding.compose_files) == 3 else None


def registered_status_overlay(store):
    if not store.production:
        return None
    # A legacy core journal directory is not automation enrollment. Do not let
    # its older permissions hide the Installation's original preflight remedy.
    try:
        store.path.lstat()
    except FileNotFoundError:
        return None
    try:
        return binding_status_overlay(store.read_registration().binding)
    except StoreError as exc:
        if exc.reason_code != ReasonCode.SCHEDULER_UNREGISTERED:
            raise
        return None


def check_status_deployment(installation, manifest):
    """Do not let an older signed manifest silently omit the selected overlay."""
    require(STATUS_OVERLAY in manifest['deployment']['files'], ReasonCode.DEPLOYMENT_FILES_MISMATCH)
    check_deployment(installation, manifest)


def overlay_discovery(store, settings, registration, supervisor, clock):
    """Qualify signed overlay bytes even for notify, without changing the runner."""
    from .runner import host_discovery
    discovery = host_discovery(store, settings, registration, supervisor, clock)

    class CheckedDiscovery:
        def checked(self, result):
            if result.candidate is not None and result.candidate.verification.verified:
                check_status_deployment(SimpleNamespace(root=store.checkout), result.candidate.manifest)
            return result

        def discover(self, **kwargs):
            return self.checked(discovery.discover(**kwargs))

        def refresh(self, *args, **kwargs):
            return self.checked(discovery.refresh(*args, **kwargs))

    return CheckedDiscovery()


def installation_binding(installation, store, context):
    """Fresh read-only ownership and daemon identity; deterministic re-init."""
    require(context and all(ord(c) >= 33 and ord(c) != 127 for c in context), ReasonCode.OWNERSHIP_UNCONFIRMED)
    installation.check_ownership()
    daemon = installation.docker_run('info', '--format', '{{.ID}}').stdout.strip()
    require(bool(daemon), ReasonCode.OWNERSHIP_UNCONFIRMED)
    alias = Path(getattr(installation, 'env_alias', installation.env_file)).absolute()
    files = tuple(installation._ownership_files())
    selected_profiles = profiles(installation)
    try:
        slot = store.read_registration().binding.slot_id
    except StoreError as exc:
        if exc.reason_code != ReasonCode.SCHEDULER_UNREGISTERED:
            raise
        slot = uuid.uuid4().hex  # Public correlation, never a private path hash.
    binding = InstallationBinding(slot, store.selection_hash, '0' * 64, store.checkout,
                                  alias, store.env_target, store.topology, store.production, installation.project,
                                  files, selected_profiles, context, daemon, os.geteuid())
    return replace(binding, installation_fingerprint=_binding_fingerprint(binding))


def daemon_space_available(installation, binding):
    """Free bytes of registered dockerd's root, or None (fail closed).

    Docker's system-df reports image usage, NOT filesystem capacity. A Unix
    endpoint alone does not prove locality: inspect the connected peer PID and
    require native dockerd in this mount namespace before consulting statvfs.
    No network connection except the explicitly selected local daemon socket.
    """
    if sys.platform != 'linux':
        return None
    try:
        context = installation.environ['DOCKER_CONTEXT']
        require(context == binding.docker_context, ReasonCode.OWNERSHIP_UNCONFIRMED)
        result = installation.docker_run('context', 'inspect', context)
        entries = json.loads(result.stdout)
        require(len(entries) == 1 and entries[0]['Name'] == context)
        endpoint = entries[0]['Endpoints']['docker']['Host']
        require(endpoint.startswith('unix:///'))
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(5)
            peer.connect(endpoint[7:])
            pid, _, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            proc = Path('/proc') / str(pid)
            require(pid > 0 and (proc / 'comm').read_text().strip() == 'dockerd')
            require(os.readlink(proc / 'ns/mnt') == os.readlink('/proc/self/ns/mnt'))
            info = json.loads(installation.docker_run('info', '--format', '{{json .}}').stdout)
            root = Path(info['DockerRootDir'])
            require(root.is_absolute() and '..' not in root.parts and info.get('ID') == binding.daemon_identity)
            # Same namespace/root, not a proxy or a container with a different root.
            require(os.stat(proc / 'root') == os.stat('/'))
            # The containerd snapshotter, devicemapper and remote plugins can
            # store image bytes elsewhere. Qualify only classic overlay2 here.
            require(info.get('Driver') == 'overlay2'
                    and not any('containerd' in str(row) for row in info.get('DriverStatus', [])))
            values = os.statvfs(safe_path(root / 'overlay2', directory=True))
            return values.f_bavail * values.f_frsize
    except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError):
        return None


def _private_bytes(directory, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and info.st_size <= limit, ReasonCode.INITIALIZATION_REQUIRED)
        with os.fdopen(os.dup(fd), 'rb') as handle:
            raw = handle.read(limit + 1)
        require(len(raw) <= limit, ReasonCode.INITIALIZATION_REQUIRED)
        return raw
    finally:
        os.close(fd)


class EnrolledFacts:
    """Explicit state attestation plus fresh images/config, never inferred consent.

    The closed facts.json document binds the operator's unchanged-state claim to
    this exact config/profile/deployment and predecessor/proof bytes. It is NOT
    created by schedule init. The guard independently verifies the target online
    and checks proof certification at every boundary. Every service/replica,
    including completed initialization jobs, must be accounted for.
    """
    def __init__(self, store, *, clock=now, verifier_factory=Verifier):
        self.store, self.clock, self.verifier_factory = store, clock, verifier_factory

    def __call__(self, installation, boundary):
        from .orchestrator import GuardRefused
        try:
            return self._observe(installation, boundary)
        except GuardRefused:
            raise
        except (OSError, ValueError, TypeError, KeyError, StoreError) as exc:
            raise GuardRefused(getattr(exc, 'reason_code', ReasonCode.INITIALIZATION_REQUIRED)) from None

    def _observe(self, installation, boundary):
        from .settings import load_settings
        binding = self.store.read_registration().binding
        check_binding(installation, binding)
        with self.store._directory() as directory:
            document = decode_document(_private_bytes(directory, 'facts.json', 65536))
            keys = {'schema_version', 'installation_fingerprint', 'config_fingerprint', 'profile_fingerprint',
                    'deployment_fingerprint', 'predecessor_sha256', 'bundle_sha256', 'proof_sha256',
                    'state_epochs', 'persistence', 'unchanged_state_attested', 'no_pending_migrations_attested'}
            require(set(document) == keys and type(document['schema_version']) is int
                    and document['schema_version'] == 1, ReasonCode.INITIALIZATION_REQUIRED)
            require(document['unchanged_state_attested'] is True
                    and document['no_pending_migrations_attested'] is True, ReasonCode.INITIALIZATION_REQUIRED)
            raw = _private_bytes(directory, 'predecessor.json', 1024 * 1024)
            bundle = _private_bytes(directory, 'predecessor.bundle', 1024 * 1024)
            proof = _private_bytes(directory, 'compatibility.json', 256 * 1024)
        for name, value in (('predecessor_sha256', raw), ('bundle_sha256', bundle), ('proof_sha256', proof)):
            require(document[name] == hashlib.sha256(value).hexdigest(), ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        previous = validate_manifest(decode_document(raw, 1024 * 1024))
        config, profile, deployment = fingerprints(installation)
        require((document['installation_fingerprint'], document['config_fingerprint'],
                 document['profile_fingerprint'], document['deployment_fingerprint']) ==
                (binding.installation_fingerprint, config, profile, deployment), ReasonCode.CONFIGURATION_CHANGED)
        settings = load_settings(self.store.checkout, environ={})
        import shutil
        launcher = self.store.read_registration().launcher
        verifier = self.verifier_factory(settings, runner=installation.runner, probe=installation.runner,
            environ=installation.environ, which=lambda name: shutil.which(name, path=launcher.path), host_only=True)
        fetched = FetchedManifest(str(self.store.path / 'predecessor.json'), self.store.path,
                                  self.store.path / 'predecessor.json', raw, previous,
                                  bundle_path=self.store.path / 'predecessor.bundle')
        verified = verifier.verify(fetched)
        require(verified.verified, ReasonCode.SIGNATURE_REJECTED)
        # Never accept a file changed while the verifier was reading it.
        with self.store._directory() as directory:
            require(_private_bytes(directory, 'predecessor.json', 1024 * 1024) == raw
                    and _private_bytes(directory, 'predecessor.bundle', 1024 * 1024) == bundle,
                    ReasonCode.IMMUTABLE_RELEASE_CHANGED)
        layout = topology_profile(binding.topology, binding.profiles)
        require(type(document['state_epochs']) is dict and set(document['state_epochs']) == set(layout.domains)
                and all(type(v) is int and v >= 0 for v in document['state_epochs'].values()),
                ReasonCode.INITIALIZATION_REQUIRED)
        require(document['persistence'] == dict(layout.persistence)
                and all(type(v) is bool for v in document['persistence'].values()), ReasonCode.UNSUPPORTED_INSTALLATION)
        model = installation.service_model(refresh=True)
        services = installation.config(refresh=True).get('services', {})
        wanted = {s for s, _ in layout.products} | set(layout.dependencies)
        require(model.get('complete') is True and set(services) == wanted
                and all(not s.get('build') for s in services.values()), ReasonCode.UNSUPPORTED_INSTALLATION)
        images = {s: [] for s in wanted}
        for container in installation.containers():
            service = container['service']
            require(service in wanted and (container['status'] == 'running' or
                    container['status'] == 'exited' and container.get('exit_code') == 0
                    and service.endswith(('-init', '-bootstrap'))), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
            info = installation.image(container['image_id'])
            require(info is not None and info['id'] == container['image_id'], ReasonCode.IMAGE_DIGEST_MISMATCH)
            ref = parse_image_ref(container['image'])
            digests = {parse_image_ref(d).digest for d in info['repo_digests'] if parse_image_ref(d).name == ref.name}
            require(len(digests) == 1 and None not in digests, ReasonCode.IMAGE_DIGEST_MISMATCH)
            images[service].append(RuntimeImage(container['image'], next(iter(digests)), info['id']))
        require(all(images.values()), ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        observed = json.loads(installation.docker_run('info', '--format', '{{json .}}').stdout)
        architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(observed['Architecture'], observed['Architecture'])
        platform = observed['OSType'] + '/' + architecture
        return Observations(self.clock(), predecessor_raw=raw,
            predecessor_verification=VerificationSnapshot(verified.verified, verified.method, verified.signer,
                                                          verified.issuer, verified.tool, self.clock()),
            release_proof=proof, platform=platform,
            product_images={s: tuple(images[s]) for s, _ in layout.products},
            dependency_images={s: tuple(images[s]) for s in layout.dependencies},
            state_epochs=document['state_epochs'], persistence=document['persistence'],
            deployment_files={name: hash_path(installation.root, name) for name in previous['deployment']['files']},
            env=dict(installation.env), installation_fingerprint=binding.installation_fingerprint,
            config_fingerprint=config, profile_fingerprint=profile, trust_fingerprint=verifier.trust_fingerprint(),
            boundary=boundary, owner_confirmed=True, topology_qualified=True, runtime_complete=True,
            baseline_attested=True, state_unchanged=True, graph_unchanged=True, env_unchanged=True,
            no_source_build=True, no_pending_migrations=True)
