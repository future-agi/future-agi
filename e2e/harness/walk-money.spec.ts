import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { parseCsv, statementFrom, usd6, entriesFromApiShape, exactFields } from '../lib/walk/billing';
import { grantOnlyCredits, parseNeverFundedMessage } from '../lib/walk/credit-message';
import {
  EXACT_PRICING,
  usdToMicros,
  readChatStream,
  readMessagesStream,
  tokenOf,
  moneySnapshot,
  type EntryRow,
} from '../lib/walk/money';
import { expectedLogCells, formatCostCell, formatExactCost, formatBalance } from '../lib/walk/money-ui';
import {
  billingNoAccessMessage,
  consentRefusalMessages,
  consentVersion,
  minimumTopupText,
} from '../lib/walk/money-source';
import type { StateProbe } from '../lib/state-probe';
import { declaredOverlay, HARNESS_PATHS } from '../lib/walk/receipt';
import { assertValueFreeReceipt } from '../lib/walk/receipt-values.mjs';
// R-LIT: all fabricated prose, stream payloads, IDs and repeated head characters below are synthetic offline fixtures.
// The money walks' own readers (W01 one-key, W02 wallet; lib/walk/money.ts,
// money-ui.ts, billing.ts) and the overlay a walk proof declares
// (lib/walk/receipt.ts). Offline: no stack. Run: bin/e2e test harness/walk-money.spec.ts --grep offline
const head = 'a'.repeat(40);

// Synthetic prose only: the test verifies source-driven selection, not a second
// hand-maintained copy of the product's customer-facing message.
const creditSource = `package models
const (
  creditAddHosted = "hosted remedy"
  creditAddGrantOnly = "operator remedy"
  creditOwnKeys = "own keys remedy"
)
func CreditsExhaustedMessage(neverFunded, grantOnly bool) string {
  state := "previously funded"
  if neverFunded {
    state = "never funded"
  }
  return fmt.Sprintf("%s / %s / %s", state, creditAdd(grantOnly), creditOwnKeys)
}
func creditAdd(grantOnly bool) string {
  if grantOnly {
    return creditAddGrantOnly
  }
  return creditAddHosted
}`;

test('offline: exhausted-credit prose and punctuation are read from product source', () => {
  expect(parseNeverFundedMessage(creditSource, false)).toBe('never funded / hosted remedy / own keys remedy');
  expect(
    parseNeverFundedMessage(
      creditSource.replace('never funded', 'new product words').replace('%s / %s / %s', '%s: %s; %s!'),
      false,
    ),
  ).toBe('new product words: hosted remedy; own keys remedy!');
  expect(parseNeverFundedMessage(creditSource.replace('hosted remedy', 'another remedy'), false)).toBe(
    'never funded / another remedy / own keys remedy',
  );
});

test('offline: missing, duplicate and unsupported credit-message sources fail closed', () => {
  for (const source of [
    '',
    creditSource + '\n' + creditSource,
    creditSource.replace('if neverFunded', 'if !neverFunded'),
    creditSource.replace('return creditAddHosted', 'return creditAddGrantOnly'),
    creditSource.replace('%s / %s / %s', '%v / %s / %s'),
    creditSource.replace('creditAdd(grantOnly), creditOwnKeys', 'creditOwnKeys, creditAdd(grantOnly)'),
  ]) {
    expect(() => parseNeverFundedMessage(source, true)).toThrow(/credit message source/);
  }
});

test('offline: mounted credit mode selects the corresponding product branch', () => {
  expect(parseNeverFundedMessage(creditSource, grantOnlyCredits('managed:\n  grant_only_credits: true\n'))).toBe(
    'never funded / operator remedy / own keys remedy',
  );
  expect(parseNeverFundedMessage(creditSource, grantOnlyCredits('managed:\n  grant_only_credits: false\n'))).toBe(
    'never funded / hosted remedy / own keys remedy',
  );
  expect(grantOnlyCredits('managed: {}')).toBe(false);
  for (const source of [
    'managed: {grant_only_credits: "true"}',
    'managed: {grant_only_credits: true, grant_only_credits: false}',
    'managed: {grant_only_credits: ${MODE}}',
    'managed: &m {grant_only_credits: true}\ncopy: *m',
  ]) {
    expect(() => grantOnlyCredits(source)).toThrow(/credit message config/);
  }
  expect(() => parseNeverFundedMessage(creditSource.replace('creditAddGrantOnly =', 'missing ='), true)).toThrow(
    /expected one creditAddGrantOnly/,
  );
});

