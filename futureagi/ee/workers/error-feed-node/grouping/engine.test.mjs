import {addCompanionEvidence} from './f6/companion-context.mjs';
import {ViewIndex, selectPrototypes} from './f6/retrieval.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {adaptGroupingSnapshot} from './snapshot.mjs';
import {makeGroupingSnapshotFixture} from './snapshot-fixture.mjs';
import {digest} from './f6/common.mjs';
import {runGrouping} from './engine.mjs';
import {F6_MINILM_POLICY, SAMPLED_F6_MINILM_POLICY, SIMULATION_F6_MINILM_POLICY} from './policy.mjs';
import {Paused} from './f6/provider.mjs';
import {issueWording} from './f6/admission.mjs';

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
    title: 'Refund uses the wrong amount',
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
    title: 'Refund uses the wrong amount',
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

test('simulation catalog compares distant peers before evidence-based admission', async () => {
  const setup = fixture(), second = addSecondFinding(setup);
  const rows = [setup.row, second];
  for (const row of rows) {
    row.trace_id = null;
    row.workload_type = 'simulation_test_execution';
    row.test_execution_id = 'simulation-execution';
  }
  const seen = [];
  const investigate = withReceipts(async prompt => {
    seen.push(prompt);
    if (prompt.instructions.startsWith('Prepare candidate comparisons')) {
      assert.deepEqual(new Set(prompt.findings.map(item => item.id)), new Set(rows.map(row => row.id)));
      return {groups: [{finding_ids: rows.map(row => row.id),
        behavior: 'Refund amount differs from the requested amount',
        fix_hypothesis: 'Use the checked requested amount'}]};
    }
    if (prompt.candidate) return {action: 'hold', groups: [], removed_ids: [], reason: 'One coherent behavior'};
    assert.ok(prompt.instructions.includes('Not matching an existing issue'));
    assert.ok(prompt.retrieval_suggestion);
    return {groups: [groupFor(prompt, rows.map(row => row.id))], deferred: []};
  });
  const input = {rows, pendingIds: rows.map(row => row.id), features: setup.features,
    candidateWindow: setup.candidateWindow, store: setup.store, investigate,
    policy: SIMULATION_F6_MINILM_POLICY};
  const result = await runGrouping(input);
  assert.equal(result.status, 'complete');
  assert.equal(result.registry.issues.filter(issue => issue.active).length, 1);
  assert.ok(result.dispositions.every(disposition => disposition.state === 'assigned'));
  assert.ok(result.decision_receipts.some(receipt => receipt.phase === 'behavior_catalog'));
  const calls = seen.length;
  await runGrouping(input);
  assert.equal(seen.length, calls, 'completed replay must not repeat model calls');
});

test('simulation behavior policy rejects trace workloads before inference', async () => {
  const setup = fixture(); let called = false;
  await assert.rejects(() => runGrouping({rows: [setup.row], pendingIds: [setup.row.id],
    features: setup.features, candidateWindow: setup.candidateWindow, store: setup.store,
    policy: SIMULATION_F6_MINILM_POLICY,
    investigate: async () => { called = true; }}), /limited to simulation/);
  assert.equal(called, false);
});

