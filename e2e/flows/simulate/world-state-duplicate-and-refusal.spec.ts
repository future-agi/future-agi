import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// Browser-side waits: one navigation's first paint, not a stage.
const UI_READY = 60_000;
// Path from futureagi/simulate/urls.py (v3/call-executions).
const CALLS = '/simulate/v3/call-executions';
// The world actor e2e/lib/harness_environment_seed.py seeds: it rules on the agent's refund approvals.
const ACTOR = { id: 'claims_supervisor', name: 'Dana Brooks' };

interface Decision { actor: string; name: string; verdict: string | null; committed_after_refusal: boolean }
interface CallDetail { trial_state: { duplicates?: { action: string }[]; decisions?: Decision[] } | null }

const trialPage = (seed: { environmentId: string; runTestId?: string; testExecutionId?: string;
  callExecutionId?: string }) => `/dashboard/simulate/environments/${seed.environmentId}/runs/${seed.runTestId}`
  + `/${seed.testExecutionId}?rowId=${seed.callExecutionId}`;

test('SIM-E2E-021: World state flags a write committed twice', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-021', area: 'simulate',
    userGoal: 'A developer sees that the world committed the agent\'s refund twice after its first answer was lost, '
      + 'said apart from a single commit',
    steps: ['seed a trial whose refund the world committed twice (e2e/lib/harness_environment_seed.py)',
            'read the trial (GET v3/call-executions/<id>)', 'open its World state'],
    backendChecks: ['the trial\'s World state lists issue_refund among the writes committed twice'],
    browserChecks: ['World state reads "Committed twice: issue_refund" and counts 1 duplicate'],
  }),
}, async ({ page, actor }) => {
  test.setTimeout(240_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, actors: true, trial: 'duplicate' });

  await test.step('api: the trial lists the write committed twice', async () => {
    const call = await actor.api.get<CallDetail>(`${CALLS}/${seed.callExecutionId}/`);
    expect(call.trial_state?.duplicates?.map((one) => one.action)).toEqual(['issue_refund']);
  });

  await test.step('browser: World state flags the duplicate commit', async () => {
    await page.goto(trialPage(seed));
    await page.getByRole('tab', { name: 'World state' }).click({ timeout: UI_READY });
    await expect(page.getByText('Committed twice: issue_refund')).toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(/1 duplicate/)).toBeVisible();
  });
});

test('SIM-E2E-022: World state says an approval was refused and the agent committed anyway', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-022', area: 'simulate',
    userGoal: 'A developer reads that an approver refused the agent\'s request and that the agent committed the '
      + 'change after the refusal, never that the agent waited for the decision and stopped',
    steps: ['seed a trial where Dana Brooks refused the refund and the agent issued it anyway '
              + '(e2e/lib/harness_environment_seed.py)',
            'read the trial (GET v3/call-executions/<id>)', 'open its World state'],
    backendChecks: ['the trial\'s decision by Dana Brooks is refused and flagged committed after the refusal'],
    browserChecks: ['her decision reads Refused',
                    'a line says the agent committed the change after Dana Brooks refused it',
                    'the approval names the refund the agent wrote after the decision'],
  }),
}, async ({ page, actor }) => {
  test.setTimeout(240_000);
  const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    refunds: 1, lookups: 1, actors: true, trial: 'refused' });

  await test.step('api: the decision is refused and flagged committed after it', async () => {
    const call = await actor.api.get<CallDetail>(`${CALLS}/${seed.callExecutionId}/`);
    expect(call.trial_state?.decisions?.find((one) => one.actor === ACTOR.id)).toMatchObject({
      name: ACTOR.name, verdict: 'refused', committed_after_refusal: true,
    });
  });

  await test.step('browser: World state says Refused and that the agent committed after it', async () => {
    await page.goto(trialPage(seed));
    await page.getByRole('tab', { name: 'World state' }).click({ timeout: UI_READY });
    await expect(page.getByText(/Refused/).first()).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('committed-after-refusal')).toHaveText(
      `The agent committed the change after ${ACTOR.name} refused it`);
    await expect(page.getByText(/then the agent wrote: issue_refund \(committed\)/)).toBeVisible();
  });
});
