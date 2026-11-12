import type { Page } from '@playwright/test';
import { gatewaySelectors } from '../selectors';
import { definitionValue, fixtureText, recordCell, recordRow, type LocatorScope } from './scope';

// All product citations are at LOCATOR_SOURCE_REF (scope.ts), relative to
// frontend/src/sections/. No private payment-adapter contract is inferred here.
const credits = (scope: LocatorScope) => scope.getByRole('region', { name: 'AI Gateway credits', exact: true });
const ledger = (scope: LocatorScope) => credits(scope).getByRole('region', { name: 'Credit ledger', exact: true });
const statement = (scope: LocatorScope) => credits(scope).getByRole('region', { name: 'Monthly statement', exact: true });
// AutoReloadForm.jsx always shows the auto-reload switch, so the section has
// this name whether or not credit can be bought; purchaseAvailable no longer
// changes it.
const settings = (scope: LocatorScope, _purchaseAvailable: boolean) => credits(scope).getByRole('region', {
  name: 'Low balance and auto-reload', exact: true,
});
const topUpDialog = (page: Page) => page.getByRole('dialog', { name: 'Add credits', exact: true });

export type LedgerColumn = 'occurred_at' | 'kind' | 'amount_usd' | 'model' | 'provider' | 'request_id' | 'markup_usd';

export const walletLocators = {
  // gateway/settings/BillingCreditsPanel.jsx:39-60; panel is role/availability gated.
  gatewayPanel: gatewaySelectors.billingPanel,
  openBilling: (scope: LocatorScope) => gatewaySelectors.billingPanel(scope)
    .getByRole('link', { name: 'Open Billing & credits', exact: true }),
  gatewayAvailable: (scope: LocatorScope) => definitionValue(gatewaySelectors.billingPanel(scope), 'Credit available'),
  // settings/BillingV2/gateway-credits/GatewayCreditsSection.jsx:84-125;
  // CreditLedger.jsx:136-144; CreditStatement.jsx:58-66; AutoReloadForm.jsx:77-86.
  credits, ledger, statement, settings,
  addCredits: (scope: LocatorScope) => credits(scope).getByRole('button', { name: 'Add credits', exact: true }),
  // GatewayCreditsSection.jsx:34-54,171-217. Figure's immediate Box contains
  // the label, value and optional caption. This is a display root, NOT a ledger.
  figure: (scope: LocatorScope, label: 'Available' | 'Credited' | 'Spent' | 'Spent, settling' | 'Minimum top-up') =>
    credits(scope).getByText(label, { exact: true }).locator('..'),
  // AutoReloadForm.jsx. The section name is the same in every build; the
  // purchaseAvailable argument is accepted and does not change it.
  lowBalance: (scope: LocatorScope, purchaseAvailable: boolean) => settings(scope, purchaseAvailable)
    .getByRole('spinbutton', { name: 'Alert when balance falls below (USD)', exact: true }),
  autoReload: (scope: LocatorScope) => settings(scope, true).getByRole('checkbox', { name: 'Auto-reload', exact: true }),
  reloadThreshold: (scope: LocatorScope) => settings(scope, true)
    .getByRole('spinbutton', { name: 'Reload when balance falls below (USD)', exact: true }),
  reloadAmount: (scope: LocatorScope) => settings(scope, true)
    .getByRole('spinbutton', { name: 'Reload amount (USD)', exact: true }),
  saveSettings: (scope: LocatorScope, purchaseAvailable: boolean) => settings(scope, purchaseAvailable)
    .getByRole('button', { name: 'Save', exact: true }),
  settingsErrors: (scope: LocatorScope, purchaseAvailable: boolean) => settings(scope, purchaseAvailable).getByRole('alert'),
  // TopUpDialog.jsx:56-92. Dialog is portalled; do NOT scope it to credits.
  topUpDialog,
  topUpAmount: (page: Page) => topUpDialog(page).getByRole('spinbutton', { name: 'Amount (USD)', exact: true }),
  continuePayment: (page: Page) => topUpDialog(page).getByRole('button', { name: 'Continue to payment', exact: true }),
  cancelTopUp: (page: Page) => topUpDialog(page).getByRole('button', { name: 'Cancel', exact: true }),
  // CreditLedger.jsx:COLUMNS:52-104 and getRowId:176 use entry.id, NOT request_id.
  ledgerRow: (scope: LocatorScope, entryId: string) => recordRow(ledger(scope), entryId),
  ledgerCell: (scope: LocatorScope, entryId: string, column: LedgerColumn) => recordCell(ledger(scope), entryId, column),
  ledgerKind: (scope: LocatorScope) => ledger(scope).getByLabel('Kind', { exact: true }),
  ledgerExport: (scope: LocatorScope) => ledger(scope).getByRole('button', { name: 'Export CSV', exact: true }),
  ledgerLoadMore: (scope: LocatorScope) => ledger(scope).getByRole('button', { name: 'Load more', exact: true }),
  // CreditStatement.jsx:69-87,113-162. The model line includes '(estimated)'
  // when set; the consumer supplies that exact rendered label, no fuzzy match.
  statementMonth: (scope: LocatorScope) => statement(scope).getByLabel('Month', { exact: true }),
  statementExport: (scope: LocatorScope) => statement(scope).getByRole('button', { name: 'Export CSV', exact: true }),
  statementRow: (page: Page, exactLine: string) => statement(page).getByRole('row').filter({
    has: page.getByRole('cell', { name: fixtureText(exactLine), exact: true }),
  }),
  // useCheckoutReturn.js emits notistack messages; GatewayCreditsSection.jsx also
  // shows one role=status notice while a checkout return is pending (it never says
  // whether the payment was taken). Match literal text; never take either as money proof.
  confirmingNotice: (scope: LocatorScope) => credits(scope).getByRole('status')
    .filter({ hasText: 'Confirming your payment. Your balance updates here once the top-up is credited.' }),
  pendingNotice: (page: Page) => page.getByText('Your payment is still being processed. We will refresh the balance once more shortly. If it is still pending, refresh Billing later.', { exact: true }),
  creditedNotice: (page: Page) => page.getByText('Credits added to your AI Gateway balance.', { exact: true }),
  cancelledNotice: (page: Page) => page.getByText('Top-up cancelled. No payment was taken.', { exact: true }),
};

