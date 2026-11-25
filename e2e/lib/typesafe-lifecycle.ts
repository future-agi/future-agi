import type { TestInfo } from '@playwright/test';
import { TypeSafeLifecycle } from './typesafe-live';

// Shared by the live spec and the real-Playwright, browserless regressions.
// Register as a test-scoped fixture with TYPE_SAFE_FIXTURE_TIMEOUT, and depend
// on any actors/browser it uses so they remain alive through finalization.
export async function typeSafeLifecycleFixture(
  use: (lifecycle: TypeSafeLifecycle) => Promise<void>, testInfo: TestInfo,
  lifecycle = new TypeSafeLifecycle(),
): Promise<void> {
  try { await use(lifecycle); }
  finally { await lifecycle.finish(testInfo.status !== 'passed' || testInfo.errors.length > 0); }
}
