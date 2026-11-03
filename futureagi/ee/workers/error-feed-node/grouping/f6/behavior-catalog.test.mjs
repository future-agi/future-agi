import test from 'node:test';
import assert from 'node:assert/strict';
import {catalogPrompt, validateCatalog, catalogCohort, prioritizeCatalogIssues, prepareCatalog,
  mergeCatalogPrompt, validateMergeCatalog, prepareMergeCatalog} from './behavior-catalog.mjs';
import {reconciliationCandidates} from './pipeline.mjs';
import {pairKey, scopeKey} from './common.mjs';

function fixture() {
  const rows = ['a', 'b', 'c'].map(id => ({id, organization_id: 'org', project_id: 'project',
    workload_type: 'simulation_test_execution', test_execution_id: 'execution',
    control: false, kind: 'behavior', evidence: [{id: 'report', text: `Accepted report ${id}`}]}));
  const context = {byId: new Map(rows.map(row => [row.id, row])), constraints: new Set(), policy: {digest: 'policy'}};
  const catalog = {groups: [{finding_ids: ['a', 'b'], behavior: 'Same reported failure', fix_hypothesis: 'Correct that action'},
    {finding_ids: ['c'], behavior: 'Distinct failure', fix_hypothesis: 'Different correction'}]};
  return {rows, context, catalog};
}

test('catalog compares every pending finding and keeps complete disjoint coverage', () => {
  const {context, catalog} = fixture();
  const prompt = catalogPrompt(['a', 'b', 'c'], context, {issues: []});
  assert.deepEqual(prompt.findings.map(row => row.id), ['a', 'b', 'c']);
  assert.equal(validateCatalog(catalog, ['a', 'b', 'c'], context), catalog);
  assert.deepEqual(catalogCohort('b', catalog, context.constraints).members, ['b', 'a']);
});

test('catalog rejects omissions, repeated ownership, unknown IDs and cannot-links', () => {
  const {context, catalog} = fixture();
  assert.throws(() => validateCatalog({groups: catalog.groups.slice(0, 1)}, ['a', 'b', 'c'], context), /drops/);
  assert.throws(() => validateCatalog({groups: [...catalog.groups, catalog.groups[1]]}, ['a', 'b', 'c'], context), /Repeated/);
  const unknown = structuredClone(catalog); unknown.groups[1].finding_ids = ['foreign'];
  assert.throws(() => validateCatalog(unknown, ['a', 'b', 'c'], context), /Unknown/);
  context.constraints.add(pairKey('a', 'b'));
  assert.throws(() => validateCatalog(catalog, ['a', 'b', 'c'], context), /Unsafe/);
});

test('catalog cannot pool findings across executions', () => {
  const {context} = fixture(); context.byId.get('c').test_execution_id = 'other';
  assert.throws(() => catalogPrompt(['a', 'c'], context, {issues: []}), /crosses scope/);
});

test('an admitted peer keeps its issue in the next cohort candidate window', () => {
  const {rows, context, catalog} = fixture();
  const issue = {id: 'same-behavior', active: true, scope: scopeKey(rows[0]), members: ['a']};
  const unrelated = {id: 'unrelated', active: true, members: ['c']};
  const ranked = prioritizeCatalogIssues(catalogCohort('b', catalog, context.constraints),
    {issues: [unrelated, issue]}, [unrelated], {candidate_issues: 1});
  assert.deepEqual(ranked.map(item => item.id), ['same-behavior']);
});

test('checkpointed catalog resumes without repeating paid planning or creating membership', async () => {
  const {context, catalog} = fixture();
  const state = {registry: {issues: []}, receipts: []}; let calls = 0, saves = 0;
  const investigate = async () => { calls++; return catalog; };
  investigate.receiptFor = () => 'receipt';
  const args = {findings: ['a', 'b', 'c'], state, context, investigate, save: async () => { saves++; }};
  await prepareCatalog(args); await prepareCatalog(args);
  assert.equal(calls, 1); assert.equal(saves, 1);
  assert.deepEqual(state.registry.issues, []);
  assert.equal(state.receipts[0].primary_receipt_id, 'receipt');
});

function mergeFixture() {
  const {rows, context} = fixture();
  context.policy = {...context.policy, max_merge_reviews: 2, max_input_bytes: 100000};
  const issues = rows.map(row => ({id: 'issue-' + row.id, active: true, protected: false,
    scope: scopeKey(row), members: [row.id], prototypes: [row.id],
    title: 'Reported failure ' + row.id, mechanism: 'Observed action ' + row.id,
    fix_hypothesis: 'Correct the reported action'}));
  return {context, issues, proposal: {pairs: [{issue_ids: ['issue-a', 'issue-c'], reason: 'Same concrete omission'}]}};
}

test('invalid discovery plans checkpoint a singleton fallback and never replan on resume', async () => {
  for (const invalid of ['omitted', 'duplicate', 'foreign', 'forbidden']) {
    const {context, catalog} = fixture();
    if (invalid === 'omitted') catalog.groups = [];
    if (invalid === 'duplicate') catalog.groups.push(catalog.groups[1]);
    if (invalid === 'foreign') catalog.groups[1].finding_ids = ['foreign'];
    if (invalid === 'forbidden') context.constraints.add(pairKey('a', 'b'));
    const state = {registry: {issues: []}, receipts: []};
    let calls = 0, checkpoint;
    const args = {findings: ['c', 'b', 'a'], state, context,
      investigate: async () => { calls++; return catalog; },
      save: async () => { checkpoint = structuredClone(state); }};
    await prepareCatalog(args);
    const resumed = await prepareCatalog({...args, state: checkpoint});
    assert.equal(calls, 1, invalid);
    assert.deepEqual(resumed.groups.map(group => group.finding_ids), [['a'], ['b'], ['c']]);
    assert.equal(checkpoint.receipts[0].status, 'incomplete');
    assert.equal(checkpoint.receipts[0].fallback, 'singleton_cohorts');
    assert.deepEqual(checkpoint.receipts[0].proposal, catalog);
    assert.deepEqual(checkpoint.registry.issues, []);
  }
});

