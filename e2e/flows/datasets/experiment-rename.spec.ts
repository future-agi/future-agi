import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';
import { CREATE, ROWS, resultCells, seedScope } from './experiment-scope-seed';

const UI_READY = 60_000;
const LIST = '/model-hub/experiments/v2/list/';
type Detail = { result: { id: string; name: string; description: string } };

test('EXP-E2E-009: name and describe an experiment, then edit both', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'EXP-E2E-009', area: 'experiments',
    userGoal: 'Give an experiment a description when it is created, then rename it and change the description, and trust both are kept and private to the organization.',
    steps: ['seed two synthetic organizations and a prompt backed by the managed mock-llm echo server',
      'create an experiment in the wizard with a typed description',
      'open Edit… on the Experiments tab row and change the name and the description',
      'reload and read both back on the page and from the server',
      'refuse the other organization a read, an edit and a list row of the experiment'],
    backendChecks: ['the create body carries the typed description and the stored experiment keeps it',
      'the edit is a PUT of only the name and description, and the stored experiment keeps both',
      "the other organization's GET and PUT by id answer 404 and change nothing, and its list has no row of the experiment"],
    browserChecks: ['the wizard offers Description and sends what was typed',
      'the Edit experiment dialog saves the name and description and the row shows the new name after a reload'],
  }),
}, async ({ browser, scopeActors, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopeActors.ownerA;
  const seed = await seedScope(actor, probe, suffix, scopeActors);
  const name = `e2e-rename-${suffix}`;
  const description = `Does citing the policy stop invented dates? ${suffix}`;
  const renamed = `e2e-renamed-${suffix}`;
  const redescribed = `Second question for ${suffix}`;
  const context = await scopeActors.openContext(browser, actor);
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  let id = '';
  try {
    await test.step('create with a typed description', async () => {
      const query = new URLSearchParams({ tab: 'experiments', new_experiment: '1',
        source: 'prompt', prompt_id: seed.promptId, prompt_version: seed.promptVersion });
      await page.goto(`/dashboard/develop/${seed.datasetId}?${query}`, { waitUntil: 'domcontentloaded' });
      const wizard = page.getByRole('dialog', { name: 'New experiment', exact: true });
      // The linked card and saved version settle the Configuration layout.
      await expect(wizard.getByText(seed.promptName, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(wizard.getByRole('button', { name: 'Version', exact: true })).toHaveText('v1', { timeout: UI_READY });
      const next = wizard.getByRole('button', { name: /^Continue/ });
      await expect(next).toBeEnabled({ timeout: UI_READY });
      const nameBox = wizard.getByRole('textbox', { name: /^Experiment name/ });
      const descriptionBox = wizard.getByRole('textbox', { name: 'Description (optional)', exact: true });
      await nameBox.fill(name);
      await descriptionBox.fill(description);
      await expect(nameBox).toHaveValue(name);
      await expect(descriptionBox).toHaveValue(description);
      await next.click();
      await expect(wizard.getByText('None of these runs use {{variables}} — nothing to map.', { exact: true }))
        .toBeVisible({ timeout: UI_READY });
      await next.click();
      await expect(page.getByText(seed.evalName, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: /^Continue/ }).click();
      const posted = page.waitForResponse(response => new URL(response.url()).pathname === CREATE
        && response.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: /^Run experiment/ }).click();
      const response = await posted;
      expect(response.status(), await response.text()).toBe(200);
      const body = response.request().postDataJSON();
      expect(body.name).toBe(name);
      expect(body.description).toBe(description);
      id = /experiment_id=([0-9a-f-]{36})/.exec((await response.json()).result)?.[1] ?? '';
      expect(id).toBeTruthy();
      const detail = (await actor.api.get<Detail>(`${CREATE}${id}/`)).result;
      expect(detail.name).toBe(name);
      expect(detail.description).toBe(description);
    }, { timeout: UI_READY * 3 });

    await expect.poll(async () => (await resultCells(probe, id)).map(cell => ({
      source: cell.source, status: cell.status, value: cell.value,
    })), POLL.ASYNC_JOB).toEqual([
      ...Array.from({ length: ROWS }, () => ({ source: 'experiment', status: 'pass',
        value: `echo: [{"type":"text","text":"SCOPE:${suffix}"}]` })),
      ...Array.from({ length: ROWS }, () => ({ source: 'experiment_evaluation', status: 'pass', value: 'Passed' })),
    ]);

    await test.step('rename and describe again from the Experiments tab row', async () => {
      await page.goto(`/dashboard/develop/${seed.datasetId}?tab=experiments`, { waitUntil: 'domcontentloaded' });
      const row = page.getByRole('row')
        .filter({ has: page.getByText(name, { exact: true }) }).first();
      await expect(row).toBeVisible({ timeout: UI_READY });
      await row.hover();
      await row.getByRole('button', { name: /actions$/ }).first().click();
      await page.getByRole('menuitem', { name: /^Edit/ }).click();
      const dialog = page.getByRole('dialog', { name: 'Edit experiment' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('textbox', { name: 'Name' }).fill(renamed);
      await dialog.getByRole('textbox', { name: 'Description' }).fill(redescribed);
      const put = page.waitForResponse(response => new URL(response.url()).pathname === `${CREATE}${id}/`
        && response.request().method() === 'PUT', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Save', exact: true }).click();
      const saved = await put;
      expect(saved.status(), await saved.text()).toBe(200);
      const sent = saved.request().postDataJSON();
      expect(sent).toMatchObject({ name: renamed, description: redescribed });
      await testInfo.attach('edit-wire', { contentType: 'application/json', body: JSON.stringify(sent) });
    }, { timeout: UI_READY * 2 });

    await test.step('reload: both are kept', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('row').filter({ hasText: renamed }).first()).toBeVisible({ timeout: UI_READY });
      await expect(page.getByRole('row').filter({ hasText: name })).toHaveCount(0);
      const detail = (await actor.api.get<Detail>(`${CREATE}${id}/`)).result;
      expect(detail.name).toBe(renamed);
      expect(detail.description).toBe(redescribed);
      const stored = await probe.pg<{ name: string; description: string }>(
        'SELECT name, description FROM model_hub_experimentstable WHERE id=$1', [id]);
      expect(stored).toEqual([{ name: renamed, description: redescribed }]);
    }, { timeout: UI_READY * 2 });

    await test.step("the other organization can't read, edit or list it", async () => {
      const other = scopeActors.ownerB;
      expect((await scopeActors.send(other, 'GET', `${CREATE}${id}/`)).status).toBe(404);
      expect((await scopeActors.send(other, 'PUT', `${CREATE}${id}/`, { name: `e2e-foreign-${suffix}` })).status).toBe(404);
      const listed = await scopeActors.send(other, 'GET', `${LIST}?dataset_id=${seed.datasetId}`);
      if (listed.status === 200) expect(JSON.stringify(listed.body ?? {})).not.toContain(id);
      else expect([403, 404]).toContain(listed.status);
      const stored = await probe.pg<{ name: string; description: string }>(
        'SELECT name, description FROM model_hub_experimentstable WHERE id=$1', [id]);
      expect(stored).toEqual([{ name: renamed, description: redescribed }]);
    });
  } catch (error) {
    await testInfo.attach('rename-page', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    await testInfo.attach('rename-page-text', { contentType: 'text/plain', body: await page.locator('body').innerText() });
    throw error;
  } finally {
    await context.close();
  }
});
