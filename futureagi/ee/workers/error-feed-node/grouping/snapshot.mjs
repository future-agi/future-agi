import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

export const GROUPING_SNAPSHOT_CONTRACT_VERSION = 'grouping-snapshot/v1';

const MAX = Object.freeze({
  findings: 100,
  requirements: 100,
  evidence: 200,
  verifications: 100,
  citations: 100,
});
const REPORT_FIELDS = [
  'id', 'organization_id', 'workspace_id', 'project_id', 'trace_id', 'source',
  'source_version', 'recorded_at', 'is_current', 'has_issues', 'grouping_status',
  'job_id', 'generation', 'attempt_id', 'engine_version', 'read_cutoff',
  'memory_snapshot_id', 'memory_digest', 'idempotency_key',
  'source_contract_version', 'result_digest', 'evidence_digest',
  'execution_status', 'outcome', 'coverage', 'usage', 'requirement_checks',
  'evidence_receipts', 'findings', 'verification_receipts', 'missing_fields',
];
const ATTRIBUTION_ROLES = ['origin', 'decisive', 'symptom'];
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const UTC_DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/;
const COST = /^\d+\.\d{9}$/;

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value, expected, label) {
  assert.ok(isObject(value), `${label} must be an object`);
  const actual = Object.keys(value).sort();
  assert.deepEqual(actual, [...expected].sort(), `${label} has unexpected or missing fields`);
}

function text(value, label) {
  assert.ok(typeof value === 'string' && value.length > 0, `${label} must be non-empty text`);
  return value;
}

function nullableText(value, label) {
  assert.ok(value === null || (typeof value === 'string' && value.length > 0), `${label} must be text or null`);
}

function integer(value, label) {
  assert.ok(Number.isSafeInteger(value) && value >= 0, `${label} must be a non-negative integer`);
}

function boolean(value, label) {
  assert.equal(typeof value, 'boolean', `${label} must be boolean`);
}

function list(value, limit, label) {
  assert.ok(Array.isArray(value), `${label} must be an array`);
  assert.ok(value.length <= limit, `${label} exceeds ${limit}`);
  return value;
}

function textList(value, limit, label) {
  list(value, limit, label);
  value.forEach((item, index) => text(item, `${label}[${index}]`));
  assert.equal(new Set(value).size, value.length, `${label} contains duplicates`);
}

function dateText(value, label) {
  assert.ok(typeof value === 'string' && UTC_DATETIME.test(value), `${label} must be canonical UTC text`);
}

function digestText(value, label) {
  assert.ok(typeof value === 'string' && DIGEST.test(value), `${label} must be a SHA-256 digest`);
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (!isObject(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalValue(value[key])]));
}

export function canonicalSnapshotJson(value) {
  return JSON.stringify(canonicalValue(value));
}

export function canonicalSnapshotDigest(value) {
  return `sha256:${createHash('sha256').update(canonicalSnapshotJson(value), 'utf8').digest('hex')}`;
}

function contentDigest(value) {
  return createHash('sha256').update(canonicalSnapshotJson(value), 'utf8').digest('hex');
}

function unique(values) {
  return [...new Set(values)].sort();
}

function validateCoverage(coverage) {
  exactKeys(coverage, ['scope', 'observed_span_count', 'read_complete', 'future_arrivals_known'], 'report.coverage');
  text(coverage.scope, 'report.coverage.scope');
  integer(coverage.observed_span_count, 'report.coverage.observed_span_count');
  boolean(coverage.read_complete, 'report.coverage.read_complete');
  boolean(coverage.future_arrivals_known, 'report.coverage.future_arrivals_known');
}

function validateUsage(usage) {
  exactKeys(usage, ['model_calls', 'input_tokens', 'output_tokens', 'cost_usd', 'cost_status'], 'report.usage');
  integer(usage.model_calls, 'report.usage.model_calls');
  integer(usage.input_tokens, 'report.usage.input_tokens');
  integer(usage.output_tokens, 'report.usage.output_tokens');
  assert.ok(usage.cost_usd === null || (typeof usage.cost_usd === 'string' && COST.test(usage.cost_usd)),
    'report.usage.cost_usd must be a fixed 9-decimal string or null');
  text(usage.cost_status, 'report.usage.cost_status');
}

function validateAttribution(attribution, label) {
  exactKeys(attribution, ATTRIBUTION_ROLES, label);
  for (const role of ATTRIBUTION_ROLES) {
    const item = attribution[role];
    if (item === null) continue;
    exactKeys(item, ['status', 'span_id', 'evidence_ids'], `${label}.${role}`);
    assert.ok(['supported', 'unsupported', 'unknown'].includes(item.status), `${label}.${role}.status is invalid`);
    nullableText(item.span_id, `${label}.${role}.span_id`);
    textList(item.evidence_ids, MAX.citations, `${label}.${role}.evidence_ids`);
  }
}

