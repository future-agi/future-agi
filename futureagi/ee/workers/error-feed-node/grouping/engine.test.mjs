import test from 'node:test';
import assert from 'node:assert/strict';
import {adaptGroupingSnapshot} from './snapshot.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';
import {digest} from './f6/common.mjs';
import {runGrouping} from './engine.mjs';
import {F6_MINILM_POLICY} from './policy.mjs';
import {Paused} from './f6/provider.mjs';

function fixture() {
  const [row] = adaptGroupingSnapshot(makeGroupingSnapshotFixture());
  const vector = Array.from({length: 384}, (_, index) => index === 0 ? 1 : 0);
  const features = {model: 'all-MiniLM-L6-v2', dimension: 384, rows: {
    [row.id]: {source_digest: row.source_digest, evidence_revision: row.evidence_revision,
      views: {semantics: {vector, text_digest: digest(row.views.semantics)},
        task: {vector, text_digest: digest(row.views.task)}}},
  }};
  const candidateWindow = {registry_revision: 7, issues: [], omitted_candidates: []};
  const files = new Map();
  const store = {read: async path => files.get(path) ?? null,
    save: async (path, value) => { files.set(path, structuredClone(value)); }};
  return {row, features, candidateWindow, store, files};
}

function withReceipts(callback) {
  const ids = new WeakMap();
  let sequence = 0;
  const investigate = async (...args) => {
    const result = await callback(...args);
    ids.set(result, `00000000-0000-4000-8000-${String(++sequence).padStart(12, '0')}`);
    return result;
  };
  investigate.receiptFor = result => ids.get(result) ?? null;
  return investigate;
}

function validSingleton(prompt) {
  const finding = prompt.findings[0];
  const source = finding.evidence.find(item => item.id === 'report');
  return {groups: [{target_issue_id: null, member_ids: [finding.id],
    mechanism: 'Refund execution used ten instead of the requested hundred',
    fix_hypothesis: 'Use the checked requested amount in the refund execution step',
    falsifier: 'A captured execution with the correct requested amount would refute this mechanism',
    predicted_observations: ['The executed amount differs from the requested amount'],
    citations: [{finding_id: finding.id, evidence_id: 'report',
      evidence_digest: source.digest, quote: source.text}],
    contradictions: [], alternatives: ['A stale display value alone would not explain the captured executed amount'],
    missing_evidence: []}], deferred: []};
}

function addSecondFinding(fixture) {
  const second = structuredClone(fixture.row);
  second.id = '99999999-9999-4999-8999-999999999999';
  second.occurrence_id = second.id;
  second.upstream_finding_id = 'finding-2';
  second.summary = 'Only 20 was refunded instead of 200.';
  second.views.semantics = second.summary;
  second.evidence[0] = {...second.evidence[0], text: second.summary,
    digest: digest(second.summary)};
  const vector = Array.from({length: 384}, (_, index) => index === 0 ? 1 : 0);
  fixture.features.rows[second.id] = {
    source_digest: second.source_digest,
    evidence_revision: second.evidence_revision,
    views: {semantics: {vector, text_digest: digest(second.views.semantics)},
      task: {vector, text_digest: digest(second.views.task)}},
  };
  return second;
}

function addThirdFinding(fixture) {
  const third = addSecondFinding(fixture);
  third.id = '77777777-7777-4777-8777-777777777777';
  third.occurrence_id = third.id;
  third.upstream_finding_id = 'finding-3';
  fixture.features.rows[third.id] = {
    source_digest: third.source_digest, evidence_revision: third.evidence_revision,
    views: {semantics: {vector: Array.from({length: 384}, (_, index) => index === 0 ? 1 : 0),
      text_digest: digest(third.views.semantics)},
    task: {vector: Array.from({length: 384}, (_, index) => index === 0 ? 1 : 0),
      text_digest: digest(third.views.task)}},
  };
  return third;
}