test('merge planning sees all issue summaries and preserves membership on resume', async () => {
  const {context, issues, proposal} = mergeFixture();
  assert.equal(mergeCatalogPrompt(issues, context).issues.length, 3);
  const state = {registry: {issues: structuredClone(issues)}, receipts: []};
  let calls = 0;
  const args = {state, context, investigate: async () => { calls++; return proposal; }, save: async () => {}};
  await prepareMergeCatalog(args); await prepareMergeCatalog(args);
  assert.equal(calls, 1); assert.deepEqual(state.registry.issues, issues);
  assert.deepEqual(state.merge_catalog, proposal);
});

test('merge planning rejects foreign, repeated, unsafe and excessive comparisons', () => {
  const {context, issues, proposal} = mergeFixture();
  assert.equal(validateMergeCatalog(proposal, issues, context), proposal);
  const pair = ids => ({issue_ids: ids, reason: 'Candidate comparison'});
  for (const pairs of [[pair(['issue-a', 'missing'])], [pair(['issue-a', 'issue-a'])],
    [pair(['issue-a', 'issue-c']), pair(['issue-c', 'issue-a'])], Array(3).fill(pair(['issue-a', 'issue-c']))]) {
    assert.throws(() => validateMergeCatalog({pairs}, issues, context));
  }
  context.constraints.add(pairKey('a', 'c'));
  assert.throws(() => validateMergeCatalog(proposal, issues, context), /Unsafe/);
});

test('a distant planned duplicate is reviewed ahead of high-similarity distractors', () => {
  const {context, issues, proposal} = mergeFixture();
  const index = {similarity: (a, b) => [a, b].includes('b') ? 0.95 : 0.1};
  const candidates = reconciliationCandidates({issues}, index, () => ({decision: 'hold'}),
    {...context.policy, max_reconcile_candidates: 1}, proposal.pairs);
  assert.deepEqual(candidates[0].issue_ids, ['issue-a', 'issue-c']);
});

test('shared correction operations recover pairs omitted by both planner and embeddings', () => {
  const {context, issues} = mergeFixture();
  issues[0].fix_hypothesis = 'Invoke save_followup before offering a handoff';
  issues[2].fix_hypothesis = 'Persist requested callbacks with save_followup';
  issues[1].fix_hypothesis = 'Use a different search operation';
  const index = {similarity: (a, b) => [a, b].includes('b') ? 0.95 : 0.1};
  const unrelatedPlan = [{issue_ids: ['issue-a', 'issue-b'], reason: 'Planner suggestion'}];
  const ranked = reconciliationCandidates({issues}, index, () => ({decision: 'hold'}),
    {...context.policy, shared_correction_retrieval: true, max_reconcile_candidates: 1}, unrelatedPlan);
  assert.deepEqual(ranked[0].issue_ids, ['issue-a', 'issue-c']);
  const legacy = reconciliationCandidates({issues}, index, () => ({decision: 'hold'}),
    {...context.policy, max_reconcile_candidates: 1}, unrelatedPlan);
  assert.deepEqual(legacy[0].issue_ids, ['issue-a', 'issue-b']);
  assert.ok(issues.every(issue => issue.members.length === 1), 'Retrieval cannot merge membership');
});

test('invalid or oversized merge plans preserve issues and fall back without repeat calls', async () => {
  for (const oversized of [false, true]) {
    const {context, issues} = mergeFixture();
    if (oversized) context.policy.max_input_bytes = 1;
    const state = {registry: {issues: structuredClone(issues)}, receipts: []}; let calls = 0;
    const args = {state, context, investigate: async () => { calls++; return {pairs: [{issue_ids: ['foreign', 'issue-a'], reason: 'Bad'}]}; }, save: async () => {}};
    await prepareMergeCatalog(args); await prepareMergeCatalog(args);
    assert.equal(calls, oversized ? 0 : 1);
    assert.deepEqual(state.merge_catalog, {pairs: []});
    assert.deepEqual(state.registry.issues, issues);
    assert.equal(state.receipts[0].status, oversized ? 'held' : 'incomplete');
  }
});

test('source omission evidence retrieves duplicates even when generated corrections omit tool names', () => {
  const {context, issues} = mergeFixture();
  for (const [id, row] of context.byId) {
    row.supporting_refs = ['report', 'evaluation'];
    row.evidence.push({id: 'evaluation', text: id === 'b'
      ? 'Available tools include save_followup; it was called successfully.'
      : 'save_followup was not called and the follow-up store is empty.'});
    row.evidence.push({id: 'unrelated', text: 'unrelated_operation was not called'});
  }
  const index = {rows: context.byId, similarity: (a, b) => [a, b].includes('b') ? 0.95 : 0.1};
  const policy = {...context.policy, shared_correction_retrieval: true,
    source_operation_retrieval: true, max_reconcile_candidates: 1};
  const before = structuredClone(issues);
  const ranked = reconciliationCandidates({issues}, index, () => ({decision: 'hold'}), policy);
  assert.deepEqual(ranked[0].issue_ids, ['issue-a', 'issue-c']);
  assert.deepEqual(issues, before, 'Source retrieval cannot alter membership');
  const legacy = reconciliationCandidates({issues}, index, () => ({decision: 'hold'}),
    {...policy, source_operation_retrieval: false});
  assert.notDeepEqual(legacy[0].issue_ids, ['issue-a', 'issue-c']);
});
