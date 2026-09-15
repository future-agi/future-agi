import { test as base, expect, request as pwRequest } from '@playwright/test';
import { E2E } from './env';
import { provisionActor, TestActor } from './provisioning';
import { authInitScript } from './auth';
import { StateProbe } from './state-probe';
import { DeploymentMode, fetchDeploymentMode } from './deployment';
import { fetchCapabilities } from './capabilities';

type WorkerFixtures = {
  actor: TestActor;
  deploymentMode: DeploymentMode;
  capabilities: Record<string, boolean>;
};
type TestFixtures = { probe: StateProbe };

export const test = base.extend<TestFixtures, WorkerFixtures>({
  actor: [async ({}, use, workerInfo) => {
    const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
    const actor = await provisionActor(req, `w${workerInfo.workerIndex}`);
    await use(actor);
    await req.dispose();
  }, { scope: 'worker' }],

  // One /api/deployment-info/ call per worker (not per test) — it's static
  // config for the whole stack, not per-org, so it can't change mid-worker.
  deploymentMode: [async ({ actor }, use, workerInfo) => {
    const mode = await fetchDeploymentMode(actor.api);
    // Printed once per worker, before any spec reads it. When a
    // deployment-gated flow does not skip the way it was expected to, this is
    // the ground truth for what the suite actually resolved — as opposed to
    // what `GET /api/deployment-info/` returns when curled by hand against a
    // stack that has since been rebuilt with a different EE_LICENSE_KEY.
    // eslint-disable-next-line no-console
    console.log(`[deployment] worker=${workerInfo.workerIndex} mode="${mode}" api=${E2E.apiUrl}`);
    await use(mode);
  }, { scope: 'worker' }],

  // Entitlements, worker-scoped for the same reason as deploymentMode: static
  // per stack, not per org. Prefer this over deploymentMode for anything
  // license-shaped — mode only says a key is set, not what it grants.
  capabilities: [async ({ actor }, use, workerInfo) => {
    const caps = await fetchCapabilities(actor.api);
    const denied = Object.entries(caps).filter(([, ok]) => !ok).map(([id]) => id);
    // eslint-disable-next-line no-console
    console.log(`[capabilities] worker=${workerInfo.workerIndex} denied=[${denied.join(', ')}]`);
    await use(caps);
  }, { scope: 'worker' }],

  context: async ({ context, actor }, use) => {
    await context.addInitScript(authInitScript, {
      access: actor.tokens.access,
      refresh: actor.tokens.refresh,
      organizationId: actor.organizationId,
      workspaceId: actor.workspaceId,
    });
    await use(context);
  },

  probe: async ({ actor }, use) => {
    const probe = new StateProbe({ api: actor.api, chUrl: E2E.chUrl,
      chDatabase: E2E.chDatabase, pgUrl: E2E.pgUrl });
    await use(probe);
    await probe.dispose();
  },
});

export { expect };