test.describe('walk money offline: overlay declaration', () => {
  test('offline: an overlay names a full commit and harness-owned paths only', () => {
    expect(declaredOverlay(undefined, undefined)).toBeNull();
    expect(declaredOverlay(head, 'e2e bin/e2e/ bin/lib agentcc-gateway/cmd/agentcc-mcpmock')).toEqual({
      from: head,
      paths: ['e2e', 'bin/e2e', 'bin/lib', 'agentcc-gateway/cmd/agentcc-mcpmock'],
    });
    expect(() => declaredOverlay('abc123', 'e2e')).toThrow(/not a full commit id/);
    expect(() => declaredOverlay(head, '')).toThrow(/harness paths only/);
    for (const product of [
      'futureagi',
      'agentcc-gateway',
      'frontend/src',
      'e2e/../futureagi',
      'agentcc-gateway/cmd/agentcc',
    ]) {
      expect(() => declaredOverlay(head, `e2e ${product}`), product).toThrow(/harness paths only/);
    }
    expect([...HARNESS_PATHS]).toEqual([
      'e2e',
      'bin/e2e',
      'bin/lib',
      'agentcc-gateway/cmd/agentcc-mcpmock',
      'agentcc-gateway/cmd/agentcc-otlpmock',
    ]);
  });

  test('offline: a receipt may carry the overlay commit and its paths, nothing else raw', () => {
    expect(() =>
      assertValueFreeReceipt(
        JSON.stringify({ realHead: head, overlay: { from: head, paths: ['e2e'], identical: true } }),
      ),
    ).not.toThrow();
    expect(() => assertValueFreeReceipt(JSON.stringify({ overlay: { paths: [head] } }))).toThrow(/key-shaped/);
    expect(tokenOf('org')).toMatch(/^sha256:[a-f0-9]{64}$/);
    expect(() =>
      assertValueFreeReceipt(JSON.stringify({ read: tokenOf('0b5a4e2c-1111-4c3a-9d2e-aaaaaaaaaaaa') })),
    ).not.toThrow();
  });
});

test.describe('walk money offline: wire readers', () => {
  test('offline: an OpenAI chat stream reads back its text and usage chunk', () => {
    const chunk = (body: unknown) => `data: ${JSON.stringify(body)}\n\n`;
    const raw =
      chunk({ choices: [{ delta: { role: 'assistant' } }] }) +
      chunk({ choices: [{ delta: { content: 'echo: ' } }] }) +
      chunk({ choices: [{ delta: { content: 'hi' } }] }) +
      chunk({ choices: [], usage: { prompt_tokens: 7, completion_tokens: 7 } }) +
      'data: [DONE]\n\n';
    expect(readChatStream(raw)).toEqual({ text: 'echo: hi', prompt: 7, completion: 7 });
    expect(readChatStream('data: [DONE]\n\n')).toEqual({ text: '', prompt: null, completion: null });
  });

  test('offline: an Anthropic Messages stream reads input from message_start and output from message_delta', () => {
    const ev = (type: string, body: object) => `event: ${type}\ndata: ${JSON.stringify({ type, ...body })}\n\n`;
    const raw =
      ev('message_start', { message: { usage: { input_tokens: 7, output_tokens: 0 } } }) +
      ev('content_block_delta', { delta: { type: 'text_delta', text: 'echo: ' } }) +
      ev('content_block_delta', { delta: { type: 'text_delta', text: 'hi' } }) +
      ev('message_delta', { delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 7 } }) +
      ev('message_stop', {});
    expect(readMessagesStream(raw)).toEqual({ text: 'echo: hi', prompt: 7, completion: 7 });
  });
});

// Additional offline cases; executed only by the lead on the assembled head.
test('offline: snapshot prices half-up once then scale 10500 exactly', () => {
  const snapshot = JSON.parse(
    readFileSync(new URL('../../agentcc-gateway/internal/catalog/openrouter_snapshot.json', import.meta.url), 'utf8'),
  );
  // Independent rational arithmetic: 1 USD = 1e9 nano-dollars, no float tolerance.
  const nanos = (text: string) => {
    const match = /^(\d+)\.(\d{1,9})$/.exec(text);
    if (!match) throw new Error('snapshot price shape changed');
    return BigInt(match[1]) * 1_000_000_000n + BigInt(match[2].padEnd(9, '0'));
  };
  for (const [id, price] of Object.entries(EXACT_PRICING)) {
    const models = snapshot.data.filter((model: { id: string }) => model.id === id);
    expect(models.length).toBe(1);
    const card = models[0].pricing;
    const upstream = (7n * (nanos(card.prompt) + nanos(card.completion)) + 500n) / 1000n;
    const billed = (upstream * 10500n + 5000n) / 10000n;
    expect([String(upstream), String(billed), String(billed - upstream), usd6(billed)]).toEqual([
      price.upstream,
      price.billed,
      price.markup,
      price.header,
    ]);
  }
});

