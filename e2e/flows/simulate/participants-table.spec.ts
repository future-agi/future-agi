import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedParticipantsCall } from '../../lib/simulate-seed';

interface WorkersResponse {
  turns: { turn: number }[];
  steps: { kind: string; relationship: string; turn: number; order: number; worker_id: string | null }[];
}

test('SIM-E2E-009: a multi-agent call serves its workers turn by turn, an unplaced step as unknown, and a stopped call its cancelled worker', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-009', area: 'simulate',
    userGoal: 'A developer reads which worker did what in each turn of a multi-agent call, what the record could not place, and how a stopped call left its workers',
    steps: [
      'seed one completed chat call with a stored Participants record, and a call the user stopped',
      'read the completed call\'s workers',
      'read the stopped call\'s workers',
    ],
    backendChecks: [
      'the workers API serves two turns, each step with its turn and order, and the second hand-off as relationship unknown',
      'the stopped call serves its refund worker as cancelled, stopped with the run sandbox',
    ],
  }),
}, async ({ actor }, testInfo) => {
  const seed = seedParticipantsCall({ organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await testInfo.attach('participants-seed', { contentType: 'application/json', body: JSON.stringify(seed) });

  await test.step('api: the record is served as two turns, the unplaced hand-off as unknown', async () => {
    const served = await actor.api.get<WorkersResponse>(`/simulate/v3/call-executions/${seed.callExecutionId}/workers/`);
    expect(served.turns.map((one) => one.turn)).toEqual([1, 2]);
    const handOffs = served.steps.filter((one) => one.kind === 'handoff');
    expect(handOffs.map((one) => [one.turn, one.order, one.relationship])).toEqual([[1, 1, 'observed'], [2, 1, 'unknown']]);
  });

  await test.step('api: the stopped call serves its cancelled worker with the sandbox cleanup', async () => {
    const served = await actor.api.get<{ workers: { id: string; state: string; cleanup?: { status: string } }[] }>(
      `/simulate/v3/call-executions/${seed.cancelledCallExecutionId}/workers/`,
    );
    const refund = served.workers.find((one) => one.id === 'refund');
    expect([refund?.state, refund?.cleanup?.status]).toEqual(['cancelled', 'stopped_with_sandbox']);
  });
});
