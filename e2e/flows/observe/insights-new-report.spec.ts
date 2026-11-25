import { request } from '@playwright/test';
import type { Request, Route } from '@playwright/test';
import { test, expect } from '../../lib/licensed-customer';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// The licensed customer's org may use Insights (lib/licensed-customer.ts,
// e2e/stack/backend/settings_e2e.py). A run's prepare stage needs a managed
// model token the stack doesn't have, so every run ends failed there: this flow
// proves the report is started on Sessions, not what the report says.

// Insights/insightsApi.js useHasSessions → observePopulation.js collectPopulation:
// the New report form asks the Sessions list for one row of the whole project to
// pick its default unit. readQuery (utils/axios.js) sends it as a POST read; body
// captured with page.on('request') from the running app:
// {"project_id":…,"page_number":0,"page_size":1,"filters":"[]"}.
const SESSION_LIST = '/tracer/trace-session/list_sessions/';
// insightsApi.js fetchScopeCount (axios.js insightsEstimate) and startRun
// (axios.js insightsRuns); tracer/views/insights.py answers runs/ with
// {run_id, config_id}. The page also posts {project_id, action} to the estimate
// for its action credits (useActionCredits); only bodies with a unit are
// population counts (captured with page.on('request') from the running app).
const ESTIMATE = '/tracer/insights/estimate/';
const RUNS = '/tracer/insights/runs/';
// InstantEval/EvaluateMenu.jsx action title on the Traces view.
const FIND_PATTERNS = /Find patterns/;
// NewReport.jsx: the unit toggle group, its buttons (observePopulation.js
// UNITS[*].title) and the primary action; ReportRail.jsx: the rail's New report.
const UNIT_GROUP = 'What each item is';
const RUN_REPORT = 'Run report';
const NEW_REPORT = 'New report';
// The seed: session A has two traces, session B one, so the Sessions count is 2.
const TRACES_PER_SESSION = [2, 1];
// README's browser first-paint ceiling, not the default 10-second expect budget.
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

type Envelope<T> = { status: boolean; result: T };
type SpanSession = { id: string; project_id: string; session_id: string | null };
type ConfigRow = { id: string; unit: string; lens: string; name: string };
type RunRow = { id: string; config_id: string; unit: string; status: string };

test.use({ viewport: { width: 1600, height: 1100 } }); // the reports rail fits beside the form at ≥ 1440 (InsightsView.jsx RAIL_FITS_WIDTH)

