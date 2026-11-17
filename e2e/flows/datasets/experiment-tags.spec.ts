import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { CREATE, ROWS, resultCells, seedScope } from './experiment-scope-seed';

const UI_READY = 60_000;
const TAGS = '/model-hub/tags/';
type Tag = { id: string; name: string; color: string };
type Registry = { result: { results: Tag[]; experiments: Record<string, string[]> } };

test('EXP-E2E-010: tag an experiment, then change its tags', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'EXP-E2E-010', area: 'experiments',
    userGoal: "Put tags on an experiment from its Edit dialog, see them on the Experiments tab, change them later, and trust the workspace's tags are private to the organization.",
    steps: ['seed two synthetic organizations and a prompt backed by the managed mock-llm echo server',
      'create an experiment in the wizard',
      'open Edit… on the Experiments tab row, create two tags in the tag picker and save',
      'reload and see both tags on the row, on the page and from the server',
      'open Edit… again, take one tag off and save',
      "refuse the other organization a read, an edit, a delete and a tag change of the first organization's tags and experiment"],
    backendChecks: ['each new tag is a POST of its name and the stored tag keeps it in the workspace',
      "the save is a PUT of the experiment's tag ids and the stored links match it",
      "the other organization's tag list has none of the tags; its PATCH and DELETE of a tag and its PUT of the experiment's tags answer 404 and change nothing"],
    browserChecks: ['the Edit experiment dialog offers Add tags and creates a tag from the typed name',
      "the Experiments tab row shows the experiment's tags after a reload and after the change"],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA;
  const seed = await seedScope(actor, probe, suffix, scopeActors);
  const name = `e2e-tags-${suffix}`;
  const first = `candidate-${suffix}`;
  const second = `golden-${suffix}`;
  const context = await scopeActors.openContext(browser, actor);
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  let id = '';
  const tagIds: Record<string, string> = {};
  const links = async () => (await probe.pg<{ name: string }>(
    `SELECT t.name FROM model_hub_experiment_tag l JOIN model_hub_workspace_tag t ON t.id = l.tag_id
     WHERE l.experiment_id=$1 ORDER BY t.name`, [id])).map(row => row.name);
  const openEdit = async () => {
    const row = page.getByRole('row')
      .filter({ has: page.getByText(name, { exact: true }) }).first();
    await expect(row).toBeVisible({ timeout: UI_READY });
    await row.hover();
    await row.getByRole('button', { name: /actions$/ }).first().click();
    await page.getByRole('menuitem', { name: /^Edit/ }).click();
    const dialog = page.getByRole('dialog', { name: 'Edit experiment' });
    await expect(dialog).toBeVisible({ timeout: UI_READY });
    return dialog;
  };
  // The tag picker is a popover. While it is open, MUI hides everything behind it from the accessibility
  // tree, the Edit dialog included, so no role query reaches the dialog. Close it from inside (Escape in
  // its search box, again while it is still shown) and see it closed before reading the dialog.
  const closePicker = async (picker: Locator) => {
    await expect(async () => {
      if (await picker.isVisible()) {
        await picker.getByRole('textbox', { name: 'Search tags' }).press('Escape', { timeout: 2_000 });
      }
      await expect(picker).toBeHidden({ timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
  };
  try {
    await test.step('create an experiment', async () => {
      const query = new URLSearchParams({ tab: 'experiments', new_experiment: '1',
        source: 'prompt', prompt_id: seed.promptId, prompt_version: seed.promptVersion });
      await page.goto(`/dashboard/develop/${seed.datasetId}?${query}`, { waitUntil: 'domcontentloaded' });
      await fillExperimentHead(page, { name });
      // Continue stays on a step until that step is complete, and the run the
      // link seeds (its prompt version and model) fills in after the page
      // opens. So press Continue only while a step is still shown, until the
      // next one is: never twice on the same step, never past one.
      const continueFrom = async (step: Locator, next: Locator) => {
        await expect(async () => {
          if (await step.isVisible()) {
            await page.getByRole('button', { name: /^Continue/ }).click({ timeout: 2_000 });
          }
          await expect(next).toBeVisible({ timeout: 2_000 });
        }).toPass({ timeout: UI_READY });
      };
      const configuration = page.getByRole('textbox', { name: /^Experiment name/ })
        .or(page.getByTestId('experiment-head-compact'));
      const data = page.getByRole('checkbox', { name: 'Run a random sample of the rows' });
      await continueFrom(configuration, data);
      await continueFrom(data, page.getByText(seed.evalName, { exact: true }).first());
      // Run only from the Review step, which names how errored tries score (as
      // experiment-scope.spec.ts does): press Continue again if the Evaluations
      // step was still settling when it was first pressed.
      const review = page.getByRole('combobox', { name: 'Errored tries in scores' });
      await expect(async () => {
        if (!(await review.isVisible())) {
          await page.getByRole('button', { name: /^Continue/ }).click({ timeout: 2_000 });
        }
        await expect(review).toBeVisible({ timeout: 2_000 });
      }).toPass({ timeout: UI_READY });
      const posted = page.waitForResponse(response => new URL(response.url()).pathname === CREATE
        && response.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: /^Run experiment/ }).click();
      const response = await posted;
      expect(response.status(), await response.text()).toBe(200);
      id = /experiment_id=([0-9a-f-]{36})/.exec((await response.json()).result)?.[1] ?? '';
      expect(id).toBeTruthy();
    }, { timeout: UI_READY * 3 });

    await expect.poll(async () => (await resultCells(probe, id)).map(cell => ({
      source: cell.source, status: cell.status, value: cell.value,
    })), POLL.ASYNC_JOB).toEqual([
      ...Array.from({ length: ROWS }, () => ({ source: 'experiment', status: 'pass',
        value: `echo: [{"type":"text","text":"SCOPE:${suffix}"}]` })),
      ...Array.from({ length: ROWS }, () => ({ source: 'experiment_evaluation', status: 'pass', value: 'Passed' })),
    ]);

    await test.step('create two tags in the Edit dialog and save them', async () => {
      await page.goto(`/dashboard/develop/${seed.datasetId}?tab=experiments`, { waitUntil: 'domcontentloaded' });
      const dialog = await openEdit();
      await dialog.getByRole('button', { name: 'Add tags' }).click();
      const picker = page.getByTestId('tag-picker');
      for (const tag of [first, second]) {
        await picker.getByRole('textbox', { name: 'Search tags' }).fill(tag);
        const created = page.waitForResponse(response => new URL(response.url()).pathname === TAGS
          && response.request().method() === 'POST', { timeout: UI_READY });
        await picker.getByText(`Create tag “${tag}”`, { exact: true }).click();
        const response = await created;
        expect(response.status(), await response.text()).toBe(200);
        expect(response.request().postDataJSON()).toEqual({ name: tag });
        tagIds[tag] = (await response.json()).result.id;
        await expect(picker.getByRole('checkbox', { name: tag })).toBeChecked();
      }
      await closePicker(picker);
      await expect(dialog.getByTestId('tag-chip').filter({ hasText: first })).toBeVisible({ timeout: UI_READY });
      const put = page.waitForResponse(response => new URL(response.url()).pathname === `${CREATE}${id}/tags/`
        && response.request().method() === 'PUT', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save', exact: true }).click();
      const saved = await put;
      expect(saved.status(), await saved.text()).toBe(200);
      const sent = saved.request().postDataJSON();
      expect([...sent.tag_ids].sort()).toEqual([tagIds[first], tagIds[second]].sort());
      await testInfo.attach('tags-wire', { contentType: 'application/json', body: JSON.stringify(sent) });
      await expect(dialog).toBeHidden({ timeout: UI_READY });
    }, { timeout: UI_READY * 3 });

    await test.step('reload: the row shows both tags, and the server keeps them', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      const row = page.getByRole('row').filter({ hasText: name }).first();
      await expect(row.getByTestId('tag-chip').filter({ hasText: first })).toBeVisible({ timeout: UI_READY });
      await expect(row.getByTestId('tag-chip').filter({ hasText: second })).toBeVisible();
      const registry = (await actor.api.get<Registry>(`${TAGS}?include=experiments`)).result;
      expect(registry.results.map(tag => tag.name)).toEqual(expect.arrayContaining([first, second]));
      expect([...registry.experiments[id]].sort()).toEqual([tagIds[first], tagIds[second]].sort());
      expect(await links()).toEqual([first, second].sort());
    }, { timeout: UI_READY * 2 });

    await test.step('take one tag off and save', async () => {
      const dialog = await openEdit();
      await dialog.getByRole('button', { name: 'Edit tags' }).click();
      const picker = page.getByTestId('tag-picker');
      await picker.getByRole('checkbox', { name: second }).click();
      await expect(picker.getByRole('checkbox', { name: second })).not.toBeChecked();
      await closePicker(picker);
      const put = page.waitForResponse(response => new URL(response.url()).pathname === `${CREATE}${id}/tags/`
        && response.request().method() === 'PUT', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save', exact: true }).click();
      const saved = await put;
      expect(saved.status(), await saved.text()).toBe(200);
      expect(saved.request().postDataJSON()).toEqual({ tag_ids: [tagIds[first]] });
      await page.reload({ waitUntil: 'domcontentloaded' });
      const row = page.getByRole('row').filter({ hasText: name }).first();
      await expect(row.getByTestId('tag-chip').filter({ hasText: first })).toBeVisible({ timeout: UI_READY });
      await expect(row.getByTestId('tag-chip').filter({ hasText: second })).toHaveCount(0);
      expect(await links()).toEqual([first]);
    }, { timeout: UI_READY * 3 });

    await test.step("the other organization can't read, edit, delete or change them", async () => {
      const other = scopeActors.ownerB;
      const listed = await scopeActors.send<Registry>(other, 'GET', `${TAGS}?include=experiments`);
      expect(listed.status).toBe(200);
      expect(JSON.stringify(listed.body ?? {})).not.toContain(tagIds[first]);
      expect(JSON.stringify(listed.body ?? {})).not.toContain(id);
      expect((await scopeActors.send(other, 'PATCH', `${TAGS}${tagIds[first]}/`, { name: `e2e-foreign-${suffix}` })).status).toBe(404);
      expect((await scopeActors.send(other, 'DELETE', `${TAGS}${tagIds[first]}/`)).status).toBe(404);
      expect((await scopeActors.send(other, 'PUT', `${CREATE}${id}/tags/`, { tag_ids: [] })).status).toBe(404);
      const stored = await probe.pg<{ name: string }>(
        'SELECT name FROM model_hub_workspace_tag WHERE id=$1', [tagIds[first]]);
      expect(stored).toEqual([{ name: first }]);
      expect(await links()).toEqual([first]);
    });
  } catch (error) {
    await testInfo.attach('tags-page', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    await testInfo.attach('tags-page-text', { contentType: 'text/plain', body: await page.locator('body').innerText() });
    throw error;
  } finally {
    await context.close();
  }
});

/**
 * Fill the New experiment wizard's name, and its description when one is given.
 *
 * The wizard shows its head in one of three ways (ConfigureStepExperimentCreation.jsx):
 * the picker-first layout (the name and a Description button), the full layout (the
 * name and the "Description (optional)" field), or, while a run editor is open, one
 * folded line with an Edit button (compactHead). Which one shows changes as the
 * prompt picked in the URL loads as the wizard's run, so a click or a fill can land
 * just as its target goes away. This unfolds a folded head, opens a description
 * behind its button, fills, and retries the whole step until both fields hold the
 * values.
 */
async function fillExperimentHead(
  page: Page,
  { name, description }: { name: string; description?: string },
  timeout = UI_READY,
): Promise<void> {
  const folded = page.getByTestId('experiment-head-compact');
  const unfold = page.getByTestId('experiment-head-edit');
  const nameBox = page.getByRole('textbox', { name: /^Experiment name/ });
  const describeField = page.getByRole('textbox', { name: /^Description \(optional\)/ });
  const describeButton = page.getByRole('button', { name: 'Description', exact: true });
  const quick = { timeout: 2_000 };
  await expect(async () => {
    if (await folded.isVisible()) await unfold.click(quick);
    await nameBox.fill(name, quick);
    if (description !== undefined) {
      if (!(await describeField.isVisible()) && (await describeButton.isVisible())) {
        await describeButton.click(quick);
      }
      await describeField.fill(description, quick);
    }
    await expect(nameBox).toHaveValue(name, quick);
    if (description !== undefined) await expect(describeField).toHaveValue(description, quick);
  }).toPass({ timeout });
}
