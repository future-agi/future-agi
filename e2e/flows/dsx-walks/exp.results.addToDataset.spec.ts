import { randomUUID } from 'node:crypto';
import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Sources pinned at 818b068e64b476b2a694706df2fc84bed6df790e:
// ManuallyCreateDataset.jsx; results/{SelectionBar,ResultsGrid,AddResultsToDatasetDialog}.jsx;
// addToDatasetModel.js:copyBody; e2e/flows/datasets/row-runs-tab.spec.ts (public SDK seed).
// Import paths assume installation at e2e/flows/dsx-walks/. No mocks or storage writes.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const UI = 60_000;
type Table = { result: { column_config: { id: string; name: string }[];
  table: ({ row_id: string } & Record<string, unknown>)[] } };
type Copy = { status: boolean; result: { dataset_id: string; dataset_name: string; created: boolean;
  rows_requested: number; rows_added: number; rows_failed: number; failures: unknown[]; trial: number;
  column_map: Record<string, string>; rows: { example_id: string; row_id: string; result_id: string }[] } };

async function dataset(scopes: ScopeActors, actor: ScopeActor, name: string) {
  const made = await scopes.send<{ result: { dataset_id: string } }>(actor, 'POST', CREATE, {
    dataset_name: name, model_type: 'GenerativeLLM', number_of_rows: 0, number_of_columns: 3,
    columns: [{ name: 'input', type: 'json', role: 'input' },
      { name: 'expected', type: 'json', role: 'expected' }, { name: 'output', type: 'json', role: 'metadata' }],
  });
  expect(made.status).toBe(200);
  return made.body.result.dataset_id;
}
async function table(scopes: ScopeActors, actor: ScopeActor, id: string) {
  const read = await scopes.send<Table>(actor, 'GET', `/model-hub/develops/${id}/get-dataset-table/`,
    undefined, { current_page_index: 0, page_size: 25 });
  expect(read.status).toBe(200);
  return read.body.result;
}
async function selectAndOpen(page: Page, datasetId: string, run: string, example: string) {
  await page.goto(`/dashboard/develop/${datasetId}/compare/data?runs=${run}&baseline=${run}&run=${run}`);
  // ResultsGrid.jsx sets getRowId=data.example_id and multiRow checkboxes.
  const selected = page.locator(`[data-results-grid-root] .ag-row[row-id="${example}"]`).getByRole('checkbox');
  await expect(selected).toBeVisible({ timeout: UI });
  await selected.check();
  const bar = page.getByTestId('results-selection-bar');
  await expect(bar.getByTestId('selection-count')).toContainText('1 selected');
  const direct = bar.getByRole('button', { name: 'Add to dataset…', exact: true });
  if (await direct.isVisible()) await direct.click();
  else {
    await bar.getByRole('button', { name: 'More actions', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Add to dataset…', exact: true }).click();
  }
  return page.getByRole('dialog', { name: 'Add 1 row to a dataset', exact: true });
}

 test('@DSX-WALK-RES-21: Copy one immutable result into new and existing datasets without crossing organizations', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'DSX-WALK-RES-21', area: 'dsx-walks',
    userGoal: 'Copy the selected result, including its saved input, expected value and output, into my own datasets',
    steps: ['seed two isolated organizations and an immutable two-example SDK run through served APIs',
      'select one result in the real Data grid and copy it to a new dataset',
      'copy the same result to an existing dataset with explicit column mapping and reopen it',
      'refuse organization B reads and copies of organization A results and confirm A remains unchanged'],
    backendChecks: ['both copy responses report one added row, zero failures, trial one and the exact selected example',
      'the dataset table contains exactly the selected input, expected and output values after both UI writes',
      'foreign source, destination and results reads return 404; neither destination acquires extra rows'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(420_000);
  const a = scopes.ownerA;
  const b = scopes.ownerB;
  const suffix = randomUUID().slice(0, 8);
  const source = await dataset(scopes, a, `walk-copy-source-${suffix}`);
  const targetName = `walk-copy-existing-${suffix}`;
  const target = await dataset(scopes, a, targetName);
  const foreign = await dataset(scopes, b, `walk-copy-b-${suffix}`);
  const examples = [randomUUID(), randomUUID()];
  const input = { question: `synthetic-selected-${suffix}` };
  const expected = { answer: 'synthetic-expected' };
  const output = { answer: 'synthetic-output', count: 0, valid: false };
  const version = await scopes.send<{ result: { id: string } }>(a, 'POST', `/model-hub/datasets/${source}/versions/`, {
    items: examples.map((example_id, index) => ({ example_id,
      input: index === 0 ? input : { question: 'synthetic-unselected' }, expected })),
  });
  expect(version.status).toBe(201);
  const run = randomUUID();
  const madeRun = await scopes.send<{ result: { id: string } }>(a, 'POST', '/model-hub/experiment-runs/', {
    run_id: run, dataset_version_id: version.body.result.id,
    expected_items: examples.map(example_id => ({ example_id, trial_number: 1 })),
  });
  expect(madeRun.status).toBe(200);
  expect(madeRun.body.result.id).toBe(run);
  const ingested = await scopes.send(a, 'POST', `/model-hub/experiment-runs/${run}/results/`, {
    items: examples.map((example_id, index) => ({ example_id, trial_number: 1,
      receipt_key: `copy-${suffix}-${index}`, status: 'completed',
      output: index === 0 ? output : { answer: 'must-not-be-copied' }, latency_ms: 12, cost: 0 })),
  });
  expect(ingested.status).toBe(200);
  const seeded = { organizationA: a.organizationId, organizationB: b.organizationId, source, target, foreign,
    version: version.body.result.id, run, examples, newDataset: '' };
  await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify(seeded) });
  const context = await scopes.openContext(browser, a);
  const allowed = new Set([new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin]);
  await context.route('**/*', route => allowed.has(new URL(route.request().url()).origin)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI);
  await page.setViewportSize({ width: 1600, height: 1000 });
  try {
    const newName = `walk-copy-new-${suffix}`;
    for (const destination of ['new', 'existing'] as const) {
      const dialog = await selectAndOpen(page, source, run, examples[0]);
      await expect(dialog).toBeVisible();
      let mapping: Record<string, string>;
      if (destination === 'new') {
        await dialog.getByRole('textbox', { name: 'New dataset name', exact: true }).fill(newName);
        mapping = { inputs: 'input', expected: 'expected', output: 'output' };
      } else {
        await dialog.getByRole('radio', { name: 'Another dataset', exact: true }).check();
        await dialog.getByRole('combobox', { name: 'Target dataset', exact: true }).click();
        await page.getByRole('option', { name: targetName, exact: true }).click();
        const cols = (await table(scopes, a, target)).column_config;
        mapping = Object.fromEntries(['inputs', 'expected', 'output'].map(key => {
          const column = cols.find(c => c.name === (key === 'inputs' ? 'input' : key));
          expect(column).toBeDefined();
          return [key, column!.id];
        }));
      }
      const path = `/model-hub/datasets/${destination === 'new' ? 'new' : target}/rows/from-results/`;
      const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === path && r.request().method() === 'POST');
      await dialog.getByRole('button', { name: `Add 1 row to ${destination === 'new' ? newName : targetName}`, exact: true }).click();
      const response = await responsePromise;
      expect(response.request().postDataJSON()).toMatchObject({ source_dataset_id: source, run_id: run,
        example_ids: [examples[0]], trial: 1, mode: 'copy', column_map: mapping, score_columns: [], expected_from: 'keep' });
      expect(response.status()).toBe(200);
      const copied = (await response.json() as Copy).result;
      expect(copied).toMatchObject({ dataset_name: destination === 'new' ? newName : targetName,
        created: destination === 'new', rows_requested: 1, rows_added: 1, rows_failed: 0, failures: [], trial: 1 });
      expect(copied.rows.map(row => row.example_id)).toEqual([examples[0]]);
      if (destination === 'new') seeded.newDataset = copied.dataset_id;
      else expect(copied.dataset_id).toBe(target);
      await expect(page.getByText(`1 row added to ${copied.dataset_name}`, { exact: true })).toBeVisible();
      const read = await table(scopes, a, copied.dataset_id);
      expect(read.table.map(row => row.row_id)).toEqual([copied.rows[0].row_id]);
      for (const [name, value] of Object.entries({ input, expected, output })) {
        const column = read.column_config.find(c => c.name === name)!;
        expect(column).toBeDefined();
        const cell = read.table[0][column.id] as { cell_value: string };
        expect(JSON.parse(cell.cell_value)).toEqual(value);
      }
      await page.goto(`/dashboard/develop/${copied.dataset_id}?tab=data`);
      await expect(page.getByText(input.question, { exact: false }).first()).toBeVisible();
      await page.reload();
      expect((await table(scopes, a, copied.dataset_id)).table).toEqual(read.table);
    }
    const before = await table(scopes, a, target);
    const body = { source_dataset_id: source, run_id: run, example_ids: [examples[0]], trial: 1,
      mode: 'copy', column_map: { inputs: 'input', expected: 'expected', output: 'output' },
      score_columns: [], expected_from: 'keep', version_description: '', new_dataset_name: `foreign-copy-${suffix}` };
    expect((await scopes.send(b, 'GET', `/model-hub/datasets/${source}/results/?runs=${run}`)).status).toBe(404);
    expect((await scopes.send(b, 'POST', '/model-hub/datasets/new/rows/from-results/', body)).status).toBe(404);
    const foreignColumns = (await table(scopes, b, foreign)).column_config;
    const foreignOutput = foreignColumns.find(c => c.name === 'output')!.id;
    expect((await scopes.send(a, 'POST', `/model-hub/datasets/${foreign}/rows/from-results/`, {
      ...body, new_dataset_name: '', column_map: { output: foreignOutput },
    })).status).toBe(404);
    expect((await table(scopes, b, foreign)).table).toEqual([]);
    expect((await table(scopes, a, target)).table).toEqual(before.table);
    expect((await table(scopes, a, seeded.newDataset)).table).toHaveLength(1);
    await testInfo.attach('copied-ids', { contentType: 'application/json', body: JSON.stringify(seeded) });
  } finally { await context.close(); }
});
