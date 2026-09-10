import type { Request, Response } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeUserInfo } from '../../lib/scope-actors';
import { authInitScript } from '../../lib/auth';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// accounts/serializers/workspace.py:SwitchWorkspaceSerializer and
// accounts/views/workspace_management.py:SwitchWorkspaceAPIView.
const SWITCH = '/accounts/workspace/switch/';
// tracer/serializers/project.py:ProjectSerializer / ProjectListResponseSerializer;
// frontend/src/api/project/observe-project-list.js:readObserveProjectPage.
const CREATE_PROJECT = '/tracer/project/';
const PROJECTS = '/tracer/project/list_projects/';
const PROJECT_QUERY = { project_type: 'observe', page_number: '0', page_size: '25' };
// accounts/views/user.py:get_user_info; frontend/src/utils/axios.js:auth endpoints.
const ME = '/accounts/user-info/';
const TOKEN_PATHS = ['/accounts/token/', '/accounts/token/refresh/'];
// frontend/src/contexts/WorkspaceContext.jsx:switchWorkspace hard-navigates to Develop.
const OBSERVE = '/dashboard/observe';
const DEVELOP = '/dashboard/develop';
const LOGIN = '/auth/jwt/login'; // flows/auth/login.spec.ts.
const UI_READY = 60_000; // README Writing a flow: browser action/first-paint budget.

interface Project { id: string; name: string; workspaceId: string }
interface ProjectPage {
  status: boolean;
  result: {
    table: { id: string; name: string }[];
    metadata: { total_rows: number; total_pages: number; page_number: number; page_size: number };
  };
}
interface Preference {
  current_workspace: string;
  default_workspace: string;
  organization_workspace: string;
}
interface WireReceipt {
  phase: string; method: string; path: string; afterPrime: boolean;
  organization: string | null; workspace: string | null; workspaceQuery: string | null;
  sameBearer: boolean; headerReadFailed: boolean;
}

