import type { Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py and DatasetCreateEmptyRequestSerializer.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/';
// useDatasetListQuery.js: datasetListQueryConfig and realDatasetListParams.
const LIST = '/model-hub/develops/get-datasets/';
// EvaluationDrawer.jsx useEvalsList: GET with { eval_type: 'user' }.
const evalList = (datasetId: string) => `/model-hub/develops/${datasetId}/get_evals_list/`;
const EVAL_LIST_QUERY = { eval_type: 'user' };
// CommandPalette.jsx Row: data-command is the command id; providers.js
// help.glossary, and the palette's glossary results `glossary.<term key>`.
const GLOSSARY_COMMAND = '[data-command="help.glossary"]';
const termCommand = (key: string) => `[data-command="glossary.${key}"]`;
const ANY_TERM_COMMAND = '[data-command^="glossary."]';
// RowPanel/ShortcutSheet.jsx: the sheet's dialog carries SHORTCUT_SHEET_ATTR
// (utils/keyboard-guards.js); its search hint is built from the listed terms.
const SHEET = '[data-fa-shortcut-sheet]';
const UI_READY = 60_000;

// The cloud walk builds these profiles separately; this selects expectations,
// never product state. Both claim palette.glossary but NOT dataset.collab.reviews.
// matrix-only additionally claims exp.results.matrix with experimentsBasic ON.
const PROFILE = process.env.E2E_F77_GLOSSARY_PROFILE ?? 'default';
if (!['default', 'matrix-only'].includes(PROFILE)) {
  throw new Error('Unknown E2E_F77_GLOSSARY_PROFILE');
}
const MATRIX_ONLY = PROFILE === 'matrix-only';
const SEARCH_HINT = MATRIX_ONLY
  ? 'Search terms (pass^3, baseline, flaky…)'
  : 'Search terms (wrap lines, matched by meaning…)';
const HIDDEN_QUERY = MATRIX_ONLY ? 'path share' : 'pass^3';

// EXPECTATION-ONLY fallibility anchors. Never interpolate these into a request
// body or a seeded value. The expected lists are independent of product imports.
const EXPECTED_TERMS = ['wrapLines', 'matchedByMeaning'];
const EXPECTED_MATRIX_TERMS = [
  'successRate', 'passAt1', 'passHatK', 'reliable', 'flaky', 'trial', 'run',
  'experiment', 'baseline', 'delta', 'improved', 'check', 'costPerTask',
  'totalCost', 'timePerTask', 'carried', 'latencyP90', 'ttfw', 'bargeIn', 'wer',
  'talkRatio', 'deadAir', 'worse', 'anyEvalWorse', 'onlyFails', 'runBar',
  'whyColumn', 'costPerSolved', 'bestValue', 'carriedFrom', 'liveMock',
  'keptTalking', 'pairDiffers', 'matrix', 'tries', 'noRunReliable', 'p50', 'p90',
  'expectedAnswers', 'wrapLines', 'matchedByMeaning',
];
const EXPECTED_FOREIGN_STATUS = 404;
const EXPECTED_NAME_SUFFIX = '';

type Envelope<T> = { status: boolean; result: T };
type Dataset = { actor: ScopeActor; id: string; name: string };
type List = Envelope<{ datasets: { id: string; name: string }[] }>;

// Only loopback runtime traffic; dependency installation is outside the browser.
const offHost = (url: URL) => /^https?:$/.test(url.protocol)
  && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);

