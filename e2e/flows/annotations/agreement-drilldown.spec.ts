import { test, expect as baseExpect, type ScopeActor } from '../../lib/scope-actors';
import type { Locator, Page } from '@playwright/test';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/serializers/annotation_queues.py and develop_annotations.py;
// routes are also consumed by frontend/src/api/annotation-queues/annotation-queues.js.
const QUEUES = '/model-hub/annotation-queues/';
const LABELS = '/model-hub/annotations-labels/';
// Dataset seed wire bodies captured in the isolated #924 acceptance run, 3 Nov 2026.
const DATASETS = '/model-hub/develops/';
// frontend/src/routes/paths.js: annotations.queueDetail / annotate.
const QUEUE_PAGE = '/dashboard/annotations/queues/';
const UI_READY = 60_000;
const expect = baseExpect.configure({ timeout: UI_READY });
// useQueueAgreement: staleTime is two minutes. Wait real time, not a mocked clock.
const AGREEMENT_STALE = 125_000;
const roleSets = {
  manager: ['manager'], reviewer: ['reviewer'], annotator: ['annotator'],
  dual: ['reviewer', 'annotator'],
};
type Role = keyof typeof roleSets;
type Envelope<T> = { result: T };
type Label = { id: string; name: string };
type Item = { id: string; rowId: string; marker: string };
type Agreement = { labels: Record<string, {
  label_name: string; disagreement_count: number; disagreement_items: string[];
}> };
// QueueAnnotateDetailResultSerializer + ScoreSerializer: annotator, not annotator_id.
type Detail = { item: { id: string; source_content: { row_id: string } };
  annotations: { annotator: string; label_id: string; value: { value: number } }[] };

function labelRow(page: Page, label: Label) {
  return page.getByRole('row', { includeHidden: true }).filter({
    has: page.getByRole('cell', { name: label.name, exact: true, includeHidden: true }),
  });
}
function countCell(page: Page, label: Label) {
  return labelRow(page, label).getByRole('cell', { includeHidden: true }).last();
}
function trigger(page: Page, label: Label) {
  return countCell(page, label).getByRole('button', { includeHidden: true });
}
function popup(page: Page, label: Label) {
  return page.getByRole('dialog', { name: `Disagreed Items: ${label.name}`, exact: true });
}
async function noHorizontalOverflow(locator: Locator) {
  const sizes = await locator.evaluate(el => ({ client: el.clientWidth, scroll: el.scrollWidth }));
  expect(sizes.scroll).toBeLessThanOrEqual(sizes.client + 2);
}

