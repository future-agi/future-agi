import { test as base, expect, request } from '@playwright/test';
import { E2E } from './env';
import { provisionActor, type TestActor } from './provisioning';
import { authInitScript } from './auth';
import { StateProbe } from './state-probe';
import { inspectManagedMock, registerMockModel } from './managed-mock';

// Opt-in test-scoped actor: runtime custom model lookup is organization+name,
// not workspace. Do not change the ordinary worker-scoped fixture or share gpt-4o.
export const test = base.extend<{
  actor: TestActor; probe: StateProbe;
  evalBackground: boolean;
  managedMock: () => Promise<void>; mockModel: Awaited<ReturnType<typeof registerMockModel>>;
}>({
  evalBackground: [false, { option: true }],
  managedMock: async ({ evalBackground }, use, testInfo) => {
    const initial = inspectManagedMock({ evalBackground });
    await testInfo.attach('managed-mock-routing', { contentType: 'application/json', body: JSON.stringify(initial) });
    await use(async () => {
      expect(inspectManagedMock({ evalBackground }), 'STOP: managed stack changed during the test').toEqual(initial);
    });
  },
  actor: async ({ managedMock }, use, testInfo) => {
    await managedMock(); // Including background capability BEFORE public identity provisioning.
    const req = await request.newContext({ baseURL: E2E.apiUrl });
    try {
      const actor = await provisionActor(req, `mock-${testInfo.workerIndex}-${Date.now().toString(36)}`);
      await testInfo.attach('isolated-mock-actor', { contentType: 'application/json', body: JSON.stringify({
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
    const probe = new StateProbe({ api: actor.api, pgUrl: E2E.pgUrl, chUrl: E2E.chUrl, chDatabase: E2E.chDatabase });
    try { await use(probe); } finally { await probe.dispose(); }
  },
  mockModel: async ({ actor, probe, managedMock }, use, testInfo) => {
    const model = await registerMockModel(actor, probe, managedMock);
    await testInfo.attach('isolated-mock-model', { contentType: 'application/json', body: JSON.stringify({
      id: model.id, model: model.model, organizationId: actor.organizationId, workspaceId: actor.workspaceId }) });
    await use(model);
  },
});
export { expect };
