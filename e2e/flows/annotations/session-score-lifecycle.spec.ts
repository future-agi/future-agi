import { test, expect } from '../../lib/scope-actors';
import type { Request } from '@playwright/test';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// InlineAnnotator + api/scores/scores.js; model_hub/serializers/scores.py.
const SCORES = '/model-hub/scores/';
// model_hub/serializers/{develop_annotations,annotation_queues}.py.
const LABELS = '/model-hub/annotations-labels/';
const QUEUES = '/model-hub/annotation-queues/';
// Session-grid.buildParams -> trace_session.list_sessions -> SessionListQueryBuilderV2.
const LIST = '/tracer/trace-session/list_sessions/';
// ObserveToolbar: definitions use traces; annotation values use sessions.
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
const UI_READY = 60_000; // README Writing a flow: browser readiness, not CDC.
type Value = { value: number | string } | { rating: number } | { text: string } | { selected: string[] };
type Label = { id: string; name: string; type: string; settings: Record<string, unknown>;
  old: Value; next: Value; filter: [string | number, string | number]; choices: string[] };
type Session = { project_id: string; trace_session_id: string; external_session_id: string };
type Score = { id: string; label_id: string; source_type: string; trace_session_id: string;
  trace_id: string | null; observation_span_id: string | null; call_execution_id: string | null;
  prototype_run_id: string | null; dataset_row_id: string | null; tracer_project_id: string;
  organization_id: string; workspace_id: string; annotator_id: string; queue_item_id: string;
  score_source: string; value: Value; value_history: { value: Value; at: string }[];
  deleted: number; has_delete_timestamp: number };
type ScoreApi = { id: string; label_id: string; source_type: string; source_id: string;
  queue_item: string; queue_id: string; value: Value; value_history: Score['value_history'] };
type Filter = { column_id: string; property_id?: string; filter_config: {
  col_type: string; filter_type: string; filter_op: string; filter_value: unknown } };
type ListPage = { result: { table: { session_id: string; project_id: string; total_traces_count: number }[];
  metadata: { has_more: boolean; next_cursor: string | null; query_complete: boolean;
    query_status: string; query_error_code: string | null; total_rows_is_lower_bound: boolean } } };
// trace_session._empty_session_list_response: a workspace with no projects skips CH entirely.
type EmptyWorkspacePage = { result: { table: never[]; metadata: { total_rows: number } } };
type MetricsPage = { result: { metrics: { property_id: string; output_type: string }[] } };
type ValuePage = { result: { values: { value: string; label: string }[]; has_more: boolean; next_cursor: string | null } };

