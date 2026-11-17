import { test, expect } from '@playwright/test';
import { ApiClient } from '../../lib/api-client';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL, StateProbe } from '../../lib/state-probe';

// frontend/src/routes/paths.js; captured from the fresh signup UI journey.
const REGISTER = '/auth/jwt/register';
const DEVELOP = '/dashboard/develop';
const OBSERVE = '/dashboard/observe';
// accounts/urls.py and frontend/src/sections/auth/jwt/jwt-register-view.jsx.
const SIGNUP = '/accounts/signup/';
const ONBOARDING = '/accounts/onboarding/';
// accounts/user_onboard.py: the existing sample is one 35-span trace per project.
const PROJECT_NAMES = ['Demo SQL Agent (Experiment)', 'Demo SQL Agent (Observe)'];
const DATASET_NAMES = ['Demo-dataset', 'Image-Demo-dataset'];
const TRACE_NAME = 'SQL Agent Trace';
const ROOT_SPAN_NAME = 'SQL Agent Executor';
const SPAN_COUNT = 35;
// Captured from the running Observe trace grid and trace detail drawer.
const TRACE_CELLS = '.clean-data-table:visible .ag-row [col-id="trace_name"]';
const UI_READY = 60_000;

interface Project { id: string; name: string; trace_id: string; session_id: string; spans: number; sessions: number }

