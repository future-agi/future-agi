import type { Page, Route } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// The isolated E2E stack has no Enterprise license (fix_my_agent is
// requires_license) and no simulation runner, so it cannot hold a finished fix.
// Stub only the fix's read, its make-default write and its patch, as SIM-E2E-007
// does for the launch; the fix page, Ship's confirmation, the trial picker, the
// reason, the request it sends and what the page shows after are the product's
// own. The ship rules and the refusals behind this are covered by
// futureagi/simulate/tests (require_shippable, overridable_regressions).

// Browser-side waits: one navigation's first paint, not a stage. The local
// stack slows several-fold beside other workers, so not the 10s expect default.
const UI_READY = 60_000;

// The prebuilt template env-voice-support (frontend/src/api/simulate-environments/
// _fixtures/prebuiltEnvironments.js), which the workspace opens without a build.
const ENV_ID = 'env-voice-support';
const BASE_VERSION_ID = 'agv-8';
const SHIPPER = 'E2E Developer';
// The must-pass check Trial 2 broke, as overridable_regressions words it
// ("scenario key: check", futureagi/simulate/selectors/agent_fix.py).
const REGRESSION = 'on-time-cancel: escalated';

type Json = Record<string, unknown>;

const tiers = (mustPass: 'passed' | 'failed') => [
  { name: 'static', status: 'passed', detail: '1 file changed, 6 lines' },
  { name: 'target', status: 'passed', detail: '1 of 1 target scenarios passed' },
  { name: 'held-back', status: 'passed', detail: '2 of 2 held-back scenarios passed' },
  { name: 'must-pass', status: mustPass, detail: mustPass === 'passed' ? '9 of 9 must-pass scenarios held' : '8 of 9 must-pass scenarios held' },
  { name: 'repeats', status: 'passed', detail: '3 repeats agree' },
];

// One trial in AgentFixCandidateSerializer's read shape (futureagi/simulate/
// serializers/agent_fix.py), as candidate() in the frontend's fixRuns fixture.
function trial(fixId: string, attempt: number, outcome: string, extra: Json = {}): Json {
  return {
    id: `${fixId}-c${attempt}`,
    attempt,
    agent_version_id: `agv-${8 + attempt}`,
    version_number: 8 + attempt,
    summary: attempt === 1 ? 'Escalate refused late cancellations to the front desk.' : 'Say when lookup_order applies before answering.',
    outcome,
    verdict: {
      accepted: outcome === 'winner',
      reason: outcome === 'winner' ? 'proven on targets at the acceptance bar; must-pass held' : 'target: 0 of 1 target scenarios passed',
      target_pass_rate: outcome === 'winner' ? [0.33, 1.0] : [0.0, 0.0],
      held_back_pass_rate: outcome === 'winner' ? [0.17, 0.83] : null,
      must_pass_regressions: [],
      must_pass_within_noise: [],
      tiers: outcome === 'winner' ? tiers('passed') : tiers('passed').slice(0, 2).map((t) => (t.name === 'target' ? { ...t, status: 'failed' } : t)),
    },
    check_runs: [],
    pull_request_url: '',
    override: { allowed: false, regressions: [], reason: '', by: null, at: null },
    created_at: `2026-11-14T08:1${attempt}:05Z`,
    ...extra,
  };
}

// GET /simulate/api/agent-fix-runs/<id>/: a finished fix in AgentFixRunSerializer's
// read shape (the fix() fixture): a winner (Trial 3), a trial only the must-pass
// check refused (Trial 2), and a trial its targets refused (Trial 1).
function finishedFix(fixId: string): Json {
  return {
    id: fixId,
    number: 9,
    environment_id: ENV_ID,
    environment: { name: 'Customer Support Line', version: 1, default_agent_version_id: BASE_VERSION_ID },
    source_run_id: 'e2e-sim9-source-run',
    source_run_number: 1,
    base_agent_version_id: BASE_VERSION_ID,
    agent: { name: 'Customer Support Line agent', kind: 'archive', version_number: 8, ref: null },
    state: 'ready',
    step: 'done',
    stage: 'Ship',
    request: {
      target_scenario_keys: ['late-cancel'],
      protected_scenario_keys: ['refund-window'],
      held_back_scenario_keys: ['late-cancel-2', 'late-cancel-3'],
      failing_sub_goals: ['escalated'],
      surfaces: ['prompt'],
      locked_paths: [],
      budget_credits: 1500,
      max_minutes: 150,
    },
    spent_credits: 412,
    committed_credits: 520,
    plan: { target: 6, held_back: 2, must_pass: 9, protected: 1, max_candidates: 4, repeats: 3, baseline_spread_pts: 17 },
    diagnosis: { causes: ['The instructions never ask the agent to escalate cancellations'] },
    report: { ending: 'ready', summary: 'Found a fix.' },
    failure: null,
    retried_from_id: null,
    retryable: false,
    limited_validation: false,
    eval_warnings: [],
    check_runs: [],
    candidates: [
      trial(fixId, 1, 'rejected'),
      trial(fixId, 2, 'rejected', {
        verdict: {
          accepted: false,
          reason: 'must-pass: 1 must-pass scenario regressed',
          target_pass_rate: [0.0, 1.0],
          held_back_pass_rate: [0.17, 0.83],
          must_pass_regressions: ['on-time-cancel'],
          must_pass_within_noise: [],
          tiers: tiers('failed'),
        },
        override: { allowed: true, regressions: [REGRESSION], reason: '', by: null, at: null },
      }),
      trial(fixId, 3, 'winner'),
    ],
    started_at: '2026-11-14T08:00:00Z',
    finished_at: '2026-11-14T09:00:00Z',
    created_at: '2026-11-14T08:00:00Z',
  };
}

