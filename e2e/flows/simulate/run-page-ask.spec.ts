import { test, expect } from '../../lib/licensed-customer';
import type { TestActor } from '../../lib/provisioning';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedHarnessEnvironment } from '../../lib/simulate-seed';

// README's browser first-paint ceiling, not the default 10-second expect budget.
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

// src/utils/axios.js endpoints.runResultsV3.asks / askCheck / askDetail.
const asksPath = (executionId: string) => `/simulate/v3/test-executions/${executionId}/asks/`;
// src/utils/axios.js endpoints.develop.apiKey.status / .create.
const PROVIDER_STATUS = '/model-hub/develops/provider-status/';
const API_KEYS = '/model-hub/api-keys/';
// The body AIProviders.jsx (KeyCard.jsx handleFormSubmit) posts for a first TypeSafe key, as
// flows/observe/ask-jev-run.spec.ts pins it. e2e/stack/mock-llm/server.mjs accepts this TypeSafe key only.
const TYPESAFE_KEY_BODY = { provider: 'typesafe', key: 'e2e-decision-typesafe-key', has_key: false };
// e2e/stack/mock-llm/server.mjs decisions(): a call whose transcript holds this marker gets the last label
// (No); every other call the first (Yes). The ask sends the call's transcript as the decision's state.
const PASS_MARKER = 'e2e-decision:pass';

// Three finished chat calls, by the agent's reply; the first two are ticked, the third is not. The expected
// answers are written out, not derived from where the marker is put, so moving the marker fails the flow.
const REPLIES = { a: `${PASS_MARKER} reply`, b: 'plain reply', c: 'plain reply' } as const;
const TICKED = ['a', 'b'] as const;
const EXPECTED: Record<(typeof TICKED)[number], 'Yes' | 'No'> = { a: 'No', b: 'Yes' };

interface ProviderStatus { result: { providers: { provider: string; has_key: boolean }[] } }
// simulate/serializers ask detail: the fields this flow reads.
interface AskDetail {
  id: string; status: string; selection_kind: string; requested: number;
  questions: { key: string; name: string; answer_type: string; labels: string[]; kinds: string[];
    counts: Record<string, number> & { rate: { answered: number; labels: Record<string, number> } } }[];
  results: { call_execution_id: string; call_name: string; question_key: string; state: string;
    answer: string | null }[];
}
interface AskPage { count: number; results: { id: string; status: string }[] }

/** Saves the org's TypeSafe key the way AI Providers does, unless a flow earlier in this worker already saved
 * it (the licensed actor is shared). */
async function ensureTypesafeKey(actor: TestActor): Promise<void> {
  const typesafe = async () => (await actor.api.get<ProviderStatus>(PROVIDER_STATUS))
    .result.providers.filter(p => p.provider === TYPESAFE_KEY_BODY.provider);
  if (!(await typesafe())[0]?.has_key) await actor.api.post(API_KEYS, TYPESAFE_KEY_BODY);
  expect(await typesafe()).toEqual([expect.objectContaining({ has_key: true })]);
}

