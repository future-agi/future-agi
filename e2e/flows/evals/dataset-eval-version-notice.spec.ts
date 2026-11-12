import type { BrowserContext, Page, Response } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// Public wires, pinned from model_hub/urls.py and model_hub/serializers/contracts.py.
const CREATE_DATASET = '/model-hub/develops/create-empty-dataset/'; // CreateEmptyDatasetRequestSerializer
const addColumn = (d: string) => `/model-hub/develops/${d}/add_static_column/`; // DatasetStaticColumnRequestSerializer
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/'; // EvalTemplateCreateV2RequestSerializer
// useUpdateEval (PUT) and useCreateEvalVersion: the eval editor's Save Version.
const updateTemplate = (t: string) => `/model-hub/eval-templates/${t}/update/`;
const createVersion = (t: string) => `/model-hub/eval-templates/${t}/versions/create/`;
const listVersions = (t: string) => `/model-hub/eval-templates/${t}/versions/`;
// useSetDefaultVersion (PUT): the eval page's "Set as default".
const setDefault = (t: string, v: string) => `/model-hub/eval-templates/${t}/versions/${v}/set-default/`;
const addEval = (d: string) => `/model-hub/develops/${d}/add_user_eval/`; // UserEvalMutationRequestSerializer
const editEval = (d: string, e: string) => `/model-hub/develops/${d}/edit_and_run_user_eval/${e}/`; // UserEvalUpdateRequestSerializer
// EvaluationDrawer.jsx useEvalsList: GET with { eval_type: 'user' }, polled every 5 s.
const evalList = (d: string) => `/model-hub/develops/${d}/get_evals_list/`;
const LIST_QUERY = { eval_type: 'user' };
// Never executed by this flow (every binding is saved with run: false); a
// non-Turing name passes the OSS model gate on add/edit.
const MODEL = 'gpt-4o-mini';
const INSTRUCTIONS = 'Score from 0 to 1 how polite this reply is: {{output}}';
const UI_READY = 60_000;

// EXPECTATION-ONLY fallibility anchors. Never interpolate these into a request
// body or a seeded value: each one is read by one assertion only.
const EXPECTED_HELD_PIN = 2; // NOTICE_NEG_STORAGE: the version the held binding is pinned to
const EXPECTED_FOLLOWS_THRESHOLD = 0.7; // NOTICE_NEG_API: the pass rule of the newest version
const EXPECTED_FOREIGN_STATUSES = [403, 404]; // NOTICE_NEG_TENANCY: a foreign organization's reads

type Envelope<T> = { status: boolean; result: T };
type VersionRow = { id: string; version_number: number; is_default: boolean; pass_threshold: number | null;
  deleted: boolean; organization_id: string };
type BindingRow = { id: string; name: string; dataset_id: string; template_id: string; organization_id: string;
  workspace_id: string; pinned_version_id: string | null; deleted: boolean };
type ListItem = { id: string; name: string; template_id: string; pinned_version_id: string | null;
  version: string | null; version_number: number | null; pinned_version_number: number | null;
  latest_version: string | null; latest_version_number: number | null; pass_threshold: number | null;
  higher_is_better: boolean };
type ListBody = Envelope<{ evals: ListItem[] }>;
const VIEW_FIELDS = ['pinned_version_id', 'version', 'version_number', 'pinned_version_number', 'latest_version',
  'latest_version_number', 'pass_threshold', 'higher_is_better'] as const;
type Tenant = { actor: ScopeActor; label: string; datasetId: string; columnId: string; templateId: string;
  follows: string; held: string; ids: Record<string, string>; versions: Record<number, string> };

const view = (item: ListItem) => Object.fromEntries(VIEW_FIELDS.map(key => [key, item[key]]));
const isLoopback = (url: string) => {
  const { protocol, hostname } = new URL(url);
  return !/^https?:$/.test(protocol) || ['localhost', '127.0.0.1', '[::1]'].includes(hostname);
};

