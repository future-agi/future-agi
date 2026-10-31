import test from 'node:test';
import assert from 'node:assert/strict';
import {scopeKey} from './common.mjs';
import {retrieveIssues} from './pipeline.mjs';

function fixture(ids, issueSpecs, scores) {
  const rows = new Map(ids.map(id => [id, {id, organization_id: 'org', project_id: 'project', control: false}]));
  const scope = scopeKey(rows.get(ids[0]));
  const issues = issueSpecs.map(([id, members, prototypes = members]) => ({id, active: true, scope, members, prototypes}));
  const similarity = (a, b) => {
    assert.ok(rows.has(a) && rows.has(b), `no embedding row for ${a} or ${b}`);
    return a === b ? 1 : scores[`${a}|${b}`] ?? scores[`${b}|${a}`] ?? 0.1;
  };
  return {registry: {issues}, index: {rows, similarity}, policy: {candidate_issues: 4}};
}

test('assigned cohort neighbours do not tie their own issues ahead of the matching issue', () => {
  // Each assigned neighbour matches its own issue at similarity 1; those issues sort first by id.
  const neighbours = ['n1', 'n2', 'n3', 'n4'];
  const {registry, index, policy} = fixture(['pending', ...neighbours, 'match'],
    [...neighbours.map((id, i) => [`issue-${'abcd'[i]}`, [id]]), ['issue-match', ['match']]],
    {'pending|match': 0.9, ...Object.fromEntries(neighbours.map(id => [`pending|${id}`, 0.2]))});
  const ranked = retrieveIssues({seed: 'pending', members: ['pending', ...neighbours]}, registry, index, policy);
  assert.equal(ranked[0].id, 'issue-match');
  assert.equal(ranked[0].retrieval_score, 0.9);
  assert.equal(ranked.length, 4);
});

test('issue members beyond the prototypes count toward retrieval', () => {
  const {registry, index, policy} = fixture(['pending', 'p', 'm', 'q'],
    [['issue-x', ['p', 'm'], ['p']], ['issue-y', ['q']]], {'pending|m': 0.8, 'pending|q': 0.5});
  const ranked = retrieveIssues({seed: 'pending', members: ['pending']}, registry, index, policy);
  assert.deepEqual(ranked.map(issue => [issue.id, issue.retrieval_score]), [['issue-x', 0.8], ['issue-y', 0.5]]);
});

test('members without an embedding row are skipped and a fully assigned cohort scores from its seed', () => {
  const {registry, index, policy} = fixture(['seed', 'p', 'q'],
    [['issue-x', ['p', 'gone'], ['p']], ['issue-y', ['q', 'seed']]], {'seed|p': 0.7, 'seed|q': 0.3});
  const ranked = retrieveIssues({seed: 'seed', members: ['seed']}, registry, index, policy);
  assert.deepEqual(ranked.map(issue => [issue.id, issue.retrieval_score]), [['issue-y', 1], ['issue-x', 0.7]]);
});
