import { request } from '@playwright/test';
import { test, expect } from '../../lib/licensed-customer';
import type { TestActor } from '../../lib/provisioning';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// README's browser first-paint ceiling, not the default 10-second expect budget.
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

// useInstantEval.js startAskRun and page/useAskRun.js (tracer/views/instant_eval_scale.py).
const START = '/tracer/instant-eval/start/';
const SUMMARY = '/tracer/instant-eval/summary/';
const ROWS = '/tracer/instant-eval/rows/';
// src/utils/axios.js endpoints.develop.apiKey.status / .create.
const PROVIDER_STATUS = '/model-hub/develops/provider-status/';
const API_KEYS = '/model-hub/api-keys/';
// The body AIProviders.jsx (KeyCard.jsx handleFormSubmit) posts for a first
// TypeSafe key, captured with page.on('request') from the running app:
// {"provider":"typesafe","key":"e2e-decision-typesafe-key","has_key":false}.
// e2e/stack/mock-llm/server.mjs accepts this TypeSafe key only.
const TYPESAFE_KEY_BODY = { provider: 'typesafe', key: 'e2e-decision-typesafe-key', has_key: false };
// e2e/stack/mock-llm/server.mjs decisions(): a row whose state holds this
// marker gets the last label (No); every other row the first (Yes).
const PASS_MARKER = 'e2e-decision:pass';
// InstantEval/EvaluateMenu.jsx action title on the Traces view.
const ASK_A_QUESTION = /Ask a question/;

// The three traces, by their minted input, and the answer each must get. The
// expected answers are written out, not derived from where the marker is put,
// so moving the marker in the seed fails the flow.
const SEED = [
  { key: 'a', output: `${PASS_MARKER} reply` },
  { key: 'b', output: 'plain reply' },
  { key: 'c', output: 'plain reply' },
] as const;
const EXPECTED: Record<string, 'Yes' | 'No'> = { a: 'No', b: 'Yes', c: 'Yes' };
const COUNTS = { Yes: 2, No: 1, __error__: 0 };

interface ProviderStatus { result: { providers: { provider: string; has_key: boolean }[] } }
interface Summary { result: { status: string; total: number; answered: number; failed: number;
  counts: Record<string, Record<string, number>> } }
interface Rows { result: { rows: { id: string; excerpt: string;
  answers: Record<string, { answer: string | null; status: string }> }[]; total: number } }

/** Saves the org's TypeSafe key the way AI Providers does, unless a flow
 * earlier in this worker already saved it (the licensed actor is shared). */
async function ensureTypesafeKey(actor: TestActor): Promise<void> {
  const typesafe = async () => (await actor.api.get<ProviderStatus>(PROVIDER_STATUS))
    .result.providers.filter(p => p.provider === TYPESAFE_KEY_BODY.provider);
  if (!(await typesafe())[0]?.has_key) await actor.api.post(API_KEYS, TYPESAFE_KEY_BODY);
  expect(await typesafe()).toEqual([expect.objectContaining({ has_key: true })]);
}

