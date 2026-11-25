import type { Request } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedEnvironment } from '../../lib/environment-seed';

// Coverage this flow does not claim, and what covers it instead:
// - A JSON-messages conversation cell, a row whose cell does not parse, a deleted row or dataset,
//   the import's size budget, and the org and workspace fence: the backend's
//   simulate/tests/test_evidence_datasets.py.
// - Select all and more than 20 rows disabling the action, and an audio column never offered as
//   the conversation: DevelopDataSelectionActive.evidence.test.jsx and
//   CreateEnvironmentTestDialog.test.jsx.
// - The builder's draft and the add to the suite: as in SIM-E2E-003 and SIM-E2E-004, this stack
//   starts no sandbox; test_evidence_dispatch.py drafts a dataset import from its expected outcome.
//
// Deliberate failures, one run per check; each run must fail on that check and no earlier one:
// 1. tick the decoy row too: the POST names three rows.
// 2. seed the second chat as JSON cut short: the POST is unchanged, but its case cannot be
//    fingerprinted, so it is stored with a blank digest.
// 3. read the dataset's own cells instead of the import's rows: the first ask is found, which
//    proves the probe reads the text it searches.
// 4. seed the first chat without the email: the row reads without it and no finding names it.
// 5. skip the cell edit: the edited row does not read as changed.
// 6. serve the browser's review a bundle marked as Observe sessions: the API lane is unchanged,
//    but no card is marked "Dataset row".

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Copy, pinned from frontend/src/sections/simulate/environments/evidence/evidence.constants.js.
const ACTION = 'Create environment test';
const DIALOG_BODY = /rows? from this dataset become a draft import/;
const NOT_PRODUCTION = /Not marked as production, so these become tests, not regression tests/;
const NOT_EXACT_REPLAY = /A dataset row doesn't hold the seed records/;
const CHANGED = 'The source changed since it was imported.';
// Routes, pinned from model_hub/urls.py and simulate/views/harness_environment.py.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
const addColumns = (dataset: string) => `/model-hub/develops/${dataset}/add_columns/`;
const addRows = (dataset: string) => `/model-hub/develops/${dataset}/add_rows/`;
const updateCell = (dataset: string) => `/model-hub/develops/${dataset}/update_cell_value/`;
const imports = (env: string) => `/simulate/api/harness-environments/${env}/evidence-imports/`;

interface Case {
  id: string;
  source_id: string;
  status: string;
  changed_since_import: boolean | null;
  transcript: { status: string; turns: { role: string; text: string }[] };
  labels: { status: string; labels: { name: string; kind: string; value: unknown; notes: string; target: string }[] };
  expected_outcome: { status: string; reason: string; text: string };
}
interface Bundle {
  id: string;
  source_kind: string;
  dataset_id: string | null;
  production: boolean;
  cases: Case[];
  redaction: { method: string; total: number;
    findings: { case_id: string; part: string; value: string; replacement: string }[] };
}

