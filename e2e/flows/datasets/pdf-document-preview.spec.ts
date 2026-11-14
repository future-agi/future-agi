import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { test, expect } from '../../lib/fixtures';
import type { Locator } from '@playwright/test';
import { JPX_SWATCH_RGB, PDF_EXTERNAL_LINK, PDF_LINK_IDS, pdfNotes, uploadPdfFixture } from '../../lib/pdf-fixture';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// develop_dataset_contracts.py and frontend/src/utils/axios.js.
const CREATE_DATASET = '/model-hub/develops/create-dataset-manually/';
// ExperimentsTableSerializer: empty prompt_config snapshots existing data without provider calls.
const CREATE_EXPERIMENT = '/model-hub/experiments/';
// FileCellRenderer.jsx uses the existing .icon-actions button for both entry points.
const PREVIEW_BUTTON = '.icon-actions button';
const UI_READY = 60_000;
const digest = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
// PdfDocumentPreview.jsx scrolls the box directly around react-pdf's Document.
const pdfLink = (preview: Locator, id: string) => preview.locator(`.annotationLayer [data-annotation-id="${id}"] a`);
const scrollTop = (preview: Locator) => preview.locator('.react-pdf__Document')
  .evaluate(element => (element.parentElement as HTMLElement).scrollTop);
const setScrollTop = (preview: Locator, top: number) => preview.locator('.react-pdf__Document')
  .evaluate((element, value) => { (element.parentElement as HTMLElement).scrollTop = value; }, top);
// Pixels from the top of the visible preview to the top of a text-layer line.
const belowViewTop = (preview: Locator, text: string) => preview.locator('.textLayer')
  .getByText(text, { exact: true }).evaluate(element => {
    const box = element.closest('.react-pdf__Document')!.parentElement!;
    return { offset: element.getBoundingClientRect().top - box.getBoundingClientRect().top, view: box.clientHeight };
  });

