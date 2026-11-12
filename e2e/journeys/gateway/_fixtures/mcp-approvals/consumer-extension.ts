import type { TestType } from '@playwright/test';
import { attachSeededIds } from '../../_shared/contract';
import { startMcpOAuthFixture, type LocalMcpOAuth } from './server.ts';
import { startNativeCallback } from './native-callback.ts';

interface NamedFixtures {
  mcpFixturePrincipalIds: readonly string[];
  nativeMcpCallback: Awaited<ReturnType<typeof startNativeCallback>>;
  localMcpOAuth: LocalMcpOAuth;
}

/** Consumer fixture module, not an orchestrator/dispatcher.
 * E6 convention: CONSUMER-ABI-REQUEST.json interfaces_E0_E6.E6 names
 * fixtures.cjs:withNamedFixtures(acceptedTest). Verified request SHA256:
 * b78a171fea942f88d1900ae4e3388e40a11593397ca733441abe4759daf6e803.
 *
 * This export extends the ACCEPTED test without changing context/page/browser,
 * guard preloads, process environment or runner config. The consumer must supply
 * fresh F09 principal IDs through the option (never an actor/key object). Native
 * receiver and MCP/OAuth target run in-process on ephemeral 127.0.0.1 listeners.
 * No endpoint defaults are copied from host environments.
 *
 * Runtime binding is OWED: CONSUMER-ABI.json was absent at authoring. The runner
 * must actually consume this E6 export, discover these specs, bind guarded worker
 * loopback to same-job product callers without relaxing SSRF/egress, and execute
 * native positives/reds. This module does not manufacture an accepted ABI marker
 * or invent additional descriptor fields. Module import starts nothing.
 */
export function withNamedFixtures<T extends {}, W extends {}>(acceptedTest: TestType<T, W>) {
  return acceptedTest.extend<NamedFixtures>({
    mcpFixturePrincipalIds: [[], { option: true }],
    nativeMcpCallback: async ({}, use) => {
      const native = await startNativeCallback();
      try { await use(native); } finally { await native.close(); }
    },
    localMcpOAuth: async ({ mcpFixturePrincipalIds, nativeMcpCallback }, use, info) => {
      // Fails before opening a target listener if unbound/empty/duplicate IDs.
      await attachSeededIds(info, 'J14', { userIds: mcpFixturePrincipalIds });
      const fixture = await startMcpOAuthFixture({ principalIds: mcpFixturePrincipalIds,
        redirectUris: [nativeMcpCallback.redirectUri] });
      try { await use(fixture); }
      finally {
        // Stop accepting calls BEFORE taking the final counter census.
        try { await fixture.close(); }
        finally {
          await info.attach('j14-local-target-counters', { contentType: 'application/json',
            body: JSON.stringify({ schema: 'j14-local-target-counters/v1',
              syntheticFixture: true, productAccepted: false, counters: fixture.counters() }) });
        }
      }
    },
  });
}
