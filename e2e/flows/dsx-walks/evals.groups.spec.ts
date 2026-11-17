import { test, expect, type ScopeActor } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';

// NewGroupDialog, GroupsGrid, IndividualGroup, DeleteEvalFromGroupDialog.
// eval_group.py deliberately returns 404 for a foreign read but 400 for writes.
const GROUPS = '/model-hub/eval-groups/';
const EDIT = `${GROUPS}edit-eval-list/`;
const groupPath = (id: string) => `${GROUPS}${id}/`;
type Envelope<T> = { result: T };
type Template = { id: string; name: string };
type Detail = { eval_group: { id: string; name: string; description: string };
  members: { eval_template_id: string; name: string }[] };
type Tenant = { actor: ScopeActor; prefix: string; templates: Template[]; groupId: string;
  groupName: string; description: string };

test('@DSX-WALK-EVAL-07: Create an evaluation group, remove its members and delete only the group when its last member is removed.', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'DSX-WALK-EVAL-07', area: 'dsx-walks',
    userGoal: 'Manage my evaluation groups and their membership without deleting the underlying evaluations or exposing another organization’s group.',
    steps: [
      'seed two synthetic evaluations in each organization through create-v2',
      'create a named group with both evaluations through the Groups UI in each organization',
      'remove one member, reload and verify exactly one remains',
      'refuse cross-organization reads, membership edits and deletion and return empty foreign-name list results',
      'remove each group’s final member, confirm Delete group, and verify the evaluations still exist',
    ],
    backendChecks: [
      'create POST contains exactly the chosen template ids, name and description',
      'detail GET reports exact membership after creation and removal',
      'foreign detail returns 404; foreign edit and delete return 400; filtered list contains no foreign group',
      'last-member removal sends DELETE, soft-deletes the group and preserves both templates',
    ],
  }),
}, async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(900_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const call = async <T,>(actor: ScopeActor, method: string, path: string, body?: unknown,
    params?: Record<string, string | number>): Promise<T> => {
    const response = await scopes.send<T>(actor, method, path, body, params);
    expect(response.status, `${method} ${path}`).toBe(200);
    return response.body;
  };
  const tenants: Tenant[] = [];
  for (const [actor, label] of [[scopes.ownerA, 'a'], [scopes.ownerB, 'b']] as const) {
    const prefix = `walk_groups_${label}_${suffix}`;
    const templates: Template[] = [];
    for (const part of ['alpha', 'beta']) {
      const name = `${prefix}_${part}`;
      const response = await call<Envelope<{ id: string }>>(actor, 'POST', '/model-hub/eval-templates/create-v2/', {
        name, eval_type: 'llm', model: 'gpt-4o-mini', instructions: 'Assess the synthetic reply: {{output}}',
        output_type: 'pass_fail', pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false,
      });
      templates.push({ id: response.result.id, name });
    }
    tenants.push({ actor, prefix, templates, groupId: '', groupName: `${prefix}_group`, description: `Synthetic checks ${label}` });
  }
  await testInfo.attach('seeded-template-ids', { contentType: 'application/json', body: JSON.stringify(tenants.map(t => ({
    organizationId: t.actor.organizationId, workspaceId: t.actor.workspaceId, templateIds: t.templates.map(e => e.id),
  }))) });
  const read = async (t: Tenant) => (await call<Envelope<Detail>>(t.actor, 'GET', groupPath(t.groupId))).result;
  const open = async (actor: ScopeActor) => {
    const context = await scopes.openContext(browser, actor);
    await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost'
      ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.setDefaultTimeout(60_000);
    return { context, page };
  };
  for (const t of tenants) {
    const { context, page } = await open(t.actor);
    try {
      await page.goto('/dashboard/evaluations/groups', { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Create group', exact: true })
        .or(page.getByRole('button', { name: 'Create Groups', exact: true })).click();
      const dialog = page.getByRole('dialog', { name: 'New group', exact: true });
      await dialog.getByRole('textbox', { name: 'Group name', exact: true }).fill(t.groupName);
      await dialog.getByRole('textbox', { name: 'Group description', exact: true }).fill(t.description);
      await expect(dialog.getByRole('button', { name: 'Create group', exact: true })).toBeDisabled();
      const search = page.waitForResponse(r => new URL(r.url()).pathname === '/model-hub/eval-templates/list/'
        && r.request().method() === 'POST' && r.request().postDataJSON()?.search === t.prefix);
      await dialog.getByRole('textbox', { name: 'Search evaluations', exact: true }).fill(t.prefix);
      expect((await search).status()).toBe(200);
      for (const template of t.templates) await dialog.getByRole('checkbox', { name: template.name, exact: true }).check();
      await expect(dialog.getByText('Evaluations in this group (2)', { exact: true })).toBeVisible();
      const creating = page.waitForResponse(r => new URL(r.url()).pathname === GROUPS && r.request().method() === 'POST');
      await dialog.getByRole('button', { name: 'Create group', exact: true }).click();
      const created = await creating;
      expect(created.status()).toBe(200);
      expect(created.request().postDataJSON()).toEqual({ name: t.groupName, description: t.description,
        eval_template_ids: t.templates.map(e => e.id) });
      t.groupId = ((await created.json()) as Envelope<{ id: string }>).result.id;
      await testInfo.attach(`created-group-${t.actor === scopes.ownerA ? 'a' : 'b'}`, { contentType: 'application/json',
        body: JSON.stringify({ organizationId: t.actor.organizationId, groupId: t.groupId, templateIds: t.templates.map(e => e.id) }) });
      await expect(page).toHaveURL(new RegExp(`/dashboard/evaluations/groups/${t.groupId}$`));
      await expect(page.getByText(`Group Name : ${t.groupName}`, { exact: true })).toBeVisible();
      const detail = await read(t);
      expect(detail.eval_group).toMatchObject({ id: t.groupId, name: t.groupName, description: t.description });
      expect(detail.members.map(m => m.eval_template_id).sort()).toEqual(t.templates.map(e => e.id).sort());
      const removed = t.templates[1];
      // IndividualGroup's Actions cell contains one unnamed IconButton with row-hover-action.
      const row = page.getByRole('row').filter({ has: page.getByText(removed.name, { exact: true }) });
      await row.locator('button.row-hover-action').click();
      const confirm = page.getByRole('dialog');
      await expect(confirm).toContainText('The evaluation will be deleted only from this group.');
      const deleting = page.waitForResponse(r => new URL(r.url()).pathname === EDIT && r.request().method() === 'POST');
      await confirm.getByRole('button', { name: 'Delete Evaluation', exact: true }).click();
      const deleted = await deleting;
      expect(deleted.status()).toBe(200);
      expect(deleted.request().postDataJSON()).toEqual({ deleted_template_ids: [removed.id], eval_group_id: t.groupId });
      await expect(row).toHaveCount(0);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.getByText(`Group Name : ${t.groupName}`, { exact: true })).toBeVisible();
      await expect(page.getByRole('row').filter({ has: page.getByText(t.templates[0].name, { exact: true }) })).toHaveCount(1);
      await expect(page.getByRole('row').filter({ has: page.getByText(removed.name, { exact: true }) })).toHaveCount(0);
      await expect(page.getByText('All (1)', { exact: true })).toBeVisible();
      expect((await read(t)).members.map(m => m.eval_template_id)).toEqual([t.templates[0].id]);
    } finally {
      await context.close();
    }
  }
  const [a, b] = tenants;
  for (const [own, foreign] of [[a, b], [b, a]] as const) {
    const before = await read(own);
    expect((await scopes.send(foreign.actor, 'GET', groupPath(own.groupId))).status).toBe(404);
    expect((await scopes.send(foreign.actor, 'POST', EDIT, {
      eval_group_id: own.groupId, deleted_template_ids: [own.templates[0].id],
    })).status).toBe(400);
    expect((await scopes.send(foreign.actor, 'DELETE', groupPath(own.groupId))).status).toBe(400);
    const filtered = await call<Envelope<{ data: unknown[]; total_count: number; total_pages: number }>>(foreign.actor,
      'GET', GROUPS, undefined, { name: own.groupName, page_size: 12, page_number: 0 });
    expect(filtered.result).toEqual({ data: [], total_count: 0, total_pages: 0 });
    expect(await read(own)).toEqual(before);
  }
  for (const t of tenants) {
    const { context, page } = await open(t.actor);
    try {
      await page.goto(`/dashboard/evaluations/groups/${t.groupId}`, { waitUntil: 'domcontentloaded' });
      const row = page.getByRole('row').filter({ has: page.getByText(t.templates[0].name, { exact: true }) });
      await row.locator('button.row-hover-action').click();
      const confirm = page.getByRole('dialog');
      await expect(confirm).toContainText('Deleting it deletes the group too.');
      const deleting = page.waitForResponse(r => new URL(r.url()).pathname === groupPath(t.groupId) && r.request().method() === 'DELETE');
      await confirm.getByRole('button', { name: 'Delete group', exact: true }).click();
      expect((await deleting).status()).toBe(200);
      await expect(page).toHaveURL(/\/dashboard\/evaluations\/groups$/);
      await expect(page.getByText(t.groupName, { exact: true })).toHaveCount(0);
      expect((await scopes.send(t.actor, 'GET', groupPath(t.groupId))).status).toBe(404);
      expect(await probe.pg('SELECT deleted FROM model_hub_eval_group WHERE id=$1', [t.groupId])).toEqual([{ deleted: true }]);
      expect(await probe.pg('SELECT evaltemplate_id FROM model_hub_eval_group_eval_templates WHERE evalgroup_id=$1', [t.groupId])).toEqual([]);
      for (const template of t.templates) {
        const detail = await call<Envelope<{ id: string; name: string }>>(t.actor, 'GET', `/model-hub/eval-templates/${template.id}/detail/`);
        expect(detail.result).toMatchObject(template);
      }
    } finally {
      await context.close();
    }
  }
});
