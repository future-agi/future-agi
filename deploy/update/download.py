"""Verified product-image cache, entirely separate from apply --stage.

No backup hook, env write/journal, compose up/run/build or initialization. Only
active product images are guaranteed; inactive profiles and third-party images
are explicitly excluded. All pulls run under update.lock on a fresh in-memory
pin overlay. A receipt is identity evidence, never evidence against eviction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .apply import _digest_set
from .compatibility import plain
from .contracts import PreparedUpdate, ReasonCode
from .install import COMPONENT_VARIABLES
from .manifest import manifest_hash, parse_image_ref
from .orchestrator import GuardRefused, check_binding, check_deployment, fingerprints, require

MIN_FREE_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class DownloadImage:
    component: str
    image: str
    digest: str
    image_id: str


@dataclass(frozen=True)
class DownloadReceipt:
    schema_version: int
    identity: str
    installation_fingerprint: str
    config_fingerprint: str
    profile_fingerprint: str
    trust_fingerprint: str
    manifest_hash: str
    manifest_sha256: str
    bundle_sha256: str
    completed_at: str
    images: Tuple[DownloadImage, ...]
    skipped_inactive: Tuple[str, ...]


def identity(prepared):
    return manifest_hash({
        'binding': asdict(prepared.binding) | {'checkout': str(prepared.binding.checkout),
            'env_alias': str(prepared.binding.env_alias), 'env_target': str(prepared.binding.env_target),
            'compose_files': [str(p) for p in prepared.binding.compose_files]},
        'config': prepared.config_fingerprint, 'profiles': prepared.profile_fingerprint,
        'deployment': prepared.deployment_fingerprint, 'trust': prepared.trust_fingerprint,
        'manifest': prepared.manifest_sha256, 'bundle': prepared.bundle_sha256,
        'canonical': manifest_hash(plain(prepared.manifest)),
    })


class Downloader:
    def __init__(self, installation, state, *, space_available: Optional[Callable[[], int]] = None,
                 clock=lambda: datetime.now(timezone.utc), deployment_authorization=None):
        self.deployment_authorization = deployment_authorization
        self.installation, self.state = installation, state
        # Must observe capacity on the REGISTERED daemon's image filesystem,
        # not shutil.disk_usage(checkout) on the client host/another VM.
        # Unknown capacity fails closed. The floor is a preflight, not a promise
        # that all images fit; a full-disk pull failure still changes caches only.
        self.space_available = space_available
        self.clock = clock

    def _fresh(self, prepared):
        require(prepared.verification.verified, ReasonCode.SIGNATURE_REJECTED)
        require(self.state.read_journal() is None, ReasonCode.RECOVERY_REQUIRED)
        fresh = self.installation.fresh()
        check_binding(fresh, prepared.binding)
        check_deployment(fresh, prepared.manifest, self.deployment_authorization, prepared.raw_manifest)
        require(fingerprints(fresh) == (prepared.config_fingerprint, prepared.profile_fingerprint,
                                       prepared.deployment_fingerprint), ReasonCode.CONFIGURATION_CHANGED)
        require(not prepared.plan.get('deployment_drift') and not prepared.plan.get('missing_env_keys')
                and not prepared.plan.get('resume') and not prepared.plan.get('reasons')
                and prepared.plan.get('decision') in ('update_available', 'up_to_date'),
                ReasonCode.CONFIGURATION_CHANGED)
        model = fresh.service_model(refresh=True)
        require(model.get('complete') is True, ReasonCode.UNSUPPORTED_INSTALLATION)
        return fresh

    def _targets(self, prepared, fresh):
        pins = {}
        components = plain(prepared.manifest)['components']
        active = {}
        skipped = []
        for item in plain(prepared.plan)['components']:
            name = item['component']
            require(item['variable'] == COMPONENT_VARIABLES.get(name), ReasonCode.CONFIGURATION_CHANGED)
            signed = components.get(name)
            require(signed is not None and item['target_digest'] == signed['digest']
                    and parse_image_ref(item['target_image']) == parse_image_ref(signed['image']),
                    ReasonCode.IMAGE_DIGEST_MISMATCH)
            if item.get('inactive') or not item.get('services'):
                skipped.append(name)
                continue
            require(item['variable'] not in pins or pins[item['variable']] == item['target_tag'])
            pins[item['variable']] = item['target_tag']
            active[name] = item
        require(bool(active), ReasonCode.UNSUPPORTED_INSTALLATION)
        fresh.overlay_env(pins)
        services = fresh.futureagi_services(fresh.config(refresh=True))
        expected = {service for item in active.values() for service in item['services']}
        require({service['service'] for service in services} == expected, ReasonCode.CONFIGURATION_CHANGED)
        for service in services:
            item = active.get(service['component'])
            require(item is not None and service['service'] in item['services']
                    and parse_image_ref(service['image']) == parse_image_ref(item['target_image']),
                    ReasonCode.CONFIGURATION_CHANGED)
        return active, services, tuple(sorted(set(skipped)))

    @staticmethod
    def _verified(item, info):
        return (info is not None and bool(info.get('id')) and
                (parse_image_ref(item['target_image']).name, item['target_digest'])
                in _digest_set(info.get('repo_digests') or []))

    def download(self, prepared: PreparedUpdate) -> DownloadReceipt:
        from .processes import ProcessError
        from .receipt import StateBusy
        try:
            return self._download(prepared)
        except (GuardRefused, ProcessError, StateBusy):
            raise
        except Exception:
            raise GuardRefused(ReasonCode.PROCESS_FAILED) from None

    def _download(self, prepared: PreparedUpdate) -> DownloadReceipt:
        with self.state.lock(automation=True):
            fresh = self._fresh(prepared)
            before = self.installation.env_file.read_bytes()
            active, services, skipped = self._targets(prepared, fresh)
            missing = [s['service'] for s in services
                       if not self._verified(active[s['component']], fresh.image(s['image']))]
            if missing:
                capacity = self.space_available() if self.space_available is not None else None
                require(type(capacity) is int and capacity >= MIN_FREE_BYTES, ReasonCode.DISK_SPACE_INSUFFICIENT)
                # Production is allowed here: this command pulls ONLY. The
                # supervised Installation runner bounds it with pull_timeout.
                fresh.compose_run('pull', *sorted(set(missing)))
            images = []
            for name, item in sorted(active.items()):
                info = fresh.image(item['target_image'])
                require(self._verified(item, info), ReasonCode.IMAGE_DIGEST_MISMATCH)
                if info.get('revision'):
                    require(info['revision'] == prepared.manifest['source_sha'], ReasonCode.IMAGE_DIGEST_MISMATCH)
                images.append(DownloadImage(name, item['target_image'], item['target_digest'], info['id']))
            require(self.installation.env_file.read_bytes() == before, ReasonCode.CONFIGURATION_CHANGED)
            # Recheck the live selection after slow pulls, never the overlay.
            self._fresh(prepared)
            completed = self.clock()
            require(completed.tzinfo is not None and completed.utcoffset() is not None, ReasonCode.CLOCK_SKEW)
            receipt = DownloadReceipt(1, identity(prepared), prepared.binding.installation_fingerprint,
                prepared.config_fingerprint, prepared.profile_fingerprint, prepared.trust_fingerprint,
                manifest_hash(plain(prepared.manifest)), prepared.manifest_sha256, prepared.bundle_sha256,
                completed.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), tuple(images), skipped)
            self.state.write_private('download', receipt.identity, asdict(receipt))
            return receipt

    def load(self, prepared) -> Optional[DownloadReceipt]:
        value = self.state.read_private('download', identity(prepared))
        if value is None:
            return None
        try:
            value['images'] = tuple(DownloadImage(**item) for item in value['images'])
            value['skipped_inactive'] = tuple(value['skipped_inactive'])
            receipt = DownloadReceipt(**value)
            require(receipt.schema_version == 1 and receipt.identity == identity(prepared), ReasonCode.CACHE_INVALID)
            return receipt
        except (ValueError, TypeError, KeyError):
            raise GuardRefused(ReasonCode.CACHE_INVALID) from None

    def reusable(self, prepared, receipt):
        """Read-only hint, not apply authorization. Updater always reinspects again."""
        if receipt.schema_version != 1 or receipt.identity != identity(prepared):
            return False
        expected = {name: item for name, item in plain(prepared.manifest)['components'].items()}
        for image in receipt.images:
            signed = expected.get(image.component)
            if signed is None or signed['digest'] != image.digest or signed['image'] != image.image:
                return False
            info = self.installation.image(image.image)
            if (info is None or info['id'] != image.image_id or
                    (parse_image_ref(image.image).name, image.digest) not in _digest_set(info.get('repo_digests') or [])):
                return False
        wanted = {i['component'] for i in prepared.plan['components'] if not i.get('inactive') and i.get('services')}
        return bool(wanted) and {image.component for image in receipt.images} == wanted
