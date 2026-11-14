import { test, expect } from '../lib/fixtures';

// Test-image-only E2EIssueRequest; production never registers this endpoint.
const ISSUE_SEED = '/tracer/e2e/feed-issue/';
// ProjectSerializer / ProjectViewSet.create and FeedListQuerySerializer.
const PROJECT_CREATE = '/tracer/project/';
const FEED_LIST = '/tracer/feed/issues/';
type Seed = { issue_id: string; cluster_id: string };
type Times = { id: string; project_id: string; title: string; first_seen: Date | null; last_seen: Date | null };
type Feed = { result: { total: number; data: { cluster_id: string; error: { name: string } }[] } };

test('issue fixture is visible in the real feed time window and updates preserve its observation times', async ({ actor, probe }, testInfo) => {
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-feed-seed-${suffix}`;
  const title = `${name}-original`;
  const updatedTitle = `${name}-updated`;
  const created = await actor.api.post<{ result: { project_id: string } }>(PROJECT_CREATE, {
    name, trace_type: 'observe', model_type: 'GenerativeLLM',
  });
  const projectId = created.result.project_id;
  const seeded = await actor.api.post<Seed>(ISSUE_SEED, { project_id: projectId, title });
  await testInfo.attach('seeded-issue', { contentType: 'application/json', body: JSON.stringify({
    organizationId: actor.organizationId, workspaceId: actor.workspaceId, projectId, ...seeded,
  }) });
  const readStored = () => probe.pg<Times>(
    'SELECT id, project_id, title, first_seen, last_seen FROM tracer_trace_error_group WHERE id=$1', [seeded.issue_id]);
  const before = await readStored();
  expect(before).toHaveLength(1);
  expect(before[0]).toMatchObject({ id: seeded.issue_id, project_id: projectId, title });
  expect(before[0].first_seen).not.toBeNull();
  expect(before[0].last_seen).toEqual(before[0].first_seen);
  // The default Error Feed UI sends this same seven-day filter. Null timestamps
  // used to leave a successfully seeded issue permanently invisible in that UI.
  const list = await actor.api.get<Feed>(FEED_LIST, { project_id: projectId, time_range_days: 7 });
  expect(list.result.total).toBe(1);
  expect(list.result.data.map(row => [row.cluster_id, row.error.name])).toEqual([[seeded.cluster_id, title]]);

  const updated = await actor.api.post<Seed>(ISSUE_SEED, {
    project_id: projectId, issue_id: seeded.issue_id, title: updatedTitle, status: 'acknowledged',
  });
  expect(updated).toEqual(seeded);
  expect(await readStored()).toEqual([{ ...before[0], title: updatedTitle }]);
  const after = await actor.api.get<Feed>(FEED_LIST, { project_id: projectId, time_range_days: 7 });
  expect(after.result.total).toBe(1);
  expect(after.result.data.map(row => [row.cluster_id, row.error.name])).toEqual([[seeded.cluster_id, updatedTitle]]);
});