function groupFor(prompt, ids, {target = null, prototypeIds = []} = {}) {
  const sources = new Map([...prompt.findings, ...(prompt.existing_issues || [])
    .flatMap(issue => issue.prototypes)] .map(item => [item.id, item]));
  return {target_issue_id: target, member_ids: ids,
    mechanism: 'Refund execution used a smaller amount than the requested amount',
    fix_hypothesis: 'Use the checked requested amount in the refund execution step',
    falsifier: 'The captured execution used the correct requested amount',
    predicted_observations: ['Requested and executed amounts differ'],
    citations: [...new Set([...ids, ...prototypeIds])].map(id => {
      const source = sources.get(id).evidence.find(item => item.id === 'report');
      return {finding_id: id, evidence_id: 'report', evidence_digest: source.digest,
        quote: source.text};
    }),
    contradictions: [], alternatives: ['A stale display value alone would not explain the execution'],
    missing_evidence: []};
}

function offeredIssue(rows, protectedIssue = false) {
  return {issue_id: 'issue-existing', revision: 2, protected: protectedIssue,
    mechanism: {mechanism: 'Refund execution used the wrong amount',
      fix_hypothesis: 'Use the checked requested amount',
      falsifier: 'The captured execution used the correct amount'},
    prototype_occurrence_ids: rows.map(row => row.id), membership_complete: true,
    members: rows.map(row => ({occurrence_id: row.id,
      report_id: row.investigation_report_ref.report_id, trace_id: row.trace_id,
      source_digest: row.source_digest, evidence_revision: row.evidence_revision}))};
}

test('bounded F6 engine admits supported singleton and emits typed create command', async () => {
  const {row, features, candidateWindow, store} = fixture();
  let calls = 0;
  const result = await runGrouping({rows: [row], pendingIds: [row.id], features,
    candidateWindow, store, investigate: withReceipts(async prompt => { calls++; return validSingleton(prompt); })});
  assert.equal(result.status, 'complete');
  assert.equal(result.policy_digest, F6_MINILM_POLICY.digest);
  assert.equal(calls, 1);
  assert.equal(result.commands.length, 1);
  assert.equal(result.commands[0].type, 'create');
  assert.deepEqual(result.commands[0].occurrence_ids, [row.id]);
  assert.deepEqual(result.commands[0].admission,
    {primary_receipt_id:'00000000-0000-4000-8000-000000000001',
      group_index:0,repair_receipt_id:null});
  assert.deepEqual(result.dispositions, [{occurrence_id: row.id, state: 'assigned',
    issue_id: result.commands[0].temporary_id}]);
  assert.ok(result.decision_receipts.some(receipt => receipt.status === 'complete'));
});

test('incomplete or stale candidate membership fails before any model call', async () => {
  const {row, features, candidateWindow, store} = fixture();
  let calls = 0;
  const investigate = async () => { calls++; return validSingleton(); };
  const issue = {issue_id: 'issue-1', revision: 1, protected: false,
    mechanism: {mechanism: 'wrong amount', fix_hypothesis: 'correct amount', falsifier: 'amount was correct'},
    prototype_occurrence_ids: [row.id], members: [{occurrence_id: row.id,
      report_id: 'report', trace_id: row.trace_id, source_digest: row.source_digest,
      evidence_revision: row.evidence_revision}], membership_complete: false};
  await assert.rejects(() => runGrouping({rows: [row], pendingIds: [row.id], features,
    candidateWindow: {...candidateWindow, issues: [issue]}, store, investigate}), /Incomplete candidate/);
  assert.equal(calls, 0);
});

test('feature revision and policy mismatches fail closed', async () => {
  const {row, features, candidateWindow, store} = fixture();
  features.rows[row.id].source_digest = 'stale';
  await assert.rejects(() => runGrouping({rows: [row], pendingIds: [row.id], features,
    candidateWindow, store, investigate: async () => { throw new Error('unexpected call'); }}), /stale F6 feature/);
  await assert.rejects(() => runGrouping({rows: [row], pendingIds: [row.id], features,
    candidateWindow, store, policy: {...F6_MINILM_POLICY, max_output_tokens: 2048},
    investigate: async () => { throw new Error('unexpected call'); }}), /Unapproved F6/);
});