for (const [entry, id] of [['dataset', 'DATA-E2E-003'], ['experiment', 'DATA-E2E-004']] as const) {
  test(`${id}: ${entry} PDFs render without a runtime CDN worker`, {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id, area: 'datasets',
      userGoal: `Read a stored PDF from a ${entry} cell without a CDN dependency and recognize an unreadable document`,
      steps: ['create a document dataset and upload a synthetic PDF through the product input',
        `open the ${entry} document preview with external runtime requests denied`,
        'read both pages and the JPEG 2000 image, navigate back, follow the in-document link to page 2,'
          + ' follow section links to a position on the same page and on page 2,'
          + ' close and reopen, then download the PDF',
        'upload an unreadable PDF and verify the error state with no rendered page'],
      backendChecks: ['the stored dataset and document cell belong to the actor scope',
        'the storage URL and browser download return exactly the uploaded PDF bytes',
        'the document cell opens the correct PDF through a same-origin worker without a CDN request',
        'the JPEG 2000 image is decoded with same-origin decoder files and painted on the page',
        'the in-document link moves the preview to its page and the external link opens in a new tab',
        'a section link scrolls its destination position to the top of the preview, on its own page or another'],
    }),
  }, async ({ page, context, actor, probe }, testInfo) => {
    // 2 * (ASYNC_JOB 60s + 15 UI_READY 60s) + 120s setup headroom.
    test.setTimeout(2_040_000);
    page.setDefaultTimeout(UI_READY);
    const blocked: string[] = [];
    const workers: string[] = [];
    const errors: string[] = [];
    const dialogs: string[] = [];
    const warnings: string[] = [];
    page.on('worker', worker => workers.push(worker.url()));
    page.on('console', message => { if (['warning', 'error'].includes(message.type())) warnings.push(message.text()); });
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.dismiss(); });
    // Real application and storage responses are never fulfilled or replaced.
    // Unrelated optional network dependencies are recorded separately from PDF workers.
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) {
        blocked.push(url.href);
        return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    try {
      for (const unreadable of [false, true]) {
        const name = `e2e-pdf-${entry}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
        const created = await actor.api.post<{ result: { dataset_id: string } }>(CREATE_DATASET,
          { dataset_name: name, number_of_rows: 1, number_of_columns: 1 });
        const datasetId = created.result.dataset_id;
        const columns = await probe.pg<{ id: string }>(
          'SELECT id FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted', [datasetId]);
        expect(columns).toHaveLength(1);
        const columnId = columns[0].id;
        await actor.api.put(`/model-hub/develops/${datasetId}/update_column_type/${columnId}/`,
          { new_column_type: 'document', preview: false, force_update: true });
        await testInfo.attach(`seed-${name}`, { body: JSON.stringify({ name, datasetId, columnId,
          organizationId: actor.organizationId, workspaceId: actor.workspaceId, unreadable }),
        contentType: 'application/json' });
        const scope = await probe.pg<{ name: string; organization_id: string; workspace_id: string }>(
          'SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [datasetId]);
        expect(scope).toEqual([{ name, organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);

        // dashboard.jsx dataset route; FileCellRenderer / ReplaceMediaDialog upload UI.
        await page.goto(`/dashboard/develop/${datasetId}`, { waitUntil: 'domcontentloaded' });
        // Exclude the experiment grid's pinned Average footer; this is the seeded data row.
        const cell = page.locator(`.ag-center-cols-container .ag-row [col-id="${columnId}"]`);
        await expect(cell).toHaveCount(1, { timeout: UI_READY });
        await cell.getByText('Click here', { exact: true }).click();
        const upload = page.getByRole('dialog').filter({ has: page.getByText('Upload File', { exact: true }) });
        const bytes = await uploadPdfFixture(upload.locator('input[type="file"]'), name, unreadable);
        await upload.getByRole('button', { name: 'Upload', exact: true }).click();
        let storedUrl = '';
        await expect.poll(async () => {
          const rows = await probe.pg<{ value_infos: string | { document_url?: string } }>(
            'SELECT value_infos FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted',
            [datasetId, columnId]);
          expect(rows).toHaveLength(1);
          const value = typeof rows[0].value_infos === 'string' ? JSON.parse(rows[0].value_infos) : rows[0].value_infos;
          storedUrl = value?.document_url ?? '';
          return Boolean(storedUrl);
        }, POLL.ASYNC_JOB).toBe(true);
        expect(['localhost', '127.0.0.1', '[::1]']).toContain(new URL(storedUrl).hostname);
        const stored = await context.request.get(storedUrl);
        expect(stored.status()).toBe(200);
        expect(digest(await stored.body())).toBe(digest(bytes));
        await testInfo.attach(`document-${name}`, { body: JSON.stringify({ datasetId, columnId,
          storedUrl, sha256: digest(bytes) }), contentType: 'application/json' });

        let path = `/dashboard/develop/${datasetId}`;
        if (entry === 'experiment') {
          await actor.api.post(CREATE_EXPERIMENT, { name, dataset_id: datasetId,
            column_id: columnId, prompt_config: [], user_eval_template_ids: [] });
          const experiments = await probe.pg<{ id: string }>(
            'SELECT id FROM model_hub_experimentstable WHERE dataset_id = $1 AND name = $2 AND NOT deleted',
            [datasetId, name]);
          expect(experiments).toHaveLength(1);
          path = `/dashboard/develop/experiment/${experiments[0].id}/data`;
          await testInfo.attach(`experiment-${name}`, { body: JSON.stringify(experiments), contentType: 'application/json' });
        }
        await page.goto(path, { waitUntil: 'domcontentloaded' });
        await expect(cell).toHaveCount(1, { timeout: UI_READY });
        await cell.hover();
        await cell.locator(PREVIEW_BUTTON).first().click();
        // PdfPreviewDrawer.jsx exposes Download file and Close in both renderer versions.
        const preview = page.locator('.MuiDrawer-paper').filter({
          has: page.getByRole('button', { name: 'Download file', exact: true }),
        });
        await expect(preview).toBeVisible({ timeout: UI_READY });
        if (unreadable) {
          await expect(preview.getByText('Unable to preview this document', { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(preview.locator('canvas')).toHaveCount(0);
        } else {
          // Renderer-neutral regression anchor: public dev must fail here for its missing CDN worker.
          await expect(preview.getByText(`PDF 1 ${name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          const canvas = preview.locator('canvas');
          await expect(canvas).toBeVisible({ timeout: UI_READY });
          expect(await canvas.evaluate((element: HTMLCanvasElement) => {
            const pixels = element.getContext('2d')!.getImageData(0, 0, element.width, element.height).data;
            let ink = 0;
            for (let i = 0; i < pixels.length; i += 4) {
              if (pixels[i + 3] > 0 && pixels[i] + pixels[i + 1] + pixels[i + 2] < 600) ink++;
            }
            return ink;
          })).toBeGreaterThan(20);
          // Page 1 paints a 200x100 pt JPEG 2000 swatch; without a working decoder that area stays blank.
          await expect.poll(() => canvas.evaluate((element: HTMLCanvasElement, [r, g, b]) => {
            const pixels = element.getContext('2d')!.getImageData(0, 0, element.width, element.height).data;
            let swatch = 0;
            for (let i = 0; i < pixels.length; i += 4) {
              if (Math.abs(pixels[i] - r) <= 8 && Math.abs(pixels[i + 1] - g) <= 8 && Math.abs(pixels[i + 2] - b) <= 8) swatch++;
            }
            return swatch;
          }, JPX_SWATCH_RGB), { timeout: UI_READY }).toBeGreaterThan(5_000);
          expect(workers.length).toBeGreaterThan(0);
          for (const worker of workers) expect(new URL(worker).origin).toBe(new URL(page.url()).origin);
          expect(blocked.filter(url => /pdf\.worker|pdfjs-dist|unpkg\.com/.test(url))).toEqual([]);
          await preview.getByRole('button', { name: 'Next page', exact: true }).click();
          await expect(preview.getByText(`PDF 2 ${name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          await preview.getByRole('button', { name: 'Previous page', exact: true }).click();
          await expect(preview.getByText(`PDF 1 ${name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          // Page 1's link annotations: the external one opens a new tab and is not followed here.
          const external = preview.locator(`.annotationLayer a[href="${PDF_EXTERNAL_LINK}"]`);
          await expect(external).toHaveAttribute('target', '_blank', { timeout: UI_READY });
          expect((await external.getAttribute('rel'))?.split(' ')).toEqual(expect.arrayContaining(['noopener', 'noreferrer']));
          // The /Fit link opens page 2 from the top, with the page buttons in view.
          await setScrollTop(preview, 100);
          await pdfLink(preview, PDF_LINK_IDS.internal).click();
          await expect(preview.getByText(`PDF 2 ${name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(preview.getByText('Page 2 of 2', { exact: true })).toBeVisible();
          await expect.poll(() => scrollTop(preview), { timeout: UI_READY }).toBe(0);
          await preview.getByRole('button', { name: 'Previous page', exact: true }).click();
          await expect(preview.getByText(`PDF 1 ${name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          // /XYZ links: each note starts below the visible preview and its link brings it to the top.
          const notes = pdfNotes(1, name);
          await expect(preview.locator('.textLayer').getByText(notes, { exact: true })).toBeAttached({ timeout: UI_READY });
          const start = await belowViewTop(preview, notes);
          expect(start.offset).toBeGreaterThan(start.view);
          await pdfLink(preview, PDF_LINK_IDS.notes).click();
          await expect.poll(async () => (await belowViewTop(preview, notes)).offset, { timeout: UI_READY })
            .toBeLessThan(24);
          expect((await belowViewTop(preview, notes)).offset).toBeGreaterThanOrEqual(0);
          await expect(preview.getByText('Page 1 of 2', { exact: true })).toBeAttached();
          await pdfLink(preview, PDF_LINK_IDS.otherNotes).click();
          await expect(preview.getByText('Page 2 of 2', { exact: true })).toBeAttached({ timeout: UI_READY });
          const otherNotes = pdfNotes(2, name);
          await expect(preview.locator('.textLayer').getByText(otherNotes, { exact: true })).toBeAttached({ timeout: UI_READY });
          await expect.poll(async () => (await belowViewTop(preview, otherNotes)).offset, { timeout: UI_READY })
            .toBeLessThan(24);
          expect((await belowViewTop(preview, otherNotes)).offset).toBeGreaterThanOrEqual(0);
          await setScrollTop(preview, 0);
          await preview.getByRole('button', { name: 'Previous page', exact: true }).click();
          await expect(preview.getByText(`PDF 1 ${name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          await preview.getByRole('button', { name: 'Close', exact: true }).click();
          await cell.hover();
          await cell.locator(PREVIEW_BUTTON).first().click();
          await expect(preview.getByText(`PDF 1 ${name}`, { exact: true })).toBeVisible({ timeout: UI_READY });
          const [download] = await Promise.all([page.waitForEvent('download'),
            preview.getByRole('button', { name: 'Download file', exact: true }).click()]);
          expect(digest(await readFile((await download.path())!))).toBe(digest(bytes));
        }
        expect(dialogs).toEqual([]);
        expect(new URL(page.url()).pathname).toBe(path);
        await testInfo.attach(`preview-${name}`, { body: await page.screenshot(), contentType: 'image/png' });
        await preview.getByRole('button', { name: 'Close', exact: true }).click();
      }
      expect(errors.filter(error => /useLocation|Router|staticContext/.test(error))).toEqual([]);
    } finally {
      await testInfo.attach('runtime-network-and-errors', {
        body: JSON.stringify({ blocked, workers, errors, dialogs, warnings }), contentType: 'application/json',
      });
    }
  });
}
