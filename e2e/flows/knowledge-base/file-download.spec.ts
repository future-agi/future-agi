import fs from 'node:fs/promises';
import { test, expect } from '../../lib/scope-actors';
import { knowledgeHeaders, uploadKnowledgeDocument } from '../../lib/kb-upload';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';

const UI_READY = 60_000;
// ExistingKnowledgeBaseView.post; the file table's data source uses this endpoint.
const FILE_LIST = '/model-hub/knowledge-base/files/';

test('KB-E2E-001: a workspace reader downloads an owned knowledge-base file', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'KB-E2E-001', area: 'knowledge-base',
    userGoal: 'A workspace reader downloads an uploaded file with its original filename and exact bytes, while other scopes cannot download it',
    steps: ['upload a synthetic text document', 'open its knowledge-base file table',
      'click Download on the file row', 'save the browser download',
      'check foreign-organization, sibling-workspace and anonymous refusals'],
    backendChecks: ['the file list API returns the uploaded file identity and name',
      'authorized download returns exactly the uploaded bytes as an attachment with no storage URL',
      'foreign-org, sibling-workspace and anonymous requests return refusal without file bytes'],
  }),
}, async ({ scopeActors, browser, request }, testInfo) => {
  test.setTimeout(300_000);
  const actor = scopeActors.ownerA;
  const name = `e2e-kb1-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const contents = `Knowledge-base download fixture ${name}\n`;
  const seeded = await uploadKnowledgeDocument(request, actor, name, contents);
  await testInfo.attach('seeded-kb', { contentType: 'application/json', body: JSON.stringify(seeded) });
  const path = `/model-hub/kb/${seeded.kbId}/download-file/`;
  const params = { file_id: seeded.fileId };
  const rows = await actor.api.post<{ result: { table_data: { id: string; name: string }[] } }>(
    FILE_LIST, { kb_id: seeded.kbId, page_number: 0, page_size: 25 });
  expect(rows.result.table_data.map(({ id, name }) => ({ id, name })))
    .toEqual([{ id: seeded.fileId, name: seeded.filename }]);

  // The public upload returns before its storage thread completes.
  await expect.poll(async () => {
    const response = await request.get(`${E2E.apiUrl}${path}`, { headers: knowledgeHeaders(actor), params });
    if (response.status() !== 200) return `status ${response.status()}`;
    expect(response.headers()['content-disposition']).toBe(`attachment; filename="${seeded.filename}"`);
    expect(response.headers()['cache-control']).toBe('private, no-store');
    expect(response.headers()['content-type']).toBe('text/plain');
    expect(response.headers()['x-content-type-options']).toBe('nosniff');
    expect(response.headers()['location']).toBeUndefined();
    return response.text();
  }, POLL.ASYNC_JOB).toBe(contents);

  const context = await scopeActors.openContext(browser, actor);
  const blocked: string[] = [];
  const inventory: { method: string; host: string; blocked: boolean }[] = [];
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname;
    const external = !['localhost', '127.0.0.1'].includes(host);
    inventory.push({ method: route.request().method(), host, blocked: external });
    if (external) {
      blocked.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  try {
    // KnowledgeBaseData.handleRowClick pins this file-table route.
    await page.goto(`/dashboard/knowledge/${seeded.kbId}`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(seeded.filename, { exact: true })).toBeVisible({ timeout: UI_READY });
    const downloaded = page.waitForEvent('download', { timeout: UI_READY });
    await page.getByRole('button', { name: `Download ${seeded.filename}`, exact: true }).click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe(seeded.filename);
    const destination = testInfo.outputPath('downloaded.txt');
    await download.saveAs(destination);
    expect(await fs.readFile(destination, 'utf8')).toBe(contents);
    await testInfo.attach('downloaded-file', { path: destination, contentType: 'text/plain' });
    await testInfo.attach('file-table-after-download', {
      contentType: 'image/png', body: await page.screenshot({ fullPage: true }),
    });

    for (const outsider of [scopeActors.ownerB, scopeActors.withWorkspace(actor, scopeActors.emptyWorkspace.id)]) {
      const response = await request.get(`${E2E.apiUrl}${path}`, { headers: knowledgeHeaders(outsider), params });
      expect(response.status()).toBe(403);
      expect(await response.text()).not.toContain(contents);
      expect(response.headers()['location']).toBeUndefined();
    }
    const anonymous = await request.get(`${E2E.apiUrl}${path}`, { params });
    expect(anonymous.status()).toBe(401);
    expect(await anonymous.text()).not.toContain(contents);
  } finally {
    await testInfo.attach('blocked-external-urls', { contentType: 'application/json', body: JSON.stringify(blocked) });
    await testInfo.attach('browser-network-inventory', { contentType: 'application/json', body: JSON.stringify(inventory) });
    await context.close();
  }
});