test('rejected behavior plans still admit independently supported findings and resume without replanning', async () => {
  for (const invalid of ['omitted', 'duplicate', 'foreign', 'forbidden']) {
    const setup = fixture(), second = addSecondFinding(setup), rows = [setup.row, second];
    for (const row of rows) Object.assign(row, {trace_id: null,
      workload_type: 'simulation_test_execution', test_execution_id: 'simulation-execution'});
    let planning = 0, reviews = 0;
    const investigate = withReceipts(async prompt => {
      if (prompt.instructions.startsWith('Prepare candidate comparisons')) {
        planning++;
        const ids = rows.map(row => row.id);
        const group = finding_ids => ({finding_ids, behavior: 'Unverified plan', fix_hypothesis: 'Unverified correction'});
        return {groups: invalid === 'omitted' ? [] : invalid === 'duplicate' ? [group(ids), group([ids[0]])]
          : invalid === 'foreign' ? [group(['foreign'])] : [group(ids)]};
      }
      if (prompt.instructions.startsWith('Prepare cluster merge')) return {pairs: []};
      if (prompt.candidate) return {action: 'hold', groups: [], removed_ids: [], reason: 'Keep independent issues'};
      reviews++;
      assert.equal(prompt.findings.length, 1);
      assert.equal(prompt.retrieval_suggestion.behavior, 'Independently review the reported behavior');
      return validSingleton(prompt);
    });
    const input = {rows, pendingIds: rows.map(row => row.id), features: setup.features,
      candidateWindow: setup.candidateWindow, store: setup.store, investigate,
      cannotLinks: invalid === 'forbidden' ? [rows.map(row => row.id)] : [], policy: SIMULATION_F6_MINILM_POLICY};
    const result = await runGrouping(input);
    assert.equal(result.status, 'complete', invalid);
    assert.ok(result.dispositions.every(item => item.state === 'assigned'), invalid);
    assert.equal(result.registry.issues.filter(issue => issue.active).length, 2);
    assert.equal(reviews, 2); assert.equal(planning, 1);
    await runGrouping(input);
    assert.equal(reviews, 2); assert.equal(planning, 1);
  }
});

test('simulation merge catalog routes separate discoveries through validated merge commands', async () => {
  const setup = fixture(), second = addSecondFinding(setup), rows = [setup.row, second];
  for (const row of rows) Object.assign(row, {trace_id: null,
    workload_type: 'simulation_test_execution', test_execution_id: 'simulation-execution'});
  const investigate = withReceipts(async prompt => {
    if (prompt.instructions.startsWith('Prepare candidate comparisons')) return {
      groups: rows.map(row => ({finding_ids: [row.id], behavior: 'Wrong refund amount', fix_hypothesis: 'Use checked amount'}))};
    if (prompt.instructions.startsWith('Prepare cluster merge comparisons')) return {
      pairs: [{issue_ids: prompt.issues.map(issue => issue.id), reason: 'Both report using the wrong refund amount'}]};
    if (prompt.candidate) return {action: 'merge', groups: [groupFor(prompt, rows.map(row => row.id))],
      removed_ids: [], reason: 'Both own reports support the same action and correction'};
    return {groups: [groupFor(prompt, prompt.findings.map(row => row.id))], deferred: []};
  });
  const result = await runGrouping({rows, pendingIds: rows.map(row => row.id),
    features: setup.features, candidateWindow: setup.candidateWindow, store: setup.store,
    investigate, policy: SIMULATION_F6_MINILM_POLICY});
  assert.equal(result.status, 'complete');
  assert.equal(result.registry.issues.filter(issue => issue.active).length, 1);
  assert.ok(result.commands.some(command => command.type === 'merge'));
  assert.ok(result.decision_receipts.some(receipt => receipt.phase === 'merge_catalog'));
  assert.ok(result.dispositions.every(disposition => disposition.state === 'assigned'));
});

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
  assert.equal(result.commands[0].mechanism.title, 'Refund uses the wrong amount');
  assert.equal(result.commands[0].mechanism.mechanism,
    'Refund execution used ten instead of the requested hundred');
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