function validateReport(report) {
  exactKeys(report, REPORT_FIELDS, 'report');
  for (const field of ['id', 'organization_id', 'project_id', 'trace_id', 'job_id', 'attempt_id',
    'engine_version', 'memory_snapshot_id', 'memory_digest', 'idempotency_key',
    'source_contract_version', 'result_digest', 'evidence_digest', 'outcome']) text(report[field], `report.${field}`);
  nullableText(report.workspace_id, 'report.workspace_id');
  nullableText(report.source_version, 'report.source_version');
  dateText(report.recorded_at, 'report.recorded_at');
  dateText(report.read_cutoff, 'report.read_cutoff');
  assert.equal(report.source, 'omega', 'only Omega reports can be grouped');
  assert.equal(report.is_current, true, 'report is not current');
  assert.notEqual(report.grouping_status, 'stale', 'stale report cannot be grouped');
  assert.equal(report.execution_status, 'completed', 'report execution is not completed');
  assert.ok(report.has_issues === null || typeof report.has_issues === 'boolean', 'report.has_issues must be boolean or null');
  assert.ok(Number.isSafeInteger(report.generation) && report.generation >= 1, 'report.generation is invalid');
  digestText(report.result_digest, 'report.result_digest');
  digestText(report.evidence_digest, 'report.evidence_digest');
  digestText(report.memory_digest, 'report.memory_digest');
  validateCoverage(report.coverage);
  validateUsage(report.usage);
  textList(report.missing_fields, 100, 'report.missing_fields');

  const requirements = new Map();
  for (const [index, item] of list(report.requirement_checks, MAX.requirements, 'report.requirement_checks').entries()) {
    const label = `report.requirement_checks[${index}]`;
    exactKeys(item, ['requirement_id', 'requirement', 'status', 'evidence_ids'], label);
    text(item.requirement_id, `${label}.requirement_id`);
    text(item.requirement, `${label}.requirement`);
    text(item.status, `${label}.status`);
    textList(item.evidence_ids, MAX.citations, `${label}.evidence_ids`);
    assert.ok(!requirements.has(item.requirement_id), 'duplicate requirement_id');
    requirements.set(item.requirement_id, item);
  }

  const receipts = new Map();
  for (const [index, item] of list(report.evidence_receipts, MAX.evidence, 'report.evidence_receipts').entries()) {
    const label = `report.evidence_receipts[${index}]`;
    exactKeys(item, ['evidence_id', 'span_id', 'parent_span_id', 'excerpt', 'end_time'], label);
    text(item.evidence_id, `${label}.evidence_id`);
    text(item.span_id, `${label}.span_id`);
    nullableText(item.parent_span_id, `${label}.parent_span_id`);
    text(item.excerpt, `${label}.excerpt`);
    if (item.end_time !== null) dateText(item.end_time, `${label}.end_time`);
    assert.ok(!receipts.has(item.evidence_id), 'duplicate evidence_id');
    receipts.set(item.evidence_id, item);
  }

  const findings = new Map();
  for (const [index, item] of list(report.findings, MAX.findings, 'report.findings').entries()) {
    const label = `report.findings[${index}]`;
    exactKeys(item, ['finding_id', 'kind', 'statement', 'requirement_id', 'evidence_ids', 'recovery', 'attribution'], label);
    text(item.finding_id, `${label}.finding_id`);
    nullableText(item.kind, `${label}.kind`);
    text(item.statement, `${label}.statement`);
    nullableText(item.requirement_id, `${label}.requirement_id`);
    nullableText(item.recovery, `${label}.recovery`);
    textList(item.evidence_ids, MAX.citations, `${label}.evidence_ids`);
    validateAttribution(item.attribution, `${label}.attribution`);
    if (item.requirement_id !== null) assert.ok(requirements.has(item.requirement_id), 'unresolved requirement_id');
    assert.ok(!findings.has(item.finding_id), 'duplicate finding_id');
    findings.set(item.finding_id, item);
  }

  const verificationIds = new Set();
  for (const [index, item] of list(report.verification_receipts, MAX.verifications, 'report.verification_receipts').entries()) {
    const label = `report.verification_receipts[${index}]`;
    exactKeys(item, ['receipt_id', 'executed'], label);
    text(item.receipt_id, `${label}.receipt_id`);
    boolean(item.executed, `${label}.executed`);
    assert.ok(!verificationIds.has(item.receipt_id), 'duplicate verification receipt_id');
    verificationIds.add(item.receipt_id);
  }
  return {findings, requirements, receipts};
}

/**
 * Convert a strict normalized Django snapshot into the rows consumed by F6.
 * Gold labels and source fields that are absent from normalized storage are
 * never inferred here.
 */
