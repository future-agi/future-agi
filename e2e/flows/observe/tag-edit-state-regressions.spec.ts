import { createHash } from 'node:crypto';
import { request, type Page, type Request, type Response, type Route, type TestInfo } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL, type StateProbe } from '../../lib/state-probe';
import type { TestActor } from '../../lib/provisioning';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// TraceGrid.jsx / SpanGrid.jsx / useSaveEntityTags.js: real list and write paths.
const TRACE_LIST = '/tracer/trace/list_traces_of_session/';
const SPAN_LIST = '/tracer/observation-span/list_spans_observe/';
const SPAN_TAGS = '/tracer/observation-span/update-tags/';
// README: first-paint/response budget, not a CDC wait.
const UI_READY = 60_000;
// config/runtime_limits.js: default Observe cell bound; fixture must exceed it.
const CELL_LIMIT = 16_384;
// TagInput.jsx / tagUtils.js: independently predict the rich color the UI authors.
const COLORS = ['#7857FC', '#3B82F6', '#06B6D4', '#22C55E', '#F59E0B', '#EF4444', '#EC4899', '#94A3B8'];
const tag = (name: string): Tag => {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  return { name, color: COLORS[Math.abs(hash) % COLORS.length] };
};
interface Tag { name: string; color?: string }
// tracer/serializers/entity_tags.py TARGET_FIELDS; timestamps/UInt64 stay strings.
interface Target {
  project_id: string; trace_id: string; span_id: string; start_hour: string;
  observation_type: string; service_name: string; expected_start_time: string; expected_version: string;
}
interface Tagged {
  tags: Tag[]; source_tag_names: string[]; manual_tags: Tag[]; tag_revision: number; tag_target: Target;
  tag_assignment_exists: boolean;
}
interface Span extends Tagged { id: string }
interface Entry { observation_span: Span; children: Entry[] }
interface Detail { result: { trace: Tagged; observation_spans: Entry[] } }
interface ListRow extends Tagged { trace_id: string; span_id: string; project_id: string; start_time: string }
interface ListResult { result: { table: ListRow[] } }
interface Oracle {
  traceId: string; spanId: string; name: string; root: boolean; sdk: string[];
  manual: Tag[]; revision: number; target: Target;
}
const flatten = (entries: Entry[]): Span[] => entries.flatMap(e => [e.observation_span, ...flatten(e.children)]);
const names = (tags: Tag[]) => tags.map(t => t.name).sort();
const sortedTags = (tags: Tag[]) => [...tags].sort((a, b) => a.name.localeCompare(b.name));
const checksum = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const paramsOf = (r: Request): Record<string, string> => r.method() === 'POST'
  ? r.postDataJSON() : Object.fromEntries(new URL(r.url()).searchParams);
const attach = (info: TestInfo, name: string, value: unknown) => info.attach(name, {
  body: JSON.stringify(value, null, 2), contentType: 'application/json',
});
const tracePath = (id: string) => `/tracer/trace/${id}/`;
const traceTagsPath = (id: string) => `${tracePath(id)}tags/`;
const mutation = (r: Request) => (r.method() === 'POST' && new URL(r.url()).pathname === SPAN_TAGS)
  || (r.method() === 'PATCH' && /\/tracer\/trace\/[^/]+\/tags\/$/.test(new URL(r.url()).pathname));

