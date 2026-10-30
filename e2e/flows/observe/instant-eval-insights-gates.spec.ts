import type { Locator, Page, TestInfo } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { ApiError } from '../../lib/api-client';
import type { TestActor } from '../../lib/provisioning';
import type { StateProbe } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

// The isolated E2E stack runs the in-tree EE code with no licence. Instant eval
// and Insights are oss_locked (futureagi/tfc/capabilities/registry.py), so the
// capability service denies both with LICENSE_MISSING
// (tfc/capabilities/service.py _check_self_hosted_ee), the same answer the stack
// gives turing_models in EVAL-E2E-006. Nothing is stubbed: the capabilities read,
// the gate, the menu and the 402 are the stack's own.

// tracer/urls.py; ProjectSerializer requires model_type (ALERT-E2E-002 seeds the same body).
const PROJECTS = '/tracer/project/';
// src/hooks/useCapabilities.js reads this; CapabilityGate decides from it.
const CAPABILITIES = '/api/capabilities/';
// The Ask page's composer posts here first (useInstantEval.js estimateAsk);
// InsightsView reads the report list here on mount (insightsApi.js fetchConfigs).
const INSTANT_EVAL_ESTIMATE = '/tracer/instant-eval/estimate/';
const INSIGHTS_CONFIGS = '/tracer/insights/configs/';
// Every Instant eval and Insights endpoint lives under one of these
// (tracer/urls.py); only the gated pages call them (AskPage, InsightsView).
const INSTANT_EVAL_API = '/tracer/instant-eval/';
const INSIGHTS_API = '/tracer/insights/';
const DENIED = 'LICENSE_MISSING';
// InstantEval/EvaluateMenu.jsx action titles, in menu order on the Traces view.
// An item's text runs on into its mark and description, so match the title.
const ACTIONS = [/Ask a question/, /Find patterns/, /Add evaluators/];
const LOCKED_ACTIONS = [/Ask a question/, /Find patterns/];
// capability-gate/EnterpriseMark.jsx: the lock and this word mark a denied action.
const ENTERPRISE = 'Enterprise';
// oss-upgrade-gate/constants.js. LICENSE_MISSING has no REASONS entry, so the
// gate offers the upgrade link rather than "Manage license" or "Talk to us".
const UPGRADE_CTA = 'Upgrade to EE license key';
const CONTACT_URL = 'https://futureagi.com/talk-to-human';
const INSTANT_EVAL_TITLE = 'Upgrade to use Instant eval';
const INSTANT_EVAL_FOOTNOTE = 'Jev evaluators come with every edition, open source included: add one from Evals with your own TypeSafe key.';
const INSIGHTS_TITLE = 'Upgrade to use Insights';
// README's browser first-paint ceiling, not the default 10-second expect budget.
const UI_READY = 60_000;
const ui = expect.configure({ timeout: UI_READY });

interface Capabilities { features: Record<string, { allowed: boolean; reason_code: string | null }> }
interface CreatedProject { result: { project_id: string; name: string } }

