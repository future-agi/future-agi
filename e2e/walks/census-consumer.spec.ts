import { test, expect } from '@playwright/test';
import { readCensusResult } from '../lib/walk/census-result';
import { currentCensusBinding } from '../lib/walk/census-runtime';

// No ScopeActors and no replay. Dedicated composition smoke; ordinary harness
// runs must not select this integration consumer without its W00 dependency.
test('W00 result consumer accepts the current attested tuple', { tag: '@census-consumer' }, async ({ browserName }, info) => {
  expect(['w00-chromium', 'w00-firefox']).not.toContain(info.project.name);
  if (browserName !== 'chromium' && browserName !== 'firefox') throw new Error('W00_RESULT_BROWSER');
  const result = readCensusResult(currentCensusBinding(browserName));
  expect(result.ui).toHaveLength(224);
  expect(result.backend).toHaveLength(2);
  await info.attach('w00-consumed', { contentType: 'application/json',
    body: JSON.stringify({ digest: result.digest, orgSet: result.orgSet, binding: result.binding }) });
});
