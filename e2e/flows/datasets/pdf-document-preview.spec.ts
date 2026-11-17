import { createHash } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import { uploadPdfFixture } from '../../lib/pdf-fixture';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// develop_dataset_contracts.py and frontend/src/utils/axios.js.
const CREATE_DATASET = '/model-hub/develops/create-dataset-manually/';
// futureagi/model_hub/views/experiments.py:4582-4602 creates the experiment and its source snapshot.
const CREATE_EXPERIMENT = '/model-hub/experiments/v2/';
const CREATE_PROMPT = '/model-hub/prompt-templates/create-draft/';
const CREATE_EVALUATION = '/model-hub/eval-templates/create-v2/';
const UI_READY = 60_000;
const digest = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
// Requests a PDF.js worker or its CDN would make; the native document preview needs none.
const PDF_RUNTIME = /pdf\.worker|pdfjs-dist|unpkg\.com/;

for (const [entry, id] of [['dataset', 'DATA-E2E-003'], ['experiment', 'DATA-E2E-004']] as const) {
  test(`${id}: ${entry} PDFs render without a runtime CDN worker`, {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id, area: 'datasets',
      userGoal: `Open a stored PDF from a ${entry} cell in the document preview without a CDN dependency,`
        + ' an unreadable one included',
      steps: ['create a document dataset and upload a synthetic PDF through the product input',
        `open the ${entry} cell's document preview from its named document control with external runtime requests denied`,
        'close the preview and reopen it from the same control',
        'upload an unreadable PDF and open it in the same preview'],
      backendChecks: ['the stored dataset and document cell belong to the actor scope',
        "the storage URL and the preview's document address return exactly the uploaded PDF bytes",
        ...(entry === 'experiment'
          ? ['the experiment snapshot belongs to the actor scope and copies the document column with its stored URL']
          : [])],
      browserChecks: ['the preview embeds the stored document URL as a native object named by the document',
        'the object declares the PDF type only when the document reference supplies it',
        'opening the preview requests no PDF worker or CDN script'],
    }),
  }, async ({ page, context, actor, probe }, testInfo) => {
    // 2 * (ASYNC_JOB 60s + 14 UI_READY 60s + 8 expects 10s) + 120s setup headroom.
    test.setTimeout(2_080_000);
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
        // frontend/src/sections/common/DevelopCellRenderer/CellRenderers/FileCellRenderer.jsx:398-409 renders a clickable label.
        await cell.getByText('Upload file', { exact: true }).click();
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
          // futureagi/model_hub/services/dataset_snapshot.py:102-104 requires a non-empty source.
          const sourceRows = await probe.pg<{ id: string }>(
            'SELECT id FROM model_hub_row WHERE dataset_id = $1 AND NOT deleted', [datasetId]);
          expect(sourceRows).toHaveLength(1);
          // futureagi/model_hub/serializers/experiments.py:1331-1343 requires a metric and a prompt.
          const template = await actor.api.post<{ result: { id: string } }>(CREATE_EVALUATION, {
            name: `${name}-evaluation`, eval_type: 'code', code_language: 'python', output_type: 'pass_fail',
            code: 'def evaluate(output, **kwargs):\n    return bool(output)\n', pass_threshold: 0.5,
            check_internet: false, error_localizer_enabled: false,
          });
          const prompt = await actor.api.post<{ result: { id: string } }>(CREATE_PROMPT, {
            name: `${name}-prompt`, is_draft: false, variable_names: {}, prompt_config: [{
              messages: [{ role: 'user', content: [{ type: 'text', text: `${name} input` }] }],
              configuration: { model: 'gpt-4o-mini', model_detail: { value: 'gpt-4o-mini' }, temperature: 0, max_tokens: 16 },
            }],
          });
          const versions = await probe.pg<{ id: string }>(
            `SELECT v.id FROM model_hub_promptversion v JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
             WHERE p.id=$1 AND p.organization_id=$2 AND v.template_version='v1' AND NOT v.deleted`,
            [prompt.result.id, actor.organizationId]);
          expect(versions).toHaveLength(1);
          await actor.api.post(CREATE_EXPERIMENT, {
            name, dataset_id: datasetId, experiment_type: 'llm',
            prompt_config: [{ name: `${name}-run`, prompt_id: prompt.result.id, prompt_version: versions[0].id,
              model: 'gpt-4o-mini', model_params: { temperature: 0, max_tokens: 16 } }],
            user_eval_metrics: [{ template_id: template.result.id, name: `${name}-metric`,
              config: { mapping: { output: columnId } } }],
          });
          const experiments = await probe.pg<{ id: string; snapshot_dataset_id: string }>(
            'SELECT id, snapshot_dataset_id FROM model_hub_experimentstable WHERE dataset_id = $1 AND name = $2 AND NOT deleted',
            [datasetId, name]);
          expect(experiments).toHaveLength(1);
          const snapshotId = experiments[0].snapshot_dataset_id;
          expect(snapshotId).toBeTruthy();
          const snapshotScope = await probe.pg<{ organization_id: string; workspace_id: string }>(
            'SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [snapshotId]);
          expect(snapshotScope).toEqual([{ organization_id: actor.organizationId, workspace_id: actor.workspaceId }]);
          // futureagi/model_hub/services/dataset_snapshot.py:142-150,205-208 copies the column with new ids and retains its PDF.
          const documents = await probe.pg<{ column_id: string; value: string; value_infos: string | { document_url?: string } }>(
            `SELECT c.column_id, c.value, c.value_infos FROM model_hub_cell c
             JOIN model_hub_column col ON col.id=c.column_id
             WHERE c.dataset_id=$1 AND col.source_id=$2 AND NOT c.deleted AND NOT col.deleted`,
            [snapshotId, columnId]);
          expect(documents).toHaveLength(1);
          expect(documents[0].column_id).not.toBe(columnId);
          expect(documents[0].value).toBe(storedUrl);
          const documentInfo = typeof documents[0].value_infos === 'string'
            ? JSON.parse(documents[0].value_infos) : documents[0].value_infos;
          expect(documentInfo.document_url).toBe(storedUrl);
          path = `/dashboard/develop/experiment/${experiments[0].id}/data`;
          await testInfo.attach(`experiment-${name}`, { body: JSON.stringify(experiments), contentType: 'application/json' });
          await testInfo.attach(`experiment-source-${name}`, { body: JSON.stringify({ datasetId, columnId,
            sourceRows, snapshotId, documents, promptId: prompt.result.id, versionId: versions[0].id,
            templateId: template.result.id }), contentType: 'application/json' });
        }
        await page.goto(path, { waitUntil: 'domcontentloaded' });
        // frontend/src/sections/common/Media/mediaRef.js:88-132 names the document by the stored URL's last
        // segment and gives it the PDF type only by a .pdf extension (else application/octet-stream).
        const documentName = decodeURIComponent(new URL(storedUrl).pathname.split('/').pop()!);
        const pdfType = /\.pdf$/i.test(documentName) ? 'application/pdf' : null;
        // The dataset cell (CustomCellRender.jsx media cells) and the experiment Input cell (ExampleCells.jsx,
        // typed by its dataset column) both render DocChip.jsx, a button named Open <document name>.
        const previewButton = page.getByRole('button', { name: `Open ${documentName}`, exact: true });
        await expect(previewButton).toHaveCount(1, { timeout: UI_READY });
        // frontend/src/sections/common/Media/MediaLightbox.jsx:94-108,166-183 is the dialog with Open in new tab and Close.
        const preview = page.getByRole('dialog').filter({
          has: page.getByRole('link', { name: 'Open in new tab', exact: true }),
        });
        // MediaLightbox.jsx:212-229 embeds the document as a native object.
        const embedded = preview.locator('object');
        const expectEmbedded = async () => {
          await expect(preview.getByRole('link', { name: 'Open in new tab', exact: true })).toHaveAttribute('href', storedUrl);
          await expect(embedded).toHaveCount(1);
          await expect(embedded).toHaveAttribute('data', storedUrl);
          await expect(embedded).toHaveAttribute('aria-label', documentName);
          expect(await embedded.getAttribute('type')).toBe(pdfType);
        };
        await expect(preview).toHaveCount(0);
        await previewButton.click();
        await expect(preview).toBeVisible({ timeout: UI_READY });
        await expectEmbedded();
        // The preview's own document address serves exactly the uploaded bytes.
        const shown = await context.request.get((await embedded.getAttribute('data'))!);
        expect(shown.status()).toBe(200);
        expect(digest(await shown.body())).toBe(digest(bytes));
        await preview.getByRole('button', { name: 'Close', exact: true }).click();
        await expect(preview).toHaveCount(0, { timeout: UI_READY });
        await previewButton.click();
        await expect(preview).toBeVisible({ timeout: UI_READY });
        await expectEmbedded();
        expect(blocked.filter(url => PDF_RUNTIME.test(url))).toEqual([]);
        expect(dialogs).toEqual([]);
        expect(new URL(page.url()).pathname).toBe(path);
        await testInfo.attach(`preview-${name}`, { body: await page.screenshot(), contentType: 'image/png' });
        await preview.getByRole('button', { name: 'Close', exact: true }).click();
        await expect(preview).toHaveCount(0, { timeout: UI_READY });
      }
      expect(errors.filter(error => /useLocation|Router|staticContext/.test(error))).toEqual([]);
    } finally {
      await testInfo.attach('runtime-network-and-errors', {
        body: JSON.stringify({ blocked, workers, errors, dialogs, warnings }), contentType: 'application/json',
      });
    }
  });
}
