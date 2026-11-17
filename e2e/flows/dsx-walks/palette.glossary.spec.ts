import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

const LIST = '/model-hub/develops/get-datasets/';
const evalList = (id: string) => `/model-hub/develops/${id}/get_evals_list/`;
type DatasetList = { result: { datasets: { id: string; name: string }[] } };
const definitions = [
  { key: 'wrapLines', label: 'Wrap lines', short: 'Show long cell text on several lines instead of cutting it off.' },
  { key: 'judge', label: 'Judge', short: 'An LLM that scores outputs against a rubric. Check its agreement with reviewers.' },
  { key: 'expectedAnswers', label: 'Expected answers', short: "The answer each row should get. Evals compare a run's output with it." },
];

test('@DSX-WALK-EVAL-87: navigate from the command palette to exact glossary definitions while keeping dataset context tenant-scoped', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-87', area: 'dsx-walks',
    userGoal: 'Understand a displayed dataset or evaluation term through the command palette without leaving my organization',
    steps: ['seed one private dataset in each organization using the served API',
      'focus the dataset grid and open its command palette',
      'choose the Wrap lines definition and read the Glossary tab',
      'search Judge and Expected answers and read their exact definitions',
      'verify unknown-term feedback, close the sheet and check backend tenant boundaries'],
    backendChecks: ['the dataset list contains exactly the seeded owned id and name',
      'own evaluation-list GET is 200 with an empty list; foreign dataset evaluation-list GET is exactly 404',
      'glossary navigation does not write to model-hub endpoints or change the owned dataset list'],
    browserChecks: ['the palette item opens a Glossary dialog with Wrap lines prefilled',
      'the three visible definitions match the shipped text exactly',
      'an unknown glossary term produces the exact no-match message and no glossary entries'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  test.setTimeout(600_000);
  const ui = expect.configure({ timeout: 60_000 });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasets = [];
  for (const [tag, actor] of [['a', scopes.ownerA], ['b', scopes.ownerB]] as const) {
    const name = `dsx-glossary-${tag}-${suffix}`;
    const created = await scopes.send<{ result: { dataset_id: string } }>(actor, 'POST',
      '/model-hub/develops/create-empty-dataset/', { new_dataset_name: name });
    expect(created.status).toBe(200);
    datasets.push({ actor, name, id: created.body.result.dataset_id });
  }
  await testInfo.attach('seeded-identities', { contentType: 'application/json', body: JSON.stringify(
    datasets.map(({ actor, ...ids }) => ({ ...ids, organizationId: actor.organizationId, workspaceId: actor.workspaceId }))) });
  for (const own of datasets) {
    const foreign = datasets.find(d => d.id !== own.id)!;
    const listBefore = await scopes.send<DatasetList>(own.actor, 'GET', LIST, undefined, { page: 0, page_size: 25 });
    expect(listBefore.status).toBe(200);
    expect(listBefore.body.result.datasets.map(({ id, name }) => ({ id, name }))).toEqual([{ id: own.id, name: own.name }]);
    const context = await scopes.openContext(browser, own.actor);
    await context.route(url => ['http:', 'https:'].includes(url.protocol) &&
      ![new URL(E2E.appUrl).origin, new URL(E2E.apiUrl).origin].includes(url.origin), route => route.abort());
    try {
      const page = await context.newPage();
      page.setDefaultTimeout(60_000);
      await page.goto(`/dashboard/develop/${own.id}`, { waitUntil: 'domcontentloaded' });
      // shortcutOwner.js requires grid focus, not body/Falcon focus.
      const grid = page.locator('#fa-data-grid-shell');
      await ui(grid).toBeVisible();
      const focus = grid.locator('button:enabled:visible, input:enabled:visible, [tabindex]:not([disabled]):visible').first();
      const command = page.getByRole('combobox', { name: 'Command', exact: true });
      await expect(async () => {
        if (!(await command.isVisible())) {
          await focus.focus({ timeout: 2_000 });
          await expect(focus).toBeFocused({ timeout: 2_000 });
          await page.keyboard.press('Control+Shift+P');
        }
        await expect(command).toBeVisible({ timeout: 2_000 });
      }).toPass({ timeout: 60_000 });
      const writes: string[] = [];
      page.on('request', request => {
        const path = new URL(request.url()).pathname;
        if (path.startsWith('/model-hub/') && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(request.method())) writes.push(path);
      });
      await command.fill('wrap lines');
      // CommandPalette.jsx: the term's exact label and hint form its option name.
      const option = page.getByRole('option', { name: 'What does “Wrap lines” mean? Show long cell text on several lines instead of cutting it off.', exact: true });
      await ui(option).toHaveAttribute('data-command', 'glossary.wrapLines');
      await option.click();
      await ui(command).toBeHidden();
      // ShortcutSheet.jsx uses the shared shortcut-sheet attribute and labeled search.
      const sheet = page.locator('[data-fa-shortcut-sheet]');
      await ui(sheet).toHaveRole('dialog');
      await ui(sheet.getByRole('tab', { name: 'Glossary', exact: true })).toHaveAttribute('aria-selected', 'true');
      const search = sheet.getByRole('textbox', { name: 'Search terms', exact: true });
      await ui(search).toHaveValue('Wrap lines');
      for (const term of definitions) {
        await search.fill(term.label);
        const entry = sheet.locator(`[data-glossary="${term.key}"]`);
        await ui(entry.getByText(term.label, { exact: true })).toBeVisible();
        await ui(entry.getByText(term.short, { exact: true })).toBeVisible();
      }
      const unknown = `dsx-no-term-${suffix}`;
      await search.fill(unknown);
      await ui(sheet.locator('[data-glossary]')).toHaveCount(0);
      await ui(sheet.getByText(`No term matches “${unknown}”.`, { exact: true })).toBeVisible();
      await sheet.getByRole('button', { name: 'Close', exact: true }).click();
      await ui(sheet).toBeHidden();
      expect(new URL(page.url()).pathname).toBe(`/dashboard/develop/${own.id}`);
      expect(writes).toEqual([]);
      await testInfo.attach(`glossary-${own.id}`, { contentType: 'application/json', body: JSON.stringify({
        datasetId: own.id, definitions, writes,
      }) });
    } finally { await context.close(); }
    const ownEvals = await scopes.send<{ result: { evals: unknown[] } }>(own.actor, 'GET', evalList(own.id), undefined, { eval_type: 'user' });
    expect(ownEvals.status).toBe(200);
    expect(ownEvals.body.result.evals).toEqual([]);
    const denied = await scopes.send(own.actor, 'GET', evalList(foreign.id), undefined, { eval_type: 'user' });
    expect(denied.status).toBe(404);
    expect(JSON.stringify(denied.body)).not.toContain(foreign.name);
    const listAfter = await scopes.send<DatasetList>(own.actor, 'GET', LIST, undefined, { page: 0, page_size: 25 });
    expect(listAfter.status).toBe(200);
    expect(listAfter.body.result.datasets.map(({ id, name }) => ({ id, name }))).toEqual([{ id: own.id, name: own.name }]);
  }
});
