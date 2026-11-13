import { createHash } from 'node:crypto';
import { request, type Request, type Response } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// TraceGrid.jsx / useSaveEntityTags.js / useDashboards.js: actual browser endpoints.
const TRACE_LIST = '/tracer/trace/list_traces_of_session/';
const SPAN_TAGS = '/tracer/observation-span/update-tags/';
const VALUES = '/tracer/dashboard/filter_values/';
// README: first-paint budget per action; the approved plan has nine UI stages.
const UI_READY = 60_000;
// TagInput.jsx defaults to tagUtils.js hashColor; pin the authored rich color,
// rather than treating whatever the save returns as the expected assignment.
const COLORS = ['#7857FC', '#3B82F6', '#06B6D4', '#22C55E', '#F59E0B', '#EF4444', '#EC4899', '#94A3B8'];
const colorFor = (name: string) => {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return COLORS[Math.abs(hash) % COLORS.length];
};
// TraceGrid readQuery sends GET params or the identical POST body. Never invent
// a second filter: replay the request actually made by the browser.
const paramsOf = (req: Request): Record<string, string> => req.method() === 'POST'
  ? req.postDataJSON() : Object.fromEntries(new URL(req.url()).searchParams);
interface Tag { name: string; color?: string }
interface Tagged {
  tags: Tag[]; source_tag_names: string[]; manual_tags: Tag[]; tag_revision: number;
  tag_target: { project_id: string; trace_id: string; span_id: string; start_hour: string;
    observation_type: string; service_name: string; expected_start_time: string; expected_version: string };
}
interface TraceRow extends Tagged { trace_id: string }
interface Span extends Tagged { id: string }
interface Entry { observation_span: Span; children: Entry[] }
interface Detail { result: { trace: Tagged; observation_spans: Entry[] } }
const flatten = (entries: Entry[]): Span[] => entries.flatMap(e => [e.observation_span, ...flatten(e.children)]);
const names = (tags: Tag[]) => tags.map(t => t.name).sort();
const checksum = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');