// Annotation metadata is deliberately limited to the checks below; the separate
// history endpoint's assignment policy is not qualified by this frontend flow.
test('ANNOT-E2E-005: queue members drill into disagreements and recover from unavailable items', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ANNOT-E2E-005', area: 'annotations',
    userGoal: 'A queue member opens the exact disagreed item in the appropriate workspace and can recover when that item is unavailable',
    steps: [
      'seed synthetic dataset rows, numeric labels and four non-admin queue roles through public APIs',
      'read zero, three and twenty-five disagreements; open the exact capped list with five more',
      'use keyboard open, traversal, Escape and Enter to inspect the exact item in each role',
      'read a long label in light and dark without horizontal overflow',
      'open an unassigned and a deleted item, read a persistent explanation and return to the queue',
      'resolve and remove an open label, reconnect after the real stale interval and see the popup close',
    ],
    backendChecks: [
      'four non-admin viewer_roles and real agreement responses match the seeded 0/3/25 conflicts and capped item sets',
      'each role opens the exact source row and permitted score authors in review or annotate mode',
      'unassigned and deleted item navigation returns real 403 and 404 while the UI offers persistent queue recovery',
      'public score corrections and label removal change agreement results and close the already-open popup on refetch',
    ],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  // 300s seed + 4*4*UI_READY + 4*UI_READY errors + 2*(125s+25s) refetch.
  test.setTimeout(1_800_000);
  const prefix = `e2e-annot5-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const owner = scopes.ownerA;
  const roles = Object.keys(roleSets) as Role[];
  const actors = {} as Record<Role, ScopeActor>;
  for (const role of roles) {
    actors[role] = await scopes.inviteMember(`${prefix}-${role}`);
    const info = await actors[role].api.get<{ org_level: number; ws_level: number }>('/accounts/user-info/');
    expect(info).toMatchObject({ org_level: 3, ws_level: 3 });
  }
  const labels: Label[] = [];
  for (const name of [`${prefix}-zero`, `${prefix}-few`, `${prefix}-${'LongUnbrokenDisagreementLabel'.repeat(6)}`]) {
    const created = await owner.api.post<Envelope<Label>>(LABELS, { name, type: 'numeric', allow_notes: false,
      settings: { min: 0, max: 100, step_size: 1, display_type: 'slider' } });
    labels.push(created.result);
  }
  const dataset = await owner.api.post<Envelope<{ dataset_id: string }>>(`${DATASETS}create-dataset-manually/`, {
    dataset_name: `${prefix}-dataset`, number_of_rows: 28, number_of_columns: 1,
  });
  const datasetId = dataset.result.dataset_id;
  const table = (await owner.api.get<Envelope<{ table: { row_id: string; order: number }[];
    column_config: { id: string }[] }>>(`${DATASETS}${datasetId}/get-dataset-table/`, { page_size: 100 })).result;
  expect(table.table).toHaveLength(28);
  const rows = [...table.table].sort((a, b) => Number(a.order) - Number(b.order));
  const markers = rows.map((_, i) => `${prefix}-row-${i}`);
  for (let i = 0; i < rows.length; i++) await owner.api.post(`${DATASETS}${datasetId}/update_cell_value/`, {
    row_id: rows[i].row_id, column_id: table.column_config[0].id, new_value: markers[i],
  });
  const queue = await owner.api.post<{ id: string; name: string }>(QUEUES, {
    name: `${prefix}-queue`, auto_assign: false, assignment_strategy: 'manual', annotations_required: 2,
    requires_review: false, label_ids: labels.map(l => l.id), annotator_ids: roles.map(r => actors[r].userId),
    annotator_roles: Object.fromEntries(roles.map(r => [actors[r].userId, roleSets[r]])),
  });
  await owner.api.post(`${QUEUES}${queue.id}/update-status/`, { status: 'active' });
  const added = await owner.api.post<Envelope<{ added: number }>>(`${QUEUES}${queue.id}/items/add-items/`, {
    items: rows.map(row => ({ source_type: 'dataset_row', source_id: row.row_id })),
  });
  expect(added.result.added).toBe(28);
  // QueueItemSerializer.source_id is write-only. Public source_preview.row_order
  // joins the exact dataset row; list order and random UUID order are irrelevant.
  const listed = await owner.api.get<{ results: { id: string; source_preview: { row_order: number } }[] }>(
    `${QUEUES}${queue.id}/items/`, { limit: 100 });
  expect(listed.results).toHaveLength(28);
  const items: Item[] = rows.map((row, i) => {
    const item = listed.results.find(it => Number(it.source_preview.row_order) === Number(row.order));
    expect(item, `queue item for seeded row ${i}`).toBeDefined();
    return { id: item!.id, rowId: row.row_id, marker: markers[i] };
  });
  await testInfo.attach('seeded-agreement-identities', { contentType: 'application/json', body: JSON.stringify({
    queue, labels, datasetId, items, organizationId: owner.organizationId, workspaceId: owner.workspaceId,
    actors: roles.map(role => ({ role, userId: actors[role].userId, viewer_roles: roleSets[role] })),
  }) });
  await owner.api.post(`${QUEUES}${queue.id}/items/assign/`, {
    item_ids: items.slice(1).map(i => i.id), user_ids: [actors.annotator.userId, actors.dual.userId], action: 'set',
  });
  // Never score item 0 as annotator: old scores can independently grant access.
  await owner.api.post(`${QUEUES}${queue.id}/items/assign/`, {
    item_ids: [items[0].id], user_ids: [actors.manager.userId, actors.dual.userId], action: 'set',
  });
  const submit = async (actor: ScopeActor, item: Item, values: number[]) => {
    const response = await actor.api.post<Envelope<{ submitted: number }>>(
      `${QUEUES}${queue.id}/items/${item.id}/annotations/submit/`, {
        annotations: labels.map((label, i) => ({ label_id: label.id, value: { value: values[i] } })),
      });
    expect(response.result.submitted).toBe(3);
  };
  for (let i = 0; i < items.length; i++) {
    await submit(i === 0 ? actors.manager : actors.annotator, items[i], [10, 10, 10]);
    await submit(actors.dual, items[i], [10, i < 3 ? 90 : 10, i < 25 ? 90 : 10]);
  }
  const agreementPath = `${QUEUES}${queue.id}/agreement/`;
  const agreement = async (actor: ScopeActor) => (await actor.api.get<Envelope<Agreement>>(agreementPath)).result;
  const detailPath = (item: Item) => `${QUEUES}${queue.id}/items/${item.id}/annotate-detail/`;
  const openAgreement = async (page: Page, role: Role) => {
    const loaded = page.waitForResponse(r => new URL(r.url()).pathname === `${QUEUES}${queue.id}/` && r.status() === 200,
      { timeout: UI_READY });
    await page.goto(`${QUEUE_PAGE}${queue.id}`);
    await loaded;
    // Existing manager tab-index race is excluded: wait for the role-dependent
    // Settings tab before choosing Agreement; do not describe this as a race fix.
    if (role === 'manager') await expect(page.getByRole('tab', { name: 'Settings', exact: true })).toBeVisible({ timeout: UI_READY });
    const fetched = page.waitForResponse(r => new URL(r.url()).pathname === agreementPath && r.status() === 200,
      { timeout: UI_READY });
    await page.getByRole('tab', { name: 'Agreement', exact: true }).click();
    const displayed = (await (await fetched).json() as Envelope<Agreement>).result;
    await expect(countCell(page, labels[0])).toHaveText('0', { timeout: UI_READY });
    // The backend does not order its capped sample. Compare against the response
    // this browser actually received, not a separate request's equally valid 20.
    return displayed;
  };
  const assertList = async (page: Page, label: Label, truth: Agreement) => {
    const dialog = popup(page, label);
    await expect(dialog).toBeVisible({ timeout: UI_READY });
    const expected = truth.labels[label.id];
    const buttons = dialog.getByRole('button', { name: /^Item #/ });
    await expect(buttons).toHaveCount(expected.disagreement_items.length, { timeout: UI_READY });
    expect((await buttons.allTextContents()).map(s => s.trim()).sort())
      .toEqual(expected.disagreement_items.map(id => `Item #${id.slice(0, 8)}`).sort());
    return dialog;
  };
  const screenshot = async (page: Page, name: string) => testInfo.attach(name, {
    contentType: 'image/png', body: await page.screenshot({ fullPage: true }),
  });

  for (const role of roles) {
    const actor = actors[role];
    const truth = await agreement(actor);
    const actualRoles = await actor.api.get<{ viewer_roles: string[] }>(`${QUEUES}${queue.id}/`);
    expect([...actualRoles.viewer_roles].sort()).toEqual([...roleSets[role]].sort());
    expect(labels.map(l => truth.labels[l.id].disagreement_count)).toEqual([0, 3, 25]);
    expect([...truth.labels[labels[1].id].disagreement_items].sort()).toEqual(items.slice(0, 3).map(i => i.id).sort());
    const capped = truth.labels[labels[2].id].disagreement_items;
    expect(capped).toHaveLength(20);
    expect(new Set(capped).size).toBe(20);
    expect(capped.every(id => items.slice(0, 25).some(item => item.id === id))).toBe(true);
    const context = await scopes.openContext(browser, actor);
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.setViewportSize({ width: 1440, height: 1100 });
    await test.step(`${role}: truthful counts and capped list`, async () => {
      const displayed = await openAgreement(page, role);
      expect(displayed.labels[labels[2].id].disagreement_count).toBe(25);
      expect(displayed.labels[labels[2].id].disagreement_items).toHaveLength(20);
      expect(displayed.labels[labels[2].id].disagreement_items.every(id => items.slice(0, 25).some(item => item.id === id))).toBe(true);
      for (let i = 0; i < labels.length; i++) {
        await expect(countCell(page, labels[i])).toHaveText(String([0, 3, 25][i]), { timeout: UI_READY });
      }
      await expect(countCell(page, labels[0]).getByRole('button')).toHaveCount(0);
      await trigger(page, labels[2]).click();
      const dialog = await assertList(page, labels[2], displayed);
      await expect(dialog.getByText('+ 5 more disagreements', { exact: true })).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(trigger(page, labels[2])).toBeFocused();
    }, { timeout: 2 * UI_READY });
    await test.step(`${role}: keyboard navigation to the exact permitted item`, async () => {
      const button = trigger(page, labels[1]);
      await button.focus();
      await page.keyboard.press('Enter');
      const dialog = await assertList(page, labels[1], truth);
      await expect(dialog.getByText(/more disagreements/)).toHaveCount(0);
      await page.keyboard.press('Tab');
      const links = dialog.getByRole('button', { name: /^Item #/ });
      await expect(links.nth(0)).toBeFocused();
      await page.keyboard.press('Tab');
      await expect(links.nth(1)).toBeFocused();
      await page.keyboard.press('Shift+Tab');
      await expect(links.nth(0)).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(button).toBeFocused();
      await page.keyboard.press('Space');
      await expect(dialog).toBeVisible();
      const item = items[1];
      await dialog.getByRole('button', { name: `Item #${item.id.slice(0, 8)}`, exact: true }).focus();
      const mode = role === 'annotator' ? 'annotate' : 'review';
      const detailResponse = page.waitForResponse(r => new URL(r.url()).pathname === detailPath(item) &&
        (mode === 'annotate' || new URL(r.url()).searchParams.get('view_mode') === 'review'), { timeout: UI_READY });
      await page.keyboard.press('Enter');
      const response = await detailResponse;
      expect(response.status()).toBe(200);
      const detail = (await response.json() as Envelope<Detail>).result;
      expect(detail.item.id).toBe(item.id);
      expect(detail.item.source_content.row_id).toBe(item.rowId);
      const authors = [...new Set(detail.annotations.map(a => a.annotator))].sort();
      expect(authors).toEqual((mode === 'review' ? [actors.annotator.userId, actors.dual.userId] : [actor.userId]).sort());
      await expect(page).toHaveURL(url => url.pathname === `${QUEUE_PAGE}${queue.id}/annotate` &&
        url.searchParams.get('itemId') === item.id && url.searchParams.get('mode') === mode, { timeout: UI_READY });
      await expect(page.getByTestId('annotation-workspace-root')).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText(item.marker, { exact: false }).first()).toBeVisible({ timeout: UI_READY });
      if (mode === 'review') await expect(page.locator('[data-review-label-id]').first()).toBeVisible();
      else {
        await expect(page.locator('[data-review-score-key]')).toHaveCount(0);
        await expect(page.getByRole('slider').first()).toBeVisible();
      }
      await screenshot(page, `${role}-exact-item`);
    }, { timeout: 2 * UI_READY });
    await context.close();
  }

  const backgrounds: string[] = [];
  for (const theme of ['light', 'dark']) {
    const themeContext = await scopes.openContext(browser, actors.annotator);
    await themeContext.addInitScript(mode => localStorage.setItem('settings', JSON.stringify({
      themeMode: mode, themeDirection: 'ltr', themeContrast: 'default', themeLayout: 'vertical', themeStretch: false,
    })), theme);
    const page = await themeContext.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.setViewportSize({ width: 1440, height: 1100 });
    await test.step(`long label in ${theme}`, async () => {
      const displayed = await openAgreement(page, 'annotator');
      backgrounds.push(await page.evaluate(() => getComputedStyle(document.body).backgroundColor));
      await noHorizontalOverflow(labelRow(page, labels[2]).locator('xpath=..'));
      await expect(page.getByRole('cell', { name: labels[2].name, exact: true })).toHaveAttribute('title', labels[2].name);
      await trigger(page, labels[2]).click();
      const dialog = await assertList(page, labels[2], displayed);
      await noHorizontalOverflow(dialog);
      await expect(dialog.getByText(`Disagreed Items: ${labels[2].name}`, { exact: true })).toHaveAttribute('title', labels[2].name);
      await expect(dialog.getByRole('button', { name: /^Item #/ }).first()).toBeVisible();
      await screenshot(page, `long-label-${theme}`);
      await page.keyboard.press('Escape');
    }, { timeout: UI_READY });
    await themeContext.close();
  }
  expect(backgrounds[1]).not.toBe(backgrounds[0]);
  const context = await scopes.openContext(browser, actors.annotator);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  await page.setViewportSize({ width: 1440, height: 1100 });
  const recover = async (status: 403 | 404, item: Item) => {
    const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === detailPath(item), { timeout: UI_READY });
    await popup(page, labels[1]).getByRole('button', { name: `Item #${item.id.slice(0, 8)}`, exact: true }).click();
    expect((await responsePromise).status()).toBe(status);
    const heading = status === 403 ? 'Item access denied' : 'Item not found';
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(page.getByTestId('annotation-workspace-root')).toHaveCount(0);
    await expect(page.getByText('Something went wrong', { exact: true })).toHaveCount(0);
    // Assert the persistent page, not a notistack toast that disappears in 5s.
    await expect(page.getByRole('alert').filter({ hasText: status === 403 ? 'assigned to another annotator' : 'deleted or is no longer available' })).toBeVisible();
    await screenshot(page, `item-${status}-recovery`);
    await page.getByRole('button', { name: 'Back to Queue', exact: true }).click();
    await expect(page).toHaveURL(url => url.pathname === `${QUEUE_PAGE}${queue.id}`, { timeout: UI_READY });
    await expect(page.getByRole('tab', { name: 'Agreement', exact: true })).toBeVisible({ timeout: UI_READY });
  };
  await test.step('real assignment denial and recovery', async () => {
    await openAgreement(page, 'annotator');
    await trigger(page, labels[1]).click();
    await recover(403, items[0]);
  }, { timeout: 2 * UI_READY });
  await test.step('real deletion after the list opens and recovery', async () => {
    await openAgreement(page, 'annotator');
    await trigger(page, labels[1]).click();
    await owner.api.delete(`${QUEUES}${queue.id}/items/${items[2].id}/`);
    await recover(404, items[2]);
  }, { timeout: 2 * UI_READY });

  // Keep the popup mounted across the backend mutation. A reload would hide the
  // disappearing-anchor defect even without the product fix.
  const reconnect = async () => {
    const staleAt = Date.now() + AGREEMENT_STALE;
    await expect.poll(() => Date.now() >= staleAt, { timeout: AGREEMENT_STALE + 5_000, intervals: [1_000] }).toBe(true);
    await context.setOffline(true);
    await expect.poll(() => page.evaluate(() => navigator.onLine)).toBe(false);
    const refetched = page.waitForResponse(r => new URL(r.url()).pathname === agreementPath && r.status() === 200,
      { timeout: UI_READY });
    await context.setOffline(false);
    await refetched;
  };
  await test.step('resolve all disagreements while the popup remains open', async () => {
    await openAgreement(page, 'annotator');
    await trigger(page, labels[1]).click();
    for (const item of items.slice(0, 2)) await submit(actors.dual, item, [10, 10, 90]);
    const updated = await agreement(actors.annotator);
    expect(updated.labels[labels[1].id].disagreement_count).toBe(0);
    expect(updated.labels[labels[1].id].disagreement_items).toEqual([]);
    await reconnect();
    await expect(page.getByRole('dialog')).toHaveCount(0, { timeout: UI_READY });
    await expect(countCell(page, labels[1])).toHaveText('0');
    await expect(page.getByRole('region', { name: 'Agreement results' })).toBeFocused();
    await screenshot(page, 'resolved-popup-closed');
  }, { timeout: AGREEMENT_STALE + 2 * UI_READY });
  await test.step('remove the selected label while the popup remains open', async () => {
    await trigger(page, labels[2]).click();
    // calculate_agreement groups live Scores, not queue-label bindings. Removing
    // the binding alone retains historical agreement. Delete only this synthetic
    // label's scores via the public owner-authorized endpoint to remove its result.
    const scoreIds: string[] = [];
    for (const item of items.filter(it => it.id !== items[2].id)) {
      const scores = await owner.api.get<Envelope<{ id: string; label_id: string }[]>>(
        `${QUEUES}${queue.id}/items/${item.id}/annotations/`);
      scoreIds.push(...scores.result.filter(score => score.label_id === labels[2].id).map(score => score.id));
    }
    expect(scoreIds).toHaveLength(54);
    await owner.api.post(`${QUEUES}${queue.id}/remove-label/`, { label_id: labels[2].id });
    // model_hub/views/scores.py:destroy permits the creating annotator or org owner.
    for (const id of scoreIds) await owner.api.delete(`/model-hub/scores/${id}/`);
    expect((await agreement(actors.annotator)).labels[labels[2].id]).toBeUndefined();
    await reconnect();
    await expect(page.getByRole('dialog')).toHaveCount(0, { timeout: UI_READY });
    await expect(labelRow(page, labels[2])).toHaveCount(0);
    await expect(page.getByRole('region', { name: 'Agreement results' })).toBeFocused();
    await screenshot(page, 'removed-label-popup-closed');
  }, { timeout: AGREEMENT_STALE + 2 * UI_READY });
  await context.close();
});
