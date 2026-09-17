import { test as base, expect, request as pwRequest } from '@playwright/test';
import { E2E } from './env';
import { provisionActor, TestActor } from './provisioning';
import { authInitScript } from './auth';
import { StateProbe } from './state-probe';

type WorkerFixtures = { actor: TestActor };
type TestFixtures = { probe: StateProbe };

export const test = base.extend<TestFixtures, WorkerFixtures>({
  actor: [async ({}, use, workerInfo) => {
    const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
    const actor = await provisionActor(req, `w${workerInfo.workerIndex}`);
    await use(actor);
    await req.dispose();
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
