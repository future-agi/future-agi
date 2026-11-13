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

test(`TH-7275 real-user proof (${MODE})`, async ({ page }, testInfo) => {
  test.setTimeout(900_000);
  page.setDefaultTimeout(UI);
  const shot = async (name: string) => {
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: false });
  };
  const attach = async (name: string, value: unknown) => {
    writeFileSync(testInfo.outputPath(`${name}.json`), JSON.stringify(value, null, 2));
    await testInfo.attach(name, { body: JSON.stringify(value, null, 2), contentType: 'application/json' });
  };
  const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
  const a = await provisionActor(req, `p746a`);
  // Finish organization setup as a new user would, so sign-in lands in the app.
  await a.api.post(ORG_SETUP, { org_name: `th7275-${MODE}-org-a` });
  const probe = new StateProbe({ api: a.api, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
    chPassword: E2E.chPassword, pgUrl: E2E.pgUrl, catalogChUrl: E2E.catalogChUrl,
    catalogChDatabase: E2E.catalogChDatabase });
  const prefix = `th7275-${MODE}-${Date.now().toString(36)}`;
  const alpha = `${prefix}-alpha`, beta = `${prefix}-beta`, childName = `${alpha}-child`;
  const sdkA = `${prefix}-sdk-alpha`, sdkAC = `${prefix}-sdk-alpha-child`;
  const sdkB = `${prefix}-sdk-beta`, sdkShared = `${prefix}-sdk-shared`;
  const manual = `${prefix}-manual-trace`, childManual = `${prefix}-manual-child`;
  const start = BigInt(Date.now() - 30_000) * 1_000_000n;
  const cfg = (actor: TestActor, projectName: string) => ({ collectorUrl: E2E.collectorUrl,
    apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
    startTimeUnixNano: start, endTimeUnixNano: start + 50_000_000n });
  // SDK tags arrive as the OTel tag.tags attribute; sdkShared is on both roots.
  const ta = await sendTrace(req, { ...cfg(a, prefix), rootName: alpha, childName,
    rootAttributes: { 'fi.span.kind': 'chain', 'tag.tags': [sdkA, sdkShared] },
    childAttributes: { 'tag.tags': [sdkAC] } });
  const tb = await sendTrace(req, { ...cfg(a, prefix), rootName: beta, childName: `${beta}-child`,
    rootAttributes: { 'fi.span.kind': 'chain', 'tag.tags': [sdkB, sdkShared] },
    childAttributes: { 'tag.tags': [`${prefix}-sdk-beta-child`] } });
  const [project] = await probe.pg<{ id: string }>(
    'SELECT id::text FROM tracer_project WHERE name=$1 AND organization_id=$2', [prefix, a.organizationId]);
  const projectId = project.id;
  await expect.poll(async () => (await probe.ch<{ n: string }>(
    'SELECT toString(count()) AS n FROM spans FINAL WHERE project_id={p:UUID}', { p: projectId }))[0]?.n,
  POLL.SPAN_VISIBLE).toBe('4');
  await attach('seed', { prefix, projectId, alpha: ta, beta: tb, sdkA, sdkAC, sdkB, sdkShared, manual, childManual });

  // ---- sign in through the real login page ---------------------------------
  await page.goto('/auth/jwt/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel(/email/i).fill(a.email);
  await page.getByLabel(/password/i).fill(a.password);
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page).toHaveURL(/\/dashboard\//, { timeout: UI });
  await shot('00-signed-in');

  const url = `/dashboard/observe/${projectId}/llm-tracing?selectedTab=trace`;
  const row = (id: string) => page.locator(`.clean-data-table:visible .ag-row[row-id="${id}"]`);
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');
  const tagCell = (id: string) => row(id).locator('[col-id="tags"]');
  const drawer = () => page.locator('.MuiDrawer-paper:visible');
  const showTagColumns = async () => {
    await page.locator('#observe-toolbar-slot').getByRole('button', { name: 'Display', exact: true }).click();
    await page.getByText('View columns', { exact: true }).click();
    const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Search', { exact: true }) });
    const all = popup.getByRole('checkbox', { name: 'Toggle Select all', exact: true });
    await all.check(); await all.uncheck();
    for (const name of ['Trace Name', 'Tags'])
      await popup.getByText(name, { exact: true }).locator('..').getByRole('checkbox').check();
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    await expect(page.getByText('View columns', { exact: true })).toBeHidden({ timeout: UI });
  };
  const openGrid = async () => {
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await expect.poll(async () => (await traceNames.allTextContents()).sort(), { timeout: UI }).toEqual([alpha, beta].sort());
    await showTagColumns();
  };
  const listResponse = (needle: string) => page.waitForResponse(r => new URL(r.url()).pathname === TRACE_LIST &&
    r.request().method() !== 'OPTIONS' && (r.request().postData() ?? r.url()).includes(needle), { timeout: UI });
  const pickTag = async (value: string) => {
    await page.getByRole('button', { name: 'Filter', exact: true }).click();
    await page.getByRole('button', { name: 'Property', exact: true }).first().click();
    await page.getByPlaceholder('Search properties...').fill('Tag');
    const values = page.waitForResponse(r => new URL(r.url()).pathname === VALUES &&
      r.request().method() !== 'OPTIONS' && (r.request().postData() ?? r.url()).includes('tag'), { timeout: UI });
    await page.locator('[data-filter-property-option="tag"]').click();
    await page.locator('[data-filter-value-trigger="tag"]').click();
    const body = await (await values).json();
    const choices: string[] = (body?.result?.values ?? []).map((v: { value: string }) => v.value).sort();
    return { choices, choose: async () => {
      const filtered = listResponse(value);
      await page.locator(`[data-filter-value-option="${value}"]`).click();
      await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
      const response = await filtered;
      const json = await response.json();
      return { status: response.status(), ids: (json?.result?.table ?? []).map((r: Row) => r.trace_id).sort() };
    } };
  };
  const editTagsButton = (id: string) => tagCell(id).getByRole('button', { name: /^(Add|Edit) tags$/ });
  // The grid cell clips long chip rows; the editor popover lists every tag.
  const tagsInEditor = async (id: string, shotName: string) => {
    await editTagsButton(id).click();
    const editor = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Add tag...', { exact: true }) });
    await expect(editor).toBeVisible({ timeout: UI });
    const text = (await editor.textContent()) ?? '';
    await page.waitForTimeout(500); // let the popover finish fading in
    await shot(shotName);
    await page.keyboard.press('Escape');
    await expect(editor).toBeHidden({ timeout: UI });
    return text;
  };
  const snackbarText = async () => (await page.locator('#notistack-snackbar, .notistack-MuiContent').allTextContents()).join(' | ');

  {
    // ---- lane flow 1: grid trace tag + drawer child tag, reload -------------
    await openGrid();
    await expect(tagCell(ta.traceId).getByText(sdkA, { exact: true })).toBeVisible({ timeout: UI });
    await shot('10-grid-sdk-tags');
    await editTagsButton(ta.traceId).click();
    const saved = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace/${ta.traceId}/tags/` &&
      r.request().method() === 'PATCH', { timeout: UI });
    await page.getByPlaceholder('Add tag...', { exact: true }).fill(manual);
    await page.getByPlaceholder('Add tag...', { exact: true }).press('Enter');
    record('flow1.grid_trace_tag_save_status', 200, (await saved).status());
    await shot('11-grid-manual-tag-added');
    await page.keyboard.press('Escape');

    await row(ta.traceId).locator('[col-id="trace_name"]').click();
    await drawer().getByRole('region', { name: 'Trace Tree', exact: true }).getByText(childName, { exact: true }).click();
    await drawer().getByRole('button', { name: 'tag', exact: true }).click();
    const childSaved = page.waitForResponse(r => new URL(r.url()).pathname === SPAN_TAGS &&
      r.request().method() === 'POST', { timeout: UI });
    await drawer().getByPlaceholder('tag name', { exact: true }).fill(childManual);
    await drawer().getByPlaceholder('tag name', { exact: true }).press('Enter');
    const childResponse = await childSaved;
    record('flow1.drawer_child_tag_save_status', 200, childResponse.status());
    await expect(drawer().getByText(childManual, { exact: true })).toBeVisible({ timeout: UI });
    await shot('12-drawer-child-tag-added');

    await page.reload({ waitUntil: 'domcontentloaded' });
    if (!(await drawer().count())) {
      await expect.poll(async () => (await traceNames.allTextContents()).length, { timeout: UI }).toBe(2);
      await row(ta.traceId).locator('[col-id="trace_name"]').click();
    }
    await drawer().getByRole('region', { name: 'Trace Tree', exact: true }).getByText(childName, { exact: true }).click();
    await expect(drawer().getByText(childManual, { exact: true })).toBeVisible({ timeout: UI });
    await expect(drawer().getByText(sdkAC, { exact: true })).toBeVisible({ timeout: UI });
    record('flow1.after_reload_drawer_child_tags', [sdkAC, childManual].sort(), [sdkAC, childManual].sort());
    await shot('13-reload-drawer-child-sdk-and-manual');
    await page.keyboard.press('Escape');
    await openGrid();
    await shot('14-reload-grid');
    const cellText = await tagsInEditor(ta.traceId, '15-reload-grid-tag-editor-manual-and-sdk');
    record('flow1.after_reload_grid_shows', { manual: true, sdkA: true, sdkShared: true },
      { manual: cellText.includes(manual), sdkA: cellText.includes(sdkA), sdkShared: cellText.includes(sdkShared) });

    // ---- lane flow 2: picker lists SDK + manual, filters are exact ----------
    const picker = await pickTag(manual);
    const expectedChoices = [sdkA, sdkAC, sdkB, `${prefix}-sdk-beta-child`, sdkShared, manual, childManual].sort();
    record('flow2.picker_choices', expectedChoices, picker.choices);
    await shot('20-tag-picker-sdk-and-manual');
    const byManual = await picker.choose();
    record('flow2.filter_manual_tag', [ta.traceId], byManual.ids);
    await expect(traceNames).toHaveText([alpha], { timeout: UI });
    await shot('21-filter-manual-tag-alpha-only');
    await openGrid();
    const sharedPicker = await pickTag(sdkShared);
    const byShared = await sharedPicker.choose();
    record('flow2.filter_sdk_shared_tag', [ta.traceId, tb.traceId].sort(), byShared.ids);
    await shot('22-filter-sdk-shared-tag-both');
    await openGrid();
    const betaPicker = await pickTag(sdkB);
    const byBeta = await betaPicker.choose();
    record('flow2.filter_sdk_beta_tag', [tb.traceId], byBeta.ids);
    await expect(traceNames).toHaveText([beta], { timeout: UI });
    await shot('23-filter-sdk-beta-tag-beta-only');

    // ---- lane flow 3: remove the manual tag; SDK tags stay ------------------
    await openGrid();
    await editTagsButton(ta.traceId).click();
    const popup = page.locator('.MuiPopover-paper:visible').filter({ has: page.getByPlaceholder('Add tag...', { exact: true }) });
    record('flow3.sdk_tag_has_no_remove_button', 0,
      await popup.getByText(sdkA, { exact: true }).locator('..').getByRole('button').count());
    const removed = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace/${ta.traceId}/tags/` &&
      r.request().method() === 'PATCH', { timeout: UI });
    await popup.getByText(manual, { exact: true }).locator('..').getByRole('button').click();
    record('flow3.remove_status', 200, (await removed).status());
    await page.keyboard.press('Escape');
    await openGrid();
    const afterRemove = await tagsInEditor(ta.traceId, '32-after-remove-tag-editor');
    record('flow3.after_reload', { manual: false, sdkA: true, sdkShared: true },
      { manual: afterRemove.includes(manual), sdkA: afterRemove.includes(sdkA), sdkShared: afterRemove.includes(sdkShared) });
    await shot('30-after-remove-sdk-tags-stay');
    const removedPicker = await pickTag(sdkA);
    record('flow3.picker_after_remove_has_manual', false, removedPicker.choices.includes(manual));
    const bySdkA = await removedPicker.choose();
    record('flow3.filter_sdk_alpha_after_remove', [ta.traceId], bySdkA.ids);
    await shot('31-after-remove-sdk-filter-alpha');

    // ---- lane flow 4a: another project / another org is refused -------------
    const otherProjectName = `${prefix}-other-project`;
    const tOther = await sendTrace(req, { ...cfg(a, otherProjectName), rootName: `${prefix}-other` });
    const b = await provisionActor(req, `p746b`);
    const tOrg = await sendTrace(req, { ...cfg(b, `${prefix}-org-b`), rootName: `${prefix}-org-b`,
      rootAttributes: { 'tag.tags': [`${prefix}-sdk-org-b`] } });
    const [otherProject] = await probe.pg<{ id: string }>('SELECT id::text FROM tracer_project WHERE name=$1', [otherProjectName]);
    const [orgBProject] = await probe.pg<{ id: string }>('SELECT id::text FROM tracer_project WHERE name=$1', [`${prefix}-org-b`]);
    const listOf = async (actor: TestActor, project: string) => {
      let rows: Row[] = [];
      await expect.poll(async () => {
        const res = await actor.api.post<{ result: { table: Row[] } }>(TRACE_LIST,
          { project_id: project, page_number: 0, page_size: 25, filters: '[]' });
        rows = res.result.table; return rows.length;
      }, POLL.SPAN_VISIBLE).toBeGreaterThan(0);
      return rows;
    };
    const alphaRow = (await listOf(a, projectId)).find(r => r.trace_id === ta.traceId)!;
    await listOf(a, otherProject.id);
    const orgBRow = (await listOf(b, orgBProject.id))[0];
    // The browser user's own token: read it from the signed-in page.
    const token = await page.evaluate(() => localStorage.getItem('accessToken') ?? localStorage.getItem('access') ?? '');
    const callAs = async (path: string, body: Record<string, unknown>) => page.evaluate(async ({ api, path, body, token, org, ws }) => {
      const res = await fetch(`${api}${path}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`, 'X-Organization-Id': org, 'X-Workspace-Id': ws }, body: JSON.stringify(body) });
      return { status: res.status, body: await res.json().catch(() => null) };
    }, { api: E2E.apiUrl, path, body, token: token || a.tokens.access, org: a.organizationId, ws: a.workspaceId });
    const crossProject = await callAs(`/tracer/trace/${ta.traceId}/tags/`, { ...alphaRow.tag_target,
      project_id: otherProject.id, expected_tag_revision: alphaRow.tag_revision, tags: ['cross-project'] });
    record('flow4.cross_project_status', 404, crossProject.status);
    record('flow4.cross_project_code', 'tag_target_not_found', crossProject.body?.code ?? crossProject.body?.result?.code);
    const crossOrg = await callAs(`/tracer/trace/${tOrg.traceId}/tags/`, { ...orgBRow.tag_target,
      expected_tag_revision: orgBRow.tag_revision, tags: ['cross-org'] });
    record('flow4.cross_org_status', 404, crossOrg.status);
    const leaked = await probe.pg<{ n: number }>(
      'SELECT count(*)::int AS n FROM tracer_entity_tag_assignment WHERE project_id = ANY($1::uuid[])',
      [[otherProject.id, orgBProject.id]]);
    record('flow4.no_assignment_rows_written', 0, leaked[0].n);
    const orgBAfter = (await listOf(b, orgBProject.id))[0];
    record('flow4.org_b_tags_unchanged', [`${prefix}-sdk-org-b`], orgBAfter.tags.map(t => t.name));
    await attach('cross-scope', { crossProject, crossOrg, otherProject, orgBProject, tOther, tOrg });

    // ---- flow 6: a drawer tag save on page 2 keeps the grid on page 2 ------
    const pagedName = `${prefix}-paged`;
    const pagedIds: string[] = [];
    const pagedLabel = (i: number) => `${prefix}-p-${String(i).padStart(2, '0')}`;
    for (let i = 0; i < 28; i++) {
      const at = start - BigInt(i) * 1_000_000_000n;
      const t = await sendTrace(req, { ...cfg(a, pagedName), startTimeUnixNano: at, endTimeUnixNano: at + 50_000_000n,
        rootName: pagedLabel(i), childName: `${pagedLabel(i)}-child`, rootAttributes: { 'fi.span.kind': 'chain' } });
      pagedIds.push(t.traceId);
    }
    const [paged] = await probe.pg<{ id: string }>(
      'SELECT id::text FROM tracer_project WHERE name=$1 AND organization_id=$2', [pagedName, a.organizationId]);
    await expect.poll(async () => (await probe.ch<{ n: string }>(
      'SELECT toString(count()) AS n FROM spans FINAL WHERE project_id={p:UUID}', { p: paged.id }))[0]?.n,
    POLL.SPAN_VISIBLE).toBe('56');
    await page.goto(`/dashboard/observe/${paged.id}/llm-tracing?selectedTab=trace`, { waitUntil: 'domcontentloaded' });
    // AG Grid renders only the rows in view, so wait for page 1's newest row.
    await expect(traceNames.getByText(pagedLabel(0), { exact: true })).toBeVisible({ timeout: UI });
    await showTagColumns();
    const pageTwo = page.getByRole('button', { name: 'Go to page 2', exact: true });
    await expect(pageTwo).toBeEnabled({ timeout: UI });
    await pageTwo.click();
    await expect(pageTwo).toHaveAttribute('aria-current', 'page', { timeout: UI });
    await expect.poll(async () => (await traceNames.allTextContents()).length, { timeout: UI }).toBe(3);
    const pageTwoNames = (await traceNames.allTextContents()).sort();
    record('flow6.page_2_rows', [pagedLabel(25), pagedLabel(26), pagedLabel(27)].sort(), pageTwoNames);
    const targetName = pageTwoNames[0];
    const targetId = pagedIds[Number(targetName.slice(-2))];
    await shot('60-paged-grid-on-page-2');

    const drawerTag = `${prefix}-drawer-p2`, gridTag = `${prefix}-grid-p2`, againTag = `${prefix}-grid-p2-again`;
    await row(targetId).locator('[col-id="trace_name"]').click();
    // Select the root span (a click on an already-selected span clears it).
    const rootInTree = drawer().getByRole('region', { name: 'Trace Tree', exact: true }).getByText(targetName, { exact: true });
    const inlineTag = drawer().getByRole('button', { name: 'tag', exact: true });
    await rootInTree.click();
    if (!(await inlineTag.isVisible().catch(() => false))) {
      await page.waitForTimeout(1000);
      if (!(await inlineTag.isVisible().catch(() => false))) await rootInTree.click();
    }
    await inlineTag.click();
    const drawerSaved = page.waitForResponse(r => new URL(r.url()).pathname === SPAN_TAGS &&
      r.request().method() === 'POST', { timeout: UI });
    await drawer().getByPlaceholder('tag name', { exact: true }).fill(drawerTag);
    await drawer().getByPlaceholder('tag name', { exact: true }).press('Enter');
    const drawerResponse = await drawerSaved;
    const drawerBody = await drawerResponse.json().catch(() => null);
    record('flow6.drawer_root_tag_save_status', 200, drawerResponse.status());
    record('flow6.drawer_save_revision', 1, drawerBody?.result?.tag_revision);
    await expect(drawer().getByText(drawerTag, { exact: true })).toBeVisible({ timeout: UI });
    await shot('61-drawer-tag-on-page-2');
    await page.keyboard.press('Escape');
    await expect(drawer()).toHaveCount(0, { timeout: UI });
    record('flow6.grid_still_on_page_2', 'page', await pageTwo.getAttribute('aria-current'));
    record('flow6.grid_rows_after_drawer_save', pageTwoNames, (await traceNames.allTextContents()).sort());
    await expect(tagCell(targetId).getByText(drawerTag, { exact: true })).toBeVisible({ timeout: UI });
    record('flow6.grid_cell_shows_drawer_tag_without_reload', true,
      await tagCell(targetId).getByText(drawerTag, { exact: true }).isVisible());
    await shot('62-grid-page-2-after-drawer-save');

    await editTagsButton(targetId).click();
    const gridSaved = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace/${targetId}/tags/` &&
      r.request().method() === 'PATCH', { timeout: UI });
    await page.getByPlaceholder('Add tag...', { exact: true }).fill(gridTag);
    await page.getByPlaceholder('Add tag...', { exact: true }).press('Enter');
    const gridResponse = await gridSaved;
    const gridBody = await gridResponse.json().catch(() => null);
    record('flow6.next_grid_edit_status', 200, gridResponse.status());
    record('flow6.next_grid_edit_sent_revision', 1, JSON.parse(gridResponse.request().postData() ?? '{}').expected_tag_revision);
    record('flow6.next_grid_edit_revision', 2, gridBody?.result?.tag_revision);
    await shot('63-grid-edit-after-drawer-save');
    await page.keyboard.press('Escape');
    await expect(tagCell(targetId).getByText(gridTag, { exact: true })).toBeVisible({ timeout: UI });
    record('flow6.grid_still_on_page_2_after_grid_edit', 'page', await pageTwo.getAttribute('aria-current'));
    await shot('64-grid-cell-after-grid-edit');

    // Leave page 2 and come back: the cell remounts from the grid's page cache.
    await page.getByRole('button', { name: 'Go to page 1', exact: true }).click();
    await expect(traceNames.getByText(pagedLabel(0), { exact: true })).toBeVisible({ timeout: UI });
    await pageTwo.click();
    await expect.poll(async () => (await traceNames.allTextContents()).length, { timeout: UI }).toBe(3);
    await editTagsButton(targetId).click();
    const againSaved = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace/${targetId}/tags/` &&
      r.request().method() === 'PATCH', { timeout: UI });
    await page.getByPlaceholder('Add tag...', { exact: true }).fill(againTag);
    await page.getByPlaceholder('Add tag...', { exact: true }).press('Enter');
    const againResponse = await againSaved;
    record('flow6.grid_edit_after_page_round_trip_status', 200, againResponse.status());
    record('flow6.grid_edit_after_page_round_trip_sent_revision', 2,
      JSON.parse(againResponse.request().postData() ?? '{}').expected_tag_revision);
    await page.keyboard.press('Escape');
    await shot('65-grid-edit-after-page-round-trip');
    const pagedRows = await a.api.post<{ result: { table: Row[] } }>(TRACE_LIST,
      { project_id: paged.id, page_number: 0, page_size: 50, filters: '[]' });
    const pagedRow = pagedRows.result.table.find(r => r.trace_id === targetId);
    record('flow6.server_manual_tags', [drawerTag, gridTag, againTag].sort(),
      (pagedRow?.manual_tags ?? []).map(t => t.name).sort());
    record('flow6.server_tag_revision', 3, pagedRow?.tag_revision);
    await attach('paged', { pagedProject: paged.id, targetId, targetName, pageTwoNames,
      drawer: drawerBody, grid: gridBody, againStatus: againResponse.status() });

    // ---- lane flow 4b: ClickHouse unavailable -> clear UI error -------------
    if (CH_CONTAINER) {
      await openGrid();
      await editTagsButton(ta.traceId).click();
      execFileSync('docker', ['stop', CH_CONTAINER], { stdio: 'ignore' });
      try {
        const failed = page.waitForResponse(r => new URL(r.url()).pathname === `/tracer/trace/${ta.traceId}/tags/` &&
          r.request().method() === 'PATCH', { timeout: UI });
        await page.getByPlaceholder('Add tag...', { exact: true }).fill(`${prefix}-while-ch-down`);
        await page.getByPlaceholder('Add tag...', { exact: true }).press('Enter');
        const response: Response = await failed;
        const copy = 'Tags could not be saved because the service is temporarily unavailable. Try again shortly.';
        await expect(page.getByText(copy)).toBeVisible({ timeout: UI });
        record('flow4.ch_down_status', 503, response.status());
        record('flow4.ch_down_copy_visible', true, await page.getByText(copy).isVisible());
        await shot('40-clickhouse-down-clear-error');
      } finally {
        execFileSync('docker', ['start', CH_CONTAINER], { stdio: 'ignore' });
      }
      await expect.poll(async () => {
        try { return (await probe.ch<{ n: string }>('SELECT toString(1) AS n', {}))[0]?.n; } catch { return ''; }
      }, { timeout: 120_000 }).toBe('1');
      const leakedWhileDown = await probe.pg<{ n: number }>(
        `SELECT count(*)::int AS n FROM tracer_entity_tag_assignment WHERE project_id=$1::uuid
         AND manual_tags::text LIKE '%' || $2 || '%'`, [projectId, `${prefix}-while-ch-down`]);
      record('flow4.ch_down_nothing_saved', 0, leakedWhileDown[0].n);
    }
  }
  await attach('observed', observed);
  await req.dispose();
  await probe.dispose();
  const failedChecks = Object.entries(observed).filter(([, v]) => !(v as { pass: boolean }).pass).map(([k]) => k);
  expect(failedChecks, 'every recorded check matches its expected value').toEqual([]);
});
