// H1: the walk fixture. A walk spec imports `test` from here instead of
// lib/scope-actors.ts and gets, next to scopeActors and scopeProbe:
//   walk.open(actor, label)   a browser context signed in as `actor`, with the
//                              boundary (H12) installed before its first page,
//                              service workers blocked and the install-link
//                              interception (H11b) in every page
//   walk.anonymous(label)     the same with nobody signed in
//   walk.receipt              lib/walk/receipt.ts: verdicts, shots, empty icons
//   walk.boundary             lib/walk/boundary.ts, one per test
// At teardown the fixture writes walk-receipt.json (profile, mounted config
// digest from the managed-mock inspector, the boundary ledger, browser settings)
// and then fails the test on any non-local attempt that is not ASSETS-PENDING.
// Each Playwright project (chromium, firefox: playwright.walk.config.ts) runs
// its own tests, so every browser gets its own ScopeActors org set.
import type { BrowserContext } from '@playwright/test';
import { inspectManagedMock } from '../managed-mock';
import { walkGatewayProfile } from '../profile';
import { test as scopeTest, expect, type ScopeActor } from '../scope-actors';
import { WalkBoundary } from './boundary';
import { PROTOCOL_SCHEMES, protocolInterceptScript, walkBrowserSettings } from './browsers';
import { digest, WalkReceipt } from './receipt';
import { publishCensusResult, type CensusData } from './census-result';
import { currentCensusBinding } from './census-runtime';

export interface Walk {
  receipt: WalkReceipt;
  boundary: WalkBoundary;
  /** Queue W00 evidence; publication happens only after teardown/boundary pass. */
  publishCensus(data: CensusData): void;
  open(actor: ScopeActor, label: string): Promise<BrowserContext>;
  anonymous(label: string): Promise<BrowserContext>;
  /** The managed-mock inspection's profile and mounted-config digest, read once per test. */
  inspection(): { profile: string; mountedConfig: string | null; error?: string };
}

export const test = scopeTest.extend<{ walk: Walk; walkName: string }>({
  walkName: ['walk', { option: true }],
  walk: async ({ browser, browserName, launchOptions, scopeActors, walkName }, use, testInfo) => {
    const boundary = new WalkBoundary(`${walkName}-${testInfo.project.name}`);
    const receipt = new WalkReceipt(walkName, testInfo);
    const contexts: BrowserContext[] = [];
    const prepare = async (context: BrowserContext, label: string) => {
      await boundary.install(context, label);
      await context.addInitScript(protocolInterceptScript, [...PROTOCOL_SCHEMES]);
    };
    let inspected: ReturnType<Walk['inspection']> | undefined;
    let pendingCensus: CensusData | undefined;
    let writtenReceipt: unknown;
    const walk: Walk = {
      receipt, boundary,
      publishCensus(data) {
        if (walkName !== 'W00' || pendingCensus) throw new Error('W00_RESULT_DUPLICATE');
        pendingCensus = structuredClone(data);
      },
      async open(actor, label) {
        const context = await scopeActors.openContext(browser, actor, { options: { serviceWorkers: 'block' },
          guard: (ctx) => prepare(ctx, label) });
        contexts.push(context);
        return context;
      },
      async anonymous(label) {
        const context = await browser.newContext({ serviceWorkers: 'block' });
        try { await prepare(context, label); } catch (error) { await context.close(); throw error; }
        contexts.push(context);
        return context;
      },
      inspection() {
        if (!inspected) {
          try {
            const r = inspectManagedMock();
            receipt.extra.runningImages = r.services.map(({ service, image }) => ({ service, imageId: image }));
            inspected = { profile: r.profile, mountedConfig: digest(r.gatewaySha) };
          } catch (error) {
            inspected = { profile: walkGatewayProfile(), mountedConfig: null, error: (error as Error).message.slice(0, 300) };
          }
        }
        return inspected;
      },
    };
    try {
      await use(walk);
    } finally {
      for (const context of contexts) await context.close().catch(() => undefined);
      const { profile, mountedConfig, error } = walk.inspection();
      if (error) receipt.notes.push(`managed-mock inspection: ${error}`);
      writtenReceipt = await receipt.write({ profile, mountedConfig, boundary: boundary.receipt(),
        browser: walkBrowserSettings(browserName, launchOptions) });
    }
    // After the receipt: a late attempt in a closing context counts too.
    boundary.assert();
    if (pendingCensus && process.env.E2E_CENSUS_RESULTS && testInfo.status === 'passed' && testInfo.errors.length === 0) {
      if (browserName !== 'chromium' && browserName !== 'firefox') throw new Error('W00_RESULT_BROWSER');
      const result = publishCensusResult(currentCensusBinding(browserName), pendingCensus, writtenReceipt);
      await testInfo.attach('w00-result', { contentType: 'application/json', body: JSON.stringify(result) });
    }
  },
});

export { expect };