test(
  'SIM-E2E-005: a person picks dataset rows and their columns and reviews them in an environment as evidence read live from the dataset',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-005',
      area: 'simulate',
      userGoal:
        'A person picks rows in a dataset, says which column is the conversation, the labels and the expected outcome, and reviews them in an environment as evidence read live from the dataset',
      steps: [
        'seed a built, versioned environment (lib/environment-seed.ts) and a dataset with text columns chat, intent and expected, and three rows',
        "open the dataset and tick the two rows whose chat holds a minted ask",
        'choose Create environment test, pick the environment, chat as the conversation, intent as a label and expected as the expected outcome, leave production off and import',
        "land on the environment's Scenarios tab with the import's review open",
        "edit one picked row's expected cell through the dataset API and reload the review",
      ],
      backendChecks: [
        'from the Import click on, the only mutating /simulate/ request is one POST to the environment evidence-imports route, answered 201, naming the dataset, the two ticked rows, the three picked columns and production false',
        'simulate_evidence_import has one row for the actor org and the seeded environment: source_kind dataset, no project, the seeded dataset, not declared production, with the picked column ids; its two cases are pending, store no transcript and carry a 64-character digest',
        'no simulate_evidence_import or simulate_evidence_import_case row holds the minted asks, email or expected text',
        'the bundle read again shows the first row as the dataset holds it now, a transcript finding that replaces the minted email with <EMAIL_ADDRESS>, the intent label on the row, the minted expected text as the expected outcome, and is not production',
        'after the cell edit the bundle says only the edited row changed since import, and shows its new expected text',
        'the review shows two cards marked "Dataset row", each with its expected outcome, "2 rows from a dataset" and that they are not marked production; after the reload only the edited card says the source changed',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Seeding + seven UI_READY browser steps (grid, two ticks, dialog, import, review, reload).
    test.setTimeout(420_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const asks = [`e2e-ask-${suffix}-a`, `e2e-ask-${suffix}-b`];
    const email = `e2e-${suffix}@example.com`;
    const expected = [`e2e-expected-${suffix}-a`, `e2e-expected-${suffix}-b`];
    const edited = `${expected[0]}-edited`;
    const rowIds = [randomUUID(), randomUUID(), randomUUID()];

    const environment = seedEnvironment({
      organizationId: actor.organizationId, workspaceId: actor.workspaceId, name: `e2e-sim5-env-${suffix}`,
    });
    const env = environment.environmentId;

    // Bodies pinned from model_hub/serializers/contracts.py: CreateEmptyDatasetRequestSerializer,
    // DatasetAddColumnsRequestSerializer ({name, data_type} read by AddColumnsView) and
    // DatasetAddRowsRequestSerializer (rows keep the id given; cells name their column).
    const created = await actor.api.post<{ result: { dataset_id: string } }>(CREATE_DATASET, {
      new_dataset_name: `e2e-sim5-${suffix}`, row: 0,
    });
    const datasetId = created.result.dataset_id;
    await actor.api.post(addColumns(datasetId), {
      new_columns_data: ['chat', 'intent', 'expected'].map((name) => ({ name, data_type: 'text' })),
    });
    const row = (id: string, chat: string, intent: string, outcome: string) => ({
      id, cells: [{ column_name: 'chat', value: chat }, { column_name: 'intent', value: intent },
        { column_name: 'expected', value: outcome }],
    });
    await actor.api.post(addRows(datasetId), { rows: [
      row(rowIds[0], `User: ${asks[0]}: my refund never arrived, write to ${email}\nAgent: I have issued a second refund.`,
        'refund', expected[0]),
      row(rowIds[1], `User: ${asks[1]}: where is my parcel?\nAgent: It left the depot today.`, 'delivery', expected[1]),
      // The decoy: never ticked, so it must never be imported.
      row(rowIds[2], `User: e2e-decoy-${suffix}: hello\nAgent: Hello.`, 'greeting', 'Say hello.'),
    ] });
    const columnRows = await probe.pg<{ id: string; name: string }>(
      'SELECT id::text, name FROM model_hub_column WHERE dataset_id = $1 AND deleted = false', [datasetId]);
    const column = Object.fromEntries(columnRows.map((one) => [one.name, one.id]));
    expect(Object.keys(column).sort()).toEqual(['chat', 'expected', 'intent']);
    await testInfo.attach('seeded', { contentType: 'application/json',
      body: JSON.stringify({ environment, datasetId, rowIds, column, asks, expected }) });

    await test.step('tick the two rows holding the asks', async () => {
      await page.goto(`/dashboard/develop/${datasetId}?tab=data`);
      // DevelopDataV2's getRowId is the row id, so each row is addressed by the id it was seeded with.
      for (const id of rowIds.slice(0, 2)) {
        const checkbox = page.locator(`.ag-row[row-id="${id}"] .ag-checkbox-input`).first();
        // AG Grid redraws the cell as it selects the row, so check()'s own read of
        // the old input can miss it; the selected row below is the assertion.
        await checkbox.click({ force: true, timeout: UI_READY });
        await expect(page.locator(`.ag-row.ag-row-selected[row-id="${id}"]`).first()).toBeVisible({ timeout: UI_READY });
      }
    });

    // Check 1 records from here: every request the import makes.
    const sent: Request[] = [];
    page.on('request', (request) => sent.push(request));

    let bundle!: Bundle;
    await test.step('pick the environment and the columns, and import', async () => {
      await page.getByRole('button', { name: ACTION }).click({ timeout: UI_READY });
      const dialog = page.getByRole('dialog').filter({ hasText: DIALOG_BODY });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('combobox', { name: 'Environment' }).click();
      await page.getByRole('option', { name: environment.name }).click();
      await dialog.getByRole('combobox', { name: 'Conversation column' }).click();
      await page.getByRole('option', { name: 'chat', exact: true }).click({ timeout: UI_READY });
      await dialog.getByRole('combobox', { name: /^Label columns/ }).click();
      await page.getByRole('option', { name: 'intent', exact: true }).click();
      await page.keyboard.press('Escape');
      await dialog.getByRole('combobox', { name: /^Expected outcome column/ }).click();
      await page.getByRole('option', { name: 'expected', exact: true }).click();
      const answered = page.waitForResponse((r) => r.url().endsWith(imports(env))
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: ACTION }).click();
      const response = await answered;
      expect(response.status()).toBe(201);
      bundle = await response.json() as Bundle;
      await expect(page).toHaveURL(new RegExp(`/simulate/environments/${env}\\?tab=scenarios&import=${bundle.id}$`), { timeout: UI_READY });
    });
    await testInfo.attach('import', { contentType: 'application/json', body: JSON.stringify({ importId: bundle.id }) });

    // Check 1.
    const mutating = sent.filter((r) => r.url().startsWith(E2E.apiUrl) && r.url().includes('/simulate/')
      && r.method() !== 'GET');
    expect(mutating.map((r) => `${r.method()} ${new URL(r.url()).pathname}`)).toEqual([`POST ${imports(env)}`]);
    const posted = mutating[0].postDataJSON() as { ids: string[] } & Record<string, unknown>;
    expect([...posted.ids].sort()).toEqual(rowIds.slice(0, 2).sort());
    expect(posted).toEqual({
      source_kind: 'dataset', dataset_id: datasetId, ids: posted.ids,
      columns: { conversation: column.chat, labels: [column.intent], expected: column.expected },
      production: false,
    });

    // Check 2.
    const rows = await probe.pg<{ organization_id: string; project_id: string | null; dataset_id: string;
      source_kind: string; declared_production: boolean; dataset_columns: string }>(
      `SELECT organization_id::text, project_id::text, dataset_id::text, source_kind, declared_production,
              dataset_columns::text FROM simulate_evidence_import WHERE environment_id = $1`, [env]);
    expect(rows.map(({ dataset_columns: _, ...rest }) => rest)).toEqual([{ organization_id: actor.organizationId,
      project_id: null, dataset_id: datasetId, source_kind: 'dataset', declared_production: false }]);
    expect(JSON.parse(rows[0].dataset_columns)).toEqual(
      { conversation: column.chat, labels: [column.intent], expected: column.expected });
    const cases = await probe.pg<{ source_id: string; status: string; stored: boolean; digest: number }>(
      `SELECT source_id, status, transcript IS NOT NULL AS stored, length(digest) AS digest
         FROM simulate_evidence_import_case WHERE evidence_import_id = $1 ORDER BY position`, [bundle.id]);
    expect(cases.map((one) => one.source_id).sort()).toEqual(rowIds.slice(0, 2).sort());
    expect(cases.map(({ source_id: _, ...rest }) => rest)).toEqual(
      [0, 1].map(() => ({ status: 'pending', stored: false, digest: 64 })));

    // Check 3: nothing of the rows is stored; they are read again on every view.
    const kept = await probe.pg<{ text: string }>(
      `SELECT row_to_json(i)::text AS text FROM simulate_evidence_import i WHERE i.id = $1
        UNION ALL
       SELECT row_to_json(c)::text FROM simulate_evidence_import_case c WHERE c.evidence_import_id = $1`, [bundle.id]);
    expect(kept).toHaveLength(3);
    for (const minted of [...asks, email, ...expected]) {
      expect(kept.map((one) => one.text).join('\n')).not.toContain(minted);
    }

    // Check 4: read again through the route the review reads. As for Observe, the review shows
    // the source as it is now and lists what redaction replaces before any of it becomes scenario
    // text (only a transcript, which has no source to read again, stores redacted text).
    const read = () => actor.api.get<Bundle>(`${imports(env)}${bundle.id}/`);
    const caseOf = (got: Bundle, rowId: string) => got.cases.find((one) => one.source_id === rowId)!;
    const again = await read();
    const first = caseOf(again, rowIds[0]);
    expect(first.transcript.turns.map((t) => [t.role, t.text])).toEqual([
      ['user', `${asks[0]}: my refund never arrived, write to ${email}`],
      ['agent', 'I have issued a second refund.'],
    ]);
    expect(again.redaction.findings.filter((one) => one.case_id === first.id && one.part === 'transcript')
      .map((one) => [one.value, one.replacement])).toEqual([[email, '<EMAIL_ADDRESS>']]);
    expect(first.labels.labels).toEqual([{ name: 'intent', kind: 'text', value: 'refund', notes: '', target: 'row' }]);
    expect(first.expected_outcome).toEqual({ status: 'present', reason: '', text: expected[0] });
    expect([again.source_kind, again.dataset_id, again.production]).toEqual(['dataset', datasetId, false]);

    const review = page.getByTestId('evidence-review');
    const card = (got: Bundle, rowId: string) => review.getByTestId(`evidence-case-${caseOf(got, rowId).id}`);
    await test.step('read the review', async () => {
      // Check 6, before the edit.
      await expect(review.locator('[data-testid^="evidence-case-"]')).toHaveCount(2, { timeout: UI_READY });
      await expect(review).toContainText('2 rows from a dataset');
      await expect(review.getByTestId('evidence-dataset')).toContainText(NOT_PRODUCTION);
      await expect(review).toContainText(NOT_EXACT_REPLAY);
      for (const [index, rowId] of rowIds.slice(0, 2).entries()) {
        await expect(card(again, rowId)).toContainText('Dataset row');
        await expect(card(again, rowId)).not.toContainText('from production');
        // EvidenceCaseCard.jsx: one "Has:" line, parts in PART_LABELS order.
        await expect(card(again, rowId)).toContainText('Has: Transcript; Labels; Expected outcome');
        await expect(card(again, rowId).getByTestId('evidence-expected-outcome')).toHaveText(
          `Expected outcome: ${expected[index]}`);
        await expect(card(again, rowId)).not.toContainText(CHANGED);
      }
    });

    // Body pinned from DatasetUpdateCellValueRequestSerializer: the call the dataset grid makes
    // on a committed edit (catalog-column-lifecycle.spec.ts captures it).
    await actor.api.post(updateCell(datasetId), { row_id: rowIds[0], column_id: column.expected, new_value: edited });

    // Check 5.
    const after = await read();
    expect(caseOf(after, rowIds[0]).changed_since_import).toBe(true);
    expect(caseOf(after, rowIds[0]).expected_outcome.text).toBe(edited);
    expect(caseOf(after, rowIds[1]).changed_since_import).toBe(false);

    await test.step('reload the review', async () => {
      // Check 6, after the edit.
      await page.reload();
      await expect(card(after, rowIds[0])).toContainText(CHANGED, { timeout: UI_READY });
      await expect(card(after, rowIds[0]).getByTestId('evidence-expected-outcome')).toHaveText(
        `Expected outcome: ${edited}`);
      await expect(card(after, rowIds[1])).not.toContainText(CHANGED);
    });
  },
);
