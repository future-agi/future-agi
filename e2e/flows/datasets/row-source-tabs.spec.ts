import { request, type Browser, type BrowserContext, type Page, type TestInfo } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor, ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { flowAnnotation } from '../../lib/flow-meta';

// owed-733-rerun: spans reach Observe through the real collector, with the
// owner's credential used only through the harness Actor abstraction; rerun on
// the composed personal-key head before the Row view source surface turns on.
//
// The Observe import (tracer/views/dataset.py add_to_new_dataset) records each
// row's source (model_hub_row_source); get-row-data projects it as
// row_meta.source. The build under test ships the row source surface
// (dataset.rowExtras.conversation) unless E2E_DSX_ROW_SOURCE=unclaimed.
const IMPORT = '/tracer/dataset/add_to_new_dataset/';
const ROW_READ = '/model-hub/datasets/{dataset_id}/rows/{row_id}/';
const CREATE = '/model-hub/develops/create-dataset-manually/';
const UI_READY = 60_000;
const CLAIMED = (process.env.E2E_DSX_ROW_SOURCE ?? 'claimed') !== 'unclaimed';
const MAPPING = ['name', 'input', 'output'].map(name => ({ col_name: name, span_field: name, data_type: 'text' }));

type Source = {
  type: string; label: string; project_id: string; trace_id?: string; span_id?: string;
  session_id?: string; expired?: boolean;
};
type Current = { row_id: string; row_meta?: { source?: Source } } & Record<string, unknown>;
type Table = { result: { column_config: { id: string; name: string }[]; table: { row_id: string }[] } };
type Stored = {
  kind: string; project_id: string; trace_id: string; span_id: string; session_id: string | null;
  organization_id: string; workspace_id: string;
};

function owner(scopes: ScopeActors) {
  // owed-733-rerun: the credential stays inside the harness Actor; never read or listed here.
  return scopes.owners.find(o => o.organizationId === scopes.ownerA.organizationId)!;
}

