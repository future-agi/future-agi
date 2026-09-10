import { request, type BrowserContext, type Response } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { authInitScript } from '../../lib/auth';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// DashboardMetricsCatalogQuerySerializer / DashboardFilterValuesQuerySerializer.
const METRICS = '/tracer/dashboard/metrics/';
const VALUES = '/tracer/dashboard/filter_values/';
// ObserveListView, SpanGrid and WorkspaceContext.switchWorkspace.
const PROJECTS = '/tracer/project/list_projects/';
const SPANS = '/tracer/observation-span/list_spans_observe/';
const SWITCH = '/accounts/workspace/switch/';
// accounts/serializers/org_api_key.py; views/keys.py; tfc/utils/signals.py.
const MINT = '/accounts/key/generate_secret_key/';
const DISABLE = '/accounts/key/disable_key/';
// CreateDatasetFromLocalFileRequestSerializer; fixture also used by DATA-E2E-001.
const UPLOAD = '/model-hub/develops/create-dataset-from-local-file/';
const CSV = readFileSync(new URL('../../fixtures/catalog-regions.csv', import.meta.url));
const CHOICES = ['catalog-east', 'catalog-west'];
const UI_READY = 60_000; // README: browser first-paint budget.

type Wire = Record<string, string | number | boolean>;
type Created = { result: { id: string } };
type Metric = { property_id: string; name: string; display_name: string };
type Option = { value: string; type?: string; label?: string };
type CatalogPage = { result: { metrics: Metric[]; next_cursor: string | null; has_more: boolean } };
type ValuePage = { result: { values: Option[]; next_cursor: string | null; has_more: boolean } };
type Cell = { id: string; row_id: string; value: string };
type Definition = { id: string; name: string; templateId?: string };
type Seed = { label: string; owner: ScopeActor; reader: ScopeActor; name: string;
  projectId: string; datasetId: string; columnId: string; cells: Cell[];
  traces: SeededTrace[]; configs: Definition[]; labels: Definition[] };
type Family = { kind: 'span' | 'dataset' | 'eval' | 'annotation'; metrics: Wire; values: Wire;
  ids: string[]; uiIds: string[]; search: string };