test('EVAL-E2E-075: the dataset eval list says which version runs, its pass rule and a newer saved version', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-075', area: 'evals',
    userGoal: 'See, in a dataset\'s eval list, which eval version each evaluation runs, the pass rule it applies and when a newer version is saved, for my organization only',
    steps: [
      'through the public API, create in each of two organizations a dataset with two saved evaluations of one score eval',
      'through the public API, save one evaluation\'s edited config in the first organization, then a new eval version with a stricter pass threshold',
      'open the dataset, open Evaluate and read each evaluation\'s version line and newer-version notice',
      'set the previous version as the default through the public API and watch the open list change without a reload',
      'reload the dataset, open Evaluate and read the list again',
      'as the second organization, read its own dataset\'s list, then try to read and change the first organization\'s evals through the API',
    ],
    backendChecks: [
      'each organization\'s template versions, default flag, thresholds and binding pins are persisted under that organization',
      'the list endpoint the drawer polls reports the version that runs, the pin, the newest version and the pass threshold of that version',
      'the drawer shows exactly those values per evaluation, follows a default change and keeps them after a reload',
      'the second organization cannot list, read the versions of, re-default or pin the first organization\'s evals, and its own list shows only its own',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // H1 provisioning 120 + seeding 60 + 10 browser actions x UI_READY 60 + API/PG 60.
  test.setTimeout(840_000);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const blocked = new Set<string>();
  const attach = (name: string, data: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(data, null, 1) });
  const call = async <T,>(actor: ScopeActor, method: string, path: string, data?: unknown,
    params?: Record<string, string>) => {
    const res = await scopes.send<T>(actor, method, path, data, params);
    expect(res.status, `${method} ${path}: ${JSON.stringify(res.body).slice(0, 300)}`).toBe(200);
    return res.body;
  };
  const versionsOf = (templateId: string) => probe.pg<VersionRow>(
    `SELECT id, version_number, is_default, pass_threshold, deleted, organization_id
     FROM model_hub_eval_template_version WHERE eval_template_id = $1 ORDER BY version_number`, [templateId]);
  const bindingsOf = (datasetId: string) => probe.pg<BindingRow>(
    `SELECT id, name, dataset_id, template_id, organization_id, workspace_id, pinned_version_id, deleted
     FROM model_hub_userevalmetric WHERE dataset_id = $1 ORDER BY name`, [datasetId]);
  const listOf = async (t: Tenant) => {
    const body = await call<ListBody>(t.actor, 'GET', evalList(t.datasetId), undefined, LIST_QUERY);
    return Object.fromEntries(body.result.evals.map(item => [item.name, item]));
  };

  async function seed(actor: ScopeActor, label: string, threshold: number): Promise<Tenant> {
    const name = `e2e-ev6-${label}-${suffix}`;
    const created = await call<Envelope<{ dataset_id: string }>>(actor, 'POST', CREATE_DATASET,
      { new_dataset_name: `${name}-data` });
    const datasetId = created.result.dataset_id;
    await call(actor, 'POST', addColumn(datasetId), { new_column_name: 'output', column_type: 'text' });
    // The toolbar's Evaluate waits for a row ("Add rows first"); DatasetAddEmptyRowsRequestSerializer.
    await call(actor, 'POST', `/model-hub/develops/${datasetId}/add_empty_rows/`, { num_rows: 1 });
    const datasetRows = await probe.pg<{ id: string }>(
      'SELECT id FROM model_hub_row WHERE dataset_id = $1 AND NOT deleted', [datasetId]);
    expect(datasetRows).toHaveLength(1);
    const columns = await probe.pg<{ id: string }>(
      `SELECT id FROM model_hub_column WHERE dataset_id = $1 AND name = 'output' AND NOT deleted`, [datasetId]);
    expect(columns).toHaveLength(1);
    const template = await call<Envelope<{ id: string; name: string; version: string }>>(actor, 'POST',
      CREATE_TEMPLATE, { name, eval_type: 'llm', instructions: INSTRUCTIONS, model: MODEL,
        output_type: 'percentage', pass_threshold: threshold, check_internet: false, error_localizer_enabled: false });
    expect(template.result).toMatchObject({ name, version: 'V1' });
    const tenant: Tenant = { actor, label, datasetId, columnId: columns[0].id, templateId: template.result.id,
      follows: `${name}-follows`, held: `${name}-held`, ids: {}, versions: {} };
    for (const binding of [tenant.follows, tenant.held]) {
      await call(actor, 'POST', addEval(datasetId), { name: binding, template_id: tenant.templateId, model: MODEL,
        run: false, config: { mapping: { output: tenant.columnId }, config: {} } });
    }
    for (const row of await bindingsOf(datasetId)) tenant.ids[row.name] = row.id;
    expect(Object.keys(tenant.ids).sort()).toEqual([tenant.follows, tenant.held].sort());
    return tenant;
  }

  async function openList(context: BrowserContext, t: Tenant): Promise<{ page: Page; first: Response }> {
    const page = await context.newPage();
    page.setDefaultTimeout(UI_READY);
    await page.goto(`/dashboard/develop/${t.datasetId}`, { waitUntil: 'domcontentloaded' });
    await ui(page).toHaveURL(new RegExp(`/dashboard/develop/${t.datasetId}`));
    const listed = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === evalList(t.datasetId) && url.searchParams.get('eval_type') === 'user'
        && r.request().method() === 'GET' && r.ok();
    }, { timeout: UI_READY });
    await openEvaluate(page);
    const first = await listed;
    const scope = first.request().headers();
    expect(scope['x-organization-id']).toBe(t.actor.organizationId);
    expect(scope['x-workspace-id']).toBe(t.actor.workspaceId);
    return { page, first };
  }

  // Evaluate sits in the dataset toolbar's More menu. The menu lists the toolbar as it was when it
  // opened, so it is reopened until the grid's row has loaded and Evaluate is enabled.
  async function openEvaluate(page: Page) {
    const more = page.locator('[data-action-bar="grid"]').getByRole('button', { name: 'More actions', exact: true });
    const item = page.getByRole('menuitem', { name: /^Evaluate/ });
    await expect(async () => {
      if (await item.isVisible()) await page.keyboard.press('Escape');
      await more.click();
      await expect(item).toBeEnabled({ timeout: 2_000 });
    }).toPass({ timeout: UI_READY });
    await item.click();
  }

  const rows = (page: Page) => page.getByTestId('saved-eval-row').filter({ visible: true });
  const rowOf = (page: Page, name: string) => rows(page).filter({ has: page.getByText(name, { exact: true }) });
  async function readList(page: Page, expected: Record<string, { line: string; newer: string | null }>) {
    await ui(rows(page)).toHaveCount(Object.keys(expected).length);
    for (const [name, want] of Object.entries(expected)) {
      const row = rowOf(page, name);
      await ui(row).toHaveCount(1);
      await ui(row.getByTestId('saved-eval-definition')).toHaveText(want.line);
      const newer = row.getByTestId('saved-eval-newer-version');
      if (want.newer === null) await ui(newer).toHaveCount(0);
      else await ui(newer).toHaveText(want.newer);
    }
    const notices = Object.values(expected).filter(w => w.newer !== null).length;
    await ui(page.getByTestId('saved-eval-newer-version').filter({ visible: true })).toHaveCount(notices);
  }

  async function lockToLoopback(context: BrowserContext) {
    // Only the fixture stack on this host; anything else the app asks for is refused.
    await context.route(url => !isLoopback(url.toString()), route => {
      blocked.add(new URL(route.request().url()).host);
      return route.abort();
    });
  }

  const a = await seed(scopes.ownerA, 'a', 0.6);
  const b = await seed(scopes.ownerB, 'b', 0.55);
  expect(a.actor.organizationId).not.toBe(b.actor.organizationId);

  await test.step('first organization saves an edited binding, then a stricter version', async () => {
    // The dataset eval editor's Update: a changed binding config is saved as a new
    // template version (eval_version_pinning.maybe_pin_new_version) pinned to the binding.
    await call(a.actor, 'POST', editEval(a.datasetId, a.ids[a.held]), { name: a.held, template_id: a.templateId,
      model: MODEL, run: false, config: { mapping: { output: a.columnId }, config: {} } });
    // The eval editor's Save Version: template update, then a version snapshot.
    await call(a.actor, 'PUT', updateTemplate(a.templateId), { pass_threshold: 0.7 });
    const saved = await call<Envelope<{ id: string; version_number: number; is_default: boolean }>>(
      a.actor, 'POST', createVersion(a.templateId), {});
    expect(saved.result).toMatchObject({ version_number: 3, is_default: true });
    for (const t of [a, b]) {
      for (const row of await versionsOf(t.templateId)) t.versions[row.version_number] = row.id;
    }
    await attach('seeded', { a: { ...a, actor: undefined, organizationId: a.actor.organizationId,
      workspaceId: a.actor.workspaceId }, b: { ...b, actor: undefined, organizationId: b.actor.organizationId,
      workspaceId: b.actor.workspaceId } });
  });

  await test.step('backend check 1: versions, defaults, thresholds and pins persist per organization', async () => {
    const versionsA = await versionsOf(a.templateId);
    expect(versionsA.map(({ version_number, is_default, pass_threshold, deleted, organization_id }) =>
      ({ version_number, is_default, pass_threshold, deleted, organization_id }))).toEqual([
      { version_number: 1, is_default: false, pass_threshold: 0.6, deleted: false, organization_id: a.actor.organizationId },
      { version_number: 2, is_default: false, pass_threshold: 0.6, deleted: false, organization_id: a.actor.organizationId },
      { version_number: 3, is_default: true, pass_threshold: 0.7, deleted: false, organization_id: a.actor.organizationId },
    ]);
    const versionsB = await versionsOf(b.templateId);
    expect(versionsB.map(({ version_number, is_default, pass_threshold, organization_id }) =>
      ({ version_number, is_default, pass_threshold, organization_id }))).toEqual([
      { version_number: 1, is_default: true, pass_threshold: 0.55, organization_id: b.actor.organizationId },
    ]);
    for (const t of [a, b]) {
      const bindings = await bindingsOf(t.datasetId);
      expect(bindings).toHaveLength(2);
      for (const row of bindings) {
        expect(row).toMatchObject({ dataset_id: t.datasetId, template_id: t.templateId, deleted: false,
          organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId });
      }
      const pins = Object.fromEntries(bindings.map(row => [row.name, row.pinned_version_id]));
      expect(pins).toEqual({ [t.follows]: null,
        [t.held]: t === a ? a.versions[EXPECTED_HELD_PIN] : null });
    }
    await attach('storage-versions-and-bindings', { versionsA, versionsB,
      bindingsA: await bindingsOf(a.datasetId), bindingsB: await bindingsOf(b.datasetId) });
  });

  await test.step('backend check 2: the list endpoint reports what runs and the newest version', async () => {
    const listA = await listOf(a);
    expect(Object.keys(listA).sort()).toEqual([a.follows, a.held].sort());
    expect(view(listA[a.follows])).toEqual({ pinned_version_id: null, version: 'v3', version_number: 3,
      pinned_version_number: null, latest_version: 'v3', latest_version_number: 3,
      pass_threshold: EXPECTED_FOLLOWS_THRESHOLD, higher_is_better: true });
    expect(view(listA[a.held])).toEqual({ pinned_version_id: a.versions[2], version: 'v2', version_number: 2,
      pinned_version_number: 2, latest_version: 'v3', latest_version_number: 3, pass_threshold: 0.6,
      higher_is_better: true });
    const listB = await listOf(b);
    expect(Object.keys(listB).sort()).toEqual([b.follows, b.held].sort());
    for (const name of [b.follows, b.held]) {
      expect(view(listB[name])).toEqual({ pinned_version_id: null, version: 'v1', version_number: 1,
        pinned_version_number: null, latest_version: 'v1', latest_version_number: 1, pass_threshold: 0.55,
        higher_is_better: true });
      expect(listB[name].template_id).toBe(b.templateId);
    }
    await attach('list-endpoint', { a: listA, b: listB });
  });

  const contextA = await scopes.openContext(browser, a.actor);
  await lockToLoopback(contextA);
  const { page: pageA, first } = await openList(contextA, a);

  await test.step('backend check 3: the drawer shows the version that runs, the pin and the newer version', async () => {
    const drawerBody = await first.json() as ListBody;
    const drawerItems = Object.fromEntries(drawerBody.result.evals.map(item => [item.name, view(item)]));
    const apiItems = await listOf(a);
    expect(drawerItems).toEqual(Object.fromEntries(Object.entries(apiItems).map(([k, v]) => [k, view(v)])));
    await readList(pageA, {
      [a.follows]: { line: '↑ Higher is better · Pass ≥ 0.70 · v3', newer: null },
      [a.held]: { line: '↑ Higher is better · Pass ≥ 0.60 · v2 · pinned', newer: 'v2 → v3 available' },
    });
    await attach('drawer-list-response', { status: first.status(), body: drawerBody });
  });

  await test.step('the open list follows a default change, and keeps it after a reload', async () => {
    const changed = await call<Envelope<{ version_number: number; is_default: boolean }>>(a.actor, 'PUT',
      setDefault(a.templateId, a.versions[2]), {});
    expect(changed.result).toMatchObject({ version_number: 2, is_default: true });
    const after = { [a.follows]: { line: '↑ Higher is better · Pass ≥ 0.60 · v2', newer: 'v2 → v3 available' },
      [a.held]: { line: '↑ Higher is better · Pass ≥ 0.60 · v2 · pinned', newer: 'v2 → v3 available' } };
    // No reload: the drawer's own poll of the list endpoint carries the change.
    await readList(pageA, after);
    expect((await versionsOf(a.templateId)).map(v => [v.version_number, v.is_default]))
      .toEqual([[1, false], [2, true], [3, false]]);
    expect(view((await listOf(a))[a.follows])).toMatchObject({ version: 'v2', latest_version: 'v3',
      pass_threshold: 0.6 });
    await pageA.reload({ waitUntil: 'domcontentloaded' });
    await openEvaluate(pageA);
    await readList(pageA, after);
  });

  await test.step('backend check 4: the second organization sees only its own and changes nothing of the first', async () => {
    const contextB = await scopes.openContext(browser, b.actor);
    await lockToLoopback(contextB);
    const { page: pageB } = await openList(contextB, b);
    await readList(pageB, {
      [b.follows]: { line: '↑ Higher is better · Pass ≥ 0.55 · v1', newer: null },
      [b.held]: { line: '↑ Higher is better · Pass ≥ 0.55 · v1', newer: null },
    });

    const refusals: Record<string, { status: number; body: unknown }> = {};
    const foreign = {
      list: await scopes.send(b.actor, 'GET', evalList(a.datasetId), undefined, LIST_QUERY),
      versions: await scopes.send(b.actor, 'GET', listVersions(a.templateId)),
      setDefault: await scopes.send(b.actor, 'PUT', setDefault(a.templateId, a.versions[3]), {}),
      pin: await scopes.send(b.actor, 'POST', editEval(a.datasetId, a.ids[a.held]), { model: MODEL, run: false,
        pinned_version_id: a.versions[1], config: { mapping: { output: a.columnId }, config: {} } }),
    };
    for (const [key, res] of Object.entries(foreign)) {
      refusals[key] = res;
      expect(EXPECTED_FOREIGN_STATUSES, `${key} answered ${res.status}`).toContain(res.status);
      const text = JSON.stringify(res.body);
      for (const value of [a.follows, a.held, a.templateId, a.ids[a.follows], a.ids[a.held],
        ...Object.values(a.versions)]) expect(text).not.toContain(value);
    }
    // B's own binding cannot be pinned to the first organization's version.
    const crossPin = await scopes.send(b.actor, 'POST', editEval(b.datasetId, b.ids[b.held]), { model: MODEL,
      run: false, pinned_version_id: a.versions[1], config: { mapping: { output: b.columnId }, config: {} } });
    refusals.crossPin = crossPin;
    expect(crossPin.status).toBe(400);
    // Positive controls: the first organization still reads both.
    const own = await scopes.send<ListBody>(a.actor, 'GET', evalList(a.datasetId), undefined, LIST_QUERY);
    expect(own.status).toBe(200);
    const ownVersions = await scopes.send<Envelope<{ versions: { version_number: number }[] }>>(a.actor, 'GET',
      listVersions(a.templateId));
    expect(ownVersions.status).toBe(200);
    expect(ownVersions.body.result.versions.map(v => v.version_number)).toEqual([3, 2, 1]);

    // Nothing of either organization moved.
    expect((await versionsOf(a.templateId)).map(v => [v.version_number, v.is_default]))
      .toEqual([[1, false], [2, true], [3, false]]);
    expect((await versionsOf(b.templateId)).map(v => [v.version_number, v.is_default])).toEqual([[1, true]]);
    expect(Object.fromEntries((await bindingsOf(a.datasetId)).map(r => [r.name, r.pinned_version_id])))
      .toEqual({ [a.follows]: null, [a.held]: a.versions[2] });
    expect(Object.fromEntries((await bindingsOf(b.datasetId)).map(r => [r.name, r.pinned_version_id])))
      .toEqual({ [b.follows]: null, [b.held]: null });
    await readList(pageB, {
      [b.follows]: { line: '↑ Higher is better · Pass ≥ 0.55 · v1', newer: null },
      [b.held]: { line: '↑ Higher is better · Pass ≥ 0.55 · v1', newer: null },
    });
    await attach('cross-organization-refusals', refusals);
  });

  await attach('browser-requests-refused-off-host', [...blocked].sort());
});
