import { test, expect, ScopeUserInfo } from '../lib/scope-actors';

// tracer/serializers/project.py:15 and views/project.py:310,527.
const PROJECTS = '/tracer/project/list_projects/';
const PROJECT_QUERY = { project_type: 'observe', page_number: 0, page_size: 25 };
interface ProjectList { result: { table: { id: string; name: string }[] } }
interface WorkspaceList { result: { workspaces: { id: string }[] } }
const UI_READY = 60_000;

test('H1 API: public scopes, roles, empty workspace and membership revocation',
  async ({ scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(180_000); // public provisioning + synchronous PG/API checks; no CDC barrier.
    const { ownerA: a, ownerB: b, member, viewer, emptyWorkspace } = scopes;
    const a2 = scopes.withWorkspace(a, emptyWorkspace.id);
    const workspaces = await probe.pg<{ id: string; organization_id: string }>(
      `SELECT id, organization_id FROM accounts_workspace
       WHERE organization_id = ANY($1::uuid[]) AND NOT deleted AND is_active ORDER BY id`,
      [[a.organizationId, b.organizationId]]);
    expect(workspaces).toEqual([
      { id: a.workspaceId, organization_id: a.organizationId },
      { id: emptyWorkspace.id, organization_id: a.organizationId },
      { id: b.workspaceId, organization_id: b.organizationId },
    ].sort((x, y) => x.id.localeCompare(y.id)));

    const memberships = await probe.pg<{ user_id: string; organization_id: string;
      workspace_id: string; org_level: number; ws_level: number; linked: boolean }>(
      `SELECT o.user_id, o.organization_id, w.workspace_id, o.level AS org_level,
       w.level AS ws_level, w.organization_membership_id = o.id AS linked
       FROM accounts_organization_membership o JOIN accounts_workspacemembership w
       ON w.user_id = o.user_id AND w.organization_membership_id = o.id
       WHERE o.user_id = ANY($1::uuid[]) AND o.is_active AND NOT o.deleted
         AND w.is_active AND NOT w.deleted ORDER BY o.user_id, w.workspace_id`, [[member.userId, viewer.userId]]);
    expect(memberships).toEqual([
      { user_id: member.userId, organization_id: a.organizationId, workspace_id: a.workspaceId, org_level: 3, ws_level: 3, linked: true },
      { user_id: member.userId, organization_id: a.organizationId, workspace_id: emptyWorkspace.id, org_level: 3, ws_level: 3, linked: true },
      { user_id: viewer.userId, organization_id: a.organizationId, workspace_id: a.workspaceId, org_level: 1, ws_level: 1, linked: true },
    ].sort((x, y) => x.user_id.localeCompare(y.user_id) || x.workspace_id.localeCompare(y.workspace_id)));
    await testInfo.attach('scope-relations-postgres', { contentType: 'application/json',
      body: JSON.stringify({ workspaces, memberships }) });

    for (const [actor, ids] of [[a, [a.workspaceId, emptyWorkspace.id]], [b, [b.workspaceId]],
      [member, [a.workspaceId, emptyWorkspace.id]], [viewer, [a.workspaceId]]] as const) {
      const result = await scopes.send<WorkspaceList>(actor, 'GET', '/accounts/workspaces/');
      expect(result.status).toBe(200);
      expect(result.body.result.workspaces.map(row => row.id).sort()).toEqual([...ids].sort());
    }

    // The same project name in both organizations prevents name-only isolation proofs.
    const name = `${scopes.prefix}-project`;
    const created = [];
    for (const actor of [member, b]) {
      const result = await scopes.send<{ result: { project_id: string } }>(actor, 'POST', '/tracer/project/',
        { name, trace_type: 'observe', model_type: 'GenerativeLLM' });
      expect(result.status).toBe(200);
      created.push(result.body.result.project_id);
    }
    await testInfo.attach('seeded-projects', { contentType: 'application/json',
      body: JSON.stringify({ name, own: created[0], foreign: created[1], emptyWorkspaceId: emptyWorkspace.id }) });
    for (const [actor, ids] of [[a, [created[0]]], [viewer, [created[0]]], [b, [created[1]]], [a2, []]] as const) {
      const result = await scopes.send<ProjectList>(actor, 'GET', PROJECTS, undefined, PROJECT_QUERY);
      expect(result.status).toBe(200);
      expect(result.body.result.table.map(row => row.id).sort()).toEqual([...ids].sort());
    }
    expect(await probe.pg('SELECT id FROM tracer_project WHERE workspace_id = $1', [emptyWorkspace.id])).toEqual([]);
    const deniedWrite = await scopes.send(viewer, 'POST', '/tracer/project/', { name: `${name}-denied`, trace_type: 'observe', model_type: 'GenerativeLLM' });
    expect(deniedWrite.status).toBe(403);
    expect(await probe.pg('SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [`${name}-denied`, a.organizationId])).toEqual([]);

    // accounts/authentication.py:314 falls back on invalid/foreign org headers.
    // With a valid own workspace, require the exact OWN identity and project set.
    const foreignHeaders = { ...a, organizationId: b.organizationId };
    const fallback = await scopes.send<ScopeUserInfo>(foreignHeaders, 'GET', '/accounts/user-info/');
    expect(fallback.status).toBe(200);
    expect(fallback.body).toMatchObject({ id: a.userId, organization: { id: a.organizationId } });
    const fallbackProjects = await scopes.send<ProjectList>(foreignHeaders, 'GET', PROJECTS, undefined, PROJECT_QUERY);
    expect(fallbackProjects.status).toBe(200);
    expect(fallbackProjects.body.result.table.map(row => row.id)).toEqual([created[0]]);
    // If BOTH headers are foreign, authentication.py:449 may choose either own
    // workspace from the cached preference/first membership. Observed locally:
    // own A1 rows OR empty A2, even after workspace/switch/ returns 200. This
    // check qualifies non-disclosure, not deterministic invalid-header routing.
    const bothForeign = { ...foreignHeaders, workspaceId: b.workspaceId };
    const bothInfo = await scopes.send<ScopeUserInfo>(bothForeign, 'GET', '/accounts/user-info/');
    expect(bothInfo.status).toBe(200);
    expect(bothInfo.body).toMatchObject({ id: a.userId, organization: { id: a.organizationId } });
    const bothProjects = await scopes.send<ProjectList>(bothForeign, 'GET', PROJECTS, undefined, PROJECT_QUERY);
    expect(bothProjects.status).toBe(200);
    const fallbackIds = bothProjects.body.result.table.map(row => row.id);
    expect([[], [created[0]]]).toContainEqual(fallbackIds);
    expect(fallbackIds).not.toContain(created[1]);
    await testInfo.attach('foreign-header-resolution', { contentType: 'application/json', body: JSON.stringify({
      resolvedOrganizationId: bothInfo.body.organization.id, userInfoWorkspaceId: bothInfo.body.default_workspace_id,
      projectIds: fallbackIds, allowedOwnProjectId: created[0], foreignProjectId: created[1],
      limitation: 'Both invalid headers do not deterministically select a workspace; only non-disclosure is qualified',
    }) });
    const noAccess = await scopes.send(scopes.withWorkspace(viewer, emptyWorkspace.id), 'GET', PROJECTS, undefined, PROJECT_QUERY);
    expect(noAccess.status).toBe(403);

    // Switch is intentionally allowed for read-only users, but checks membership.
    const switched = await scopes.send<{ result: { workspace: { id: string } } }>(member, 'POST', '/accounts/workspace/switch/',
      { old_workspace_id: a.workspaceId, new_workspace_id: emptyWorkspace.id });
    expect(switched.status).toBe(200);
    expect(switched.body.result.workspace.id).toBe(emptyWorkspace.id);
    expect((await scopes.send(viewer, 'POST', '/accounts/workspace/switch/',
      { new_workspace_id: emptyWorkspace.id })).status).toBe(403);
    expect((await scopes.send(a, 'POST', '/accounts/workspace/switch/',
      { new_workspace_id: b.workspaceId })).status).toBe(404);

    // rbac.py:366: viewer -> member -> viewer; org_level remains 1, so the
    // org/workspace max-level rule cannot mask the workspace-role transition.
    for (const level of [3, 1]) {
      const changed = await scopes.send<{ result: { user_id: string; ws_level: number } }>(a, 'POST',
        `/accounts/workspace/${a.workspaceId}/members/role/`, { user_id: viewer.userId, ws_level: level });
      expect(changed.status).toBe(200);
      expect(changed.body.result).toMatchObject({ user_id: viewer.userId, ws_level: level });
      expect(await probe.pg('SELECT level FROM accounts_workspacemembership WHERE user_id = $1 AND workspace_id = $2 AND NOT deleted',
        [viewer.userId, a.workspaceId])).toEqual([{ level }]);
      const info = await scopes.send<ScopeUserInfo>(viewer, 'GET', '/accounts/user-info/');
      expect(info.status).toBe(200);
      expect(info.body).toMatchObject({ org_level: 1, ws_level: level });
    }

    // rbac_views.py:1349 blocks removing a last workspace. Member retains A2.
    const removed = await scopes.send(a, 'DELETE', `/accounts/workspace/${a.workspaceId}/members/remove/`, { user_id: member.userId });
    expect(removed.status).toBe(200);
    expect(await probe.pg('SELECT workspace_id FROM accounts_workspacemembership WHERE user_id = $1 AND is_active AND NOT deleted',
      [member.userId])).toEqual([{ workspace_id: emptyWorkspace.id }]);
    expect((await scopes.send(member, 'GET', PROJECTS, undefined, PROJECT_QUERY)).status).toBe(403);
    const remaining = await scopes.send<ProjectList>(scopes.withWorkspace(member, emptyWorkspace.id), 'GET', PROJECTS, undefined, PROJECT_QUERY);
    expect(remaining.status).toBe(200);
    expect(remaining.body.result.table).toEqual([]);
    expect((await scopes.send(a2, 'DELETE', `/accounts/workspace/${emptyWorkspace.id}/members/remove/`,
      { user_id: member.userId })).status).toBe(400);
    await testInfo.attach('scope-final-api-state', { contentType: 'application/json', body: JSON.stringify(scopes.evidence()) });
  });

test('H1 UI: isolated contexts retain actor and workspace headers through reload',
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(600_000); // 8 sequential navigation/reload UI_READY waits + 120 s provisioning/headroom.
    // One page at a time; no second implicit page/context fixture is requested.
    const observations = [];
    for (const actor of [scopes.ownerA, scopes.withWorkspace(scopes.ownerA, scopes.emptyWorkspace.id),
      scopes.ownerB, scopes.viewer]) {
      const context = await scopes.openContext(browser, actor);
      try {
        const page = await context.newPage();
        for (const reload of [false, true]) {
          const userResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/accounts/user-info/', { timeout: UI_READY });
          const listResponse = page.waitForResponse(r => new URL(r.url()).pathname === PROJECTS, { timeout: UI_READY });
          if (reload) await page.reload({ timeout: UI_READY });
          else await page.goto('/dashboard/observe', { timeout: UI_READY });
          const [user, list] = await Promise.all([userResponse, listResponse]);
          expect(user.status()).toBe(200);
          expect(await user.json()).toMatchObject({ id: actor.userId, organization: { id: actor.organizationId } });
          expect(list.status()).toBe(200);
          expect((await list.json() as ProjectList).result.table).toEqual([]);
          const headers = await list.request().allHeaders();
          expect(headers['x-organization-id']).toBe(actor.organizationId);
          expect(headers['x-workspace-id']).toBe(actor.workspaceId);
          expect(headers.authorization).toBe(`Bearer ${actor.tokens.access}`);
          await expect(page).toHaveURL(/\/dashboard\/observe/, { timeout: UI_READY });
          // ProjectWrapperView.jsx:233 / ProjectFtux.jsx:45: a genuinely new
          // workspace shows first-use instructions, not the filtered-table empty row.
          await expect(page.getByText('Welcome to Observe', { exact: true })).toBeVisible({ timeout: UI_READY });
          observations.push({ userId: actor.userId, organizationId: headers['x-organization-id'],
            workspaceId: headers['x-workspace-id'], reload, userStatus: user.status(), listStatus: list.status() });
        }
      } finally { await context.close(); }
    }
    await testInfo.attach('scope-browser-headers', { contentType: 'application/json', body: JSON.stringify(observations) });
  });