test('OBS-E2E-063: choose Sessions for a new Insights report while the session check is still loading', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-063', area: 'observe',
    userGoal: 'Start a new Insights report on Sessions, even when I pick Sessions before the page has finished checking that the project has sessions (the report is started, its contents are not claimed: a run cannot finish in the E2E stack)',
    steps: [
      'seed three traces with session.id in two sessions in a minted project',
      'open the project\'s Traces view and choose Find patterns (a New report on Traces), then the rail\'s New report (a fresh form)',
      'hold the form\'s sessions check (the real request is delayed, then continued; nothing is stubbed)',
      'click the already-selected Sessions unit',
      'see the estimate go out for sessions and Run report become enabled while the check is still held',
      'release the check and click Run report',
    ],
    backendChecks: [
      'ClickHouse spans FINAL holds every seeded span with its trace_session_id: one session per label, two in all',
      'POST /tracer/insights/estimate/ was sent with unit sessions before the held sessions check was released, and counted the two sessions',
      'POST /tracer/insights/runs/ returns a run_id and config_id, and PG holds exactly one insights config (unit sessions) and one run for the actor org and project',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // SPAN_VISIBLE 15 + five UI_READY browser waits (menu, form, estimate,
  // Run enabled, run start) 300 + navigation and PG headroom 45.
  test.setTimeout(360_000);
  page.setDefaultTimeout(UI_READY);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-obs63-${suffix}`;
  const labels = [`e2e-obs63-a-${suffix}`, `e2e-obs63-b-${suffix}`];
  const seeded: { label: string; spanIds: string[] }[] = [];

  await test.step('seed: three traces in two sessions', async () => {
    const req = await request.newContext();
    try {
      for (const [index, label] of labels.entries()) {
        const spanIds: string[] = [];
        for (let turn = 0; turn < TRACES_PER_SESSION[index]; turn++) {
          const trace = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
            secretKey: actor.secretKey, projectName, resourceAttributes: { project_type: 'observe' },
            rootAttributes: { 'session.id': label }, childAttributes: { 'session.id': label, 'fi.span.kind': 'llm' } });
          spanIds.push(...trace.spanIds);
        }
        seeded.push({ label, spanIds });
      }
    } finally {
      await req.dispose();
    }
  });
  const allIds = seeded.flatMap(s => s.spanIds);

  const { projectId, sessionIds } = await test.step('backend check 1: spans carry their trace_session_id', async () => {
    const idsLiteral = allIds.map(id => `'${id}'`).join(','); // harness-generated 16-hex ids, not request input
    let facts: SpanSession[] = [];
    await expect.poll(async () => {
      facts = await probe.ch<SpanSession>(
        `SELECT id, toString(project_id) AS project_id, toString(trace_session_id) AS session_id
         FROM spans FINAL WHERE id IN (${idsLiteral})`);
      return facts.map(row => row.id).sort();
    }, POLL.SPAN_VISIBLE).toEqual([...allIds].sort());
    const projectIds = [...new Set(facts.map(row => row.project_id))];
    expect(projectIds).toHaveLength(1);
    const ids = seeded.map(({ spanIds }) => {
      const sessions = [...new Set(facts.filter(row => spanIds.includes(row.id)).map(row => row.session_id))];
      expect(sessions).toHaveLength(1);
      expect(sessions[0]).toMatch(/^[0-9a-f-]{36}$/);
      return sessions[0] as string;
    });
    expect(new Set(ids).size).toBe(2);
    // Each session id reads back exactly its own spans, project-wide.
    for (const [index, { spanIds }] of seeded.entries()) {
      const rows = await probe.ch<{ id: string }>(
        `SELECT id FROM spans FINAL WHERE project_id = {p:UUID} AND trace_session_id = {s:UUID}`,
        { p: projectIds[0], s: ids[index] });
      expect(rows.map(row => row.id).sort()).toEqual([...spanIds].sort());
    }
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({
      projectId: projectIds[0], projectName, organizationId: actor.organizationId,
      sessions: seeded.map((s, i) => ({ ...s, sessionId: ids[i] })),
    }) });
    return { projectId: projectIds[0], sessionIds: ids };
  });

  // Ordered log of the sessions check and the estimates, as the browser saw them.
  const events: string[] = [];
  const held: Route[] = [];
  let recording = false; // from the New report click: the Insights page counts traces on its own before it
  const isCheck = (req: Request) => {
    if (req.method() !== 'POST' || new URL(req.url()).pathname !== SESSION_LIST) return false;
    const body = req.postDataJSON() as { project_id?: string; page_size?: number; filters?: string };
    return body.project_id === projectId && body.page_size === 1 && body.filters === '[]';
  };
  const isCount = (req: Request) => req.method() === 'POST' && new URL(req.url()).pathname === ESTIMATE
    && typeof (req.postDataJSON() as { unit?: string }).unit === 'string';
  page.on('request', req => {
    if (recording && isCount(req)) events.push(`estimate:${(req.postDataJSON() as { unit: string }).unit}`);
  });
  page.on('response', res => { if (isCheck(res.request())) events.push('check-answered'); });
  const release = async () => {
    for (const route of held.splice(0)) {
      events.push('check-released');
      await route.continue();
    }
  };

  try {
    await test.step('UI: Find patterns opens Insights on Traces, and the rail\'s New report opens a fresh form', async () => {
      await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`,
        { waitUntil: 'domcontentloaded' });
      await page.getByRole('button', { name: 'Evaluate', exact: true }).click();
      // From the Traces view, Find patterns hands the view over: a New report
      // on Traces, already chosen (LLMTracingView.jsx openInsights), which
      // counts traces at once. Let that count land before recording.
      const tracesCounted = page.waitForResponse(res => isCount(res.request())
        && (res.request().postDataJSON() as { unit: string }).unit === 'traces', { timeout: UI_READY });
      await page.getByRole('menu').getByRole('menuitem').filter({ hasText: FIND_PATTERNS }).click();
      await ui(page).toHaveURL(new RegExp(`/dashboard/observe/${projectId}/insights\\?(.*&)?new=1`));
      await ui(page.getByRole('group', { name: UNIT_GROUP }).getByRole('button', { name: 'Traces', exact: true }))
        .toHaveAttribute('aria-pressed', 'true');
      await tracesCounted;
      // Hold the check from here on; only the one-row whole-project check is held.
      await page.route(`**${SESSION_LIST}**`, route => {
        if (!isCheck(route.request())) return route.continue();
        events.push('check-held');
        held.push(route);
      });
      recording = true;
      // The rail's New report starts a fresh form whose unit nobody has chosen.
      await page.getByRole('navigation', { name: 'Reports' }).getByRole('button', { name: NEW_REPORT, exact: true }).click();
      // InsightsView.jsx: ?new=1 with no seed is a form with nothing handed over.
      await ui(page).toHaveURL(new RegExp(`^(?!.*[?&]seed=).*/dashboard/observe/${projectId}/insights\\?(.*&)?new=1(&|$)`));
      await ui(page.getByRole('heading', { name: NEW_REPORT, exact: true })).toBeVisible();
    });

    const group = page.getByRole('group', { name: UNIT_GROUP });
    const sessions = group.getByRole('button', { name: 'Sessions', exact: true });
    const run = page.getByRole('button', { name: RUN_REPORT, exact: true });

    const estimate = await test.step('UI: clicking the shown Sessions unit counts sessions while the check is held', async () => {
      await ui.poll(() => events).toEqual(['check-held']);
      await ui(sessions).toHaveAttribute('aria-pressed', 'true');
      await ui(run).toBeDisabled();
      const counted = page.waitForResponse(res => isCount(res.request()), { timeout: UI_READY });
      await sessions.click();
      const response = await counted;
      expect(response.ok()).toBe(true);
      await ui(run).toBeEnabled();
      await ui(sessions).toHaveAttribute('aria-pressed', 'true');
      await testInfo.attach('form-while-held', { contentType: 'image/png', body: await page.screenshot() });
      return response;
    });

    await test.step('backend check 2: the estimate went out for sessions before the check was released', async () => {
      expect(events).toEqual(['check-held', 'estimate:sessions']);
      expect(held).toHaveLength(1);
      expect(estimate.request().postDataJSON()).toMatchObject({ project_id: projectId, unit: 'sessions' });
      const body = await estimate.json() as Envelope<{ population: number }>;
      await testInfo.attach('estimate', { contentType: 'application/json', body: JSON.stringify(body) });
      expect(body.result.population).toBe(sessionIds.length);
    });

    const started = await test.step('UI: release the check and run the report', async () => {
      await release();
      await ui.poll(() => events.includes('check-answered')).toBe(true);
      await ui(run).toBeEnabled();
      const response = page.waitForResponse(res => res.request().method() === 'POST'
        && new URL(res.url()).pathname === RUNS, { timeout: UI_READY });
      await run.click();
      const runs = await response;
      const body = await runs.json() as Envelope<{ run_id: string; config_id: string }>;
      await testInfo.attach('run', { contentType: 'application/json',
        body: JSON.stringify({ status: runs.status(), body }) });
      expect(runs.status()).toBe(200);
      expect(runs.request().postDataJSON()).toMatchObject({ project_id: projectId, config: { unit: 'sessions' } });
      expect(body.result.run_id).toMatch(/^[0-9a-f-]{36}$/);
      expect(body.result.config_id).toMatch(/^[0-9a-f-]{36}$/);
      await ui(page).toHaveURL(new RegExp(`[?&]report=${body.result.config_id}&run=${body.result.run_id}`));
      return body.result;
    });

    await test.step('backend check 3: PG holds one sessions report and its run for the actor org', async () => {
      const configs = await probe.pg<ConfigRow>(
        `SELECT id::text AS id, unit, lens, name FROM tracer_insight_config
         WHERE project_id = $1 AND organization_id = $2 AND deleted = false`,
        [projectId, actor.organizationId]);
      expect(configs.map(row => ({ id: row.id, unit: row.unit }))).toEqual([{ id: started.config_id, unit: 'sessions' }]);
      const runs = await probe.pg<RunRow>(
        `SELECT id::text AS id, config_id::text AS config_id, unit, status FROM tracer_insight_run
         WHERE project_id = $1 AND organization_id = $2 AND deleted = false`,
        [projectId, actor.organizationId]);
      await testInfo.attach('pg', { contentType: 'application/json', body: JSON.stringify({ configs, runs }) });
      expect(runs.map(row => ({ id: row.id, config_id: row.config_id, unit: row.unit })))
        .toEqual([{ id: started.run_id, config_id: started.config_id, unit: 'sessions' }]);
    });
  } finally {
    await release(); // nothing is left held, whichever step failed
  }
});