test('refresh preserves the legacy mechanism shape for an issue without a title', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const ownedRows = [setup.row, second];
  const pending = addThirdFinding(setup);
  const issue = offeredIssue(ownedRows);
  issue.mechanism.mechanism = 'A long existing mechanism explains the faulty refund decision and its evidence in detail. '.repeat(2);
  issue.prototype_occurrence_ids = [setup.row.id];
  const result = await runGrouping({rows: [...ownedRows, pending], pendingIds: [pending.id], features: setup.features,
    candidateWindow: {...setup.candidateWindow, issues: [issue]}, store: setup.store,
    investigate: withReceipts(async prompt => ({groups: [], deferred: prompt.findings.map(item =>
      ({finding_id: item.id, reason: 'No supported assignment'}))}))});
  assert.equal(result.status, 'complete');
  assert.deepEqual(result.commands.map(command => command.type), ['refresh']);
  assert.deepEqual(result.commands[0].mechanism, issue.mechanism);
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

test('targeted singleton admission retains evidence, contradiction and selected-member checks', async () => {
  for (const scenario of ['supported', 'missing citation', 'contradicted', 'foreign member']) {
    const setup = fixture();
    Object.assign(setup.row, {trace_id: null, workload_type: 'simulation_test_execution', test_execution_id: 'execution'});
    let targeted = 0;
    const investigate = withReceipts(async prompt => {
      if (prompt.instructions.startsWith('Prepare candidate comparisons')) return {groups: [{finding_ids: [setup.row.id],
        behavior: 'Incorrect refund amount', fix_hypothesis: 'Use requested amount'}]};
      if (prompt.instructions.includes('TARGETED LATE REVISIT:')) {
        targeted++;
        assert.equal(prompt.existing_issues.length, 0);
        const proposal = validSingleton(prompt), group = proposal.groups[0];
        if (scenario === 'missing citation') group.citations = [];
        if (scenario === 'contradicted') group.contradictions = [...group.citations];
        if (scenario === 'foreign member') group.member_ids.push('unselected-finding');
        return proposal;
      }
      return {groups: [], deferred: [{finding_id: setup.row.id, reason: 'No matching cluster yet'}]};
    });
    const input = {rows: [setup.row], pendingIds: [setup.row.id], features: setup.features,
      candidateWindow: setup.candidateWindow, store: setup.store, investigate, policy: SIMULATION_F6_MINILM_POLICY};
    const result = await runGrouping(input);
    assert.equal(result.status, 'complete', scenario);
    assert.equal(targeted, 1, scenario);
    assert.deepEqual(result.commands.map(command => command.type), scenario === 'supported' ? ['create'] : [], scenario);
    assert.equal(result.dispositions[0].state, scenario === 'supported' ? 'assigned' : 'deferred', scenario);
    if (scenario === 'supported') {
      const receipt = result.decision_receipts.find(item => item.phase === 'targeted_revisit');
      assert.equal(result.commands[0].admission.primary_receipt_id, receipt.primary_receipt_id);
      assert.deepEqual(result.commands[0].occurrence_ids, [setup.row.id]);
    }
    await runGrouping(input);
    assert.equal(targeted, 1, 'Completed checkpoint must not repeat singleton creation');
  }
});

test('a singleton created on revisit accepts a matching finding in a later batch of the same execution', async () => {
  const setup = fixture();
  Object.assign(setup.row, {trace_id: null, workload_type: 'simulation_test_execution', test_execution_id: 'execution'});
  const first = await runGrouping({rows: [setup.row], pendingIds: [setup.row.id], features: setup.features,
    candidateWindow: setup.candidateWindow, store: setup.store, policy: SIMULATION_F6_MINILM_POLICY,
    investigate: withReceipts(async prompt => {
      if (prompt.instructions.startsWith('Prepare candidate comparisons')) return {groups: [{finding_ids: [setup.row.id],
        behavior: 'Incorrect refund amount', fix_hypothesis: 'Use requested amount'}]};
      if (prompt.instructions.includes('TARGETED LATE REVISIT:')) return validSingleton(prompt);
      return {groups: [], deferred: [{finding_id: setup.row.id, reason: 'No matching cluster yet'}]};
    })});
  assert.deepEqual(first.commands.map(command => command.type), ['create']);
  const singleton = first.registry.issues[0];
  const offered = {...offeredIssue([setup.row]), issue_id: singleton.id, revision: singleton.mechanism_revision,
    mechanism: {title: singleton.title, mechanism: singleton.mechanism, fix_hypothesis: singleton.fix_hypothesis,
      falsifier: singleton.falsifier}, evidence_mode: 'sampled', member_count: 1,
    membership_revision: 1, membership_digest: 'sha256:' + 'a'.repeat(64)};
  offered.members[0].test_execution_id = setup.row.test_execution_id;
  const later = addSecondFinding(setup);
  setup.files.clear();
  const next = await runGrouping({rows: [setup.row, later], pendingIds: [later.id], features: setup.features,
    candidateWindow: {...setup.candidateWindow, registry_revision: 8, issues: [offered]},
    store: setup.store, policy: SIMULATION_F6_MINILM_POLICY,
    investigate: withReceipts(async prompt => {
      if (prompt.instructions.startsWith('Prepare candidate comparisons')) return {groups: [{finding_ids: [later.id],
        behavior: 'Incorrect refund amount', fix_hypothesis: 'Use requested amount'}]};
      if (prompt.candidate) return {action: 'hold', groups: [], removed_ids: [], reason: 'Same faulty operation'};
      const target = prompt.existing_issues.find(issue => issue.id === singleton.id);
      assert.ok(target, 'Previously created singleton must be offered for attachment');
      return {groups: [groupFor(prompt, [later.id], {target: target.id,
        prototypeIds: target.prototypes.map(row => row.id)})], deferred: []};
    })});
  assert.equal(next.commands.filter(command => command.type === 'create').length, 0);
  assert.equal(next.commands.filter(command => command.type === 'attach').length, 1);
  assert.equal(next.dispositions[0].issue_id, singleton.id);
  assert.deepEqual(new Set(next.registry.issues[0].members), new Set([setup.row.id, later.id]));
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

test('reconciliation merge keeps a source identity with exact member coverage', async () => {
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
  assert.ok(merged.source_issue_ids.includes(merged.survivor_issue_id));
  assert.deepEqual(result.registry.issues.find(issue => issue.id === merged.survivor_issue_id).members,
    rows.map(row => row.id).sort());
  assert.deepEqual(result.registry.issues.find(issue => issue.id === merged.survivor_issue_id).aliases.sort(),
    merged.source_issue_ids.filter(id => id !== merged.survivor_issue_id).sort());
  assert.equal(result.registry.issues.filter(issue => issue.active).length, 1);

  // A completed checkpoint from the previous worker has a newly created
  // merge target and two inactive sources. Its event is still replayable, so
  // resuming it must publish the legacy command instead of crashing.
  const checkpoint = structuredClone(setup.files.get('checkpoint.json'));
  const event = checkpoint.registry.events.at(-1);
  assert.equal(event.command.type, 'merge');
  const oldTarget = {...structuredClone(event.after.issues.find(issue =>
    issue.id === merged.survivor_issue_id)), id: 'legacy-merge-target', aliases: []};
  event.after.issues = event.after.issues.map(issue =>
    merged.source_issue_ids.includes(issue.id) ? {...issue, active: false} : issue);
  event.after.issues.push(oldTarget);
  const {hash, ...unsigned} = event;
  event.hash = digest(unsigned);
  checkpoint.registry.issues = structuredClone(event.after.issues);
  await setup.store.save('checkpoint.json', checkpoint);
  const resumed = await runGrouping({rows, pendingIds: rows.map(row => row.id),
    features: setup.features, candidateWindow: setup.candidateWindow, store: setup.store,
    investigate: async () => { throw new Error('completed checkpoint called provider'); }});
  assert.equal(resumed.status, 'complete');
  assert.equal(resumed.commands[2].temporary_id, oldTarget.id);
  assert.ok(!('survivor_issue_id' in resumed.commands[2]));
});

test('discovery and reconciliation prompts ask for plain-language titles and descriptions', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const rows = [setup.row, second];
  const seen = new Map();
  await runGrouping({rows, pendingIds: rows.map(row => row.id),
    features: setup.features, candidateWindow: setup.candidateWindow, store: setup.store,
    investigate: withReceipts(async prompt => {
      seen.set(prompt.candidate?.type ?? 'discovery', prompt.instructions);
      if (prompt.candidate) return {action: 'hold', groups: [], removed_ids: [], reason: 'Not reviewed'};
      return {groups: [groupFor(prompt, [prompt.findings[0].id])],
        deferred: prompt.findings.slice(1).map(item => ({finding_id: item.id,
          reason: 'Independent review required'}))};
    })});
  assert.ok(seen.has('discovery') && seen.has('merge_review'));
  for (const instructions of seen.values()) assert.ok(instructions.includes(issueWording));
  assert.match(issueWording, /agent the subject of the title, in active voice, and never stack more than three nouns/);
  assert.match(issueWording, /short sentences of at most 25 words/);
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
  assert.equal(result.registry.issues.filter(issue => issue.active && issue.aliases.includes('issue-existing')).length, 1);
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


function sampledSetup() {
  const setup=fixture();
  const second=addSecondFinding(setup), pending=addThirdFinding(setup);
  const offered=(row,id,count)=>({...offeredIssue([row]),issue_id:id,
    evidence_mode:'sampled',member_count:count,membership_complete:false,
    membership_revision:1,membership_digest:'sha256:'+'a'.repeat(64)});
  setup.input={rows:[setup.row,second,pending],pendingIds:[pending.id],features:setup.features,
    candidateWindow:{...setup.candidateWindow,issues:[offered(setup.row,'issue-a',15),offered(second,'issue-b',10)]},
    store:setup.store,policy:SAMPLED_F6_MINILM_POLICY};
  return setup;
}

test('large source counts no longer block a sampled merge',async()=>{
  const setup=sampledSetup();let merges=0;
  const result=await runGrouping({...setup.input,investigate:withReceipts(async prompt=>{
    if(!prompt.candidate)return {groups:[],deferred:prompt.findings.map(item=>({finding_id:item.id,reason:'uncertain'}))};
    assert.equal(prompt.candidate.type,'merge_review','partial membership must not receive a split review');
    assert.match(prompt.instructions,/4-12 words and at most 120 characters/);
    merges++;
    assert.deepEqual(prompt.issues.map(item=>item.member_count).sort((a,b)=>a-b),[10,15]);
    return {action:'merge',groups:[groupFor(prompt,prompt.findings.map(item=>item.id))],removed_ids:[],reason:'Same corrective action'};
  })});
  assert.equal(merges,1);
  assert.equal(result.commands.filter(item=>item.type==='merge').length,1);
  const merge=result.commands.find(item=>item.type==='merge');
  assert.equal(merge.reviewed_occurrence_ids.length,2);
  assert.equal(merge.survivor_issue_id,'issue-a');
  const target=result.registry.issues.find(item=>item.id===merge.survivor_issue_id);
  assert.equal(target.member_count,25);
  assert.equal(target.membership_complete,false);
});

test('uncertain sampled review leaves both large sources active',async()=>{
  const setup=sampledSetup();
  const result=await runGrouping({...setup.input,investigate:async prompt=>prompt.candidate
    ? {action:'hold',groups:[],removed_ids:[],reason:'Distinct corrections remain possible'}
    : {groups:[],deferred:prompt.findings.map(item=>({finding_id:item.id,reason:'uncertain'}))}});
  assert.equal(result.commands.some(item=>item.type==='merge'),false);
  assert.equal(result.registry.issues.filter(item=>item.active).length,2);
});

test('merge budget exhaustion holds topology and still allows publication',async()=>{
  const setup=sampledSetup();
  const result=await runGrouping({...setup.input,investigate:async prompt=>{
    if(prompt.candidate)throw Object.assign(new Error('Merge spending ceiling reached'),{name:'MergeReviewBudgetExceeded'});
    return {groups:[],deferred:prompt.findings.map(item=>({finding_id:item.id,reason:'uncertain'}))};
  }});
  assert.equal(result.status,'complete');
  assert.ok(result.decision_receipts.some(item=>item.reason==='Merge spending ceiling reached'));
  assert.equal(result.commands.some(item=>item.type==='merge'),false);
});

test('discovery budget exhaustion after a merge preserves publication and defers the pending finding',async()=>{
  const setup=sampledSetup();let merged=false,pausedPhase;
  const result=await runGrouping({...setup.input,investigate:withReceipts(async prompt=>{
    if(prompt.candidate){
      merged=true;
      return {action:'merge',groups:[groupFor(prompt,prompt.findings.map(item=>item.id))],
        removed_ids:[],reason:'Same corrective action'};
    }
    if(merged)throw Object.assign(new Error('Work spending limit reached'),{name:'GroupingBudgetExceeded',limit:'work',onBudgetPause:phase=>{pausedPhase=phase;}});
    return {groups:[],deferred:prompt.findings.map(item=>({finding_id:item.id,reason:'uncertain'}))};
  })});
  assert.equal(result.status,'complete');
  assert.equal(result.commands.filter(item=>item.type==='merge').length,1);
  assert.equal(pausedPhase,'discovery');
  assert.ok(result.decision_receipts.some(item=>item.reason==='Work spending limit reached'));
  assert.ok(result.dispositions.some(item=>item.state==='deferred'&&item.reason==='budget_exhausted:work'));
  const resumed=await runGrouping({...setup.input,investigate:async()=>{throw new Error('Completed budget-limited work retried inference');}});
  assert.deepEqual(resumed.commands,result.commands);
});

test('default policy retains reviewed candidates as attachment targets',async()=>{
  const setup=fixture(), owned=addSecondFinding(setup);
  const result=await runGrouping({rows:[setup.row,owned],pendingIds:[setup.row.id],features:setup.features,
    candidateWindow:{...setup.candidateWindow,issues:[offeredIssue([owned],true)]},store:setup.store,
    investigate:async prompt=>{
      assert.ok(prompt.existing_issues.some(issue=>issue.id===offeredIssue([owned],true).issue_id));
      return {groups:[],deferred:prompt.findings.map(item=>({finding_id:item.id,reason:'uncertain'}))};
    }});
  assert.equal(result.commands.some(item=>item.type==='attach'),false);
});

test('sampled policy attaches to a reviewed issue without refreshing or changing its identity',async()=>{
  const setup=fixture(),owned=addSecondFinding(setup);
  const issue={...offeredIssue([owned],true),evidence_mode:'sampled',member_count:1,
    membership_complete:true,membership_revision:1,membership_digest:'sha256:'+'a'.repeat(64)};
  const result=await runGrouping({rows:[setup.row,owned],pendingIds:[setup.row.id],features:setup.features,
    policy:SAMPLED_F6_MINILM_POLICY,candidateWindow:{...setup.candidateWindow,issues:[issue]},store:setup.store,
    investigate:withReceipts(async prompt=>{
      assert.ok(prompt.existing_issues.some(item=>item.id===issue.issue_id));
      return {groups:[groupFor(prompt,[setup.row.id],{target:issue.issue_id,prototypeIds:[owned.id]})],deferred:[]};
    })});
  assert.deepEqual(result.commands.map(command=>command.type),['attach']);
  const current=result.registry.issues.find(item=>item.id===issue.issue_id);
  assert.equal(current.protected,true);
  assert.equal(current.mechanism_revision,issue.revision);
  assert.equal(current.title,issue.mechanism.title);
  assert.equal(current.member_count,2);
});

test('loading ranked prototypes preserves their order without manufacturing a refresh', async () => {
  const setup = fixture();
  const ownedRows = Array.from({length: 8}, (_, i) => {
    const row = addSecondFinding(setup);
    row.id = `${i + 1}0000000-0000-4000-8000-000000000000`;
    row.occurrence_id = row.id;
    row.upstream_finding_id = 'owned-' + i;
    const feature = structuredClone(setup.features.rows['99999999-9999-4999-8999-999999999999']);
    const angle = [0, .15, .3, .45, .6, 1, 1.3, 1.5][i];
    feature.views.semantics.vector = Array.from({length: 384}, (_, d) => d === 0 ? Math.cos(angle) : d === 1 ? Math.sin(angle) : 0);
    setup.features.rows[row.id] = feature;
    return row;
  });
  const pending = addThirdFinding(setup);
  const issue = offeredIssue(ownedRows);
  const index = new ViewIndex(ownedRows, setup.features, F6_MINILM_POLICY);
  issue.prototype_occurrence_ids = selectPrototypes(ownedRows.map(row => row.id), index, F6_MINILM_POLICY.prototypes);
  assert.notDeepEqual(issue.prototype_occurrence_ids, [...issue.prototype_occurrence_ids].sort());
  const result = await runGrouping({rows: [...ownedRows, pending], pendingIds: [pending.id], features: setup.features,
    candidateWindow: {...setup.candidateWindow, issues: [issue]}, store: setup.store,
    investigate: withReceipts(async prompt => ({groups: [], deferred: prompt.findings.map(item =>
      ({finding_id: item.id, reason: 'No supported assignment'}))}))});
  assert.equal(result.status, 'complete');
  assert.ok(!result.commands.some(command => command.type === 'refresh'));
  // Reproduce the previous release's sorted registry and its sealed binding.
  const checkpoint = await setup.store.read('checkpoint.json');
  assert.equal(checkpoint.registry.events.length, 0);
  for (const item of checkpoint.registry.issues) item.prototypes.sort();
  const preparedRows = addCompanionEvidence([...ownedRows, pending], F6_MINILM_POLICY);
  const preparedFeatures = structuredClone(setup.features);
  for (const row of preparedRows) {
    preparedFeatures.rows[row.id].source_digest = row.source_digest;
    preparedFeatures.rows[row.id].evidence_revision = row.evidence_revision;
  }
  checkpoint.binding = digest({rows: preparedRows, pendingIds: [pending.id], features: preparedFeatures,
    cannotLinks: [], policy: F6_MINILM_POLICY, pairRelease: null, initialRegistry: checkpoint.registry,
    inputBinding: {registry_revision: setup.candidateWindow.registry_revision,
      omitted_candidates: setup.candidateWindow.omitted_candidates}});
  await setup.store.save('checkpoint.json', checkpoint);
  const resume = window => runGrouping({rows: [...ownedRows, pending], pendingIds: [pending.id],
    features: setup.features, candidateWindow: window, store: setup.store,
    investigate: async () => {throw new Error('Completed checkpoint must not call the model');}});
  assert.equal((await resume({...setup.candidateWindow, issues: [issue]})).status, 'complete');
  await assert.rejects(() => resume({...setup.candidateWindow, issues: [issue],
    registry_revision: setup.candidateWindow.registry_revision + 1}), /Checkpoint input\/policy mismatch/);
});

test('protected issues retain their examples even when the selector would replace them', async () => {
  const setup = fixture();
  const second = addSecondFinding(setup);
  const pending = addThirdFinding(setup);
  const issue = offeredIssue([setup.row, second], true);
  issue.prototype_occurrence_ids = [setup.row.id];
  const result = await runGrouping({rows: [setup.row, second, pending], pendingIds: [pending.id], features: setup.features,
    candidateWindow: {...setup.candidateWindow, issues: [issue]}, store: setup.store,
    investigate: withReceipts(async prompt => ({groups: [], deferred: prompt.findings.map(item =>
      ({finding_id: item.id, reason: 'No supported assignment'}))}))});
  assert.equal(result.status, 'complete');
  assert.ok(!result.commands.some(command => command.type === 'refresh'));
});
