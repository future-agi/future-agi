import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// The readiness panel is on the run readiness screen (TH-8877); until it ships this flow drives the API the run dialog
// reads and the refusal it shows in place (runErrors.js INLINE_ERRORS: all_cases_blocked). Paths from
// futureagi/simulate/views/harness_environment.py (HarnessEnvironmentViewSet: capabilities, run).
const ENVIRONMENTS = '/simulate/api/harness-environments';
const REFUNDS = 2;
const LOOKUPS = 2;

interface WorkerRow { worker_id: string; name: string; status: string }
interface HeldBy { id: string; count: number; reason: string | null }
interface Report { workers: WorkerRow[]; selection: { eligible: number; held_by: HeldBy[] } }
interface Refusal { error: string; message: string }

test('SIM-E2E-034: a worker whose limits nothing holds keeps back the cases relying on it, and the run refusal names it', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-034', area: 'simulate',
    userGoal: 'A developer testing a multi-agent app who selects only the cases a blocked worker holds back is told '
      + 'which worker holds them and why, instead of a refusal that names nothing',
    steps: [`seed a certified Environment with two separately wired workers whose tool service ALK sealed outside the `
              + `world's door, ${REFUNDS} refund cases that give the refund worker a budget (each sealed held in `
              + `worker-limits.json) and ${LOOKUPS} lookups (e2e/lib/harness_environment_seed.py)`,
            'read the pairing\'s readiness (GET capabilities)',
            'start a run of only the refund cases (POST run)'],
    backendChecks: [`readiness counts ${REFUNDS} cases held back by the refund worker and ${LOOKUPS} eligible`,
                    'the run is refused all_cases_blocked, and the message names the Refund worker with the held reason'],
  }),
}, async ({ actor }, testInfo) => {
  // One seed through the backend container (Django start-up) and two API calls.
  test.setTimeout(240_000);
  const scope = { organizationId: actor.organizationId, workspaceId: actor.workspaceId };
  const seed = seedHarnessEnvironment({ ...scope, refunds: REFUNDS, lookups: LOOKUPS, workers: 'not_fronted', refundWorkerBudget: true });
  await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ ...scope, ...seed }) });
  let held: HeldBy | undefined;

  await test.step('api: the refund worker is blocked and holds back the refund cases', async () => {
    const report = await actor.api.get<Report>(`${ENVIRONMENTS}/${seed.environmentId}/capabilities/`);
    expect(report.workers.find((one) => one.worker_id === 'refund')?.status).toBe('blocked');
    expect([report.selection.held_by.map((one) => [one.id, one.count]), report.selection.eligible]).toEqual([[['worker:refund', REFUNDS]], LOOKUPS]);
    held = report.selection.held_by[0];
  });

  await test.step('api: a run of only the refund cases is refused naming the Refund worker', async () => {
    const { status, body } = await actor.api.request<Refusal>('post', `${ENVIRONMENTS}/${seed.environmentId}/run/`, {
      data: { scenario_ids: seed.refundKeys, trials: 1 },
      headers: { 'Idempotency-Key': `sim-e2e-021-${seed.environmentId}` },
    });
    expect([status, body.error]).toEqual([409, 'all_cases_blocked']);
    expect(body.message).toContain(`(Refund worker: ${held?.reason})`);
  });
});
