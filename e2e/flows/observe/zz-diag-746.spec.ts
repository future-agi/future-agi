// TH-7275 real-user proof (task 746, from task 685's spec). Not a product flow:
// it lives under proof/746/ and is copied into e2e/flows/observe/ only to run,
// against a stack built from the task head. Flows 1-4 are task 685's; flow 6
// is new: a drawer tag save on page 2 keeps the grid on page 2, and the grid
// tag edits that follow succeed. Every browser session signs in through the
// real login page. Synthetic data only.
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { request as pwRequest, test, expect, type Page, type Response } from '@playwright/test';
import { sendTrace } from '../../lib/otlp';
import { POLL, StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { provisionActor, type TestActor } from '../../lib/provisioning';

const MODE = 'pr';
const CH_CONTAINER = process.env.PROOF_CH_CONTAINER ?? '';
const TRACE_LIST = '/tracer/trace/list_traces_of_session/';
const SPAN_TAGS = '/tracer/observation-span/update-tags/';
const VALUES = '/tracer/dashboard/filter_values/';
const UI = 60_000;
const ORG_SETUP = '/accounts/team/users/';

interface Tag { name: string; color?: string }
interface Row { trace_id: string; tags: Tag[]; manual_tags?: Tag[]; source_tag_names?: string[];
  tag_revision?: number; tag_target?: Record<string, string> }

const observed: Record<string, unknown> = {};
const record = (key: string, expected: unknown, actual: unknown) => {
  observed[key] = { expected, observed: actual, pass: JSON.stringify(expected) === JSON.stringify(actual) };
};


test.describe.configure({ mode: 'serial' });
test('page-1 return diagnostic', async ({ page }, testInfo) => {
  test.setTimeout(600_000);
  page.setDefaultTimeout(UI);
  const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  const a = await provisionActor(req, `p746d${process.env.DIAG ?? ''}`);
  await a.api.post(ORG_SETUP, { org_name: `th7275-diag-org` });
  const probe = new StateProbe({ api: a.api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
    chPassword: E2E.chPassword, pgUrl: E2E.pgUrl, catalogChUrl: E2E.catalogChUrl, catalogChDatabase: E2E.catalogChDatabase });
  const prefix = `diag-${Date.now().toString(36)}`;
  const start = BigInt(Date.now() - 60_000) * 1_000_000n;
  const label = (i: number) => `${prefix}-p-${String(i).padStart(2, '0')}`;
  const ids: string[] = [];
  for (let i = 0; i < 28; i++) {
    const at = start - BigInt(i) * 1_000_000_000n;
    const t = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: a.apiKey, secretKey: a.secretKey,
      projectName: prefix, startTimeUnixNano: at, endTimeUnixNano: at + 50_000_000n, rootName: label(i), childName: `${label(i)}-c` });
    ids.push(t.traceId);
  }
  const [p] = await probe.pg<{ id: string }>('SELECT id::text FROM tracer_project WHERE name=$1', [prefix]);
  await expect.poll(async () => (await probe.ch<{ n: string }>('SELECT toString(count()) AS n FROM spans FINAL WHERE project_id={p:UUID}', { p: p.id }))[0]?.n, POLL.SPAN_VISIBLE).toBe('56');
  await page.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel(/email/i).fill(a.email);
  await page.getByLabel(/password/i).fill(a.password);
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page).toHaveURL(/\/dashboard\//, { timeout: UI });
  const names = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
  await page.goto(`/dashboard/observe/${p.id}/llm-tracing?selectedTab=trace`, { waitUntil: 'domcontentloaded' });
  await expect(names.getByText(label(0), { exact: true })).toBeVisible({ timeout: UI });
  const p1 = page.getByRole('button', { name: 'Go to page 1', exact: true });
  const p2 = page.getByRole('button', { name: 'Go to page 2', exact: true });
  const result: Record<string, unknown> = {};
  const roundTrip = async (key: string) => {
    await p2.click();
    await expect.poll(async () => (await names.allTextContents()).length, { timeout: UI }).toBe(3);
    if (key === 'after_cell_edit') {
      // Same edit path as the grid cell: open, add, close.
      await page.locator('.clean-data-table:visible .ag-row').first().locator('[col-id="tags"]').getByRole('button').click().catch(() => {});
    }
    await p1.click();
    await page.waitForTimeout(8000);
    result[key] = (await names.allTextContents()).slice(0, 3);
    await page.screenshot({ path: testInfo.outputPath(`${key}.png`) });
  };

  const reset = async () => {
    await page.goto(`/dashboard/observe/${p.id}/llm-tracing?selectedTab=trace`, { waitUntil: 'domcontentloaded' });
    await expect(names.getByText(label(0), { exact: true })).toBeVisible({ timeout: UI });
  };
  const showTags = async () => {
    await page.locator('#observe-toolbar-slot').getByRole('button', { name: 'Display', exact: true }).click();
    await page.getByText('View columns', { exact: true }).click();
    const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Search', { exact: true }) });
    const all = popup.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
    await all.check(); await all.uncheck();
    for (const n of ['Trace Name', 'Tags']) await popup.getByText(n, { exact: true }).locator('..').getByRole('checkbox').check();
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI });
  };
  const toPage2 = async () => { await p2.click(); await expect.poll(async () => (await names.allTextContents()).length, { timeout: UI }).toBe(3); };
  const backToPage1 = async (key: string) => {
    await p1.click(); await page.waitForTimeout(8000);
    result[key] = (await names.allTextContents()).slice(0, 3);
    await page.screenshot({ path: testInfo.outputPath(`${key}.png`) });
  };
  const drawer = () => page.locator('.MuiDrawer-paper:visible');
  // C: open and close the cell editor without saving, page 1.
  await reset(); await showTags(); await toPage2();
  await page.locator('.clean-data-table:visible .ag-row').nth(2).locator('[col-id="tags"]').getByRole('button').click();
  await page.waitForTimeout(500); await page.keyboard.press('Escape'); await page.waitForTimeout(1500);
  await backToPage1('after_cell_open_close');
  // B: grid cell edit on page 2 (no drawer), close, page 1.
  await reset(); await toPage2();
  await page.locator('.clean-data-table:visible .ag-row').nth(1).locator('[col-id="tags"]').getByRole('button').click();
  const cellSaved = page.waitForResponse(r => /\/tracer\/trace\/.*\/tags\/$/.test(new URL(r.url()).pathname) && r.request().method() === 'PATCH');
  await page.getByPlaceholder('Add tag...', { exact: true }).fill(`${prefix}-g`);
  await page.getByPlaceholder('Add tag...', { exact: true }).press('Enter');
  result.cell_status = (await cellSaved).status();
  await page.keyboard.press('Escape');
  await page.waitForTimeout(1500);
  await backToPage1('after_cell_edit');
  result.reload_page_names = null;
  writeFileSync(testInfo.outputPath('diag.json'), JSON.stringify(result, null, 2));
  console.log('DIAG', JSON.stringify(result));
});
