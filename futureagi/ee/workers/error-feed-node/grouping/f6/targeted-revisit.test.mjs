import test from 'node:test';
import assert from 'node:assert/strict';
import {digest, scopeKey} from './common.mjs';
import {snapshotTargets, rankNovelReviews, runTargetedReviews} from './targeted-revisit.mjs';
import {packInvestigation, retrieveIssues} from './pipeline.mjs';

function fixture() {
  const rows = ['pending', 'first', 'omitted'].map(id => ({
    id, organization_id: 'org', project_id: 'project', control: false,
    evidence_revision: 'revision',
    refuting_refs: [], missing_evidence: [], supporting_refs: ['report'],
    evidence: [{id: 'report', text: `The agent did not perform the required action for ${id}.`,
      digest: digest(`The agent did not perform the required action for ${id}.`)}],
  }));
  const byId = new Map(rows.map(row => [row.id, row]));
  const issues = rows.slice(1).map(row => ({
    id: `issue-${row.id}`, active: true, scope: scopeKey(row),
    mechanism_revision: 1, mechanism: row.id, fix_hypothesis: row.id,
    falsifier: row.id, members: [row.id], prototypes: [row.id], retrieval_score: 1,
  }));
  const context = {byId, constraints: new Set(), policy: {}};
  const state = {registry: {issues}, targeted_seen: {}, targeted_attempts: {}};
  return {rows, issues, context, state};
}

test('an eligible target omitted from a prompt is still novel on revisit', () => {
  const {issues, context, state} = fixture();
  state.targeted_seen.pending = {
    targets: snapshotTargets('pending', state.registry, context, issues.slice(0, 1)),
  };
  const reviews = rankNovelReviews({findings: ['pending'], state, context,
    index: {}, retrieveIssues: () => issues});
  assert.deepEqual(reviews.map(review => review.novel_ids), [['issue-omitted']]);
});

test('a target actually exposed with unchanged evidence needs no repeat review', () => {
  const {issues, context, state} = fixture();
  state.targeted_seen.pending = {targets: snapshotTargets('pending', state.registry, context, issues)};
  assert.deepEqual(rankNovelReviews({findings: ['pending'], state, context,
    index: {}, retrieveIssues: () => issues}), []);
});

test('unseen fifth-ranked target is exposed before truncation and then reviews terminate', async () => {
  const {context, state, issues} = fixture();
  for (let number = 3; number <= 5; number++) {
    const id = `candidate-${number}`;
    context.byId.set(id, {...context.byId.get('first'), id});
    issues.push({...issues[0], id: `issue-${number}`, members: [id], prototypes: [id]});
  }
  const index = {rows: context.byId,
    similarity: (_a, b) => 1 - issues.findIndex(issue => issue.members.includes(b)) / 10};
  context.policy = {track_exposed_targets: true, candidate_issues: 4,
    max_targeted_reviews: 10, max_input_bytes: 10000};
  state.receipts = [];
  state.targeted_seen.pending = {targets: snapshotTargets('pending', state.registry, context, issues.slice(0, 4))};
  const options = {findings: ['pending'], state, context, index, retrieveIssues};
  assert.deepEqual(retrieveIssues({seed: 'pending', members: ['pending']}, state.registry, index, context.policy)
    .map(issue => issue.id), issues.slice(0, 4).map(issue => issue.id));
  let calls = 0;
  await runTargetedReviews({...options,
    packInvestigation: (selection, candidates) => {
      assert.equal(candidates[0].id, 'issue-5');
      assert.equal(candidates.length, 4);
      return {selection, issues: candidates, shown: new Set(['pending', ...candidates.flatMap(issue => issue.prototypes)]),
        prompt: {instructions: 'Review'}};
    },
    investigate: async () => { calls++; return {groups: [], deferred: [{finding_id: 'pending', reason: 'Different correction'}]}; },
    commit: () => assert.fail('No admission was proposed'), hold: () => {}, save: async () => {},
  });
  assert.equal(calls, 1);
  assert.deepEqual(rankNovelReviews(options), []);
  issues[4].mechanism_revision++;
  assert.deepEqual(rankNovelReviews(options)[0].novel_ids, ['issue-5']);
});

