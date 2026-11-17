import { isDeepStrictEqual } from 'node:util';
import { request, type Request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/utils/axios.js:1972-1973,2005-2006; list params:
// frontend/src/sections/projects/LLMTracing/TraceGrid.jsx:460-484, SpanGrid.jsx:617-631.
const LIST = {
  traces: '/tracer/trace/list_traces_of_session/',
  spans: '/tracer/observation-span/list_spans_observe/',
};
// futureagi/tracer/serializers/saved_view.py:99-156.
const SAVED_VIEWS = '/tracer/saved-views/';
const UI_READY = 60_000;
// frontend/src/sections/projects/LLMTracing/inputOutputSearch.js:11-26.
const searchLeaf = (value: string) => ({
  column_id: 'input_output', display_name: 'Input or output',
  filter_config: { col_type: 'SYSTEM_METRIC', filter_type: 'text', filter_op: 'contains', filter_value: value },
});
type Filter = ReturnType<typeof searchLeaf>;
// Lists use the shared readQuery transport (TraceGrid.jsx:94 / SpanGrid.jsx:92).
const wire = (r: Request): Record<string, unknown> => r.method() === 'POST'
  ? r.postDataJSON() : Object.fromEntries(new URL(r.url()).searchParams);
const contentLeaves = (r: Request): Filter[] => {
  const raw = wire(r).filters;
  const filters = (typeof raw === 'string' ? JSON.parse(raw) : raw) as Filter[];
  return filters.filter(f => f.column_id === 'input_output');
};
// futureagi/tracer/serializers/trace.py:336-363 and observation_span.py:447-457.
type ListRow = { trace_id: string; span_id: string };
const sorted = <T>(rows: T[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));

test.use({ viewport: { width: 1600, height: 1100 } });
test('OBS-E2E-066: search trace and span input or output and restore it from saved views', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-066', area: 'observe',
    userGoal: 'A developer finds traces and spans by input or output content, clears the search, and restores it from a saved view',
    steps: [
      'seed three traces with input-only, child-output-only and name-only controls',
      'in trace and span tables search common, input-only, output-only and absent markers with different casing',
      'clear the search and recover all rows',
      'save a searched view, clear without saving, reselect the view and reload it',
    ],
    backendChecks: [
      'the scoped PG project contains exactly the three curated traces and six source spans with independently expected input/output payloads in ClickHouse FINAL',
      'actual browser list requests carry the exact reserved content leaf; responses and visible name sets equal the seeded match matrix, and clearing removes the leaf',
      'each saved view persists its search in PG and restores the textbox, exact request filter and exact API/UI results on reselection and reload',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(1_530_000); // 2 SPAN_VISIBLE + 24 UI_READY chains + 60s setup/disposal.
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-obs66-${suffix}`;
  const common = `CoMmOn-${suffix}`, inputOnly = `InPuT-${suffix}`, outputOnly = `OuTpUt-${suffix}`;
  const absent = `absent-${suffix}`;
  // input.value/output.value: fi-collector/exporter/clickhouse25exporter/converter.go:883-912.
  const matrix = [
    { names: [`A-root-${suffix}`, `A-child-${suffix}`], inputs: [`${common} ${inputOnly}`, 'child A input'], outputs: ['root A output', 'child A output'] },
    { names: [`B-root-${suffix}`, `B-child-${suffix}`], inputs: ['root B input', 'child B input'], outputs: ['root B output', `${common} ${outputOnly}`] },
    { names: [`C-name-only-${common}`, `C-child-${suffix}`], inputs: ['root C input', 'child C input'], outputs: ['root C output', 'child C output'] },
  ];
  try {
    const seeded: SeededTrace[] = [];
    for (const [i, item] of matrix.entries()) {
      // Explicit recent timestamps and two-span shape: e2e/lib/otlp.ts:27-49.
      const at = BigInt(Date.now() - 30_000 + i * 1000) * 1_000_000n;
      const trace = await sendTrace(req, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
        rootName: item.names[0], childName: item.names[1], startTimeUnixNano: at, endTimeUnixNano: at + 50_000_000n,
        rootAttributes: { 'input.value': item.inputs[0], 'output.value': item.outputs[0] },
        childAttributes: { 'input.value': item.inputs[1], 'output.value': item.outputs[1] },
      });
      seeded.push(trace);
      await testInfo.attach(`seed-${i}.json`, { contentType: 'application/json', body: JSON.stringify({ ...trace, ...item }) });
    }
    // Project/CH schema and empty root parent: e2e/flows/observe/native-source.ts:261,302-333.
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
      [projectName, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    await testInfo.attach('project.json', { contentType: 'application/json', body: JSON.stringify({ projectId, projectName }) });
    const expectedSpans = seeded.flatMap((t, i) => t.spanIds.map((id, j) => ({
      trace_id: t.traceId, id, parent_span_id: j === 0 ? '' : t.spanIds[0],
      name: matrix[i].names[j], input: matrix[i].inputs[j], output: matrix[i].outputs[j],
    })));
    await expect.poll(async () => sorted(await probe.ch(
      'SELECT trace_id,id,parent_span_id,name,input,output FROM spans FINAL WHERE project_id={p:UUID}',
      { p: projectId })), POLL.SPAN_VISIBLE).toEqual(sorted(expectedSpans));
    await expect.poll(async () => sorted(await probe.ch(
      'SELECT id,name,input,output FROM traces FINAL WHERE project_id={p:UUID}', { p: projectId })), POLL.SPAN_VISIBLE)
      .toEqual(sorted(seeded.map((t, i) => ({ id: t.traceId, name: matrix[i].names[0], input: matrix[i].inputs[0], output: matrix[i].outputs[0] }))));

    for (const mode of ['traces', 'spans'] as const) {
      // Routes: frontend/src/sections/projects/LLMTracing/LLMTracingView.jsx:855-901.
      const defaultUrl = `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=${mode === 'traces' ? 'trace' : 'spans'}`;
      // Accessible labels: frontend/src/sections/projects/LLMTracing/InputOutputSearch.jsx:14-50.
      const textbox = page.getByRole('textbox', { name: 'Search input or output', exact: true });
      const clear = () => page.getByRole('button', { name: 'Clear input or output search', exact: true }).click();
      // Names: frontend/src/sections/projects/LLMTracing/Renderers/NameCell.jsx:43-55.
      const cells = page.locator(`.ag-row [col-id="${mode === 'traces' ? 'trace_name' : 'span_name'}"]:visible`);
      const all = mode === 'traces' ? [[0, 0], [1, 0], [2, 0]] : [[0, 0], [0, 1], [1, 0], [1, 1], [2, 0], [2, 1]];
      const commonMatches = mode === 'traces' ? [[0, 0], [1, 0]] : [[0, 0], [1, 1]];
      const outputMatches = mode === 'traces' ? [[1, 0]] : [[1, 1]];
      // Backend scope any-member vs one span and ILIKE: latest_filter_predicates.py:1884-1923,1953-1955.
      const check = async (label: string, value: string, matches: number[][], action: () => Promise<unknown>) => {
        await test.step(`${mode}: ${label}`, async () => {
          const responsePromise = page.waitForResponse(r => {
            if (new URL(r.url()).pathname !== LIST[mode] || r.request().method() === 'OPTIONS') return false;
            // Saved-view hydration reorders keys (frontend/src/api/contracts/filter-contract.js:359-375,459-470).
            return wire(r.request()).project_id === projectId
              && isDeepStrictEqual(contentLeaves(r.request()), value ? [searchLeaf(value)] : []);
          }, { timeout: UI_READY });
          await action();
          const response = await responsePromise;
          expect(response.status()).toBe(200);
          expect(contentLeaves(response.request())).toEqual(value ? [searchLeaf(value)] : []);
          // Actual browser envelope: futureagi/tracer/views/trace.py:7329-7336; observation_span.py:3353-3362.
          const body = await response.json() as { result: { table: ListRow[] } };
          const identities = body.result.table.map(r => mode === 'traces' ? r.trace_id : `${r.trace_id}:${r.span_id}`);
          expect(identities.sort()).toEqual(matches.map(([i, j]) => mode === 'traces'
            ? seeded[i].traceId : `${seeded[i].traceId}:${seeded[i].spanIds[j]}`).sort());
          await expect(textbox).toHaveValue(value, { timeout: UI_READY });
          await expect.poll(async () => (await cells.allTextContents()).map(s => s.trim()).sort(), { timeout: UI_READY })
            .toEqual(matches.map(([i, j]) => matrix[i].names[j]).sort());
        }, { timeout: UI_READY });
      };
      await check('all seeded rows', '', all, () => page.goto(defaultUrl));
      await check('common content, case insensitive', common.toLowerCase(), commonMatches, () => textbox.fill(common.toLowerCase()));
      await check('input only', inputOnly.toUpperCase(), [[0, 0]], () => textbox.fill(inputOnly.toUpperCase()));
      await check('child output only', outputOnly.toLowerCase(), outputMatches, () => textbox.fill(outputOnly.toLowerCase()));
      await check('no matches', absent, [], () => textbox.fill(absent));
      await check('clear', '', all, clear);
      const savedValue = common.toUpperCase();
      await check('search to save', savedValue, commonMatches, () => textbox.fill(savedValue));
      const viewName = `e2e-obs66-${mode}-${suffix}`;
      let viewId = '';
      await test.step(`${mode}: save searched view and verify PG`, async () => {
        // frontend/src/components/observe-tabs/ObserveTabBar.jsx:501; frontend/src/components/traceDetail/SaveViewDialog.jsx:100-111,164-177.
        await page.locator('[data-create-view-btn]').click();
        await page.getByLabel(/View Name/).fill(viewName);
        const saving = page.waitForResponse(r => new URL(r.url()).pathname === SAVED_VIEWS
          && r.request().method() === 'POST', { timeout: UI_READY });
        await page.getByRole('button', { name: 'Save view', exact: true }).click();
        const response = await saving;
        expect(response.ok()).toBe(true);
        // frontend/src/sections/projects/LLMTracing/LLMTracingView.jsx:2948-3024 persists the UI config, not a fabricated test config.
        expect(response.request().postDataJSON()).toMatchObject({
          project_id: projectId, name: viewName, tab_type: mode, config: { extra_filters: [searchLeaf(savedValue)] },
        });
        viewId = (await response.json()).result.id;
        await testInfo.attach(`view-${mode}.json`, { contentType: 'application/json', body: JSON.stringify({ viewId, projectId, viewName }) });
        // futureagi/tracer/models/saved_view.py:27-65.
        const rows = await probe.pg<{ id: string; tab_type: string; config: { extra_filters: Filter[] } }>(
          'SELECT id,tab_type,config FROM tracer_saved_view WHERE id=$1 AND project_id=$2 AND name=$3 AND workspace_id=$4 AND deleted=false',
          [viewId, projectId, viewName, actor.workspaceId]);
        expect(rows).toHaveLength(1);
        expect(rows[0]).toMatchObject({ id: viewId, tab_type: mode, config: { extra_filters: [searchLeaf(savedValue)] } });
      }, { timeout: UI_READY });
      await check('clear without updating saved view', '', all, clear);
      await test.step(`${mode}: return to default`, async () => {
        await page.goto(defaultUrl);
        await expect(textbox).toHaveValue('', { timeout: UI_READY });
      }, { timeout: UI_READY });
      // CustomViewTab.jsx:60-71: inactive tooltip adds " · Press <shortcut>" to the accessible name.
      // ObserveTabBar.jsx:76-96 also gives the sortable wrapper role=button; select the native button only.
      await check('reselect saved view', savedValue, commonMatches,
        () => page.getByRole('button', { name: new RegExp(`^${viewName}( · Press \\d+)?$`) }).and(page.locator('button')).click());
      // frontend/src/sections/projects/LLMTracing/LLMTracingView.jsx:2551-2552.
      await expect(page).toHaveURL(u => u.searchParams.get('tab') === `view-${viewId}`, { timeout: UI_READY });
      await check('reload saved view', savedValue, commonMatches, () => page.reload());
    }
  } finally {
    await req.dispose();
  }
});
