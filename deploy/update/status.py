"""Read-only, allowlisted host observations for a later public projection.

This module never recovers, writes, invokes hooks or interprets public status as
host authority. Selected pins and observed image labels are different facts. An
unknown/missing replica makes the running release unknown, not "up to date".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from .contracts import ReasonCode
from .install import COMPONENT_VARIABLES
from .manifest import is_version, parse_version


def _version(value):
    if not isinstance(value, str) or len(value) > 128 or value != value.strip():
        return None
    value = value[:-4] if value.endswith('-gpu') else value
    return value if is_version(value) else None


def _timestamp(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')
        return value if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') == value else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class StatusObservation:
    versions: Dict[str, Any]
    pending_transaction: bool
    last_run: Optional[Dict[str, Any]]
    reason_codes: Tuple[ReasonCode, ...]


def observe_status(installation, state, *, now=None) -> StatusObservation:
    """Collect pure data with real as-of time. No raw error or private correlation."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware observation time required')
    unknown = {'observed_at': None, 'runtime_state': 'unknown', 'selected_release': None,
               'running_release': None, 'components': []}
    reasons = []
    pending = True  # unreadable journal is not absence
    last_run = None
    try:
        pending = state.read_journal() is not None
        if pending:
            reasons.append(ReasonCode.RECOVERY_REQUIRED)
    except Exception:
        reasons.append(ReasonCode.RECOVERY_REQUIRED)
    try:
        fresh = installation.fresh()
        fresh.check_ownership()
        config = fresh.config(refresh=True)
        services = fresh.futureagi_services(config)
        containers = [c for c in fresh.containers() if not c.get('oneoff')]
        components = []
        for component in sorted({s['component'] for s in services} & set(COMPONENT_VARIABLES)):
            names = {s['service'] for s in services if s['component'] == component}
            replicas = [c for c in containers if c['service'] in names]
            versions = set()
            known = bool(replicas) and {c['service'] for c in replicas} == names
            for container in replicas:
                info = fresh.image(container.get('image_id') or '')
                version = _version((info or {}).get('version'))
                if version is None or container.get('status') != 'running':
                    known = False
                else:
                    versions.add(version)
            selected = {_version(s['ref'].tag) for s in services if s['component'] == component}
            components.append({'component': component,
                               'selected_version': next(iter(selected)) if len(selected) == 1 else None,
                               'observed_versions': sorted(versions, key=parse_version) if known else [],
                               'observation': 'known' if known else 'unknown'})
        known = bool(components) and all(c['observation'] == 'known' for c in components)
        running = {v for c in components for v in c['observed_versions']}
        selected = {c['selected_version'] for c in components}
        runtime = 'consistent' if known and len(running) == 1 else 'mixed' if known else 'unknown'
        unknown = {'observed_at': now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'runtime_state': runtime,
                   'selected_release': next(iter(selected)) if len(selected) == 1 and None not in selected else None,
                   'running_release': next(iter(running)) if runtime == 'consistent' else None,
                   'components': components}
        if runtime != 'consistent':
            reasons.append(ReasonCode.MIXED_RUNNING_VERSIONS if runtime == 'mixed' else ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        receipt = state.latest_receipt(fresh.identity())
        automatic, fallback = False, 'none'
        if receipt and receipt.get('run_id'):
            private = state.read_private('automation', receipt['run_id'])
            automatic = bool(private and private.get('run_id') == receipt['run_id'])
            if automatic and private.get('forward_run_id'):
                forwards = [item for item in state.history(fresh.identity())
                            if item.get('run_id') == private['forward_run_id'] and item.get('kind') == 'apply'
                            and item.get('status') == 'failed_during_rollout']
                if len(forwards) == 1:
                    fallback = 'succeeded' if receipt.get('status') == 'rolled_back' else 'failed'
                    receipt = forwards[0]  # Preserve forward failure after a successful safety fallback.
        statuses = ('succeeded', 'staged', 'rolled_back', 'failed_before_rollout', 'failed_during_rollout',
                    'interrupted_before_rollout', 'interrupted_during_rollout', 'blocked')
        if receipt and receipt.get('kind') in ('apply', 'rollback') and receipt.get('status') in statuses:
            source = receipt.get('from_version')
            if source is None and len(receipt.get('from_versions') or []) == 1:
                source = receipt['from_versions'][0]
            last_run = {'kind': receipt['kind'], 'automatic': automatic, 'outcome': receipt['status'],
                        'from_version': _version(source), 'target_version': _version(receipt.get('target_version')),
                        'finished_at': _timestamp(receipt.get('finished_at')), 'fallback_outcome': fallback}
    except Exception:
        # Exception strings can contain Docker/env output, paths and secrets.
        # Partial data is not a successful refreshed version observation.
        reasons.append(ReasonCode.RUNNING_VERSIONS_UNKNOWN)
        unknown = {'observed_at': None, 'runtime_state': 'unknown', 'selected_release': None,
                   'running_release': None, 'components': []}
        last_run = None
    return StatusObservation(unknown, pending, last_run, tuple(dict.fromkeys(reasons)))
