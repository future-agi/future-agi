import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/serializers/scores.py; AnnotationSidebarContent's native save call.
const SCORES = '/model-hub/scores/';
// AnnotationQueueViewSet default/add-label actions and label create serializer.
const QUEUES = '/model-hub/annotation-queues/';
const LABELS = '/model-hub/annotations-labels/';
// TraceGrid/SpanGrid.readQuery and TraceFilterPanel's catalog adapters.
const LISTS = { trace: '/tracer/trace/list_traces_of_session/',
  span: '/tracer/observation-span/list_spans_observe/' };
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
const UI_READY = 60_000;
type Value = { value: number | string } | { rating: number } | { text: string } | { selected: string[] };
type Label = { id: string; name: string; type: string; settings: Record<string, unknown>;
  old: Value; next: Value; filter: [string | number, string | number]; choices: string[] };
type Score = { id: string; label_id: string; source_type: string; trace_id: string;
  observation_span_id: string | null; tracer_project_id: string; organization_id: string;
  workspace_id: string; annotator_id: string; queue_item_id: string; score_source: string;
  value: Value; value_history: { value: Value; at: string }[]; deleted: number };
type Filter = { column_id: string; property_id?: string; filter_config: {
  col_type: string; filter_type: string; filter_op: string; filter_value: unknown } };
type ListPage = { result: { table: { trace_id: string; span_id?: string }[]; metadata: {
  has_more: boolean; next_cursor: string | null; query_complete: boolean;
  query_status: string; query_error_code: string | null; total_rows_is_lower_bound: boolean;
} } };

