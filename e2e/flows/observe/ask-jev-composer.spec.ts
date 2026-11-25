import { request } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../../lib/licensed-customer';
import type { TestActor } from '../../lib/provisioning';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// README's browser first-paint ceiling, not the default 10-second expect budget.
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

// INSTANT_EVAL_CONFIRM_ROWS=3 on the test backend (e2e/stack/backend/settings_e2e.py):
// four traces is one past the confirm size, so Ask restates the read first.
const TRACES = 4;
const CONFIRM_ROWS = 3;
// useInstantEval.js estimateAsk / startAskRun (tracer/views/instant_eval_scale.py).
const ESTIMATE = '/tracer/instant-eval/estimate/';
const START = '/tracer/instant-eval/start/';
// src/utils/axios.js endpoints.develop.apiKey.status / .create.
const PROVIDER_STATUS = '/model-hub/develops/provider-status/';
const API_KEYS = '/model-hub/api-keys/';
// The body AIProviders.jsx (KeyCard.jsx handleFormSubmit) posts for a first
// TypeSafe key, captured with page.on('request') from the running app:
// {"provider":"typesafe","key":"e2e-decision-typesafe-key","has_key":false}.
// e2e/stack/mock-llm/server.mjs accepts this TypeSafe key only.
const TYPESAFE_KEY_BODY = { provider: 'typesafe', key: 'e2e-decision-typesafe-key', has_key: false };
// page/askExamples.js EXAMPLES.traces: two yes/no examples under "Try one".
const EXAMPLES = [
  'Is the customer frustrated or upset?',
  'Did the agent promise a refund without checking the order first?',
];
// InstantEval/EvaluateMenu.jsx action title on the Traces view.
const ASK_A_QUESTION = /Ask a question/;
// page/AskComposer.jsx: the composer's own confirm, for a run past confirm_rows
// with no credits billed (self-hosted).
const CONFIRM_TEXT = new RegExp(
  `^Read ${TRACES} traces for 3 questions\\? It takes about .+\\. You can pause or cancel it while it runs\\.$`);

interface Estimate { result: { population: number; rows_to_read: number; needs_confirm: boolean; confirm_rows: number } }
interface Started { result: { task_id: string; rows_requested: number; population: number } }
interface ProviderStatus { result: { providers: { provider: string; has_key: boolean }[] } }

/** Saves the org's TypeSafe key the way AI Providers does, unless a flow
 * earlier in this worker already saved it (the licensed actor is shared). */
async function ensureTypesafeKey(actor: TestActor): Promise<void> {
  const typesafe = async () => (await actor.api.get<ProviderStatus>(PROVIDER_STATUS))
    .result.providers.filter(p => p.provider === TYPESAFE_KEY_BODY.provider);
  if (!(await typesafe())[0]?.has_key) await actor.api.post(API_KEYS, TYPESAFE_KEY_BODY);
  expect(await typesafe()).toEqual([expect.objectContaining({ has_key: true })]);
}

/** Where the Ask Jev row sits in its scrolling pane: how many elements follow
 * it there, and whether the pane overflows (else "pinned" proves nothing). */
function placement(actions: Locator) {
  return actions.evaluate(row => {
    let pane = row.parentElement;
    while (pane && !/(auto|scroll)/.test(getComputedStyle(pane).overflowY)) pane = pane.parentElement;
    if (!pane) return null;
    const following = Array.from(pane.querySelectorAll('*')).filter(el => !row.contains(el)
      && (row.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING)).length;
    return {
      following,
      overflows: pane.scrollHeight > pane.clientHeight,
      gapBelow: Math.round(pane.getBoundingClientRect().bottom - row.getBoundingClientRect().bottom),
    };
  });
}

