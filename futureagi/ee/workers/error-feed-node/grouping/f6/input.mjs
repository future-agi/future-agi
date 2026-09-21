import assert from 'node:assert/strict';
import {digest, requireText, pairKey, VIEWS} from './common.mjs';

// The shared production snapshot adapter owns interpretation. This validator
// retains the sealed F6 row/evidence checks without importing research readers.
export function validateInput(rows, cannotLinks = []) {
  const byId = new Map();
  for (const row of rows) {
    for (const field of ['id', 'organization_id', 'project_id', 'trace_id',
      'engine_version', 'scan_version', 'evidence_revision', 'source_digest', 'slice']) {
      requireText(row[field], field);
    }
    assert.ok(!byId.has(row.id), 'Duplicate occurrence identity');
    assert.ok(typeof row.control === 'boolean' && Array.isArray(row.missing_evidence));
    assert.ok(Array.isArray(row.evidence)
      && new Set(row.evidence.map(evidence => evidence.id)).size === row.evidence.length);
    for (const evidence of row.evidence) {
      requireText(evidence.text, 'Evidence text');
      assert.equal(evidence.digest, digest(evidence.text), 'Evidence changed');
    }
    for (const reference of [...row.supporting_refs, ...row.refuting_refs]) {
      assert.ok(row.evidence.some(evidence => evidence.id === reference), 'Unresolved evidence reference');
    }
    assert.ok(row.supporting_refs.length, 'No supported finding/control evidence');
    if (row.control) {
      assert.ok(row.analysis_status === 'completed' && row.outcome === 'satisfied'
        && row.supporting_refs.every(id => row.evidence.find(evidence => evidence.id === id).provenance === 'captured_record'),
      'Unverified healthy control');
    }
    for (const [view, value] of Object.entries(row.views)) {
      assert.ok(VIEWS.includes(view) && typeof value === 'string');
    }
    byId.set(row.id, row);
  }
  const constraints = new Set();
  for (const [first, second] of cannotLinks) {
    assert.ok(first !== second && byId.has(first) && byId.has(second), 'Invalid cannot-link');
    constraints.add(pairKey(first, second));
  }
  return {byId, constraints};
}