/** Test-specific oracle/readbacks, not a new harness or alternative write path. */
async function seedScenario(page: Page, actor: TestActor, probe: StateProbe, info: TestInfo, mode: 'large' | 'roots') {
  const prefix = `e2e-obs${mode === 'roots' ? '68' : info.title.includes('065') ? '65' : '67'}-${info.workerIndex}-${Date.now().toString(36)}`;
  const start = BigInt(Date.now() - 30_000) * 1_000_000n;
  const req = await request.newContext();
  const seeds: Awaited<ReturnType<typeof sendTrace>>[] = [];
  const entities: Oracle[] = [];
  try {
    for (const [i, label] of ['alpha', 'beta'].entries()) {
      const rootName = `${prefix}-${label}`, childName = `${rootName}-child`;
      const sdk = (suffix: string) => mode === 'large'
        ? Array.from({ length: 80 }, (_, n) => `${prefix}-${suffix}-${String(n).padStart(2, '0')}-`.padEnd(200, 'x'))
        : [`${prefix}-${suffix}`];
      const rootSdk = sdk(`sdk-${label}`), childSdk = sdk(`sdk-${label}-child`);
      const time = start + BigInt(i) * 1_000_000_000n;
      const seeded = await sendTrace(req, {
        collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: prefix, rootName, childName,
        ...(mode === 'roots' && i === 1 ? { traceId: seeds[0].traceId } : {}),
        startTimeUnixNano: time, endTimeUnixNano: time + 50_000_000n,
        rootAttributes: { 'fi.span.kind': 'chain', 'tag.tags': rootSdk },
        childAttributes: { 'tag.tags': childSdk, 'gen_ai.usage.input_tokens': 11 + i,
          'gen_ai.usage.output_tokens': 3, 'gen_ai.cost.total': 0.25 },
      });
      seeds.push(seeded);
      for (const [index, sdkNames] of [rootSdk, childSdk].entries()) entities.push({
        traceId: seeded.traceId, spanId: seeded.spanIds[index], name: index === 0 ? rootName : childName,
        root: index === 0, sdk: sdkNames, manual: [], revision: 0, target: undefined as unknown as Target,
      });
    }
  } finally { await req.dispose(); }
  await attach(info, 'seeded-identities', { prefix, seeds, start: String(start), entities });
  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
    [prefix, actor.organizationId, actor.workspaceId]);
  expect(projects, 'A: actor-scoped project').toHaveLength(1);
  const projectId = projects[0].id;
  const traceIds = [...new Set(seeds.map(s => s.traceId))].sort();
  const sources = async () => ({
    spans: await probe.ch<Record<string, unknown>>(
      'SELECT * FROM spans FINAL WHERE project_id={p:UUID} ORDER BY trace_id,id', { p: projectId }),
    traces: await probe.ch<Record<string, unknown>>(
      'SELECT * FROM traces FINAL WHERE project_id={p:UUID} ORDER BY toString(id)', { p: projectId }),
  });
  await expect.poll(async () => {
    const current = await sources();
    return { spans: current.spans.map(s => s.id).sort(), traces: current.traces.map(t => t.id).sort() };
  }, POLL.SPAN_VISIBLE).toEqual({ spans: entities.map(e => e.spanId).sort(), traces: traceIds });
  const before = await sources();
  const identity = (s: Record<string, unknown>) => ({ id: s.id, trace_id: s.trace_id, parent_span_id: s.parent_span_id,
    project_id: s.project_id, org_id: s.org_id, service_name: s.service_name, observation_type: s.observation_type });
  expect(before.spans.map(identity), 'A: exact physical source identities').toEqual(entities.map(e => ({
    id: e.spanId, trace_id: e.traceId, parent_span_id: e.root ? '' : seeds.find(s => s.spanIds[1] === e.spanId)!.spanIds[0],
    project_id: projectId, org_id: actor.organizationId, service_name: prefix, observation_type: e.root ? 'chain' : 'llm',
  })).sort((a, b) => `${a.trace_id}/${a.id}`.localeCompare(`${b.trace_id}/${b.id}`)));
  const detail = (id: string) => actor.api.get<Detail>(tracePath(id), { project_id: projectId });
  for (const id of traceIds) {
    const value = await detail(id);
    const spans = flatten(value.result.observation_spans);
    expect(spans.map(s => s.id).sort()).toEqual(entities.filter(e => e.traceId === id).map(e => e.spanId).sort());
    for (const entity of entities.filter(e => e.traceId === id)) {
      const span = spans.find(s => s.id === entity.spanId)!;
      expect([...span.source_tag_names].sort(), 'A: exact independently seeded SDK tags').toEqual([...entity.sdk].sort());
      expect(span.tag_target).toMatchObject({ project_id: projectId, trace_id: id, span_id: entity.spanId,
        service_name: prefix, observation_type: entity.root ? 'chain' : 'llm' });
      entity.target = span.tag_target;
    }
  }
  const assignments = () => probe.pg<Record<string, unknown>>(
    `SELECT project_id,entity_kind,trace_id,span_id,start_hour,observation_type,service_name,manual_tags,revision::int
     FROM tracer_entity_tag_assignment WHERE project_id=$1 ORDER BY entity_kind,trace_id,span_id`, [projectId]);
  expect(await assignments()).toEqual([]);
  if (mode === 'large') {
    for (const entity of entities) {
      entity.manual = [tag(`${entity.name}-existing`)];
      // Supported API setup only. Wire pinned from EntityTagsUpdateSerializer.
      const body = { ...entity.target, expected_tag_revision: 0, tags: entity.manual };
      const response = await actor.api.post<{ result: Tagged }>(SPAN_TAGS, body);
      entity.revision = 1;
      expect(response.result.manual_tags).toEqual(entity.manual);
      expect(response.result.tag_revision).toBe(1);
      expect(entity.sdk).toHaveLength(80);
      expect(entity.sdk.every(s => s.length === 200)).toBe(true);
      expect(Buffer.byteLength(JSON.stringify(response.result.tags))).toBeGreaterThan(CELL_LIMIT);
      // Source metadata stays complete under the same bound (16,241 bytes).
      expect(Buffer.byteLength(JSON.stringify(entity.sdk))).toBeLessThan(CELL_LIMIT);
      await attach(info, `seed-manual-${entity.spanId}`, { body, response, effectiveBytes: Buffer.byteLength(JSON.stringify(response.result.tags)) });
    }
  }
  const oracleFor = (target: Target) => {
    const found = entities.find(e => e.spanId === target.span_id && e.traceId === target.trace_id);
    expect(found, 'physical target belongs to seeded oracle').toBeDefined();
    return found!;
  };
  const checkTags = (value: Tagged, entity: Oracle) => {
    expect([...value.source_tag_names].sort(), 'C: selected physical source tags').toEqual([...entity.sdk].sort());
    expect(sortedTags(value.manual_tags), 'C: exact manual names and rich colors').toEqual(sortedTags(entity.manual));
    expect(value.tag_revision, 'C: exact acknowledged revision').toBe(entity.revision);
    expect(value.tag_assignment_exists, 'C: explicit assignment presence, never inferred from the display preview').toBe(entity.revision > 0);
    expect(names(value.tags), 'C: exact effective union').toEqual([...entity.sdk, ...entity.manual.map(t => t.name)].sort());
    for (const manual of entity.manual) expect(value.tags.find(t => t.name === manual.name)).toEqual(manual);
    expect(value.tag_target).toEqual(entity.target);
  };
  const checkAssignments = async () => {
    const unique = entities.filter((e, i) => e.revision > 0 && (!e.root || !entities.slice(0, i).some(p => p.root && p.traceId === e.traceId)));
    const expected = unique.map(e => ({ project_id: projectId, entity_kind: e.root ? 'trace' : 'span', trace_id: e.traceId,
      span_id: e.root ? '' : e.spanId, start_hour: e.root ? new Date(0) : new Date(e.target.start_hour),
      observation_type: e.root ? '' : 'llm', service_name: e.root ? '' : prefix, manual_tags: e.manual, revision: e.revision,
    })).sort((a, b) => `${a.entity_kind}/${a.trace_id}/${a.span_id}`.localeCompare(`${b.entity_kind}/${b.trace_id}/${b.span_id}`));
    const actual = await assignments();
    await attach(info, `assignments-${entities.map(e => e.revision).join('-')}`, actual);
    expect(actual, 'D: only intended assignments change; bulk preserves each target').toEqual(expected);
  };
  const browserLists = new Map<string, { method: string; params: Record<string, string> }>();
  page.on('request', r => {
    const path = new URL(r.url()).pathname;
    if ([TRACE_LIST, SPAN_LIST].includes(path) && ['GET', 'POST'].includes(r.method()))
      browserLists.set(path, { method: r.method(), params: paramsOf(r) });
  });
  const readback = async (label: string) => {
    const lists: Record<string, ListRow[]> = {};
    for (const path of [TRACE_LIST, SPAN_LIST]) {
      const captured = browserLists.get(path);
      const params = captured?.params ?? { project_id: projectId, page_number: 0, page_size: 25, filters: '[]' };
      const result = captured?.method === 'POST'
        ? await actor.api.post<ListResult>(path, params) : await actor.api.get<ListResult>(path, params);
      lists[path] = result.result.table;
      expect(result.result.table.map(r => path === TRACE_LIST ? r.trace_id : r.span_id).sort(), 'C: exact fresh list identities')
        .toEqual(path === TRACE_LIST ? traceIds : entities.map(e => e.spanId).sort());
      for (const row of result.result.table) checkTags(row, oracleFor(row.tag_target));
    }
    const details = [];
    for (const id of traceIds) {
      const value = await detail(id);
      checkTags(value.result.trace, oracleFor(value.result.trace.tag_target));
      const spans = flatten(value.result.observation_spans);
      expect(spans.map(s => s.id).sort()).toEqual(entities.filter(e => e.traceId === id).map(e => e.spanId).sort());
      spans.forEach(s => checkTags(s, oracleFor(s.tag_target)));
      details.push(value);
    }
    await attach(info, `server-readback-${label}`, { lists, details, browserQueries: Object.fromEntries(browserLists) });
    await checkAssignments();
    return lists;
  };
  const acknowledge = (entity: Oracle, manual: Tag[]) => {
    const revision = entity.revision + 1;
    for (const other of entities.filter(e => e === entity || (entity.root && e.root && e.traceId === entity.traceId))) {
      other.manual = [...manual]; other.revision = revision;
    }
  };
  const checkSave = async (response: Response, entity: Oracle, manual: Tag[], oldRevision = entity.revision) => {
    const body = response.request().postDataJSON();
    const result = await response.json();
    // Capture an independent committed read even when the payload assertion is
    // about to fail on the baseline: a response body alone is not persistence.
    const stored = await detail(entity.traceId);
    const persisted = flatten(stored.result.observation_spans).find(span => span.id === entity.spanId);
    await attach(info, `browser-save-${entity.spanId}-${oldRevision + 1}`, {
      body, status: response.status(), response: result, persisted, assignments: await assignments(),
    });
    expect(response.status()).toBe(200);
    expect(body, 'B: physical target, current revision and complete manual replacement').toEqual({
      ...entity.target, expected_tag_revision: oldRevision, tags: manual,
    });
    expect(result.result.manual_tags).toEqual(manual);
    expect(result.result.tag_revision).toBe(oldRevision + 1);
    expect(result.result.tag_assignment_exists).toBe(true);
    expect([...result.result.source_tag_names].sort()).toEqual([...entity.sdk].sort());
    expect(names(result.result.tags)).toEqual([...entity.sdk, ...manual.map(t => t.name)].sort());
  };
  const waitSave = (entity: Oracle) => page.waitForResponse(r => mutation(r.request())
    && r.request().postDataJSON().span_id === entity.spanId, { timeout: UI_READY });
  const conserve = async () => {
    const after = await sources();
    await attach(info, 'source-conservation', { before: checksum(before), after: checksum(after), sourceBefore: before, sourceAfter: after });
    expect(checksum(after), 'E: source versions, tags, attributes and usage are unchanged').toBe(checksum(before));
    const usage = await probe.ch<{ tokens: string; cost: number }>(
      'SELECT toString(sum(total_tokens)) AS tokens, sum(cost) AS cost FROM spans FINAL WHERE project_id={p:UUID}', { p: projectId });
    expect(usage).toEqual([{ tokens: '29', cost: 0.5 }]);
  };
  await readback('seed');
  return { prefix, projectId, traceIds, entities, detail, readback, acknowledge, checkSave, waitSave, conserve, sources };
}

