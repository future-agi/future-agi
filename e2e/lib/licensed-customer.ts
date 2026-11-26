import { request as pwRequest } from '@playwright/test';
import { test as base, expect } from './fixtures';
import { E2E } from './env';
import { provisionActor } from './provisioning';

// The licensed customer: an org whose member signed up as
// e2e-licensed-…@futureagi.com. The test image allows Instant eval and
// Insights for such an org only (E2E_LICENSED_EMAIL_PREFIX in
// e2e/stack/backend/settings_e2e.py); every other org keeps the stack's
// unlicensed answer, which OBS-E2E-053 and OBS-E2E-058 pin.
export const LICENSED_EMAIL_PREFIX = 'e2e-licensed-';

export const test = base.extend({
  // Overrides only the worker-scoped actor. context, page and probe take
  // `actor` by name, so they follow it. A different worker-fixture set gets
  // its own workers, so this actor never serves an ordinary spec.
  actor: [async ({}, use, workerInfo) => {
    const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
    const actor = await provisionActor(req, `licensed-w${workerInfo.workerIndex}`);
    if (!actor.email.startsWith(LICENSED_EMAIL_PREFIX)) {
      throw new Error(`licensed customer: ${actor.email} lacks ${LICENSED_EMAIL_PREFIX}`);
    }
    await use(actor);
    await req.dispose();
  }, { scope: 'worker' }],
});

export { expect };
