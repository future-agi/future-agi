import { randomUUID } from 'node:crypto';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { fetchRoute } from '../../lib/backend-request';
import { flowAnnotation } from '../../lib/flow-meta';
import { exactPath } from '../../lib/url';
import {
  CHANGES,
  ENV_ID,
  NAMED,
  NAMED_KEY,
  UNNAMED_KEY,
  checkRunCalls,
  readyEpoch,
  type Ids,
} from './epoch-change-and-scenario-names.payload';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license) and no simulation runner, so it cannot hold an epoch or its
// check runs. Stub only the epoch's read and its check runs' calls, as SIM-E2E-008
// and ERR-E2E-006 do. The stubs live in epoch-change-and-scenario-names.payload.ts,
// which the frontend suite parses with the generated contract (epochNamesStubs.test.js),
// so they cannot drift from the API. The epoch page, its tabs and the requests it
// sends are the product's own.
// Known limit: this proves the page shows the read's words, not that the server
// counts a patch's lines or stores a scenario's name. That is
// futureagi/simulate/tests/test_agent_fix_wording.py (TestScenarioNamesOnThePage and
// the change-line cases) and test_agent_fix_api.py.

// Browser-side waits: one navigation's or one click's first paint.
const UI_READY = 60_000;

// What the Scenarios tab says for a scenario the read names nothing for
// (UNNAMED_SCENARIO in improvements/scenarioOutcomes.js).
const UNNAMED = 'Unnamed scenario';

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubApi(page: Page, ids: Ids) {
  const askedCalls: string[] = [];
  // The real capabilities, with Epoch switched on.
  await page.route(endsWith('/api/capabilities/'), async (route: Route) => {
    const response = await fetchRoute(route);
    const json = await response.json();
    json.features = { ...json.features, fix_my_agent: { ...json.features?.fix_my_agent, allowed: true } };
    await route.fulfill({ response, json });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${ids.epoch}/`), (route) =>
    route.fulfill({ json: readyEpoch(ids) }),
  );
  for (const executionId of [ids.baseExecution, ids.candidateExecution]) {
    await page.route(endsWith(`/simulate/v3/test-executions/${executionId}/calls/`), (route) => {
      askedCalls.push(executionId);
      return route.fulfill({ json: checkRunCalls(ids, executionId) });
    });
  }
  return askedCalls;
}

test('SIM-E2E-041: an Epoch page shows each candidate\'s change and each scenario by its stored name', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-041', area: 'simulate',
    userGoal: 'A developer opens a finished Epoch and reads what each candidate changes, and each scenario by the name it was stored under, never its key',
    steps: ['open a finished epoch\'s page', 'read the winner\'s row on the Candidates tab', 'open the Scenarios tab'],
    browserChecks: [
      'the winner\'s What it changes cell says the read\'s change line, not the engine\'s note',
      'a scenario whose key differs from its stored name reads the stored name',
      'a scenario with no stored name reads Unnamed scenario',
      'no scenario row shows a scenario key',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 3 browser waits, each its own UI_READY: 3 × 60s = 180s, plus 60s headroom
  // so a slow run fails on the wait that ran out.
  test.setTimeout(240_000);
  const ids: Ids = {
    epoch: randomUUID(),
    candidate: randomUUID(),
    baseRun: randomUUID(),
    baseExecution: randomUUID(),
    candidateRun: randomUUID(),
    candidateExecution: randomUUID(),
  };
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, ...ids }),
    contentType: 'application/json',
  });
  const askedCalls = await stubApi(page, ids);

  await test.step('ui: check 1, the winner says what its patch changes', async () => {
    await page.goto(`/dashboard/simulate/environments/${ENV_ID}/improvements/${ids.epoch}`);
    await expect(page).toHaveURL(
      exactPath(`/dashboard/simulate/environments/${ENV_ID}/improvements/${ids.epoch}`), { timeout: UI_READY },
    );
    // The row's What it changes cell (data-testid="candidate-changes" in CandidatesTable.tsx).
    const changes = page.getByTestId('candidate-1').getByTestId('candidate-changes');
    await expect(changes).toHaveText(CHANGES, { timeout: UI_READY });
  }, { timeout: UI_READY });

  const scenarios = page.getByRole('table', { name: 'Scenarios' });
  const nameOf = (key: string) => page.getByTestId(`scenario-${key}`).getByRole('cell').first();
  await test.step('ui: check 2, each scenario reads by its stored name', async () => {
    await page.getByRole('tab', { name: 'Scenarios', exact: true }).click();
    await expect(nameOf(NAMED_KEY)).toHaveText(NAMED, { timeout: UI_READY });
    expect(askedCalls).toEqual(expect.arrayContaining([ids.baseExecution, ids.candidateExecution]));
  }, { timeout: UI_READY });

  await test.step('ui: check 3, a scenario with no stored name is Unnamed, and no key shows', async () => {
    await expect(nameOf(UNNAMED_KEY)).toHaveText(UNNAMED, { timeout: UI_READY });
    // Exactly these two rows, in the read's order: no other row, no key.
    await expect(scenarios.locator('tbody tr > td:first-child')).toHaveText([NAMED, UNNAMED], { timeout: UI_READY });
    await expect(scenarios).not.toContainText(NAMED_KEY, { timeout: UI_READY });
    await expect(scenarios).not.toContainText(UNNAMED_KEY, { timeout: UI_READY });
  }, { timeout: UI_READY });
});