async function ingest(scopes: ScopeActors, probe: StateProbe, info: TestInfo, traces: number, session?: string,
  options: { rootSpanId?: string; sessionAttribute?: string; suffix?: string } = {}) {
  const label = `e2e-rowsrc-${info.workerIndex}-${Date.now().toString(36)}${options.suffix ?? ''}`;
  const sessionAttribute = options.sessionAttribute ?? (session ? `${label}-${session}` : undefined);
  const start = BigInt(Date.now()) * 1_000_000n;
  const expected = Array.from({ length: traces }, (_, i) => ({
    input: `${label}-question-${i + 1}`, output: `${label}-answer-${i + 1}`,
  }));
  const actor = owner(scopes);
  const req = await request.newContext();
  const seeded: SeededTrace[] = [];
  try {
    for (let i = 0; i < traces; i++) {
      // Collector's input.value/output.value attributes become spans.input/output.
      const attributes = { ...(sessionAttribute ? { 'session.id': sessionAttribute } : {}),
        'input.value': expected[i].input, 'output.value': expected[i].output }; 
      const trace = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
        secretKey: actor.secretKey, projectName: label, resourceAttributes: { project_type: 'observe' },
        rootName: `${label}-turn-${i + 1}`, childName: `${label}-call-${i + 1}`,
        rootAttributes: attributes, childAttributes: attributes, rootSpanId: options.rootSpanId,
        startTimeUnixNano: start + BigInt(i) * 1_000_000_000n });
      seeded.push(trace);
      await expect.poll(async () => Number((await probe.ch<{ n: string }>(
        'SELECT count() n FROM spans FINAL WHERE trace_id={t:String}', { t: trace.traceId }))[0].n),
      POLL.SPAN_VISIBLE).toBe(2);
    }
  } finally { await req.dispose(); }
  const projects = await probe.pg<{ id: string }>(
    'SELECT id::text AS id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
    [label, actor.organizationId, actor.workspaceId]);
  expect(projects).toHaveLength(1);
  const roots: { id: string; trace_id: string; name: string; input: string; output: string; session: string | null }[] = [];
  for (const trace of seeded) {
    const [root, ...extra] = await probe.ch<{ id: string; trace_id: string; name: string; input: string; output: string; session: string | null }>(
      `SELECT id, trace_id, name, input, output, toString(trace_session_id) AS session FROM spans FINAL
       WHERE trace_id = {t:String} AND id = {r:String}`, { t: trace.traceId, r: trace.spanIds[0] });
    expect(extra).toEqual([]);
    roots.push(root);
  }
  await info.attach('row-source-ingest', { contentType: 'application/json', body: JSON.stringify({
    label, project: projects[0].id, ingestion: 'collector', marker: 'owed-733-rerun',
    traces: seeded.map(t => t.traceId), roots: roots.map(r => r.id),
    organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  expect(roots.map(({ input, output }) => ({ input, output }))).toEqual(expected);
  return { label, project: projects[0].id, seeded, roots, expected, sessionAttribute };
}

async function importRows(a: ScopeActor, probe: StateProbe, body: Record<string, unknown>, rows: number) {
  const started = await a.api.post<{ result: { dataset_id: string } }>(IMPORT, { mapping_config: MAPPING, ...body });
  const dataset = started.result.dataset_id;
  await expect.poll(async () => (await probe.pg<{ n: string }>(
    'SELECT count(*)::text AS n FROM model_hub_row WHERE dataset_id=$1::uuid AND NOT deleted', [dataset]))[0].n,
  POLL.ASYNC_JOB).toBe(String(rows));
  const table = (await a.api.get<Table>(`/model-hub/develops/${dataset}/get-dataset-table/`)).result;
  return { dataset, rowIds: table.table.map(r => r.row_id), column: table.column_config.find(c => c.name === 'name')!, columns: table.column_config };
}

async function current(a: ScopeActor, dataset: string, row: string) {
  return (await a.api.post<{ result: { current: Current } }>(
    `/model-hub/develops/${dataset}/get-row-data/`, { row_id: row })).result.current;
}

async function stored(probe: StateProbe, row: string) {
  return probe.pg<Stored>(
    `SELECT kind, project_id::text AS project_id, trace_id, span_id, session_id,
       organization_id::text AS organization_id, workspace_id::text AS workspace_id
     FROM model_hub_row_source WHERE row_id=$1::uuid`, [row]);
}

async function refusedToB(scopes: ScopeActors, dataset: string, row: string, secrets: string[]) {
  const b = scopes.ownerB;
  const reads = [
    await scopes.send<unknown>(b, 'POST', `/model-hub/develops/${dataset}/get-row-data/`, { row_id: row }),
    await scopes.send<unknown>(b, 'GET', ROW_READ.replace('{dataset_id}', dataset).replace('{row_id}', row)),
    // org A's headers on org B's session: still refused.
    await scopes.send<unknown>({ ...b, organizationId: scopes.ownerA.organizationId,
      workspaceId: scopes.ownerA.workspaceId }, 'GET', ROW_READ.replace('{dataset_id}', dataset).replace('{row_id}', row)),
  ];
  for (const read of reads) {
    expect(read.status).toBe(404);
    for (const secret of secrets) expect(JSON.stringify(read.body)).not.toContain(secret);
  }
}

async function open(scopes: ScopeActors, browser: Browser, a: ScopeActor, dataset: string, search: string):
  Promise<{ context: BrowserContext; page: Page; traffic: string[] }> {
  const context = await scopes.openContext(browser, a);
  // Runtime traffic stays on fixture loopback. External requests are blocked, never mocked.
  await context.route('**/*', route => ['localhost', '127.0.0.1'].includes(new URL(route.request().url()).hostname)
    || ['data:', 'blob:'].includes(new URL(route.request().url()).protocol)
    ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  const traffic: string[] = [];
  page.on('request', r => traffic.push(`${r.method()} ${new URL(r.url()).pathname}${new URL(r.url()).search}`));
  const opened = page.waitForResponse(r => new URL(r.url()).pathname === `/model-hub/develops/${dataset}/get-row-data/`
    && r.request().method() === 'POST', { timeout: UI_READY });
  await page.goto(`/dashboard/develop/${dataset}?${search}`, { waitUntil: 'domcontentloaded' });
  expect((await opened).status()).toBe(200);
  return { context, page, traffic };
}

const tab = (page: Page, name: string) => page.getByRole('tab', { name, exact: true });

test('DATA-E2E-095: a row imported from a trace opens its trajectory and the same trace', {
  tag: ['@flow'], annotation: flowAnnotation({ id: 'DATA-E2E-095', area: 'datasets',
    userGoal: 'Open the trace a dataset row was imported from, read in its own project, without another organization reaching it',
    steps: ['send a trace to the collector with the owner credential and import it into a new dataset',
      'open the row in Row view and its Trajectory tab', 'open the original trace from the tab',
      'open the same row as another organization'],
    backendChecks: ['the stored row source names the import kind, project, trace and span the collector wrote, in the owner organization and workspace',
      'get-row-data and the row read return the source and exact IO from its project, not the competing project sharing the span id',
      'another organization cannot read the row, original trace or source span'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  test.setTimeout(600_000);
  const a = scopeActors.ownerA;
  const data = await ingest(scopeActors, scopeProbe, info, 1);
  const [root] = data.roots;
  const competitor = await ingest(scopeActors, scopeProbe, info, 1, undefined,
    { rootSpanId: root.id, suffix: '-competing' });
  expect(competitor.project).not.toBe(data.project);
  expect(competitor.roots[0].id).toBe(root.id);
  const { dataset, rowIds: [row], column, columns } = await importRows(a, scopeProbe,
    { new_dataset_name: `${data.label}-traces`, project: data.project, trace_ids: [root.trace_id], source_kind: 'trace' }, 1);

  const source: Source = { type: 'trace', label: `Trace ${root.trace_id.slice(0, 8)}`, project_id: data.project,
    trace_id: root.trace_id, span_id: root.id, expired: false };
  const live = await current(a, dataset, row);
  expect(live.row_meta?.source).toEqual(source);
  expect((live[column.id] as { cell_value: string }).cell_value).toBe(root.name);
  for (const field of ['input', 'output'] as const) {
    const col = columns.find(c => c.name === field)!;
    expect((live[col.id] as { cell_value: string }).cell_value).toBe(root[field]);
  }
  expect(await stored(scopeProbe, row)).toEqual([{ kind: 'trace', project_id: data.project, trace_id: root.trace_id,
    span_id: root.id, session_id: root.session, organization_id: a.organizationId, workspace_id: a.workspaceId }]);
  const read = await scopeActors.send<{ result: { row_meta: { source: Source } } }>(a, 'GET',
    ROW_READ.replace('{dataset_id}', dataset).replace('{row_id}', row));
  expect(read.status).toBe(200);
  expect(read.body.result.row_meta.source).toEqual(source);
  await refusedToB(scopeActors, dataset, row, [root.trace_id, root.id]);
  for (const path of [`/tracer/trace/${root.trace_id}/?project_id=${data.project}`,
    `/tracer/observation-span/${root.id}/?project_id=${data.project}`]) {
    const allowed = await scopeActors.send(a, 'GET', path);
    expect(allowed.status).toBe(200);
    expect(JSON.stringify(allowed.body)).toContain(root.input);
    expect(JSON.stringify(allowed.body)).not.toContain(competitor.roots[0].input);
    const denied = await scopeActors.send(scopeActors.ownerB, 'GET', path);
    expect([400, 403, 404]).toContain(denied.status);
    for (const value of [root.id, root.trace_id, root.input, root.output]) {
      expect(JSON.stringify(denied.body)).not.toContain(value);
    }
  }

  const view = await open(scopeActors, browser, a, dataset, `row=${row}`);
  try {
    const { page } = view;
    if (!CLAIMED) {
      await expect(page.getByTestId('row-source-pill')).toHaveCount(0);
      await expect(tab(page, 'Trajectory')).toHaveCount(0);
      return;
    }
    await expect(page.getByTestId('row-source-pill')).toContainText(source.label);
    const traceRead = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace/${root.trace_id}/`);
    await tab(page, 'Trajectory').click();
    const traced = await traceRead;
    expect(traced.status()).toBe(200);
    expect(new URL(traced.url()).searchParams.get('project_id')).toBe(data.project);
    const detail = await traced.json();
    const rootSpan = detail.result.observation_spans.find((entry: { observation_span: { id: string } }) => entry.observation_span.id === root.id).observation_span;
    expect([rootSpan.input, rootSpan.output]).toEqual([root.input, root.output]);
    expect(JSON.stringify(detail)).not.toContain(competitor.roots[0].input);
    await expect(page.getByText(root.input, { exact: true })).toBeVisible();
    await expect(page.getByText(root.output, { exact: true })).toBeVisible();
    await expect(page.getByTestId('source-trace-tree')).toContainText(root.name);
    const drawerRead = page.waitForRequest(r => new URL(r.url()).pathname === `/tracer/trace/${root.trace_id}/`);
    await page.getByTestId('open-original').click();
    await drawerRead;
  } finally { await view.context.close(); }

  // Org B's session on the same deep link: no row, no trace.
  const foreign = await open(scopeActors, browser, scopeActors.ownerB, dataset, `row=${row}&rowTab=trajectory`)
    .catch(error => ({ error }));
  if ('error' in foreign) {
    expect(String(foreign.error)).toContain('404');
  } else {
    try {
      await expect(foreign.page.getByTestId('source-trace-tree')).toHaveCount(0);
      expect(foreign.traffic.filter(t => t.includes(root.trace_id))).toEqual([]);
    } finally { await foreign.context.close(); }
  }
});

test('DATA-E2E-096: a row imported from a session shows its turns, then the saved copy once the session is gone', {
  tag: ['@flow'], annotation: flowAnnotation({ id: 'DATA-E2E-096', area: 'datasets',
    userGoal: 'Read the conversation a session-imported row came from, and keep its saved copy after the session is deleted',
    steps: ['send 51 distinct turns of one session to the collector and import the session into a new dataset',
      'open a row in Row view and its Conversation tab', 'delete the session in Observe and reopen the row',
      'open the same row as another organization'],
    backendChecks: ['the stored row source is a session source naming the session the collector wrote',
      'the session query pages and Conversation render all 51 persisted turns in order',
      'get-row-data reports expiry after deletion and retains the exact saved values without a source request',
      'another organization cannot read the row or source session'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  test.setTimeout(1_200_000);
  const a = scopeActors.ownerA;
  const data = await ingest(scopeActors, scopeProbe, info, 51, 'chat');
  const session = data.roots[0].session!;
  expect(data.roots.map(r => r.session)).toEqual(Array(51).fill(session));
  const { dataset, rowIds, columns } = await importRows(a, scopeProbe, { new_dataset_name: `${data.label}-session`,
    project: data.project, trace_ids: data.roots.map(r => r.trace_id), source_kind: 'session', session_ids: [session] }, 51);
  const row = rowIds[0];
  const record = (await stored(scopeProbe, row))[0];
  const savedRoot = data.roots.find(root => root.id === record.span_id)!;
  expect(savedRoot).toBeTruthy();
  const live = await current(a, dataset, row);
  expect(live.row_meta?.source).toEqual({ type: 'session', label: `Session ${session.slice(0, 8)}`,
    project_id: data.project, session_id: session, expired: false });
  expect((await stored(scopeProbe, row))[0]).toMatchObject({ kind: 'session', session_id: session,
    project_id: data.project, organization_id: a.organizationId, workspace_id: a.workspaceId });
  await refusedToB(scopeActors, dataset, row, [session]);
  const sessionPath = `/tracer/trace-session/${session}/query/`;
  const denied = await scopeActors.send(scopeActors.ownerB, 'POST', sessionPath, { page_number: 0, page_size: 50 });
  expect([400, 403, 404]).toContain(denied.status);
  expect(JSON.stringify(denied.body)).not.toContain(session);
  type TurnPage = { result: { response: { trace_id: string; input: string; output: string }[]; next: boolean } };
  const pages = await Promise.all([0, 1].map(page_number => a.api.post<TurnPage>(sessionPath, { page_number, page_size: 50 })));
  expect(pages.map(page => page.result.next)).toEqual([true, false]);
  expect(pages.flatMap(page => page.result.response.map(({ trace_id, input, output }) => ({ trace_id, input, output }))))
    .toEqual(data.roots.map(({ trace_id, input, output }) => ({ trace_id, input, output })));
  const savedValues = columns.map(col => (live[col.id] as { cell_value: string }).cell_value);
  for (const field of ['name', 'input', 'output'] as const) {
    expect(savedValues[columns.findIndex(c => c.name === field)]).toBe(savedRoot[field]);
  }

  if (CLAIMED) {
    const view = await open(scopeActors, browser, a, dataset, `row=${row}&rowTab=conversation`);
    try {
      const turns = view.page.getByTestId('session-turn');
      const expectedTurns = data.roots.map((root, i) => `Turn ${i + 1}User${root.input}Agent${root.output}`);
      await expect(turns).toHaveText(expectedTurns.slice(0, 50));
      await view.page.getByRole('button', { name: 'Load more turns', exact: true }).click();
      await expect(turns).toHaveText(expectedTurns);
      await expect(view.page.getByRole('button', { name: 'Load more turns', exact: true })).toHaveCount(0);
    } finally { await view.context.close(); }
  }

  // Delete the session in Observe; the row keeps its saved copy.
  await a.api.delete(`/tracer/trace-session/${session}/`);
  await expect.poll(async () => (await current(a, dataset, row)).row_meta?.source?.expired, POLL.ASYNC_JOB).toBe(true);
  const savedAfter = await current(a, dataset, row);
  expect(columns.map(col => (savedAfter[col.id] as { cell_value: string }).cell_value)).toEqual(savedValues);
  if (CLAIMED) {
    const view = await open(scopeActors, browser, a, dataset, `row=${row}&rowTab=conversation`);
    try {
      await expect(view.page.getByTestId('conversation-expired-note')).toContainText('copy saved at import');
      await expect(view.page.getByTestId('saved-row-value')).toHaveText(savedValues);
      expect(view.traffic.filter(r => /\/tracer\/(trace-session|trace)\//.test(r))).toEqual([]);
      await expect(view.page.getByTestId('session-turn')).toHaveCount(0);
    } finally { await view.context.close(); }
  }
});

test('DATA-E2E-097: a row with no source shows neither source tab, and a source deep link lands on Row', {
  tag: ['@flow'], annotation: flowAnnotation({ id: 'DATA-E2E-097', area: 'datasets',
    userGoal: 'Open a dataset row that was not imported from Observe without source tabs or doors',
    steps: ['create a synthetic dataset in each of two organizations', 'open a row with a Trajectory deep link',
      'open the same row as another organization'],
    backendChecks: ['get-row-data returns no row_meta.source and no source is stored for the row',
      'another organization gets 404 for the row'],
  }),
}, async ({ browser, scopeActors, scopeProbe }, info) => {
  test.setTimeout(300_000);
  const a = scopeActors.ownerA;
  const name = `e2e-rowsrc-none-${info.workerIndex}-${Date.now().toString(36)}`;
  const dataset = (await a.api.post<{ result: { dataset_id: string } }>(CREATE,
    { dataset_name: name, number_of_rows: 1, number_of_columns: 1 })).result.dataset_id;
  const [row] = (await a.api.get<Table>(`/model-hub/develops/${dataset}/get-dataset-table/`)).result.table.map(r => r.row_id);
  expect((await current(a, dataset, row)).row_meta?.source).toBeUndefined();
  expect(await stored(scopeProbe, row)).toEqual([]);
  await refusedToB(scopeActors, dataset, row, [name]);

  for (const deepLink of ['trajectory', 'conversation']) {
    const view = await open(scopeActors, browser, a, dataset, `row=${row}&rowTab=${deepLink}`);
    try {
      await expect(tab(view.page, 'Row')).toHaveAttribute('aria-selected', 'true');
      await expect(tab(view.page, 'Trajectory')).toHaveCount(0);
      await expect(tab(view.page, 'Conversation')).toHaveCount(0);
      await expect(view.page.getByTestId('row-source-pill')).toHaveCount(0);
    } finally { await view.context.close(); }
  }
});
