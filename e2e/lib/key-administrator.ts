import { request } from '@playwright/test';
import { test as base, expect } from './fixtures';
import { E2E } from './env';
import { provisionActor } from './provisioning';

// Separate fixture: regular flow actors must never silently acquire admin rights.
export const test = base.extend({
  actor: [async ({}, use, workerInfo) => {
    const req = await request.newContext({ baseURL: E2E.apiUrl });
    try {
      const actor = await provisionActor(req, `key-admin-w${workerInfo.workerIndex}`,
        { keyAdministrator: true });
      await use(actor);
    } finally {
      await req.dispose();
    }
  }, { scope: 'worker' }],
});

export { expect };
