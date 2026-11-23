import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedParticipantsCall } from '../../lib/simulate-seed';

interface EnvironmentDetail {
  contract?: { workers?: { id: string; tools_shown: string[]; binding: string }[] };
}

test('SIM-E2E-011: the environment serves its declared workers, and a worker the harness stopped at its deadline is served so', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-011', area: 'simulate',
    userGoal: 'A developer reads which workers the app declares before a run, and after a run that a worker past its deadline was stopped, not only refused',
    steps: [
      'seed an Environment whose contract declares two workers, with a call whose refund worker the harness stopped at its deadline',
      'read the environment detail',
      'read that call\'s workers',
    ],
    backendChecks: [
      'the environment detail serves both declared workers with their binding and the tools each may use',
      'the workers API serves the stopped worker as past its deadline with cleanup stopped',
    ],
  }),
}, async ({ actor }, testInfo) => {
  const seed = seedParticipantsCall({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('participants-seed', { contentType: 'application/json', body: JSON.stringify(seed) });

  await test.step('api: the environment detail serves the declared workers', async () => {
    const detail = await actor.api.get<EnvironmentDetail>(`/simulate/api/harness-environments/${seed.environmentId}/`);
    const workers = detail.contract?.workers ?? [];
    expect(workers.map((one) => [one.id, one.binding])).toEqual([['order_lookup', 'separate'], ['refund', 'shared']]);
    expect(workers.map((one) => one.tools_shown)).toEqual([['get_order', 'list_refunds'], ['list_refunds', 'issue_refund']]);
  });

  await test.step('api: the stopped worker is served past its deadline with cleanup stopped', async () => {
    const served = await actor.api.get<{ workers: { id: string; state?: string; cleanup?: { status: string } }[] }>(
      `/simulate/v3/call-executions/${seed.stoppedWorkerCallExecutionId}/workers/`,
    );
    const refund = served.workers.find((one) => one.id === 'refund');
    expect([refund?.state, refund?.cleanup?.status]).toEqual(['deadline_passed', 'stopped']);
  });
});