test('EVAL-E2E-077: the glossary explains only the words of features this build shows', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-077', area: 'evals',
    userGoal: 'Look up what a word on my screen means and find only terms for features I can use, in my own organization',
    steps: [
      'create a uniquely named dataset in each of two synthetic organizations through the public API',
      'open Datasets and read the organization\'s own saved dataset from the real list',
      'open the saved dataset, focus a control inside its grid and open the command palette, find the Wrap lines term and no term for a hidden results word',
      'open Glossary: what the words mean and read the listed terms and the search hint',
      'search the glossary for a hidden results word, then for a shown one',
      'repeat in the second organization, verify foreign dataset refusals and read back both saved datasets',
    ],
    backendChecks: [
      'each organization\'s Datasets page reads exactly its own saved dataset through the real list endpoint with its own scope headers',
      'both tenants can read their own dataset evaluations and each receives 404 for the other tenant\'s dataset, and Postgres keeps both datasets with their exact names and tenant identities',
    ],
    browserChecks: [
      'the glossary command, the palette results and the Glossary tab list exactly the terms of shown features and none of the hidden results terms',
      'the glossary search hint names only listed terms, and a hidden results word finds no term',
      'in a separate Matrix-only build, Matrix terms are present but pairwise and blind review terms are absent from the palette and sheet',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Provision 120 s + seed/readback 60 s + two tenants x three UI_READY steps (360 s) + headroom 60 s.
  test.setTimeout(600_000);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value, null, 2),
  });
  const datasets: Dataset[] = [];
  for (const [label, actor] of [['a', scopes.ownerA], ['b', scopes.ownerB]] as const) {
    const name = `e2e-glossary-${label}-${suffix}`;
    const created = await scopes.send<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET,
      { new_dataset_name: name });
    expect(created.status).toBe(200);
    datasets.push({ actor, id: created.body.result.dataset_id, name });
  }
  await attach('seeded-identities', datasets.map(({ actor, ...d }) => ({ ...d,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId })));
  const persisted = () => probe.pg<{ id: string; name: string; organization_id: string; workspace_id: string }>(
    'SELECT id, name, organization_id, workspace_id FROM model_hub_dataset WHERE id = ANY($1::uuid[]) ORDER BY id',
    [datasets.map(d => d.id)]);
  const before = await persisted();
  expect(before).toHaveLength(2);

  const glossaryRows = (page: Page) => page.locator(SHEET).locator('[data-glossary]');
  const listedTerms = (page: Page) => glossaryRows(page)
    .evaluateAll(rows => rows.map(row => row.getAttribute('data-glossary')));

  for (const d of datasets) {
    const context = await scopes.openContext(browser, d.actor);
    const blocked = new Set<string>();
    await context.route(offHost, route => {
      blocked.add(new URL(route.request().url()).host);
      return route.abort();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);

    await test.step(`read the saved dataset of ${d.name}`, async () => {
      const listed = page.waitForResponse(response => new URL(response.url()).pathname === LIST
        && response.request().method() === 'GET' && response.status() === 200
        && !new URL(response.url()).searchParams.has('search_text'), { timeout: UI_READY });
      await page.goto('/dashboard/develop', { waitUntil: 'domcontentloaded' });
      const response = await listed;
      const scope = response.request().headers();
      expect(scope['x-organization-id']).toBe(d.actor.organizationId);
      expect(scope['x-workspace-id']).toBe(d.actor.workspaceId);
      const body = await response.json() as List;
      expect(body.result.datasets.map(row => row.name)).toEqual([`${d.name}${EXPECTED_NAME_SUFFIX}`]);
      await ui(page.getByText(d.name, { exact: true })).toBeVisible();
    });

    await test.step('the palette lists shown terms only', async () => {
      await page.goto(`/dashboard/develop/${d.id}`, { waitUntil: 'domcontentloaded' });
      // shortcutOwner.js assigns body/list focus to Falcon, not the DSX palette.
      // DevelopDataV2's shell is not focusable; use a real control inside it,
      // including when the newly created dataset shows its empty state.
      const grid = page.locator('#fa-data-grid-shell');
      await ui(grid).toBeVisible();
      const gridFocus = grid.locator(
        'button:enabled:visible, input:enabled:visible, [tabindex]:not([disabled]):visible',
      ).first();
      await ui(gridFocus).toBeVisible();
      const command = page.getByRole('combobox', { name: 'Command' });
      // The palette mounts lazily; restore grid focus before each opening attempt.
      await expect(async () => {
        if (!(await command.isVisible())) {
          await gridFocus.focus({ timeout: 2_000 });
          await expect(gridFocus).toBeFocused({ timeout: 2_000 });
          await page.keyboard.press('Control+k');
        }
        await expect(command).toBeVisible({ timeout: 2_000 });
      }).toPass({ timeout: UI_READY });
      await command.fill('wrap lines');
      await ui(page.locator(termCommand('wrapLines'))).toHaveRole('option');
      await ui(page.locator(termCommand('wrapLines'))).toContainText('What does “Wrap lines” mean?');
      if (MATRIX_ONLY) {
        await command.fill('matrix');
        await ui(page.locator(termCommand('matrix'))).toHaveRole('option');
      }
      for (const query of [HIDDEN_QUERY, 'pairwise', 'blind']) {
        await command.fill(query);
        await ui(page.locator(termCommand('wrapLines'))).toHaveCount(0);
        await ui(page.locator(ANY_TERM_COMMAND)).toHaveCount(0);
      }
      await command.fill('glossary');
      const glossaryCommand = page.locator(GLOSSARY_COMMAND);
      await ui(glossaryCommand).toHaveCount(1);
      await glossaryCommand.click();
      await ui(command).toBeHidden();
    });

    await test.step('the Glossary tab lists shown terms only', async () => {
      const sheet = page.locator(SHEET);
      await ui(sheet).toBeVisible();
      await ui(sheet).toHaveRole('dialog');
      await ui(sheet.getByRole('tab', { name: 'Glossary' })).toHaveAttribute('aria-selected', 'true');
      await expect.poll(() => listedTerms(page), { timeout: UI_READY })
        .toEqual(MATRIX_ONLY ? EXPECTED_MATRIX_TERMS : EXPECTED_TERMS);
      const search = sheet.getByRole('textbox', { name: 'Search terms' });
      await ui(search).toHaveAttribute('placeholder', SEARCH_HINT);
      for (const query of [HIDDEN_QUERY, 'pairwise', 'blind']) {
        await search.fill(query);
        await ui(sheet.getByText(`No term matches “${query}”.`, { exact: true })).toBeVisible();
        await ui(glossaryRows(page)).toHaveCount(0);
      }
      await search.fill('meaning');
      await expect.poll(() => listedTerms(page), { timeout: UI_READY }).toEqual(['matchedByMeaning']);
      await page.keyboard.press('Escape');
      await ui(sheet).toBeHidden();
    });
    await attach(`off-host-blocked-${d.id}`, [...blocked].sort());
    await context.close();
  }

  await test.step('persisted datasets and tenant refusals', async () => {
    for (const d of datasets) {
      const own = await scopes.send<List>(d.actor, 'GET', LIST, undefined, { page: 0, page_size: 25 });
      expect(own.status).toBe(200);
      expect(own.body.result.datasets.map(row => ({ id: row.id, name: row.name })))
        .toEqual([{ id: d.id, name: `${d.name}${EXPECTED_NAME_SUFFIX}` }]);
      expect((await scopes.send(d.actor, 'GET', evalList(d.id), undefined, EVAL_LIST_QUERY)).status).toBe(200);
      const foreign = datasets.find(other => other.id !== d.id)!;
      const denied = await scopes.send(d.actor, 'GET', evalList(foreign.id), undefined, EVAL_LIST_QUERY);
      expect(denied.status).toBe(EXPECTED_FOREIGN_STATUS);
      expect(JSON.stringify(denied.body)).not.toContain(foreign.name);
    }
    expect(await persisted()).toEqual(before);
    for (const d of datasets) expect(before.find(row => row.id === d.id)).toEqual({
      id: d.id, name: `${d.name}${EXPECTED_NAME_SUFFIX}`,
      organization_id: d.actor.organizationId, workspace_id: d.actor.workspaceId,
    });
    await attach('persisted-after-glossary', await persisted());
  });
});