test('AUTH-E2E-002: workspace switching updates implicit scope without replacing the signed-in session', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-002', area: 'auth',
    userGoal: 'Switch workspaces and continue seeing the selected workspace after refresh, while explicitly scoped requests still reach the requested authorized workspace',
    steps: [
      'open A1 with the existing signed-in member and prime no-workspace-header reads',
      'select A2 through the real workspace switcher',
      'read exactly A2 projects without a workspace header using the unchanged bearer',
      'explicitly read A1 then confirm implicit reads still use A2',
      'open Observe and reload with the exact A2 sidebar and projects',
    ],
    backendChecks: [
      'UI switching persists A2 in all three workspace preferences for the same signed-in user',
      'the unchanged bearer resolves no-workspace-header project reads to A2 after the UI switch',
      'explicit A1 scope returns P1 without changing subsequent implicit A2 scope or persisted preferences',
      'normal navigation and reload retain A2’s sidebar, exact project rows and unchanged bearer',
    ],
  }),
}, async ({ page, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
  test.setTimeout(540_000); // provisioning/setup 120 + 6*UI_READY 360 + receipts/teardown 60.
  page.setDefaultTimeout(UI_READY);
  page.setDefaultNavigationTimeout(UI_READY);
  const member = scopes.member;
  const a1 = member.workspaceId, a2 = scopes.emptyWorkspace.id;
  const org = member.organizationId;
  const prefix = `e2e-auth2-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projects: Project[] = [];
  const receipts: unknown[] = [];
  const observed: { request: Request; phase: string; afterPrime: boolean }[] = [];
  let phase = 'setup', primed = false;
  let finalWire: WireReceipt[] = [];
  const apiOrigin = new URL(E2E.apiUrl).origin;
  const attach = (name: string, value: unknown) => testInfo.attach(name, {
    contentType: 'application/json', body: JSON.stringify(value),
  });
  const watch = (request: Request) => {
    const url = new URL(request.url());
    if (url.origin === apiOrigin && request.method() !== 'OPTIONS') {
      observed.push({ request, phase, afterPrime: primed });
    }
  };
  // Record actual requests without altering headers, fulfilling routes or changing app state.
  page.on('request', watch);
  const wire = async (request: Request, label: string, afterPrime: boolean): Promise<WireReceipt> => {
    const url = new URL(request.url());
    const headers = await request.allHeaders();
    return { phase: label, method: request.method(), path: url.pathname, afterPrime,
      organization: headers['x-organization-id'] ?? null,
      workspace: headers['x-workspace-id'] ?? null,
      workspaceQuery: url.searchParams.get('workspace_id'),
      sameBearer: headers.authorization === `Bearer ${member.tokens.access}`, headerReadFailed: false };
  };
  const requestScope = async (response: Response, workspace: string | null) => {
    const receipt = await wire(response.request(), phase, primed);
    receipts.push({ wire: receipt, status: response.status() });
    // Boolean-only token assertions keep a failed assertion from printing the credential.
    expect(receipt.sameBearer, 'request must retain the original bearer').toBe(true);
    expect(receipt.organization).toBe(org);
    expect(receipt.workspace).toBe(workspace);
    expect(receipt.workspaceQuery).toBeNull();
    expect(response.status()).toBe(200);
  };
  const preferences = async (label: string) => {
    // SwitchWorkspaceAPIView writes these synchronously; no eventual-result polling.
    const rows = await probe.pg<Preference>(`SELECT config->>'currentWorkspaceId' AS current_workspace,
      config->>'defaultWorkspaceId' AS default_workspace,
      config->'orgWorkspaceMap'->>$2 AS organization_workspace FROM accounts_user WHERE id=$1`,
    [member.userId, org]);
    receipts.push({ preferences: label, rows });
    return rows;
  };
  const expectedPreferences = (id: string): Preference[] => [{ current_workspace: id,
    default_workspace: id, organization_workspace: id }];
  const exactPage = (body: ProjectPage, project: Project) => {
    expect(body.status).toBe(true);
    expect(body.result.table.map(row => ({ id: row.id, name: row.name })))
      .toEqual([{ id: project.id, name: project.name }]);
    expect(body.result.metadata).toEqual({ total_rows: 1, total_pages: 1, page_number: 0, page_size: 25 });
  };
  const browserRead = async (label: string, path: typeof PROJECTS | typeof ME, workspace: string | null = null) => {
    const url = new URL(path, E2E.apiUrl);
    if (path === PROJECTS) url.search = new URLSearchParams(PROJECT_QUERY).toString();
    const [response, result] = await Promise.all([
      // Native fetch is the deliberate API lane; do not capture concurrent axios/XHR user-info.
      page.waitForResponse(r => r.url() === url.href && r.request().method() === 'GET'
        && r.request().resourceType() === 'fetch', { timeout: UI_READY }),
      page.evaluate(async ({ url, organization, workspace, originalToken, timeout }) => {
        // Use this tab's existing token, never a new client/login or axios's default workspace header.
        const token = localStorage.getItem('accessToken');
        if (token !== originalToken) throw new Error('Existing browser bearer changed');
        const headers: Record<string, string> = { Authorization: `Bearer ${token}`, 'X-Organization-Id': organization };
        if (workspace !== null) headers['X-Workspace-Id'] = workspace;
        const response = await fetch(url, { method: 'GET', headers, credentials: 'omit',
          cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(timeout) });
        return { status: response.status, body: await response.json() as unknown };
      }, { url: url.href, organization: org, workspace, originalToken: member.tokens.access, timeout: UI_READY }),
    ]);
    await requestScope(response, workspace);
    expect(result.status).toBe(200);
    // User-info contains unrelated account fields: attach only the identity/preference witness.
    const body = result.body;
    receipts.push(path === PROJECTS ? { read: label, ...(body as ProjectPage) } : {
      read: label, id: (body as ScopeUserInfo).id,
      organization: (body as ScopeUserInfo).organization,
      default_workspace_id: (body as ScopeUserInfo).default_workspace_id,
    });
    return body;
  };
  const browserState = async (label: string, workspace: string) => {
    const state = await page.evaluate(original => ({ sameBearer: localStorage.getItem('accessToken') === original,
      workspaceId: sessionStorage.getItem('workspaceId'), orgId: sessionStorage.getItem('organizationId'),
      workspaceOrgId: sessionStorage.getItem('workspaceOrgId'),
      displayName: sessionStorage.getItem('workspaceDisplayName') }), member.tokens.access);
    receipts.push({ browser: label, state });
    expect(state.sameBearer, 'stored bearer must not be replaced').toBe(true);
    expect(state.workspaceId).toBe(workspace);
    expect(state.orgId).toBe(org);
    expect(state.workspaceOrgId).toBe(org);
  };
  const uiProjects = async (label: string, project: Project, workspaceLabel: string, reload = false) => {
    const [response] = await Promise.all([
      page.waitForResponse(r => {
        const url = new URL(r.url());
        return url.origin === apiOrigin && url.pathname === PROJECTS && r.request().method() === 'GET'
          && Object.entries(PROJECT_QUERY).every(([key, value]) => url.searchParams.get(key) === value);
      }, { timeout: UI_READY }),
      reload ? page.reload({ waitUntil: 'domcontentloaded' }) : page.goto(OBSERVE, { waitUntil: 'domcontentloaded' }),
    ]);
    await requestScope(response, project.workspaceId);
    const body = await response.json() as ProjectPage;
    receipts.push({ ui: label, body });
    exactPage(body, project);
    await expect(page).toHaveURL(new URL(OBSERVE, E2E.appUrl).href, { timeout: UI_READY });
    // ObserveListView.jsx:name column; components/data-table/DataTable.jsx uses MUI row.id.
    const rows = page.locator('.MuiDataGrid-row[data-id]');
    await expect(rows).toHaveCount(1, { timeout: UI_READY });
    await expect(rows.locator('[data-field="name"]')).toHaveText([project.name], { timeout: UI_READY });
    expect(await rows.evaluateAll(elements => elements.map(row => row.getAttribute('data-id')))).toEqual([project.id]);
    // WorkspaceSwitcher.jsx:wsDisplayLabel. Never accept whichever of A1/A2 happens to render.
    await expect(page.getByText(workspaceLabel, { exact: true }).filter({ visible: true }))
      .toHaveCount(1, { timeout: UI_READY });
    await browserState(label, project.workspaceId);
    await testInfo.attach(label, { contentType: 'image/png', body: await page.screenshot() });
  };

  try {
    // Scope fixture already requires localhost before provisioning. Keep PG reads local too.
    expect(new URL(E2E.pgUrl).hostname).toBe('localhost');
    for (const workspaceId of [a1, a2]) {
      const name = `${prefix}-${workspaceId === a1 ? 'a1' : 'a2'}`;
      const created = await scopes.send<{ result: { project_id: string } }>(
        scopes.withWorkspace(member, workspaceId), 'POST', CREATE_PROJECT,
        { name, trace_type: 'observe', model_type: 'GenerativeLLM' });
      expect(created.status).toBe(200);
      expect(created.body.result.project_id).toMatch(/^[0-9a-f-]{36}$/);
      projects.push({ id: created.body.result.project_id, name, workspaceId });
      await attach('seeded-workspace-projects', { prefix, userId: member.userId, organizationId: org, projects });
    }
    const [p1, p2] = projects;
    expect(p1.id).not.toBe(p2.id);
    expect(await probe.pg(`SELECT id, name, organization_id, workspace_id FROM tracer_project
      WHERE id=ANY($1::uuid[]) AND NOT deleted ORDER BY id`, [projects.map(project => project.id)]))
      .toEqual(projects.map(project => ({ id: project.id, name: project.name,
        organization_id: org, workspace_id: project.workspaceId })).sort((x, y) => x.id.localeCompare(y.id)));
    expect(await probe.pg(`SELECT workspace_id FROM accounts_workspacemembership
      WHERE user_id=$1 AND is_active AND NOT deleted ORDER BY workspace_id`, [member.userId]))
      .toEqual([a1, a2].sort().map(workspace_id => ({ workspace_id })));
    const labels = await probe.pg<{ id: string; label: string }>(`SELECT id,
      coalesce(nullif(display_name, ''), name) AS label FROM accounts_workspace
      WHERE id=ANY($1::uuid[]) AND organization_id=$2 AND is_active AND NOT deleted ORDER BY id`, [[a1, a2], org]);
    expect(labels.map(row => row.id)).toEqual([a1, a2].sort());
    const a1Label = labels.find(row => row.id === a1)!.label;
    const a2Label = labels.find(row => row.id === a2)!.label;
    expect(a1Label).toBeTruthy(); expect(a2Label).toBeTruthy(); expect(a1Label).not.toBe(a2Label);
    await attach('workspace-identities', { labels, memberId: member.userId, organizationId: org });
    const initial = await scopes.send<{ result: { workspace: { id: string } } }>(member, 'POST', SWITCH,
      { new_workspace_id: a1 });
    expect(initial.status).toBe(200); expect(initial.body.result.workspace.id).toBe(a1);
    expect(await preferences('initial A1')).toEqual(expectedPreferences(a1));

    await test.step('one-time existing-member browser setup', async () => {
      phase = 'browser setup';
      await page.goto(LOGIN, { waitUntil: 'domcontentloaded' });
      await expect(page.getByLabel(/email/i)).toBeVisible({ timeout: UI_READY });
      // Approved one-shot reuse: addInitScript/openContext would force A1 again on every reload.
      await page.evaluate(authInitScript, { access: member.tokens.access, refresh: member.tokens.refresh,
        organizationId: org, workspaceId: a1 });
      // WorkspaceContext.jsx:readSessionWorkspaceForOrg rejects a missing organization marker.
      await page.evaluate(id => sessionStorage.setItem('workspaceOrgId', id), org);
    }, { timeout: UI_READY });

    await test.step('A1 UI and warmed no-workspace-header baseline', async () => {
      phase = 'A1 baseline';
      await uiProjects('a1-before-switch', p1, a1Label);
      primed = true;
      exactPage(await browserRead('A1 first implicit read', PROJECTS) as ProjectPage, p1);
      exactPage(await browserRead('A1 repeated implicit read', PROJECTS) as ProjectPage, p1);
    }, { timeout: UI_READY });

    await test.step('check 1: real UI switch persists A2', async () => {
      phase = 'UI switch A1 to A2';
      await page.getByText(a1Label, { exact: true }).filter({ visible: true }).click();
      await page.locator('.MuiPopover-root:visible').getByText(a1Label, { exact: true }).hover();
      // Actual SelectWorkspace Popper role, also pinned by catalog-permission-boundaries.spec.ts.
      const [response] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).origin === apiOrigin && new URL(r.url()).pathname === SWITCH
          && r.request().method() === 'POST', { timeout: UI_READY }),
        page.getByRole('tooltip').getByText(a2Label, { exact: true }).click(),
      ]);
      await requestScope(response, a1);
      const body = response.request().postDataJSON();
      receipts.push({ switchRequest: body, status: response.status() });
      expect(body).toEqual({ old_workspace_id: a1, new_workspace_id: a2 });
      // A hard navigation can invalidate response.json(). Prove persistence independently, not by a fallback envelope.
      expect(await preferences('after UI switch')).toEqual(expectedPreferences(a2));
      await expect(page).toHaveURL(new URL(DEVELOP, E2E.appUrl).href, { timeout: UI_READY });
      await page.waitForLoadState('domcontentloaded');
      await browserState('after switch navigation', a2);
    }, { timeout: UI_READY });

    await test.step('checks 2 and 3: immediate implicit A2 and explicit A1 precedence', async () => {
      phase = 'implicit and explicit controls';
      // No polling: the FIRST response after the successful switch must be correct.
      exactPage(await browserRead('A2 first implicit read', PROJECTS) as ProjectPage, p2);
      const info = await browserRead('A2 user-info', ME) as ScopeUserInfo;
      expect(info.id).toBe(member.userId); expect(info.organization.id).toBe(org);
      expect(info.default_workspace_id).toBe(a2);
      // get_user_info reloads preferences itself; its default ID is NOT an explicit-request scope oracle.
      exactPage(await browserRead('explicit A1 override', PROJECTS, a1) as ProjectPage, p1);
      exactPage(await browserRead('A2 remains implicit after override', PROJECTS) as ProjectPage, p2);
      expect(await preferences('after explicit override')).toEqual(expectedPreferences(a2));
      await browserState('explicit reads leave the tab on A2', a2);
    }, { timeout: UI_READY });

    await test.step('check 4: normal Observe navigation uses A2', async () => {
      phase = 'A2 Observe navigation';
      await uiProjects('a2-before-reload', p2, a2Label);
    }, { timeout: UI_READY });

    await test.step('check 4: reload retains A2 sidebar, exact projects and bearer', async () => {
      phase = 'A2 reload';
      await uiProjects('a2-after-reload', p2, a2Label, true);
      exactPage(await browserRead('A2 implicit read after reload', PROJECTS) as ProjectPage, p2);
      expect(await preferences('after reload')).toEqual(expectedPreferences(a2));
    }, { timeout: UI_READY });
  } finally {
    page.off('request', watch);
    finalWire = await Promise.all(observed.map(async record => {
      try { return await wire(record.request, record.phase, record.afterPrime); }
      catch {
        // Never attach exception bodies or raw headers; a missing receipt fails closed below.
        return { phase: record.phase, method: record.request.method(), path: new URL(record.request.url()).pathname,
          afterPrime: record.afterPrime, organization: null, workspace: null, workspaceQuery: null,
          sameBearer: false, headerReadFailed: true };
      }
    }));
    await attach('default-workspace-receipts', { prefix, projects, receipts, requests: finalWire,
      limitations: ['Same-token warmed requests; Redis cache hits are not directly inspected',
        'No cross-tab, no-organization-header, revocation or catalog-family parity claim'],
      // Standard Playwright traces still contain authenticated network traffic; keep them local/private.
      fixtureLimitations: scopes.limitations });
  }
  expect(finalWire.filter(row => row.headerReadFailed), 'all request receipts must be readable').toEqual([]);
  expect(finalWire.filter(row => row.afterPrime && TOKEN_PATHS.includes(row.path)),
    'no login or refresh-token request may replace the primed bearer').toEqual([]);
  expect(finalWire.filter(row => row.afterPrime && [PROJECTS, ME, SWITCH].includes(row.path) && !row.sameBearer),
    'every protected request after priming must use the same bearer').toEqual([]);
});
