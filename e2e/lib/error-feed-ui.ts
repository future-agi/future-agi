import type { Browser, Page } from '@playwright/test';
import { expect, ScopeActors } from './scope-actors';
import type { StateProbe } from './state-probe';
import type { FixtureProject, IssueReceipt } from './error-feed-harness';
import { UI_READY } from './error-feed-settings';

// tracer/urls.py and serializers/feed.py; the browser's same Feed APIs.
export const ISSUES_PATH = '/tracer/feed/issues/';
export const STATS_PATH = '/tracer/feed/issues/stats/';
export interface FeedRow {
  cluster_id: string; status: string; status_origin: string; status_revision: number;
  error: { name: string }; escalation: { current_count: number; affected_trace_count: number;
    baseline: number; entry_threshold: number; evaluated_at: string; policy_revision: number } | null;
}
export interface StoredIssue {
  deleted: boolean;
  status: string; status_origin: string; status_revision: string;
  status_changed_by_id: string | null;
  escalation_state: { reason?: { current_count: number; historical_count: number; baseline: number;
    baseline_exact: string; entry_threshold: number; policy_revision: number; window_end: string;
    transition_reason: string }; progress?: { frozen_baseline: string; entry_checks: number; recovery_checks: number } };
}
export const STATUS_LABELS: Record<string, string> = {
  for_review: 'For review', escalating: 'Escalating', acknowledged: 'Acknowledged', resolved: 'Resolved',
};

export async function listIssues(scopes: ScopeActors, project: FixtureProject, status?: string) {
  const response = await scopes.send<{ result: { data: FeedRow[]; total: number } }>(scopes.ownerA,
    'GET', ISSUES_PATH, undefined, { project_id: project.id, limit: 200, time_range_days: 90,
      ...(status ? { status } : {}) });
  expect(response.status).toBe(200);
  expect(response.body.result.total).toBe(response.body.result.data.length);
  return response.body.result.data;
}

export async function readIssue(scopes: ScopeActors, project: FixtureProject, issue: IssueReceipt) {
  const response = await scopes.send<{ result: { row: FeedRow } }>(scopes.ownerA,
    'GET', `${ISSUES_PATH}${issue.cluster_id}/`, undefined, { project_id: project.id });
  expect(response.status).toBe(200);
  expect(response.body.result.row.cluster_id).toBe(issue.cluster_id);
  return response.body.result.row;
}

export async function storedIssue(probe: StateProbe, issue: IssueReceipt) {
  // Read physical state, including tombstones created by issue redirects.
  const rows = await probe.pg<StoredIssue>(
    `SELECT deleted, status, status_origin, status_revision, status_changed_by_id, escalation_state
     FROM tracer_trace_error_group WHERE id=$1`, [issue.id]);
  expect(rows).toHaveLength(1);
  return rows[0];
}

export async function openIssue(browser: Browser, scopes: ScopeActors, issue: IssueReceipt) {
  const context = await scopes.openContext(browser, scopes.ownerA);
  const page = await context.newPage();
  await page.goto(`/dashboard/error-feed/${issue.cluster_id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Change status', exact: true })).toBeVisible({ timeout: UI_READY });
  return { page, context };
}

export async function expectUiStatus(page: Page, status: string) {
  await expect(page.getByRole('button', { name: 'Change status', exact: true }))
    .toHaveText(STATUS_LABELS[status], { timeout: UI_READY });
}

export async function chooseStatus(page: Page, issue: IssueReceipt, status: string) {
  await page.getByRole('button', { name: 'Change status', exact: true }).click();
  const response = page.waitForResponse(res => new URL(res.url()).pathname === `${ISSUES_PATH}${issue.cluster_id}/`
    && res.request().method() === 'PATCH', { timeout: UI_READY });
  await page.getByRole('menuitem', { name: STATUS_LABELS[status], exact: true }).click();
  expect((await response).status()).toBe(200);
  await expectUiStatus(page, status);
}

export async function expectFeedStatus(scopes: ScopeActors, project: FixtureProject, issue: IssueReceipt, status: string) {
  const detail = await readIssue(scopes, project, issue);
  expect(detail.status).toBe(status);
  expect((await listIssues(scopes, project, status)).map(row => row.cluster_id)).toContain(issue.cluster_id);
  return detail;
}