const PATCH = `diff --git a/instructions.md b/instructions.md
--- a/instructions.md
+++ b/instructions.md
@@ -1,2 +1,2 @@
-Escalate refunds above $200.
+Escalate refunds above $200 to the front desk.
`;

const endsWith = (suffix: string) => (url: URL) => url.pathname.endsWith(suffix);

async function stubFixApi(page: Page, fixId: string) {
  const fix = finishedFix(fixId);
  const seen = { shipPosts: [] as { path: string; body: Json }[] };
  // The real capabilities, with only fix_my_agent switched on.
  await page.route(endsWith('/api/capabilities/'), async (route: Route) => {
    const response = await route.fetch();
    const json = await response.json();
    json.features = { ...json.features, fix_my_agent: { ...json.features?.fix_my_agent, allowed: true } };
    await route.fulfill({ response, json });
  });
  await page.route(endsWith(`/simulate/api/agent-fix-runs/${fixId}/`), (route) => route.fulfill({ json: fix }));
  await page.route(/\/simulate\/api\/agent-fix-runs\/[^/]+\/candidates\/[^/]+\/patch\/$/, (route) =>
    route.fulfill({ contentType: 'text/plain', body: PATCH }),
  );
  // make-default as the backend does it: the override is recorded once, with who
  // and when, and the trial's version becomes the environment's default.
  await page.route(/\/simulate\/api\/agent-fix-runs\/[^/]+\/candidates\/([^/]+)\/make-default\/$/, (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON() as Json;
    seen.shipPosts.push({ path, body });
    const candidateId = path.split('/candidates/')[1].split('/')[0];
    const candidates = fix.candidates as Json[];
    const shipped = candidates.find((c) => c.id === candidateId) as Json;
    if (body.override_reason) {
      shipped.override = {
        ...(shipped.override as Json),
        reason: body.override_reason,
        by: { id: 'e2e-shipper', name: SHIPPER },
        at: new Date().toISOString(),
      };
    }
    (fix.environment as Json).default_agent_version_id = shipped.agent_version_id;
    return route.fulfill({ json: { outcome: 'activated' } });
  });
  return seen;
}

test('SIM-E2E-009: a trial only its must-pass check refused ships on the developer\'s reason', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-009', area: 'simulate',
    userGoal: 'A developer ships a fix\'s trial that broke only must-pass checks, giving a reason, and sees which trial is now the default and who shipped it, when and why',
    steps: ['open a finished fix with a winner and a trial only must-pass refused', 'read that trial\'s row',
            'press Ship', 'pick that trial in the Trial dropdown', 'read the checks it broke', 'type the reason',
            'press Make default', 'read the header and the trial\'s row'],
    browserChecks: [
      'Trial 2\'s row reads "Broke must-pass: on-time-cancel: escalated"',
      'Ship opens on the winner; picking Trial 2 shows "Must-pass on-time-cancel: escalated" and keeps Make default disabled until a reason is typed',
      'exactly one POST goes to Trial 2\'s make-default, with exactly the minted reason as override_reason',
      'the header reads "Trial 2 is the default"',
      'Trial 2\'s row reads "Shipped anyway by E2E Developer" with the minted reason',
    ],
  }),
}, async ({ page, actor }, testInfo) => {
  // 6 browser steps, each bounded by its own UI_READY: 6 × 60s = 360s, plus 60s
  // headroom so a slow run fails on the step that ran out.
  test.setTimeout(420_000);
  const fixId = `e2e-sim9-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const reason = `${fixId}: the new escalation wording is intended`;
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, envId: ENV_ID, fixId, reason }),
    contentType: 'application/json',
  });
  const seen = await stubFixApi(page, fixId);
  const row = page.getByTestId('trial-2');

  await test.step('ui: check 1, the trial lists the must-pass check it broke', async () => {
    await page.goto(`/dashboard/simulate/environments/${ENV_ID}/improvements/${fixId}`);
    await expect(row.getByText(`Broke must-pass: ${REGRESSION}`, { exact: true })).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  const dialog = page.getByRole('dialog').filter({ has: page.getByRole('button', { name: 'Make default' }) });
  await test.step('ui: Ship opens on the winner', async () => {
    await page.getByRole('button', { name: 'Ship', exact: true }).click();
    await expect(dialog.getByRole('combobox', { name: 'Trial to ship' })).toHaveText('Trial 3 · Winner', { timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 2, picking Trial 2 needs a reason', async () => {
    await dialog.getByRole('combobox', { name: 'Trial to ship' }).click();
    await page.getByRole('option', { name: 'Trial 2 · Broke must-pass checks', exact: true }).click();
    await expect(dialog.getByText(`Must-pass ${REGRESSION}`, { exact: true })).toBeVisible({ timeout: UI_READY });
    await expect(dialog.getByRole('button', { name: 'Make default' })).toBeDisabled({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 3, Make default sends the reason for Trial 2', async () => {
    await dialog.getByRole('textbox', { name: 'Why ship it anyway?' }).fill(reason);
    await dialog.getByRole('button', { name: 'Make default' }).click();
    await expect.poll(() => seen.shipPosts.length, { timeout: UI_READY }).toBe(1);
    expect(seen.shipPosts[0]).toEqual({
      path: `/simulate/api/agent-fix-runs/${fixId}/candidates/${fixId}-c2/make-default/`,
      body: { override_reason: reason },
    });
  }, { timeout: UI_READY });

  await test.step('ui: check 4, the header names the trial that is now the default', async () => {
    await expect(page.getByRole('button', { name: 'Trial 2 is the default', exact: true })).toBeDisabled({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: check 5, the row says who shipped it and why', async () => {
    await expect(row.getByText(new RegExp(`^Shipped anyway by ${SHIPPER} · .+: ${reason}$`))).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });
});