test('offline: USD decimal conversion rejects fractional micros and absent amounts', () => {
  expect(['0.000088', '1', '-0.000441', '1e-6', '0.998925'].map(usdToMicros)).toEqual([
    '88',
    '1000000',
    '-441',
    '1',
    '998925',
  ]);
  for (const value of [null, undefined, true, '', '0.0000009', 0.0000009, NaN, Infinity, 1000001]) {
    expect(() => usdToMicros(value)).toThrow();
  }
});

test('offline: exact display ports preserve money display without using it as billing evidence', () => {
  const values = [null, 0, 0.000009, 0.000092, 0.000441, 0.998925, 0.001075];
  const exact = ['—', '$0.00', '$0.000009', '$0.000092', '$0.000441', '$0.998925', '$0.001075'];
  for (const format of [formatCostCell, formatExactCost, formatBalance]) {
    expect(values.map(format)).toEqual(exact);
    expect(['1.000092', '1234.567890', '-0.000092', '9007199254740993.000001', '9.2e-5', '-0.00'].map(format)).toEqual([
      '$1.000092',
      '$1,234.56789',
      '-$0.000092',
      '$9,007,199,254,740,993.000001',
      '$0.000092',
      '$0.00',
    ]);
    expect([undefined, '', NaN, Infinity, 'not money'].map(format)).toEqual(['—', '—', '—', '—', '—']);
  }
});

test('offline: log cell billing labels and v2 attempt presence are exact', () => {
  const row = {
    id: 'synthetic-row',
    request_id: 'synthetic-request',
    model: 'gpt-4o',
    status_code: 200,
    billing_mode: 'managed',
    cost: '0.000092',
    cost_upstream: '0.000088',
    cost_markup: '0.000004',
    input_tokens: 7,
    output_tokens: 7,
    attempt_count: 2,
    generation_id: 'synthetic-generation',
  };
  expect(expectedLogCells([row]).map((value) => JSON.parse(value))).toEqual([
    {
      Model: 'gpt-4o',
      Status: '200',
      Cost: '$0.000092',
      Tokens: '7 / 7',
      'Billing mode': 'Future AGI credits',
      'Upstream cost': '$0.000088',
      Markup: '$0.000004',
      Attempts: '2',
    },
  ]);
  expect(JSON.parse(expectedLogCells([{ ...row, generation_id: null, billing_mode: null }])[0]).Attempts).toBe('—');
});

test('offline: CSV quoting and exact signed micro formatting', () => {
  expect(parseCsv('line,key,amount_usd\r\nspend,"a,b",1.000000\r\ncredit,"say ""hi""",\r\n')).toEqual([
    ['line', 'key', 'amount_usd'],
    ['spend', 'a,b', '1.000000'],
    ['credit', 'say "hi"', ''],
  ]);
  expect([1000000n, 88n, -441n].map(usd6)).toEqual(['1.000000', '0.000088', '-0.000441']);
});

test('offline: statement groups estimated and measured rows separately in exact micros', () => {
  const entry = (kind: string, amount: string, estimated = false): EntryRow => ({
    kind,
    amount_micros: amount,
    model: kind === 'spend' ? 'synthetic-model' : '',
    usage_estimated: estimated,
    idempotency_key: `synthetic-${kind}-${amount}`,
    request_id: '',
    stripe_ref: '',
    note: '',
    markup_micros: kind === 'spend' ? '4' : null,
    upstream_cost_micros: null,
    occurred_at: '2026-11-05T00:00:00Z',
  });
  expect(
    statementFrom([entry('grant', '1000000'), entry('spend', '-92'), entry('spend', '-92', true)], '2026-11'),
  ).toEqual({
    month: '2026-11',
    currency: 'USD',
    opening_balance: '0',
    closing_balance: '999816',
    credits_by_kind: {
      topup: '0',
      grant: '1000000',
      refund: '0',
      adjustment: '0',
      expiry: '0',
      topup_reversal: '0',
      dispute_hold: '0',
      dispute_release: '0',
    },
    spend_by_model: [
      { model: 'synthetic-model', count: 1, spend_usd: '92', markup_usd: '4', estimated: false },
      { model: 'synthetic-model', count: 1, spend_usd: '92', markup_usd: '4', estimated: true },
    ],
  });
  expect(() => entriesFromApiShape({ entries: [], next_cursor: 'synthetic-next' })).toThrow();
  expect(() => exactFields({ unexpected: true }, ['expected'])).toThrow();
});

