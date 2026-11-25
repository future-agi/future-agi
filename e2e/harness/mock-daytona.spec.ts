import { test, expect } from '../lib/fixtures';
import { AGENT_SOURCE_NAME, uploadAgentSource } from '../lib/agent-source';
import { E2E } from '../lib/env';
import { POLL } from '../lib/state-probe';

test.skip(process.env.E2E_STACK === 'standalone', 'mock-daytona and worker-reading run in the distributed E2E stack');

interface Scan {
  id: string;
  status: 'pending' | 'running' | 'found' | 'none' | 'error' | 'failed';
  agents: { name: string; entry: string; env_names: string[] }[];
  failure: { code: string; message: string } | null;
}

test('an uploaded folder is read through mock-daytona and its agent is found', async ({ actor, request }, testInfo) => {
  test.setTimeout(120_000);
  const source = await uploadAgentSource(request, actor);
  expect(source.name).toBe(AGENT_SOURCE_NAME);
  const started = await actor.api.post<{ id: string; status: string }>('/simulate/agents/scan/', {
    kind: 'archive', archive_artifact_id: source.source_id,
  });
  await testInfo.attach('seeded-scan', { contentType: 'application/json',
    body: JSON.stringify({ sourceId: source.source_id, scanId: started.id }) });

  let scan: Scan | undefined;
  await expect.poll(async () => {
    scan = await actor.api.get<Scan>(`/simulate/agents/scan/${started.id}/`);
    return scan.status;
  }, POLL.ASYNC_JOB).toBe('found');

  expect(scan?.agents.map(({ name, entry, env_names }) => ({ name, entry, env_names })))
    .toEqual([{ name: 'support_agent', entry: 'main.py', env_names: ['ACME_API_KEY'] }]);

  const { calls } = await (await request.get(`${E2E.daytonaMockUrl}/calls`)).json() as { calls: string[] };
  expect(calls).toContain('POST /api/sandbox');
  expect(calls.some(call => /^DELETE \/api\/sandbox\/e2e-/.test(call))).toBe(true);
});
