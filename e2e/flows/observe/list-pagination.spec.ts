import { request } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL, StateProbe } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedCallExecutions } from '../../lib/simulate-seed';
import type { TestActor } from '../../lib/provisioning';

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
// instance. `getByRole` is accessibility-tree aware — MUI hides the inactive
// grid's Box with `display: none`, which removes it from the tree — so
// `.filter({ visible: true })` is a belt-and-braces second check, not the
// only one, unlike the raw CSS `:visible` pseudo-class the plain
// `button[aria-label=...]` selectors below needed.
function pagerButton(page: Page, name: string | RegExp) {
  return page.getByRole('button', { name }).filter({ visible: true });
}

/** The `<Stack>` that owns Previous, the page numbers, both ellipses and
 * Next — the "pager subtree" INT-01/INT-02/INT-03 attach listeners and
 * observers to. Derived from Previous page's own DOM ancestry rather than a
 * dedicated test id, since nothing about this container is otherwise
 * addressable. */
function pagerRoot(page: Page): Locator {
  return pagerButton(page, 'Previous page').locator(
    'xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " MuiStack-root ")][1]',
  );
}

function waitForCurrentPage(page: Page, pageNumber: number) {
  return expect(
    pagerButton(page, `Go to page ${pageNumber}`).and(page.locator('[aria-current="page"]')),
  ).toBeVisible({ timeout: UI_READY });
}

async function readPager(page: Page): Promise<PagerState> {
  // The pager keeps "Loading page…" up until the datasource has published the
  // page and AG Grid has painted it, so this is the only point at which the
  // window, the ellipses and Next are all settled. Reading before it can catch
  // the optimistic mid-transition state.
  await expect(page.getByText('Loading page…')).toHaveCount(0, { timeout: UI_READY });
  const pageButtons = pagerButton(page, /^Go to page \d+$/);
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
    leadingEllipsis: (await page.getByTestId('pager-leading-ellipsis').filter({ visible: true }).count()) > 0,
    trailingEllipsis: (await page.getByTestId('pager-trailing-ellipsis').filter({ visible: true }).count()) > 0,
    nextDisabled: await pagerButton(page, 'Next page').isDisabled(),
    prevDisabled: await pagerButton(page, 'Previous page').isDisabled(),
  };
}

/**
 * Seeds `count` traces into one fresh project (first trace sent alone so
 * project auto-create settles before the rest arrive in parallel batches —
 * same reasoning as OBS-E2E-004's own seed step) and waits for all of them to
 * land in CH before returning the project id. Shared by every test below that
 * just needs "N traces, one project" and does not otherwise duplicate
 * OBS-E2E-004's own inline seed, which stays as-is to avoid touching a test
 * already pinned against a passing run.
 */
