"""Reproduce producer -> backend-reader fixtures for frontend regressions.

Component fixtures only, not a real update or release qualification. Run with
Python -I; --check compares the committed fixture without rewriting it.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path[:0] = [str(ROOT), str(ROOT / 'futureagi')]

from deploy.tests.test_update_compatibility import NOW, default_v2, restore_context
from deploy.tests.test_update_public_status import PublisherCase
from deploy.update.contracts import AutomaticUpdateDecision, ReasonCode, RestorationResult
from deploy.update.discovery import DiscoveryResult
from deploy.update.public_status import restoration_projection, stamp
from tfc.host_updates.status import available_response, decode_public_status


def fixtures():
    case = PublisherCase()
    case.setUp()
    try:
        case.policy = replace(case.policy, mode='apply', qualification_policy='standalone-cold-restore-v1')
        prepared, observation, _, _ = restore_context()
        result = RestorationResult(observation.attempt_id, 'restored',
            observation.runtime_observation.release_version, prepared.manifest['version'],
            'restored', observation.backup_seal, observation.runtime_observation)
        previous = case.snapshot(**restoration_projection(result, now=NOW, started_at=NOW))
        candidate = replace(case.release, raw_manifest=json.dumps(default_v2('v1.53.0')).encode())
        discovery = DiscoveryResult(candidate, checked_at=NOW)
        rows = []
        for reason in (ReasonCode.RESTORE_NOT_CERTIFIED, ReasonCode.TRANSITION_NOT_REHEARSED,
                       ReasonCode.MIGRATION_EXCEEDS_WINDOW):
            operation = dict(state='blocked', kind='check', stage=None, started_at=stamp(NOW),
                finished_at=stamp(NOW), reported_at=stamp(NOW), reason_codes=[reason.value])
            decision = (AutomaticUpdateDecision(False, (reason,), None)
                        if reason == ReasonCode.MIGRATION_EXCEEDS_WINDOW else None)
            snapshot = case.snapshot(previous=previous, discovery=discovery, operation=operation,
                                     reason_codes=(reason,), automatic_decision=decision)
            assert snapshot['discovery']['state'] == 'blocked'
            assert snapshot['discovery']['reason_codes'] == [reason.value]
            assert snapshot['operation']['state'] == 'blocked'
            assert snapshot['operation']['reason_codes'] == [reason.value]
            rows.append((reason.value, snapshot))
        operation = dict(state='verifying', kind='apply', stage='stability', phase='target_verifying',
            started_at=stamp(NOW), finished_at=None, reported_at=stamp(NOW), reason_codes=[])
        rows.append(('verifying', case.snapshot(previous=previous, discovery=discovery, operation=operation)))
        output = []
        for name, snapshot in rows:
            assert snapshot['last_run']['restore_result'] == 'verified'
            assert snapshot['last_run']['target_version'] != snapshot['discovery']['candidate_version']
            # Exercise the shipping reader, not a handwritten equivalent of its
            # cross-field rules, before committing data consumed by UI tests.
            decoded = decode_public_status(json.dumps(snapshot).encode(),
                expected_installation=snapshot['installation'], now=NOW)
            assert decoded == snapshot
            output.append(dict(name=name, response=available_response(decoded, now=NOW)))
        return output
    finally:
        case.doCleanups()


if __name__ == '__main__':
    path = Path(__file__).with_name('projected-status-fixtures.json')
    rendered = json.dumps(fixtures(), indent=2) + '\n'
    if '--check' in sys.argv:
        if path.read_text() != rendered:
            raise SystemExit('Producer/reader fixtures are stale; rerun project-status-fixtures.py')
    else:
        path.write_text(rendered)
    print('4 producer -> shipping backend-reader fixtures verified (not product qualification)')
