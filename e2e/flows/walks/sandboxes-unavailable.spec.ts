import type { BrowserContext, Route } from '@playwright/test';
import { test, expect } from '../../lib/walk/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// On a deployment with no sandbox backend, EE
// answers GET /workspace/sandboxes 200 with no sandboxes and caps.createRefusal
// coded `sandboxes_unavailable` (and no provider), and every other sandbox route
// 409. The Sandboxes page shows that as its normal state, not as an error.
//
// A browser render check. The stack runs no Omega, so this context alone answers
// the Omega reads the page makes (the gateway check and the sandbox list, in EE's
// shape) and the one Future AGI answer that hands out a sign-in for Omega. Future
// AGI's own sign-in, workspace and capabilities answers stay real, except that
// this context turns the Omega switch on and admits the Sandboxes product.
const UI_READY = 60_000;
const SCHEMA = 'omega-workspace-sandboxes/v1';
const OFF_TITLE = 'Sandboxes are not available on this deployment.';
const OFF_NEEDS = 'An administrator enables a sandbox host for this deployment.';
// EE's caps on such an install: nothing on offer, network off only.
const OFF_LIST = {
  schemaVersion: SCHEMA,
  sandboxes: [],
  caps: {
    defaultImage: null, images: [], sizes: [], defaultSize: null, templates: [], defaultTemplate: null,
    customLimits: false,
    limits: { default: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 }, min: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 },
      max: { cpus: 0.1, memoryMb: 64, pidsLimit: 16 } },
    networkModes: ['none'], defaultNetwork: 'none', networkUnavailableReason: null, networkUnavailableCode: null,
    egressAllowList: { supported: false, reason: null },
    idleTtlSeconds: { default: 1800, min: 60, max: 604800 },
    exec: { defaultTimeoutSeconds: 60, maxTimeoutSeconds: 900, maxOutputBytes: 1000000 },
    files: { maxReadBytes: 8388608, maxWriteBytes: 8388608 },
    maxActiveSandboxes: 10,
    settings: { canEdit: false },
    createRefusal: { field: 'deployment', code: 'sandboxes_unavailable',
      reason: 'Sandboxes are not enabled on this deployment.' },
  },
};
// synthetic test value: a made-up one-use Omega sign-in, never sent anywhere but this page's mocked Omega answers
const SIGN_IN = { tokenType: 'Bearer', accessToken: 'omega_delegate_walk.synthetic.value', expiresIn: 45 };

const corsFor = (route: Route): Record<string, string> => {
  const origin = route.request().headers()['origin'];
  return origin ? { 'access-control-allow-origin': origin, 'access-control-allow-credentials': 'true', vary: 'Origin' } : {};
};

/** Registered after the walk boundary, so these win; anything else falls through to it. */
async function answerOmega(context: BrowserContext): Promise<{ listReads: { bearer: boolean }[] }> {
  const listReads: { bearer: boolean }[] = [];
  // The runtime config the page loads before the app (index.html: /config.js), with the Omega switch on.
  await context.route(/\/config\.js(\?.*)?$/, async (route) => {
    const response = await route.fetch();
    const body = `${await response.text()}\n;window.__FUTURE_AGI_CONFIG__ = Object.assign(window.__FUTURE_AGI_CONFIG__ || {}, { VITE_OMEGA_CONSOLE: "1" });\n`;
    await route.fulfill({ response, body });
  });
  // The real capabilities answer, with the Sandboxes product admitted (rollout.js productStatus).
  await context.route(/\/api\/capabilities\/?(\?.*)?$/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    const admit = (bag: { features?: Record<string, unknown> } | null | undefined) => {
      if (bag && typeof bag === 'object') bag.features = { ...(bag.features ?? {}), omega_sandboxes: { allowed: true } };
    };
    admit(body);
    admit(body?.result);
    await route.fulfill({ response, json: body });
  });
  // Future AGI's sign-in for Omega (sessionAssertion.js SESSION_EXCHANGE_PATH).
  await context.route(/\/accounts\/omega\/session-exchange\/?(\?.*)?$/, async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: { ...corsFor(route), 'access-control-allow-methods': 'POST, OPTIONS',
        'access-control-allow-headers': request.headers()['access-control-request-headers'] ?? '*' } });
      return;
    }
    await route.fulfill({ status: 200, headers: corsFor(route), json: SIGN_IN });
  });
  await context.route(/\/omega\/api\/readyz(\?.*)?$/, (route) => route.fulfill({ status: 200, json: { status: 'ok' } }));
  await context.route(/\/omega\/api\/workspace\/sandboxes\/?(\?.*)?$/, async (route) => {
    const request = route.request();
    if (request.method() !== 'GET') return route.fallback();
    listReads.push({ bearer: (request.headers()['authorization'] ?? '').startsWith('Bearer omega_delegate_') });
    await route.fulfill({ status: 200, json: OFF_LIST });
  });
  return { listReads };
}

test.use({ walkName: 'SBX-CUT5' });

test('WALK-E2E-020: Sandboxes on a deployment with no sandboxes set up render a calm unavailable state, not an error', {
  tag: ['@flow', '@walk', '@walk-SBX'],
  annotation: flowAnnotation({
    id: 'WALK-E2E-020', area: 'walks',
    userGoal: 'An owner opens Sandboxes on a deployment with no sandbox backend and reads, in plain words, that sandboxes are not available there, with nothing offered that cannot work',
    steps: [
      'Owner A opens /dashboard/sandboxes while Omega answers the sandbox list 200 with no sandboxes and a create refusal coded sandboxes_unavailable',
      'read the Sandboxes page',
    ],
    browserChecks: [
      'the unavailable state shows its title and what the deployment needs, with no button inside it',
      'no New sandbox button, no create-refusal warning, no error state, no empty-list state and no alert on the page',
      'the list read carried a one-use Omega sign-in',
    ],
  }),
}, async ({ scopeActors: scopes, walk }) => {
  const context = await walk.open(scopes.ownerA, 'ownerA');
  const { listReads } = await answerOmega(context);
  const page = await context.newPage();
  await page.goto('/dashboard/sandboxes');
  const tab = page.getByTestId('omega-tab-sandboxes');
  const state = tab.getByTestId('sandboxes-unavailable');
  await expect(state).toBeVisible({ timeout: UI_READY });
  await expect(state).toContainText(OFF_TITLE);
  await expect(state).toContainText(OFF_NEEDS);
  await expect(state.getByRole('button')).toHaveCount(0);
  for (const testId of ['new-sandbox', 'sandboxes-create-refusal', 'sandbox-error-state', 'sandboxes-empty']) {
    await expect(tab.getByTestId(testId), testId).toHaveCount(0);
  }
  await expect(tab.getByRole('alert')).toHaveCount(0);
  expect(listReads.length).toBeGreaterThan(0);
  expect(listReads.every((read) => read.bearer)).toBe(true);
});
