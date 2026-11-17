import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import type { ExportSyncRecord } from '../_shared/postgres-exports';
import { assertExactExportIds, assertRecoveryHistory, type RecoveryCounts } from './export-state';
import { fragmentAnnotation } from './fragments';

// DATA-ONLY ORACLE TESTS, not a product walk or live negative/restored control.
// They need no browser, actor, stack, endpoint, credential, seed write or runner
// fault hook. Distinct tags/titles prevent selection(packet='J15') from silently
// counting them as GW-JOURNEY row coverage. All W17 packet rows remain OWED.
// Negative fixtures are private copies; unchanged detectors run again restored.
// No test.fail(), skip, catch-any-error or successful placeholder callback.

for (const defect of ['missing-current', 'old-record', 'foreign-record', 'extra-repeat'] as const) {
  test(`J15-DETECTOR W17.R2 ${defect}: exact export multiplicities`, {
    tag: ['@journey-J15-oracle'],
    annotation: [fragmentAnnotation('W17.A03', `W17.R2/${defect}`, true), {
      type: 'journey-oracle-control', description: JSON.stringify({ control: 'W17.R2',
        row: 'W17.A03', defect, liveControl: false, walkedStatus: 'NOT_WALKED' }),
    }],
  }, async ({}, info) => {
    const ids = Array.from({ length: 5 }, () => randomUUID());
    const current = ids.slice(0, 3);
    // Two successful windows overlap fully here. Exactly six deliveries, not
    // three unique IDs, per activities.py:_sync_export_integration's repeat rule.
    const windows = [[...current], [...current]];
    const good = windows.flat();
    const bad = defect === 'missing-current' ? good.slice(1)
      : defect === 'old-record' ? [...good, ids[3]]
      : defect === 'foreign-record' ? [...good, ids[4]]
      : [...good, current[0]];
    await attachSeededIds(info, 'J15', { requestIds: ids });
    await test.step('oracle baseline: full multiset including valid repeats', async () => {
      assertExactExportIds(good, windows);
    });
    await test.step(`oracle negative: ${defect} kills W17.A03 detector only`, async () => {
      expect(() => assertExactExportIds(bad, windows)).toThrow('W17.A03 EXACT_EXPORT_ID_MULTISET');
    });
    await test.step('oracle restored: unchanged detector, original observation', async () => {
      assertExactExportIds(good, windows);
    });
  });
}

function history(connectionId: string): ExportSyncRecord[] {
  const time = Date.now();
  return (['success', 'failed', 'failed', 'success'] as const).map((status, index) => ({
    id: randomUUID(), connection_id: connectionId, status,
    started_at: new Date(time + index * 2000).toISOString(),
    completed_at: new Date(time + index * 2000 + 1000).toISOString(),
    traces_fetched: status === 'success' ? 3 : 0,
    traces_created: status === 'success' ? 3 : 0,
    traces_updated: 0, spans_synced: 0, scores_synced: 0,
    error_kind: status === 'success' ? null : 'unreachable',
    sync_from: new Date(time - 60_000).toISOString(),
    sync_to: new Date(time + index * 2000).toISOString(), deleted: false,
  }));
}

const counts: readonly RecoveryCounts[] = [
  { traces_fetched: 3, traces_created: 3, traces_updated: 0, spans_synced: 0, scores_synced: 0 },
  { traces_fetched: 0, traces_created: 0, traces_updated: 0, spans_synced: 0, scores_synced: 0 },
  { traces_fetched: 0, traces_created: 0, traces_updated: 0, spans_synced: 0, scores_synced: 0 },
  { traces_fetched: 3, traces_created: 3, traces_updated: 0, spans_synced: 0, scores_synced: 0 },
];
const historyDefects = [
  { id: 'false-success', expected: 'W17.A02 EXACT_RECOVERY_STATUS_HISTORY', control: 'W17.R1' },
  { id: 'duplicate-dispatch-row', expected: 'W17.A02 EXACT_RECOVERY_STATUS_HISTORY', control: 'W17.R1' },
  { id: 'wrong-count', expected: 'W17.A02 EXACT_RECOVERY_COUNTS', control: 'W17.R1' },
  { id: 'wrong-kind', expected: 'W17.A02 EXACT_RECOVERY_KINDS', control: 'W17.R1' },
  { id: 'missing-final', expected: 'W17.A02 COMPLETED_SCOPED_HISTORY', control: 'W17.R1' },
  { id: 'foreign-connection', expected: 'W17.A02 COMPLETED_SCOPED_HISTORY', control: 'W17.R1' },
] as const;

for (const defect of historyDefects) {
  test(`J15-DETECTOR ${defect.control} ${defect.id}: exact recovery history`, {
    tag: ['@journey-J15-oracle'],
    annotation: [fragmentAnnotation('W17.A02', `${defect.control}/${defect.id}`, true), {
      type: 'journey-oracle-control', description: JSON.stringify({ control: defect.control,
        row: 'W17.A02', defect: defect.id, liveControl: false, walkedStatus: 'NOT_WALKED' }),
    }],
  }, async ({}, info) => {
    const connectionId = randomUUID();
    const good = history(connectionId);
    const bad = structuredClone(good);
    switch (defect.id) {
      case 'false-success': bad[1].status = 'success'; break;
      case 'duplicate-dispatch-row': bad.push({ ...bad[3], id: randomUUID() }); break;
      case 'wrong-count': bad[3].traces_fetched += 1; break;
      case 'wrong-kind': bad[1].error_kind = 'credentials_refused'; break;
      case 'missing-final': bad[2].completed_at = null; break;
      case 'foreign-connection': bad[3].connection_id = randomUUID(); break;
    }
    // Deduplication is only for the seed inventory; observations above retain
    // all repeats and extras when the detector checks them.
    await attachSeededIds(info, 'J15', {
      connectionIds: [...new Set([connectionId, ...bad.map(row => row.connection_id)])],
      runIds: [...new Set([...good, ...bad].map(row => row.id))],
    });
    await test.step('oracle baseline: exact four-run history', async () => {
      assertRecoveryHistory(good, connectionId, counts, 'unreachable');
    });
    await test.step(`oracle negative: ${defect.id} kills its exact detector`, async () => {
      expect(() => assertRecoveryHistory(bad, connectionId, counts, 'unreachable')).toThrow(defect.expected);
    });
    await test.step('oracle restored: full original history', async () => {
      assertRecoveryHistory(good, connectionId, counts, 'unreachable');
    });
  });
}
