// 708 add.78: the trace-hygiene leak classes without the stack (trace-hygiene-flow.ts):
// a traced request context against a loopback stand-in, then the independent
// auditor over the trace it produced. Runs with the offline harness self-tests.
import { test } from '../lib/scope-actors';
import { expectCleanTrace, loopbackCredentialCalls } from './trace-hygiene-flow';

test.use({ trace: 'on' });
test.describe.configure({ mode: 'serial' });
let tracePath = '';

test('offline: a traced request context sends an invitation password, a bearer and API keys',
  async ({ playwright }, testInfo) => {
    tracePath = testInfo.outputPath('trace.zip');
    const api = await playwright.request.newContext();
    try {
      await loopbackCredentialCalls(api);
    } finally {
      await api.dispose();
    }
  });

test('offline: the trace it produced carries zero detector findings', async () => {
  expectCleanTrace(tracePath, 2);
});
