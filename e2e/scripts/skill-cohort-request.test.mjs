import assert from 'node:assert/strict';
import test from 'node:test';
import { isSkillCohortRequest } from '../lib/skill-cohort-request.ts';

const target = { projectId: 'project-1', versionId: 'version-1' };
const versionFilter = { column_id: 'skill.version_id', filter_config: {
  col_type: 'SKILL', filter_type: 'text', filter_op: 'equals', filter_value: 'version-1' } };
const cohort = { projection: 'cohort', project_id: 'project-1', from: '2026-11-27T00:00:00Z',
  to: '2026-11-28T00:00:00Z', filters: [versionFilter] };
const COHORT = '/tracer/trace/skill-projection/';

test('the trace list answering first neither matches nor throws (it sends filters as a JSON string)', () => {
  const list = { project_id: 'project-1', filters: JSON.stringify([versionFilter]), page_number: 0 };
  // Seen in this order on the page: the list, then the cohort projection.
  const seen = [['POST', '/tracer/trace/list_traces_of_session/', list], ['POST', COHORT, cohort]];
  assert.deepEqual(seen.map(([method, path, body]) => isSkillCohortRequest(method, path, body, target)), [false, true]);
  // Even a cohort-path request with string filters is just not a match.
  assert.equal(isSkillCohortRequest('POST', COHORT, { ...cohort, filters: JSON.stringify([versionFilter]) }, target), false);
});

test('only the cohort projection for this project, window and version matches', () => {
  const cases = [
    ['GET', COHORT, cohort], ['POST', '/tracer/trace/other/', cohort], ['POST', COHORT, null],
    ['POST', COHORT, 'text'], ['POST', COHORT, { ...cohort, projection: 'split' }],
    ['POST', COHORT, { ...cohort, project_id: 'project-2' }], ['POST', COHORT, { ...cohort, from: 1 }],
    ['POST', COHORT, { ...cohort, from: cohort.to, to: cohort.from }],
    ['POST', COHORT, { ...cohort, filters: [{ ...versionFilter, group: 1 }] }],
    ['POST', COHORT, { ...cohort, filters: [{ ...versionFilter, filter_config: { ...versionFilter.filter_config, filter_value: 'version-2' } }] }],
    ['POST', COHORT, { ...cohort, filters: [null, 'x', { column_id: 'skill.version_id' }] }],
  ];
  for (const [method, path, body] of cases) assert.equal(isSkillCohortRequest(method, path, body, target), false);
  assert.equal(isSkillCohortRequest('POST', `/api${COHORT}`, { ...cohort, filters: [{}, versionFilter] }, target), true);
});