/** An Observe project of the actor's own, created the way the UI's API does. */
async function seedProject(actor: TestActor, probe: StateProbe, testInfo: TestInfo,
  label: string): Promise<string> {
  const name = `e2e-${label}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const created = await actor.api.post<CreatedProject>(PROJECTS,
    { name, trace_type: 'observe', model_type: 'GenerativeLLM' });
  const projectId = created.result.project_id;
  await testInfo.attach('project', { contentType: 'application/json',
    body: JSON.stringify({ projectId, name, organizationId: actor.organizationId }) });
  const rows = await probe.pg<{ id: string }>(
    'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2',
    [name, actor.organizationId]);
  expect(rows).toEqual([{ id: projectId }]);
  return projectId;
}

/** The method and path of every browser request under `prefix`. */
function recordRequests(page: Page, prefix: string): string[] {
  const seen: string[] = [];
  page.on('request', request => {
    const { pathname } = new URL(request.url());
    if (pathname.startsWith(prefix)) seen.push(`${request.method()} ${pathname}`);
  });
  return seen;
}

/** Opens the Traces view's Evaluate menu and checks which actions are marked.
 * Returns the menu's items. */
async function openEvaluateMenu(page: Page, projectId: string): Promise<Locator> {
  await page.goto(`/dashboard/observe/${projectId}/llm-tracing?tab=traces&selectedTab=trace`,
    { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Evaluate', exact: true }).click();
  const items = page.getByRole('menu').getByRole('menuitem');
  await ui(items).toHaveText(ACTIONS);
  // Exactly the two Enterprise actions carry the mark; Add evaluators does not.
  await ui(items.filter({ has: page.getByText(ENTERPRISE, { exact: true }) })).toHaveText(LOCKED_ACTIONS);
  return items;
}

/** The upgrade gate CapabilityGate shows in place of the page. */
async function expectUpgradeGate(page: Page, title: string): Promise<void> {
  await ui(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
  await ui(page.getByRole('link', { name: UPGRADE_CTA, exact: true })).toHaveAttribute('href', CONTACT_URL);
}

/** The shared 402 a licence-gated view answers (accounts/authentication.py
 * custom_exception_handler for FeatureUnavailable). */
async function expectRefused(call: Promise<unknown>, feature: string, testInfo: TestInfo,
  name: string): Promise<void> {
  const refused = await call.then(() => null, (error: unknown) => error);
  await testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(refused) });
  expect(refused).toBeInstanceOf(ApiError);
  expect(refused).toMatchObject({ status: 402, body: {
    code: DENIED, type: 'entitlement_error', upgrade_required: true,
    error: { code: DENIED, detail: { feature } },
  } });
}

test('OBS-E2E-053: without a licence, Ask a question opens the Instant eval upgrade page and its API answers 402', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-053', area: 'observe',
    userGoal: 'Try Instant eval on a self-hosted install with no Enterprise licence and be shown its upgrade page instead of the feature (nothing stubbed)',
    steps: ['create an Observe project through the API', 'open the project\'s Traces view and its Evaluate menu',
      'choose Ask a question', 'open the Ask page from a bookmarked link',
      'post an Instant eval estimate for the project through the API'],
    backendChecks: [
      'the project row exists in PG tracer_project under the actor org',
      'the capabilities API denies instant_eval with LICENSE_MISSING',
      'the estimate API answers 402 with upgrade_required, code LICENSE_MISSING and feature instant_eval',
    ],
    browserChecks: [
      'the Evaluate menu lists Ask a question, Find patterns, Add evaluators, and only the first two carry the Enterprise mark',
      'Ask a question lands on /observe/:id/ask showing the Instant eval upgrade page, its upgrade link and the Jev footnote',
      'the bookmarked /ask link shows the same upgrade page',
      'the browser never requests an Instant eval endpoint',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000); // Three UI_READY browser stages (menu, hand-off, bookmark) 180 + API/PG and headroom 60.
  page.setDefaultTimeout(UI_READY);
  const featureRequests = recordRequests(page, INSTANT_EVAL_API);
  const projectId = await test.step('seed: an Observe project in the actor org', () =>
    seedProject(actor, probe, testInfo, 'ask-gate'));

  await test.step('backend check: the stack denies Instant eval for a missing licence', async () => {
    const caps = await actor.api.get<Capabilities>(CAPABILITIES);
    await testInfo.attach('capabilities', { contentType: 'application/json',
      body: JSON.stringify({ instant_eval: caps.features.instant_eval }) });
    expect(caps.features.instant_eval).toMatchObject({ allowed: false, reason_code: DENIED });
  });

  const items = await test.step('UI: the Evaluate menu marks Ask a question and Find patterns, not Add evaluators', () =>
    openEvaluateMenu(page, projectId));

  await test.step('UI: Ask a question opens the Instant eval upgrade page', async () => {
    await items.filter({ hasText: LOCKED_ACTIONS[0] }).click();
    await ui(page).toHaveURL(new RegExp(`/dashboard/observe/${projectId}/ask(\\?.*)?$`));
    await expectUpgradeGate(page, INSTANT_EVAL_TITLE);
    await ui(page.getByText(INSTANT_EVAL_FOOTNOTE, { exact: true })).toBeVisible();
    await testInfo.attach('ask-gate', { body: await page.screenshot(), contentType: 'image/png' });
  });

  await test.step('UI: a bookmarked Ask link shows the same upgrade page', async () => {
    await page.goto(`/dashboard/observe/${projectId}/ask`, { waitUntil: 'domcontentloaded' });
    await expectUpgradeGate(page, INSTANT_EVAL_TITLE);
  });

  await test.step('backend check: the estimate API refuses with the licence 402', async () => {
    // estimateAsk's body for a Traces view with no filters.
    await expectRefused(actor.api.post(INSTANT_EVAL_ESTIMATE,
      { project_id: projectId, unit: 'traces', filters: [], sample_size: null, questions: 1 }),
    'instant_eval', testInfo, 'estimate-refusal');
  });

  await test.step('browser check: the page never called Instant eval', async () => {
    expect(featureRequests).toEqual([]);
  });
});

test('OBS-E2E-054: without a licence, Find patterns opens the Insights upgrade page and its API answers 402', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-054', area: 'observe',
    userGoal: 'Try Insights on a self-hosted install with no Enterprise licence and be shown its upgrade page instead of the feature (nothing stubbed)',
    steps: ['create an Observe project through the API', 'open the project\'s Traces view and its Evaluate menu',
      'choose Find patterns', 'open the Insights page from a bookmarked link',
      'list the project\'s Insights reports through the API'],
    backendChecks: [
      'the project row exists in PG tracer_project under the actor org',
      'the capabilities API denies insights with LICENSE_MISSING',
      'the report list API answers 402 with upgrade_required, code LICENSE_MISSING and feature insights',
    ],
    browserChecks: [
      'the Evaluate menu lists Ask a question, Find patterns, Add evaluators, and only the first two carry the Enterprise mark',
      'Find patterns lands on /observe/:id/insights showing the Insights upgrade page and its upgrade link',
      'the bookmarked /insights link shows the same upgrade page',
      'the browser never requests an Insights endpoint',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(240_000); // Three UI_READY browser stages (menu, hand-off, bookmark) 180 + API/PG and headroom 60.
  page.setDefaultTimeout(UI_READY);
  const featureRequests = recordRequests(page, INSIGHTS_API);
  const projectId = await test.step('seed: an Observe project in the actor org', () =>
    seedProject(actor, probe, testInfo, 'insights-gate'));

  await test.step('backend check: the stack denies Insights for a missing licence', async () => {
    const caps = await actor.api.get<Capabilities>(CAPABILITIES);
    await testInfo.attach('capabilities', { contentType: 'application/json',
      body: JSON.stringify({ insights: caps.features.insights }) });
    expect(caps.features.insights).toMatchObject({ allowed: false, reason_code: DENIED });
  });

  const items = await test.step('UI: the Evaluate menu marks Ask a question and Find patterns, not Add evaluators', () =>
    openEvaluateMenu(page, projectId));

  await test.step('UI: Find patterns opens the Insights upgrade page', async () => {
    await items.filter({ hasText: LOCKED_ACTIONS[1] }).click();
    await ui(page).toHaveURL(new RegExp(`/dashboard/observe/${projectId}/insights(\\?.*)?$`));
    await expectUpgradeGate(page, INSIGHTS_TITLE);
    await testInfo.attach('insights-gate', { body: await page.screenshot(), contentType: 'image/png' });
  });

  await test.step('UI: a bookmarked Insights link shows the same upgrade page', async () => {
    await page.goto(`/dashboard/observe/${projectId}/insights`, { waitUntil: 'domcontentloaded' });
    await expectUpgradeGate(page, INSIGHTS_TITLE);
  });

  await test.step('backend check: the report list API refuses with the licence 402', async () => {
    await expectRefused(actor.api.get(INSIGHTS_CONFIGS, { project_id: projectId }),
      'insights', testInfo, 'configs-refusal');
  });

  await test.step('browser check: the page never called Insights', async () => {
    expect(featureRequests).toEqual([]);
  });
});
