import type { Browser, Page } from '@playwright/test';
import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// model_hub/urls.py: eval-templates/create-v2/ and the eval-groups router (views/eval_group.py).
const CREATE_TEMPLATE = '/model-hub/eval-templates/create-v2/';
const GROUPS = '/model-hub/eval-groups/';
const EDIT_LIST = '/model-hub/eval-groups/edit-eval-list/';
const TEMPLATE_LIST = '/model-hub/eval-templates/list/';
const groupPath = (id: string) => `${GROUPS}${id}/`;
const UI_READY = 60_000;
// Assertion anchors only. Seeds below do not read these constants.
const EXPECTED_MEMBERS_AFTER_REMOVE = 1;
const EXPECTED_FOREIGN = 400;
const EXPECTED_DELETED = true;
type Envelope<T> = { result: T };
type Detail = { eval_group: { id: string; name: string; description: string }; members: { eval_template_id: string; name: string }[] };
type Listing = { data: { id: string; name: string }[] };
type Template = { id: string; name: string };
type Tenant = { actor: ScopeActor; prefix: string; templates: Template[]; groupName: string; description: string; groupId: string };
type GroupRow = { name: string; description: string; organization_id: string; workspace_id: string; deleted: boolean; has_deleted_at: boolean };

test('EVAL-E2E-013: create an eval group, remove its members and keep it inside my organization', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-013', area: 'evals',
    userGoal: 'Group my evaluations on the Groups page, remove members from a group and delete it with its last member, without reaching another organization or workspace',
    steps: [
      'through the public API, create three evaluations in each of two organizations',
      'open Groups, create a group with a name, a description and two of the evaluations, and land on its page',
      'remove one member on the group page, then reload it',
      'as the other organization and from another workspace, open the group link and try to read, rename, edit and delete the group and to group the other organization\'s evaluations',
      'remove the last member, confirm that this deletes the group, and return to Groups',
    ],
    backendChecks: [
      'the create request carries exactly the chosen evaluations; PostgreSQL stores the group, its two members and two ADD history rows under the actor organization and workspace',
      'after the removal the group API and PostgreSQL list exactly the remaining member, with a DELETE history row',
      'another organization and another workspace of the same organization get 400 for read, rename, member edit and delete, never list the group, cannot group foreign evaluations, and change nothing',
      'removing the last member soft-deletes the group, clears its members and keeps all three evaluations',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  // 120s provisioning + two tenants * (two sessions * 3 UI_READY) + 60s API/storage headroom.
  test.setTimeout(900_000);
  const ui = expect.configure({ timeout: UI_READY });
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const attach = (name: string, value: unknown) => testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(value) });
  async function call<T>(actor: ScopeActor, method: string, path: string, body?: unknown, params?: Record<string, string>) {
    const response = await scopes.send<T>(actor, method, path, body, params);
    expect(response.status, `${method} ${path}: ${JSON.stringify(response.body).slice(0, 300)}`).toBe(200);
    return response.body;
  }
  async function seed(actor: ScopeActor, tag: string): Promise<Tenant> {
    const prefix = `e2e-groups-${tag}-${suffix}`;
    const templates: Template[] = [];
    for (const label of ['alpha', 'beta', 'gamma']) {
      const name = `${prefix}-${label}`;
      const created = await call<Envelope<{ id: string }>>(actor, 'POST', CREATE_TEMPLATE, {
        name, eval_type: 'llm', model: 'gpt-4o-mini', output_type: 'pass_fail',
        instructions: 'Assess {{output}}', check_internet: false, error_localizer_enabled: false,
      });
      templates.push({ id: created.result.id, name });
    }
    return { actor, prefix, templates, groupName: `${prefix}-group`, description: `Release checks ${tag}`, groupId: '' };
  }
  const groupRow = async (id: string) => (await probe.pg<GroupRow>(
    'SELECT name, description, organization_id::text AS organization_id, workspace_id::text AS workspace_id, deleted, deleted_at IS NOT NULL AS has_deleted_at FROM model_hub_eval_group WHERE id=$1', [id]))[0];
  const members = async (id: string) => (await probe.pg<{ id: string }>(
    'SELECT evaltemplate_id::text AS id FROM model_hub_eval_group_eval_templates WHERE evalgroup_id=$1', [id])).map(r => r.id).sort();
  const history = async (id: string) => (await probe.pg<{ action: string; reference_id: string; organization_id: string; workspace_id: string }>(
    "SELECT action, reference_id::text AS reference_id, organization_id::text AS organization_id, workspace_id::text AS workspace_id FROM model_hub_history WHERE source_id=$1 AND source_type='EVAL_GROUP'", [id]))
    .map(r => `${r.action}:${r.reference_id}:${r.organization_id}:${r.workspace_id}`).sort();
  const templateRows = async (t: Tenant) => (await probe.pg<{ id: string; organization_id: string; workspace_id: string; deleted: boolean }>(
    'SELECT id::text AS id, organization_id::text AS organization_id, workspace_id::text AS workspace_id, deleted FROM model_hub_evaltemplate WHERE id = ANY($1::uuid[]) ORDER BY name', [t.templates.map(x => x.id)]));
  const snapshot = async (t: Tenant) => ({ group: await groupRow(t.groupId), members: await members(t.groupId), history: await history(t.groupId) });
  const scoped = (t: Tenant, action: string, template: Template) => `${action}:${template.id}:${t.actor.organizationId}:${t.actor.workspaceId}`;

  async function open(b: Browser, actor: ScopeActor): Promise<Page> {
    const context = await scopes.openContext(b, actor);
    await context.route(url => /^https?:$/.test(url.protocol) && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname), route => route.abort());
    const page = await context.newPage(); page.setDefaultTimeout(UI_READY);
    return page;
  }
  const memberRow = (page: Page, template: Template) => page.getByRole('row').filter({ hasText: template.name });
  function scopedRequest(t: Tenant, response: { request(): { headers(): Record<string, string> } }) {
    expect(response.request().headers()['x-organization-id']).toBe(t.actor.organizationId);
    expect(response.request().headers()['x-workspace-id']).toBe(t.actor.workspaceId);
  }

  async function createAndTrim(t: Tenant) {
    const page = await open(browser, t.actor);
    await page.goto('/dashboard/evaluations/groups', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Create Groups' }).click();
    const dialog = page.getByRole('dialog', { name: 'New group' });
    await dialog.getByRole('textbox', { name: 'Group name' }).fill(t.groupName);
    await dialog.getByRole('textbox', { name: 'Group description' }).fill(t.description);
    const searched = page.waitForResponse(r => new URL(r.url()).pathname === TEMPLATE_LIST && r.request().postDataJSON()?.search === t.prefix);
    await dialog.getByRole('textbox', { name: 'Search evaluations' }).fill(t.prefix);
    expect((await searched).status()).toBe(200);
    for (const template of t.templates) await ui(dialog.getByRole('checkbox', { name: template.name, exact: true })).toBeVisible();
    for (const template of t.templates.slice(0, 2)) {
      const box = dialog.getByRole('checkbox', { name: template.name, exact: true });
      await box.click();
      await ui(box).toBeChecked();
    }
    await ui(dialog.getByText('Evaluations in this group (2)')).toBeVisible();
    const created = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === GROUPS);
    await dialog.getByRole('button', { name: 'Create group' }).click();
    const response = await created;
    expect(response.status()).toBe(200);
    scopedRequest(t, response);
    expect(response.request().postDataJSON()).toEqual({
      name: t.groupName, description: t.description, eval_template_ids: [t.templates[0].id, t.templates[1].id],
    });
    t.groupId = (await response.json() as Envelope<{ id: string }>).result.id;
    await page.waitForURL(`**/dashboard/evaluations/groups/${t.groupId}`);
    await ui(page.getByText(`Group Name : ${t.groupName}`)).toBeVisible();
    await ui(memberRow(page, t.templates[0])).toHaveCount(1);
    await ui(memberRow(page, t.templates[1])).toHaveCount(1);
    await ui(memberRow(page, t.templates[2])).toHaveCount(0);
    expect(await snapshot(t)).toEqual({
      group: { name: t.groupName, description: t.description, organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId, deleted: false, has_deleted_at: false },
      members: [t.templates[0].id, t.templates[1].id].sort(),
      history: [scoped(t, 'ADD', t.templates[0]), scoped(t, 'ADD', t.templates[1])].sort(),
    });

    await memberRow(page, t.templates[1]).getByRole('button').click();
    const confirm = page.getByRole('dialog');
    await ui(confirm).toContainText('The evaluation will be deleted only from this group.');
    const edited = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === EDIT_LIST);
    await confirm.getByRole('button', { name: 'Delete Evaluation' }).click();
    const editResponse = await edited;
    expect(editResponse.status()).toBe(200);
    scopedRequest(t, editResponse);
    expect(editResponse.request().postDataJSON()).toEqual({ deleted_template_ids: [t.templates[1].id], eval_group_id: t.groupId });
    await ui(memberRow(page, t.templates[1])).toHaveCount(0);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await ui(page.getByText(`Group Name : ${t.groupName}`)).toBeVisible();
    await ui(memberRow(page, t.templates[0])).toHaveCount(1);
    await ui(memberRow(page, t.templates[1])).toHaveCount(0);
    await ui(page.getByText('All (1)', { exact: true })).toBeVisible();
    const readback = (await call<Envelope<Detail>>(t.actor, 'GET', groupPath(t.groupId))).result;
    expect(readback.eval_group).toMatchObject({ id: t.groupId, name: t.groupName, description: t.description });
    expect(readback.members.length, 'GROUPS_NEG_MEMBERS').toBe(EXPECTED_MEMBERS_AFTER_REMOVE);
    expect(readback.members.map(m => m.eval_template_id)).toEqual([t.templates[0].id]);
    const saved = await snapshot(t);
    expect(saved.members).toEqual([t.templates[0].id]);
    expect(saved.history).toEqual([scoped(t, 'ADD', t.templates[0]), scoped(t, 'ADD', t.templates[1]), scoped(t, 'DELETE', t.templates[1])].sort());
    await attach(`saved-${t.prefix}`, { groupId: t.groupId, readback, saved });
    await page.context().close();
  }

  async function denied(actor: ScopeActor, own: Tenant | null, foreign: Tenant) {
    const probes: [string, string, unknown?, Record<string, string>?][] = [
      ['GET', groupPath(foreign.groupId)],
      ['PATCH', groupPath(foreign.groupId), { name: `${foreign.groupName}-renamed` }],
      ['POST', EDIT_LIST, { eval_group_id: foreign.groupId, deleted_template_ids: [foreign.templates[0].id] }],
      ['DELETE', groupPath(foreign.groupId)],
    ];
    if (own) probes.push(['POST', GROUPS, { name: `${own.groupName}-foreign`, description: '', eval_template_ids: [foreign.templates[0].id] }]);
    for (const [method, path, body] of probes) {
      const response = await scopes.send(actor, method, path, body);
      expect(response.status, `GROUPS_NEG_TENANCY ${method} ${path}`).toBe(EXPECTED_FOREIGN);
      expect(JSON.stringify(response.body)).not.toContain(foreign.groupName);
    }
    const listed = await call<Envelope<Listing>>(actor, 'GET', GROUPS, undefined, { name: foreign.groupName, page_size: '50', page_number: '0' });
    expect(listed.result.data.map(g => g.id)).not.toContain(foreign.groupId);
  }

  async function deleteWithLastMember(t: Tenant, foreign: Tenant) {
    const page = await open(browser, t.actor);
    const foreignRead = page.waitForResponse(r => r.request().method() === 'GET' && new URL(r.url()).pathname === groupPath(foreign.groupId));
    await page.goto(`/dashboard/evaluations/groups/${foreign.groupId}`, { waitUntil: 'domcontentloaded' });
    expect((await foreignRead).status()).toBe(EXPECTED_FOREIGN);
    await ui(page.getByText(/Group Name :/)).toBeVisible();
    await expect(page.getByText(foreign.groupName)).toHaveCount(0);
    await expect(memberRow(page, foreign.templates[0])).toHaveCount(0);

    await page.goto(`/dashboard/evaluations/groups/${t.groupId}`, { waitUntil: 'domcontentloaded' });
    await ui(page.getByText(`Group Name : ${t.groupName}`)).toBeVisible();
    await ui(memberRow(page, t.templates[0])).toHaveCount(1);
    await memberRow(page, t.templates[0]).getByRole('button').click();
    const confirm = page.getByRole('dialog');
    await ui(confirm).toContainText('Deleting it deletes the group too.');
    const removed = page.waitForResponse(r => r.request().method() === 'DELETE' && new URL(r.url()).pathname === groupPath(t.groupId));
    await confirm.getByRole('button', { name: 'Delete group' }).click();
    const response = await removed;
    expect(response.status()).toBe(200);
    scopedRequest(t, response);
    await page.waitForURL('**/dashboard/evaluations/groups');
    await ui(page.getByRole('button', { name: 'Create Groups' })).toBeVisible();
    await expect(page.getByText(t.groupName)).toHaveCount(0);
    const gone = await scopes.send(t.actor, 'GET', groupPath(t.groupId));
    expect(gone.status).toBe(400);
    const listed = await call<Envelope<Listing>>(t.actor, 'GET', GROUPS, undefined, { name: t.groupName, page_size: '50', page_number: '0' });
    expect(listed.result.data.map(g => g.id)).not.toContain(t.groupId);
    const row = await groupRow(t.groupId);
    expect(row.deleted, 'GROUPS_NEG_STORAGE').toBe(EXPECTED_DELETED);
    expect(row.has_deleted_at).toBe(true);
    expect(await members(t.groupId)).toEqual([]);
    expect(await templateRows(t)).toEqual(t.templates.map(x => ({ id: x.id, organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId, deleted: false })));
    await page.context().close();
  }

  const a = await seed(scopes.ownerA, 'a'), b = await seed(scopes.ownerB, 'b');
  for (const t of [a, b]) {
    expect(await templateRows(t)).toEqual(t.templates.map(x => ({ id: x.id, organization_id: t.actor.organizationId, workspace_id: t.actor.workspaceId, deleted: false })));
    await createAndTrim(t);
  }
  await attach('seeded-identities', [a, b].map(({ actor, ...t }) => ({ ...t, organizationId: actor.organizationId, workspaceId: actor.workspaceId })));
  const before = [await snapshot(a), await snapshot(b)];
  await denied(b.actor, b, a);
  await denied(a.actor, a, b);
  await denied(scopes.withWorkspace(a.actor, scopes.emptyWorkspace.id), null, a);
  expect([await snapshot(a), await snapshot(b)]).toEqual(before);
  await deleteWithLastMember(a, b);
  await deleteWithLastMember(b, a);
  await attach('scope-receipts', scopes.receipts);
});