export function adaptGroupingSnapshot(snapshot) {
  exactKeys(snapshot, ['contract_version', 'report', 'occurrences', 'snapshot_digest'], 'snapshot');
  assert.equal(snapshot.contract_version, GROUPING_SNAPSHOT_CONTRACT_VERSION, 'unsupported grouping snapshot contract');
  digestText(snapshot.snapshot_digest, 'snapshot.snapshot_digest');
  const {snapshot_digest: suppliedDigest, ...body} = snapshot;
  assert.equal(suppliedDigest, canonicalSnapshotDigest(body), 'snapshot digest mismatch');

  const {findings, requirements, receipts} = validateReport(snapshot.report);
  const occurrences = list(snapshot.occurrences, MAX.findings, 'snapshot.occurrences');
  assert.equal(occurrences.length, findings.size, 'findings/occurrences count mismatch');
  const occurrenceIds = new Set();
  const mappedFindings = new Set();
  const rows = [];
  for (const [index, occurrence] of occurrences.entries()) {
    const label = `snapshot.occurrences[${index}]`;
    exactKeys(occurrence, ['occurrence_id', 'finding_id'], label);
    text(occurrence.occurrence_id, `${label}.occurrence_id`);
    text(occurrence.finding_id, `${label}.finding_id`);
    assert.ok(!occurrenceIds.has(occurrence.occurrence_id), 'duplicate occurrence_id');
    assert.ok(findings.has(occurrence.finding_id) && !mappedFindings.has(occurrence.finding_id),
      'missing or repeated finding mapping');
    occurrenceIds.add(occurrence.occurrence_id);
    mappedFindings.add(occurrence.finding_id);

    const report = snapshot.report;
    const finding = findings.get(occurrence.finding_id);
    const linkedRequirement = finding.requirement_id === null ? null : requirements.get(finding.requirement_id);
    const attributionRefs = ATTRIBUTION_ROLES.flatMap(role => finding.attribution[role]?.evidence_ids || []);
    const allRefs = unique([...finding.evidence_ids, ...attributionRefs, ...(linkedRequirement?.evidence_ids || [])]);
    const evidence = [{id: 'report', text: finding.statement, digest: contentDigest(finding.statement),
      provenance: 'accepted_finding_report'}];
    for (const evidenceId of allRefs) {
      const receipt = receipts.get(evidenceId);
      if (receipt) evidence.push({id: `receipt:${evidenceId}`, text: receipt.excerpt,
        digest: contentDigest(receipt.excerpt), provenance: 'investigation_evidence_receipt',
        reference: structuredClone(receipt)});
    }
    report.requirement_checks.forEach((check, requirementIndex) => evidence.push({
      id: `requirement-${requirementIndex}`,
      text: check.requirement,
      digest: contentDigest(check.requirement),
      provenance: 'requirement_report',
      reference: structuredClone(check),
    }));
    const missing = [...report.missing_fields,
      ...allRefs.filter(evidenceId => !receipts.has(evidenceId)).map(evidenceId => `unresolved_evidence:${evidenceId}`)];
    for (const role of ATTRIBUTION_ROLES) {
      if (finding.attribution[role] === null) missing.push(`attribution.${role}`);
    }
    if (!report.coverage.read_complete) missing.push('investigation_read_incomplete');
    if (report.coverage.future_arrivals_known) missing.push('future_arrivals_known');

    rows.push({
      id: occurrence.occurrence_id,
      occurrence_id: occurrence.occurrence_id,
      scan_issue_id: null,
      upstream_finding_id: finding.finding_id,
      organization_id: report.organization_id,
      workspace_id: report.workspace_id,
      project_id: report.project_id,
      trace_id: report.trace_id,
      engine_version: report.engine_version,
      scan_version: `${report.generation}:${report.attempt_id}`,
      evidence_revision: report.evidence_digest,
      source_digest: snapshot.snapshot_digest,
      kind: finding.kind,
      summary: finding.statement,
      recovery: finding.recovery,
      terminal_effect: 'unknown',
      outcome: {success: 'satisfied', failure: 'violated', unknown: 'unknown'}[report.outcome] || 'unknown',
      analysis_status: report.execution_status,
      control: false,
      impact: null,
      slice: GROUPING_SNAPSHOT_CONTRACT_VERSION,
      localization: null,
      attribution: structuredClone(finding.attribution),
      requirement_id: finding.requirement_id,
      requirement_checks: structuredClone(report.requirement_checks),
      linked_requirement: structuredClone(linkedRequirement),
      evidence,
      source_event_ids: [...finding.evidence_ids],
      supporting_refs: ['report', ...unique(finding.evidence_ids).filter(evidenceId => receipts.has(evidenceId))
        .map(evidenceId => `receipt:${evidenceId}`)],
      refuting_refs: [],
      unresolved_supporting_refs: finding.evidence_ids.filter(evidenceId => !receipts.has(evidenceId)),
      unresolved_refuting_refs: [],
      missing_evidence: unique(missing),
      views: {
        semantics: finding.statement,
        task: linkedRequirement?.requirement || report.requirement_checks.map(check => check.requirement).join('\n'),
      },
      investigation_report_ref: {report_id: report.id, result_digest: report.result_digest},
      investigation_context: structuredClone(report),
      investigation_context_digest: contentDigest(report),
    });
  }
  return rows;
}
