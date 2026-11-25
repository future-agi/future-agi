import { test as base, expect, request } from '@playwright/test';
import { E2E } from './env';
import { provisionActor, type TestActor } from './provisioning';
import { authInitScript } from './auth';
import { StateProbe } from './state-probe';

// Opt-in test-scoped actor: a brand-new ordinary (unlicensed) org for one test.
// For a flow that leaves state other flows on the worker-scoped actor must not
// see, such as a saved TypeSafe key (EVAL-E2E-011 and EVAL-E2E-013 require none).
// Same shape as lib/mock-model-fixtures.ts, without the mock-model setup.
export const test = base.extend<{ actor: TestActor; probe: StateProbe }>({
  actor: async ({}, use, testInfo) => {
    const req = await request.newContext({ baseURL: E2E.apiUrl });
    try {
      const actor = await provisionActor(req, `fresh-${testInfo.workerIndex}-${Date.now().toString(36)}`);
      await testInfo.attach('fresh-actor', { contentType: 'application/json', body: JSON.stringify({
        email: actor.email, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
      await use(actor);
    } finally { await req.dispose(); }
  },
  context: async ({ context, actor }, use) => {
    await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh,
      organizationId: actor.organizationId, workspaceId: actor.workspaceId });
    await use(context);
  },
  probe: async ({ actor }, use) => {
    const probe = new StateProbe({ api: actor.api, pgUrl: E2E.pgUrl, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase,
      chPassword: E2E.chPassword, catalogChUrl: E2E.catalogChUrl, catalogChDatabase: E2E.catalogChDatabase });
    try { await use(probe); } finally { await probe.dispose(); }
  },
});

export { expect };