// DisplayPanel / ColumnVisibilityDropdown. Actual controls, not AG Grid API injection.
async function showTagColumns(page: Page, kind: 'trace' | 'span') {
  await page.locator('#observe-toolbar-slot').getByRole('button', { name: 'Display', exact: true }).click();
  await page.getByText('View columns', { exact: true }).click();
  const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Search', { exact: true }) });
  const all = popup.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
  await all.check(); await all.uncheck();
  for (const name of [kind === 'trace' ? 'Trace Name' : 'Span Name', 'Tags'])
    await popup.getByText(name, { exact: true }).locator('..').getByRole('checkbox').check();
  await expect(popup.getByRole('checkbox', { checked: true })).toHaveCount(2, { timeout: UI_READY });
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
  await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI_READY });
}
const drawer = (page: Page) => page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByRole('region', { name: 'Trace Tree', exact: true }) });
// SpanDetailPane.jsx InlineTagsRow: tag button's parent holds TagChip children;
// each TagChip owns exactly one direct span (its name). Excludes tree/header text.
const chipRow = (page: Page) => drawer(page).getByRole('button', { name: 'tag', exact: true }).locator('..');
async function expectChips(page: Page, entity: Oracle) {
  await expect(drawer(page).getByText(entity.spanId, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
  await expect.poll(async () => (await chipRow(page).locator(':scope > div > span').allTextContents()).sort(), { timeout: UI_READY })
    .toEqual([...entity.sdk, ...entity.manual.map(t => t.name)].sort());
  for (const source of entity.sdk)
    await expect(chipRow(page).getByText(source, { exact: true }).locator('..').getByRole('button')).toHaveCount(0, { timeout: UI_READY });
}
async function selectSpan(page: Page, entity: Oracle) {
  // TraceDetailDrawerV2 handleSelectSpan toggles an already-selected span off.
  // Opening a trace preselects its root after detail loads; wait for that pane
  // before deciding whether a selection change is needed.
  await expect(drawer(page).getByRole('button', { name: 'tag', exact: true })).toBeVisible({ timeout: UI_READY });
  if (!(await drawer(page).getByText(entity.spanId, { exact: true }).first().isVisible()))
    await drawer(page).getByRole('region', { name: 'Trace Tree', exact: true }).getByText(entity.name, { exact: true }).click();
  await expectChips(page, entity);
}
async function addInDrawer(page: Page, value: string) {
  await drawer(page).getByRole('button', { name: 'tag', exact: true }).click();
  const input = drawer(page).getByPlaceholder('tag name', { exact: true });
  await input.fill(value); await input.press('Enter');
}

for (const kind of ['trace', 'span'] as const) {
  const id = kind === 'trace' ? 'OBS-E2E-065' : 'OBS-E2E-067';
  test(`${id}: oversized ${kind} tags survive grid, drawer and bulk additions`, {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id, area: 'observe',
      userGoal: `A developer adds labels to heavily tagged ${kind === 'trace' ? 'traces' : 'child spans'} individually and in bulk without losing existing manual or SDK labels or changing other entities`,
      steps: [
        'ingest two two-span traces with 80 distinct 200-character SDK tags per physical span and seed distinct rich manual labels through the supported API',
        `open the real bounded ${kind} grid and add a label to alpha`,
        'reload and verify exact list/detail and assignment readback',
        `open alpha’s ${kind === 'trace' ? 'root' : 'child'} in the drawer and add another label`,
        'reload to traverse row bounding again, select exactly alpha and beta, and use Actions > Add tags',
        'reload and reopen both targets to verify each retained its own original labels plus the intended additions',
        ...(kind === 'span' ? ['hold a real child save acknowledgment, ingest a newer version of the same source, reload the grid, then release the old acknowledgment and save again without losing the newer SDK tag or target'] : []),
      ],
      backendChecks: [
        'A: exact physical source identities belong to the actor project and expose the independently seeded SDK sets',
        'B: each browser save sends the intended physical target, current revision and complete manual replacement, including each bulk target’s distinct labels',
        'C: fresh trace/span lists, replayed browser queries and detail reads return exact source/manual/effective tags, rich colors and revisions',
        'D: only intended PostgreSQL assignments change and each bulk target preserves its own manual tags',
        'E: ClickHouse source rows, versions, tags, attributes and nonzero usage remain unchanged through grid, drawer and bulk additions',
        ...(kind === 'span' ? ['F: after deliberate source replay, a delayed older-source acknowledgment preserves the loaded SDK tag and subsequent write target; unrelated rows and the refreshed sources remain unchanged'] : []),
      ],
    }),
  }, async ({ page, actor, probe }, info) => {
    test.setTimeout(kind === 'span' ? 780_000 : 540_000); // Span adds 15s replay + 3 × 60s UI + 45s readback/cleanup.
    page.setDefaultTimeout(UI_READY);
    const s = await seedScenario(page, actor, probe, info, 'large');
    const [alpha, beta] = s.entities.filter(e => e.root === (kind === 'trace'));
    const column = kind === 'trace' ? 'trace_name' : 'span_name';
    const url = `/dashboard/observe/${s.projectId}/llm-tracing?selectedTab=${kind === 'trace' ? 'trace' : 'spans'}`;
    const gridNames = page.locator(`.clean-data-table:visible .ag-row [col-id="${column}"]`);
    // spanPhysicalIdentity.js: the real row key includes project, trace, id, time.
    const tuple = (r: ListRow) => JSON.stringify([r.project_id, r.trace_id, r.span_id, r.start_time]);
    const rowId = (e: Oracle, rows: ListRow[]) => kind === 'trace' ? e.traceId : tuple(
      rows.find(r => r.project_id === s.projectId && r.trace_id === e.traceId && r.span_id === e.spanId)!,
    );
    let rows = (await s.readback('before-browser'))[kind === 'trace' ? TRACE_LIST : SPAN_LIST];
    const browserSpanResponses: Response[] = [];
    page.on('response', response => {
      if (new URL(response.url()).pathname === SPAN_LIST
        && ['GET', 'POST'].includes(response.request().method())) browserSpanResponses.push(response);
    });
    // AG Grid 35 RowCtrl uses _escapeString before setting the DOM attribute:
    // getAttribute('row-id') contains literal &quot;, not JSON quote characters.
    const row = (e: Oracle) => page.locator(`.clean-data-table:visible .ag-row[row-id=${JSON.stringify(rowId(e, rows).replaceAll('"', '&quot;'))}]`);
    const gridReady = async () => {
      const expected = s.entities.filter(e => kind === 'span' || e.root).map(e => e.name).sort();
      await expect.poll(async () => (await gridNames.allTextContents()).sort(), { timeout: UI_READY }).toEqual(expected);
      if (kind === 'span') {
        const response = browserSpanResponses.at(-1)!;
        expect(response, 'the rendered grid has a real browser list response').toBeDefined();
        expect(response.status()).toBe(200);
        rows = ((await response.json()) as ListResult).result.table;
        // Keep independent seeded IDs and exact microsecond timestamps; never
        // derive expected identity from the DOM or reduce it to a bare span ID.
        const expectedTuples = s.entities.map(e => JSON.stringify([
          s.projectId, e.traceId, e.spanId, e.target.expected_start_time.replace(/\+00:00$/, 'Z'),
        ])).sort();
        expect(rows.map(tuple).sort(), 'browser wire has the exact seeded physical tuples').toEqual(expectedTuples);
        const rendered = await page.locator('.clean-data-table:visible .ag-row').evaluateAll(elements =>
          elements.map(element => element.getAttribute('row-id')!).sort());
        expect(rendered, 'every DOM row retains its full wire identity').toEqual(
          expectedTuples.map(id => id.replaceAll('"', '&quot;')).sort(),
        );
        await attach(info, `span-grid-identities-${browserSpanResponses.length}`, {
          query: paramsOf(response.request()), wire: rows.map(tuple).sort(), rendered,
        });
      }
    };
    await test.step('1: navigate through the real bounded grid', async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' }); await gridReady(); await showTagColumns(page, kind);
      // A source array fits but its rich effective tag objects exceed 16 KiB.
      // This passive empty preview proves the actual boundObserveListRow path ran.
      await expect(row(alpha).locator('[col-id="tags"]').getByRole('button', { name: 'Add tags', exact: true }))
        .toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    const gridTag = tag(`${s.prefix}-grid-added`), drawerTag = tag(`${s.prefix}-drawer-added`), bulkTag = tag(`${s.prefix}-bulk-added`);
    await test.step('2: add through the grid without dropping the existing assignment', async () => {
      await row(alpha).locator('[col-id="tags"]').getByRole('button', { name: /^(Add|Edit) tags$/ }).click();
      const input = page.getByPlaceholder('Add tag...', { exact: true });
      await input.fill(gridTag.name);
      const saved = s.waitSave(alpha); await input.press('Enter');
      const expected = [...alpha.manual, gridTag];
      await s.checkSave(await saved, alpha, expected); s.acknowledge(alpha, expected);
      await page.keyboard.press('Escape');
    }, { timeout: UI_READY });
    await test.step('3: reload and independently verify list, detail and assignment state', async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' }); await gridReady(); await showTagColumns(page, kind);
      await s.readback('after-grid');
    }, { timeout: UI_READY });
    await test.step('4: add through the selected physical entity in the drawer', async () => {
      await row(alpha).locator(`[col-id="${column}"]`).click(); await selectSpan(page, alpha);
      const saved = s.waitSave(alpha); await addInDrawer(page, drawerTag.name);
      const expected = [...alpha.manual, drawerTag];
      await s.checkSave(await saved, alpha, expected); s.acknowledge(alpha, expected);
      await expectChips(page, alpha); await s.readback('after-drawer');
    }, { timeout: UI_READY });
    await test.step('5: reload and select exactly the two intended bounded rows', async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' }); await gridReady(); await showTagColumns(page, kind);
      for (const entity of [alpha, beta]) await row(entity).getByRole('checkbox').check();
      await expect(page.getByText('2 selected', { exact: true })).toBeVisible({ timeout: UI_READY });
      await page.getByRole('button', { name: 'Actions', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Add tags', exact: true }).click();
      await expect(page.getByText('Add tags to 2 items', { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('6: bulk-add while preserving each target’s distinct existing tags', async () => {
      const saved = [s.waitSave(alpha), s.waitSave(beta)];
      const input = page.getByPlaceholder('Add tag...', { exact: true });
      await input.fill(bulkTag.name); await input.press('Enter');
      const responses = await Promise.all(saved);
      for (const [i, entity] of [alpha, beta].entries()) {
        const expected = [...entity.manual, bulkTag];
        await s.checkSave(responses[i], entity, expected); s.acknowledge(entity, expected);
      }
      await page.keyboard.press('Escape'); await s.readback('after-bulk');
    }, { timeout: UI_READY });
    for (const [i, entity] of [alpha, beta].entries()) await test.step(`${7 + i}: reload and inspect ${entity.name}`, async () => {
      await page.goto(url, { waitUntil: 'domcontentloaded' }); await gridReady(); await showTagColumns(page, kind);
      await row(entity).locator(`[col-id="${column}"]`).click(); await selectSpan(page, entity);
      await s.readback(`final-${i}`); await s.conserve();
      await info.attach(`final-${kind}-${i}`, { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
    if (kind === 'span') await test.step('9: retain a refreshed source across a delayed older-source acknowledgment', async () => {
      await refreshedSourceAcknowledgment(page, actor, info, s, alpha);
    }, { timeout: 240_000 });
  });
}

function barrier() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}
/** Hold unaltered server responses, not synthetic successes or rewritten JSON. */
async function holdResponses(page: Page, path: string, method: string) {
  const gate = barrier();
  const running: Promise<void>[] = [];
  let count = 0;
  const pattern = (url: URL) => url.pathname === path;
  const handler = async (route: Route) => {
    if (route.request().method() !== method) { await route.continue(); return; }
    const work = (async () => {
      const response = await route.fetch();
      count += 1;
      await gate.promise;
      await route.fulfill({ response });
    })();
    running.push(work); await work;
  };
  await page.route(pattern, handler);
  return {
    waitFetched: () => expect.poll(() => count, { timeout: UI_READY }).toBeGreaterThan(0),
    release: gate.resolve,
    // Removing an active handler continues its route in Playwright. Fulfill the
    // held real responses first so unroute cannot race a second handling.
    close: async () => { gate.resolve(); await Promise.all(running); await page.unroute(pattern, handler); },
  };
}

/** Real OTLP replay and unmodified responses: no injected source or assignment metadata. */
async function refreshedSourceAcknowledgment(
  page: Page, actor: TestActor, info: TestInfo,
  s: Awaited<ReturnType<typeof seedScenario>>, entity: Oracle,
) {
  const root = s.entities.find(e => e.root && e.traceId === entity.traceId)!;
  const beforeReplay = await s.sources();
  await page.goto(`/dashboard/observe/${s.projectId}/llm-tracing?selectedTab=spans`, { waitUntil: 'domcontentloaded' });
  await showTagColumns(page, 'span');
  const row = page.locator('.clean-data-table:visible .ag-row').filter({ has: page.locator('[col-id="span_name"]', { hasText: entity.name }) });
  await expect(row).toHaveCount(1, { timeout: UI_READY });
  const cell = row.locator('[col-id="tags"]');
  const originalTarget = { ...entity.target }, originalRevision = entity.revision;
  const first = tag(`${s.prefix}-version-held`), second = tag(`${s.prefix}-version-current`);
  const nextManual = [...entity.manual, first], sdkNext = `${s.prefix}-sdk-v2`;
  const ackGate = barrier(), ackFetched = barrier();
  const active: Promise<void>[] = [];
  let ackBody: { result: Tagged } | undefined;
  let ackRequest: unknown;
  const writePattern = (url: URL) => url.pathname === SPAN_TAGS;
  const writeHandler = async (route: Route) => {
    if (route.request().method() !== 'POST') { await route.continue(); return; }
    const work = (async () => {
      const response = await route.fetch();
      ackRequest = route.request().postDataJSON();
      ackBody = await response.json();
      expect(response.status()).toBe(200);
      ackFetched.resolve();
      await ackGate.promise; await route.fulfill({ response });
    })();
    active.push(work); await work;
  };
  await page.route(writePattern, writeHandler);
  let readHold: Awaited<ReturnType<typeof holdResponses>> | undefined;
  const req = await request.newContext();
  try {
    await cell.getByRole('button', { name: /^(Add|Edit) tags$/ }).click();
    const input = page.getByPlaceholder('Add tag...', { exact: true });
    await input.fill(first.name); await input.press('Enter'); await ackFetched.promise;
    expect(ackRequest).toEqual({ ...originalTarget, expected_tag_revision: originalRevision, tags: nextManual });
    expect(ackBody!.result.manual_tags).toEqual(nextManual);
    expect(ackBody!.result.tag_revision).toBe(originalRevision + 1);
    s.acknowledge(entity, nextManual);
    await page.keyboard.press('Escape');
    // sendTrace root/child timestamps are identical. The original fixture uses millisecond-aligned times.
    const start = BigInt(Date.parse(originalTarget.expected_start_time)) * 1_000_000n;
    await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: s.prefix, traceId: entity.traceId, rootSpanId: root.spanId, childSpanId: entity.spanId,
      rootName: root.name, childName: entity.name, startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n,
      rootAttributes: { 'fi.span.kind': 'chain', 'tag.tags': root.sdk },
      childAttributes: { 'tag.tags': [sdkNext], 'gen_ai.usage.input_tokens': 11,
        'gen_ai.usage.output_tokens': 3, 'gen_ai.cost.total': 0.25 },
    });
    await expect.poll(async () => flatten((await s.detail(entity.traceId)).result.observation_spans)
      .find(span => span.id === entity.spanId)?.source_tag_names, POLL.SPAN_VISIBLE).toEqual([sdkNext]);
    entity.sdk = [sdkNext];
    const detail = await s.detail(entity.traceId);
    for (const changed of [root, entity]) changed.target = flatten(detail.result.observation_spans)
      .find(span => span.id === changed.spanId)!.tag_target;
    const refreshed = page.waitForResponse(r => new URL(r.url()).pathname === SPAN_LIST && r.status() === 200, { timeout: UI_READY });
    // ObserveHeader.jsx: native refresh keeps the mounted grid and in-flight save handler alive.
    await page.getByRole('button', { name: /^Reload/ }).click();
    const response = await refreshed;
    const fresh = ((await response.json()) as ListResult).result.table.find(r => r.span_id === entity.spanId)!;
    expect(fresh.source_tag_names).toEqual([sdkNext]);
    expect(fresh.tag_revision).toBe(originalRevision + 1);
    expect(fresh.tag_target).toEqual(entity.target);
    expect(fresh.tag_target.expected_version).not.toBe(originalTarget.expected_version);
    await expect(cell.getByText(sdkNext, { exact: true })).toBeVisible({ timeout: UI_READY });
    const identities = () => page.locator('.clean-data-table:visible .ag-row').evaluateAll(rows => rows.map(r => r.getAttribute('row-id')).sort());
    const scroll = () => page.locator('.clean-data-table:visible .ag-body-viewport').evaluate(e => e.scrollTop);
    const beforeDelivery = { identities: await identities(), scroll: await scroll(), sources: await s.sources() };
    expect(beforeDelivery.sources.spans.filter(r => r.trace_id !== entity.traceId)).toEqual(beforeReplay.spans.filter(r => r.trace_id !== entity.traceId));
    // Hold actual post-save list responses so a refetch cannot conceal bad acknowledgment reconciliation.
    readHold = await holdResponses(page, SPAN_LIST, response.request().method());
    const delivered = s.waitSave(entity); ackGate.resolve(); await delivered;
    await Promise.all(active); await page.unroute(writePattern, writeHandler);
    await attach(info, 'delayed-source-acknowledgment', { ackRequest, ackBody, fresh, beforeDelivery });
    await expect(cell.getByText(sdkNext, { exact: true }), 'F: newer SDK tag survives older-source acknowledgment')
      .toBeVisible({ timeout: UI_READY });
    expect(await identities(), 'F: physical row identities remain stable').toEqual(beforeDelivery.identities);
    expect(await scroll(), 'F: grid scroll remains stable').toBe(beforeDelivery.scroll);
    await info.attach('refreshed-source-after-delayed-ack', { body: await page.screenshot(), contentType: 'image/png' });
    // A second actual save proves retained target metadata, not just a correctly painted chip.
    await cell.getByRole('button', { name: /^(Add|Edit) tags$/ }).click();
    const nextInput = page.getByPlaceholder('Add tag...', { exact: true });
    await nextInput.fill(second.name); const saved = s.waitSave(entity); await nextInput.press('Enter');
    const replacement = [...entity.manual, second];
    await s.checkSave(await saved, entity, replacement); s.acknowledge(entity, replacement);
    expect(await s.sources(), 'F: acknowledgment and subsequent save do not mutate refreshed sources').toEqual(beforeDelivery.sources);
    await readHold.close(); await s.readback('refreshed-source-final');
  } finally {
    ackGate.resolve(); await Promise.all(active); await page.unroute(writePattern, writeHandler);
    await readHold?.close(); await req.dispose();
  }
}

test('OBS-E2E-068: rapid sibling-root edits share manual labels without sharing SDK tags', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-068', area: 'observe',
    userGoal: 'A developer switches rapidly between roots of one trace while labeling them and always sees each root’s own SDK tags with shared manual labels',
    steps: [
      'ingest two root trees under one trace ID with distinct root and child SDK tags',
      'open root A and save a label while delaying its unaltered real detail-refetch response',
      'switch A to B to A to B after acknowledgment and verify each selected root’s exact SDK/manual set',
      'attempt to add B’s SDK name, then add a B-authored manual label without promoting an SDK name to manual',
      'release refetch and repeat with the real A save response held until after selection switches to B',
      'remove a shared manual label through the real drawer editor, release responses and reload',
    ],
    backendChecks: [
      'A: exact physical source identities belong to the actor project and expose the independently seeded SDK sets',
      'B: each browser save sends the selected physical root, current shared revision and manual tags only in both acknowledgment/refetch orders',
      'C: fresh trace/span lists and detail reads preserve each root’s own SDK set with the exact shared manual tags, rich colors and revision',
      'D: PostgreSQL stores only the shared trace assignment, never either root’s SDK name or a child assignment',
      'E: ClickHouse source identities, versions, tags, attributes and nonzero usage are unchanged after addition and deletion',
    ],
  }),
}, async ({ page, actor, probe }, info) => {
  test.setTimeout(660_000); // 30s setup + 15s ingestion + 10 × 60s UI + 15s headroom.
  page.setDefaultTimeout(UI_READY);
  const s = await seedScenario(page, actor, probe, info, 'roots');
  const [a, b] = s.entities.filter(e => e.root);
  const url = `/dashboard/observe/${s.projectId}/llm-tracing?selectedTab=trace`;
  const open = async () => {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await page.locator(`.clean-data-table:visible .ag-row[row-id="${a.traceId}"] [col-id="trace_name"]`).click();
  };
  const writes: Request[] = [];
  page.on('request', r => { if (mutation(r)) writes.push(r); });
  await test.step('1: open the two-root trace and select A', async () => {
    await open(); await selectSpan(page, a);
  }, { timeout: UI_READY });
  for (const [round, beforeAck] of [false, true].entries()) {
    const refetch = await holdResponses(page, tracePath(a.traceId), 'GET');
    const heldSave = beforeAck ? await holdResponses(page, SPAN_TAGS, 'POST') : undefined;
    try {
      const fromA = tag(`${s.prefix}-a-${round}`), fromB = tag(`${s.prefix}-b-${round}`);
      await test.step(`${round + 2}: A save with ${beforeAck ? 'selection before acknowledgment' : 'acknowledgment before selection'}`, async () => {
        await selectSpan(page, a);
        const saved = s.waitSave(a);
        const next = [...a.manual, fromA];
        await addInDrawer(page, fromA.name);
        if (heldSave) {
          await heldSave.waitFetched();
          // Server committed, but this browser has not received the acknowledgment.
          await selectSpan(page, b);
          heldSave.release();
        }
        await s.checkSave(await saved, a, next); s.acknowledge(a, next);
        await refetch.waitFetched();
        if (heldSave) await heldSave.close();
      }, { timeout: UI_READY });
      await test.step(`${round + 4}: rapid A/B switches while invalidation remains held`, async () => {
        for (const entity of [b, a, b]) await selectSpan(page, entity);
        await s.readback(`race-${round}-after-a`);
      }, { timeout: UI_READY });
      await test.step(`${round + 6}: B SDK duplicate stays source-only and B adds a manual label`, async () => {
        await addInDrawer(page, b.sdk[0]);
        // Duplicate submission clears but keeps the real input mounted. Reuse it.
        const input = drawer(page).getByPlaceholder('tag name', { exact: true });
        await expect(input).toHaveValue('', { timeout: UI_READY });
        const saved = s.waitSave(b);
        await input.fill(fromB.name); await input.press('Enter');
        const next = [...b.manual, fromB];
        await s.checkSave(await saved, b, next); s.acknowledge(b, next);
        await expectChips(page, b);
        // Inspect every emitted write, including any illicit duplicate submission.
        for (const request of writes) {
          const body = request.postDataJSON();
          expect(body.tags.map((t: Tag) => t.name).filter((name: string) => [...a.sdk, ...b.sdk].includes(name)),
            'B: SDK names never enter a manual replacement').toEqual([]);
        }
        await s.readback(`race-${round}-after-b`);
      }, { timeout: UI_READY });
      await test.step(`${round + 8}: release real refetch and retain selected B identity`, async () => {
        await refetch.close(); await expectChips(page, b); await s.readback(`race-${round}-released`);
      }, { timeout: UI_READY });
    } finally {
      await heldSave?.close(); await refetch.close();
    }
  }
  await test.step('10: remove a shared manual label, reload and conserve both SDK sets', async () => {
    const removed = b.manual[0], next = b.manual.slice(1);
    const saved = s.waitSave(b);
    await chipRow(page).getByText(removed.name, { exact: true }).locator('..').getByRole('button').click();
    await s.checkSave(await saved, b, next); s.acknowledge(b, next);
    await expectChips(page, b); await selectSpan(page, a);
    await open(); await selectSpan(page, b); await selectSpan(page, a);
    await s.readback('final'); await s.conserve();
    await attach(info, 'all-browser-write-payloads', writes.map(r => r.postDataJSON()));
    await info.attach('final-sibling-roots', { body: await page.screenshot(), contentType: 'image/png' });
  }, { timeout: UI_READY });
});
