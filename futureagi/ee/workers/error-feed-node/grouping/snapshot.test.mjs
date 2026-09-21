import assert from 'node:assert/strict';
import test from 'node:test';

import {
  adaptGroupingSnapshot,
  canonicalGroupingSourceDigest,
  canonicalSnapshotDigest,
} from './snapshot.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';

function redigest(snapshot) {
  const {snapshot_digest: _old, ...body} = snapshot;
  snapshot.snapshot_digest = canonicalSnapshotDigest(body);
  return snapshot;
}

test('adapts normalized snapshot to deterministic F6 statement and task rows', () => {
  const snapshot = makeGroupingSnapshotFixture();
  const [row] = adaptGroupingSnapshot(snapshot);

  assert.equal(row.id, snapshot.occurrences[0].occurrence_id);
  assert.equal(row.source_digest, canonicalGroupingSourceDigest(snapshot));
  assert.equal(row.source_digest,
    'sha256:28d4fd92c5b661ff265b0c92ebc243914f5f19e8646c5914a98c3fcb20651123');
  assert.equal(row.scan_version, `7:${snapshot.report.attempt_id}`);
  assert.deepEqual(row.views, {semantics: 'Only 10 was refunded instead of 100.', task: 'Refund café customer'});
  assert.deepEqual(row.supporting_refs, ['report', 'receipt:evidence-1']);
  assert.deepEqual(row.unresolved_supporting_refs, ['deleted-evidence']);
  assert.ok(row.missing_evidence.includes('unresolved_evidence:deleted-evidence'));
  assert.ok(row.missing_evidence.includes('attribution.origin'));
  assert.ok(row.missing_evidence.includes('investigation_read_incomplete'));
  assert.ok(row.missing_evidence.includes('future_arrivals_known'));
  assert.equal(row.evidence[0].provenance, 'accepted_finding_report');
  assert.equal(row.evidence[1].reference.evidence_id, 'evidence-1');
  assert.equal(row.evidence[2].provenance, 'requirement_report');
  assert.deepEqual(row.investigation_report_ref,
    {report_id: snapshot.report.id, result_digest: snapshot.report.result_digest});
  assert.deepEqual(adaptGroupingSnapshot(structuredClone(snapshot)), [row]);
});

test('grouping status changes full envelope integrity but not accepted source identity', () => {
  const pending = makeGroupingSnapshotFixture();
  const completed = structuredClone(pending);
  completed.report.grouping_status = 'completed';
  redigest(completed);
  assert.notEqual(completed.snapshot_digest, pending.snapshot_digest);
  assert.equal(canonicalGroupingSourceDigest(completed), canonicalGroupingSourceDigest(pending));
  assert.equal(adaptGroupingSnapshot(completed)[0].source_digest,
    adaptGroupingSnapshot(pending)[0].source_digest);

  const changedEvidence = structuredClone(completed);
  changedEvidence.report.findings[0].statement = 'A different accepted finding';
  redigest(changedEvidence);
  assert.notEqual(canonicalGroupingSourceDigest(changedEvidence),
    canonicalGroupingSourceDigest(pending));
  assert.notEqual(adaptGroupingSnapshot(changedEvidence)[0].source_digest,
    adaptGroupingSnapshot(pending)[0].source_digest);
});

test('strictly rejects stale, malformed-scope, gold and digest fields', () => {
  const stale = makeGroupingSnapshotFixture();
  stale.report.grouping_status = 'stale';
  assert.throws(() => adaptGroupingSnapshot(redigest(stale)), /stale report/);

  const wrongScope = makeGroupingSnapshotFixture();
  wrongScope.report.workspace_id = 42;
  assert.throws(() => adaptGroupingSnapshot(redigest(wrongScope)), /workspace_id/);

  const gold = makeGroupingSnapshotFixture();
  gold.report.findings[0].golden_issue = 'must-never-cross';
  assert.throws(() => adaptGroupingSnapshot(redigest(gold)), /unexpected or missing fields/);

  const changed = makeGroupingSnapshotFixture();
  changed.report.findings[0].statement = 'tampered';
  assert.throws(() => adaptGroupingSnapshot(changed), /snapshot digest mismatch/);
});

test('canonical digest fixture matches Python including date, unicode and string cost', () => {
  const crossLanguageFixture = {contract_version: 'grouping-snapshot/v1',
    date: '2026-09-18T10:11:12.123456Z', cost_usd: '0.010000000',
    label: 'Refund café customer', ordered: [2, 1], nested: {z: null, a: true}};
  assert.equal(canonicalSnapshotDigest(crossLanguageFixture),
    'sha256:6ad83376b3874156fc7bbffa67bdbcd14bde430991925d6ea6cdf2603cbf9aaf');
});
