import { request } from '@playwright/test';
import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// AG Grid virtualises rows outside the viewport even within one server-side
// page — the default 800x600-ish viewport only paints ~14 rows regardless of
// page size. Tall enough that a full 25-row page (the largest this spec asks
// for) paints in one screen, so row-count assertions reflect the loaded page
// rather than scroll position.
test.use({ viewport: { width: 1440, height: 1700 } });

// The trace list request the Observe trace table issues, pinned off the running
// app (frontend TraceGrid.jsx `loadTraceObservePage` -> endpoints.project.getTracesForObserveProject).
const TRACE_LIST_PATH = '/tracer/trace/list_traces_of_session/';
const PAGE_SIZE = 10;
// 4 full pages of PAGE_SIZE plus a 5-row remainder page, so the walk crosses
// the point where the pager's window first opens a leading gap (page 4, by
// `windowedPageNumbers` in CursorGridPagination.jsx) and ends on a genuine
// partial last page.
const TOTAL_TRACES = 45;
const LAST_PAGE = 5;
const LAST_PAGE_ROWS = TOTAL_TRACES - PAGE_SIZE * (LAST_PAGE - 1);

// Browser-side waits. Modelled on span-filter-parity.spec.ts: the local stack
// slows several-fold under parallel specs, so this is sized off whole-flow
// wall time, not the 10s expect default.
const UI_READY = 60_000;
// Ingest-visibility poll for 45 traces (90 spans) across parallel batches —
// wider than POLL.SPAN_VISIBLE (sized for a single 2-span trace).
const SEED_VISIBLE = { timeout: 45_000, intervals: [1000, 2000, 3000] };

interface PagerState {
  numbers: number[];
  current: number | null;
  leadingEllipsis: boolean;
  trailingEllipsis: boolean;
  nextDisabled: boolean;
  prevDisabled: boolean;
}

// Primary and compare grids (and their trace/span variants) all stay mounted
// so view state survives tab switches, each with its own CursorGridPagination
// instance. Scope every pager locator to the one whose row grid is on screen,
// the same `:visible` convention span-filter-parity.spec.ts and
// trace-ingestion.spec.ts use for `.clean-data-table:visible`.
const VISIBLE_PAGE_BUTTONS = 'button[aria-label^="Go to page "]:visible';

function waitForCurrentPage(page: Page, pageNumber: number) {
  return expect(
    page.locator(`button[aria-label="Go to page ${pageNumber}"][aria-current="page"]:visible`),
  ).toBeVisible({ timeout: UI_READY });
}

async function readPager(page: Page): Promise<PagerState> {
  // The pager keeps "Loading page…" up until the datasource has published the
  // page and AG Grid has painted it, so this is the only point at which the
  // window, the ellipses and Next are all settled. Reading before it can catch
  // the optimistic mid-transition state.
  await expect(page.getByText('Loading page…')).toHaveCount(0, { timeout: UI_READY });
  const pageButtons = page.locator(VISIBLE_PAGE_BUTTONS);
  const count = await pageButtons.count();
  const numbers: number[] = [];
  let current: number | null = null;
  for (let i = 0; i < count; i += 1) {
    const button = pageButtons.nth(i);
    const label = await button.getAttribute('aria-label');
    const n = Number((label ?? '').replace('Go to page ', ''));
    numbers.push(n);
    if ((await button.getAttribute('aria-current')) === 'page') current = n;
  }
  numbers.sort((a, b) => a - b);
  return {
    numbers,
    current,
    leadingEllipsis: (await page.locator('[data-testid="pager-leading-ellipsis"]:visible').count()) > 0,
    trailingEllipsis: (await page.locator('[data-testid="pager-trailing-ellipsis"]:visible').count()) > 0,
    nextDisabled: await page.locator('button[aria-label="Next page"]:visible').isDisabled(),
    prevDisabled: await page.locator('button[aria-label="Previous page"]:visible').isDisabled(),
  };
}

// Pinned off the running app (2026-09-07 capture against Sessions, 136 rows at
// page size 25, walked to its true last page): the window is `{1} ∪
// {current-1..current+1 or current+proven-next}`, at most four numbers,
// always including page 1; a leading gap opens once the window first leaves
// page 1's neighbourhood; the trailing ellipsis and Next both disappear only
// on the true last page. With 45 rows at page size 10 that walk is 5 pages
// long and the gap opens on page 4.
const EXPECTED_WINDOWS: Array<{
  page: number; numbers: number[]; leading: boolean; trailing: boolean;
  prevDisabled: boolean; nextDisabled: boolean; rows: number;
}> = [
  { page: 1, numbers: [1, 2], leading: false, trailing: true, prevDisabled: true, nextDisabled: false, rows: PAGE_SIZE },
  { page: 2, numbers: [1, 2, 3], leading: false, trailing: true, prevDisabled: false, nextDisabled: false, rows: PAGE_SIZE },
  { page: 3, numbers: [1, 2, 3, 4], leading: false, trailing: true, prevDisabled: false, nextDisabled: false, rows: PAGE_SIZE },
  { page: 4, numbers: [1, 3, 4, 5], leading: true, trailing: true, prevDisabled: false, nextDisabled: false, rows: PAGE_SIZE },
  { page: LAST_PAGE, numbers: [1, 4, 5], leading: true, trailing: false, prevDisabled: false, nextDisabled: true, rows: LAST_PAGE_ROWS },
];

