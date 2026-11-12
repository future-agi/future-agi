// J07 committed consumer config (ARTIFACT-POLICY.json source_contract): Local Studio
// embedded inside FA through omega-embed/1. Imports the canonical FA config and keeps
// its timeouts, assertion expectations and quarantine pattern; restricts discovery to
// the one J07 spec; workers=1, retries=0; and turns every capture producer off.
//
// Stage A (the shared walk runner) invokes it once per engine, exactly as
// DERIVATION.json stage_a_input_draft.invocations lists:
//   yarn playwright test flows/omega/studio-embed.spec.ts --config=playwright.j07.config.ts \
//     --browser=<chromium|firefox|webkit> --reporter=line --workers=1 --retries=0
// so this config defines NO projects (Playwright refuses --browser with projects);
// the engine's walk launch policy (lib/walk/browsers) is applied by the spec fixture.
// --reporter on the command line replaces the list below, so Stage A must name the J07
// value-free reporter (flows/omega/studio-embed.reporter.cjs) itself. Worker admission
// enforces a reporter ALLOWLIST before any login or token: 'line', exactly one J07
// reporter (by real path) and runner-accepted modules (by real path and sha256); a
// missing J07 reporter refuses REPORTER_J07_ABSENT, anything else REPORTER_NOT_ALLOWLISTED.
//
// Inputs, all from the runner: J07_BINDING (absolute path of the j07-binding/v1 file),
// optional J07_OUT (output root), J07_ASSERTIONS_OUT (create-only reporter file) and
// J07_INVOCATION_ID. Without a binding every case refuses "J07-BLOCKED BINDING_MISSING";
// without G5a qualification of the installed failure-artifact producer it refuses
// "J07-BLOCKED G5A_UNQUALIFIED" (studio-embed.binding.cjs documents both files).
//
// Worker admission re-checks all of this against the EFFECTIVE values (the fixture pool's
// resolved trace/screenshot/video and each test's merged context options), so a CLI or
// test.use override cannot turn capture on behind this file.
//
// Deliberately absent: trace, screenshot, video, recordHar, storageState, and the
// trace-hygiene globalTeardown, which moves refused files into a quarantine. ARTIFACT-
// POLICY forbids filtering, moving or excluding a captured original: an unsafe artifact
// is a STOP, and the J07-10 inventory reports it instead.
import { createRequire } from 'node:module';
import { defineConfig } from '@playwright/test';
import canonical from './playwright.config';
import type { J07Options } from './flows/omega/studio-embed.fixture';

// Refused before anything runs: debug/protocol logging can echo a token-bearing URL.
// DEBUG and NODE_DEBUG are read with the loggers' OWN semantics (the `debug` parser
// Playwright 1.62.1 bundles: comma AND whitespace separators, '-' skips, '*' wildcards;
// Node's debuglog: case-insensitive), the same function worker admission applies.
const { debugLoggingViolations } = createRequire(import.meta.url)('./flows/omega/studio-embed.binding.cjs') as {
  debugLoggingViolations(env: NodeJS.ProcessEnv): string[];
};
const env = process.env;
if (debugLoggingViolations(env).length > 0 || env.E2E_RECORD_VIDEO === '1' || env.PW_TEST_CONNECT_WS_ENDPOINT) {
  throw new Error('J07-BLOCKED CAPTURE_ENV');
}
// Playwright 1.62.1 (lib/index.js ArtifactsRecorder._takePageSnapshot) copies an ARIA
// snapshot of the page into error-context.md on failure unless this is set. Set here,
// in the runner process, so every worker inherits it; the fixture re-checks it before
// any token exists. error-context.md itself is still written on failure, which is why
// admission requires the G5a receipt for the exact installed producer bytes, and the
// J07 reporter inventories every output directory after the last case.
env.PLAYWRIGHT_NO_COPY_PROMPT = '1';

const out = env.J07_OUT ?? 'j07-results';
const assertionsOut = env.J07_ASSERTIONS_OUT;

export default defineConfig<J07Options>({
  testDir: '.',
  testMatch: ['flows/omega/studio-embed.spec.ts'],
  grepInvert: canonical.grepInvert,
  forbidOnly: true,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: canonical.timeout,
  expect: canonical.expect,
  outputDir: `${out}/artifacts`,
  reporter: [
    ['line'],
    ['./flows/omega/studio-embed.reporter.cjs', { schema: 'j07-value-free/v1', ...(assertionsOut ? { outputFile: assertionsOut } : {}) }],
  ],
  use: {
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    serviceWorkers: 'block',
    j07Walk: true,
  },
});
