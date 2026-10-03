import { test, expect } from '../../lib/fixtures';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// create-label-drawer.jsx onSubmit and annotation-labels.js use these public
// routes; the UI intentionally sends no project or score when creating a label.
const LABELS = '/model-hub/annotations-labels/';
// WidgetEditorView uses these authenticated, current-definition read endpoints.
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
// Browser first-paint budget, per e2e/README.md "Writing a flow".
const UI_READY = 60_000;

interface Label {
  id: string; name: string; type: string;
  settings: { options: { label: string }[] };
}
interface LabelRow extends Label {
  organization_id: string; workspace_id: string; project_id: string | null;
}
interface Metric { property_id: string; name: string; display_name: string }
interface MetricsPage { result: { metrics: Metric[] } }
interface ValuePage { result: { values: { value: string; label: string }[] } }

test('ANNOT-E2E-001: workspace annotation choices follow UI edits without scores', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ANNOT-E2E-001', area: 'annotations',
    userGoal: 'Create a projectless annotation label and discover its current choices in a workspace filter',
    steps: ['create a categorical label through the label drawer',
      'open a workspace dashboard widget filter and inspect its choices',
      'rename one option through the label drawer',
      'refresh the widget editor and inspect the replacement choices'],
    backendChecks: [
      'label retains exact options and actor organization/workspace, with no project or scores',
      'workspace catalog and widget filter expose the exact label and never-used choices',
      'UI edit preserves label identity and replaces the old option in PG, values API and refreshed picker',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(420_000); // ASYNC_JOB 60 + five UI_READY 300 + 60 headroom.
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const name = `e2e-annot1-${suffix}`;
  const alpha = `Alpha-${suffix}`;
  const beta = `Beta-${suffix}`;
  const gamma = `Gamma-${suffix}`;
  let labelId: string;

  await test.step('UI: create the categorical label', async () => {
    await page.goto('/dashboard/annotations/labels', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Create Label', exact: true }).first().click({ timeout: UI_READY });
    await page.getByPlaceholder('e.g. Relevance, Tone, Accuracy').fill(name);
    await page.getByPlaceholder('Option 1', { exact: true }).fill(alpha);
    await page.getByPlaceholder('Option 2', { exact: true }).fill(beta);
    const saved = page.waitForResponse(r => new URL(r.url()).pathname === LABELS &&
      r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    const body = await response.json() as { result: Label };
    labelId = body.result.id;
    await testInfo.attach('annotation-ids', { contentType: 'application/json',
      body: JSON.stringify({ labelId, name, alpha, beta, gamma,
        organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  });

  await test.step('backend check 1: exact scoped definition, no manufactured score', async () => {
    const rows = await probe.pg<LabelRow>(
      `SELECT id, name, type, settings, organization_id, workspace_id, project_id
       FROM model_hub_annotationslabels WHERE id = $1 AND deleted = false`, [labelId]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ id: labelId, name, type: 'categorical',
      organization_id: actor.organizationId, workspace_id: actor.workspaceId, project_id: null });
    expect(rows[0].settings.options).toEqual([{ label: alpha }, { label: beta }]);
    const scores = await probe.pg<{ id: string }>('SELECT id FROM model_hub_score WHERE label_id = $1', [labelId]);
    expect(scores).toEqual([]);
    // ViewSet retrieve uses DRF's direct serializer response, unlike create.
    const detail = await actor.api.get<Label>(`${LABELS}${labelId}/`);
    expect(detail.settings.options).toEqual([{ label: alpha }, { label: beta }]);
  });

  // DashboardCreateUpdateSerializer: a synthetic workspace dashboard is only a
  // container for the real widget filter. No widget, preview result or score is seeded.
  const dashboard = await actor.api.post<{ result: { id: string } }>('/tracer/dashboard/', {
    name: `${name}-dashboard`, description: 'Synthetic annotation catalog qualification',
  });
  const dashboardId = dashboard.result.id;
  await testInfo.attach('dashboard-id', { contentType: 'application/json', body: JSON.stringify({ dashboardId }) });
  // DashboardDetailView's Add Widget navigation; "new" is recognized by WidgetEditorView.
  const editorUrl = `/dashboard/dashboards/${dashboardId}/widget/new`;

  for (const phase of ['initial', 'edited'] as const) {
    if (phase === 'edited') {
      await test.step('UI: edit Beta to Gamma on the same label', async () => {
        await page.goto('/dashboard/annotations/labels', { waitUntil: 'domcontentloaded' });
        await page.getByPlaceholder('Search labels...').fill(name);
        // annotation-label-table.jsx onCellClicked opens the edit drawer.
        await page.locator('.ag-row [col-id="name"]').filter({ hasText: name }).click({ timeout: UI_READY });
        await expect(page.getByPlaceholder('Option 2', { exact: true })).toHaveValue(beta, { timeout: UI_READY });
        await page.getByPlaceholder('Option 2', { exact: true }).fill(gamma);
        const saved = page.waitForResponse(r => new URL(r.url()).pathname === `${LABELS}${labelId}/` &&
          r.request().method() === 'PUT', { timeout: UI_READY });
        await page.getByRole('button', { name: 'Save', exact: true }).click();
        expect((await saved).status()).toBe(200);
      });
    }
    const expected = [alpha, phase === 'initial' ? beta : gamma];
    await test.step(`backend check ${phase === 'initial' ? 2 : 3}: ${phase} catalog and real widget picker`, async () => {
      const rows = await probe.pg<LabelRow>(
        'SELECT id, settings FROM model_hub_annotationslabels WHERE id = $1 AND deleted = false', [labelId]);
      expect(rows.map(r => ({ id: r.id, options: r.settings.options })))
        .toEqual([{ id: labelId, options: expected.map(label => ({ label })) }]);
      const catalog = await actor.api.post<MetricsPage>(METRICS, {
        source: 'all', cursor_mode: true, category: 'annotation_metric', search: name, page_size: 25,
      });
      expect(catalog.result.metrics.map(m => ({ id: m.property_id, name: m.display_name })))
        .toEqual([{ id: `annotation:${labelId}`, name }]);
      await expect.poll(async () => (await actor.api.post<ValuePage>(VALUES, {
        property_id: `annotation:${labelId}`, source: 'traces', page_size: 25,
      })).result.values.map(v => v.value).sort(), POLL.ASYNC_JOB).toEqual([...expected].sort());

      await page.goto(editorUrl, { waitUntil: 'domcontentloaded' });
      // isWidgetCatalogOptionAllowed requires a selected metric source before
      // global filters become eligible. The built-in trace metric supplies that source;
      // no project filter is selected, preserving workspace-wide label scope.
      await page.getByText('Select Metric', { exact: true }).click({ timeout: UI_READY });
      await page.getByPlaceholder('Search metrics...').fill('trace_count');
      // Option Typography has title={opt.name}; category sidebar does not.
      await page.locator('p[title="Traces"]').click({ timeout: UI_READY });
      await page.getByText('Filter', { exact: true }).click({ timeout: UI_READY });
      await page.getByPlaceholder('Search filter attributes...').fill(name);
      await page.getByText(name, { exact: true }).click({ timeout: UI_READY });
      await page.getByText('Select value...', { exact: true }).click({ timeout: UI_READY });
      // FilterValuePickerPopup renders option labels as titled Typography nodes;
      // this exact set excludes the Select-all control and any stale option.
      const popup = page.getByPlaceholder('Search...', { exact: true })
        .locator('xpath=ancestor::*[contains(@class,"MuiPaper-root")][1]');
      await expect(popup.locator('p[title]')).toHaveText(expected, { timeout: UI_READY });
      await testInfo.attach(`${phase}-picker`, { body: await page.screenshot(), contentType: 'image/png' });
    });
  }
});
