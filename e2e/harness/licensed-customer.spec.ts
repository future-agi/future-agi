import { test, expect, LICENSED_EMAIL_PREFIX } from '../lib/licensed-customer';
import { test as ordinaryTest, expect as ordinaryExpect } from '../lib/fresh-actor';

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// src/hooks/useCapabilities.js reads this; CapabilityGate decides from it.
const CAPABILITIES = '/api/capabilities/';

// e2e/stack/backend/settings_e2e.py E2E_LICENSED_FEATURES.
const LICENSED = ['instant_eval', 'insights', 'turing_models'];

interface Capabilities { features: Record<string, { allowed: boolean; reason_code: string | null }> }

// The second test below is the other half: an ordinary org on the same stack
// is still refused, so the unlock is limited to the licensed org. OBS-E2E-053,
// OBS-E2E-058 and EVAL-E2E-011 pin the same refusal through the UI.
test('the licensed-customer actor may use Instant eval, Insights and the Turing models Insights runs on, and the signed-in page reads that', async ({ page, actor }, testInfo) => {
  await testInfo.attach('licensed-customer', { contentType: 'application/json',
    body: JSON.stringify({ email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
  expect(actor.email.startsWith(LICENSED_EMAIL_PREFIX)).toBe(true);

  const direct = await actor.api.get<Capabilities>(CAPABILITIES);
  for (const feature of LICENSED) {
    expect(direct.features[feature]).toMatchObject({ allowed: true, reason_code: null });
  }

  // The read the app makes at boot, with the page's own org header.
  const booted = page.waitForResponse(r => new URL(r.url()).pathname === CAPABILITIES
    && r.request().method() === 'GET' && r.status() === 200, { timeout: UI_READY });
  await page.goto('/dashboard/observe');
  const read = await (await booted).json() as Capabilities;
  for (const feature of LICENSED) {
    expect(read.features[feature]).toMatchObject({ allowed: true, reason_code: null });
  }
});

ordinaryTest('an ordinary org on the same stack is still refused Instant eval, Insights and Turing models', async ({ actor }, testInfo) => {
  await testInfo.attach('ordinary-actor', { contentType: 'application/json',
    body: JSON.stringify({ email: actor.email, organizationId: actor.organizationId }) });
  ordinaryExpect(actor.email.startsWith(LICENSED_EMAIL_PREFIX)).toBe(false);
  const read = await actor.api.get<Capabilities>(CAPABILITIES);
  for (const feature of LICENSED) {
    ordinaryExpect(read.features[feature]).toMatchObject({ allowed: false, reason_code: 'LICENSE_MISSING' });
  }
});