test('OBS-E2E-009: catalog permissions follow explicit scope and membership removal', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-009', area: 'observe',
    userGoal: 'Discover only currently authorized catalog properties and choices, including after workspace membership removal',
    steps: ['inspect a genuinely empty workspace',
      'inspect four catalog families in two sibling projects and their datasets',
      'switch to the second workspace and inspect its four families',
      'consume real pages and reject cross-scope cursors and foreign identities',
      'remove workspace membership through the public API and reload'],
    backendChecks: [
      'actors, memberships and initial empty workspace match the explicit scopes',
      'seeded span and native source identities belong to their exact organization/workspace/project or dataset',
      'each authorized catalog and UI picker exposes exactly the current source IDs and typed choices with no foreign IDs',
      'real property and value cursors reject a different authorized scope without hiding valid first-page results',
      'public membership removal denies fresh and cursor requests for the revoked workspace and removes its visible catalog access',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // Approved shared lifecycle: 60 provisioning/import + 15 spans + 180 CDC +
  // 60 catalog readiness + six bounded UI_READY stages + 45 headroom = 720s.
  test.setTimeout(720_000);
  const prefix = `e2e-obs9-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const customKeys = [`${prefix}-region-a`, `${prefix}-region-b`];
  const witness = `${prefix}-foreign-only`;
  const req = await request.newContext();
  let context: BrowserContext | undefined;
  const receipts: unknown[] = [];
  const keys: { actor: ScopeActor; id: string; enabled: boolean }[] = [];
  const savedPages: { family: Family; metricCursor: string; valueCursor: string }[] = [];
  const a1 = scopes.ownerA;
  const a2 = scopes.withWorkspace(a1, scopes.emptyWorkspace.id);
  const memberA2 = scopes.withWorkspace(scopes.member, a2.workspaceId);
  const seeds: Seed[] = [
    { label: 'a1p1', owner: a1, reader: scopes.member },
    { label: 'a1p2', owner: a1, reader: scopes.member },
    { label: 'a2p3', owner: a2, reader: memberA2 },
    { label: 'b1p4', owner: scopes.ownerB, reader: scopes.ownerB },
  ].map(s => ({ ...s, name: `${prefix}-${s.label}`, projectId: '', datasetId: '', columnId: '',
    cells: [], traces: [], configs: [], labels: [] }));

  // Local assertion composition only: identities/clients/probes come from H1.
  const read = async <T>(actor: ScopeActor, path: string, body: Wire): Promise<T> => {
    const response = await scopes.send<T>(actor, 'POST', path, body);
    receipts.push({ actor: actor.userId, workspace: actor.workspaceId, path, request: body, ...response });
    expect(response.status, `${path} in ${actor.workspaceId}`).toBe(200);
    return response.body;
  };
  const headers = async (response: Response, actor: ScopeActor) => {
    const actual = await response.request().allHeaders();
    expect(actual['x-organization-id']).toBe(actor.organizationId);
    expect(actual['x-workspace-id']).toBe(actor.workspaceId);
    expect(actual.authorization).toBe(`Bearer ${actor.tokens.access}`);
    receipts.push({ ui: new URL(response.url()).pathname, status: response.status(),
      organizationId: actual['x-organization-id'], workspaceId: actual['x-workspace-id'], authenticated: true });
  };
  const families = (s: Seed): Family[] => [
    { kind: 'span', metrics: { source: 'spans', category: 'custom_attribute', project_ids: s.projectId,
      search: `${prefix}-region`, cursor_mode: true, page_size: 1 },
    values: { property_id: `custom_attribute:${customKeys[0]}`, source: 'spans', project_ids: s.projectId, page_size: 1 },
    ids: customKeys.map(k => `custom_attribute:${k}`), uiIds: customKeys, search: `${prefix}-region` },
    { kind: 'dataset', metrics: { source: 'datasets', category: 'custom_column', search: 'region', cursor_mode: true, page_size: 1 },
      values: { property_id: `dataset_column:${s.columnId}`, source: 'dataset_column', dataset_id: s.datasetId, page_size: 1 },
      // Dataset discovery is workspace-wide: both A1 datasets are legitimately visible.
      ids: seeds.filter(d => d.owner.workspaceId === s.owner.workspaceId).map(d => `dataset_column:${d.columnId}`),
      uiIds: [s.columnId], search: 'region' },
    { kind: 'eval', metrics: { source: 'traces', category: 'eval_metric', per_eval_config: true,
      project_ids: s.projectId, search: `${prefix}-eval`, cursor_mode: true, page_size: 1 },
    values: { property_id: `eval_config:${s.configs[0]?.id}`, source: 'traces', project_ids: s.projectId, page_size: 1 },
    ids: s.configs.map(d => `eval_config:${d.id}`), uiIds: s.configs.map(d => d.id), search: `${prefix}-eval` },
    { kind: 'annotation', metrics: { source: 'traces', category: 'annotation_metric', project_ids: s.projectId,
      search: `${prefix}-annotation`, cursor_mode: true, page_size: 1 },
    values: { property_id: `annotation:${s.labels[0]?.id}`, source: 'traces', project_ids: s.projectId, page_size: 1 },
    ids: s.labels.map(d => `annotation:${d.id}`), uiIds: s.labels.map(d => d.id), search: `${prefix}-annotation` },
  ];
  try {
    await test.step('check 1: public roles and genuinely empty A2', async () => {
      const memberships = await probe.pg<{ workspace_id: string; level: number }>(
        `SELECT workspace_id, level FROM accounts_workspacemembership
         WHERE user_id = $1 AND is_active AND NOT deleted ORDER BY workspace_id::text`, [scopes.member.userId]);
      expect(memberships).toEqual([a1.workspaceId, a2.workspaceId].sort().map(workspace_id => ({ workspace_id, level: 3 })));
      expect(await probe.pg('SELECT level FROM accounts_workspacemembership WHERE user_id = $1 AND workspace_id = $2 AND is_active AND NOT deleted',
        [scopes.viewer.userId, a1.workspaceId])).toEqual([{ level: 1 }]);
      expect(a1.organizationId).not.toBe(scopes.ownerB.organizationId);
      for (const [source, category, search] of [['spans', 'custom_attribute', prefix],
        ['datasets', 'custom_column', 'region'], ['traces', 'eval_metric', prefix], ['traces', 'annotation_metric', prefix]]) {
        const empty = await read<CatalogPage>(memberA2, METRICS, { source, category, search, cursor_mode: true, page_size: 25 });
        expect(empty.result.metrics).toEqual([]);
      }
      context = await scopes.openContext(browser, memberA2);
      const page = await context.newPage();
      const projects = page.waitForResponse(r => new URL(r.url()).pathname === PROJECTS, { timeout: UI_READY });
      await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
      const response = await projects;
      await headers(response, memberA2);
      expect(response.status()).toBe(200);
      expect((await response.json()).result.table).toEqual([]);
      await expect(page.getByText('Welcome to Observe', { exact: true })).toBeVisible({ timeout: UI_READY });
      await testInfo.attach('empty-workspace', { body: await page.screenshot(), contentType: 'image/png' });
      await context.close(); context = undefined;
    }, { timeout: UI_READY });

    // Public key mint is scoped by request context in the global post_save
    // signal. Switching a JWT does NOT rebind the existing organization key.
    const originalKeys = await probe.pg('SELECT id, workspace_id, enabled, deleted FROM accounts_orgapikey WHERE organization_id = ANY($1) ORDER BY id::text',
      [[a1.organizationId, scopes.ownerB.organizationId]]);
    for (const owner of [a1, a2, scopes.ownerB]) {
      const minted = await scopes.send<{ result: { key_id: string; api_key: string; secret_key: string } }>(owner, 'POST', MINT,
        { key_name: `${prefix}-${owner.workspaceId.slice(0, 8)}` });
      expect(minted.status).toBe(200);
      const key = minted.body.result;
      keys.push({ actor: owner, id: key.key_id, enabled: true });
      expect(await probe.pg('SELECT organization_id, workspace_id, user_id, type, enabled FROM accounts_orgapikey WHERE id = $1', [key.key_id]))
        .toEqual([{ organization_id: owner.organizationId, workspace_id: owner.workspaceId, user_id: owner.userId, type: 'user', enabled: true }]);
      for (const s of seeds.filter(item => item.owner.workspaceId === owner.workspaceId)) {
        for (const [i, value] of CHOICES.entries()) {
          s.traces.push(await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: key.api_key, secretKey: key.secret_key,
            projectName: s.name, rootName: `${s.name}-${i}`, rootAttributes: { [customKeys[0]]: value, [customKeys[1]]: value,
              ...(s.label === 'b1p4' ? { [witness]: value } : {}) } }));
        }
      }
    }
    await testInfo.attach('seeded-traces', { body: JSON.stringify(seeds.map(s => ({ name: s.name, traces: s.traces }))), contentType: 'application/json' });
    await expect.poll(async () => (await probe.ch<{ id: string }>(
      'SELECT id FROM spans FINAL WHERE trace_id IN (SELECT arrayJoin(JSONExtract({ids:String}, \'Array(String)\'))) ORDER BY id',
      { ids: JSON.stringify(seeds.flatMap(s => s.traces.map(t => t.traceId))) })).map(r => r.id), POLL.SPAN_VISIBLE)
      .toEqual(seeds.flatMap(s => s.traces.flatMap(t => t.spanIds)).sort());

    for (const s of seeds) {
      const project = await probe.pg<{ id: string; organization_id: string; workspace_id: string }>(
        'SELECT id, organization_id, workspace_id FROM tracer_project WHERE name = $1 AND NOT deleted', [s.name]);
      expect(project).toHaveLength(1);
      s.projectId = project[0].id;
      expect(project[0]).toEqual({ id: s.projectId, organization_id: s.owner.organizationId, workspace_id: s.owner.workspaceId });
      const upload = await req.post(`${E2E.apiUrl}${UPLOAD}`, { headers: scopes.headers(s.owner), multipart: {
        file: { name: 'catalog-regions.csv', mimeType: 'text/csv', buffer: CSV }, new_dataset_name: s.name, model_type: 'GenerativeLLM' } });
      expect(upload.status(), await upload.text()).toBe(200);
      s.datasetId = (await upload.json()).result.dataset_id;
      receipts.push({ path: UPLOAD, status: upload.status(), datasetId: s.datasetId, workspaceId: s.owner.workspaceId });
      // EvalTemplateCreateV2RequestSerializer and graph-metric-scoping.spec.ts:
      // definitions only; code is NEVER executed, and no model/provider is created.
      for (const suffix of ['a', 'b']) {
        const template = await s.owner.api.post<Created>('/model-hub/eval-templates/create-v2/', {
          name: `${s.name}-${suffix}`, eval_type: 'code', code: 'def main(**kwargs):\n    return True',
          code_language: 'python', output_type: 'deterministic', choice_scores: { 'catalog-east': 0, 'catalog-west': 1 } });
        // separate_evals.py:3065 resets code-create choices to [] even when
        // choice_scores was accepted. The supported definition-only PUT at
        // :3535 persists the vocabulary; retain this create-path limitation.
        const choiceUpdate = await scopes.send(s.owner, 'PUT', `/model-hub/eval-templates/${template.result.id}/update/`, {
          output_type: 'deterministic', choice_scores: { 'catalog-east': 0, 'catalog-west': 1 } });
        expect(choiceUpdate.status).toBe(200);
        receipts.push({ path: `/model-hub/eval-templates/${template.result.id}/update/`,
          status: choiceUpdate.status, templateId: template.result.id, definitionOnly: true });
        const name = `${prefix}-eval-${suffix}`;
        const config = await s.owner.api.post<Created>('/tracer/custom-eval-config/', { project: s.projectId,
          eval_template: template.result.id, name, config: { mapping: {} }, mapping: {}, error_localizer: false });
        s.configs.push({ id: config.result.id, name, templateId: template.result.id });
        const labelName = `${prefix}-annotation-${suffix}`;
        const label = await s.owner.api.post<Created>('/model-hub/annotations-labels/', { name: labelName,
          type: 'categorical', project: s.projectId, settings: { options: CHOICES.map(label => ({ label })),
            multi_choice: false, rule_prompt: '', auto_annotate: false, strategy: null } });
        s.labels.push({ id: label.result.id, name: labelName });
      }
    }
    // Release task-owned keys as soon as ingestion is done; no original key changes.
    for (const key of keys) {
      expect((await scopes.send(key.actor, 'POST', DISABLE, { key_id: key.id })).status).toBe(200);
      key.enabled = false;
    }
    expect(await probe.pg('SELECT id, enabled FROM accounts_orgapikey WHERE id = ANY($1) ORDER BY id::text', [keys.map(k => k.id)]))
      .toEqual(keys.map(k => k.id).sort().map(id => ({ id, enabled: false })));
    expect(await probe.pg('SELECT id, workspace_id, enabled, deleted FROM accounts_orgapikey WHERE organization_id = ANY($1) AND NOT (id = ANY($2)) ORDER BY id::text',
      [[a1.organizationId, scopes.ownerB.organizationId], keys.map(k => k.id)])).toEqual(originalKeys);

    await test.step('check 2: exact source identities and real native CDC', async () => {
      await expect.poll(async () => (await probe.pg<{ dataset_id: string; n: string }>(
        'SELECT dataset_id, count(*) AS n FROM model_hub_cell WHERE dataset_id = ANY($1) AND NOT deleted GROUP BY dataset_id ORDER BY dataset_id::text',
        [seeds.map(s => s.datasetId)])), POLL.ASYNC_JOB)
        .toEqual(seeds.map(s => s.datasetId).sort().map(dataset_id => ({ dataset_id, n: '6' })));
      for (const s of seeds) {
        expect(await probe.pg('SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted', [s.datasetId]))
          .toEqual([{ name: s.name, organization_id: s.owner.organizationId, workspace_id: s.owner.workspaceId }]);
        const columns = await probe.pg<{ id: string; name: string; data_type: string }>(
          'SELECT id, name, data_type FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name', [s.datasetId]);
        expect(columns.map(({ name, data_type }) => ({ name, data_type })))
          .toEqual([{ name: 'quantity', data_type: 'integer' }, { name: 'region', data_type: 'text' }]);
        s.columnId = columns.find(c => c.name === 'region')!.id;
        s.cells = await probe.pg<Cell>('SELECT id, row_id, value FROM model_hub_cell WHERE dataset_id = $1 AND column_id = $2 AND NOT deleted ORDER BY row_id::text',
          [s.datasetId, s.columnId]);
        expect(s.cells.map(c => c.value).sort()).toEqual(['catalog-east', 'catalog-west', 'catalog-west']);
        expect(await probe.ch('SELECT id, project_id, org_id FROM spans FINAL WHERE trace_id IN ({a:String}, {b:String}) ORDER BY id',
          { a: s.traces[0].traceId, b: s.traces[1].traceId }))
          .toEqual(s.traces.flatMap(t => t.spanIds).sort().map(id => ({ id, project_id: s.projectId, org_id: s.owner.organizationId })));
        // observed_catalog/schema.sql: AggregatingMergeTree, NOT FINAL and not
        // physical counts. Full canonical identity includes fingerprint + bytes.
        const catalog = await probe.ch<{ organization_id: string; workspace_id: string; project_id: string;
          source_kind: string; attribute_key: string; attribute_type: string; value_fingerprint: string; value_json: string }>(
          `SELECT organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type, value_fingerprint, value_json
           FROM property_catalog.observed_attribute_values
           WHERE project_id = {p:String} AND attribute_key IN ({a:String}, {b:String})
           GROUP BY organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type, value_fingerprint, value_json
           ORDER BY attribute_key, value_json`, { p: s.projectId, a: customKeys[0], b: customKeys[1] });
        expect(catalog.map(({ value_fingerprint, ...row }) => row)).toEqual(customKeys.flatMap(attribute_key => CHOICES.map(value => ({
          organization_id: s.owner.organizationId, workspace_id: s.owner.workspaceId, project_id: s.projectId,
          source_kind: 'custom_attribute', attribute_key, attribute_type: 'string', value_json: JSON.stringify(value) }))));
        for (const d of s.configs) {
          expect(await probe.pg('SELECT project_id, eval_template_id FROM tracer_custom_eval_config WHERE id = $1 AND NOT deleted', [d.id]))
            .toEqual([{ project_id: s.projectId, eval_template_id: d.templateId }]);
          const template = await probe.pg<{ organization_id: string; workspace_id: string;
            choices: string[]; config: { choice_scores: Record<string, number> } }>(
            'SELECT organization_id, workspace_id, choices, config FROM model_hub_evaltemplate WHERE id = $1', [d.templateId]);
          expect(template[0]).toMatchObject({ organization_id: s.owner.organizationId, workspace_id: s.owner.workspaceId });
          expect(template[0].choices.sort()).toEqual(CHOICES);
          expect(template[0].config.choice_scores).toEqual({ 'catalog-east': 0, 'catalog-west': 1 });
        }
        for (const d of s.labels) {
          expect(await probe.pg('SELECT name, organization_id, workspace_id, project_id, settings FROM model_hub_annotationslabels WHERE id = $1 AND NOT deleted', [d.id]))
            .toEqual([{ name: d.name, organization_id: s.owner.organizationId, workspace_id: s.owner.workspaceId,
              project_id: s.projectId, settings: { options: CHOICES.map(label => ({ label })),
                multi_choice: false, rule_prompt: '', auto_annotate: false, strategy: null } }]);
          expect(await probe.pg('SELECT id FROM model_hub_score WHERE label_id = $1', [d.id])).toEqual([]);
        }
        expect(await probe.pg('SELECT id FROM tracer_eval_logger WHERE custom_eval_config_id = ANY($1)', [s.configs.map(d => d.id)])).toEqual([]);
      }
      // One combined CDC wait, not four serial 180-second waits.
      await expect.poll(async () => (await probe.ch<Cell>(
        `SELECT id, row_id, value FROM model_hub_cell FINAL
         WHERE column_id IN (SELECT arrayJoin(JSONExtract({ids:String}, 'Array(UUID)')))
         AND NOT deleted AND _peerdb_is_deleted = 0 ORDER BY toString(row_id)`,
        { ids: JSON.stringify(seeds.map(s => s.columnId)) })), POLL.CDC_VISIBLE)
        .toEqual(seeds.flatMap(s => s.cells).sort((a, b) => a.row_id.localeCompare(b.row_id)));
    });
    await testInfo.attach('seeded-identities', { body: JSON.stringify(seeds.map(({ owner, reader, ...s }) => ({ ...s,
      organizationId: owner.organizationId, workspaceId: owner.workspaceId }))), contentType: 'application/json' });

    await test.step('check 3: exact four-family discovery and foreign ID exclusion', async () => {
      for (const s of seeds) {
        for (const f of families(s)) {
          const catalog = await read<CatalogPage>(s.reader, METRICS, { ...f.metrics, page_size: 25 });
          expect(catalog.result.metrics.map(m => m.property_id).sort()).toEqual([...f.ids].sort());
          const values = await read<ValuePage>(s.reader, VALUES, { ...f.values, page_size: 25 });
          expect(values.result.values.map(v => v.value).sort()).toEqual(CHOICES);
          if (f.kind === 'span') expect(values.result.values.map(v => v.type)).toEqual(['string', 'string']);
        }
      }
      // Identical names/values are intentional. Establish that B's extra
      // witness really is discoverable before its absence can prove isolation.
      const bWitness = await read<CatalogPage>(seeds[3].reader, METRICS,
        { ...families(seeds[3])[0].metrics, search: witness, page_size: 25 });
      expect(bWitness.result.metrics.map(m => m.property_id)).toEqual([`custom_attribute:${witness}`]);
      const bWitnessValues = await read<ValuePage>(seeds[3].reader, VALUES,
        { ...families(seeds[3])[0].values, property_id: `custom_attribute:${witness}`, page_size: 25 });
      expect(bWitnessValues.result.values.map(v => v.value).sort()).toEqual(CHOICES);
      for (const f of families(seeds[0])) {
        const own = await read<CatalogPage>(scopes.viewer, METRICS, { ...f.metrics, page_size: 25 });
        expect(own.result.metrics.map(m => m.property_id).sort()).toEqual([...f.ids].sort());
        expect((await scopes.send(scopes.withWorkspace(scopes.viewer, a2.workspaceId), 'POST', METRICS, f.metrics)).status).toBe(403);
        // Foreign workspace headers do not identify a workspace in the current
        // org. authentication.py falls back to an own workspace (H1's separate
        // non-disclosure contract), not necessarily 403. Do not claim its
        // implicit selection is fixed: accept only an exact authorized ID set.
        const { project_ids, ...workspaceQuery } = f.metrics;
        const fallback = await read<CatalogPage>(scopes.withWorkspace(scopes.member, scopes.ownerB.workspaceId),
          METRICS, { ...workspaceQuery, page_size: 25 });
        const allowedSets = [a1.workspaceId, a2.workspaceId].map(workspaceId => [...new Set(seeds
          .filter(s => s.owner.workspaceId === workspaceId)
          .flatMap(s => families(s).find(item => item.kind === f.kind)!.ids))].sort());
        expect(allowedSets).toContainEqual(fallback.result.metrics.map(m => m.property_id).sort());
      }
      const foreignHeaderWitness = await read<CatalogPage>(scopes.withWorkspace(scopes.member, scopes.ownerB.workspaceId), METRICS,
        { source: 'spans', category: 'custom_attribute', search: witness, cursor_mode: true, page_size: 25 });
      expect(foreignHeaderWitness.result.metrics).toEqual([]);
      for (const other of [seeds[2], seeds[3]]) {
        for (const ids of [other.projectId, `${seeds[0].projectId},${other.projectId}`]) {
          for (const path of [METRICS, VALUES]) {
            const f = families(seeds[0])[0];
            const denied = await scopes.send<{ code: string }>(scopes.member, 'POST', path,
              { ...(path === METRICS ? f.metrics : f.values), project_ids: ids });
            receipts.push({ path, ids, ...denied });
            expect(denied.status).toBe(400); expect(denied.body.code).toBe('invalid');
          }
        }
        for (const f of families(other).filter(f => f.kind !== 'span')) {
          const body = { ...f.values, ...(f.kind === 'dataset' ? {} : { project_ids: seeds[0].projectId }), page_size: 25 };
          expect((await read<ValuePage>(scopes.member, VALUES, body)).result.values).toEqual([]);
        }
      }
      expect((await read<CatalogPage>(scopes.member, METRICS, { ...families(seeds[0])[0].metrics, search: witness, page_size: 25 })).result.metrics).toEqual([]);
    });

    // Initialize H1's already-public credentials ONCE. H1.openContext installs
    // authInitScript on every document, which would overwrite the real UI's
    // new workspace on its hard navigation. No mocked auth or storage rewriting
    // after this bootstrap: subsequent switch/reload state belongs to the app.
    context = await browser.newContext({ baseURL: E2E.appUrl });
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
    await page.evaluate(authInitScript, { access: scopes.member.tokens.access, refresh: scopes.member.tokens.refresh,
      organizationId: a1.organizationId, workspaceId: a1.workspaceId });
    await page.evaluate(org => sessionStorage.setItem('workspaceOrgId', org), a1.organizationId);

    const inspect = async (s: Seed) => {
      for (const f of families(s).filter(f => f.kind !== 'dataset')) {
        await page.goto(`/dashboard/observe/${s.projectId}/llm-tracing?tab=traces&selectedTab=spans`, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Filter', exact: true }).click();
        await page.getByRole('button', { name: 'Property', exact: true }).first().click();
        await page.getByPlaceholder('Search properties...').fill(f.search);
        await expect.poll(() => page.locator('[data-filter-property-option]').evaluateAll(nodes =>
          nodes.map(n => n.getAttribute('data-filter-property-option')).sort()), { timeout: UI_READY }).toEqual([...f.uiIds].sort());
        await page.locator(`[data-filter-property-option="${f.uiIds[0]}"]`).click();
        const valueRead = page.waitForResponse(r => new URL(r.url()).pathname === VALUES &&
          JSON.stringify(r.request().postDataJSON()).includes(f.uiIds[0]), { timeout: UI_READY });
        await page.locator(`[data-filter-value-trigger="${f.uiIds[0]}"]`).click();
        const response = await valueRead;
        await headers(response, s.reader);
        expect(response.status()).toBe(200);
        const body = await response.json() as ValuePage;
        expect(body.result.values.map(v => v.value).sort()).toEqual(CHOICES);
        await expect.poll(() => page.locator('[data-filter-value-option]').evaluateAll(nodes =>
          nodes.map(n => n.getAttribute('data-filter-value-option')).sort()), { timeout: UI_READY }).toEqual(CHOICES);
        if (f.kind === 'span') {
          const listed = page.waitForResponse(r => new URL(r.url()).pathname === SPANS &&
            JSON.stringify(r.request().postDataJSON()).includes('catalog-west'), { timeout: UI_READY });
          await page.locator('[data-filter-value-option="catalog-west"][role="checkbox"]').click();
          await page.keyboard.press('Escape');
          const list = await listed;
          await headers(list, s.reader);
          expect((await list.json()).result.table.map((r: { span_id: string }) => r.span_id)).toEqual([s.traces[1].spanIds[0]]);
          await expect(page.locator('.clean-data-table:visible .ag-row [col-id="span_name"]')).toHaveText([`${s.name}-1`], { timeout: UI_READY });
        }
      }
      await page.goto(`/dashboard/develop/${s.datasetId}`, { waitUntil: 'domcontentloaded' });
      const cells = page.locator(`.ag-row [col-id="${s.columnId}"]`);
      await expect(cells).toHaveText(['catalog-west', 'catalog-east', 'catalog-west'], { timeout: UI_READY });
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.locator(`[data-filter-property-option="${s.columnId}"]`).click();
      const values = page.waitForResponse(r => new URL(r.url()).pathname === VALUES, { timeout: UI_READY });
      await page.getByPlaceholder('Value', { exact: true }).click();
      const response = await values;
      await headers(response, s.reader);
      expect((await response.json()).result.values.map((v: Option) => v.value).sort()).toEqual(CHOICES);
      await expect(page.getByRole('option')).toHaveText(CHOICES, { timeout: UI_READY });
      const filtered = page.waitForResponse(r => r.url().includes(`/${s.datasetId}/get-dataset-table/`) &&
        r.request().method() !== 'OPTIONS' && decodeURIComponent(r.url()).includes('catalog-west'), { timeout: UI_READY });
      await page.getByRole('option', { name: 'catalog-west', exact: true }).click();
      expect((await (await filtered).json()).result.table.map((r: { row_id: string }) => r.row_id).sort())
        .toEqual(s.cells.filter(c => c.value === 'catalog-west').map(c => c.row_id).sort());
      await expect(cells).toHaveText(['catalog-west', 'catalog-west'], { timeout: UI_READY });
      // The value option closes its own menu, but the enclosing filter Popover
      // remains modal. Dismiss it normally before using the workspace sidebar.
      await page.keyboard.press('Escape');
      await expect(page.getByRole('button', { name: 'Property', exact: true })).toHaveCount(0, { timeout: UI_READY });
      await testInfo.attach(`${s.label}-picker`, { body: await page.screenshot(), contentType: 'image/png' });
    };
    // WorkspaceSwitcher trigger -> current-workspace hover -> SelectWorkspace.
    const switchWorkspace = async (from: ScopeActor, to: ScopeActor) => {
      const labels = await probe.pg<{ id: string; label: string }>(
        "SELECT id, coalesce(nullif(display_name, ''), name) AS label FROM accounts_workspace WHERE id = ANY($1)", [[from.workspaceId, to.workspaceId]]);
      const fromName = labels.find(w => w.id === from.workspaceId)!.label;
      const toName = labels.find(w => w.id === to.workspaceId)!.label;
      // WorkspaceContext may display its cached/default label even while the
      // explicit ID is correct (separate H1 auth/display limitation). Read the
      // app's current label, never rewrite its scope to make a switch pass.
      const displayed = await page.evaluate(() => ({ id: sessionStorage.getItem('workspaceId'),
        name: sessionStorage.getItem('workspaceDisplayName') }));
      expect(displayed.id).toBe(from.workspaceId);
      // The stored label may be absent, in which case wsDisplayLabel renders
      // user.default_workspace_display_name. Locate the actual visible text
      // from these two synthetic authorized workspace names, not cache internals.
      const trigger = page.getByText(new RegExp(`^(${labels.map(w => w.label).join('|')})$`)).filter({ visible: true });
      await expect(trigger).toHaveCount(1, { timeout: UI_READY });
      const renderedLabel = (await trigger.textContent())!.trim();
      receipts.push({ workspaceLabel: displayed, renderedLabel, expectedName: fromName,
        limitation: renderedLabel === fromName ? null : 'Current workspace label differs from explicit workspace identity' });
      await trigger.click();
      await page.locator('.MuiPopover-root:visible').getByText(renderedLabel, { exact: true }).hover();
      // Captured rendered SelectWorkspace Popper has role=tooltip (base-Popper),
      // not a MuiPopper-root class. Use its actual accessibility surface.
      const [response] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === SWITCH && r.request().method() === 'POST', { timeout: UI_READY }),
        page.getByRole('tooltip').getByText(toName, { exact: true }).click(),
      ]);
      await headers(response, from);
      expect(response.status()).toBe(200);
      expect(response.request().postDataJSON()).toEqual({ old_workspace_id: from.workspaceId, new_workspace_id: to.workspaceId });
      // WorkspaceContext immediately navigates, invalidating Chrome's response
      // body handle even when read at responseReceived. Preserve status/request
      // and prove the endpoint's actual persisted result (workspace_management.py
      // :1719), followed by exact destination headers in inspect(). No interception.
      const persisted = await probe.pg(`SELECT config->>'currentWorkspaceId' AS current_workspace,
        config->>'defaultWorkspaceId' AS default_workspace,
        config->'orgWorkspaceMap'->>$2 AS organization_workspace FROM accounts_user WHERE id = $1`,
      [from.userId, from.organizationId]);
      expect(persisted).toEqual([{ current_workspace: to.workspaceId, default_workspace: to.workspaceId,
        organization_workspace: to.workspaceId }]);
      receipts.push({ switchStatus: response.status(), request: response.request().postDataJSON(), persisted,
        responseBody: 'Unavailable after immediate browser navigation; destination proved by PG and subsequent explicit UI headers' });
      await expect(page).toHaveURL(/\/dashboard\/develop/, { timeout: UI_READY });
      await expect.poll(() => page.evaluate(() => sessionStorage.getItem('workspaceId')), { timeout: UI_READY }).toBe(to.workspaceId);
    };
    await test.step('UI stage 2: A1/P1 and dataset D1', () => inspect(seeds[0]), { timeout: UI_READY });
    await test.step('UI stage 3: sibling P2 and dataset D2', () => inspect(seeds[1]), { timeout: UI_READY });
    await test.step('UI stage 4: real switch to A2/P3 and D3', async () => {
      await switchWorkspace(scopes.member, memberA2); await inspect(seeds[2]);
    }, { timeout: UI_READY });

    await test.step('check 4: real pages reject authorized scope changes', async () => {
      for (const [index, f] of families(seeds[0]).entries()) {
        const first = await read<CatalogPage>(scopes.member, METRICS, f.metrics);
        expect(first.result.has_more).toBe(true); expect(first.result.next_cursor).toBeTruthy();
        const metricCursor = first.result.next_cursor!;
        const second = await read<CatalogPage>(scopes.member, METRICS, { ...f.metrics, cursor: metricCursor });
        expect([...first.result.metrics, ...second.result.metrics].map(m => m.property_id).sort()).toEqual([...f.ids].sort());
        expect(second.result.has_more).toBe(false);
        const valueFirst = await read<ValuePage>(scopes.member, VALUES, f.values);
        expect(valueFirst.result.has_more).toBe(true); expect(valueFirst.result.next_cursor).toBeTruthy();
        const valueCursor = valueFirst.result.next_cursor!;
        const valueSecond = await read<ValuePage>(scopes.member, VALUES, { ...f.values, cursor: valueCursor });
        expect([...valueFirst.result.values, ...valueSecond.result.values].map(v => v.value).sort()).toEqual(CHOICES);
        expect(valueSecond.result.has_more).toBe(false);
        savedPages.push({ family: f, metricCursor, valueCursor });
        for (const [recipient, target] of [[seeds[1].reader, families(seeds[1])[index]],
          [memberA2, families(seeds[2])[index]], [scopes.viewer, f],
          [seeds[3].reader, families(seeds[3])[index]]] as const) {
          // Dataset metrics intentionally do not bind a dataset ID: same A1
          // discovery cursor remains valid for D2, so only its value cursor changes.
          for (const path of [METRICS, VALUES]) {
            if (f.kind === 'dataset' && recipient === seeds[1].reader && path === METRICS) continue;
            const body = { ...(path === METRICS ? target.metrics : target.values), cursor: path === METRICS ? metricCursor : valueCursor };
            const rejected = await scopes.send<{ code: string }>(recipient, 'POST', path, body);
            receipts.push({ path, request: body, actor: recipient.userId, workspace: recipient.workspaceId, ...rejected });
            expect(rejected.status).toBe(400); expect(rejected.body.code).toBe('cursor_mismatch');
          }
        }
      }
    });
    await test.step('UI stage 5: return to populated A1 before removal', async () => {
      await switchWorkspace(memberA2, scopes.member);
      await page.goto(`/dashboard/observe/${seeds[0].projectId}/llm-tracing?tab=traces&selectedTab=spans`, { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Filter', exact: true }).click();
      await page.getByRole('button', { name: 'Property', exact: true }).first().click();
      await page.getByPlaceholder('Search properties...').fill(customKeys[0]);
      await expect(page.locator(`[data-filter-property-option="${customKeys[0]}"]`)).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('check 5 / UI stage 6: actual membership removal, fresh and cursor denial, reload', async () => {
      // rbac_views.py blocks deleting the last membership: retain A2 explicitly.
      const removed = await scopes.send(a1, 'DELETE', `/accounts/workspace/${a1.workspaceId}/members/remove/`, { user_id: scopes.member.userId });
      expect(removed.status).toBe(200);
      expect(await probe.pg('SELECT workspace_id FROM accounts_workspacemembership WHERE user_id = $1 AND is_active AND NOT deleted', [scopes.member.userId]))
        .toEqual([{ workspace_id: a2.workspaceId }]);
      for (const saved of savedPages) {
        for (const path of [METRICS, VALUES]) {
          for (const continued of [false, true]) {
            const body = { ...(path === METRICS ? saved.family.metrics : saved.family.values),
              ...(continued ? { cursor: path === METRICS ? saved.metricCursor : saved.valueCursor } : {}) };
            const denied = await scopes.send(scopes.member, 'POST', path, body);
            receipts.push({ revoked: a1.workspaceId, path, request: body, ...denied });
            expect(denied.status).toBe(403);
          }
        }
      }
      const deniedUser = page.waitForResponse(r => new URL(r.url()).pathname === '/accounts/user-info/', { timeout: UI_READY });
      await page.reload({ waitUntil: 'domcontentloaded' });
      const response = await deniedUser;
      await headers(response, scopes.member);
      expect(response.status()).toBe(403);
      // AuthProvider.initialize clears auth after explicit scoped user-info 403;
      // no implicit-workspace fallback is being qualified or repaired here.
      await expect(page).toHaveURL(/\/auth\/jwt\/login/, { timeout: UI_READY });
      await expect(page.locator('[data-filter-property-option], [data-filter-value-option]')).toHaveCount(0, { timeout: UI_READY });
      for (const f of families(seeds[2])) {
        expect((await read<CatalogPage>(memberA2, METRICS, { ...f.metrics, page_size: 25 })).result.metrics.map(m => m.property_id).sort()).toEqual([...f.ids].sort());
        expect((await read<ValuePage>(memberA2, VALUES, { ...f.values, page_size: 25 })).result.values.map(v => v.value).sort()).toEqual(CHOICES);
      }
      await testInfo.attach('revoked-browser', { body: await page.screenshot(), contentType: 'image/png' });
    }, { timeout: UI_READY });
  } finally {
    await context?.close();
    for (const key of keys.filter(k => k.enabled)) {
      expect((await scopes.send(key.actor, 'POST', DISABLE, { key_id: key.id })).status).toBe(200);
      key.enabled = false;
    }
    await testInfo.attach('permission-receipts', { body: JSON.stringify({ receipts, keys: keys.map(k => ({ id: k.id, enabled: k.enabled })),
      seeds: seeds.map(({ owner, reader, ...s }) => ({ ...s, organizationId: owner.organizationId, workspaceId: owner.workspaceId })),
      limitations: scopes.limitations }), contentType: 'application/json' });
    await req.dispose();
  }
});
