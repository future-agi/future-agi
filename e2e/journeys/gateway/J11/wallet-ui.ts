import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { GatewayGridReader } from '../../../lib/gateway-grid';
import { attachSeededIds } from '../_shared/contract';
import { requireCohort } from '../_shared/identifiers';

// Source: frontend/src/routes/paths.js:dashboard.settings.billing.
export const BILLING_PAGE = '/dashboard/settings/billing';
// Source: sections/settings/BillingV2/gateway-credits/{GatewayCreditsSection,
// CreditLedger,CreditStatement,AutoReloadForm,TopUpDialog}.jsx. These named
// section regions and accessible labels already exist; no testid is invented.
export const walletSelectors = {
  credits: (page: Page) => page.getByRole('region', { name: 'AI Gateway credits', exact: true }),
  ledger: (page: Page) => page.getByRole('region', { name: 'Credit ledger', exact: true }),
  statement: (page: Page) => page.getByRole('region', { name: 'Monthly statement', exact: true }),
  lowBalance: (page: Page) => page.getByRole('spinbutton', {
    name: 'Alert when balance falls below (USD)', exact: true }),
  addCredits: (page: Page) => page.getByRole('button', { name: 'Add credits', exact: true }),
  topupDialog: (page: Page) => page.getByRole('dialog', { name: 'Add credits', exact: true }),
  // ONLY present when canBuy=true. The actual self-hosted UI hides purchase and
  // auto-reload, so W02-01's button path cannot be manufactured with a locator.
  autoReload: (page: Page) => page.getByRole('checkbox', { name: 'Auto-reload', exact: true }),
};
const LEDGER_FIRST_PAINT = { timeout: 60_000 };

/** W02-04 partial UI step, for the packet's one grant + three-debit fixture.
 * Caller passes its already-authenticated, admitted Page; no identity factory,
 * mock API, navigation wrapper or runner fixture is introduced here.
 * The 390px hidden-column path, money tooltips/CSV and authoritative ledger
 * checks remain owed. Never annotate this as a complete journey-assertion.
 */
export async function assertDesktopLedgerRequests(
  page: Page, info: TestInfo, mintedRequestIds: readonly string[],
): Promise<void> {
  requireCohort(mintedRequestIds);
  if (mintedRequestIds.length !== 3 || (page.viewportSize()?.width ?? 0) < 1440) {
    throw new Error('J11: this partial step requires three minted requests and the desktop invocation');
  }
  info.annotations.push({ type: 'journey-partial', description: JSON.stringify({
    packet: 'J11', row: 'W02-04', coverage: 'request-identity-only', status: 'NOT_WALKED',
  }) });
  await attachSeededIds(info, 'J11', { requestIds: mintedRequestIds });
  await test.step('J11/W02-04/partial desktop ledger exact request inventory', async () => {
    const ledger = walletSelectors.ledger(page);
    await expect(ledger).toBeVisible(LEDGER_FIRST_PAINT);
    const grid = GatewayGridReader.in(ledger);
    await expect(grid.root).toBeVisible(LEDGER_FIRST_PAINT);
    await expect.poll(() => grid.displayedRowCount(), LEDGER_FIRST_PAINT).toBe(4);
    // CreditLedger uses field=request_id, preserves a blank grant cell, and
    // renders Load more when the backend declares another cursor. Preserve the
    // blank and multiplicity; do not turn the three IDs into a contains check.
    await expect.poll(async () => (await grid.column('request_id')).sort(), LEDGER_FIRST_PAINT)
      .toEqual(['', ...mintedRequestIds].sort());
    await expect(ledger.getByRole('button', { name: 'Load more', exact: true })).toHaveCount(0);
  });
}
