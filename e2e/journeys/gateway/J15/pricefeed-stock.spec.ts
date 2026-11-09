import { test, expect } from '@playwright/test';
import { GatewayAdmin } from '../../../lib/gateway-admin';
import { E2E } from '../../../lib/env';
import { journeyTitle } from '../_shared/contract';
import { fragmentAnnotation, fragmentStep, notWalked } from './fragments';
import { PRICE_FEED_PATH, assertStockPublication, readPublicationState } from './pricefeed-state';

// Base Playwright intentionally avoids lib/fixtures' org-key-minting actor.
// No actor, key, tenant or private state is needed for this anonymous stock arm.
// Not a full W05.A01 test: the paired enabled profile/restart witness is O04.
// Sole future executor: accepted GW-WALK-RUNNER, not bin/e2e/another runtime.
// F01 must admit this exact fragment separately; it cannot cover a full row.
const ANONYMOUS_FEED_NAVIGATION = 60_000;

test(journeyTitle('J15', 'W05-STOCK-ABSENCE-FRAGMENT', 'baseline'), {
  tag: ['@journey-J15', '@journey-fragment', '@gw-profile-stock'],
  annotation: fragmentAnnotation('W05.A01', 'anonymous-stock-profile-only'),
}, async ({ page }, testInfo) => {
  test.setTimeout(ANONYMOUS_FEED_NAVIGATION + 30_000);
  // No managed-stack defaults or profile inference for an admitted invocation.
  // These are existing E2E env names, not a new fixture or runner interface.
  if (process.env.E2E_GATEWAY_PROFILE !== 'stock' ||
      process.env.E2E_STACK !== 'standalone' || !process.env.E2E_GATEWAY_URL) {
    await notWalked(testInfo, 'W05.A01', 'J15-O04');
  }
  const base = new URL(E2E.gatewayUrl);
  if (!['http:', 'https:'].includes(base.protocol) ||
      !['localhost', '127.0.0.1', '[::1]'].includes(base.hostname) ||
      base.username || base.password || base.pathname !== '/' || base.search || base.hash) {
    await notWalked(testInfo, 'W05.A01', 'J15-O01');
  }
  const admin = await GatewayAdmin.open();
  try {
    await fragmentStep(testInfo, 'W05.A01', 'anonymous-stock-profile-only', async () => {
      const before = await readPublicationState(admin);
      const response = await page.goto(new URL(PRICE_FEED_PATH, base).href, {
        waitUntil: 'domcontentloaded', timeout: ANONYMOUS_FEED_NAVIGATION,
      });
      expect(response !== null, 'W05.A01 anonymous navigation has response').toBe(true);
      // No text, headers, response body, trace or unmasked screenshot is attached.
      expect(new URL(response!.url()).href === new URL(PRICE_FEED_PATH, base).href,
        'W05.A01 stock route did not redirect').toBe(true);
      assertStockPublication(before, response!.status());
      assertStockPublication(await readPublicationState(admin), response!.status());
    });
  } finally {
    await admin.dispose();
  }
});