test('SIM-E2E-048: Ask a question about the calls I ticked in a run and read each call\'s answer', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-048', area: 'simulate',
    userGoal: 'Tick some calls in a finished run, ask a yes/no question about them, and read how each ticked '
      + 'call was answered, with the counts adding up and the unticked call left out',
    steps: [
      'seed a finished run of three chat calls, one whose reply the mock answers No (e2e/lib/harness_environment_seed.py)',
      'save the org TypeSafe key through the endpoint AI Providers uses',
      'open the run, tick two of the three calls and choose Ask a question',
      'type a yes/no question, click Check and read what it found, then click Ask',
      'watch the ask finish on the Questions tab and read its answers per call',
    ],
    backendChecks: [
      'POST .../asks/check/ and POST .../asks/ are sent for exactly the two ticked calls; the launch answers 201',
      'GET .../asks/{id}/ reaches finished with 2 answered of 2 requested, rate Yes 1 and No 1, and each ticked '
        + 'call\'s expected answer',
      'GET .../asks/ lists exactly this ask for the run',
      'simulate_ask is finished and simulate_eval_result holds one answered row per ticked call with its label, '
        + 'none for the unticked call',
    ],
    browserChecks: [
      'the check reads 2 calls requested and 2 ready, 0 unmeasured, 0 not applicable',
      'the ask card reads Finished, its counts and its rate',
      'the answers table lists exactly the two ticked calls, each with the same answer as the API',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Seed 60 + EVAL_RESULT 90 + six UI_READY browser waits 360 + headroom 30.
  test.setTimeout(540_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const question = `Did the agent confirm the refund e2e-ask45-${suffix}?`;

  const seeded = await test.step('seed: a finished run of three chat calls, one marked for No', async () => {
    const seed = seedHarnessEnvironment({ organizationId: actor.organizationId, workspaceId: actor.workspaceId,
      refunds: 2, lookups: 1, chatCalls: [REPLIES.a, REPLIES.b, REPLIES.c] });
    await testInfo.attach('seeded', { contentType: 'application/json',
      body: JSON.stringify({ ...seed, organizationId: actor.organizationId }) });
    const [a, b, c] = seed.scenarioKeys;
    expect(Object.keys(seed.callIds ?? {}).sort()).toEqual([a, b, c].sort());
    return { ...seed, keyOf: { a, b, c } as Record<keyof typeof REPLIES, string>,
      executionId: seed.testExecutionId!, callIds: seed.callIds! };
  });
  const callOf = (name: keyof typeof REPLIES) => seeded.callIds[seeded.keyOf[name]];
  const tickedIds = TICKED.map(callOf).sort();

  await test.step('seed: the org TypeSafe key', () => ensureTypesafeKey(actor));

  const askId = await test.step('UI: tick two calls, check the question, then ask', async () => {
    await page.goto(`/dashboard/simulate/environments/${seeded.environmentId}/runs/${seeded.runTestId}`
      + `/${seeded.executionId}`, { waitUntil: 'domcontentloaded' });
    // RunTraceTable.jsx groups calls by goal, every group closed; Expand all shows the calls.
    await page.getByLabel('Expand all', { exact: true }).check();
    for (const name of TICKED) {
      // TraceTable.jsx: each call's tick box is labelled "Select <scenario>".
      await page.getByRole('checkbox', { name: `Select ${seeded.keyOf[name]}`, exact: true }).check();
    }
    // RerunSelectionBar.jsx: the ticked calls' "Ask a question" action.
    await page.getByRole('button', { name: 'Ask a question', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Ask a question' });
    await dialog.getByRole('textbox', { name: 'What do you want to know about each call?' }).fill(question);
    await ui(dialog.getByRole('radio', { name: 'Yes or no' })).toBeChecked();

    const checked = page.waitForResponse(r => new URL(r.url()).pathname === `${asksPath(seeded.executionId)}check/`
      && r.request().method() === 'POST', { timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Check', exact: true }).click();
    const check = await checked;
    const checkSent = check.request().postDataJSON() as { selection: { kind: string; call_ids: string[] } };
    await testInfo.attach('check', { contentType: 'application/json',
      body: JSON.stringify({ request: checkSent, status: check.status(), body: await check.json() }) });
    expect(check.status()).toBe(200);
    expect({ ...checkSent.selection, call_ids: [...checkSent.selection.call_ids].sort() })
      .toEqual({ kind: 'calls', call_ids: tickedIds });
    const summary = dialog.getByTestId('ask-check');
    await ui(summary.getByText('2 calls requested', { exact: true })).toBeVisible();
    await expect(summary.getByText('Question 1: 2 ready · 0 unmeasured · 0 not applicable', { exact: true }))
      .toBeVisible();
    await expect(dialog.getByTestId('ask-refusal')).toHaveCount(0);

    const launched = page.waitForResponse(r => new URL(r.url()).pathname === asksPath(seeded.executionId)
      && r.request().method() === 'POST', { timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Ask (2 answers)', exact: true }).click();
    const launch = await launched;
    const sent = launch.request().postDataJSON() as { selection: { kind: string; call_ids: string[] };
      questions: Record<string, unknown>[]; accepted: { requested: number } };
    const body = await launch.json() as { id: string; status: string };
    await testInfo.attach('launch', { contentType: 'application/json',
      body: JSON.stringify({ request: sent, status: launch.status(), body }) });
    expect(launch.status()).toBe(201);
    expect({ ...sent.selection, call_ids: [...sent.selection.call_ids].sort() })
      .toEqual({ kind: 'calls', call_ids: tickedIds });
    expect(sent.questions).toEqual([expect.objectContaining(
      { question, answer_type: 'yes_no', kinds: ['transcript'] })]);
    expect(sent.accepted).toMatchObject({ requested: 2 });
    return body.id;
  });

  await test.step('API: the ask finishes with each ticked call answered and the counts adding up', async () => {
    let detail: AskDetail | undefined;
    await expect.poll(async () => {
      detail = await actor.api.get<AskDetail>(`${asksPath(seeded.executionId)}${askId}/`);
      return detail.status;
    }, POLL.EVAL_RESULT).toBe('finished');
    await testInfo.attach('ask', { contentType: 'application/json', body: JSON.stringify(detail) });
    expect(detail).toMatchObject({ id: askId, selection_kind: 'calls', requested: 2 });
    expect(detail!.questions).toHaveLength(1);
    const [asked] = detail!.questions;
    expect(asked).toMatchObject({ name: question, answer_type: 'yes_no', labels: ['Yes', 'No'],
      kinds: ['transcript'] });
    expect(asked.counts).toEqual({ requested: 2, answered: 2, unmeasured: 0, not_applicable: 0, grading_error: 0,
      infrastructure_error: 0, cancelled: 0, pending: 0, calls_gone: 0,
      rate: { answered: 2, labels: { Yes: 1, No: 1 } } });
    expect(Object.fromEntries(detail!.results.map(row => [row.call_execution_id,
      `${row.question_key}:${row.state}:${row.answer}:${row.call_name}`])))
      .toEqual(Object.fromEntries(TICKED.map(name => [callOf(name),
        `${asked.key}:answered:${EXPECTED[name]}:${seeded.keyOf[name]}`])));

    const listed = await actor.api.get<AskPage>(asksPath(seeded.executionId), { page: 1 });
    expect({ count: listed.count, results: listed.results.map(({ id, status }) => ({ id, status })) })
      .toEqual({ count: 1, results: [{ id: askId, status: 'finished' }] });
  });

  await test.step('storage: the ask is finished with one answer per ticked call, none for the unticked one', async () => {
    const asks = await probe.pg<{ status: string; kind: string }>(
      `SELECT status, selection->>'kind' AS kind FROM simulate_ask WHERE id = $1`, [askId]);
    expect(asks).toEqual([{ status: 'finished', kind: 'calls' }]);
    const rows = await probe.pg<{ call_execution_id: string; state: string; label: string | null }>(
      `SELECT call_execution_id::text, state, label FROM simulate_eval_result WHERE ask_id = $1
        ORDER BY call_execution_id`, [askId]);
    await testInfo.attach('eval-results', { contentType: 'application/json', body: JSON.stringify(rows) });
    expect(rows).toEqual(TICKED.map(name => ({ call_execution_id: callOf(name), state: 'answered',
      label: EXPECTED[name] })).sort((x, y) => x.call_execution_id.localeCompare(y.call_execution_id)));
  });

  await test.step('UI: the Questions tab shows the finished ask, its counts and each call\'s answer', async () => {
    // RunDetail.jsx askLaunched: the launch opens the Questions tab with the new ask expanded.
    await ui(page.getByRole('tab', { name: 'Questions', selected: true })).toBeVisible();
    const card = page.getByTestId('run-asks').getByTestId(`ask-${askId}`);
    await ui(card.getByText('Finished', { exact: true })).toBeVisible();
    await expect(card.getByTestId('ask-counts')).toHaveText('2 calls requested · Answered 2 · Unmeasured 0 · '
      + 'Not applicable 0 · Grading error 0 · Infrastructure error 0 · Cancelled 0');
    // The rate lists the labels in the API's order; compare them as a set.
    await expect.poll(async () => (await card.getByTestId('ask-rate').innerText()).split(' · ').sort())
      .toEqual(['No: 1 of 2 answered', 'Yes: 1 of 2 answered']);
    const table = card.getByRole('table', { name: 'Answers per call' });
    const bodyRows = table.getByRole('rowgroup').nth(1).getByRole('row');
    await ui(bodyRows).toHaveCount(TICKED.length);
    const pairs = async () => (await Promise.all((await bodyRows.all()).map(async row => {
      const cells = await row.getByRole('cell').allInnerTexts();
      return `${cells[0].trim()} → ${cells[1].trim()}`;
    }))).sort();
    await expect.poll(pairs, { timeout: UI_READY }).toEqual(
      TICKED.map(name => `${seeded.keyOf[name]} → ${EXPECTED[name]}`).sort());
    await testInfo.attach('questions', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  });
});