/** The row's box lies wholly inside the viewport. */
async function expectOnScreen(page: Page, actions: Locator): Promise<void> {
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  await expect.poll(async () => {
    const box = await actions.boundingBox();
    return box && box.y >= 0 && box.y + box.height <= viewport!.height
      ? 'on screen' : `off screen: ${JSON.stringify(box)} in ${JSON.stringify(viewport)}`;
  }, { timeout: UI_READY }).toBe('on screen');
}

test('OBS-E2E-064: Ask Jev stays in reach as example questions are added, and the cost confirm has one primary action', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-064', area: 'observe',
    userGoal: 'Write a question for Jev, add example questions without losing the Ask button, confirm the read, and start the run',
    steps: [
      'send 4 traces to a new Observe project',
      'save the org TypeSafe key through the endpoint AI Providers uses',
      'open the project\'s Traces view, its Evaluate menu, and choose Ask a question',
      'type a question and add two example questions from Try one',
      'click Ask Jev and read the confirm for 4 traces',
      'click Start',
    ],
    backendChecks: [
      'POST /tracer/instant-eval/estimate/ answers rows_to_read 4 and needs_confirm true for the project\'s traces',
      'POST /tracer/instant-eval/start/ is sent confirmed for 4 rows and returns a task_id, and PG holds its tracer_eval_task and tracer_instant_eval_run for the actor org with rows_requested 4',
    ],
    browserChecks: [
      'the Ask Jev row lies inside the 1280x720 viewport after each example is added, at the bottom of its scrolling pane with nothing after it',
      'Ask Jev opens a confirm reading 4 traces for 3 questions, Start is the only contained primary button and has focus',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // SPAN_VISIBLE 15 + six UI_READY browser waits 360 + API/PG and headroom 25.
  test.setTimeout(400_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-ask64-${suffix}`;
  const question = `Did the agent answer e2e-ask64-${suffix}?`;

  const { projectId, traceIds } = await test.step('seed: 4 traces in a new project', async () => {
    const req = await request.newContext();
    const traceIds: string[] = [];
    try {
      for (let i = 0; i < TRACES; i++) {
        const seeded = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
          secretKey: actor.secretKey, projectName, resourceAttributes: { project_type: 'observe' },
          rootAttributes: { 'input.value': `e2e-ask64-${suffix}-in-${i}`, 'output.value': `e2e-ask64-${suffix}-out-${i}` } });
        traceIds.push(seeded.traceId);
      }
    } finally {
      await req.dispose();
    }
    let rows: { project_id: string; traces: string }[] = [];
    await expect.poll(async () => {
      rows = await probe.ch<{ project_id: string; traces: string }>(
        `SELECT toString(project_id) AS project_id, toString(uniqExact(trace_id)) AS traces FROM spans FINAL
          WHERE trace_id IN (SELECT arrayJoin(JSONExtract({ids:String}, 'Array(String)'))) GROUP BY project_id`,
        { ids: JSON.stringify(traceIds) });
      return rows.map(row => Number(row.traces));
    }, POLL.SPAN_VISIBLE).toEqual([traceIds.length]);
    const projectId = rows[0].project_id;
    await testInfo.attach('seeded', { contentType: 'application/json',
      body: JSON.stringify({ projectId, projectName, traceIds, organizationId: actor.organizationId }) });
    return { projectId, traceIds };
  });

  await test.step('seed: the org TypeSafe key', () => ensureTypesafeKey(actor));

  await test.step('UI + API: Ask a question counts the 4 traces, past the confirm size', async () => {
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`,
      { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Evaluate', exact: true }).click();
    const estimated = page.waitForResponse(r => new URL(r.url()).pathname === ESTIMATE
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('menu').getByRole('menuitem').filter({ hasText: ASK_A_QUESTION }).click();
    const response = await estimated;
    expect(response.request().postDataJSON()).toMatchObject({ project_id: projectId, unit: 'traces' });
    const estimate = (await response.json() as Estimate).result;
    await testInfo.attach('estimate', { contentType: 'application/json', body: JSON.stringify(estimate) });
    expect(estimate).toMatchObject({ population: TRACES, rows_to_read: TRACES, needs_confirm: true,
      confirm_rows: CONFIRM_ROWS });
  });

  const actions = page.getByTestId('ask-actions');
  const askJev = actions.getByRole('button', { name: `Ask Jev about ${TRACES} traces`, exact: true });

  await test.step('UI: the Ask Jev row stays on screen as two examples are added', async () => {
    await ui(askJev).toBeEnabled();
    await page.getByRole('textbox', { name: 'Question 1' }).fill(question);
    for (const [index, example] of EXAMPLES.entries()) {
      await page.getByRole('button', { name: example, exact: true }).click();
      await ui(page.getByRole('textbox', { name: `Question ${index + 2}` })).toHaveValue(example);
      await expectOnScreen(page, actions);
      await ui(askJev).toBeVisible();
    }
    const where = await placement(actions);
    await testInfo.attach('ask-actions-placement', { contentType: 'application/json',
      body: JSON.stringify({ where, box: await actions.boundingBox(), viewport: page.viewportSize() }) });
    // The questions overflow the pane, nothing follows the row, and it sits on
    // the pane's bottom edge: pinned, not merely short of the fold.
    expect(where).toEqual({ following: 0, overflows: true, gapBelow: 0 });
    await testInfo.attach('composer', { body: await page.screenshot(), contentType: 'image/png' });
  });

  await test.step('UI: Ask Jev restates the read, with Start the only primary action', async () => {
    await askJev.click();
    // The alert also holds Back and Start; its message is the confirm.
    await ui(actions.getByRole('alert').getByText(CONFIRM_TEXT)).toBeVisible();
    const start = actions.getByRole('button', { name: 'Start', exact: true });
    await ui(start).toBeFocused();
    await ui(askJev).toHaveCount(0);
    // MUI marks a variant="contained" color="primary" button with this class;
    // AskPage.test.jsx primaryButtons() reads the same marker.
    await ui(page.locator('button.MuiButton-containedPrimary')).toHaveText(['Start']);
    await testInfo.attach('confirm', { body: await page.screenshot(), contentType: 'image/png' });
  });

  const taskId = await test.step('UI + API: Start launches the confirmed run', async () => {
    const started = page.waitForResponse(r => new URL(r.url()).pathname === START
      && r.request().method() === 'POST', { timeout: UI_READY });
    await actions.getByRole('button', { name: 'Start', exact: true }).click();
    const response = await started;
    const body = await response.json() as Started;
    await testInfo.attach('start', { contentType: 'application/json',
      body: JSON.stringify({ request: response.request().postDataJSON(), status: response.status(), body }) });
    expect(response.status()).toBe(200);
    const sent = response.request().postDataJSON() as { config_ids: string[] };
    expect(sent).toMatchObject({ project_id: projectId, unit: 'traces', sample_size: null,
      confirm: true, confirmed_rows: TRACES });
    expect(sent.config_ids).toHaveLength(1 + EXAMPLES.length);
    expect(body.result).toMatchObject({ rows_requested: TRACES, population: TRACES });
    await ui(page).toHaveURL(new RegExp(`/dashboard/observe/${projectId}/ask\\?run=${body.result.task_id}(&|$)`));
    return body.result.task_id;
  });

  await test.step('storage: the eval task and its Instant eval run, in the actor org', async () => {
    const rows = await probe.pg<{ project_id: string; spans_limit: number; unit: string; rows_requested: number }>(
      `SELECT t.project_id::text AS project_id, t.spans_limit, r.unit, r.rows_requested
         FROM tracer_eval_task t JOIN tracer_instant_eval_run r ON r.eval_task_id = t.id
        WHERE t.id = $1 AND r.organization_id = $2`,
      [taskId, actor.organizationId]);
    expect(rows).toEqual([{ project_id: projectId, spans_limit: TRACES, unit: 'traces', rows_requested: TRACES }]);
  });
});