test('offline: product prose readers bind current consent, no-access and both credit remedies', () => {
  expect(consentVersion()).toBe('auto-reload-v1');
  expect(consentRefusalMessages()).toEqual({
    auto_reload_consent_required: 'Enabling auto-reload requires acknowledgement of the current terms.',
    auto_reload_consent_outdated: 'Auto-reload terms changed. Review and acknowledge the current version.',
  });
  expect(billingNoAccessMessage()).toBe('Ask an organization Owner or Admin for access to Billing.');
  expect(minimumTopupText()).toBe('25');
  const source = readFileSync(
    new URL('../../agentcc-gateway/internal/models/credit_refusals.go', import.meta.url),
    'utf8',
  );
  const hosted = parseNeverFundedMessage(source, false);
  const grant = parseNeverFundedMessage(source, true);
  expect(typeof hosted).toBe('string');
  expect(typeof grant).toBe('string');
  expect(hosted).not.toBe(grant);
});

test('offline: W01 and W02 receipts retain only classified digests', () => {
  for (const walk of ['W01', 'W02'])
    expect(() =>
      assertValueFreeReceipt(
        JSON.stringify({
          walk,
          verdicts: [
            {
              feature: 'synthetic-check',
              lane: 'storage',
              verdict: 'PASS',
              detail: { read: tokenOf('synthetic-state') },
            },
          ],
        }),
      ),
    ).not.toThrow();
});

// W01-F02: synthetic probe only; no DB, gateway or browser is contacted.
// Existing W01-N15/W02-N21 compare this same snapshot before/after A's actions.
test('offline: B-only consent insert edit and delete change the isolation snapshot', async () => {
  const a = 'synthetic-org-A';
  const b = 'synthetic-org-B';
  const receipt = {
    id: 'synthetic-consent-B',
    organization_id: b,
    actor_id: 'synthetic-owner-B',
    actor_identifier: 'synthetic-owner-B',
    consent_version: 'auto-reload-v1',
    accepted_at: '2026-11-05T00:00:00Z',
    threshold_micros: '1000000',
    amount_micros: '25000000',
  };
  const receipts: Record<string, (typeof receipt)[]> = { [a]: [], [b]: [] };
  const probe = {
    async pg<T>(sql: string, values: unknown[] = []): Promise<T[]> {
      if (sql.includes('information_schema.tables')) return [{ n: '1' }] as T[];
      if (sql.includes('FROM "agentcc_auto_reload_consent"')) {
        expect(sql).toContain('WHERE organization_id = $1 ORDER BY 1');
        expect(values).toHaveLength(1);
        const org = String(values[0]);
        expect([a, b]).toContain(org);
        return receipts[org]
          .map((row) => JSON.stringify(row))
          .sort()
          .map((j) => ({ j })) as T[];
      }
      return [];
    },
  } as StateProbe;
  const aBefore = await moneySnapshot(probe, a);
  const empty = await moneySnapshot(probe, b);
  expect(empty.agentcc_auto_reload_consent).toEqual([]);
  receipts[b].push({ ...receipt });
  const inserted = await moneySnapshot(probe, b);
  expect(inserted).not.toEqual(empty);
  expect(inserted.agentcc_auto_reload_consent).toEqual([JSON.stringify(receipt)]);
  receipts[b][0].consent_version = 'synthetic-mutated-version';
  const edited = await moneySnapshot(probe, b);
  expect(edited).not.toEqual(inserted);
  receipts[b] = [];
  const deleted = await moneySnapshot(probe, b);
  expect(deleted).not.toEqual(edited);
  expect(deleted).toEqual(empty);
  expect(await moneySnapshot(probe, a)).toEqual(aBefore);
  receipts[a].push({ ...receipt, organization_id: a, id: 'synthetic-consent-A' });
  expect(await moneySnapshot(probe, b)).toEqual(empty);
});
