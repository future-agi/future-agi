// The base `test` every shared fixture builds on (lib/fixtures.ts,
// lib/scope-actors.ts and through it lib/walk/fixtures.ts). Its one auto
// fixture installs the capture-time trace hook (hook.ts) on every test, so any
// flow or walk that keeps a trace (retain-on-failure, or trace: 'on') writes a
// scrubbed, independently audited trace.zip. Specs that import `test` straight
// from @playwright/test are still covered at the end of the run by
// global-teardown.ts (sweep + audit), without the minted-value registry.
import { test as base, expect, request } from '@playwright/test';
import { installTraceHygiene } from './hook';

export const test = base.extend<{ _traceHygiene: boolean }>({
  _traceHygiene: [async ({}, use, testInfo) => {
    const installed = installTraceHygiene(testInfo);
    if (!installed) testInfo.annotations.push({ type: 'trace-hygiene',
      description: 'capture-time hook unavailable on this Playwright; teardown sweep and audit only' });
    await use(installed);
  }, { auto: true, box: true }],
});

export { expect, request };
