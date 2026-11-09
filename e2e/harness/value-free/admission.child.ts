// HR-JRN-01 structural R2 (S3): the child test of value-free/admission.config.ts (never run on its own). A worker of a
// value-free run provisions only when its own runner admitted the publication. `first`: provisionActor reaches the stub
// request context, which answers 599, so the signup is the one call. `late`: nothing reaches it. No stack, no network.
import { randomBytes } from 'node:crypto';
import { test, expect, type APIRequestContext } from '@playwright/test';
import { ApiClient } from '../../lib/api-client';
import { provisionActor } from '../../lib/provisioning';

const mode = process.env.E2E_VALUE_FREE_ADMISSION_MODE;

/** A request context that sends nothing: it records each call and answers 599. */
function stubRequest(calls: string[]): APIRequestContext {
  const record = (method: string) => async (url: string) => {
    calls.push(`${method} ${new URL(url).pathname}`);
    return { status: () => 599, text: async () => '{}' };
  };
  return { get: record('get'), post: record('post'), patch: record('patch'), delete: record('delete') } as unknown as APIRequestContext;
}

test('admission: a worker provisions only when its own runner admitted the publication', async () => {
  expect(['first', 'late'], 'E2E_VALUE_FREE_ADMISSION_MODE').toContain(mode);
  const calls: string[] = [];
  const label = `adm${randomBytes(6).toString('hex')}`;
  if (mode === 'first') {
    await expect(provisionActor(stubRequest(calls), label), 'admitted: the signup reaches the stub').rejects.toThrow(/API 599/);
    expect(calls, 'the signup, and nothing after its 599').toEqual(['post /accounts/signup/']);
  } else {
    await expect(provisionActor(stubRequest(calls), label), 'provisionActor').rejects.toThrow(/not admitted/);
    await expect(new ApiClient(stubRequest(calls), 'http://127.0.0.1:9').get('/accounts/user-info/'), 'the API client')
      .rejects.toThrow(/not admitted/);
    expect(calls, 'nothing was sent').toEqual([]);
  }
});