for (const targetKind of ['trace', 'span'] as const) {
  const flowId = targetKind === 'trace' ? 'ANNOT-E2E-002' : 'ANNOT-E2E-003';
  test(`${flowId}: corrected ${targetKind} annotations change exact filter matches`, {
    tag: ['@flow'],
    annotation: flowAnnotation({ id: flowId, area: 'annotations',
      userGoal: `Submit and correct typed human ${targetKind} annotations and find only ${targetKind}s with the current score`,
      steps: ['ingest two traces and configure six native label controls',
        `submit numeric, star, text, thumbs and single/multiple choices on the selected ${targetKind}`,
        'select every annotation in the real Observe filter',
        'correct the same scores in the drawer and compare old/new matches',
        'soft-delete only the synthetic scores through the public endpoint and verify exclusion'],
      backendChecks: [`UI-submitted scores have exact label, ${targetKind}, annotator, queue and tenant identities in Postgres`,
        'latest ClickHouse scores retain every typed value and exact Postgres identity',
        'configured catalog choices and real UI/list filters agree for every annotation type',
        'UI corrections preserve score IDs and append the old typed value to latest PG/CH history',
        `old and soft-deleted scores cannot match while current corrected scores match only the annotated ${targetKind}`],
    }),
  }, async ({ page, request, actor, probe }, testInfo) => {
    // Approved ANNOT002 780s + a separate 180s CDC barrier for public soft deletion.
    test.setTimeout(960_000);
    page.setDefaultTimeout(UI_READY);
    const prefix = `e2e-annot${targetKind === 'trace' ? 2 : 3}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const isSpan = targetKind === 'span';
    const listPath = LISTS[targetKind];
    const rowId = isSpan ? 'span_id' : 'trace_id';
    const seeds: Awaited<ReturnType<typeof sendTrace>>[] = [];
    for (const kind of ['annotated', 'unannotated']) seeds.push(await sendTrace(request, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: prefix, rootName: `${prefix}-${kind}`, childName: `${prefix}-${kind}-child`,
      resourceAttributes: { project_type: 'observe' },
    }));
    const [target, other] = seeds;
    const sourceType = isSpan ? 'observation_span' : 'trace';
    const sourceId = isSpan ? target.spanIds[1] : target.traceId;
    const targetName = `${prefix}-annotated${isSpan ? '-child' : ''}`;
    await testInfo.attach('source-traces', { body: JSON.stringify(seeds), contentType: 'application/json' });
    await expect.poll(() => probe.ch('SELECT id FROM traces FINAL WHERE id IN ({a:UUID}, {b:UUID}) ORDER BY toString(id)',
      { a: target.traceId, b: other.traceId }), POLL.SPAN_VISIBLE)
      .toEqual(seeds.map(s => ({ id: s.traceId })).sort((a, b) => a.id.localeCompare(b.id)));
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND workspace_id = $3',
      [prefix, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    const spanParams = { p: projectId, a: target.traceId, b: other.traceId };
    await expect.poll(() => probe.ch('SELECT id FROM spans FINAL WHERE project_id = {p:UUID} AND trace_id IN ({a:String}, {b:String}) ORDER BY id',
      spanParams), POLL.SPAN_VISIBLE).toEqual(seeds.flatMap(s => s.spanIds).sort().map(id => ({ id })));
    const readSource = () => probe.ch('SELECT * FROM spans FINAL WHERE project_id = {p:UUID} AND trace_id IN ({a:String}, {b:String}) ORDER BY id', spanParams);
    const sourceBefore = await readSource();
    await testInfo.attach('source-spans-before', { body: JSON.stringify(sourceBefore), contentType: 'application/json' });
    const users = await probe.pg<{ id: string }>('SELECT id FROM accounts_user WHERE email = $1', [actor.email]);
    expect(users).toHaveLength(1);
    const annotatorId = users[0].id;
    const labels: Label[] = [];
    // LabelInput and AnnotationsLabels.validate_settings require these complete
    // native objects. Automatic annotation stays off; no provider is invoked.
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
          settings: { multi_choice: multi, options: options.map(label => ({ label })),
            auto_annotate: false, rule_prompt: '', strategy: null },
          old: { selected: multi ? [options[0], options[2]] : [options[0]] },
          next: { selected: [options[1]] }, filter: [options[0], options[1]] as [string, string], choices: options };
      }),
    ];
    for (const definition of definitions) {
      const created = await actor.api.post<{ result: { id: string } }>(LABELS, {
        name: definition.name, type: definition.type, project: projectId,
        settings: definition.settings, allow_notes: false,
      });
      labels.push({ ...definition, id: created.result.id });
    }
    // Default queues resolve trace-first. An explicit span queue pins its child
    // QueueItem, so the native drawer must submit observation_span, not trace.
    // AnnotationQueueSerializer/create return a bare queue, unlike the default action.
    let queue: { id: string; name: string };
    let spanQueueItemId: string | undefined;
    if (isSpan) {
      queue = await actor.api.post(QUEUES, { name: `${prefix}-queue`, label_ids: labels.map(l => l.id) });
      await actor.api.post(`${QUEUES}${queue.id}/update-status/`, { status: 'active' });
      const added = await actor.api.post<{ result: { added: number; duplicates: number; errors: unknown[] } }>(
        `${QUEUES}${queue.id}/items/add-items/`, { project_id: projectId,
          items: [{ source_type: sourceType, source_id: sourceId }] });
      expect(added.result).toMatchObject({ added: 1, duplicates: 0, errors: [] });
      const items = await probe.pg<{ id: string; source_type: string; observation_span_id: string }>(
        'SELECT id, source_type, observation_span_id FROM model_hub_queueitem WHERE queue_id = $1 AND deleted = false', [queue.id]);
      expect(items).toHaveLength(1);
      expect(items[0]).toMatchObject({ source_type: sourceType, observation_span_id: sourceId });
      spanQueueItemId = items[0].id;
    } else {
      const created = await actor.api.post<{ result: { queue: { id: string; name: string } } }>(
        `${QUEUES}get-or-create-default/`, { project_id: projectId });
      queue = created.result.queue;
      for (const label of labels) await actor.api.post(`${QUEUES}${queue.id}/add-label/`, { label_id: label.id });
    }
    const queueId = queue.id;
    await testInfo.attach('annotation-identities', { body: JSON.stringify({ projectId, queueId, labels,
      spanQueueItemId, sourceType, sourceId, annotatorId,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId }), contentType: 'application/json' });
    const fields = `id, label_id, source_type, trace_id, tracer_project_id,
      organization_id, workspace_id, annotator_id, queue_item_id, score_source`;
    const readPG = () => probe.pg<Score>(`SELECT ${fields}, observation_span_id, value, value_history, deleted::integer AS deleted
      FROM model_hub_score WHERE label_id = ANY($1) ORDER BY label_id::text`, [labels.map(l => l.id)]);
    const readCH = async () => (await probe.ch<Omit<Score, 'value' | 'value_history'> & { value: string; value_history: string }>(
      // Existing PeerDB-created String mirrors encode absent span IDs as ''. The
      // bootstrap Nullable(String) mirror uses NULL. Neither identifies a span.
      `SELECT ${fields}, nullIf(observation_span_id, '') AS observation_span_id,
         value, value_history, toUInt8(deleted) AS deleted FROM model_hub_score FINAL
       WHERE label_id IN (${labels.map((_, i) => `{label${i}:UUID}`).join(', ')})
         AND _peerdb_is_deleted = 0 ORDER BY toString(label_id)`,
      Object.fromEntries(labels.map((l, i) => [`label${i}`, l.id]))))
      .map(s => ({ ...s, value: JSON.parse(s.value), value_history: JSON.parse(s.value_history) }));
    expect(await readPG()).toEqual([]);
    const url = `/dashboard/observe/${projectId}/llm-tracing?tab=traces&viewMode=%22graph%22${isSpan ? '&selectedTab=spans' : ''}`;
    // Span row IDs are physical tuples; assert unique minted names in the UI and
    // independent span IDs in its captured API. TraceGrid uses plain trace IDs.
    const cells = page.locator(`.clean-data-table:visible .ag-row [col-id="${isSpan ? 'span_name' : 'trace_name'}"]`);
    const rowsInGrid = () => cells.evaluateAll((nodes, span) => nodes.map(c => span
      ? c.textContent!.trim() : c.closest('.ag-row')!.getAttribute('row-id')).sort(), isSpan);
    const initialGrid = isSpan ? ['annotated', 'unannotated'].flatMap(kind =>
      [`${prefix}-${kind}`, `${prefix}-${kind}-child`]).sort() : seeds.map(s => s.traceId).sort();
    let initial: Score[] = [];
    const savedFilters: { labelId: string; filter: Filter }[] = [];
    const complete = (body: ListPage) => expect(body.result.metadata).toMatchObject({
      has_more: false, next_cursor: null, query_complete: true, query_status: 'complete',
      query_error_code: null, total_rows_is_lower_bound: false,
    });

    for (const phase of [0, 1] as const) {
      await test.step(`UI: ${phase ? 'correct' : 'submit'} all six native controls`, async () => {
        await page.goto(url, { waitUntil: 'domcontentloaded' });
        await expect.poll(rowsInGrid, { timeout: UI_READY }).toEqual(initialGrid);
        await cells.getByText(targetName, { exact: true }).click();
        await page.getByRole('button', { name: 'Actions', exact: true }).click();
        await page.getByRole('menuitem', { name: 'Annotate', exact: true }).click();
        const drawer = page.locator('.MuiDrawer-paper:visible').filter({ has: page.getByText(queue.name, { exact: true }) });
        await expect(drawer).toHaveCount(1, { timeout: UI_READY });
        await drawer.getByPlaceholder(`${prefix}-text-input`).fill(String(labels[2].filter[phase]));
        // LabelInput renders the label text plus its required asterisk in the
        // header; its parent header and parent input box are stable source structure.
        for (const label of labels.filter(l => l.type !== 'text')) {
          const heading = drawer.getByText(`${label.name}*`, { exact: true });
          const control = heading.locator('..').locator('..');
          const value = phase ? label.next : label.old;
          await heading.click();
          if (label.type === 'numeric') await control.getByRole('spinbutton').fill(String(label.filter[phase]));
          // StarInput's rating hints live inside its MUI Stack; the label header
          // separately shows the label's ordinal (also "2" for this fixture).
          else if (label.type === 'star') await control.locator('.MuiStack-root')
            .getByText(String(label.filter[phase]), { exact: true }).click();
          else if (label.type === 'thumbs_up_down') await control.getByText(phase ? 'No' : 'Yes', { exact: true }).click();
          else {
            if (phase && label.settings.multi_choice) for (const old of (label.old as { selected: string[] }).selected)
              await control.getByText(old, { exact: true }).click();
            for (const choice of (value as { selected: string[] }).selected) await control.getByText(choice, { exact: true }).click();
          }
        }
        const saved = page.waitForResponse(r => new URL(r.url()).pathname === `${SCORES}bulk/`
          && r.request().method() === 'POST', { timeout: UI_READY });
        await drawer.getByRole('button', { name: /^(Save|Update)/ }).click();
        const response = await saved;
        expect(response.status()).toBe(200);
        const outgoing = response.request().postDataJSON();
        expect(outgoing).toMatchObject({ source_type: sourceType, source_id: sourceId });
        if (isSpan) expect(outgoing.queue_item_id).toBe(spanQueueItemId);
        expect(outgoing.scores.map((s: { label_id: string; value: Value }) => ({ id: s.label_id, value: s.value }))
          .sort((a: { id: string }, b: { id: string }) => a.id.localeCompare(b.id)))
          .toEqual(labels.map(l => ({ id: l.id, value: phase ? l.next : l.old })).sort((a, b) => a.id.localeCompare(b.id)));
        const body = await response.json();
        expect(body.result.errors).toEqual([]);
        expect(body.result.scores).toHaveLength(labels.length);
        await testInfo.attach(`bulk-${phase}`, { body: JSON.stringify({ outgoing, body }), contentType: 'application/json' });
      });

      const facts = await readPG();
      await test.step(`backend check ${phase ? 4 : 1}: scoped current score and history`, async () => {
        expect(facts).toHaveLength(labels.length);
        for (const fact of facts) {
          const label = labels.find(l => l.id === fact.label_id)!;
          expect(fact).toMatchObject({ source_type: sourceType, trace_id: target.traceId,
            observation_span_id: isSpan ? sourceId : null, tracer_project_id: projectId, organization_id: actor.organizationId,
            workspace_id: actor.workspaceId, annotator_id: annotatorId, score_source: 'human', deleted: 0,
            value: phase ? label.next : label.old });
          expect(fact.queue_item_id).toMatch(/^[0-9a-f-]{36}$/);
          if (isSpan) expect(fact.queue_item_id).toBe(spanQueueItemId);
          expect(fact.value_history.map(h => h.value)).toEqual(phase ? [label.old] : []);
          for (const history of fact.value_history) expect(Number.isFinite(Date.parse(history.at))).toBe(true);
          if (phase) expect(fact.id).toBe(initial.find(s => s.label_id === label.id)!.id);
        }
        if (!phase) initial = facts;
      });
      await test.step(`backend check ${phase ? 4 : 2}: exact latest CDC score`, async () => {
        await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(facts);
        await testInfo.attach(`score-storage-${phase}`, { body: JSON.stringify({ postgres: facts, clickhouse: await readCH() }), contentType: 'application/json' });
      });

      await test.step(`backend check ${phase ? 5 : 3}: current catalog and native filter parity`, async () => {
        for (const label of labels) {
          const metadata = await actor.api.post<{ result: { metrics: { property_id: string; output_type: string }[] } }>(METRICS,
            { source: isSpan ? 'spans' : 'traces', category: 'annotation_metric', search: label.name, project_ids: projectId, cursor_mode: true, page_size: 25 });
          expect(metadata.result.metrics.map(m => m.property_id)).toEqual([`annotation:${label.id}`]);
          const values = await actor.api.post<{ result: { values: { value: string }[] } }>(VALUES,
            { source: 'traces', property_id: `annotation:${label.id}`, project_ids: projectId, page_size: 25 });
          expect(values.result.values.map(v => v.value).sort()).toEqual([...label.choices].sort());
          for (const wanted of phase ? [0, 1] as const : [0] as const) {
            await page.goto(url, { waitUntil: 'domcontentloaded' });
            await page.getByRole('button', { name: 'Filter', exact: true }).click();
            await page.getByRole('button', { name: 'Property', exact: true }).first().click();
            await page.getByPlaceholder('Search properties...').fill(label.name);
            await page.locator(`[data-filter-property-option="${label.id}"][data-filter-property-category="annotation"]`).click();
            const ready = page.waitForResponse(r => {
              if (new URL(r.url()).pathname !== listPath || r.request().method() === 'OPTIONS') return false;
              const params = r.request().method() === 'POST' ? r.request().postDataJSON() : Object.fromEntries(new URL(r.url()).searchParams);
              const filters: Filter[] = JSON.parse(params.filters);
              const value = filters.find(f => f.column_id === label.id)?.filter_config.filter_value;
              return value !== undefined && JSON.stringify(value).includes(String(label.filter[wanted]));
            }, { timeout: UI_READY });
            if (label.type === 'star' || label.type === 'numeric') await page.getByPlaceholder('Value', { exact: true }).fill(String(label.filter[wanted]));
            else if (label.type === 'text') await page.getByPlaceholder('Enter text...', { exact: true }).fill(String(label.filter[wanted]));
            else {
              await page.locator(`[data-filter-value-trigger="${label.id}"]`).click();
              // CATEGORICAL_OPS/THUMBS_OPS default to single-value "is";
              // the displayed thumbs label differs from its stable API value.
              await page.locator(`[data-filter-value-option="${label.filter[wanted]}"][role="radio"]`).click();
              await page.keyboard.press('Escape');
            }
            await page.keyboard.press('Escape');
            const response = await ready;
            expect(response.status()).toBe(200);
            const body = await response.json() as ListPage;
            const params = response.request().method() === 'POST' ? response.request().postDataJSON()
              : Object.fromEntries(new URL(response.url()).searchParams);
            const filters: Filter[] = JSON.parse(params.filters);
            const filter = filters.find(f => f.column_id === label.id)!;
            expect(filter.filter_config.col_type).toBe('ANNOTATION');
            expect(filter.property_id).toBe(`annotation:${label.id}`);
            const expected = phase === wanted ? [sourceId] : [];
            expect(body.result.table.map(r => r[rowId])).toEqual(expected);
            complete(body);
            await expect.poll(rowsInGrid, { timeout: UI_READY }).toEqual(phase === wanted ? [isSpan ? targetName : sourceId] : []);
            const replay = await actor.api.post<ListPage>(listPath, params);
            complete(replay);
            expect(replay.result.table.map(r => r[rowId])).toEqual(expected);
            if (phase === 1 && wanted === 1) savedFilters.push({ labelId: label.id, filter });
            await testInfo.attach(`filter-${label.type}-${label.id}-${phase}-${wanted}`,
              { body: JSON.stringify({ params, body, replay }), contentType: 'application/json' });
          }
        }
      });
    }

    await test.step('backend check 5: public score soft deletion excludes all corrected matches', async () => {
      for (const score of initial) await actor.api.delete(`${SCORES}${score.id}/`);
      const deleted = await readPG();
      expect(deleted.map(s => ({ id: s.id, deleted: s.deleted })))
        .toEqual(initial.map(s => ({ id: s.id, deleted: 1 })));
      await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(deleted);
      for (const { filter } of savedFilters) {
        const result = await actor.api.post<ListPage>(listPath, { project_id: projectId, page_size: 25, cursor_mode: true,
          filters: JSON.stringify([filter]) });
        complete(result);
        expect(result.result.table).toEqual([]);
      }
      // Annotation scores are derived data; source spans/traces must be untouched.
      expect(await readSource()).toEqual(sourceBefore);
      expect(await probe.ch('SELECT id FROM traces FINAL WHERE id IN ({a:UUID}, {b:UUID}) ORDER BY toString(id)',
        { a: target.traceId, b: other.traceId }))
        .toEqual(seeds.map(s => ({ id: s.traceId })).sort((a, b) => a.id.localeCompare(b.id)));
      await testInfo.attach('deleted-score-state', { body: JSON.stringify(deleted), contentType: 'application/json' });
    });
  });
}
