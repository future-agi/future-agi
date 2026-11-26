import { randomUUID } from 'node:crypto';
import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { fetchRoute } from '../../lib/backend-request';
import { flowAnnotation } from '../../lib/flow-meta';
import { exactPath } from '../../lib/url';
import {
  AGENT_NOTE,
  ATTEMPT_TITLE,
  CHECK_ROWS,
  COPIED,
  ENDING_SENTENCE,
  ENGINE_STATIC_DETAIL,
  ENGINE_TARGET_DETAIL,
  ENV_ID,
  HELD_BACK_KEYS,
  attemptEpoch,
  attemptVerdict,
  codingAgentBrief,
  copiedPrompt,
  type Ids,
} from './coding-agent-attempt.payload';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license), no runner and no AGENT_FIX_OUTSIDE_ATTEMPTS_ENABLED, so it
// cannot hold an attempt a coding agent submitted. Stub only the Epoch-side reads,
// as SIM-E2E-041 does: the capabilities gate, the attempt's Epoch read, its verdict
// read and the coding-agent brief. The stubs live in coding-agent-attempt.payload.ts,
// which the frontend suite parses with the generated contract
// (codingAgentFlowStubs.test.ts), so they cannot drift from the API. The Epoch page,
// its panel, its ending and the requests it sends are the product's own.
// Known limit: this proves the page shows the reads' words and copies the brief, not
// that the server writes the brief or keeps held-back cases out of the verdict. That
// is futureagi/simulate/tests/test_agent_fix_outside.py.

// Browser-side waits: one navigation's or one click's first paint.
const UI_READY = 60_000;

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubApi(page: Page, ids: Ids) {
  const briefRuns: string[] = [];
  // The real capabilities, with Epoch and coding-agent attempts switched on
  // (useCodingAgentAttemptsEnabled in frontend/src/api/simulate-environments/codingAgent.ts).
  await page.route(endsWith('/api/capabilities/'), async (route: Route) => {
    const response = await fetchRoute(route);
    const json = await response.json();
    json.features = { ...json.features, fix_my_agent: { ...json.features?.fix_my_agent, allowed: true } };
    json.coding_agent_attempts_enabled = true;
    await route.fulfill({ response, json });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${ids.attempt}/`), (route) =>
    route.fulfill({ json: attemptEpoch(ids) }),
  );
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${ids.attempt}/verdict/`), (route) =>
    route.fulfill({ json: attemptVerdict(ids) }),
  );
  // endpoints.agentFixRuns.codingAgentBrief in frontend/src/utils/axios.js.
  await page.route(endsWith('/simulate/api/agent-fix-runs/coding-agent-brief/'), (route) => {
    briefRuns.push(new URL(route.request().url()).searchParams.get('source_run_id') ?? '');
    return route.fulfill({ json: codingAgentBrief(ids) });
  });
  return briefRuns;
}

test('SIM-E2E-042: a coding agent\'s attempt reads its checks in plain words on its Epoch page, and goes on only through Copy for coding agent', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-042', area: 'simulate',
    userGoal: 'A developer opens the Epoch page of an attempt their own coding agent sent, reads Future AGI\'s checks on the change in plain words, and copies the run\'s brief back to the coding agent as the one way on',
    steps: [
      'open the Epoch page of the coding agent\'s attempt',
      'read the attempt\'s checks',
      'click Copy for coding agent on the ending',
    ],
    browserChecks: [
      'the page marks the Epoch as Attempt 2 from your coding agent, with the agent\'s own note',
      'each check reads in plain words from the attempt\'s verdict, in order, and the engine\'s words and the held-back cases never show',
      'the ending says what the coding agent does next, and its only button is Copy for coding agent: no Retry, no Run Epoch, no What to try next',
      'the click reads the brief for the attempt\'s run once, puts the setup and the brief\'s instructions exactly on the clipboard, and says Copied',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 4 browser waits, each its own UI_READY: 4 × 60s = 240s, plus 60s headroom
  // so a slow run fails on the wait that ran out.
  test.setTimeout(300_000);
  const ids: Ids = {
    attempt: randomUUID(),
    sourceRun: randomUUID(),
    attemptOf: randomUUID(),
    candidate: randomUUID(),
  };
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, ...ids }),
    contentType: 'application/json',
  });
  const briefRuns = await stubApi(page, ids);
  const epochPath = `/dashboard/simulate/environments/${ENV_ID}/improvements/${ids.attempt}`;
  const panel = page.getByTestId('outside-attempt');

  await test.step('ui: check 1, the Epoch is the coding agent\'s attempt', async () => {
    await page.goto(epochPath);
    await expect(page).toHaveURL(exactPath(epochPath), { timeout: UI_READY });
    await expect(panel.getByText(ATTEMPT_TITLE, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(panel.getByText(AGENT_NOTE, { exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 2, its checks read in plain words, none of the engine\'s', async () => {
    // Each row is a CheckLine (data-check in improvements/CheckLine.jsx); its text is
    // its label, then what it says.
    const rows = panel.getByTestId('outside-attempt-checks').locator(':scope > [data-check]');
    await expect(rows).toHaveText(CHECK_ROWS.map((r) => `${r.label}${r.says}`), { timeout: UI_READY });
    for (const [i, row] of CHECK_ROWS.entries()) {
      await expect(rows.nth(i)).toHaveAttribute('data-check', row.check, { timeout: UI_READY });
    }
    for (const hidden of [ENGINE_STATIC_DETAIL, ENGINE_TARGET_DETAIL, ...HELD_BACK_KEYS]) {
      await expect(panel).not.toContainText(hidden, { timeout: UI_READY });
    }
  }, { timeout: UI_READY });

  const ending = page.getByTestId('epoch-ending');
  await test.step('ui: check 3, the ending\'s one way on is Copy for coding agent', async () => {
    await expect(ending.getByText(ENDING_SENTENCE, { exact: true })).toBeVisible({ timeout: UI_READY });
    // Exactly one button: an engine Epoch with no safe improvement would add What to
    // try next's See the candidates, How it was decided and Run a new epoch.
    await expect(ending.getByRole('button')).toHaveText(['Copy for coding agent'], { timeout: UI_READY });
    const finished = page.getByTestId('fix-finished-view');
    await expect(finished.getByTestId('what-to-try-next')).toHaveCount(0);
    await expect(finished.getByRole('button', { name: /Retry|Run Epoch|Run a new epoch/ })).toHaveCount(0);
    // The page reads no brief until asked: each copy reads it fresh.
    expect(briefRuns).toEqual([]);
  }, { timeout: UI_READY });

  await test.step('ui: check 4, the copy reads the run\'s brief and copies its instructions', async () => {
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.getByTestId('attempt-copy-for-coding-agent').click();
    await expect(page.getByText(COPIED, { exact: true })).toBeVisible({ timeout: UI_READY });
    expect(briefRuns).toEqual([ids.sourceRun]);
    const origin = await page.evaluate(() => window.location.origin);
    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()), { timeout: UI_READY })
      .toBe(copiedPrompt(ids, origin));
  }, { timeout: UI_READY });
});