test('ANNOT-E2E-004: corrected session annotations match only current authorized sessions', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'ANNOT-E2E-004', area: 'annotations',
    userGoal: 'Submit and correct all six native session annotations, then find only sessions with current, authorized scores',
    steps: ['ingest two sessions and a same-named sibling-project session; configure six projectless labels on the default queue',
      'open Session History and submit all six native controls with Annotate and Save',
      'discover configured choices and select each annotation in the real session filter',
      'use Edit and Save to correct the same scores and compare old/new exact matches',
      'verify sibling-project and explicit organization/workspace isolation',
      'soft-delete only the synthetic scores through the public endpoint and reload the retained session filter'],
    backendChecks: [
      'exact cohort grouping, scoped projectless labels/default queue and unchanged source telemetry',
      'six UI-created Scores have exact session, label, annotator, queue, tenant and typed values; trace/span references are null',
      'latest ClickHouse Scores equal exact Postgres identities and typed state',
      'definition choices and actual UI/list results agree for every annotation type',
      'correction preserves Score IDs and appends exactly the previous typed value/history timestamp',
      'public soft deletion excludes retained predicates without changing source spans',
      'sibling project, owner B organization and owner A explicit other workspace cannot read target Scores/catalog/results',
    ],
  }),
}, async ({ browser, request, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Approved ANNOT002/003 target batch: 780s + 180s public-deletion CDC.
  // 2*SPAN_VISIBLE(15) + 3*CDC_VISIBLE(180) + 6*UI_READY(60) + 30 headroom = 960s.
  // Each CDC barrier covers all six labels together, never one budget per type.
  test.setTimeout(960_000);
  const prefix = `e2e-annot4-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const actor = scopes.ownerA;
  const owner = scopes.owners.find(a => a.organizationId === actor.organizationId)!;
  const siblingName = `${prefix}-sibling`;
  const shared = `${prefix}-shared`;
  const other = `${prefix}-other`;
  const seeds: (Awaited<ReturnType<typeof sendTrace>> & { session: string })[] = [];
  // converter.go:newSpanIdentity; existing OBS-E2E-006 grouping shape. No Score seeds.
  for (const [projectName, session] of [[prefix, shared], [prefix, shared], [prefix, other], [siblingName, shared]]) {
    const attrs = { 'session.id': session, 'user.id': `${prefix}-user` };
    seeds.push({ session, ...await sendTrace(request, { collectorUrl: E2E.collectorUrl,
      apiKey: owner.apiKey, secretKey: owner.secretKey, projectName,
      rootName: `${prefix}-root-${seeds.length}`, childName: `${prefix}-child-${seeds.length}`,
      resourceAttributes: { project_type: 'observe' }, rootAttributes: attrs, childAttributes: attrs }) });
  }
  await testInfo.attach('seeded-sessions', { body: JSON.stringify(seeds), contentType: 'application/json' });
  // server.go:handleHTTPTraces awaits StampResourceAttrs -> ResolveProjectsForKey ->
  // pgresolver.GetOrCreateProject's PG INSERT/RETURNING before enqueue/HTTP 200.
  // Only CH delivery is batched; a missing project here is not ingestion lag.
  const projects = await probe.pg<{ id: string; name: string }>(
    'SELECT id, name FROM tracer_project WHERE name = ANY($1) AND organization_id = $2 AND workspace_id = $3',
    [[prefix, siblingName], actor.organizationId, actor.workspaceId]);
  expect(projects.map(p => p.name).sort()).toEqual([prefix, siblingName].sort());
  const projectId = projects.find(p => p.name === prefix)!.id;
  const siblingId = projects.find(p => p.name === siblingName)!.id;
  const projectParams = { p: projectId, q: siblingId };
  const readSessions = () => probe.ch<Session>(`SELECT project_id, trace_session_id, external_session_id
    FROM trace_sessions FINAL WHERE project_id IN ({p:UUID}, {q:UUID}) AND is_deleted = 0
    ORDER BY project_id, trace_session_id`, projectParams);
  let sessions: Session[] = [];
  await test.step('check 1: collector sessions and authoritative span grouping', async () => {
    await expect.poll(async () => {
      sessions = await readSessions();
      return sessions.map(s => `${s.project_id}:${s.external_session_id}`).sort();
    }, POLL.SPAN_VISIBLE).toEqual([`${projectId}:${shared}`, `${projectId}:${other}`, `${siblingId}:${shared}`].sort());
    expect(new Set(sessions.map(s => s.trace_session_id)).size).toBe(3);
    const expected = seeds.flatMap(seed => seed.spanIds.map(id => {
      const p = seed.projectName === prefix ? projectId : siblingId;
      return { id, trace_id: seed.traceId, project_id: p,
        trace_session_id: sessions.find(s => s.project_id === p && s.external_session_id === seed.session)!.trace_session_id };
    })).sort((a, b) => a.id.localeCompare(b.id));
    await expect.poll(() => probe.ch(`SELECT id, trace_id, project_id, trace_session_id
      FROM spans FINAL WHERE project_id IN ({p:UUID}, {q:UUID}) ORDER BY id`, projectParams), POLL.SPAN_VISIBLE).toEqual(expected);
    await testInfo.attach('scoped-source-identities', { body: JSON.stringify({ projects, sessions, expected,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId }), contentType: 'application/json' });
  });
  const sessionId = sessions.find(s => s.project_id === projectId && s.external_session_id === shared)!.trace_session_id;
  const siblingSessionId = sessions.find(s => s.project_id === siblingId)!.trace_session_id;
  const initialGrid = sessions.filter(s => s.project_id === projectId).map(s => s.trace_session_id).sort();
  const readSource = () => probe.ch('SELECT * FROM spans FINAL WHERE project_id IN ({p:UUID}, {q:UUID}) ORDER BY id', projectParams);
  const sourceBefore = await readSource();
  await testInfo.attach('source-spans-before', { body: JSON.stringify(sourceBefore), contentType: 'application/json' });
  // Same native settings/value objects as ANNOT002/003; LabelInput + AnnotationsLabels.validate_settings.
  const definitions: Omit<Label, 'id'>[] = [
    { name: `${prefix}-numeric`, type: 'numeric', settings: { min: 0, max: 100, step_size: 1, display_type: 'slider' },
      old: { value: 25 }, next: { value: 75 }, filter: [25, 75], choices: [] },
    { name: `${prefix}-star`, type: 'star', settings: { no_of_stars: 5 },
      old: { rating: 2 }, next: { rating: 4 }, filter: [2, 4], choices: ['1', '2', '3', '4', '5'] },
    { name: `${prefix}-text`, type: 'text', settings: { placeholder: `${prefix}-text-input`, min_length: 0, max_length: 500 },
      old: { text: `${prefix}-old` }, next: { text: `${prefix}-new` }, filter: [`${prefix}-old`, `${prefix}-new`], choices: [] },
    { name: `${prefix}-thumbs`, type: 'thumbs_up_down', settings: {},
      old: { value: 'up' }, next: { value: 'down' }, filter: ['thumbs_up', 'thumbs_down'], choices: ['thumbs_up', 'thumbs_down'] },
    ...[false, true].map(multi => {
      const options = ['alpha', 'beta', 'gamma'].map(v => `${prefix}-${multi ? 'multi' : 'single'}-${v}`);
      return { name: `${prefix}-${multi ? 'multi' : 'single'}`, type: 'categorical',
        settings: { multi_choice: multi, options: options.map(label => ({ label })), auto_annotate: false, rule_prompt: '', strategy: null },
        old: { selected: multi ? [options[0], options[2]] : [options[0]] }, next: { selected: [options[1]] },
        filter: [options[0], options[1]] as [string, string], choices: options };
    }),
  ];
  const labels: Label[] = [];
  for (const definition of definitions) {
    // Omit project (serializer does not accept null): forces score-backed fixed-project discovery.
    const created = await actor.api.post<{ result: { id: string } }>(LABELS, {
      name: definition.name, type: definition.type, settings: definition.settings, allow_notes: false });
    labels.push({ ...definition, id: created.result.id });
  }
  const queue = (await actor.api.post<{ result: { queue: { id: string; name: string; is_default: boolean } } }>(
    `${QUEUES}get-or-create-default/`, { project_id: projectId })).result.queue;
  for (const label of labels) await actor.api.post(`${QUEUES}${queue.id}/add-label/`, { label_id: label.id });
  const labelIds = labels.map(l => l.id).sort();
  await testInfo.attach('annotation-identities', { body: JSON.stringify({ labels, queue, sessionId,
    projectId, siblingId, siblingSessionId, annotatorId: actor.userId,
    organizationId: actor.organizationId, workspaceId: actor.workspaceId }), contentType: 'application/json' });
  await test.step('check 1: exact projectless definitions and default queue', async () => {
    expect(await probe.pg(`SELECT id, name, type, settings, project_id, organization_id, workspace_id
      FROM model_hub_annotationslabels WHERE id = ANY($1) AND deleted = false ORDER BY id::text`, [labelIds]))
      .toEqual(labels.map(l => ({ id: l.id, name: l.name, type: l.type, settings: l.settings, project_id: null,
        organization_id: actor.organizationId, workspace_id: actor.workspaceId })).sort((a, b) => a.id.localeCompare(b.id)));
    expect(await probe.pg(`SELECT id, project_id, organization_id, workspace_id, is_default
      FROM model_hub_annotationqueue WHERE id = $1 AND deleted = false`, [queue.id]))
      .toEqual([{ id: queue.id, project_id: projectId, organization_id: actor.organizationId,
        workspace_id: actor.workspaceId, is_default: true }]);
    expect(await probe.pg('SELECT label_id FROM model_hub_annotationqueuelabel WHERE queue_id = $1 AND deleted = false ORDER BY label_id::text', [queue.id]))
      .toEqual(labelIds.map(label_id => ({ label_id })));
  });
  const fields = `id, label_id, source_type, trace_session_id, tracer_project_id,
    organization_id, workspace_id, annotator_id, queue_item_id, score_source`;
  const otherRefs = ['trace_id', 'observation_span_id', 'call_execution_id', 'prototype_run_id', 'dataset_row_id'];
  const readPG = () => probe.pg<Score>(`SELECT ${fields}, ${otherRefs.join(', ')}, value, value_history,
    deleted::integer AS deleted, CASE WHEN deleted_at > to_timestamp(0) THEN 1 ELSE 0 END AS has_delete_timestamp
    FROM model_hub_score WHERE label_id = ANY($1) ORDER BY label_id::text`, [labelIds]);
  // CDC_MODEL_HUB_SCORE bootstraps nullable refs; existing PeerDB mirrors use
  // UUID zero / String empty for absent refs (verified via system.columns).
  // Normalize only alternate refs; the required session/tenant/Score IDs stay exact.
  const readCH = async () => (await probe.ch<Omit<Score, 'value' | 'value_history'> & { value: string; value_history: string }>(
    `SELECT ${fields}, ${otherRefs.map(f => `nullIf(toString(${f}), '${f === 'observation_span_id' ? '' : '00000000-0000-0000-0000-000000000000'}') AS ${f}`).join(', ')}, value, value_history,
      toUInt8(deleted) AS deleted, toUInt8(ifNull(deleted_at > toDateTime64(0, 6), 0)) AS has_delete_timestamp
     FROM model_hub_score FINAL WHERE label_id IN (${labelIds.map((_, i) => `{l${i}:UUID}`).join(', ')})
       AND _peerdb_is_deleted = 0 ORDER BY toString(label_id)`, Object.fromEntries(labelIds.map((id, i) => [`l${i}`, id]))))
    .map(s => ({ ...s, value: JSON.parse(s.value), value_history: JSON.parse(s.value_history) }));
  expect(await readPG()).toEqual([]);
  const context = await scopes.openContext(browser, actor);
  const page = await context.newPage();
  page.setDefaultTimeout(UI_READY);
  // PrimaryGraph's session transport can disable the shared Reload control.
  // Keep its actual request/response alongside list evidence if that gate fails.
  const graphResponses: unknown[] = [];
  const graphReads = new Set<Promise<void>>();
  page.on('response', response => {
    if (new URL(response.url()).pathname !== '/tracer/trace-session/get_session_graph_data/') return;
    const read = (async () => {
      try {
        graphResponses.push({ status: response.status(), request: response.request().postDataJSON(),
          body: await response.json() });
      } catch (error) {
        graphResponses.push({ status: response.status(), error: String(error) });
      }
    })();
    graphReads.add(read);
    void read.finally(() => graphReads.delete(read));
  });
  // LLMTracingView Sessions navigation; TracesDrawer's existing semantic tablist.
  const url = `/dashboard/observe/${projectId}/sessions`;
  const cells = page.locator('.ag-row [col-id="session_id"]:visible');
  const rowsInGrid = () => cells.evaluateAll(nodes => nodes.map(n => n.closest('.ag-row')!.getAttribute('row-id')).sort());
  const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByRole('tablist', { name: 'session drawer tabs' }) });
  // Local assertion composition only; actor/probe/seeding capabilities remain in existing lib/.
  const paramsOf = (req: Request): Record<string, string | number | boolean> => req.method() === 'POST'
    ? req.postDataJSON() : Object.fromEntries(new URL(req.url()).searchParams);
  const complete = (body: ListPage) => expect(body.result.metadata).toMatchObject({ has_more: false,
    next_cursor: null, query_complete: true, query_status: 'complete', query_error_code: null, total_rows_is_lower_bound: false });
  const saved: { labelId: string; phase: number; params: Record<string, string | number | boolean> }[] = [];
  const receipts: unknown[] = [];
  let initial: Score[] = [];
  let current: Score[] = [];
  let previousTimes: { id: string; at: Date }[] = [];
  try {
    for (const phase of [0, 1] as const) {
      await test.step(`check ${phase ? 5 : 2}: Session History ${phase ? 'Edit' : 'Annotate'} and Save all six values`, async () => {
        await page.goto(url, { waitUntil: 'domcontentloaded' });
        await expect.poll(rowsInGrid, { timeout: UI_READY }).toEqual(initialGrid);
        await page.locator(`.ag-row[row-id="${sessionId}"] [col-id="session_id"]:visible`).click();
        await expect(drawer).toHaveCount(1, { timeout: UI_READY });
        await expect(drawer.getByRole('tab', { name: 'Session History', exact: true })).toHaveAttribute('aria-selected', 'true', { timeout: UI_READY });
        await drawer.getByRole('button', { name: phase ? 'Edit' : 'Annotate', exact: true }).click();
        // Fill debounced text first, then operate the other five native controls.
        await drawer.getByPlaceholder(`${prefix}-text-input`).fill(String(labels[2].filter[phase]));
        for (const label of labels.filter(l => l.type !== 'text')) {
          const heading = drawer.getByText(`${label.name}*`, { exact: true });
          const control = heading.locator('..').locator('..'); // LabelInput's header -> input container.
          await heading.click();
          if (label.type === 'numeric') await control.getByRole('spinbutton').fill(String(label.filter[phase]));
          else if (label.type === 'star') {
            // StarInput renders one clickable Box per rating in its inner Stack.
            // InlineAnnotator does not pass focused, so numeric hints are absent.
            const stars = control.locator('.MuiStack-root > .MuiStack-root > .MuiBox-root');
            await expect(stars).toHaveCount(Number(label.settings.no_of_stars), { timeout: UI_READY });
            await stars.nth(Number(label.filter[phase]) - 1).click();
          }
          else if (label.type === 'thumbs_up_down') await control.getByText(phase ? 'No' : 'Yes', { exact: true }).click();
          else {
            if (phase && label.settings.multi_choice) for (const choice of (label.old as { selected: string[] }).selected)
              await control.getByText(choice, { exact: true }).click();
            for (const choice of ((phase ? label.next : label.old) as { selected: string[] }).selected)
              await control.getByText(choice, { exact: true }).click();
          }
        }
        const savedResponse = page.waitForResponse(r => new URL(r.url()).pathname === `${SCORES}bulk/`
          && r.request().method() === 'POST', { timeout: UI_READY });
        await drawer.getByRole('button', { name: 'Save', exact: true }).click();
        const response = await savedResponse;
        expect(response.status()).toBe(200);
        const outgoing = response.request().postDataJSON();
        expect(outgoing).toMatchObject({ source_type: 'trace_session', source_id: sessionId });
        expect(outgoing).not.toHaveProperty('queue_item_id');
        expect(outgoing.scores.map((s: { label_id: string; value: Value; score_source: string }) => s)
          .sort((a: { label_id: string }, b: { label_id: string }) => a.label_id.localeCompare(b.label_id)))
          .toEqual(labels.map(l => ({ label_id: l.id, value: phase ? l.next : l.old, notes: '', score_source: 'human' }))
            .sort((a, b) => a.label_id.localeCompare(b.label_id)));
        const body = await response.json() as { result: { scores: ScoreApi[]; errors: unknown[] } };
        expect(body.result.errors).toEqual([]);
        expect(body.result.scores.map(s => s.label_id).sort()).toEqual(labelIds);
        await testInfo.attach(`bulk-${phase}`, { body: JSON.stringify({ outgoing, body }), contentType: 'application/json' });
        current = await readPG();
        expect(current.map(s => s.label_id)).toEqual(labelIds);
        const items = await probe.pg<{ id: string; queue_id: string; source_type: string; trace_session_id: string;
          trace_id: string | null; observation_span_id: string | null; organization_id: string; workspace_id: string }>(
          `SELECT id, queue_id, source_type, trace_session_id, trace_id, observation_span_id, organization_id, workspace_id
           FROM model_hub_queueitem WHERE queue_id = $1 AND deleted = false`, [queue.id]);
        expect(items).toHaveLength(1);
        expect(items[0]).toMatchObject({ queue_id: queue.id, source_type: 'trace_session', trace_session_id: sessionId,
          trace_id: null, observation_span_id: null, organization_id: actor.organizationId, workspace_id: actor.workspaceId });
        for (const fact of current) {
          const label = labels.find(l => l.id === fact.label_id)!;
          expect(fact).toMatchObject({ source_type: 'trace_session', trace_session_id: sessionId,
            trace_id: null, observation_span_id: null, call_execution_id: null, prototype_run_id: null, dataset_row_id: null,
            tracer_project_id: projectId, organization_id: actor.organizationId, workspace_id: actor.workspaceId,
            annotator_id: actor.userId, queue_item_id: items[0].id, score_source: 'human', deleted: 0, has_delete_timestamp: 0,
            value: phase ? label.next : label.old });
          expect(body.result.scores.find(s => s.label_id === label.id)).toMatchObject({ id: fact.id,
            source_type: 'trace_session', source_id: sessionId, queue_item: items[0].id, queue_id: queue.id, value: fact.value });
          expect(fact.value_history.map(h => h.value)).toEqual(phase ? [label.old] : []);
          if (phase) {
            expect(fact.id).toBe(initial.find(s => s.label_id === label.id)!.id);
            expect(Date.parse(fact.value_history[0].at)).toBe(previousTimes.find(s => s.id === fact.id)!.at.getTime());
          }
        }
        if (!phase) {
          initial = current;
          previousTimes = await probe.pg<{ id: string; at: Date }>('SELECT id, updated_at AS at FROM model_hub_score WHERE id = ANY($1)', [initial.map(s => s.id)]);
        }
        await expect(drawer.getByRole('button', { name: 'Edit', exact: true })).toBeVisible({ timeout: UI_READY });
        // ScoreValueChip uses native glyphs/text, truncating text only after 40 characters.
        for (const label of labels) {
          const value = phase ? label.next : label.old;
          const display = 'rating' in value ? '★'.repeat(value.rating) + '☆'.repeat(5 - value.rating)
            : 'selected' in value ? value.selected.join(', ') : 'text' in value
              ? (value.text.length > 40 ? `${value.text.slice(0, 40)}…` : value.text)
              : label.type === 'thumbs_up_down' ? (phase ? '👎' : '👍') : String(value.value);
          await expect(drawer.getByText(label.name, { exact: true }).locator('..').locator('.MuiChip-label'))
            .toHaveText(display, { timeout: UI_READY });
        }
      });
      await test.step(`check ${phase ? 5 : 3}: one exact latest-score CDC barrier`, async () => {
        await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(current);
        expect(await readSource()).toEqual(sourceBefore);
        await testInfo.attach(`score-storage-${phase}`, { body: JSON.stringify({ postgres: current, clickhouse: await readCH() }), contentType: 'application/json' });
      });
      await test.step(`check 4: ${phase ? 'old and corrected' : 'initial'} native catalog/UI/list facts`, async () => {
        for (const label of labels) {
          const metricsParams = { source: 'traces', category: 'annotation_metric', search: label.name,
            project_ids: projectId, cursor_mode: true, page_size: 25 };
          const metadata = await actor.api.post<MetricsPage>(METRICS, metricsParams);
          expect(metadata.result.metrics.map(m => ({ property_id: m.property_id, output_type: m.output_type })))
            .toEqual([{ property_id: `annotation:${label.id}`, output_type: label.type }]);
          const valueParams = { source: 'sessions', property_id: `annotation:${label.id}`, project_ids: projectId, page_size: 25 };
          const values = await actor.api.post<ValuePage>(VALUES, valueParams);
          expect(values.result.values.map(v => v.value).sort()).toEqual([...label.choices].sort());
          expect(values.result.has_more).toBe(false);
          receipts.push({ metricsParams, metadata, valueParams, values });
          for (const wanted of phase ? [0, 1] as const : [0] as const) {
            await page.goto(url, { waitUntil: 'domcontentloaded' });
            await page.getByRole('button', { name: 'Filter', exact: true }).click();
            await page.getByRole('button', { name: 'Property', exact: true }).first().click();
            await page.getByPlaceholder('Search properties...').fill(label.name);
            await page.locator(`[data-filter-property-option="${label.id}"][data-filter-property-category="annotation"]`).click();
            // buildApiFilterFromPanelRow + metricToTraceFilterProperty: exact native wire.
            const filterType = ['numeric', 'star'].includes(label.type) ? 'number'
              : label.type === 'thumbs_up_down' ? 'thumbs' : label.type;
            const expectedConfig = { col_type: 'ANNOTATION', filter_type: filterType,
              filter_op: label.type === 'text' ? 'in' : 'equals',
              filter_value: label.type === 'text' ? [label.filter[wanted]] : label.filter[wanted] };
            const ready = page.waitForResponse(r => {
              if (new URL(r.url()).pathname !== LIST || !['GET', 'POST'].includes(r.request().method())) return false;
              const params = paramsOf(r.request());
              if (typeof params.filters !== 'string') return false;
              const f = (JSON.parse(params.filters) as Filter[]).find(f => f.column_id === label.id);
              return f !== undefined && JSON.stringify(f.filter_config.filter_value) === JSON.stringify(expectedConfig.filter_value);
            }, { timeout: UI_READY });
            if (filterType === 'number') await page.getByPlaceholder('Value', { exact: true }).fill(String(label.filter[wanted]));
            else if (label.type === 'text') await page.getByPlaceholder('Enter text...', { exact: true }).fill(String(label.filter[wanted]));
            else {
              await page.locator(`[data-filter-value-trigger="${label.id}"]`).click();
              await expect.poll(() => page.locator('[data-filter-value-option][role="radio"]')
                .evaluateAll(nodes => nodes.map(n => n.getAttribute('data-filter-value-option')).sort()), { timeout: UI_READY })
                .toEqual([...label.choices].sort());
              await page.locator(`[data-filter-value-option="${label.filter[wanted]}"][role="radio"]`).click();
              await page.keyboard.press('Escape');
            }
            await page.keyboard.press('Escape');
            const response = await ready;
            expect(response.status()).toBe(200);
            const params = paramsOf(response.request());
            expect(params.project_id).toBe(projectId);
            const filter = (JSON.parse(String(params.filters)) as Filter[]).find(f => f.column_id === label.id)!;
            expect(filter).toMatchObject({ property_id: `annotation:${label.id}`, filter_config: expectedConfig });
            const body = await response.json() as ListPage;
            const expected = phase === wanted ? [sessionId] : [];
            complete(body);
            expect(body.result.table.map(r => r.session_id)).toEqual(expected);
            if (expected.length) expect(body.result.table[0]).toMatchObject({ project_id: projectId, total_traces_count: 2 });
            await expect.poll(rowsInGrid, { timeout: UI_READY }).toEqual(expected);
            const replay = await actor.api.post<ListPage>(LIST, params);
            complete(replay);
            expect(replay.result.table.map(r => r.session_id)).toEqual(expected);
            if (phase) saved.push({ labelId: label.id, phase: wanted, params });
            await testInfo.attach(`filter-${label.id}-${phase}-${wanted}`, { body: JSON.stringify({ params, body, replay }), contentType: 'application/json' });
          }
        }
      });
    }
    await test.step('check 7: exact sibling and explicitly scoped tenant negatives with live positive witness', async () => {
      const ownScores = await actor.api.get<{ result: ScoreApi[] }>(`${SCORES}for-source/`, { source_type: 'trace_session', source_id: sessionId });
      expect(ownScores.result.map(s => s.id).sort()).toEqual(initial.map(s => s.id).sort());
      expect((await actor.api.get<{ result: ScoreApi[] }>(`${SCORES}for-source/`, { source_type: 'trace_session', source_id: siblingSessionId })).result).toEqual([]);
      const scopesToCheck = [actor, scopes.ownerB, scopes.withWorkspace(actor, scopes.emptyWorkspace.id)];
      for (const reader of scopesToCheck) {
        const sibling = reader === actor;
        const catalogParams = { source: 'traces', category: 'annotation_metric', search: prefix, cursor_mode: true, page_size: 25,
          ...(sibling ? { project_ids: siblingId } : {}) };
        const catalog = await reader.api.post<MetricsPage>(METRICS, catalogParams);
        expect(catalog.result.metrics).toEqual([]);
        if (!sibling) expect((await reader.api.get<{ result: ScoreApi[] }>(`${SCORES}for-source/`,
          { source_type: 'trace_session', source_id: sessionId })).result).toEqual([]);
        for (const entry of saved.filter(s => s.phase === 1)) {
          const { project_id: _project, ...unscopedParams } = entry.params;
          const params = sibling ? { ...entry.params, project_id: siblingId } : unscopedParams;
          const body = await reader.api.post<ListPage | EmptyWorkspacePage>(LIST, params);
          if (sibling) complete(body as ListPage);
          else expect(body.result.metadata).toEqual({ total_rows: 0 });
          expect(body.result.table).toEqual([]);
          receipts.push({ organizationId: reader.organizationId, workspaceId: reader.workspaceId, params, body });
        }
        receipts.push({ organizationId: reader.organizationId, workspaceId: reader.workspaceId, catalogParams, catalog });
      }
      // Explicitly supported scopes only: this does not qualify auth-header fallback or ingestion-key rebinding.
      expect(await readPG()).toEqual(current);
    });
    await test.step('check 6: public soft deletion, old/new exact exclusion and retained UI reload', async () => {
      expect(saved).toHaveLength(12);
      for (const score of initial) await actor.api.delete(`${SCORES}${score.id}/`);
      const deleted = await readPG();
      expect(deleted).toEqual(current.map(s => ({ ...s, deleted: 1, has_delete_timestamp: 1 })));
      await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(deleted);
      for (const entry of saved) {
        const body = await actor.api.post<ListPage>(LIST, entry.params);
        complete(body);
        expect(body.result.table).toEqual([]);
        receipts.push({ deletedFilter: entry, body });
      }
      // ObserveHeader's Reload data refreshes Session-grid without discarding useState extraFilters.
      const retained = saved[saved.length - 1];
      const reload = page.getByRole('button', { name: 'Reload data', exact: true });
      await expect(reload).toBeEnabled({ timeout: UI_READY });
      const refreshed = page.waitForResponse(r => new URL(r.url()).pathname === LIST
        && ['GET', 'POST'].includes(r.request().method())
        && paramsOf(r.request()).filters === retained.params.filters, { timeout: UI_READY });
      await reload.click();
      const response = await refreshed;
      expect(response.status()).toBe(200);
      const body = await response.json() as ListPage;
      complete(body);
      expect(body.result.table).toEqual([]);
      await expect.poll(rowsInGrid, { timeout: UI_READY }).toEqual([]);
      expect(await readSource()).toEqual(sourceBefore);
      expect(await readSessions()).toEqual(sessions);
      await testInfo.attach('deleted-score-state', { body: JSON.stringify({ postgres: deleted, clickhouse: await readCH(), refreshed: body }), contentType: 'application/json' });
    });
  } finally {
    await testInfo.attach('catalog-and-scope-receipts', { body: JSON.stringify(receipts), contentType: 'application/json' });
    await context.close(); // scopeActors/scopeProbe fixtures dispose their own clients/probe.
    await Promise.all(graphReads);
    await testInfo.attach('session-graph-responses', { body: JSON.stringify(graphResponses), contentType: 'application/json' });
  }
});