test('OBS-E2E-063: ClickHouse-only trace and child labels survive reload and filter without losing SDK tags', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-063', area: 'observe',
    userGoal: 'A developer labels an ingested trace and child span, reloads, and finds only the intended trace through tag filtering without losing SDK labels',
    steps: [
      'ingest alpha and beta two-span traces with independently named SDK tags',
      'add a rich manual trace label through the grid and reload',
      'label alpha’s child in the drawer and reload',
      'discover the exact source and manual names through the native Tag picker',
      'filter to exactly alpha and replay the browser list request',
      'remove alpha’s manual trace label through the grid; its SDK label offers no remove action',
      'reload and reopen the Tag picker: the manual name is gone and the SDK tag still filters to exactly alpha',
    ],
    backendChecks: [
      'A: exact ClickHouse source identities belong to the actor project and no PostgreSQL telemetry rows exist',
      'B: only the intended trace/physical-child assignments and revisions change; fresh list/detail reads retain SDK names and rich colors',
      'C: native picker choices and browser/API filtered trace IDs equal the independently seeded oracle and exclude beta',
      'D: source versions, tags, attributes and usage remain unchanged after adding and removing manual labels',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(600_000); // 30s setup + 15s ingestion + 9 × 60s UI + 15s headroom.
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  try {
    const prefix = `e2e-th7275-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const alpha = `${prefix}-alpha`, beta = `${prefix}-beta`;
    const childName = `${alpha}-child`;
    const sdkA = `${prefix}-sdk-alpha`, sdkAC = `${prefix}-sdk-alpha-child`;
    const sdkB = `${prefix}-sdk-beta`, sdkBC = `${prefix}-sdk-beta-child`;
    const manual = { name: `${prefix}-manual-trace`, color: colorFor(`${prefix}-manual-trace`) };
    const childManual = { name: `${prefix}-manual-child`, color: colorFor(`${prefix}-manual-child`) };
    const start = BigInt(Date.now() - 30_000) * 1_000_000n;
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: prefix, startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n };
    // Existing typed sendTrace seeder; no direct source-store writes or fixtures.
    const a = await sendTrace(req, { ...cfg, rootName: alpha, childName,
      rootAttributes: { 'fi.span.kind': 'chain', 'tag.tags': [sdkA] },
      childAttributes: { 'tag.tags': [sdkAC], 'gen_ai.usage.input_tokens': 11,
        'gen_ai.usage.output_tokens': 3, 'gen_ai.cost.total': 0.25 } });
    const b = await sendTrace(req, { ...cfg, rootName: beta, childName: `${beta}-child`,
      rootAttributes: { 'fi.span.kind': 'chain', 'tag.tags': [sdkB] },
      childAttributes: { 'tag.tags': [sdkBC], 'gen_ai.usage.input_tokens': 17,
        'gen_ai.usage.output_tokens': 5, 'gen_ai.cost.total': 0.5 } });
    const attach = (name: string, value: unknown) => testInfo.attach(name, {
      body: JSON.stringify(value, null, 2), contentType: 'application/json',
    });
    await attach('seeded-identities', { projectName: prefix, a, b, start: String(start),
      alpha, beta, childName, sdkA, sdkAC, sdkB, sdkBC, manual, childManual });

    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
      [prefix, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    await attach('project-scope', { projectId, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    const scope = { p: projectId, a: a.traceId, b: b.traceId };
    // Source schema: clickhouse25exporter/converter.go. FINAL retains the winner
    // including its version; both source tables are ReplacingMergeTree.
    const sources = async () => ({
      spans: await probe.ch<Record<string, unknown>>(
        'SELECT * FROM spans FINAL WHERE project_id={p:UUID} AND trace_id IN ({a:String},{b:String}) ORDER BY trace_id,id', scope),
      traces: await probe.ch<Record<string, unknown>>(
        'SELECT * FROM traces FINAL WHERE project_id={p:UUID} AND id IN ({a:UUID},{b:UUID}) ORDER BY toString(id)', scope),
    });
    await expect.poll(async () => {
      const current = await sources();
      return { spans: current.spans.map(s => String(s.id)).sort(), traces: current.traces.map(t => String(t.id)).sort() };
    }, POLL.SPAN_VISIBLE).toEqual({ spans: [...a.spanIds, ...b.spanIds].sort(), traces: [a.traceId, b.traceId].sort() });
    const before = await sources();
    const identity = (s: Record<string, unknown>) => ({ id: s.id, trace_id: s.trace_id,
      parent_span_id: s.parent_span_id, project_id: s.project_id, org_id: s.org_id,
      service_name: s.service_name, observation_type: s.observation_type });
    const expectedIdentities = [a, b].flatMap(t => t.spanIds.map((id, i) => ({ id, trace_id: t.traceId,
      parent_span_id: i === 0 ? '' : t.spanIds[0], project_id: projectId, org_id: actor.organizationId,
      service_name: prefix, observation_type: i === 0 ? 'chain' : 'llm' })))
      .sort((x, y) => `${x.trace_id}/${x.id}`.localeCompare(`${y.trace_id}/${y.id}`));
    expect(before.spans.map(identity), 'A: exact source identities').toEqual(expectedIdentities);
    const pgTelemetry = () => probe.pg<{ traces: number; spans: number }>(
      `SELECT (SELECT count(*)::int FROM tracer_trace WHERE project_id=$1) AS traces,
       (SELECT count(*)::int FROM tracer_observation_span WHERE project_id=$1) AS spans`, [projectId]);
    expect(await pgTelemetry()).toEqual([{ traces: 0, spans: 0 }]);
    await attach('source-before', before);
    // Nonzero usage witnesses prevent a trivially invariant all-zero rollup.
    // spans columns (002_spans_v2.sql): total_tokens Int32, cost Float64.
    const usage = () => probe.ch<{ tokens: string; cost: number }>(
      'SELECT toString(sum(total_tokens)) AS tokens, sum(cost) AS cost FROM spans FINAL WHERE project_id={p:UUID} AND trace_id IN ({a:String},{b:String})', scope);
    const usageBefore = await usage();
    expect(usageBefore).toEqual([{ tokens: '36', cost: 0.75 }]);

    // EntityTagAssignment's full physical child key; trace sentinel per model.
    const assignments = () => probe.pg<Record<string, unknown>>(
      `SELECT project_id,entity_kind,trace_id,span_id,start_hour,observation_type,service_name,manual_tags,revision::int
       FROM tracer_entity_tag_assignment WHERE project_id=$1 ORDER BY entity_kind,trace_id,span_id`, [projectId]);
    expect(await assignments()).toEqual([]);
    const hour = new Date(Number(start / 1_000_000n)); hour.setUTCMinutes(0, 0, 0);
    const checkAssignments = async (revision: number, traceTags: Tag[], childTags: Tag[] | null) => {
      const expected = [
        ...(childTags === null ? [] : [{ project_id: projectId, entity_kind: 'span', trace_id: a.traceId,
          span_id: a.spanIds[1], start_hour: hour, observation_type: 'llm', service_name: prefix,
          manual_tags: childTags, revision: 1 }]),
        { project_id: projectId, entity_kind: 'trace', trace_id: a.traceId, span_id: '',
          start_hour: new Date(0), observation_type: '', service_name: '', manual_tags: traceTags, revision },
      ];
      expect(await assignments(), 'B: only intended assignment and revision').toEqual(expected);
    };
    const checkTags = (entity: Tagged, source: string, manualTags: Tag[], revision: number) => {
      expect(entity.source_tag_names).toEqual([source]);
      expect(entity.manual_tags).toEqual(manualTags);
      expect(entity.tag_revision).toBe(revision);
      expect(names(entity.tags)).toEqual([source, ...manualTags.map(t => t.name)].sort());
      for (const tag of manualTags) expect(entity.tags.find(t => t.name === tag.name)).toEqual(tag);
    };
    const freshReadback = async (traceTags: Tag[], revision: number, hasChild: boolean) => {
      const rows = await probe.apiList<TraceRow>(TRACE_LIST, {
        project_id: projectId, page_number: 0, page_size: 25, filters: '[]',
      });
      expect(rows.map(r => r.trace_id).sort()).toEqual([a.traceId, b.traceId].sort());
      checkTags(rows.find(r => r.trace_id === a.traceId)!, sdkA, traceTags, revision);
      checkTags(rows.find(r => r.trace_id === b.traceId)!, sdkB, [], 0);
      const detail = await actor.api.get<Detail>(`/tracer/trace/${a.traceId}/`, { project_id: projectId });
      checkTags(detail.result.trace, sdkA, traceTags, revision);
      const spans = flatten(detail.result.observation_spans);
      expect(spans.map(s => s.id).sort()).toEqual([...a.spanIds].sort());
      checkTags(spans.find(s => s.id === a.spanIds[0])!, sdkA, traceTags, revision);
      checkTags(spans.find(s => s.id === a.spanIds[1])!, sdkAC, hasChild ? [childManual] : [], hasChild ? 1 : 0);
      await attach(`fresh-readback-${revision}-${hasChild}`, { rows, detail });
    };
    const saveResponse = (path: string, method: string) => page.waitForResponse(r =>
      new URL(r.url()).pathname === path && r.request().method() === method, { timeout: UI_READY });
    const checkSave = async (response: Response, target: string, revision: number, tags: Tag[]) => {
      expect(response.status()).toBe(200);
      const body = response.request().postDataJSON();
      expect(body).toMatchObject({ project_id: projectId, trace_id: a.traceId,
        span_id: target, expected_tag_revision: revision - 1, tags });
      expect(Object.keys(body).sort()).toEqual(['project_id', 'trace_id', 'span_id', 'start_hour',
        'observation_type', 'service_name', 'expected_start_time', 'expected_version', 'expected_tag_revision', 'tags'].sort());
      const result = await response.json();
      expect(result.result.tag_revision).toBe(revision);
      expect(result.result.manual_tags).toEqual(tags);
      await attach(`save-${target}-${revision}`, { body, response: result });
    };
    // Existing TagsCell role, AG Grid physical row ID and TagInput placeholder.
    const row = (id: string) => page.locator(`.clean-data-table:visible .ag-row[row-id="${id}"]`);
    const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
    const tagCell = () => row(a.traceId).locator('[col-id="tags"]');
    const drawer = () => page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(a.traceId, { exact: true }) });
    const traceTagPath = `/tracer/trace/${a.traceId}/tags/`;
    const url = `/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace`;

    // DisplayPanel / ColumnVisibilityDropdown: mount Tags without depending on
    // horizontal virtualization or changing product test hooks. A reload restores
    // the default column set (captured on the managed stack), so re-apply it.
    // ObserveToolbar.jsx portals the grid toolbar into #observe-toolbar-slot; the
    // trace drawer has its own Display button while it is mounted.
    const showTagColumns = async () => {
      await page.locator('#observe-toolbar-slot').getByRole('button', { name: 'Display', exact: true }).click();
      await page.getByText('View columns', { exact: true }).click();
      const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Search', { exact: true }) });
      const all = popup.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
      await all.check(); await all.uncheck();
      for (const name of ['Trace Name', 'Tags'])
        await popup.getByText(name, { exact: true }).locator('..').getByRole('checkbox').check();
      await expect(popup.getByRole('checkbox', { checked: true })).toHaveCount(2, { timeout: UI_READY });
      await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI_READY });
    };

    await test.step('1: open the project and show Trace Name and Tags', async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY }).toEqual([alpha, beta].sort());
      await showTagColumns();
      await expect(tagCell().getByText(sdkA, { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('2: add a rich manual trace tag through the grid', async () => {
      await tagCell().getByRole('button', { name: 'Edit tags', exact: true }).click();
      await page.getByPlaceholder('Add tag...', { exact: true }).fill(manual.name);
      const saved = saveResponse(traceTagPath, 'PATCH');
      await page.getByPlaceholder('Add tag...', { exact: true }).press('Enter');
      await checkSave(await saved, a.spanIds[0], 1, [manual]);
      await checkAssignments(1, [manual], null);
      await page.keyboard.press('Escape');
    }, { timeout: UI_READY });
    await test.step('3: reload and independently read trace and child tags', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY }).toEqual([alpha, beta].sort());
      await showTagColumns();
      await expect(tagCell().getByText(manual.name, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(tagCell().getByText(sdkA, { exact: true })).toBeVisible({ timeout: UI_READY });
      await freshReadback([manual], 1, false);
    }, { timeout: UI_READY });
    await test.step('4: label only alpha’s child in the real drawer', async () => {
      await row(a.traceId).locator('[col-id="trace_name"]').click();
      await drawer().getByRole('region', { name: 'Trace Tree', exact: true }).getByText(childName, { exact: true }).click();
      await expect(drawer().getByText(a.spanIds[1], { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await drawer().getByRole('button', { name: 'tag', exact: true }).click();
      await drawer().getByPlaceholder('tag name', { exact: true }).fill(childManual.name);
      const saved = saveResponse(SPAN_TAGS, 'POST');
      await drawer().getByPlaceholder('tag name', { exact: true }).press('Enter');
      await checkSave(await saved, a.spanIds[1], 1, [childManual]);
      await checkAssignments(1, [manual], [childManual]);
    }, { timeout: UI_READY });
    await test.step('5: reload the child and confirm isolated fresh readback', async () => {
      await page.reload({ waitUntil: 'domcontentloaded' });
      // Drawer identity is in the URL; reopening explicitly also covers hosts
      // which do not restore the selected span after a reload.
      if (!(await drawer().count())) await row(a.traceId).locator('[col-id="trace_name"]').click();
      await drawer().getByRole('region', { name: 'Trace Tree', exact: true }).getByText(childName, { exact: true }).click();
      await expect(drawer().getByText(childManual.name, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(drawer().getByText(sdkAC, { exact: true })).toBeVisible({ timeout: UI_READY });
      await freshReadback([manual], 1, true);
      await page.keyboard.press('Escape');
      await expect(drawer()).toBeHidden({ timeout: UI_READY });
      await showTagColumns();
    }, { timeout: UI_READY });
    let filteredParams: Record<string, string>;
    await test.step('6: discover exactly the source and manual choices in Tag', async () => {
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.getByPlaceholder('Search properties...').fill('Tag');
      const values = page.waitForResponse(r => new URL(r.url()).pathname === VALUES &&
        r.request().method() !== 'OPTIONS' && paramsOf(r.request()).metric_name === 'tag', { timeout: UI_READY });
      await page.locator('[data-filter-property-option="tag"]').click();
      await page.locator('[data-filter-value-trigger="tag"]').click();
      const response = await values;
      expect(response.status()).toBe(200);
      const body = await response.json();
      const expected = [sdkA, sdkAC, sdkB, sdkBC, manual.name, childManual.name].sort();
      expect(body.result.values.map((v: { value: string }) => v.value).sort()).toEqual(expected);
      await expect.poll(() => page.locator('[data-filter-value-option]').evaluateAll(elements =>
        elements.map(e => e.getAttribute('data-filter-value-option')).sort()), { timeout: UI_READY }).toEqual(expected);
      await attach('picker-choices', { params: paramsOf(response.request()), body, expected });
    }, { timeout: UI_READY });
    await test.step('7: filter to exactly alpha and replay the actual browser query', async () => {
      const filtered = page.waitForResponse(r => new URL(r.url()).pathname === TRACE_LIST &&
        r.request().method() !== 'OPTIONS' && JSON.stringify(paramsOf(r.request())).includes(manual.name), { timeout: UI_READY });
      await page.locator(`[data-filter-value-option="${manual.name}"]`).click();
      await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      const response = await filtered;
      expect(response.status()).toBe(200);
      filteredParams = paramsOf(response.request());
      // Captured wire: the grid's default created_at window plus exactly one Tag item.
      const wire: { column_id: string; filter_config: { filter_value: unknown } }[] = JSON.parse(filteredParams.filters);
      expect(wire.map(f => f.column_id).sort()).toEqual(['created_at', 'tag']);
      expect(wire.find(f => f.column_id === 'tag')).toMatchObject({ property_id: 'system_attribute:traces:tag',
        filter_config: { filter_op: 'in', filter_value: [manual.name] } });
      const body = await response.json();
      expect(body.result.table.map((r: TraceRow) => r.trace_id), 'C: exact filtered IDs').toEqual([a.traceId]);
      const replay = await actor.api.post<{ result: { table: TraceRow[] } }>(TRACE_LIST, filteredParams);
      expect(replay.result.table.map(r => r.trace_id)).toEqual([a.traceId]);
      await expect(traceNames).toHaveText([alpha], { timeout: UI_READY });
      await attach('filtered-alpha', { params: filteredParams, body, replay });
    }, { timeout: UI_READY });
    await test.step('8: remove only the manual trace tag through the grid', async () => {
      await tagCell().getByRole('button', { name: 'Edit tags', exact: true }).click();
      const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Add tag...', { exact: true }) });
      // TagChip.jsx: the unique exact-name chip owns one remove IconButton;
      // source-owned chips have none. No style- or icon-dependent selector.
      await expect(popup.getByText(sdkA, { exact: true }).locator('..').getByRole('button')).toHaveCount(0, { timeout: UI_READY });
      const saved = saveResponse(traceTagPath, 'PATCH');
      await popup.getByText(manual.name, { exact: true }).locator('..').getByRole('button').click();
      await checkSave(await saved, a.spanIds[0], 2, []);
      await checkAssignments(2, [], [childManual]);
      await page.keyboard.press('Escape');
    }, { timeout: UI_READY });
    await test.step('9: the manual match disappears, SDK filtering survives and sources do not change', async () => {
      // The grid does not restore filters or column choices across a reload
      // (captured on the managed stack): the user re-applies the Tag filter.
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI_READY }).toEqual([alpha, beta].sort());
      await showTagColumns();
      await expect(tagCell().getByText(sdkA, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(tagCell().getByText(manual.name, { exact: true })).toHaveCount(0, { timeout: UI_READY });
      // The exact browser query from step 7 no longer matches anything.
      const empty = await actor.api.post<{ result: { table: TraceRow[] } }>(TRACE_LIST, filteredParams!);
      expect(empty.result.table).toEqual([]);
      await freshReadback([], 2, true);
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.getByPlaceholder('Search properties...').fill('Tag');
      const values = page.waitForResponse(r => new URL(r.url()).pathname === VALUES &&
        r.request().method() !== 'OPTIONS' && paramsOf(r.request()).metric_name === 'tag', { timeout: UI_READY });
      await page.locator('[data-filter-property-option="tag"]').click();
      await page.locator('[data-filter-value-trigger="tag"]').click();
      const remaining = [sdkA, sdkAC, sdkB, sdkBC, childManual.name].sort();
      const valuesBody = await (await values).json();
      expect(valuesBody.result.values.map((v: { value: string }) => v.value).sort()).toEqual(remaining);
      await expect.poll(() => page.locator('[data-filter-value-option]').evaluateAll(elements =>
        elements.map(e => e.getAttribute('data-filter-value-option')).sort()), { timeout: UI_READY })
        .toEqual(remaining);
      const filtered = page.waitForResponse(r => new URL(r.url()).pathname === TRACE_LIST &&
        r.request().method() !== 'OPTIONS' && JSON.stringify(paramsOf(r.request())).includes(sdkA), { timeout: UI_READY });
      await page.locator(`[data-filter-value-option="${sdkA}"]`).click();
      await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      const response = await filtered;
      expect(response.status()).toBe(200);
      const body = await response.json();
      expect(body.result.table.map((r: TraceRow) => r.trace_id)).toEqual([a.traceId]);
      const replay = await actor.api.post<{ result: { table: TraceRow[] } }>(TRACE_LIST, paramsOf(response.request()));
      expect(replay.result.table.map(r => r.trace_id)).toEqual([a.traceId]);
      await expect(traceNames).toHaveText([alpha], { timeout: UI_READY });
      await expect(tagCell().getByText(sdkA, { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(tagCell().getByText(manual.name, { exact: true })).toHaveCount(0, { timeout: UI_READY });
      const after = await sources();
      expect(checksum(after), 'D: unchanged source checksum').toBe(checksum(before));
      expect(await usage()).toEqual(usageBefore);
      expect(await pgTelemetry()).toEqual([{ traces: 0, spans: 0 }]);
      await checkAssignments(2, [], [childManual]);
      await attach('source-conservation', { before: checksum(before), after: checksum(after), usageBefore,
        usageAfter: await usage(), assignments: await assignments(), sourceAfter: after });
      await testInfo.attach('sdk-filter-after-removal', { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
  } finally {
    await req.dispose();
  }
});
