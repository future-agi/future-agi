// BillingV2 public projection and UI equality, product 651ab5bb8a.
// Display formatting is not money proof; exact micros are checked separately.
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect } from '@playwright/test';
import type { ScopeActor, ScopeActors } from '../scope-actors';
import type { StateProbe } from '../state-probe';
import type { GatewayAdmin } from '../gateway-admin';
import { GatewayGridReader } from '../gateway-grid';
import type { Walk } from './fixtures';
import { accountRow, API, checked, entryRows, usdToMicros } from './money';
import {
  csvAs,
  entriesFromApiShape,
  entryIdentities,
  ENTRY_CSV_COLUMNS,
  parseCsv,
  statementFrom,
  statementFromApiShape,
  STATEMENT_CSV_COLUMNS,
  usd6,
  type ApiEntry,
  type Statement,
} from './billing';
import {
  BILLING_PAGE,
  formatBalance,
  formatCostCell,
  formatExactCost,
  horizontalOverflow,
  PURCHASE_UNAVAILABLE,
  settle,
  ui,
  UI_READY,
  walletUi,
} from './money-ui';
// CreditLedger.jsx KIND_LABEL and CreditStatement.jsx CREDIT_LABELS: every
// kind in words. A kind neither names reads as words too (asWords), never
// as its key, as the screens render it.
const KIND_LABELS: Record<string, string> = {
  spend: 'Spend',
  usage_debit: 'Platform usage',
  topup: 'Top-up',
  topup_reversal: 'Top-up refund',
  grant: 'Grant',
  refund: 'Refund',
  adjustment: 'Adjustment',
  expiry: 'Expiry',
  dispute_hold: 'Dispute hold',
  dispute_release: 'Dispute release',
};
const CREDIT_LABELS: Record<string, string> = {
  topup: 'Top-ups',
  grant: 'Grants',
  refund: 'Refunds',
  adjustment: 'Adjustments',
  expiry: 'Expiries',
  topup_reversal: 'Top-up refunds',
  dispute_hold: 'Dispute holds',
  dispute_release: 'Dispute releases',
};
const asWords = (key: string): string => {
  const text = String(key ?? '').replace(/_/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : 'Other';
};
export async function readWallet(
  scopes: ScopeActors,
  actor: ScopeActor,
  role: string,
  walk: Walk,
  probe: StateProbe,
  gw: GatewayAdmin,
  org: string,
  month: string,
): Promise<any> {
  const { receipt } = walk;
  const summary = (await actor.api.get<{ result: any }>(API.credits)).result;
  const entries = entriesFromApiShape(
    (await actor.api.get<{ result: { entries: ApiEntry[]; next_cursor: string | null } }>(API.entries, { limit: 200 }))
      .result,
    { orgId: org, entries: await entryIdentities(probe, org) },
  );
  const statement = (await actor.api.get<{ result: Statement }>(API.statement, { month })).result;
  await checked(receipt, { feature: `W02-N12-${role}`, lane: 'api', where: 'entries, statement and CSV' }, async () => {
    expect(statementFromApiShape(statement)).toEqual(statementFrom(await entryRows(probe, org), month));
    const entriesCsv = await csvAs(scopes, actor, API.entries, 'format=csv');
    expect(entriesCsv.status).toBe(200);
    expect(entriesCsv.rows[0]).toEqual([...ENTRY_CSV_COLUMNS]);
    // Normalize only timestamps; all money remains exact decimal text.
    expect(entriesCsv.rows.slice(1).map((row) => [new Date(row[0]).toISOString(), ...row.slice(1)])).toEqual(
      entries.map((e) => [
        new Date(e.occurred_at).toISOString(),
        e.kind,
        usd6(usdToMicros(e.amount_usd)),
        e.request_id,
        e.model,
        e.provider,
        e.markup_usd === null ? '' : usd6(usdToMicros(e.markup_usd)),
        e.markup_bps === null ? '' : String(e.markup_bps),
        e.cost_source,
        e.stripe_ref,
      ]),
    );
    const statementCsv = await csvAs(scopes, actor, API.statement, `month=${month}&format=csv`);
    expect(statementCsv.status).toBe(200);
    expect(statementCsv.rows[0]).toEqual([...STATEMENT_CSV_COLUMNS]);
    expect(statementCsv.rows.filter((row) => row[0] === 'closing_balance').map((row) => row[3])).toEqual([
      usd6(usdToMicros(summary.available_usd)),
    ]);
    return { entries: entries.length, statement: statement.month };
  });
  await checked(
    receipt,
    { feature: `W02-N18-${role}`, lane: 'gateway', where: 'GET /-/orgs/{org}/credits' },
    async () => {
      const c = await gw.credits(org);
      const account = (await accountRow(probe, org))!;
      expect([
        usdToMicros(summary.gateway.available_usd),
        usdToMicros(summary.gateway.held_usd),
        usdToMicros(summary.gateway.spent_usd),
        usdToMicros(summary.pending_settlement_usd),
      ]).toEqual([String(c.available_micros), '0', '625', String(625n - BigInt(String(account.settled_spend_micros)))]);
      expect(summary.markup_percent).toBe(5);
      expect(summary.billing_available).toBe(false);
      return 625;
    },
  );
  const context = await walk.open(actor, `${role}-wallet`);
  const page = await context.newPage();
  try {
    await ui('Wallet viewport', () => page.setViewportSize({ width: 1440, height: 900 }));
    await ui('Open Billing', () => page.goto(BILLING_PAGE, { waitUntil: 'domcontentloaded' }));
    await checked(receipt, { feature: `W02-N11-${role}`, lane: 'ui', where: BILLING_PAGE }, async () => {
      await expect(page.getByRole('heading', { name: 'Billing', exact: true, level: 1 })).toBeVisible({
        timeout: UI_READY,
      });
      await expect(page.getByText('Future AGI credits for the AI Gateway.', { exact: true })).toBeVisible();
      for (const name of ['Current period', 'Invoice history', 'Payment methods'])
        await expect(page.getByRole('heading', { name, exact: true })).toHaveCount(0);
      await expect(walletUi.credits(page)).toBeVisible();
      await expect(
        walletUi.figure(page, 'Available').getByText(formatBalance(summary.available_usd), { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText(
          `${formatBalance(summary.gateway.available_usd)} available now, after ${formatExactCost(summary.gateway.held_usd)} held by requests in flight`,
          { exact: true },
        ),
      ).toBeVisible();
      // Spent is what was charged; the card states no markup or provider cost.
      await expect(walletUi.credits(page)).not.toContainText(/markup|provider cost|5%/i);
      await expect(
        walletUi
          .figure(page, 'Spent, settling')
          .getByText(formatExactCost(summary.pending_settlement_usd), { exact: true }),
      ).toBeVisible();
      await expect(page.getByRole('alert').filter({ hasText: PURCHASE_UNAVAILABLE })).toHaveCount(1);
      await expect(walletUi.addCredits(page)).toHaveCount(0);
      const grid = GatewayGridReader.in(walletUi.ledger(page));
      await expect.poll(() => grid.displayedRowCount(), { timeout: UI_READY }).toBe(entries.length);
      expect((await grid.rows.evaluateAll((rows) => rows.map((row) => row.getAttribute('row-id')))).sort()).toEqual(
        entries.map((e) => e.id).sort(),
      );
      for (const entry of entries) {
        const row = grid.root.locator(`.ag-center-cols-container .ag-row[row-id=${JSON.stringify(entry.id)}]`);
        const columns = ['kind', 'amount_usd', 'model', 'provider', 'request_id'];
        const cells: string[] = [];
        for (const col of columns)
          cells.push((await row.locator(`.ag-cell[col-id=${JSON.stringify(col)}]`).innerText()).trim());
        expect(cells).toEqual([
          KIND_LABELS[entry.kind] ?? asWords(entry.kind),
          formatCostCell(entry.amount_usd),
          entry.model,
          entry.provider ? 'Future AGI' : '',
          entry.request_id,
        ]);
      }
      // Line, Requests, Amount: the amount charged, with no markup column.
      const expectedStatement = [
        ['Opening balance', '', formatBalance(statement.opening_balance)],
        ...Object.entries(statement.credits_by_kind)
          .filter(([, amount]) => usdToMicros(amount) !== '0')
          .map(([kind, amount]) => [CREDIT_LABELS[kind] ?? asWords(kind), '', formatExactCost(amount)]),
        ...statement.spend_by_model.map((line) => [
          `${line.model || 'Unknown model'}${line.estimated ? ' (estimated)' : ''}`,
          String(line.count),
          formatExactCost(-line.spend_usd),
        ]),
        ['Closing balance', '', formatBalance(statement.closing_balance)],
      ];
      await expect
        .poll(
          async () =>
            Promise.all(
              (await walletUi.statement(page).locator('tbody tr').all()).map((row) =>
                row.locator('td').allTextContents(),
              ),
            ),
          { timeout: UI_READY },
        )
        .toEqual(expectedStatement);
      await expect(walletUi.ledgerExport(page)).toHaveCount(1);
      await expect(walletUi.statementExport(page)).toHaveCount(1);
      return entries.length;
    });
    for (const [kind, button, expectedName, endpoint, query] of [
      ['entries', walletUi.ledgerExport(page), 'ai-gateway-credit-ledger.csv', API.entries, 'format=csv'],
      [
        'statement',
        walletUi.statementExport(page),
        `ai-gateway-credit-statement-${month}.csv`,
        API.statement,
        `month=${month}&format=csv`,
      ],
    ] as const) {
      await checked(receipt, { feature: `W02-N11-CSV-${role}-${kind}`, lane: 'ui', where: BILLING_PAGE }, async () => {
        const directory = await mkdtemp(join(tmpdir(), 'e2e-wallet-csv-'));
        try {
          const download = await ui(`Export ${kind} CSV`, async () => {
            const pending = page.waitForEvent('download');
            await button.click();
            return pending;
          });
          expect(download.suggestedFilename()).toBe(expectedName);
          const path = join(directory, 'download.csv');
          await download.saveAs(path);
          const api = await csvAs(scopes, actor, endpoint, query);
          expect(api.status).toBe(200);
          expect(parseCsv(await readFile(path, 'utf8'))).toEqual(api.rows);
          return api.rows.length;
        } finally {
          await rm(directory, { recursive: true, force: true });
        }
      });
    }
    for (const [width, colorScheme] of [
      [1440, 'light'],
      [390, 'light'],
      [390, 'dark'],
    ] as const) {
      await ui('Wallet viewport', () => page.setViewportSize({ width, height: 900 }));
      await ui('Wallet scheme', () => page.emulateMedia({ colorScheme }));
      await settle(page);
      if (width === 390) expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);
      await receipt.shot(page, `${role}-wallet-${width}-${colorScheme}`);
    }
  } finally {
    await context.close();
  }
  return summary;
}