// Base test intentionally starts signed out: provisionActor would bypass the
// password signup dispatch this flow must exercise.
test('AUTH-E2E-004: fresh signup seeds demo traces visible in Observe', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'AUTH-E2E-004', area: 'auth',
    userGoal: 'A new user signs up with their own password and can inspect the seeded demo trace in Observe',
    steps: ['complete first-run checks if required', 'sign up with a unique email and a user-supplied password',
      'complete role and organization onboarding', 'see both demo datasets',
      'open the Observe demo project', 'open its single demo trace and inspect the span tree'],
    backendChecks: [
      'the fresh user organization has both demo datasets and both demo projects, each with one trace, one session and 35 linked spans in PostgreSQL',
      'ClickHouse has the same trace, session and span identities for both projects, without depending on legacy span CDC',
    ],
  }),
}, async ({ page, request }, testInfo) => {
  // Ten UI stages × 60 s + three Temporal publication polls × 60 s.
  test.setTimeout(780_000);
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const email = `e2e-demo-seed-${suffix}@futureagi.com`;
  const password = `E2e-demo-${suffix}!`;
  const probe = new StateProbe({ api: new ApiClient(request, E2E.apiUrl),
    chUrl: E2E.chUrl, chDatabase: E2E.chDatabase, chPassword: E2E.chPassword, pgUrl: E2E.pgUrl });
  await testInfo.attach('signup-identity', { contentType: 'application/json', body: JSON.stringify({ email }) });
  try {
    const fullName = page.getByLabel(/Full Name/i);
    const testFlight = page.getByText('Test flight', { exact: true });
    await test.step('open signup', async () => {
      await page.goto(REGISTER);
      await expect(fullName.or(testFlight)).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('complete first-run checks when needed', async () => {
      if (await testFlight.isVisible()) {
        await testFlight.click();
        await page.getByRole('button', { name: 'Continue', exact: true }).click();
        const next = page.getByRole('button', { name: 'Continue', exact: true });
        await expect(next).toBeEnabled({ timeout: UI_READY });
        await next.click();
      }
    }, { timeout: UI_READY });
    await test.step('reach the registration form', async () => {
      const signUp = page.getByRole('link', { name: 'Sign up', exact: true });
      await expect(fullName.or(signUp)).toBeVisible({ timeout: UI_READY });
      if (await signUp.isVisible()) await signUp.click();
      await expect(fullName).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });

    await test.step('submit a fresh password signup', async () => {
      await page.getByLabel(/Full Name/i).fill(`E2E Demo ${suffix}`);
      await page.getByLabel(/Email ID/i).fill(email);
      await page.getByLabel('Set Password', { exact: true }).fill(password);
      await page.getByLabel('Confirm Password', { exact: true }).fill(password);
      // Wait for hydration before selecting the role; it resets form defaults.
      const onboarding = page.waitForResponse(r => new URL(r.url()).pathname === ONBOARDING &&
        r.request().method() === 'GET', { timeout: UI_READY });
      const signup = page.waitForResponse(r => new URL(r.url()).pathname === SIGNUP &&
        r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('button', { name: 'Continue', exact: true }).click();
      expect((await signup).status()).toBe(200);
      expect((await onboarding).status()).toBe(200);
    }, { timeout: UI_READY });

    await test.step('complete role onboarding', async () => {
      await page.getByText('Backend / Platform Engineer / DevOps', { exact: true }).click();
      await page.getByRole('button', { name: 'Continue', exact: true }).click();
      await page.getByText('Skip for now', { exact: true }).click();
    }, { timeout: UI_READY });
    await test.step('complete organization setup', async () => {
      await page.getByLabel(/Organization Name/i).fill(`E2E Demo ${suffix}`);
      await page.getByRole('button', { name: 'Continue', exact: true }).click();
      await expect(page).toHaveURL(/\/dashboard\//, { timeout: UI_READY });
    }, { timeout: UI_READY });

    // Model table names and relationships: accounts/models/user.py,
    // model_hub/models/develop_dataset.py, tracer/models/{project,trace,trace_session,observation_span}.py.
    const users = await probe.pg<{ organization_id: string }>(
      'SELECT organization_id FROM accounts_user WHERE email = $1', [email]);
    expect(users).toHaveLength(1);
    const org = users[0].organization_id;
    await expect.poll(async () => (await probe.pg<{ name: string }>(
      'SELECT name FROM model_hub_dataset WHERE organization_id = $1 AND NOT deleted ORDER BY name',
      [org])).map(row => row.name), POLL.ASYNC_JOB).toEqual(DATASET_NAMES);
    let projects: Project[] = [];
    await expect.poll(async () => {
      projects = await probe.pg<Project>(`
        SELECT p.id, p.name, t.id AS trace_id, t.session_id,
          (SELECT count(*)::int FROM tracer_observation_span s WHERE s.trace_id = t.id) AS spans,
          (SELECT count(*)::int FROM trace_session ts WHERE ts.project_id = p.id) AS sessions
        FROM tracer_project p JOIN tracer_trace t ON t.project_id = p.id
        JOIN trace_session ts ON ts.id = t.session_id AND ts.project_id = p.id
        WHERE p.organization_id = $1 AND p.source = 'demo' AND NOT p.deleted ORDER BY p.name`, [org]);
      return projects.map(p => ({ name: p.name, spans: p.spans, sessions: p.sessions }));
    }, POLL.ASYNC_JOB).toEqual(PROJECT_NAMES.map(name => ({ name, spans: SPAN_COUNT, sessions: 1 })));
    await testInfo.attach('seeded-demo-ids', { contentType: 'application/json',
      body: JSON.stringify({ email, organizationId: org, projects }) });

    // ReplacingMergeTree tables must be read FINAL. Match every identity, not
    // just counts, so an unrelated trace cannot satisfy the publication check.
    const expected = await Promise.all(projects.map(async p => ({ project: p.id, trace: p.trace_id,
      session: p.session_id, spans: (await probe.pg<{ id: string }>(
        'SELECT id FROM tracer_observation_span WHERE trace_id = $1 ORDER BY id', [p.trace_id])).map(s => s.id) })));
    await expect.poll(async () => Promise.all(projects.map(async p => {
      const traces = await probe.ch<{ id: string; session_id: string }>(
        'SELECT id, session_id FROM traces FINAL WHERE project_id = {p:UUID}', { p: p.id });
      const sessions = await probe.ch<{ id: string }>(
        'SELECT trace_session_id AS id FROM trace_sessions FINAL WHERE project_id = {p:UUID}', { p: p.id });
      const spans = await probe.ch<{ id: string; trace_id: string; trace_session_id: string }>(
        'SELECT id, trace_id, trace_session_id FROM spans FINAL WHERE project_id = {p:UUID} ORDER BY id', { p: p.id });
      return { project: p.id, traces, sessions, spans };
    })), POLL.ASYNC_JOB).toEqual(expected.map(p => ({ project: p.project,
      traces: [{ id: p.trace, session_id: p.session }], sessions: [{ id: p.session }],
      spans: p.spans.map(id => ({ id, trace_id: p.trace, trace_session_id: p.session })) })));

    await test.step('see both demo datasets', async () => {
      await page.goto(DEVELOP);
      for (const name of DATASET_NAMES) await expect(page.getByText(name, { exact: true })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('open the Observe demo project', async () => {
      await page.goto(OBSERVE);
      await page.getByText(PROJECT_NAMES[1], { exact: true }).click({ timeout: UI_READY });
      const observe = projects.find(p => p.name === PROJECT_NAMES[1])!;
      await expect(page).toHaveURL(new RegExp(`/observe/${observe.id}/`), { timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('see exactly the seeded trace', async () => {
      await expect(page.locator(TRACE_CELLS)).toHaveText([TRACE_NAME], { timeout: UI_READY });
      await page.locator(TRACE_CELLS).click();
    }, { timeout: UI_READY });
    await test.step('inspect the demo span tree', async () => {
      await expect(page.getByText(ROOT_SPAN_NAME, { exact: true }).first()).toBeVisible({ timeout: UI_READY });
      await expect(page.getByText('RunnableSequence', { exact: true }).first()).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
  } finally {
    await probe.dispose();
  }
});
