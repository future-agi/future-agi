import { createHash } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { CREATE, MODEL, ROWS, STANDIN, resultCells, seedScope } from './experiment-scope-seed';
import type { ScopeDetail } from './experiment-scope-seed';

// Native controls: RunExperiment, SampleSetting, ErrorPolicySetting and RowLimits.
const UI_READY = 60_000;
// This release refuses token, message and cost limits (rowLimitsModel.js and
// experiment_scope_gate.py); the time limit runs.
const LIMITS_REFUSED = "Token, message and cost limits aren't available in this release. Clear them to run.";

test('EXP-E2E-005: run a selected subset with saved limits and error scoring', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'EXP-E2E-005', area: 'datasets',
    userGoal: 'Run picked or seeded sample rows with a time limit and a chosen error scoring, and trust the saved selection.',
    steps: ['seed two synthetic organizations and a prompt backed by the managed mock-llm echo server',
      'submit picked rows through the wizard with Count as 0, after a refused messages limit is cleared for a time limit',
      'submit a seeded sample with Leave out and the same time limit',
      'read the server receipt, persisted output and evaluation cells, and the classic result grid',
      'submit a seeded sample after a refused tokens limit is cleared and verify one provider call per row',
      'refuse a foreign row, a token limit and a foreign experiment read without creating a snapshot'],
    backendChecks: ['wizard POST bodies and persisted scope contain exactly the picked or deterministically sampled source rows, the chosen error policy, the time limit and the entered metadata',
      'token, message and cost limits are refused on the run card and by the API without a write',
      'execution persists the mock-llm echo and the time limit under the bounded policy',
      'foreign-row create and foreign-organization detail read are refused without writes'],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe, request }, testInfo) => {
  // Three runs: 3 ASYNC_JOB polls + 20 UI_READY steps + 120s fixture provisioning.
  test.setTimeout(1_500_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA;
  const seed = await seedScope(actor, probe, suffix, scopeActors);
  const foreign = await scopeActors.ownerB.api.post<{ result: { dataset_id: string } }>(
    '/model-hub/develops/create-dataset-manually/', {
      dataset_name: `e2e-scope-foreign-${suffix}`, number_of_rows: 1, number_of_columns: 1,
    });
  const foreignRows = await probe.pg<{ id: string }>(
    'SELECT id FROM model_hub_row WHERE dataset_id=$1 AND NOT deleted', [foreign.result.dataset_id]);
  expect(foreignRows).toHaveLength(1);
  await testInfo.attach('seeded-identities', { contentType: 'application/json',
    body: JSON.stringify({ ...seed, foreignRowId: foreignRows[0].id,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  const health = await request.get(`${STANDIN}/health`);
  expect(health.status()).toBe(200);
  expect(await health.json()).toEqual({ status: 'healthy' });
  const expectedOutput = `echo: [{"type":"text","text":"SCOPE:${suffix}"}]`;
  // CustomPromptEvaluator builds user_text before _system_message sets its
  // format hint; each row gets a fresh evaluator, so "either ." is intentional.
  const expectedJudge = '{"result":"Pass","explanation":"SCOPE_JUDGE"}\n'
    + `Observed: ${expectedOutput}\n\n--- Input Data ---\n<output>${expectedOutput}</output>\n`
    + '--- End Input Data ---\n\nPlease return a JSON object with the following fields:\n'
    + ' - result: Result must be either .\n - explanation: An explanation of the result.\n';
  type ProviderCall = { method: string; path: string;
    body: { model: string; messages: { role: string; content: unknown }[] } };
  const calls = async () => {
    // Unlike the string-only census, scoped recording preserves text blocks.
    // Ignore discovery GETs, never inference POSTs, including unexpected ones.
    const response = await request.get(`${STANDIN}/__requests/${seed.scenario}`);
    expect(response.status()).toBe(200);
    const body = await response.json() as { scenario: string; requests: ProviderCall[] };
    expect(body.scenario).toBe(seed.scenario);
    return body.requests.filter(call => call.method === 'POST');
  };
  const context = await scopeActors.openContext(browser, actor);
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const ids: string[] = [];
  let lastBody: Record<string, unknown> = {};
  const sampled = [...seed.rowIds].sort((a, b) => Buffer.compare(
    createHash('sha256').update(`17:${a}`).digest(), createHash('sha256').update(`17:${b}`).digest())).slice(0, 2).sort();
  try {
    for (const variant of ['picked-zero', 'sample-exclude', 'sample-admitted'] as const) {
      const picked = variant === 'picked-zero';
      const admitted = variant === 'sample-admitted';
      const policy = variant === 'sample-exclude' ? 'exclude' : 'zero';
      const selected = picked ? [seed.rowIds[0], seed.rowIds[2], seed.rowIds[4]] : sampled;
      const refusedLimit = admitted ? 'tokens' : 'messages';
      const limits = { timeout_s: 600 };
      const metadata: Record<string, string> = picked ? { ticket: `E2E-${suffix}` } : {};
      const query = new URLSearchParams({ tab: 'experiments', new_experiment: '1',
        source: 'prompt', prompt_id: seed.promptId, prompt_version: seed.promptVersion });
      if (picked) query.set('rows', selected.join(','));
      await test.step(`${variant}: configure prompt and limits`, async () => {
        await page.goto(`/dashboard/develop/${seed.datasetId}?${query}`, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('textbox', { name: /^Experiment name/ })).toBeVisible({ timeout: UI_READY });
        await page.getByRole('textbox', { name: /^Experiment name/ }).fill(`e2e-${variant}-${suffix}`);
        // A refused limit stays on the card and says so; clear it and set a time limit.
        await page.getByLabel(`Limit per row: ${refusedLimit}`, { exact: true }).fill(admitted ? '500' : '1');
        await expect(page.getByTestId('row-limits-issues')).toHaveText(LIMITS_REFUSED, { timeout: UI_READY });
        await page.getByLabel(`Limit per row: ${refusedLimit}`, { exact: true }).fill('');
        await page.getByLabel('Limit per row: seconds', { exact: true }).fill(String(limits.timeout_s));
        await expect(page.getByTestId('row-limits-issues')).toHaveCount(0);
        await page.getByRole('button', { name: /^Continue/ }).click();
      }, { timeout: UI_READY * 2 });
      await test.step(`${variant}: select data and evaluation`, async () => {
        if (picked) {
          await expect(page.getByTestId('these-rows')).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
        } else {
          await page.getByRole('checkbox', { name: 'Run a random sample of the rows' }).check();
          await page.getByRole('spinbutton', { name: 'Rows in the sample' }).fill('2');
          await page.getByRole('spinbutton', { name: 'Sample seed' }).fill('17');
        }
        await page.getByRole('button', { name: /^Continue/ }).click();
        await expect(page.getByText(seed.evalName, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
        await page.getByRole('button', { name: /^Continue/ }).click();
        if (picked) {
          await page.getByRole('button', { name: 'Add field', exact: true }).click();
          await page.getByPlaceholder('key', { exact: true }).fill('ticket');
          await page.getByPlaceholder('value', { exact: true }).fill(metadata.ticket);
          await expect(page.getByText(`ticket: ${metadata.ticket}`, { exact: true })).toBeVisible({ timeout: UI_READY });
        }
        // Review lists only metadata the server stores.
        await expect(page.getByText('source: ui')).toHaveCount(0);
        // No policy is the default (no score); each variant chooses its own.
        await page.getByRole('combobox', { name: 'Errored tries in scores' }).click();
        await page.getByRole('option', { name: policy === 'zero' ? 'Count as 0' : 'Leave out', exact: true }).click();
        await expect(page.getByText(policy === 'zero' ? 'Errors count as 0 in scores' : 'Errors excluded from scores', { exact: true })).toBeVisible({ timeout: UI_READY });
      }, { timeout: UI_READY * 2 });
      const before = await calls();
      await test.step(`${variant}: submit and read the server receipt`, async () => {
        const posted = page.waitForResponse(response => new URL(response.url()).pathname === CREATE
          && response.request().method() === 'POST', { timeout: UI_READY });
        await page.getByRole('button', { name: /^Run experiment/ }).click();
        const response = await posted;
        expect(response.status(), await response.text()).toBe(200);
        lastBody = response.request().postDataJSON();
        expect(lastBody.error_policy).toBe(policy);
        expect(lastBody.limits).toEqual(limits);
        expect(lastBody.metadata ?? {}).toEqual(metadata);
        expect(lastBody.user_eval_metrics).toMatchObject([{ model: MODEL }]);
        await testInfo.attach(`${variant}-wire`, { contentType: 'application/json', body: JSON.stringify(lastBody) });
        if (picked) expect(lastBody.row_ids).toEqual(selected);
        else expect(lastBody.sample).toEqual({ size: 2, seed: 17 });
        const id = /experiment_id=([0-9a-f-]{36})/.exec((await response.json()).result)?.[1];
        expect(id).toBeTruthy(); ids.push(id!);
        await expect(page.getByText(`Server selected ${selected.length} of ${picked ? selected.length : ROWS} eligible rows (${ROWS} source rows)`, { exact: true })).toBeVisible({ timeout: UI_READY });
        const detail = (await actor.api.get<ScopeDetail>(`${CREATE}${id}/`)).result;
        expect([...detail.run_scope.selected_row_ids].sort()).toEqual([...selected].sort());
        expect(detail.run_scope.selected_count).toBe(selected.length);
        expect(detail.limits).toEqual(limits);
        expect(detail.error_policy).toBe(policy);
        expect(detail.metadata).toEqual(metadata);
        const stored = await probe.pg<{ n: number }>(`SELECT count(*)::int AS n FROM model_hub_row r
          JOIN model_hub_experimentstable e ON e.snapshot_dataset_id=r.dataset_id WHERE e.id=$1 AND NOT r.deleted`, [id]);
        expect(stored).toEqual([{ n: selected.length }]);
        const copied = await probe.pg<{ value: string }>(`SELECT c.value FROM model_hub_cell c
          JOIN model_hub_column col ON col.id=c.column_id
          JOIN model_hub_experimentstable e ON e.snapshot_dataset_id=c.dataset_id
          WHERE e.id=$1 AND col.source_id=$2 AND NOT c.deleted ORDER BY c.value`, [id, seed.columnId]);
        expect(copied.map(cell => cell.value)).toEqual(selected.map(rowId => `source-${rowId}`).sort());
      }, { timeout: UI_READY * 2 });
      const id = ids.at(-1)!;
      await expect.poll(async () => {
        const cells = await resultCells(probe, id);
        return cells.length === selected.length * 2 && cells.every(cell => ['pass', 'error'].includes(cell.status));
      }, POLL.ASYNC_JOB).toBe(true);
      const cells = await resultCells(probe, id);
      const output = cells.filter(cell => cell.source === 'experiment');
      const scores = cells.filter(cell => cell.source === 'experiment_evaluation');
      expect(output).toHaveLength(selected.length); expect(scores).toHaveLength(selected.length);
      // The managed echo answers inside the time limit, so every row is scored; how
      // failed rows score under each policy is pinned by backend tests
      // (test_native_prompt_create_bridge.py, test_column_average_choice_quality.py).
      expect(output.map(cell => cell.value)).toEqual(selected.map(() => expectedOutput));
      expect(output.map(cell => cell.status)).toEqual(selected.map(() => 'pass'));
      expect(scores.map(cell => cell.status)).toEqual(selected.map(() => 'pass'));
      // EvaluationRunner persists successful Pass/Fail values as "Passed".
      expect(scores.map(cell => cell.value)).toEqual(selected.map(() => 'Passed'));
      for (const cell of output) expect(JSON.parse(cell.value_infos).execution_limits).toEqual(limits);
      const recorded = await calls();
      expect(recorded.slice(0, before.length)).toEqual(before);
      const added = recorded.slice(before.length);
      expect(added).toHaveLength(selected.length * 2);
      expect(added.map(call => ({ path: call.path, model: call.body.model }))).toEqual(
        added.map(() => ({ path: `/s/${seed.scenario}/-/v1/chat/completions`, model: MODEL })));
      const prompts = added.filter(call => Array.isArray(call.body.messages.at(-1)?.content));
      expect(prompts).toHaveLength(selected.length);
      // Which rows ran is pinned by run_scope.selected_row_ids above; every
      // prompt call carries exactly the rendered prompt and nothing else.
      expect(prompts.map(call => call.body.messages)).toEqual(selected.map(() => [
        { role: 'system', content: [{ type: 'text', text: 'Return the marker.' }] },
        { role: 'user', content: [{ type: 'text', text: `SCOPE:${suffix}` }] },
      ]));
      const judges = added.filter(call => !Array.isArray(call.body.messages.at(-1)?.content));
      expect(judges.map(call => call.body.messages.at(-1))).toEqual(
        selected.map(() => ({ role: 'user', content: expectedJudge })));
      await expect(page.locator('.ag-cell').filter({ hasText: expectedOutput }).first()).toBeVisible({ timeout: UI_READY });
      await testInfo.attach(`${variant}-persisted`, { contentType: 'application/json', body: JSON.stringify({ id, cells }) });
    }
    const before = await probe.pg<{ id: string }>('SELECT id FROM model_hub_experimentstable WHERE dataset_id=$1 ORDER BY id', [seed.datasetId]);
    const counts = () => probe.pg(`SELECT
      (SELECT count(*)::int FROM model_hub_dataset WHERE organization_id=$1) AS datasets,
      (SELECT count(*)::int FROM model_hub_row r JOIN model_hub_dataset d ON d.id=r.dataset_id WHERE d.organization_id=$1) AS rows,
      (SELECT count(*)::int FROM model_hub_cell c JOIN model_hub_dataset d ON d.id=c.dataset_id WHERE d.organization_id=$1) AS cells`, [actor.organizationId]);
    const beforeCounts = await counts();
    const beforeCalls = await calls();
    const refused = await scopeActors.send(actor, 'POST', CREATE, {
      ...lastBody, name: `e2e-foreign-${suffix}`, sample: undefined, row_ids: [foreignRows[0].id],
    });
    expect(refused.status).toBe(400);
    // The API refuses a token limit with the run card's line, before any write.
    const tokenLimit = await scopeActors.send(actor, 'POST', CREATE, {
      ...lastBody, name: `e2e-token-limit-${suffix}`, limits: { tokens: 500 },
    });
    expect(tokenLimit.status).toBe(400);
    expect((await scopeActors.send(scopeActors.ownerB, 'GET', `${CREATE}${ids[0]}/`)).status).toBe(404);
    expect(await probe.pg('SELECT id FROM model_hub_experimentstable WHERE dataset_id=$1 ORDER BY id', [seed.datasetId])).toEqual(before);
    expect(await counts()).toEqual(beforeCounts);
    expect(await calls()).toEqual(beforeCalls);
    await test.step('explicit close does not reopen the deep-linked draft', async () => {
      await page.goto(`/dashboard/develop/${seed.datasetId}?tab=experiments&new_experiment=1`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('textbox', { name: /^Experiment name/ }).fill(`e2e-discard-${suffix}`);
      await page.getByRole('button', { name: 'Close', exact: true }).click();
      await page.getByRole('button', { name: 'Discard', exact: true }).click();
      await expect(page).not.toHaveURL(/new_experiment=1/, { timeout: UI_READY });
      await expect(page.getByTestId('experiment-drawer')).toHaveCount(0, { timeout: UI_READY });
    }, { timeout: UI_READY * 2 });
  } catch (error) {
    await testInfo.attach('scope-page', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    await testInfo.attach('scope-page-text', { contentType: 'text/plain', body: await page.locator('body').innerText() });
    throw error;
  } finally {
    await context.close();
  }
});