async function seedTraceProject(
  req: APIRequestContext, probe: StateProbe, actor: TestActor, count: number, label: string,
): Promise<string> {
  const suffix = `${label}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const projectName = `e2e-${suffix}`;
  const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName };

  const first = await sendTrace(req, { ...cfg, rootName: `e2e.${suffix}-0` });
  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: first.traceId });
    return Number(rows[0].n);
  }, POLL.SPAN_VISIBLE).toBe(first.spanIds.length);

  const remaining = Array.from({ length: count - 1 }, (_, i) => `e2e.${suffix}-${i + 1}`);
  for (let i = 0; i < remaining.length; i += 10) {
    const batch = remaining.slice(i, i + 10);
    // eslint-disable-next-line no-await-in-loop
    await Promise.all(batch.map((rootName) => sendTrace(req, { ...cfg, rootName })));
  }

  const projects = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [projectName, actor.organizationId]);
  expect(projects).toHaveLength(1);
  const projectId = projects[0].id;

  await expect.poll(async () => {
    const rows = await probe.ch<{ n: string }>(
      'SELECT count(DISTINCT trace_id) AS n FROM spans FINAL WHERE project_id = {p:UUID}', { p: projectId });
    return Number(rows[0].n);
  }, SEED_VISIBLE).toBe(count);

  return projectId;
}

/** Hides the dev-only React Query Devtools toggle, which floats over the
 * pager in attach mode (Vite dev server) and intercepts its clicks — see
 * task-8-report.md workaround #3. Not part of the product. */
async function openObserveList(page: Page, url: string) {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.addStyleTag({ content: '.tsqd-parent-container { display: none !important; }' });
}

/**
 * Installs capture-phase `pointerdown`/`pointerup`/`click` counters on the
 * pager root (INT-01/INT-02). Capture phase so a remounted label between
 * press and release still gets counted by the (stable) ancestor listener,
 * even though the label itself changed identity.
 */
async function installDwellCounters(page: Page): Promise<void> {
  const root = pagerRoot(page);
  await root.evaluate((el) => {
    const w = window as unknown as { __dwellCounts?: Record<string, number> };
    w.__dwellCounts = { pointerdown: 0, pointerup: 0, click: 0 };
    (['pointerdown', 'pointerup', 'click'] as const).forEach((type) => {
      el.addEventListener(type, () => {
        w.__dwellCounts![type] += 1;
      }, true);
    });
  });
}

async function resetDwellCounters(page: Page): Promise<void> {
  await page.evaluate(() => {
    (window as unknown as { __dwellCounts: Record<string, number> }).__dwellCounts =
      { pointerdown: 0, pointerup: 0, click: 0 };
  });
}

async function readDwellCounters(page: Page): Promise<{ pointerdown: number; pointerup: number; click: number }> {
  return page.evaluate(() => (
    window as unknown as { __dwellCounts: { pointerdown: number; pointerup: number; click: number } }
  ).__dwellCounts);
}

/**
 * A raw mouse press-dwell-release on `button`, standing in for a real user's
 * imprecise click. `locator.click()` cannot reproduce the bug INT-01/INT-02
 * exist for: it resolves the element and presses+releases within a single
 * tick, so it passes even on a build where the label remounts between press
 * and release. A dwell gives an in-flight ancestor re-render (the pager's
 * parent re-renders ~26x/s from AG Grid's `ColumnAnimationService` loop — see
 * `assertAncestorIsChurning`) a real window to land inside.
 */
async function dwellClick(page: Page, button: Locator): Promise<void> {
  const box = await button.boundingBox();
  if (!box) throw new Error('dwellClick: button has no bounding box (not visible?)');
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  // Deliberate dwell — the only hardcoded wait in this file. 250-400ms gives
  // the ~26/s ancestor re-render loop several chances to land between
  // pointerdown and pointerup, which is exactly the window the pre-fix build
  // loses the click in (see CursorGridPagination.jsx's BackLabel/NextLabel
  // comment and commit e7256001b). A shorter dwell risks missing every
  // re-render by luck; a longer one buys nothing back.
  await page.waitForTimeout(300);
  await page.mouse.up();
}

// The ~26/s ancestor re-render loop INT-01/INT-02/INT-03 depend on is a
// pre-existing AG Grid `ColumnAnimationService` feedback loop, not something
// this suite drives — so a quiet render tree (e.g. a future fix that damps
// it) must not make these tests pass vacuously. `.clean-data-table`'s own
// parent Box is the nearest true DOM ancestor of *both* the grid and the
// pager (siblings under the same `gridElementRef` in TraceGrid.jsx), so
// mutations there track the same re-render frequency that broke the pager.
const CHURN_WINDOW_MS = 1_000;
const CHURN_MIN_MUTATIONS = 10;

function observeAncestor(page: Page): Locator {
  return page.locator('.clean-data-table:visible').locator('xpath=..');
}

/**
 * INT-06 guard: proves the pager's ancestor is actually re-rendering before
 * INT-01/INT-02 trust a passing dwell-click. If this loop is ever damped or
 * fixed, those tests would otherwise silently degrade into plain click tests
 * that pass whether or not the label-remount bug is still guarded against.
 * Skips (with a clear reason) instead of asserting nothing.
 */
async function assertAncestorIsChurning(page: Page): Promise<void> {
  const handle = await observeAncestor(page).elementHandle();
  if (!handle) throw new Error('assertAncestorIsChurning: pager ancestor not found');
  const mutations = await handle.evaluate((el, windowMs) => new Promise<number>((resolve) => {
    let count = 0;
    const observer = new MutationObserver((records) => {
      count += records.length;
    });
    observer.observe(el, { childList: true, subtree: true, attributes: true });
    setTimeout(() => {
      observer.disconnect();
      resolve(count);
    }, windowMs);
  }), CHURN_WINDOW_MS);
  test.skip(
    mutations < CHURN_MIN_MUTATIONS,
    `pager ancestor did not churn (${mutations} DOM mutations/${CHURN_WINDOW_MS}ms, need >= `
    + `${CHURN_MIN_MUTATIONS}) — the pre-existing AG Grid re-render loop this guard depends on `
    + 'was not observed, so INT-01/INT-02 would pass vacuously rather than exercising the '
    + 'label-remount race. Skipping instead of asserting nothing (brief: implement INT-06 as a guard).',
  );
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
      await pagerButton(page, 'Next page').click();
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

      // PAG-01/NEG-02/NEG-06: the window is exactly `{1} ∪ {cur-1, cur, cur+1}`
      // (clipped to what Next has actually proven), derived independently
      // from `windowedPageNumbers`'s own formula rather than read back off
      // the hand-authored EXPECTED_WINDOWS table above — this is what proves
      // that table is itself correct, not just self-consistent.
      const provenHighest = expected.nextDisabled ? expected.page : expected.page + 1;
      const formulaWindow = Array.from(
        new Set([1, expected.page - 1, expected.page, provenHighest].filter((n) => n >= 1)),
      ).sort((a, b) => a - b);
      expect(pager.numbers).toEqual(formulaWindow);
      // NEG-02: the trailing ellipsis is present at every page except the
      // terminal one — i.e. exactly when Next is still enabled.
      expect(pager.trailingEllipsis).toBe(!expected.nextDisabled);
    });
  }
  // NEG-06 (no ellipsis on the terminal page) is asserted below in "final
  // page: trailing ellipsis gone, Next disabled" — the last iteration above
  // already covers every non-terminal page.

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
    await pagerButton(page, 'Previous page').click();
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
    await pagerButton(page, 'Next page').click();
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

test('OBS-E2E-005: Next stays usable through a full Back-Back-Next-Next round trip from the terminal page', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-005', area: 'observe',
    userGoal: 'A developer bouncing back and forth near the end of a trace list never loses forward navigation',
    steps: ['seed 25 traces into one project over OTLP',
            "open the project's trace list at page size 10 (3 pages)",
            'walk forward to the true last page',
            'step back twice, then forward twice',
            'confirm Next stays enabled throughout and the terminal page looks the same either way it was reached'],
    backendChecks: ['all 25 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const req = await request.newContext();
  const TOTAL = 25;
  const SIZE = 10;
  const TERMINAL = 3;
  const TERMINAL_ROWS = TOTAL - SIZE * (TERMINAL - 1);
  const projectId = await seedTraceProject(req, probe, actor, TOTAL, 'pag03');
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await openObserveList(page, `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`);
  await expect(traceNames).toHaveCount(TOTAL, { timeout: UI_READY });

  const resized = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`page_size=${SIZE}`) && r.ok(),
    { timeout: UI_READY });
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: String(SIZE), exact: true }).click();
  await resized;
  await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
  await waitForCurrentPage(page, 1);

  // AG Grid serves an already-visited page out of its own server-side block
  // cache without re-invoking the datasource (see the `pagerFlagsForPage`
  // comment in listPagerState.js), so a click that lands on a page fetched
  // earlier in this same walk produces no new network request. Only wait for
  // the pager's own DOM state, not a response — the first forward walk below
  // is the only leg guaranteed to hit the wire on every step.
  const goToNext = async (target: number) => {
    await pagerButton(page, 'Next page').click();
    await waitForCurrentPage(page, target);
  };
  const goToPrevious = async (target: number) => {
    await pagerButton(page, 'Previous page').click();
    await waitForCurrentPage(page, target);
  };

  await test.step('walk forward to the terminal page', async () => {
    const advanced = page.waitForResponse(
      (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
      { timeout: UI_READY });
    await pagerButton(page, 'Next page').click();
    await advanced;
    await waitForCurrentPage(page, 2);
    const advancedAgain = page.waitForResponse(
      (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
      { timeout: UI_READY });
    await pagerButton(page, 'Next page').click();
    await advancedAgain;
    await waitForCurrentPage(page, TERMINAL);
    await expect(traceNames).toHaveCount(TERMINAL_ROWS, { timeout: UI_READY });
  });

  const terminalFirstRowId = await traceNames.first().textContent();

  await test.step('Back, Back, Next, Next — Next never disables, terminal page is identical either way', async () => {
    await goToPrevious(2);
    expect((await readPager(page)).nextDisabled).toBe(false);

    await goToPrevious(1);
    expect((await readPager(page)).nextDisabled).toBe(false);

    await goToNext(2);
    expect((await readPager(page)).nextDisabled).toBe(false);

    await goToNext(TERMINAL);
    const pager = await readPager(page);
    expect(pager.nextDisabled).toBe(true);
    expect(pager.trailingEllipsis).toBe(false);
    await expect(traceNames).toHaveCount(TERMINAL_ROWS, { timeout: UI_READY });
    // NEG-07: neither control is disabled anywhere but the true endpoints —
    // Previous is enabled again (this is not page 1) and Next is disabled
    // only because this genuinely is the terminal page, not a stale leftover
    // from the earlier visit that a naive last-write-wins pager would keep.
    expect(pager.prevDisabled).toBe(false);
    expect(await traceNames.first().textContent()).toBe(terminalFirstRowId);
  });

  await req.dispose();
});

test('OBS-E2E-006: an exactly-full final page ends pagination without offering a phantom next page', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-006', area: 'observe',
    userGoal: 'A developer whose trace count divides evenly by the page size sees a real last page, not an empty page N+1',
    steps: ['seed 30 traces (exactly 3 full pages of 10) into one project over OTLP',
            "open the project's trace list at page size 10",
            'walk forward to the third page',
            'confirm no fourth page is offered, Next is disabled, and the grid is not empty'],
    backendChecks: ['all 30 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const req = await request.newContext();
  const TOTAL = 30;
  const SIZE = 10;
  const projectId = await seedTraceProject(req, probe, actor, TOTAL, 'edg01');
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await openObserveList(page, `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`);
  await expect(traceNames).toHaveCount(25, { timeout: UI_READY });

  const resized = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`page_size=${SIZE}`) && r.ok(),
    { timeout: UI_READY });
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: String(SIZE), exact: true }).click();
  await resized;
  await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
  await waitForCurrentPage(page, 1);

  for (const target of [2, 3]) {
    // eslint-disable-next-line no-await-in-loop
    await test.step(`walk to page ${target}`, async () => {
      const advanced = page.waitForResponse(
        (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
        { timeout: UI_READY });
      await pagerButton(page, 'Next page').click();
      await advanced;
      await waitForCurrentPage(page, target);
    });
  }

  const pager = await readPager(page);
  // EDG-01: page 3 (30 / 10) is terminal — no page 4 is ever offered, Next is
  // disabled, and the last page is a real full page, not an empty one.
  expect(pager.numbers).not.toContain(4);
  expect(pager.nextDisabled).toBe(true);
  expect(pager.trailingEllipsis).toBe(false);
  await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
  await expect(traceNames.first()).toBeVisible();

  await req.dispose();
});

test('OBS-E2E-007: has_more without a strictly greater total promises no page number, but keeps Next enabled', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-007', area: 'observe',
    userGoal: 'A developer searching a sparse cursor window is never shown a page number the transport cannot prove exists',
    steps: ['seed 25 traces into one project over OTLP',
            "open the project's trace list at page size 10",
            'intercept the page-2 response to report has_more=true with a total equal to the rows already seen',
            'walk to page 2',
            'confirm no cur+1 page number is offered, while Next stays enabled'],
    backendChecks: ['all 25 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const req = await request.newContext();
  const TOTAL = 25;
  const SIZE = 10;
  const projectId = await seedTraceProject(req, probe, actor, TOTAL, 'neg01');
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await openObserveList(page, `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`);
  await expect(traceNames).toHaveCount(25, { timeout: UI_READY });

  const resized = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`page_size=${SIZE}`) && r.ok(),
    { timeout: UI_READY });
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: String(SIZE), exact: true }).click();
  await resized;
  await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
  await waitForCurrentPage(page, 1);

  // Rewrite exactly one response: keep `has_more: true` (there IS more window
  // to search) but set the reported total equal to the rows the UI will have
  // seen after this page (`seen`), not strictly greater than it — the one
  // condition under which `getListPagerState` must not promise a page number
  // for it (list_cursor.py:518-520; see the `provenNext` comment in
  // `listPagerState.js`). This state is not reachable by seeding real rows —
  // it depends on the exact relationship between a lower-bound total and the
  // cursor search window, an internal transport detail — so it is produced by
  // intercepting one real response and adjusting only its metadata, not its
  // rows.
  let rewritten = false;
  await page.route(
    (url) => url.pathname.includes(TRACE_LIST_PATH) && url.searchParams.get('project_id') === projectId,
    async (route) => {
      if (rewritten) { await route.continue(); return; }
      rewritten = true;
      const response = await route.fetch();
      const body = await response.json();
      const seen = SIZE * 2; // page 1 (10 rows) + this page (10 rows)
      body.result.metadata.has_more = true;
      body.result.metadata.total_rows = seen;
      body.result.metadata.total_rows_is_lower_bound = false;
      await route.fulfill({ response, json: body });
    },
  );

  const advanced = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
    { timeout: UI_READY });
  await pagerButton(page, 'Next page').click();
  await advanced;
  await waitForCurrentPage(page, 2);

  const pager = await readPager(page);
  // NEG-01: no page 3 button — the reported total does not strictly exceed
  // what's already been seen, so a further page is searchable but not proven.
  expect(pager.numbers).toEqual([1, 2]);
  expect(pager.numbers).not.toContain(3);
  // Next stays enabled: has_more says there is more window left to search,
  // even though no page number can honestly be drawn for it yet.
  expect(pager.nextDisabled).toBe(false);

  await req.dispose();
});

test('OBS-E2E-008: the Next label DOM node survives ~1.5s of ancestor re-render churn', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-008', area: 'observe',
    userGoal: "A developer's pointer never lands on a button whose label React just tore down and rebuilt underneath it",
    steps: ['seed 15 traces into one project over OTLP',
            "open the project's trace list at page size 10 (Next enabled)",
            "confirm the pager's ancestor is actually re-rendering rapidly (INT-06 guard)",
            "capture the Next label's DOM node and watch the pager subtree for ~1.5s",
            'confirm the node is never removed from the DOM and stays connected'],
    backendChecks: ['all 15 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(120_000);
  const req = await request.newContext();
  const TOTAL = 15;
  const SIZE = 10;
  const projectId = await seedTraceProject(req, probe, actor, TOTAL, 'int03');
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await openObserveList(page, `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`);
  await expect(traceNames).toHaveCount(TOTAL, { timeout: UI_READY });

  const resized = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`page_size=${SIZE}`) && r.ok(),
    { timeout: UI_READY });
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: String(SIZE), exact: true }).click();
  await resized;
  await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
  expect((await readPager(page)).nextDisabled).toBe(false);

  await assertAncestorIsChurning(page);

  const nextButton = pagerButton(page, 'Next page');
  const buttonHandle = await nextButton.elementHandle();
  if (!buttonHandle) throw new Error('Next button not found');
  const labelHandle = await nextButton.evaluateHandle((el) => el.querySelector('div'));

  // Watch specifically for *this* node being removed, not just "something
  // changed" in the button's subtree — MUI's own ripple `<span>` legitimately
  // mounts/unmounts on focus/hover independent of this bug, so a raw
  // mutation/removedNodes *count* would over-fire on a correct build. ~1.5s
  // of live churn is long enough to comfortably exceed the >= 10
  // mutations/1s the INT-06 guard above already proved is happening, giving
  // several ancestor re-renders a real chance to have remounted this node if
  // the label were still an inline-arrow `slots` component (pre-fix shape).
  const labelSurvived = await buttonHandle.evaluate((button, label) => new Promise<boolean>((resolve) => {
    let removed = false;
    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (Array.from(record.removedNodes).includes(label as Node)) removed = true;
      }
    });
    observer.observe(button, { childList: true, subtree: true });
    setTimeout(() => {
      observer.disconnect();
      resolve(!removed);
    }, 1500);
  }), labelHandle);

  expect(labelSurvived).toBe(true);
  expect(await labelHandle.evaluate((el) => el?.isConnected)).toBe(true);

  await req.dispose();
});

test('OBS-E2E-009: a real dwell-click on Next/Back actually fires a click, not just a press', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-009', area: 'observe',
    userGoal: "A developer's mouse press on Back/Next always produces a click, even while the ancestor is mid-re-render",
    steps: ['seed 15 traces into one project over OTLP',
            "open the project's trace list at page size 10",
            "confirm the pager's ancestor is actually re-rendering rapidly (INT-06 guard)",
            'install capture-phase pointer/click counters on the pager root',
            'press-dwell-release on Next and confirm the page advanced with pointerdown === click === 1',
            'repeat the same dwell-click on Back'],
    backendChecks: ['all 15 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(120_000);
  const req = await request.newContext();
  const TOTAL = 15;
  const SIZE = 10;
  const projectId = await seedTraceProject(req, probe, actor, TOTAL, 'int0102');
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await openObserveList(page, `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`);
  await expect(traceNames).toHaveCount(TOTAL, { timeout: UI_READY });

  const resized = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`page_size=${SIZE}`) && r.ok(),
    { timeout: UI_READY });
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: String(SIZE), exact: true }).click();
  await resized;
  await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });

  await assertAncestorIsChurning(page);
  await installDwellCounters(page);

  await test.step('INT-01/INT-02: dwell-click Next actually advances the page', async () => {
    await resetDwellCounters(page);
    const advanced = page.waitForResponse(
      (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
      { timeout: UI_READY });
    await dwellClick(page, pagerButton(page, 'Next page'));
    await advanced;
    await waitForCurrentPage(page, 2);
    const counts = await readDwellCounters(page);
    // INT-02: distinguishes "the press landed but no click fired" (the bug)
    // from "the click fired and the handler was wrong" — assert the raw
    // counters, not just that the page moved.
    expect(counts.pointerdown).toBe(1);
    expect(counts.click).toBe(1);
    expect(counts.click).toBe(counts.pointerdown);
  });

  await test.step('INT-01/INT-02: dwell-click Back actually returns to page 1', async () => {
    // Page 1 was already fetched on initial load, so AG Grid serves it back
    // out of its own server-side block cache rather than re-invoking the
    // datasource (see the `pagerFlagsForPage` comment in listPagerState.js) —
    // unlike the Next leg above, this click is not guaranteed to hit the
    // wire, so only the pager's own DOM state is awaited here.
    await resetDwellCounters(page);
    await dwellClick(page, pagerButton(page, 'Previous page'));
    await waitForCurrentPage(page, 1);
    const counts = await readDwellCounters(page);
    expect(counts.pointerdown).toBe(1);
    expect(counts.click).toBe(1);
    expect(counts.click).toBe(counts.pointerdown);
  });

  await req.dispose();
});

test('OBS-E2E-010: changing page size changes the outbound page_size, the rendered row count, and resets to page 1', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-010', area: 'observe',
    userGoal: 'A developer who changes results-per-page gets exactly that many rows and starts back at page 1, not a stale mid-list position',
    steps: ['seed 60 traces into one project over OTLP',
            "open the project's trace list at the default page size",
            'change the page size control to 10',
            'confirm the outbound request carries page_size=10, the grid renders 10 rows, and the pager is back on page 1'],
    backendChecks: ['all 60 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const req = await request.newContext();
  const TOTAL = 60;
  const NEW_SIZE = 10;
  const projectId = await seedTraceProject(req, probe, actor, TOTAL, 'pag04');
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await openObserveList(page, `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`);
  await expect(traceNames).toHaveCount(25, { timeout: UI_READY });

  const resized = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`page_size=${NEW_SIZE}`) && r.ok(),
    { timeout: UI_READY });
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: String(NEW_SIZE), exact: true }).click();
  const response = await resized;

  // PAG-04: the request itself, not just the UI, carries the new page size.
  expect(new URL(response.url()).searchParams.get('page_size')).toBe(String(NEW_SIZE));

  await expect(traceNames).toHaveCount(NEW_SIZE, { timeout: UI_READY });
  await waitForCurrentPage(page, 1);
  const pager = await readPager(page);
  expect(pager.current).toBe(1);
  expect(pager.prevDisabled).toBe(true);

  await req.dispose();
});

test('OBS-E2E-011: the agent call-log pager (a plain DRF-paginated, non-cursor screen) still paginates and reaches its last row', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-011', area: 'observe',
    userGoal: "A developer browsing an agent version's call logs gets a working pager even though this screen has no cursor `has_more` contract",
    steps: ["seed 12 completed CallExecution rows for one fresh AgentDefinition/AgentVersion directly through the backend (no simulate/voice infra runs in this harness — see e2e/lib/simulate-seed.ts)",
            "open the agent's Call Logs tab for that version",
            'confirm more than one page is offered',
            'walk forward to the last page'],
    backendChecks: ['the seeded CallExecution rows are scoped to the seeded AgentVersion, status=completed, non-empty eval_outputs — exactly what AgentVersionCallExecutionView filters for'],
  }),
}, async ({ page, actor }, testInfo) => {
  test.setTimeout(120_000);
  const CALL_EXECUTION_COUNT = 12;
  const seed = seedCallExecutions({
    organizationId: actor.organizationId,
    workspaceId: actor.workspaceId,
    count: CALL_EXECUTION_COUNT,
  });
  await openObserveList(
    page,
    `/dashboard/simulate/agent-definitions/${seed.agentDefinitionId}?activeTab=CallLogs&version=${seed.agentVersionId}`,
  );

  const callRows = page.locator('.clean-data-table:visible .ag-row');
  await expect(callRows).toHaveCount(10, { timeout: UI_READY });

  // This screen's `page_size` query param is a no-op server-side — DRF's
  // `ExtendedPageNumberPagination` (futureagi/tfc/utils/pagination.py) reads
  // `limit`, not `page_size`, and defaults to a fixed 10 regardless of what
  // the UI sends. The default results-per-page here is 25, so without this
  // the frontend's own block-size bookkeeping (still believing 25/page)
  // disagrees with the real 10-per-page server response and over-fetches
  // into a 404 page 3 that does not exist. Selecting "10" coincidentally
  // re-aligns them — not a fix for that mismatch (out of scope here; noted
  // in the report), just what makes this pager screen's own page count and
  // Next/Back state internally consistent for the assertions below.
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: '10', exact: true }).click();
  await expect(callRows).toHaveCount(10, { timeout: UI_READY });
  await waitForCurrentPage(page, 1);

  const pager = await readPager(page);
  // PAG-05: this screen's response has no `has_more` field at all — a plain
  // DRF `count`/`next`/`previous` payload (ExtendedPageNumberPagination,
  // futureagi/tfc/utils/pagination.py) — yet the shared CursorGridPagination
  // still offers a real second page for it.
  expect(pager.numbers.length).toBeGreaterThan(1);
  expect(pager.nextDisabled).toBe(false);

  // AG Grid's infinite row model prefetches the next block ahead of the
  // visible one (visible in the trace as page 2 already fetched during
  // initial load, plus a 404 probe for a page 3 that does not exist), so
  // this click is served from that cache rather than guaranteed to hit the
  // wire — wait on the pager's own DOM state, not a response (same reasoning
  // as the cached-page legs in OBS-E2E-005/OBS-E2E-009).
  await pagerButton(page, 'Next page').click();
  await waitForCurrentPage(page, 2);

  await expect(callRows).toHaveCount(seed.callExecutionCount - 10, { timeout: UI_READY });
  const finalPager = await readPager(page);
  expect(finalPager.nextDisabled).toBe(true);
});

test('OBS-E2E-012: changing the date filter resets pagination to page 1 and drops the old cursor', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-012', area: 'observe',
    userGoal: 'A developer who narrows the date range never sees stale rows or a stale page position from the filter they just replaced',
    steps: ['seed 45 traces into one project over OTLP',
            "open the project's trace list at page size 10, date range Past 12M",
            'walk forward three pages (into the window where a leading ellipsis has opened)',
            'switch the date filter to Past 30D',
            'confirm the pager resets to page 1 with its page-1 shape, and the resulting request carries no stale cursor'],
    backendChecks: ['all 45 seeded trace_ids present in CH `spans` (FINAL) under the auto-created project'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(180_000);
  const req = await request.newContext();
  const TOTAL = 45;
  const SIZE = 10;
  const projectId = await seedTraceProject(req, probe, actor, TOTAL, 'pag12');
  const traceNames = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]');

  await openObserveList(page, `/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`);
  await expect(traceNames).toHaveCount(25, { timeout: UI_READY });

  const resized = page.waitForResponse(
    (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`page_size=${SIZE}`) && r.ok(),
    { timeout: UI_READY });
  await page.locator('[aria-label="Results per page"]:visible').click();
  await page.getByRole('option', { name: String(SIZE), exact: true }).click();
  await resized;
  await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
  await waitForCurrentPage(page, 1);

  await test.step('set the date range to Past 12M', async () => {
    const toTwelveMonths = page.waitForResponse(
      (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
      { timeout: UI_READY });
    await page.getByRole('button', { name: 'Past 7D' }).click();
    await page.getByRole('menuitem', { name: 'Past 12M' }).click();
    await toTwelveMonths;
    await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
    await waitForCurrentPage(page, 1);
  });

  await test.step('walk forward three pages, opening a leading ellipsis', async () => {
    for (const target of [2, 3, 4]) {
      const advanced = page.waitForResponse(
        (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
        { timeout: UI_READY });
      // eslint-disable-next-line no-await-in-loop
      await pagerButton(page, 'Next page').click();
      // eslint-disable-next-line no-await-in-loop
      await advanced;
      // eslint-disable-next-line no-await-in-loop
      await waitForCurrentPage(page, target);
    }
    const pager = await readPager(page);
    expect(pager.current).toBe(4);
    expect(pager.leadingEllipsis).toBe(true);
  });

  await test.step('switch the date filter — pagination resets, no stale cursor on the wire', async () => {
    const afterFilter = page.waitForResponse(
      (r) => r.url().includes(TRACE_LIST_PATH) && r.url().includes(`project_id=${projectId}`) && r.ok(),
      { timeout: UI_READY });
    await page.getByRole('button', { name: 'Past 12M' }).click();
    await page.getByRole('menuitem', { name: 'Past 30D' }).click();
    const response = await afterFilter;

    // The result set is unchanged (all 45 traces are recent), so the reset
    // page 1 has the exact same shape as any fresh page 1 in this file —
    // proof this is a real reset, not a coincidental smaller window.
    await waitForCurrentPage(page, 1);
    await expect(traceNames).toHaveCount(SIZE, { timeout: UI_READY });
    const pager = await readPager(page);
    expect(pager.current).toBe(1);
    expect(pager.numbers).toEqual([1, 2]);
    expect(pager.leadingEllipsis).toBe(false);

    // The part a UI-only assertion would miss: a stale cursor from the old
    // (pre-filter) page 4 would silently keep returning rows from the old
    // result set. A fresh filter's page-1 request never carries `cursor=`
    // (listCursorPagination.js `requestParams`, the `pageNumber === 0` branch).
    expect(new URL(response.url()).searchParams.has('cursor')).toBe(false);
  });

  await req.dispose();
});

// NOTE for future work: the window cap asserted throughout this file (at most
// four page numbers — PAG-01/NEG-02, `pager.numbers.length` <= 4, and the
// `formulaWindow`/EXPECTED_WINDOWS shapes) is pinned to the *current*
// `windowedPageNumbers` formula in listPagerState.js. There is an approved
// follow-up to widen the window to five numbers to make room for a frontier
// boundary (`1 … 4 [5] 6 … 11 …`) — a separate branch, not done here. Every
// hard-coded "4" / window-shape assertion above will need updating when that
// lands; nothing in this file pre-empts it.