test('simulation revisits every newly exposed target within the total review limit', async () => {
  const {issues, context, state} = fixture();
  context.policy = {track_exposed_targets: true, max_targeted_reviews: 3, max_input_bytes: 10000};
  state.targeted_seen.pending = {targets: {}};
  state.receipts = [];
  const shownTargets = [];
  await runTargetedReviews({findings: ['pending'], state, context, index: {},
    retrieveIssues: () => issues,
    packInvestigation: (selection, candidates) => {
      // Only one issue fits. Both must eventually be shown, without repeating
      // the already-exposed high-ranked target indefinitely.
      const issue = candidates[0];
      shownTargets.push(issue.id);
      return {selection, issues: [issue], shown: new Set(['pending', ...issue.prototypes]),
        prompt: {instructions: 'Review finding'}};
    },
    investigate: async () => ({groups: [], deferred: [{finding_id: 'pending', reason: 'Different correction'}]}),
    commit: () => assert.fail('No admission was proposed'), hold: () => {}, save: async () => {},
  });
  assert.deepEqual(shownTargets, ['issue-first', 'issue-omitted']);
  assert.equal(state.targeted_attempts.pending.count, 2);
  assert.deepEqual(Object.keys(state.targeted_seen.pending.targets).sort(), issues.map(issue => issue.id).sort());
  assert.equal(state.receipts.length, 2);
});

test('simulation targeted reviews stop at the global limit with unseen work still visible', async () => {
  const {issues, context, state} = fixture();
  context.policy = {track_exposed_targets: true, max_targeted_reviews: 1, max_input_bytes: 10000};
  state.targeted_seen.pending = {targets: {}};
  state.receipts = [];
  await runTargetedReviews({findings: ['pending'], state, context, index: {}, retrieveIssues: () => issues,
    packInvestigation: (selection, candidates) => ({selection, issues: [candidates[0]],
      shown: new Set(['pending', ...candidates[0].prototypes]), prompt: {instructions: 'Review'}}),
    investigate: async () => ({groups: [], deferred: [{finding_id: 'pending', reason: 'Insufficient evidence'}]}),
    commit: () => assert.fail('No admission was proposed'), hold: () => {}, save: async () => {},
  });
  assert.equal(state.receipts.length, 1);
  assert.deepEqual(rankNovelReviews({findings: ['pending'], state, context, index: {}, retrieveIssues: () => issues})[0].novel_ids,
    ['issue-omitted']);
});

test('singleton review needs no new target, runs once on unchanged evidence, and respects the review cap', async () => {
  const {context, state} = fixture();
  state.registry.issues = [];
  state.targeted_seen.pending = {targets: {}};
  state.receipts = [];
  context.policy = {track_exposed_targets: true, targeted_singletons: true,
    candidate_issues: 4, max_targeted_reviews: 0, max_input_bytes: 10000};
  let calls = 0;
  const options = {findings: ['pending'], state, context, index: {}, retrieveIssues: () => [],
    packInvestigation: selection => ({selection, issues: [], shown: new Set(['pending']), prompt: {instructions: 'Review'}}),
    investigate: async prompt => {
      calls++;
      assert.ok(prompt.instructions.includes('create a singleton'));
      return {groups: [], deferred: [{finding_id: 'pending', reason: 'Corrective behavior is ambiguous'}]};
    },
    commit: () => assert.fail('No admission was proposed'), hold: () => {}, save: async () => {},
  };
  await runTargetedReviews(options);
  assert.equal(calls, 0);
  context.policy.max_targeted_reviews = 10;
  await runTargetedReviews(options);
  assert.equal(calls, 1);
  assert.equal(state.targeted_attempts.pending.count, 1);
  await runTargetedReviews(options);
  assert.equal(calls, 1, 'Resume must not repeat an unchanged singleton review');
  assert.deepEqual(rankNovelReviews(options), []);
});

test('a supported unmatched singleton is allowed only by the simulation policy', async () => {
  for (const allow of [false, true]) {
    const {context, state, issues} = fixture();
    context.policy = {targeted_singletons: allow, max_targeted_reviews: 1, max_input_bytes: 10000};
    state.targeted_seen.pending = {targets: allow ? snapshotTargets('pending', state.registry, context, issues) : {}};
    state.deferred = {pending: {reason: 'No matching cluster'}};
    state.receipts = [];
    const commands = [], source = context.byId.get('pending').evidence[0];
    await runTargetedReviews({findings: ['pending'], state, context, index: {},
      retrieveIssues: () => issues, packInvestigation,
      investigate: async () => ({groups: [{target_issue_id: null, member_ids: ['pending'],
        title: 'Agent omits the required operation', mechanism: source.text,
        fix_hypothesis: 'Perform the required operation', falsifier: 'The required operation was performed',
        predicted_observations: ['The required operation is absent'], alternatives: ['The report omitted a successful operation'],
        citations: [{finding_id: 'pending', evidence_id: 'report', evidence_digest: source.digest, quote: source.text}],
        contradictions: [], missing_evidence: []}], deferred: []}),
      commit: command => commands.push(command), hold: () => {}, save: async () => {},
    });
    assert.deepEqual(commands.map(command => command.type), allow ? ['create'] : []);
    assert.equal(state.receipts[0].status, allow ? 'complete' : 'incomplete');
    assert.equal('pending' in state.deferred, !allow);
    assert.deepEqual(state.registry.issues, issues, 'Existing issue identity must not change');
  }
});
