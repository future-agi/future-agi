import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { provisionActor } from '../../lib/provisioning';
import { request as pwRequest } from '@playwright/test';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';

// annotation-queues.js and AnnotationQueueSerializer / AddItemsSerializer.
const QUEUES = '/model-hub/annotation-queues/';
// ManualDatasetCreateRequestSerializer and DatasetAddRowsRequestSerializer.
const CREATE_DATASET = '/model-hub/develops/create-dataset-manually/';
// QueueAnalyticsTab and queue-detail-view.jsx use these accessible controls.
const UI_READY = 60_000;
type ExportRow = { item_id: string; source_id: string; source: { fields: Record<string, string> };
  annotations: { label_id: string; value: number }[] };
type Item = { id: string; dataset_row_id: string };

test('ANNOT-E2E-006: large queues download all annotations and source content', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ANNOT-E2E-006', area: 'annotations',
    userGoal: 'Download every annotation and its source content from a queue larger than 1000 items',
    steps: ['seed a 1001-item queue and a one-item queue through public APIs',
      'download CSV and JSON from the large queue Analytics tab',
      'download JSON from the queue header Export menu',
      'download CSV and JSON from the small queue without a background job',
      'open the finished private export link as anonymous and another organization'],
    backendChecks: ['large exports return 202 and their scoped jobs complete with all 1001 items',
      'a different browser context cannot fetch either the download endpoint or its private storage object',
      'downloaded CSV and JSON contain the exact seeded item identities, source values and annotation',
      'small queue exports return 200 inline and contain only the selected item'],
  }),
}, async ({ page, browser, actor, probe }, testInfo) => {
  // 120s seeding + 5 UI_READY downloads + 3 ASYNC_JOB waits = 600s.
  test.setTimeout(600_000);
  page.setDefaultTimeout(UI_READY);
  const prefix = `e2e-annot6-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const datasetId = (await actor.api.post<{ result: { dataset_id: string } }>(CREATE_DATASET, {
    dataset_name: prefix, number_of_rows: 1, number_of_columns: 1,
  })).result.dataset_id;
  const rows = Array.from({ length: 1001 }, (_, i) => ({ id: randomUUID(), value: `${prefix}-row-${i}` }));
  // AddDataRowsView honors explicit ids and appends rows in request order.
  for (let start = 0; start < rows.length; start += 200) {
    await actor.api.post(`/model-hub/develops/${datasetId}/add_rows/`, {
      rows: rows.slice(start, start + 200).map(r => ({ id: r.id,
        cells: [{ column_name: 'Column 1', value: r.value }] })),
    });
  }
  // AnnotationsLabels serializer: star settings and typed score value.
  const labelId = (await actor.api.post<{ result: { id: string } }>('/model-hub/annotations-labels/', {
    name: `${prefix}-rating`, type: 'star', settings: { no_of_stars: 5 },
  })).result.id;
  // ModelViewSet.create returns AnnotationQueueSerializer directly (no envelope).
  const large = (await actor.api.post<{ id: string }>(QUEUES, {
    name: `${prefix}-large`, label_ids: [labelId],
  })).id;
  const small = (await actor.api.post<{ id: string }>(QUEUES, {
    name: `${prefix}-small`, label_ids: [labelId],
  })).id;
  for (let start = 0; start < rows.length; start += 200) {
    await actor.api.post(`${QUEUES}${large}/items/add-items/`, {
      items: rows.slice(start, start + 200).map(r => ({ source_type: 'dataset_row', source_id: r.id })),
    });
  }
  await actor.api.post(`${QUEUES}${small}/items/add-items/`, {
    items: [{ source_type: 'dataset_row', source_id: rows[0].id }],
  });
  const readItems = (queue: string) => probe.pg<Item>(
    'SELECT id, dataset_row_id FROM model_hub_queueitem WHERE queue_id = $1 AND NOT deleted ORDER BY id::text', [queue]);
  const largeItems = await readItems(large), smallItems = await readItems(small);
  expect(largeItems.map(i => i.dataset_row_id).sort()).toEqual(rows.map(r => r.id).sort());
  expect(smallItems.map(i => i.dataset_row_id)).toEqual([rows[0].id]);
  const annotated = largeItems.find(i => i.dataset_row_id === rows[0].id)!;
  // CreateScoreSerializer explicit queue_item_id avoids default-queue fallback.
  await actor.api.post('/model-hub/scores/', { source_type: 'dataset_row', source_id: rows[0].id,
    label_id: labelId, queue_item_id: annotated.id, value: { rating: 4 } });
  await testInfo.attach('seeded-export-identities', { contentType: 'application/json',
    body: JSON.stringify({ datasetId, large, small, rows, largeItems, smallItems, labelId,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  const valueByRow = new Map<string, string>(rows.map(r => [r.id, r.value]));

  for (const scenario of [
    { queue: large, format: 'csv', header: false, items: largeItems },
    { queue: large, format: 'json', header: false, items: largeItems },
    { queue: large, format: 'json', header: true, items: largeItems },
    { queue: small, format: 'csv', header: false, items: smallItems },
    { queue: small, format: 'json', header: false, items: smallItems },
  ]) {
    await test.step(`${scenario.queue === large ? 'large' : 'small'} ${scenario.format} ${scenario.header ? 'header' : 'analytics'}`, async () => {
      await page.goto(`/dashboard/annotations/queues/${scenario.queue}`);
      // Wait for membership hydration before choosing an index-based tab; the
      // manager-only Settings tab is inserted when queue detail resolves.
      await expect(page.getByRole('tab', { name: 'Settings', exact: true })).toBeVisible({ timeout: UI_READY });
      if (!scenario.header) {
        await page.getByRole('tab', { name: 'Analytics', exact: true }).click();
        await expect(page.getByRole('tab', { name: 'Analytics', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
      }
      const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === `${QUEUES}${scenario.queue}/export/`, { timeout: UI_READY });
      const downloadPromise = page.waitForEvent('download', { timeout: UI_READY + POLL.ASYNC_JOB.timeout });
      if (scenario.header) {
        await page.getByRole('button', { name: 'Export', exact: true }).click();
        await page.getByRole('menuitem', { name: 'Download', exact: true }).click();
      } else {
        await page.getByRole('button', { name: `Export ${scenario.format.toUpperCase()}`, exact: true }).click();
      }
      const response = await responsePromise;
      expect(response.status()).toBe(scenario.queue === large ? 202 : 200);
      if (scenario.queue === large) {
        const scheduled = await response.json();
        const jobPath = `${QUEUES}${large}/export-jobs/${scheduled.job_id}/`;
        await expect.poll(async () => (await actor.api.get<{ result: { status: string } }>(jobPath)).result.status,
          POLL.ASYNC_JOB).toBe('succeeded');
        const jobs = await probe.pg<{ queue_id: string; organization_id: string; item_count: number; status: string }>(
          'SELECT queue_id, organization_id, item_count, status FROM model_hub_annotationexportjob WHERE id = $1', [scheduled.job_id]);
        expect(jobs).toEqual([{ queue_id: large, organization_id: actor.organizationId, item_count: 1001, status: 'succeeded' }]);
        if (scenario.format === 'json' && !scenario.header) {
          const status = (await actor.api.get<{ result: { file_url: string } }>(jobPath)).result;
          expect(status.file_url).toContain('/download/?token=');
          const stored = await probe.pg<{ storage_key: string }>(
            'SELECT storage_key FROM model_hub_annotationexportjob WHERE id = $1', [scheduled.job_id]);
          expect(stored[0].storage_key).toBeTruthy();
          const anonymous = await browser.newContext();
          const secondRequest = await pwRequest.newContext({ baseURL: E2E.apiUrl });
          try {
            const anonymousPage = await anonymous.newPage();
            const denied = await anonymousPage.goto(`${E2E.apiUrl}${status.file_url}`);
            expect([401, 403]).toContain(denied?.status());
            const storageDenied = await anonymousPage.goto(
              `http://localhost:29005/fi-content-dev-annotation-exports/${stored[0].storage_key}`);
            expect(storageDenied?.status()).toBe(403);
            const second = await provisionActor(secondRequest, 'private-export-other-org');
            expect(second.organizationId).not.toBe(actor.organizationId);
            const secondContext = await browser.newContext({ extraHTTPHeaders: {
              Authorization: `Bearer ${second.tokens.access}`,
              'X-Organization-Id': second.organizationId,
              'X-Workspace-Id': second.workspaceId,
            } });
            try {
              const secondPage = await secondContext.newPage();
              const forbidden = await secondPage.goto(`${E2E.apiUrl}${status.file_url}`);
              expect(forbidden?.status()).toBe(404);
            } finally {
              await secondContext.close();
            }
          } finally {
            await anonymous.close();
            await secondRequest.dispose();
          }
        }
      }
      const download = await downloadPromise;
      expect(await download.failure()).toBeNull();
      const bytes = await readFile((await download.path())!);
      const text = bytes.toString('utf8');
      await testInfo.attach(`${scenario.queue}-${scenario.format}-${scenario.header ? 'header' : 'analytics'}`, {
        body: bytes, contentType: scenario.format === 'json' ? 'application/json' : 'text/csv',
      });
      if (scenario.format === 'json') {
        // Analytics saves the synchronous API envelope; the header hook unwraps it.
        const document = JSON.parse(text);
        const exported: ExportRow[] = scenario.queue === small ? document.result : document;
        expect(exported.map(r => r.item_id).sort()).toEqual(scenario.items.map(i => i.id).sort());
        for (const row of exported) expect(row.source.fields['Column 1']).toBe(valueByRow.get(row.source_id));
        if (scenario.queue === large) {
          expect(exported.find(r => r.item_id === annotated.id)!.annotations).toEqual([
            expect.objectContaining({ label_id: labelId, value: 4 }),
          ]);
        }
      } else {
        // Seeded fields contain no newlines. Split only commas outside quoted
        // cells, then decode CSV's doubled quotes (including the source JSON).
        const [header, ...lines] = text.trimEnd().split(/\r?\n/);
        const columns = header.split(',');
        expect(columns.slice(-2)).toEqual(['source_id', 'source']);
        const exported = lines.map(line => {
          const cells = line.split(/,(?=(?:(?:[^"]*"){2})*[^"]*$)/).map(cell =>
            cell.startsWith('"') ? cell.slice(1, -1).replace(/""/g, '"') : cell);
          expect(cells).toHaveLength(columns.length);
          return Object.fromEntries(columns.map((column, index) => [column, cells[index]]));
        });
        expect(exported.map(row => row.item_id).sort()).toEqual(scenario.items.map(item => item.id).sort());
        for (const item of scenario.items) {
          const row = exported.find(value => value.item_id === item.id)!;
          expect(row.source_id).toBe(item.dataset_row_id);
          expect(JSON.parse(row.source).fields['Column 1']).toBe(valueByRow.get(item.dataset_row_id));
          expect({ label_id: row.label_id, value: row.value }).toEqual(
            scenario.queue === large && item.id === annotated.id
              ? { label_id: labelId, value: '4' }
              : { label_id: '', value: '' });
        }
      }
    });
  }
});