test('same-proposal citation repair adds missing own-report citation without changing group identity', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const rows = [setup.row, second];
  let repairs = 0;
  let repairIntent;
  const result = await runGrouping({rows, pendingIds: rows.map(item => item.id),
    features: setup.features, candidateWindow: setup.candidateWindow,
    store: setup.store, investigate: withReceipts(async (prompt,_schema,_rows,options) => {
      if (prompt.instructions.startsWith('CITATION-ONLY REPAIR.')) {
        repairs++;
        repairIntent=options.repairIntent;
        const id = prompt.missing_own_reports[0];
        const source = rows.find(item => item.id === id).evidence[0];
        return {action: 'add_citations', reason: 'Exact report supports the fixed mechanism',
          citations: [{finding_id: id, evidence_id: 'report',
            evidence_digest: source.digest, quote: source.text}]};
      }
      const ids = prompt.findings.map(item => item.id);
      const group = groupFor(prompt, ids);
      group.citations.pop();
      return {groups: [group], deferred: []};
    })});
  assert.equal(result.status, 'complete');
  assert.equal(repairs, 1);
  assert.equal(result.commands.filter(command => command.type === 'create').length, 1);
  assert.deepEqual(result.commands[0].occurrence_ids, rows.map(item => item.id).sort());
  assert.deepEqual(repairIntent,{primary_receipt_id:'00000000-0000-4000-8000-000000000001',
    group_index:0,missing_own_report_ids:[rows[1].id]});
  assert.deepEqual(result.commands[0].admission,
    {primary_receipt_id:'00000000-0000-4000-8000-000000000001',
      group_index:0,repair_receipt_id:'00000000-0000-4000-8000-000000000002'});
  assert.ok(result.decision_receipts[0].citation_repairs.some(audit => audit.status === 'repaired'));
});

test('targeted revisit does not duplicate a review when the target was already visible', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const rows = [setup.row, second];
  let targetedCalls = 0;
  const result = await runGrouping({rows, pendingIds: rows.map(item => item.id),
    features: setup.features, candidateWindow: setup.candidateWindow,
    store: setup.store, investigate: async prompt => {
      if (prompt.candidate) return {action: 'hold', groups: [], removed_ids: [],
        reason: 'No topology change supported'};
      if (prompt.instructions.includes('TARGETED LATE REVISIT:')) {
        targetedCalls++;
        const target = prompt.existing_issues[0];
        return {groups: [groupFor(prompt, [setup.row.id], {
          target: target.id, prototypeIds: target.prototypes.map(item => item.id),
        })], deferred: []};
      }
      const selected = prompt.findings.map(item => item.id);
      return {groups: selected.includes(second.id)
        ? [groupFor(prompt, [second.id])] : [],
      deferred: selected.includes(setup.row.id)
        ? [{finding_id: setup.row.id, reason: 'Target not yet available'}] : []};
    }});
  assert.equal(result.status, 'complete');
  assert.equal(targetedCalls, 0);
  assert.deepEqual(result.commands.map(command => command.type), ['create']);
  assert.equal(result.dispositions.filter(item => item.state === 'assigned').length, 1);
  assert.equal(result.dispositions.filter(item => item.state === 'deferred').length, 1);
});

test('a paused investigation emits no publishable commands and resumes from its checkpoint', async () => {
  const setup = fixture();
  const input = {rows: [setup.row], pendingIds: [setup.row.id], features: setup.features,
    candidateWindow: setup.candidateWindow, store: setup.store};
  const paused = await runGrouping({...input,
    investigate: async () => { throw new Paused('provider budget unavailable'); }});
  assert.equal(paused.status, 'paused');
  assert.deepEqual(paused.commands, []);
  assert.deepEqual(paused.dispositions, [{occurrence_id: setup.row.id, state: 'pending'}]);
  const completed = await runGrouping({...input,
    investigate: withReceipts(async prompt => validSingleton(prompt))});
  assert.equal(completed.status, 'complete');
  assert.deepEqual(completed.commands.map(command => command.type), ['create']);
  assert.equal(completed.commands[0].admission.primary_receipt_id,
    '00000000-0000-4000-8000-000000000001');
  await assert.rejects(() => runGrouping({...input,
    candidateWindow: {...setup.candidateWindow, registry_revision: 8},
    investigate: async () => { throw new Error('stale checkpoint called provider'); }}),
  /Checkpoint input\/policy mismatch/);
  const replayed = await runGrouping({...input,
    investigate: async () => { throw new Error('completed checkpoint called provider again'); }});
  assert.deepEqual(replayed.commands, completed.commands);
});

