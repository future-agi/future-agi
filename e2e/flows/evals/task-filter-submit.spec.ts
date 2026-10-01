import { request, type Response } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/mock-model-fixtures';
import { E2E } from '../../lib/env';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { inspectManagedMock } from '../../lib/managed-mock';
import { flowAnnotation } from '../../lib/flow-meta';

const ATTRIBUTE = 'e2e.task.cohort';
const INCLUDED = 'include';
const EXCLUDED = 'exclude';
const LIST_PATH = '/tracer/observation-span/list_spans_observe/';
const TASK_PATH = '/tracer/eval-task/';
const CONFIG_PATH = '/tracer/custom-eval-config/';
const MOCK_USAGE = { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 };

function assertLocalStack() {
  return inspectManagedMock({ evalBackground: true });
}

interface PreviewBody {
  result: { table: { span_id: string }[]; metadata: {
    has_more: boolean; next_cursor: string | null; total_rows_exact: number | null;
  } };
}

// Before actor provisioning, and again immediately before every model-capable
// operation. No shared actor, source-row mutation, task API create or teardown.
test.beforeAll(() => { assertLocalStack(); });
test.use({ evalBackground: true, viewport: { width: 1600, height: 1100 } });

test('EVAL-E2E-005: browser task submit retains a custom attribute predicate and excludes other spans', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-005', area: 'evals',
    userGoal: 'Create an evaluation task from the browser for exactly the selected custom attribute value',
    steps: ['seed two differently attributed spans in an isolated project',
      'select the project, custom property and value on the task create page',
      'verify the exact matching preview', 'select and configure an echo judge',
      'click the final Create Task button', 'verify task and result inclusion and exclusion'],
    backendChecks: ['managed mock routing attested before model calls',
      'UI POST and stored task retain the custom predicate',
      'completed task has exactly the matching span in PG and CH, with mock verdict and usage'],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  test.setTimeout(420_000);
  page.setDefaultTimeout(15_000);
  const json = async (name: string, body: unknown) => {
    const path = testInfo.outputPath(name);
    await writeFile(path, JSON.stringify(body, null, 2));
    await testInfo.attach(name, { path, contentType: 'application/json' });
  };
  const snapshot = async (name: string) => {
    const aria = testInfo.outputPath(`${name}.aria`);
    const png = testInfo.outputPath(`${name}.png`);
    await writeFile(aria, await page.locator('body').ariaSnapshot());
    await page.screenshot({ path: png, fullPage: true });
    await testInfo.attach(`${name}.aria`, { path: aria, contentType: 'text/plain' });
    await testInfo.attach(`${name}.png`, { path: png, contentType: 'image/png' });
  };
  const exchanges: unknown[] = [];
  const pending = new Set<Promise<void>>();
  const capture = (response: Response) => {
    const req = response.request();
    const url = new URL(response.url());
    if (url.origin !== E2E.apiUrl || !/\/tracer\/|eval-templates/.test(url.pathname)) return;
    const operation = (async () => {
      exchanges.push({ method: req.method(), url: response.url(), request: req.postData(),
        status: response.status(), response: await response.text() });
    })().catch(error => { exchanges.push({ url: response.url(), readError: String(error) }); });
    pending.add(operation);
    void operation.finally(() => pending.delete(operation));
  };
  page.on('response', capture);
  const req = await request.newContext();
  const suffix = `${Date.now().toString(36)}-${testInfo.workerIndex}`;
  const projectName = `e2e-task-filter-${suffix}`;
  const evalName = `e2e-filter-judge-${suffix}`;
  const taskName = `e2e-filter-task-${suffix}`;
  const verdict = `e2e-filter-verdict-${suffix}`;
  const exhaustPreview = async (first: Response) => {
    const firstRequest = first.request().postDataJSON();
    const responses = [first];
    const receive = (response: Response) => {
      if (!response.url().endsWith(LIST_PATH)) return;
      const reqBody = response.request().postDataJSON();
      if (reqBody.project_id === firstRequest.project_id && reqBody.filters === firstRequest.filters) {
        responses.push(response);
      }
    };
    page.on('response', receive);
    const pages: { request: unknown; body: PreviewBody }[] = [];
    const clicked = new Set<string>();
    try {
      await expect.poll(async () => {
        while (pages.length < responses.length) {
          const response = responses[pages.length];
          expect(response.status()).toBe(200);
          const body = await response.json() as PreviewBody;
          expect(body.result.metadata).toMatchObject({ query_complete: true, query_status: 'complete' });
          pages.push({ request: response.request().postDataJSON(), body });
        }
        const last = pages.at(-1)!.body.result;
        if (!last.metadata.has_more) return true;
        const cursor = last.metadata.next_cursor;
        expect(cursor).toEqual(expect.any(String));
        const next = page.getByRole('button', { name: 'Next row', exact: true });
        // Empty cursor pages are continued by the product itself. Click only
        // when a rendered row is awaiting the user's next-page navigation.
        if (last.table.length && cursor && !clicked.has(cursor) && await next.isEnabled()) {
          clicked.add(cursor);
          await next.click();
        }
        return false;
      }, { timeout: 30_000, intervals: [500] }).toBe(true);
      expect(pages.at(-1)!.body.result.metadata).toMatchObject({
        total_rows_is_lower_bound: false, has_more: false, next_cursor: null,
      });
      await expect(page.getByRole('button', { name: 'Next row', exact: true })).toBeDisabled();
      return pages;
    } finally {
      page.off('response', receive);
    }
  };
  try {
    await json('managed-mock-before.json', assertLocalStack());
    const seeded = await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName, rootName: `e2e-excluded-${suffix}`, childName: `e2e-included-${suffix}`,
      // Both rows have valid eval input: only the selected predicate can exclude root.
      rootAttributes: { [ATTRIBUTE]: EXCLUDED, 'fi.span.kind': 'llm' },
      childAttributes: { [ATTRIBUTE]: INCLUDED },
      resourceAttributes: { project_type: 'observe' },
    });
    await expect.poll(async () => (await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id={t:String}', { t: seeded.traceId }))[0].n,
    POLL.SPAN_VISIBLE).toBe('2');
    const [{ project_id: projectId }] = await probe.ch<{ project_id: string }>(
      'SELECT DISTINCT project_id FROM spans FINAL WHERE trace_id={t:String}', { t: seeded.traceId });
    await json('fixture.json', { projectId, projectName, organizationId: actor.organizationId,
      workspaceId: actor.workspaceId, traceId: seeded.traceId, taskName, evalName,
      includedSpanIds: [seeded.spanIds[1]], excludedSpanIds: [seeded.spanIds[0]], attribute: ATTRIBUTE });
    const model = mockModel;
    // Same deterministic prompt/verdict helper pattern as eval-task.spec.ts.
    const template = await actor.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
      name: evalName, eval_type: 'llm', model: model.model,
      instructions: `Reply with exactly this JSON: {"result": "Pass", "explanation": "${verdict} saw {{output}}"}`,
      output_type: 'pass_fail', pass_threshold: 0.5,
    });
    await json('template.json', template);

    await test.step('browser chooses custom property/value and previews exactly one span', async () => {
      // Supported deep-link date inputs keep this synthetic run bounded.
      const window = new URLSearchParams({ startDate: new Date(Date.now() - 600_000).toISOString(),
        endDate: new Date(Date.now() + 600_000).toISOString() });
      await page.goto(`/dashboard/tasks/create?${window}`);
      await page.getByPlaceholder('Enter task name').fill(taskName);
      await page.getByPlaceholder('Select Option', { exact: true }).click();
      const unfilteredResponse = page.waitForResponse(response => response.url().endsWith(LIST_PATH));
      await page.getByRole('menuitem', { name: projectName, exact: true }).click();
      // Live Preview requests one row per cursor page. Visit both before filtering.
      const unfilteredPages = await exhaustPreview(await unfilteredResponse);
      expect(unfilteredPages.flatMap(p => p.body.result.table).map(row => row.span_id).sort())
        .toEqual([...seeded.spanIds].sort());
      expect(unfilteredPages.at(-1)!.body.result.metadata.total_rows_exact).toBe(2);
      await json('unfiltered-preview-pages.json', unfilteredPages);
      await page.getByRole('button', { name: '100%', exact: true }).click();
      await page.getByRole('button', { name: 'Add filter', exact: true }).click();
      await snapshot('filter-open');
      await page.getByText('Property', { exact: true }).click();
      await page.getByPlaceholder('Search properties...').fill(ATTRIBUTE);
      await page.locator(`[data-filter-property-label="${ATTRIBUTE}"]`).click();
      await page.locator(`[data-filter-value-trigger="${ATTRIBUTE}"]`).click();
      await page.getByPlaceholder('Search values...').fill(INCLUDED);
      const previewResponse = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname.endsWith(LIST_PATH) && (response.request().postData() || '').includes(INCLUDED);
      });
      await page.getByText(INCLUDED, { exact: true }).last().click();
      await page.keyboard.press('Escape');
      await page.keyboard.press('Escape');
      const filteredPages = await exhaustPreview(await previewResponse);
      await json('filtered-preview.json', filteredPages);
      // Counts are page-local during signed cursor traversal. Exhaust the UI
      // cursor and compare the entire exact row set, including negative scope.
      expect(filteredPages.flatMap(p => p.body.result.table).map(row => row.span_id)).toEqual([seeded.spanIds[1]]);
      expect(filteredPages.at(-1)!.body.result.metadata.total_rows_exact).toBe(1);
      await expect(page.getByText('Row 1 of 1', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Next row', exact: true })).toBeDisabled();
      await snapshot('filtered-preview');
    });

    let evalConfigId = '';
    await test.step('browser selects and configures the mock judge', async () => {
      await model.assertReady();
      await page.getByRole('button', { name: 'Add Evaluation', exact: true }).click();
      await page.getByPlaceholder('Search evaluations...').fill(evalName);
      await page.getByRole('row').filter({ hasText: evalName }).getByRole('button', { name: 'Add', exact: true }).click();
      const mapping = page.getByPlaceholder('Search or type a path (e.g. attributes.input.value)');
      await mapping.fill(ATTRIBUTE);
      await page.getByRole('option', { name: ATTRIBUTE, exact: true }).click();
      await snapshot('eval-configured');
      const configResponse = page.waitForResponse(response => response.url().endsWith(CONFIG_PATH)
        && response.request().method() === 'POST');
      await page.getByRole('button', { name: 'Add Evaluation', exact: true }).click();
      const response = await configResponse;
      const body = await response.json();
      await json('ui-eval-config.json', { request: response.request().postDataJSON(), status: response.status(), body });
      expect(response.ok()).toBe(true);
      expect(response.request().postDataJSON()).toMatchObject({ model: model.model, mapping: { output: ATTRIBUTE },
        error_localizer: false });
      evalConfigId = body.result.id;
      expect(evalConfigId).toBeTruthy();
    });

    const taskId = await test.step('final browser Create Task retains the predicate', async () => {
      await model.assertReady();
      await expect(page.getByText('Row 1 of 1', { exact: true })).toBeVisible();
      await snapshot('before-final-create');
      const createdResponse = page.waitForResponse(response => response.url().endsWith(TASK_PATH)
        && response.request().method() === 'POST');
      await page.getByRole('button', { name: 'Create Task', exact: true }).click();
      const response = await createdResponse;
      const payload = response.request().postDataJSON();
      const body = await response.json();
      await json('final-ui-submit.json', { request: payload, status: response.status(), body });
      expect(payload).toMatchObject({ project: projectId, evals: [evalConfigId], row_type: 'spans',
        run_type: 'historical', sampling_rate: 100 });
      expect(payload.filters.filters).toHaveLength(1);
      expect(payload.filters.filters[0]).toMatchObject({ column_id: ATTRIBUTE,
        property_id: `custom_attribute:${ATTRIBUTE}`,
        filter_config: { filter_type: 'text', filter_op: 'in', filter_value: [INCLUDED],
          col_type: 'SPAN_ATTRIBUTE', attribute_value_types: ['string'] } });
      expect(response.ok()).toBe(true);
      expect(body.result.id).toBeTruthy();
      await expect(page).toHaveURL(new RegExp(`/dashboard/tasks/${body.result.id}`));
      const [stored] = await probe.pg<{ filters: { date_range: string[] }; row_type: string; sampling_rate: number }>(
        'SELECT filters,row_type,sampling_rate FROM tracer_eval_task WHERE id=$1 AND project_id=$2', [body.result.id, projectId]);
      await json('stored-task.json', stored);
      // DRF persists ISO dates with six fractional digits; compare the same
      // instants while retaining exact equality of every predicate field.
      expect({ ...stored.filters, date_range: stored.filters.date_range.map(value => new Date(value).toISOString()) })
        .toEqual(payload.filters);
      expect(stored.row_type).toBe('spans');
      expect(stored.sampling_rate).toBe(100);
      return body.result.id as string;
    });

    await test.step('completed task/result scope includes only the matching span in PG and CH', async () => {
      let task: { status: string } | undefined;
      await expect.poll(async () => {
        task = await actor.api.get<{ status: string }>(`${TASK_PATH}${taskId}/`);
        return task.status;
      }, POLL.EVAL_RESULT).toBe('completed');
      await json('completed-task.json', task);
      const pgRows = await probe.pg<{ observation_span_id: string; status: string; output_bool: boolean;
        eval_explanation: string; output_metadata: { usage: unknown }; custom_eval_config_id: string }>(
        `SELECT observation_span_id,status,output_bool,eval_explanation,output_metadata,custom_eval_config_id
         FROM tracer_eval_logger WHERE eval_task_id=$1 AND deleted=false ORDER BY observation_span_id`, [taskId]);
      await json('pg-results.json', pgRows);
      expect(pgRows.map(row => row.observation_span_id)).toEqual([seeded.spanIds[1]]);
      expect(pgRows[0]).toMatchObject({ status: 'completed', output_bool: true,
        eval_explanation: `${verdict} saw ${INCLUDED}`, custom_eval_config_id: evalConfigId });
      expect(pgRows[0].output_metadata.usage).toEqual(MOCK_USAGE);
      let chRows: { observation_span_id: string; status: string; output_bool: number | boolean;
        eval_explanation: string; output_metadata: string }[] = [];
      await expect.poll(async () => {
        chRows = await probe.ch<typeof chRows[number]>(
          `SELECT observation_span_id,status,output_bool,eval_explanation,output_metadata FROM tracer_eval_logger FINAL
           WHERE eval_task_id={t:String} ORDER BY observation_span_id`, { t: taskId });
        return chRows.map(row => [row.observation_span_id, row.status]);
      }, POLL.CDC_VISIBLE).toEqual([[seeded.spanIds[1], 'completed']]);
      await json('ch-results.json', chRows);
      expect([1, true]).toContain(chRows[0].output_bool);
      expect(chRows[0].eval_explanation).toBe(`${verdict} saw ${INCLUDED}`);
      expect(JSON.parse(chRows[0].output_metadata).usage).toEqual(MOCK_USAGE);
      const remaining = await probe.ch<{ id: string }>(
        'SELECT id FROM spans FINAL WHERE project_id={p:String} ORDER BY id', { p: projectId });
      expect(remaining.map(row => row.id)).toEqual([...seeded.spanIds].sort());
      await json('source-span-preservation.json', remaining);
      await snapshot('completed-task');
      await json('managed-mock-after.json', assertLocalStack());
    });
  } finally {
    await snapshot('final-browser-state').catch(() => {});
    await Promise.allSettled([...pending]);
    page.off('response', capture);
    await json('browser-exchanges.json', exchanges);
    await req.dispose();
  }
});
