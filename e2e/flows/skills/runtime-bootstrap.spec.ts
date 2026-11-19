import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';

test('SKILLS-E2E-131: runtime reads work on a newly bootstrapped standalone stack', {
  tag: '@flow', annotation: flowAnnotation({
    id: 'SKILLS-E2E-131', area: 'skills', userGoal: 'Open Runtime before the first skill load without installing a schema by hand.',
    steps: ['Publish a skill and send an ordinary trace in a new project.', 'Request runtime usage for that skill and project.'],
    backendChecks: ['The Runtime endpoint returns a zero-trace summary.', 'Both load tables and their materialized views exist.'],
  }),
}, async ({ actor, request, probe }) => {
  test.skip(process.env.E2E_STACK !== 'standalone', 'Exercises the standalone bootstrap.');
  const name = `first-load-${randomUUID().slice(0, 8)}`;
  const skill = await actor.api.post<any>('/model-hub/skills/publish/', {
    files: [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Read the first receipt.\n---\nRead the receipt.\n` }],
    labels: ['production'], expected_release_revision: 0, acknowledge_warnings: true,
  });
  const { traceId } = await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName: name });
  const projects = () => probe.ch<{ project_id: string }>('SELECT DISTINCT project_id FROM spans WHERE trace_id = {t:String}', { t: traceId });
  await expect.poll(projects, POLL.SPAN_VISIBLE).toHaveLength(1);
  const now = Date.now();
  const usage = await actor.api.get<any>('/model-hub/skills/usage/', { from: new Date(now - 3600000).toISOString(), to: new Date(now).toISOString(),
    project_ids: (await projects())[0].project_id, resource_id: skill.result.skill.id });
  expect(usage.result.summary.traces).toBe(0);
  const tables = await probe.ch<{name: string}>("SELECT name FROM system.tables WHERE database = currentDatabase() AND name IN ('skill_trace_links', 'skill_load_failures', 'skill_trace_links_mv', 'skill_load_failures_mv') ORDER BY name");
  expect(tables.map(row => row.name)).toEqual(['skill_load_failures', 'skill_load_failures_mv', 'skill_trace_links', 'skill_trace_links_mv']);
});