test('OBS-E2E-004: trace list pager windows forward without an endless page count', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-004', area: 'observe',
    userGoal: 'A developer paging through a large trace list always knows where they are and when they have reached the end',
    steps: ['seed 45 traces into one project over OTLP',
            "open the project's trace list",
            'set page size to 10 through the pager control',
            'walk forward one page at a time via Next to the true last page',
            'read the page-number window, ellipses and Next/Previous state at every page',
            'step back from the last page and walk forward again',
            'change the page size and confirm the API and the pager both follow'],
    backendChecks: ['all 45 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project',
                    'project row auto-created in PG tracer_project, scoped to the actor org'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Chains SEED_VISIBLE + navigation + up to 5x UI_READY across the page walk,
  // past the config's 120s default; without this a slow run ends as a bare
  // timeout instead of the assertion that actually ran out.
  test.setTimeout(300_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-obs4-${suffix}`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
                secretKey: actor.secretKey, projectName };

  const projectId = await test.step('seed: 45 traces in one project', async () => {
    // Send the first trace alone so project auto-create settles before the
    // rest arrive in parallel — the remaining sends target an already-known
    // project row instead of racing its creation.
    const first = await sendTrace(req, { ...cfg, rootName: `e2e.pg-${suffix}-0` });
    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: first.traceId });
      return Number(rows[0].n);
    }, POLL.SPAN_VISIBLE).toBe(first.spanIds.length);

    const remaining = Array.from({ length: TOTAL_TRACES - 1 }, (_, i) => `e2e.pg-${suffix}-${i + 1}`);
    for (let i = 0; i < remaining.length; i += 10) {
      const batch = remaining.slice(i, i + 10);
      // eslint-disable-next-line no-await-in-loop
      await Promise.all(batch.map((rootName) => sendTrace(req, { ...cfg, rootName })));
    }

    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
      [projectName, actor.organizationId]);
    expect(projects).toHaveLength(1);
    const id = projects[0].id;

    await expect.poll(async () => {
      const rows = await probe.ch<{ n: string }>(
        'SELECT count(DISTINCT trace_id) AS n FROM spans FINAL WHERE project_id = {p:UUID}', { p: id });
      return Number(rows[0].n);
    }, SEED_VISIBLE).toBe(TOTAL_TRACES);

    return id;
  });

  // Primary and compare grids stay mounted to preserve view state; scope to
  // the visible grid the same way trace-ingestion.spec.ts and
  // span-filter-parity.spec.ts do.
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await test.step('UI: open the project trace list, default page size', async () => {
    await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`,
      { waitUntil: 'domcontentloaded' });
    // Attach mode runs against the dev Vite server, which mounts the React
    // Query devtools toggle as a fixed-position overlay. It floats over the
    // pager (also pinned to the bottom of the grid) and intercepts clicks on
    // Next/page-size — irrelevant dev tooling, not part of the product.
    await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' });
    // 45 seeded rows > the default 25-per-page, so the first paint is a full page.
    await expect(traceNames).toHaveCount(25, { timeout: UI_READY });
  });

  await test.step('UI: drop to page size 10 through the control', async () => {
    const resized = page.waitForResponse(
      (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes('page_size=10') && r.ok(),
      { timeout: UI_READY });
    await page.locator('[aria-label="Results per page"]:visible').click();
    await page.getByRole('option', { name: '10', exact: true }).click();
    await resized;
    await expect(traceNames).toHaveCount(PAGE_SIZE, { timeout: UI_READY });
  });

  // Assertion 1: page 1 renders `[1] 2 …` — current page 1, page 2 numbered,
  // trailing ellipsis (more pages exist beyond what's proven).
  await test.step('page 1: [1] 2 … — current page, one proven neighbour, trailing ellipsis', async () => {
    const pager = await readPager(page);
    const expected = EXPECTED_WINDOWS[0];
    expect(pager.numbers).toEqual(expected.numbers);
    expect(pager.current).toBe(expected.page);
    expect(pager.leadingEllipsis).toBe(expected.leading);
    expect(pager.trailingEllipsis).toBe(expected.trailing);
    expect(pager.prevDisabled).toBe(expected.prevDisabled);
    expect(pager.nextDisabled).toBe(expected.nextDisabled);
  });

  for (let i = 1; i < EXPECTED_WINDOWS.length; i += 1) {
    const expected = EXPECTED_WINDOWS[i];
    // eslint-disable-next-line no-await-in-loop
    await test.step(`page ${expected.page}: walk forward via Next`, async () => {
      // Cursor-mode requests beyond page 1 carry an opaque `cursor=` token
      // instead of `page_number` (pinned off the running app — see the
      // network capture in task-8-report.md), so match on the endpoint and
      // project only; only one page-load request is in flight per click.
      const advanced = page.waitForResponse(
        (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
        { timeout: UI_READY });
      await page.locator('button[aria-label="Next page"]:visible').click();
      await advanced;
      await waitForCurrentPage(page, expected.page);
      await expect(traceNames).toHaveCount(expected.rows, { timeout: UI_READY });

      const pager = await readPager(page);

      // Assertion 2: the window never exceeds four page numbers and always
      // includes page 1.
      expect(pager.numbers.length).toBeLessThanOrEqual(4);
      expect(pager.numbers).toContain(1);
      // Assertion 5: the highest page number offered never exceeds currentPage + 1.
      expect(Math.max(...pager.numbers)).toBeLessThanOrEqual(expected.page + 1);

      expect(pager.numbers).toEqual(expected.numbers);
      expect(pager.current).toBe(expected.page);
      // Assertion 3: a gap (leading ellipsis) opens once the window leaves page 1.
      expect(pager.leadingEllipsis).toBe(expected.leading);
      expect(pager.trailingEllipsis).toBe(expected.trailing);
      expect(pager.prevDisabled).toBe(expected.prevDisabled);
      expect(pager.nextDisabled).toBe(expected.nextDisabled);
    });
  }

  // Assertion 4 (the most important one): on the true last page the trailing
  // ellipsis is gone and Next is disabled — the only signal the user has
  // reached the end.
  await test.step('final page: trailing ellipsis gone, Next disabled', async () => {
    const pager = await readPager(page);
    expect(pager.trailingEllipsis).toBe(false);
    expect(pager.nextDisabled).toBe(true);
    expect(pager.current).toBe(LAST_PAGE);
    await expect(traceNames).toHaveCount(LAST_PAGE_ROWS, { timeout: UI_READY });
  });

  // The pager's state used to be last-write-wins: `hasMore`/`provenNext` were
  // set only by the page that had just been fetched, and returning to an
  // already-cached page never re-invokes the datasource. So the terminal
  // page's `false` stuck, and one click of Back permanently disabled forward
  // navigation until a refresh. Page N must look the same whether it was
  // reached walking forward or coming back.
  await test.step('back from the terminal page: forward navigation survives', async () => {
    const backTo = LAST_PAGE - 1;
    const expected = EXPECTED_WINDOWS[backTo - 1];
    await page.locator('button[aria-label="Previous page"]:visible').click();
    await waitForCurrentPage(page, backTo);
    await expect(traceNames).toHaveCount(expected.rows, { timeout: UI_READY });

    const pager = await readPager(page);
    expect(pager.numbers).toEqual(expected.numbers);
    expect(pager.current).toBe(backTo);
    expect(pager.leadingEllipsis).toBe(expected.leading);
    // The trailing ellipsis returns: pages beyond this one are known to exist.
    expect(pager.trailingEllipsis).toBe(true);
    expect(pager.prevDisabled).toBe(false);
    expect(pager.nextDisabled).toBe(false);

    // Not just cosmetic — Next has to actually move forward again.
    await page.locator('button[aria-label="Next page"]:visible').click();
    await waitForCurrentPage(page, LAST_PAGE);
    await expect(traceNames).toHaveCount(LAST_PAGE_ROWS, { timeout: UI_READY });
  });

  // A page-size change must reach the API, not just relabel the control, and
  // it must restart pagination rather than leave the pager counting pages in
  // the old units. Asserted on the outgoing request and on the pager reset
  // that has to follow it.
  await test.step('page size change reaches the server and resets the pager', async () => {
    const RESIZED_PAGE_SIZE = 25;
    const resized = page.waitForResponse(
      (r) => r.url().includes(TRACE_LIST_PATH)
        && r.url().includes(`page_size=${RESIZED_PAGE_SIZE}`) && r.ok(),
      { timeout: UI_READY });
    await page.locator('[aria-label="Results per page"]:visible').click();
    await page.getByRole('option', { name: String(RESIZED_PAGE_SIZE), exact: true }).click();
    await resized;

    await waitForCurrentPage(page, 1);
    await expect(traceNames).toHaveCount(RESIZED_PAGE_SIZE, { timeout: UI_READY });
    await expect(page.locator('[aria-label="Results per page"]:visible'))
      .toContainText(String(RESIZED_PAGE_SIZE));

    // 45 rows at 25/page is two pages, so the pager is back to its page-1
    // shape — counted in the *new* page size, not the old one.
    const pager = await readPager(page);
    expect(pager.numbers).toEqual([1, 2]);
    expect(pager.current).toBe(1);
    expect(pager.leadingEllipsis).toBe(false);
    expect(pager.trailingEllipsis).toBe(true);
    expect(pager.prevDisabled).toBe(true);
    expect(pager.nextDisabled).toBe(false);
  });

  await req.dispose();
});
