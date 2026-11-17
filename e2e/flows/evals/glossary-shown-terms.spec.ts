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
// frontend/src/sections/common/CommandPalette/CommandPalette.jsx:142-149,388:
// each glossary option carries its term key in data-command.
const ANY_TERM_COMMAND = '[data-command^="glossary."]';
// RowPanel/ShortcutSheet.jsx: the sheet's dialog carries SHORTCUT_SHEET_ATTR
// (utils/keyboard-guards.js); its search hint is built from the listed terms.
const SHEET = '[data-fa-shortcut-sheet]';
const UI_READY = 60_000;

// frontend/src/sections/common/RowPanel/ShortcutSheet.jsx:274-288 selects
// the first three shown examples; frontend/src/launch/launchSwitch.js:98 shows every surface.
const SEARCH_HINT = 'Search terms (pass^3, baseline, flaky…)';

// EXPECTATION-ONLY fallibility anchors. Never interpolate these into a request
// body or a seeded value. The expected lists are independent of product imports.
// frontend/src/sections/common/ux/glossary.js:32-546,569 excludes absent entries;
// frontend/src/launch/launchSwitch.js:153-159 admits terms with a shown surface.
const EXPECTED_TERMS = [
  'successRate', 'passAt1', 'passHatK', 'reliable', 'flaky', 'trial', 'run',
  'experiment', 'baseline', 'delta', 'ci95', 'withinNoise', 'regressed', 'improved',
  'judge', 'check', 'costPerTask', 'totalCost', 'timePerTask', 'firstWrongStep',
  'firstDifference', 'milestone', 'differ', 'carried', 'confidence', 'preview',
  'mcp', 'traceIds', 'graphQuality', 'sideEffects', 'pathShare', 'graphDiff',
  'latencyP90', 'ttfw', 'bargeIn', 'wer', 'talkRatio', 'deadAir', 'textOnlyDepth',
  'fullVoiceDepth', 'otelPassthrough', 'worse', 'anyEvalWorse', 'aligned',
  'onlyFails', 'runBar', 'whyColumn', 'pairwise', 'blind', 'costPerSolved',
  'bestValue', 'bothRan', 'rowChecks', 'carriedFrom', 'liveMock', 'lanes', 'mapKey',
  'keptTalking', 'pairDiffers', 'level', 'tooCloseToCall', 'isolate', 'paths',
  'matrix', 'tries', 'newest', 'previousVersion', 'noRunReliable', 'p50', 'p90',
  'endStateChecks', 'dryRun', 'startState', 'asTasks', 'asConversations', 'splits',
  'expectedAnswers', 'wrapLines', 'matchedByMeaning',
];
// frontend/src/sections/common/ux/glossary.js:563-586 ranks names before text;
// frontend/src/sections/common/CommandPalette/CommandPalette.jsx:383-385 keeps two hits.
const EXPECTED_SEARCH_RESULTS = [
  { query: 'matrix', palette: ['matrix'], sheet: ['matrix'] },
  { query: 'pass^3', palette: ['passHatK', 'reliable'], sheet: ['passHatK', 'reliable', 'noRunReliable'] },
  { query: 'path share', palette: ['pathShare', 'paths'], sheet: ['pathShare', 'paths'] },
  { query: 'pairwise', palette: ['pairwise'], sheet: ['pairwise'] },
  { query: 'blind', palette: ['blind'], sheet: ['blind'] },
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

  // frontend/src/sections/common/RowPanel/ShortcutSheet.jsx:229-241 exposes the ordered term keys.
  const glossaryRows = (page: Page) => page.locator(SHEET).locator('[data-glossary]');
  const listedTerms = (page: Page) => glossaryRows(page)
    .evaluateAll(rows => rows.map(row => row.getAttribute('data-glossary')));
  // frontend/src/sections/common/CommandPalette/CommandPalette.jsx:142-149,388 identifies term options.
  const listedPaletteTerms = (page: Page) => page.getByRole('option').and(page.locator(ANY_TERM_COMMAND))
    .evaluateAll(rows => rows.map(row => row.getAttribute('data-command')?.slice('glossary.'.length)));

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
      // frontend/src/sections/common/CommandPalette/CommandPalette.jsx:549-551 names the combobox.
      const command = page.getByRole('combobox', { name: 'Command', exact: true });
      // The palette mounts lazily; restore grid focus before each opening attempt.
      await expect(async () => {
        if (!(await command.isVisible())) {
          await gridFocus.focus({ timeout: 2_000 });
          await expect(gridFocus).toBeFocused({ timeout: 2_000 });
          await page.keyboard.press('Control+Shift+P');
        }
        await expect(command).toBeVisible({ timeout: 2_000 });
      }).toPass({ timeout: UI_READY });
      await command.fill('wrap lines');
      // frontend/src/sections/common/CommandPalette/CommandPalette.jsx:142,171-175,390-391 renders label and hint.
      // frontend/src/sections/common/ux/glossary.js:534-537 supplies the exact name.
      const wrapTerm = page.getByRole('option', {
        name: 'What does “Wrap lines” mean? Show long cell text on several lines instead of cutting it off.',
        exact: true,
      });
      await ui(wrapTerm).toBeVisible();
      await ui(wrapTerm).toHaveAttribute('data-command', 'glossary.wrapLines');
      await expect.poll(() => listedPaletteTerms(page), { timeout: UI_READY }).toEqual(['wrapLines']);
      for (const { query, palette } of EXPECTED_SEARCH_RESULTS) {
        await command.fill(query);
        await expect.poll(() => listedPaletteTerms(page), { timeout: UI_READY }).toEqual(palette);
      }
      await command.fill('glossary');
      // frontend/src/sections/common/CommandPalette/providers.js:82-87 names this command;
      // frontend/src/sections/common/CommandPalette/CommandPalette.jsx:142 gives it the option role.
      const glossaryCommand = page.getByRole('option', { name: 'Glossary: what the words mean', exact: true });
      await ui(glossaryCommand).toHaveCount(1);
      await glossaryCommand.click();
      await ui(command).toBeHidden();
    });

    await test.step('the Glossary tab lists shown terms only', async () => {
      const sheet = page.locator(SHEET);
      await ui(sheet).toBeVisible();
      await ui(sheet).toHaveRole('dialog');
      // frontend/src/sections/common/RowPanel/ShortcutSheet.jsx:360 names the tab.
      await ui(sheet.getByRole('tab', { name: 'Glossary', exact: true })).toHaveAttribute('aria-selected', 'true');
      await expect.poll(() => listedTerms(page), { timeout: UI_READY }).toEqual(EXPECTED_TERMS);
      // frontend/src/sections/common/RowPanel/ShortcutSheet.jsx:363-376 names the search field.
      const search = sheet.getByRole('textbox', { name: 'Search terms', exact: true });
      await ui(search).toHaveAttribute('placeholder', SEARCH_HINT);
      for (const { query, sheet: expected } of EXPECTED_SEARCH_RESULTS) {
        await search.fill(query);
        await expect.poll(() => listedTerms(page), { timeout: UI_READY }).toEqual(expected);
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
