// Consumer assertions only. Authentication, responses and browser storage are never mocked.
import { createHash } from 'node:crypto';
import type { Browser, Page, TestInfo } from '@playwright/test';
import { expect, type ScopeActors } from '../../lib/scope-actors';
import type { StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';

export const UI_READY = 60_000;
export const LANDING = '/dashboard/develop';
export const ME = '/accounts/user-info/';
export const WORKSPACES = '/accounts/workspaces/';
export const digest = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export const sameSet = (a: string[], b: string[]) => digest([...a].sort()) === digest([...b].sort());
export type Check = (name: string, passed: boolean) => void;
export interface Consumer {
  browser: Browser; browserName: string; scopes: ScopeActors; probe: StateProbe; testInfo: TestInfo;
}
export interface CaseRow {
  scenario: string; browser: string; control: boolean; phase: string; status: 'not-run' | 'passed' | 'failed';
  checks: { name: string; passed: boolean }[];
  boundary?: { localRequests: number; refused: number; unrouted: number };
}
export interface Info {
  id: string; organization: { id: string }; default_workspace_id: string | null;
  org_level: number; ws_level: number | null; default_workspace_role: string | null;
}
export interface Workspace { id: string; name: string; display_name: string | null; is_default: boolean }
export interface WorkspaceList { result: { workspaces: Workspace[]; total: number } }
export interface Members { result: { workspace: { id: string }; members: { user_id: string; role: string }[]; total: number } }
export function checker(row: CaseRow): Check {
  return (name, passed) => {
    row.checks.push({ name, passed });
    // Do not print actual identities, passwords, links, tokens or response bodies.
    expect(passed, `AUTH004_${row.scenario}_${name}`).toBe(true);
  };
}
export function wrongOracle() {
  const control = process.env.E2E_LANDING_EXPECT_WRONG;
  if (control !== undefined && control !== 'all') throw new Error('AUTH004_UNKNOWN_CONTROL');
  return control === 'all';
}
export function firstInfo(page: Page, check: Check): Promise<Info> {
  const promise = (async () => {
    const response = await page.waitForResponse(r => new URL(r.url()).origin === new URL(E2E.apiUrl).origin &&
      new URL(r.url()).pathname === ME && r.request().method() === 'GET', { timeout: UI_READY });
    check('initial_info_200', response.status() === 200);
    const headers = await response.request().allHeaders();
    check('initial_no_workspace_selector', !headers['x-workspace-id'] && !new URL(response.url()).searchParams.has('workspace_id'));
    return await response.json() as Info;
  })();
  void promise.catch(() => undefined);
  return promise;
}
export async function passwordLogin(page: Page, email: string, password: string, check: Check) {
  await page.goto(new URL(`/auth/jwt/login?returnTo=${encodeURIComponent(LANDING)}`, E2E.appUrl).href,
    { waitUntil: 'domcontentloaded' });
  check('fresh_scope', await page.evaluate(() => !localStorage.getItem('accessToken') &&
    !sessionStorage.getItem('workspaceId') && !sessionStorage.getItem('organizationId')));
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  const initial = firstInfo(page, check);
  const [response] = await Promise.all([
    page.waitForResponse(r => new URL(r.url()).origin === new URL(E2E.apiUrl).origin &&
      new URL(r.url()).pathname === '/accounts/token/' && r.request().method() === 'POST', { timeout: UI_READY }),
    page.getByRole('button', { name: 'Continue', exact: true }).click(),
  ]);
  check('password_login_200', response.status() === 200);
  return await initial;
}
export async function readBrowserLanding(page: Page) {
  return page.evaluate(async ({ api, me, workspaces, timeout }) => {
    const token = localStorage.getItem('accessToken');
    if (!token) throw new Error('AUTH004_NO_BROWSER_SESSION');
    const get = async (path: string) => {
      const response = await fetch(new URL(path, api), { headers: { Authorization: `Bearer ${token}` },
        credentials: 'omit', redirect: 'error', signal: AbortSignal.timeout(timeout) });
      return { status: response.status, body: await response.json() };
    };
    return { me: await get(me), roster: await get(workspaces) };
  }, { api: E2E.apiUrl, me: ME, workspaces: WORKSPACES, timeout: UI_READY });
}
export async function assertLanding(page: Page, initial: Info, wanted: {
  expected: string | null; org: string; userId: string; foreign: string; eligible: string[]; local: Workspace[];
}, check: Check, phase: 'landing' | 'reload' = 'landing') {
  const readback = await readBrowserLanding(page);
  check(`${phase}_readback_200`, readback.me.status === 200 && readback.roster.status === 200);
  const info = readback.me.body as Info, available = (readback.roster.body as WorkspaceList).result;
  check(`${phase}_authenticated_actor`, initial.id === wanted.userId && info.id === wanted.userId);
  check(`${phase}_current_organization`, initial.organization.id === wanted.org && info.organization.id === wanted.org);
  check(`${phase}_exact_accessible_set`, sameSet(available.workspaces.map(w => w.id), wanted.eligible) &&
    available.total === wanted.eligible.length);
  check(`${phase}_never_outside_access`, info.default_workspace_id === null ? wanted.eligible.length === 0 :
    wanted.eligible.includes(info.default_workspace_id));
  check(`${phase}_never_foreign_landing`, info.default_workspace_id !== wanted.foreign);
  check(`${phase}_role_policy`, info.org_level === 3 && (wanted.expected === null ?
    info.ws_level === null && info.default_workspace_role === null : info.ws_level === 3));
  check(phase === 'landing' ? 'expected_landing' : 'reload_expected_landing',
    initial.default_workspace_id === wanted.expected && info.default_workspace_id === wanted.expected);
  const workspace = wanted.local.find(w => w.id === wanted.expected);
  const label = workspace ? workspace.display_name || workspace.name : 'Workspace';
  await expect.poll(async () => {
    const labels = await page.getByText(label, { exact: true }).filter({ visible: true }).allTextContents();
    return labels.length === 1 && labels[0] === label;
  }, { timeout: UI_READY, message: `AUTH004_${phase}_visible_workspace` }).toBe(true);
  const state = await page.evaluate(() => ({ org: sessionStorage.getItem('organizationId'),
    workspace: sessionStorage.getItem('workspaceId'), workspaceOrg: sessionStorage.getItem('workspaceOrgId') }));
  check(`${phase}_ui_scope`, state.org === wanted.org && state.workspace === wanted.expected &&
    (wanted.expected === null ? state.workspaceOrg === null : state.workspaceOrg === wanted.org));
}