test('OBS-E2E-065: Ask Jev reads every trace and shows each answer', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-065', area: 'observe',
    userGoal: 'Ask Jev a yes/no question about my traces and see every trace answered, with the counts adding up',
    steps: [
      'send 3 traces to a new Observe project, one whose output the mock answers No',
      'save the org TypeSafe key through the endpoint AI Providers uses',
      'open the project\'s Traces view, its Evaluate menu, and choose Ask a question',
      'type a yes/no question and click Ask Jev, which starts at once under the confirm size',
      'watch the run finish: its status line says every trace was answered',
      'read the run\'s traces table',
    ],
    backendChecks: [
      'GET /tracer/instant-eval/summary/ reaches status completed with answered 3, failed 0 and counts Yes 2, No 1',
      'GET /tracer/instant-eval/rows/ returns exactly the three seeded trace ids, each with its expected answer',
    ],
    browserChecks: [
      'the status line says 3 traces answered and the counts read 2 Yes · 1 No',
      'the traces table shows exactly the three seeded traces, each with the same answer as the rows API',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // SPAN_VISIBLE 15 + EVAL_RESULT 90 + five UI_READY browser waits 300 + headroom 15.
  test.setTimeout(420_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-ask65-${suffix}`;
  const question = `Did the agent answer e2e-ask65-${suffix}?`;
  const inputOf = (key: string) => `e2e-ask65-${suffix}-${key}`;

  const { projectId, traceOf } = await test.step('seed: 3 traces, one marked for No', async () => {
    const req = await request.newContext();
    const traceOf: Record<string, string> = {};
    try {
      for (const { key, output } of SEED) {
        const seeded = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
          secretKey: actor.secretKey, projectName, resourceAttributes: { project_type: 'observe' },
          rootAttributes: { 'input.value': inputOf(key), 'output.value': `${output} ${suffix}` } });
        traceOf[key] = seeded.traceId;
      }
    } finally {
      await req.dispose();
    }
    let rows: { project_id: string; traces: string }[] = [];
    await expect.poll(async () => {
      rows = await probe.ch<{ project_id: string; traces: string }>(
        `SELECT toString(project_id) AS project_id, toString(uniqExact(trace_id)) AS traces FROM spans FINAL
          WHERE trace_id IN (SELECT arrayJoin(JSONExtract({ids:String}, 'Array(String)'))) GROUP BY project_id`,
        { ids: JSON.stringify(Object.values(traceOf)) });
      return rows.map(row => Number(row.traces));
    }, POLL.SPAN_VISIBLE).toEqual([SEED.length]);
    const projectId = rows[0].project_id;
    await testInfo.attach('seeded', { contentType: 'application/json',
      body: JSON.stringify({ projectId, projectName, traceOf, organizationId: actor.organizationId }) });
    return { projectId, traceOf };
  });

  await test.step('seed: the org TypeSafe key', () => ensureTypesafeKey(actor));

  const { taskId, configId } = await test.step('UI: ask the question; 3 traces start without a confirm', async () => {
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`,
      { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Evaluate', exact: true }).click();
    await page.getByRole('menu').getByRole('menuitem').filter({ hasText: ASK_A_QUESTION }).click();
    await page.getByRole('textbox', { name: 'Question 1' }).fill(question);
    const started = page.waitForResponse(r => new URL(r.url()).pathname === START
      && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByTestId('ask-actions')
      .getByRole('button', { name: `Ask Jev about ${SEED.length} traces`, exact: true }).click();
    const response = await started;
    const sent = response.request().postDataJSON() as { config_ids: string[] };
    const body = await response.json() as { result: { task_id: string } };
    await testInfo.attach('start', { contentType: 'application/json',
      body: JSON.stringify({ request: sent, status: response.status(), body }) });
    expect(response.status()).toBe(200);
    // Under the confirm size the composer sends it unconfirmed, at once.
    expect(sent).toMatchObject({ project_id: projectId, unit: 'traces', sample_size: null, confirm: false });
    expect(sent.config_ids).toHaveLength(1);
    return { taskId: body.result.task_id, configId: sent.config_ids[0] };
  });

  await test.step('API: the run completes with every trace answered and the counts adding up', async () => {
    let summary: Summary['result'] | undefined;
    await expect.poll(async () => {
      summary = (await actor.api.get<Summary>(SUMMARY,
        { project_id: projectId, eval_task_id: taskId, tz: 'UTC' })).result;
      return summary.status;
    }, POLL.EVAL_RESULT).toBe('completed');
    await testInfo.attach('summary', { contentType: 'application/json',
      body: JSON.stringify({ ...summary, timeline: undefined }) });
    expect(summary).toMatchObject({ total: SEED.length, answered: SEED.length, failed: 0 });
    expect(summary!.counts).toEqual({ [configId]: COUNTS });
  });

  await test.step('API: the rows list each seeded trace with its answer', async () => {
    const rows = (await actor.api.get<Rows>(ROWS,
      { project_id: projectId, eval_task_id: taskId, tz: 'UTC', sort: '-time', page_size: 50 })).result;
    await testInfo.attach('rows', { contentType: 'application/json', body: JSON.stringify(rows) });
    const answered = Object.fromEntries(rows.rows.map(row => [row.id,
      Object.keys(row.answers).length === 1 && row.answers[configId]?.status === 'completed'
        ? row.answers[configId].answer : JSON.stringify(row.answers)]));
    expect(answered).toEqual(Object.fromEntries(
      SEED.map(({ key }) => [traceOf[key], EXPECTED[key]])));
    expect(rows.total).toBe(SEED.length);
  });

  await test.step('UI: the status line, the counts and the traces table agree', async () => {
    await ui(page).toHaveURL(new RegExp(`/dashboard/observe/${projectId}/ask\\?run=${taskId}(&|$)`));
    await ui(page.getByText(/^3 traces answered( in .+)?$/)).toBeVisible();
    await ui(page.getByText(`${COUNTS.Yes} Yes · ${COUNTS.No} No`, { exact: true })).toBeVisible();
    const table = page.getByRole('region', { name: 'Traces asked about' });
    const bodyRows = table.getByRole('rowgroup').nth(1).getByRole('row');
    await ui(bodyRows).toHaveCount(SEED.length);
    await bodyRows.first().scrollIntoViewIfNeeded();
    // Each row: When, User, Trace (its input, then a facet line), Answer.
    const pairs = async () => (await Promise.all((await bodyRows.all()).map(async row => {
      const cells = await row.getByRole('cell').allInnerTexts();
      return `${cells[2].split('\n')[0].trim()} → ${cells[3].trim()}`;
    }))).sort();
    await expect.poll(pairs, { timeout: UI_READY }).toEqual(
      SEED.map(({ key }) => `${inputOf(key)} → ${EXPECTED[key]}`).sort());
    await testInfo.attach('run', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  });
});