// PaymentMethods.jsx:149-165,207-211,244-287, plus
// develop-detail/Common/CustomDialog/CustomDialog.jsx:92-146 (dialog title).
// Card rows expose brand/last4/expiry, but React key={pm.id} is NOT DOM identity.
// No cardById/Set-default row selector is invented: F18-P01 requests a product
// hook for the non-secret payment-method ID. Duplicate masked cards must remain
// ambiguous until that lands. These locators cover header/dialog/display only.
const addCardDialog = (page: Page) => page.getByRole('dialog', { name: 'Add Payment Method', exact: true });
export const cardLocators = {
  openAdd: (page: Page) => page.getByText('Payment methods', { exact: true }).locator('..')
    .getByRole('button', { name: 'Add Card', exact: true }),
  addDialog: addCardDialog,
  confirmAdd: (page: Page) => addCardDialog(page).getByRole('button', { name: 'Add Card', exact: true }),
  cancelAdd: (page: Page) => addCardDialog(page).getByRole('button', { name: 'Cancel', exact: true }),
  maskedLabels: (page: Page, brand: string, last4: string) => {
    if (!/^\d{4}$/.test(last4)) throw new Error('Expected four synthetic card suffix digits');
    return page.getByText(`${fixtureText(brand)} •••• ${last4}`, { exact: true });
  },
  cancelledNotice: (page: Page) => page.getByText('Card setup cancelled', { exact: true }),
};

// No auto-reload consent/revoke checkbox exists in AutoReloadForm at this pin.
// Card success toast is deliberately NOT exported: PaymentMethods.jsx:124-127
// calls finalize from finally even on confirmation failure. Require main F12's
// authoritative TEST payment projection; this module never contacts checkout.
