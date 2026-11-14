import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import type { Response } from '@playwright/test';
import { catalogLifecycleTest as test, reachedOffsetBarrier, type Selection } from '../../lib/catalog-lifecycle';
import { expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { sendTrace, type SeededTrace, type SendTraceConfig } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

const LIST = '/tracer/trace/list_traces_of_session/';
const VALUES = '/tracer/dashboard/filter_values/';
const METRICS = '/tracer/dashboard/metrics/';
const READY = { timeout: 60_000, intervals: [500, 1000, 2000] };
const sorted = <T>(rows: T[]) => [...rows].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
type Fact = { label: string; cfg: SendTraceConfig; seeded: SeededTrace };
type Observation = { value: string | number; at: bigint };
type Tuple = { attribute_type: string; value_json?: string; value_fingerprint?: string; first_us: string; last_us: string };
type Source = { id: string; trace_id: string; project_id: string; org_id: string; service_name: string;
  observation_type: string; parent_span_id: string; name: string; start_us: string; end_us: string;
  attrs_string: Record<string, string>; attrs_number: Record<string, number>; attrs_bool: Record<string, number>;
  extra: string; version: string; is_deleted: number };
type Filter = { column_id: string; filter_config: { filter_type: string; filter_op: string;
  filter_value: unknown[]; attribute_value_types?: string[] } };
type Query = { project_id: string; filters: string; cursor?: string; cursor_mode: boolean; [key: string]: unknown };
type ListBody = { result: { table: { trace_id: string }[]; metadata: { query_complete: boolean;
  query_status: string; query_error_code?: string | null; total_rows_is_lower_bound: boolean;
  total_rows: number; has_more: boolean; next_cursor: string | null } } };

// Input-event oracle, not values copied from catalog/API results. Replay is
// idempotent; an update adds an observation without erasing the old suggestion.
function tuples(events: Observation[], values: boolean): Tuple[] {
  const result = new Map<string, Tuple>();
  for (const { value, at } of events) {
    const identity: Omit<Tuple, 'first_us' | 'last_us'> = { attribute_type: typeof value };
    if (values) {
      identity.value_json = JSON.stringify(value);
      identity.value_fingerprint = createHash('sha256').update(
        'futureagi.span-attribute-catalog.scalar.v1\0' + typeof value + '\0' + identity.value_json).digest('hex');
    }
    const key = JSON.stringify(identity), old = result.get(key), seen = at / 1000n;
    result.set(key, { ...identity,
      first_us: old && BigInt(old.first_us) < seen ? old.first_us : String(seen),
      last_us: old && BigInt(old.last_us) > seen ? old.last_us : String(seen) });
  }
  return sorted([...result.values()]);
}

test.describe('local historical catalog qualification', () => {
  // A declaration-time skip prevents the auto worker fixture (including actor,
  // browser and Docker preflight) from running in ordinary CI. That is NOT a pass.
  test.skip(process.env.E2E_H5_LIVE !== '1',
    'LOCAL-ONLY NOT RUN: requires coordinated H5 opt-in and external pins; ordinary CI does not qualify OBS010');
  test('OBS-E2E-010: historical and live suggestions retain history while filters use current facts', {
    tag: ['@flow', '@h5-local'],
    annotation: flowAnnotation({ id: 'OBS-E2E-010', area: 'observe',
      userGoal: 'Discover repaired historical and live values, then distinguish a stale suggestion from current trace matches',
      steps: ['ingest bounded historical source-only OTLP facts and preview without writes',
        'resume repair alongside live ingestion and replay stable historical identities',
        'select historical, live and numeric suggestions', 'update the historical trace through OTLP',
        'select the retained stale suggestion and see zero current matches',
        'select its replacement and refresh to confirm the exact trace'],
      backendChecks: ['SELECT-only source identity, stable full sorting keys and event times with newer converter versions',
        'preview has zero writes; bounded apply, resume and overlap converge through the live catalog consumer',
        'grouped catalog tuples and typed API suggestions equal an independent input-event oracle',
        'consumed-offset barrier precedes zero matches; real UI requests and complete API cursor walks return exact seeded IDs'],
    }),
  }, async ({ actor, page, request, probe, lifecycle: h5 }, testInfo) => {
    // 30 readiness + 15 source + 60 preview + 180 repair/replay + 120 initial UI
    // + 15 update source + 60 catalog/barrier + 120 final UI + 60 retain/headroom.
    test.setTimeout(660_000);
    page.setDefaultTimeout(60_000);
    const key = `e2e-obs10-${testInfo.workerIndex}-${Date.now().toString(36)}.customer`;
    const start = BigInt(Math.floor((Date.now() - 2 * 86_400_000) / 3_600_000) * 3_600_000 + 600_000) * 1_000_000n;
    const scope: Selection = { organization: actor.organizationId, workspace: actor.workspaceId, project: '',
      since: new Date(Number(start / 1_000_000n)).toISOString(),
      until: new Date(Number(start / 1_000_000n) + 3000).toISOString() };
    const facts: Fact[] = [], events: Observation[] = [], evidence: unknown[] = [];
    let outcome = 'running', failure: string | undefined, baseline: Source[] = [];
    let historical: Fact, numeric: Fact, live: Fact;
    let updateBarrier: { barrier: Awaited<ReturnType<typeof h5.offsets>>; latest: Awaited<ReturnType<typeof h5.offsets>> } | undefined;
    const attach = async (name: string, body: unknown) => {
      const path = testInfo.outputPath(name + '.json');
      await mkdir(dirname(path), { recursive: true });
      await writeFile(path, JSON.stringify(body, null, 2), { mode: 0o600, flag: 'wx' });
      await testInfo.attach(name, { contentType: 'application/json', path });
    };
    const source = () => h5.sourceSelect<Source>(
      'SELECT id, trace_id, project_id, org_id, service_name, observation_type, parent_span_id, name,'
      + ' toString(toUnixTimestamp64Micro(start_time)) AS start_us, toString(toUnixTimestamp64Micro(end_time)) AS end_us,'
      + ' attrs_string, attrs_number, attrs_bool, toString(attributes_extra) AS extra, toString(_version) AS version, is_deleted'
      + ' FROM spans FINAL WHERE service_name={n:String} ORDER BY id', { n: h5.runId });
    const seed = async (url: string, label: string, at: bigint, value: string | number) => {
      const cfg: SendTraceConfig = { collectorUrl: url, apiKey: actor.apiKey, secretKey: actor.secretKey,
        projectName: h5.runId, rootName: `${h5.runId}.${label}`, childName: `${h5.runId}.${label}.child`,
        startTimeUnixNano: at, endTimeUnixNano: at + 50_000_000n, rootAttributes: { [key]: value } };
      const fact = { label, cfg, seeded: await sendTrace(request, cfg) };
      facts.push(fact);
      await attach('seed-' + label, { label, ...fact.seeded, value, startNs: String(at), endNs: String(at + 50_000_000n) });
      return fact;
    };
    const sourceReady = async () => {
      await expect.poll(async () => (await source()).map(r => r.id).sort(), POLL.SPAN_VISIBLE)
        .toStrictEqual(facts.flatMap(f => f.seeded.spanIds).sort());
      const rows = await source();
      for (const f of facts) for (const [i, id] of f.seeded.spanIds.entries()) {
        const row = rows.find(r => r.id === id)!;
        expect([row.trace_id, row.org_id, row.service_name, row.parent_span_id, row.observation_type,
          row.name, row.start_us, row.end_us, row.is_deleted]).toStrictEqual([
          f.seeded.traceId, actor.organizationId, h5.runId, i ? f.seeded.spanIds[0] : '', i ? 'llm' : 'unknown',
          i ? f.cfg.childName : f.cfg.rootName, String(BigInt(f.cfg.startTimeUnixNano!) / 1000n),
          String(BigInt(f.cfg.endTimeUnixNano!) / 1000n), 0]);
        const value = f.cfg.rootAttributes![key];
        expect(row.attrs_string[key]).toBe(!i && typeof value === 'string' ? value : undefined);
        expect(row.attrs_number[key]).toBe(!i && typeof value === 'number' ? value : undefined);
        if (scope.project) expect(row.project_id).toBe(scope.project);
        expect(BigInt(row.version)).toBeGreaterThan(0n);
      }
      return rows;
    };
    const index = (values: boolean) => probe.ch<Tuple>('SELECT attribute_type,'
      + (values ? ' value_json, value_fingerprint,' : '')
      + ' toString(toUnixTimestamp64Micro(min(first_seen))) AS first_us,'
      + ' toString(toUnixTimestamp64Micro(max(last_seen))) AS last_us FROM property_catalog.'
      + (values ? 'observed_attribute_values' : 'observed_attribute_keys')
      + ' WHERE organization_id={o:String} AND workspace_id={w:String} AND project_id={p:String}'
      + ' AND source_kind=\'custom_attribute\' AND attribute_key={k:String}'
      + ' GROUP BY attribute_type' + (values ? ', value_json, value_fingerprint' : '')
      + ' SETTINGS readonly=1, max_execution_time=10, max_threads=1',
    { o: scope.organization, w: scope.workspace, p: scope.project, k: key }).then(sorted);
    const catalog = async () => {
      const values = (await actor.api.post<{ result: { values: { value: unknown; type: string }[];
        has_more: boolean; next_cursor: string | null } }>(VALUES,
      { property_id: 'custom_attribute:' + key, source: 'traces', project_ids: scope.project, page_size: 25 })).result;
      expect([values.has_more, values.next_cursor]).toStrictEqual([false, null]);
      return sorted(values.values.map(({ value, type }) => ({ value, type })));
    };
    const metrics = async () => (await actor.api.post<{ result: { metrics: { property_id: string }[] } }>(METRICS,
      { source: 'traces', category: 'custom_attribute', project_ids: scope.project,
        search: key, cursor_mode: true, page_size: 25 })).result.metrics.map(m => m.property_id);
    const drain = async (phase: string) => {
      const barrier = await h5.offsets();
      let latest = barrier;
      await expect.poll(async () => {
        latest = await h5.offsets();
        return reachedOffsetBarrier(barrier, latest);
      }, { timeout: 60_000, intervals: [1000, 2000, 5000] }).toBe(true);
      const receipt = { phase, barrier, latest };
      evidence.push(receipt);
      return receipt;
    };
    const verifyCatalog = async () => {
      for (const values of [false, true])
        await expect.poll(() => index(values), READY).toStrictEqual(tuples(events, values));
      await expect.poll(catalog, READY).toStrictEqual(sorted([...new Map(events.map(({ value }) =>
        [JSON.stringify(value), { value, type: typeof value }])).values()]));
      expect(await metrics()).toStrictEqual(['custom_attribute:' + key]);
      evidence.push({ phase: 'catalog', keys: await index(false), values: await index(true), api: await catalog() });
    };
    const finish = async (role: 'primary' | 'overlap') => {
      for (let i = 0; i < 3; i++) {
        const result = await h5.backfill(role);
        if (result.progress!.scan_complete) return result.progress!;
      }
      throw new Error('OBS010 bounded repair incomplete; retain receipts');
    };
    const replay = async (value: string) => {
      await h5.recheck();
      const before = await source();
      // converter.go versions with current time, but preserves wire event time.
      // RMT sorting key: project, kind, service, hour(start), trace, span. Do not
      // manufacture SQL versions or move partitions to simulate an update.
      expect(await sendTrace(request, { ...historical.cfg, collectorUrl: E2E.collectorUrl,
        traceId: historical.seeded.traceId, rootSpanId: historical.seeded.spanIds[0],
        childSpanId: historical.seeded.spanIds[1], rootAttributes: { [key]: value } })).toStrictEqual(historical.seeded);
      await expect.poll(async () => {
        const rows = await source();
        return historical.seeded.spanIds.map(id => {
          const row = rows.find(r => r.id === id);
          return !!row && BigInt(row.version) > BigInt(before.find(r => r.id === id)!.version);
        });
      }, POLL.SPAN_VISIBLE).toStrictEqual([true, true]);
      const after = await source();
      expect(after.map(({ version, ...row }) => row)).toStrictEqual(before.map(({ version, ...row }) =>
        row.id === historical.seeded.spanIds[0] ? { ...row, attrs_string: { ...row.attrs_string, [key]: value } } : row));
      expect(after.filter(r => !historical.seeded.spanIds.includes(r.id)))
        .toStrictEqual(before.filter(r => !historical.seeded.spanIds.includes(r.id)));
      historical.cfg.rootAttributes = { [key]: value };
      await sourceReady();
      evidence.push({ phase: 'OTLP replay ' + value, before, after });
    };

    const names = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
    let hasFilter = false;
    const checkQuery = (q: Query, value: string | number) => {
      expect(q.project_id).toBe(scope.project);
      expect(q.cursor_mode).toBe(true);
      const filters = JSON.parse(q.filters) as Filter[];
      expect(filters.filter(f => f.column_id === key)).toStrictEqual([{ column_id: key,
        display_name: key, property_id: 'custom_attribute:' + key,
        filter_config: { col_type: 'SPAN_ATTRIBUTE', filter_type: 'text', filter_op: 'in',
          filter_value: [value], attribute_value_types: [typeof value] } }]);
      const date = filters.find(f => f.column_id === 'created_at')!.filter_config;
      expect([date.filter_type, date.filter_op]).toStrictEqual(['datetime', 'between']);
      expect(date.filter_value).toHaveLength(2);
      expect(date.filter_value.every(v => typeof v === 'string')).toBe(true);
      const [from, to] = (date.filter_value as string[]).map(Date.parse);
      expect(Number.isFinite(from) && Number.isFinite(to)).toBe(true);
      expect(from).toBeLessThanOrEqual(Number(start / 1_000_000n));
      expect(to).toBeGreaterThanOrEqual(Number(BigInt(live.cfg.startTimeUnixNano!) / 1_000_000n));
      expect(to - from).toBeLessThanOrEqual(8 * 86_400_000);
    };
    const checkPage = (body: ListBody) => {
      const m = body.result.metadata;
      expect([m.query_complete, m.query_status, m.total_rows_is_lower_bound]).toStrictEqual([true, 'complete', false]);
      expect(m.query_error_code).toBe(null);
      expect(typeof m.has_more).toBe('boolean');
      expect(Number.isSafeInteger(m.total_rows) && m.total_rows >= 0).toBe(true);
      if (!m.has_more) expect(m.next_cursor).toBe(null);
      else expect(typeof m.next_cursor === 'string' && m.next_cursor.length > 0).toBe(true);
      return m;
    };
    const select = async (label: string, value: string | number, expected: Fact[]) => {
      const ids = expected.map(f => f.seeded.traceId).sort(), expectedNames = expected.map(f => f.cfg.rootName!).sort();
      if (label === 'stale') {
        expect(updateBarrier).toBeDefined();
        expect(reachedOffsetBarrier(updateBarrier!.barrier, updateBarrier!.latest)).toBe(true);
        const current = await sourceReady();
        expect(current.filter(r => r.parent_span_id === '' && r.attrs_string[key] === '001')).toStrictEqual([]);
        expect(current.find(r => r.id === historical.seeded.spanIds[0])!.attrs_string[key]).toBe('002');
        expect(await catalog()).toStrictEqual(sorted([{ value: '001', type: 'string' }, { value: '002', type: 'string' },
          { value: '003', type: 'string' }, { value: 1, type: 'number' }]));
      }
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      if (hasFilter) {
        await page.getByRole('button', { name: 'Clear all', exact: true }).click();
        await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden();
        await page.getByRole('button', { name: 'Filter', exact: true }).click();
      }
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.getByPlaceholder('Search properties...').fill(key);
      await page.locator(`[data-filter-property-option="${key}"]`).click();
      await page.locator(`[data-filter-value-trigger="${key}"]`).click();
      const captured: { query: Query; status: number; scoped: boolean; body?: ListBody; error?: string }[] = [];
      const listener = (response: Response) => {
        if (!response.url().includes(LIST) || response.request().method() !== 'POST') return;
        const query = response.request().postDataJSON() as Query;
        const custom = (JSON.parse(query.filters) as Filter[]).find(f => f.column_id === key);
        // Match by display only to capture a wrongly coerced payload and FAIL
        // checkQuery's strict typed oracle, rather than waiting for a timeout.
        if (custom?.filter_config.filter_value.length !== 1 || String(custom.filter_config.filter_value[0]) !== String(value)) return;
        const headers = response.request().headers();
        const entry: typeof captured[number] = { query, status: response.status(), scoped:
          headers['x-organization-id'] === actor.organizationId && headers['x-workspace-id'] === actor.workspaceId
          && headers.authorization === 'Bearer ' + actor.tokens.access };
        captured.push(entry);
        void response.json().then(body => { entry.body = body; }, error => { entry.error = String(error); });
      };
      page.on('response', listener);
      try {
        await page.getByPlaceholder('Search values...').fill(String(value));
        // An actual catalog checkbox, never the '+ Specify' string shortcut.
        await page.locator(`[data-filter-value-option="${String(value)}"][role="checkbox"]`).click();
        hasFilter = true;
        await page.keyboard.press('Escape');
        await expect(page.getByPlaceholder('Search values...')).toBeHidden();
        await page.keyboard.press('Escape');
        await expect(page.getByRole('tab', { name: 'Basic', exact: true })).toBeHidden();
        await expect.poll(() => captured.some(c => c.body?.result.metadata.has_more === false), READY).toBe(true);
        expect(captured.length).toBeLessThanOrEqual(8);
        const uiIds: string[] = [];
        let cursor: string | undefined;
        for (const c of captured) {
          expect([c.status, c.scoped, c.error]).toStrictEqual([200, true, undefined]);
          checkQuery(c.query, value);
          expect(c.query.cursor ?? undefined).toBe(cursor);
          const m = checkPage(c.body!);
          uiIds.push(...c.body!.result.table.map(r => r.trace_id));
          cursor = m.next_cursor ?? undefined;
        }
        expect(cursor).toBeUndefined();
        expect(uiIds.sort()).toStrictEqual(ids);
        // Independent authorized walk of the REAL UI query, not a fabricated
        // filter or response. Bounded opaque cursors; empty partial != zero.
        const apiIds: string[] = [], cursors = new Set<string>(), pages: ListBody[] = [];
        const query = { ...captured[0].query };
        delete query.cursor;
        for (let i = 0; i < 8; i++) {
          checkQuery(query, value);
          const body = await actor.api.post<ListBody>(LIST, query), m = checkPage(body);
          pages.push(body); apiIds.push(...body.result.table.map(r => r.trace_id));
          if (!m.has_more) break;
          expect(cursors.has(m.next_cursor!)).toBe(false);
          cursors.add(m.next_cursor!); query.cursor = m.next_cursor!;
        }
        expect(pages.at(-1)!.result.metadata.has_more).toBe(false);
        expect(apiIds.sort()).toStrictEqual(ids);
        if (!ids.length) {
          expect(pages.at(-1)!.result.metadata.total_rows).toBe(0);
          expect(captured.at(-1)!.body!.result.metadata.total_rows).toBe(0);
          await expect(page.getByText('No traces found', { exact: true })).toBeVisible();
        }
        await expect.poll(async () => (await names.allTextContents()).sort(), READY).toStrictEqual(expectedNames);
        await expect.poll(async () => page.locator('.clean-data-table:visible .ag-row[row-id]').evaluateAll(rows =>
          rows.map(r => r.getAttribute('row-id')).sort()), READY).toStrictEqual(ids);
        evidence.push({ phase: label, expected: { value, type: typeof value, ids, names: expectedNames }, apiPages: pages });
      } finally {
        page.off('response', listener);
        await attach('ui-' + label, captured);
      }
    };

    try {
      const old = await test.step('start the pinned source-only collector and verify its unauthenticated identity', async () => {
        const endpoints = await h5.startSource();
        await expect.poll(async () => {
          try {
            const r = await request.get(endpoints.healthUrl, { timeout: 2000, maxRedirects: 0 });
            const body = await r.json();
            return r.status() === 200 && body.status === 'ok' && typeof body.stats === 'object';
          } catch { return false; }
        }, { timeout: 30_000, intervals: [250, 500, 1000] }).toBe(true);
        await expect.poll(async () => {
          try {
            const r = await request.get(endpoints.collectorUrl + '/v1/traces', { timeout: 2000, maxRedirects: 0 });
            return { status: r.status(), body: (await r.text()).trim() };
          } catch { return { status: 0, body: '' }; }
        }, POLL.SPAN_VISIBLE).toStrictEqual({ status: 401,
          body: '{"error":"missing credentials: provide X-Api-Key/X-Secret-Key or Authorization: Basic"}' });
        return endpoints;
      }, { timeout: 30_000 });
      await test.step('ingest four old OTLP traces, verify source and bind the exact three-second scope', async () => {
        await seed(old.collectorUrl, 'before', start - 1000n, 'excluded-before');
        historical = await seed(old.collectorUrl, 'historical', start + 123_000n, '001');
        numeric = await seed(old.collectorUrl, 'numeric', start + 1_000_000_000n, 1);
        await seed(old.collectorUrl, 'until', start + 3_000_000_000n, 'excluded-until');
        baseline = await sourceReady();
        const projects = await probe.pg<{ id: string }>(
          'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
          [h5.runId, actor.organizationId, actor.workspaceId]);
        expect(projects).toHaveLength(1); scope.project = projects[0].id;
        expect([...new Set(baseline.map(r => r.project_id))]).toStrictEqual([scope.project]);
        await h5.bind(scope);
      }, { timeout: 15_000 });
      await test.step('stop source and prove preview leaves source, catalog and discovery unchanged', async () => {
        await h5.stop('source'); await drain('source only');
        expect(await index(false)).toStrictEqual([]); expect(await index(true)).toStrictEqual([]);
        expect(await catalog()).toStrictEqual([]); expect(await metrics()).toStrictEqual([]);
        const preview = await h5.backfill('preview');
        expect(preview.pages.reduce((sum, p) => sum + p.source_rows, 0)).toBe(8);
        expect(preview.pages.at(-1).scan_complete).toBe(true);
        await drain('preview');
        expect(await source()).toStrictEqual(baseline);
        expect(await index(false)).toStrictEqual([]); expect(await index(true)).toStrictEqual([]);
        expect(await catalog()).toStrictEqual([]); expect(await metrics()).toStrictEqual([]);
        evidence.push({ phase: 'source-only preview', baseline, preview });
      }, { timeout: 60_000 });
      await test.step('bounded apply/resume with live data, stable OTLP replay, completed resume and overlap', async () => {
        const first = await h5.backfill('primary');
        expect([first.progress!.scan_complete, first.progress!.published_pages]).toStrictEqual([false, 2]);
        const resumed = h5.backfill('primary');
        const concurrent = await Promise.allSettled([resumed,
          seed(E2E.collectorUrl, 'live', BigInt(Date.now()) * 1_000_000n, '003')]);
        for (const result of concurrent) if (result.status === 'rejected') throw result.reason;
        live = (concurrent[1] as PromiseFulfilledResult<Fact>).value;
        await sourceReady();
        const primary = await finish('primary');
        for (const f of [historical, numeric, live]) events.push({ value: f.cfg.rootAttributes![key] as string | number,
          at: BigInt(f.cfg.startTimeUnixNano!) });
        // Historical discovery is proven BEFORE replay through the live path.
        await verifyCatalog(); await drain('backfilled plus live');
        await replay('001'); await verifyCatalog(); await drain('identical replay');
        const unchanged = await source();
        const completed = await h5.backfill('primary');
        expect(completed.pages).toStrictEqual([]); expect(completed.progress).toStrictEqual(primary);
        await finish('overlap'); await verifyCatalog(); await drain('overlap');
        expect(await source()).toStrictEqual(unchanged);
      }, { timeout: 180_000 });
      await test.step('UI stage 1: discover and select repaired historical string', async () => {
        await page.goto(`/dashboard/observe/${scope.project}/llm-tracing?selectedTab=trace`, { waitUntil: 'domcontentloaded' });
        await expect.poll(async () => (await names.allTextContents()).sort(), READY)
          .toStrictEqual(facts.map(f => f.cfg.rootName!).sort());
        await select('historical', '001', [historical]);
      }, { timeout: 60_000 });
      await test.step('UI stage 2: independently select live string and numeric suggestions', async () => {
        await select('live', '003', [live]); await select('numeric', 1, [numeric]);
      }, { timeout: 60_000 });
      await test.step('update ONLY the old root attribute through OTLP with stable keys/times', async () => {
        await replay('002');
        events.push({ value: '002', at: BigInt(historical.cfg.startTimeUnixNano!) });
      }, { timeout: 15_000 });
      await test.step('retain old/new observations and consume the update barrier before zero-match UI', async () => {
        await verifyCatalog(); updateBarrier = await drain('updated history'); await h5.stop('audit');
      }, { timeout: 60_000 });
      await test.step('UI stage 3: refreshed stale checkbox remains visible but has complete zero matches', async () => {
        await page.reload({ waitUntil: 'domcontentloaded' });
        // useLLMTracingFilters uses useUrlState: the selected numeric filter
        // survives reload. Clear it through the real panel before selecting old.
        await select('stale', '001', []);
      }, { timeout: 60_000 });
      await test.step('UI stage 4: replacement matches the same identity, including after refresh', async () => {
        await select('replacement', '002', [historical]);
        await page.reload({ waitUntil: 'domcontentloaded' });
        await select('replacement-refreshed', '002', [historical]);
        expect(await source()).toHaveLength(10);
      }, { timeout: 60_000 });
      outcome = 'passed';
    } catch (error) { outcome = 'failed'; failure = String(error); throw error; }
    finally {
      try { await h5.retainAndStop(); }
      catch (error) { outcome = 'failed'; failure = String(error); throw error; }
      finally {
        await attach('obs010-evidence', { outcome, failure, runId: h5.runId, key, selection: scope,
          fixtures: facts.map(f => ({ label: f.label, ...f.seeded, value: f.cfg.rootAttributes![key],
            startNs: String(f.cfg.startTimeUnixNano), endNs: String(f.cfg.endTimeUnixNano) })),
          events: events.map(e => ({ ...e, at: String(e.at) })), evidence, receipts: h5.receipts });
      }
    }
  });
});