test('reconciliation merge creates a new target with exact member coverage', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const rows = [setup.row, second];
  const result = await runGrouping({rows, pendingIds: rows.map(row => row.id),
    features: setup.features, candidateWindow: setup.candidateWindow, store: setup.store,
    investigate: withReceipts(async prompt => {
      if (prompt.candidate) return prompt.candidate.type === 'merge_review'
        ? {action: 'merge', groups: [groupFor(prompt, rows.map(row => row.id))],
          removed_ids: [], reason: 'All current reports support one mechanism'}
        : {action: 'hold', groups: [], removed_ids: [], reason: 'No split evidence'};
      const id = prompt.findings[0].id;
      return {groups: [groupFor(prompt, [id])],
        deferred: prompt.findings.slice(1).map(item => ({finding_id: item.id,
          reason: 'Independent review required'}))};
    })});
  assert.equal(result.status, 'complete');
  assert.deepEqual(result.commands.map(command => command.type), ['create', 'create', 'merge']);
  const merged = result.commands[2];
  assert.ok(merged.admission.primary_receipt_id);
  assert.equal(merged.admission.group_index,0);
  assert.deepEqual(merged.source_issue_ids.slice().sort(),
    result.commands.slice(0, 2).map(command => command.temporary_id).sort());
  assert.deepEqual(result.registry.issues.find(issue => issue.id === merged.temporary_id).members,
    rows.map(row => row.id).sort());
});

test('reconciliation split retires the source and partitions all current members', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const rows = [setup.row, second];
  const pending = addThirdFinding(setup);
  const result = await runGrouping({rows: [...rows, pending], pendingIds: [pending.id],
    features: setup.features, candidateWindow: {...setup.candidateWindow,
      issues: [offeredIssue(rows)]}, store: setup.store,
    investigate: withReceipts(async prompt => {
      if (!prompt.candidate) return {groups: [], deferred: prompt.findings.map(item =>
        ({finding_id: item.id, reason: 'Insufficient evidence to assign this occurrence'}))};
      if (prompt.candidate?.type === 'merge_review') return {action: 'hold', groups: [],
        removed_ids: [], reason: 'Distinct mechanisms remain distinct'};
      assert.equal(prompt.candidate?.type, 'split_review');
      return {action: 'split', groups: rows.map(row => groupFor(prompt, [row.id])),
        removed_ids: [], reason: 'Current full membership supports two distinct mechanisms'};
    })});
  assert.equal(result.status, 'complete');
  assert.deepEqual(result.commands.map(command => command.type), ['split']);
  assert.equal(result.commands[0].issue_id, 'issue-existing');
  assert.deepEqual(result.commands[0].parts.flatMap(part => part.occurrence_ids).sort(),
    rows.map(row => row.id).sort());
  assert.deepEqual(result.commands[0].admissions.map(item => item.group_index),[0,1]);
  assert.equal(result.commands[0].admissions[0].primary_receipt_id,
    result.commands[0].admissions[1].primary_receipt_id);
  assert.equal(result.registry.issues.find(issue => issue.id === 'issue-existing').active, false);
});

test('a protected issue cannot be split by model review', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const rows = [setup.row, second];
  const pending = addThirdFinding(setup);
  let reconciliationCalls = 0;
  const result = await runGrouping({rows: [...rows, pending], pendingIds: [pending.id],
    features: setup.features, candidateWindow: {...setup.candidateWindow,
      issues: [offeredIssue(rows, true)]}, store: setup.store,
    investigate: async prompt => {
      if (prompt.candidate) { reconciliationCalls++; throw new Error('protected issue was reviewed'); }
      return {groups: [], deferred: prompt.findings.map(item => ({finding_id: item.id,
        reason: 'Insufficient evidence to assign this occurrence'}))};
    }});
  assert.equal(result.status, 'complete');
  assert.equal(reconciliationCalls, 0);
  assert.deepEqual(result.commands, []);
  assert.ok(result.decision_receipts.some(receipt => receipt.reason?.includes('operator approval')));
});

test('pre-owned pending occurrence fails before model use or publication', async () => {
  const setup = fixture();
  await assert.rejects(() => runGrouping({rows: [setup.row], pendingIds: [setup.row.id],
    features: setup.features, candidateWindow: {...setup.candidateWindow,
      issues: [offeredIssue([setup.row])]}, store: setup.store,
    investigate: async () => { throw new Error('provider called'); }}),
  /already belongs/);
});
