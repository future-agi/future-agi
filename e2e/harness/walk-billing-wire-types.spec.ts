import { test } from '@playwright/test';
import assert from 'node:assert/strict';
import { entriesFromApiShape, ENTRY_CSV_COLUMNS, parseCsv, statementFromApiShape } from '../lib/walk/billing';
import { readWallet } from '../lib/walk/wallet-read';

// Visibly synthetic offline data, never credentials or live organization records.
// entry_values / ManagedCreditEntrySerializer at 18519f182cee7c61ef70b959d920bd710e0a6841:454-517.
const spend = {
  id: '11111111-1111-4111-8111-111111111111',
  org_id: 'synthetic-org-A',
  api_key_id: 'synthetic-key-id-A',
  agent_run_id: 'synthetic-run-id-A',
  agent_id: null,
  occurred_at: '2026-11-01T00:00:01Z',
  kind: 'spend',
  amount_usd: -0.000625,
  request_id: 'synthetic-request-A',
  model: 'synthetic-model',
  provider: 'synthetic-provider',
  markup_usd: 0.000004,
  markup_bps: 500,
  cost_source: 'synthetic-measured',
  stripe_ref: '',
};
const grant = {
  ...spend,
  id: '22222222-2222-4222-8222-222222222222',
  api_key_id: null,
  agent_run_id: null,
  occurred_at: '2026-11-01T00:00:00Z',
  kind: 'grant',
  amount_usd: 1,
  request_id: '',
  model: '',
  provider: '',
  markup_usd: null,
  markup_bps: null,
  cost_source: '',
};
const identity = (row: typeof spend | typeof grant) => ({
  id: row.id,
  org_id: row.org_id,
  api_key_id: row.api_key_id,
  agent_run_id: row.agent_run_id,
  agent_id: row.agent_id,
});
const baseline = () => ({ entries: [structuredClone(spend), structuredClone(grant)], next_cursor: null });
const expected = () => ({ orgId: 'synthetic-org-A', entries: [identity(grant), identity(spend)] });
// Cast permits the same assertion suite to run on the pre-migration one-argument reader.
const read = entriesFromApiShape as (value: any, inventory?: ReturnType<typeof expected>) => any[];
const csvHeader = 'occurred_at,kind,amount_usd,request_id,model,provider,markup_usd,markup_bps,cost_source,stripe_ref';
const ledgerCsv = `${csvHeader}\r\n2026-11-01T00:00:01Z,spend,-0.000625,synthetic-request-A,synthetic-model,synthetic-provider,0.000004,500,synthetic-measured,\r\n2026-11-01T00:00:00Z,grant,1.000000,,,,,,,\r\n`;
const statement = {
  month: '2026-11', currency: 'USD', opening_balance: 0, closing_balance: 0.999375,
  credits_by_kind: { topup: 0, grant: 1, refund: 0, adjustment: 0, expiry: 0, topup_reversal: 0, dispute_hold: 0, dispute_release: 0 },
  spend_by_model: [{ model: 'synthetic-model', count: 1, spend_usd: 0.000625, markup_usd: 0.000004, estimated: false }],
};

// Executes the real readWallet owner/admin path through its existing money, CSV,
// and gateway assertions, then stops before UI. All transports are synthetic.
// No credentials, HTTP server, database, browser, or product implementation is mocked.
async function wallet(role: string, page = baseline(), csv = ledgerCsv, storedIdentities = expected().entries) {
  const stop = new Error('synthetic-stop-before-browser');
  const oldFetch = globalThis.fetch;
  const oldFirstFail = process.env.E2E_WALK_FIRST_FAIL;
  process.env.E2E_WALK_FIRST_FAIL = '1';
  const checks: string[] = [];
  let identityReads = 0;
  const rows = [grant, spend].map((row) => ({
    kind: row.kind, amount_micros: row.kind === 'grant' ? '1000000' : '-625',
    idempotency_key: `synthetic-${row.kind}`, request_id: row.request_id, model: row.model,
    stripe_ref: row.stripe_ref, note: '', markup_micros: row.markup_usd === null ? null : '4',
    upstream_cost_micros: null, usage_estimated: false, occurred_at: row.occurred_at,
  }));
  const summary = {
    available_usd: 0.999375, gateway: { available_usd: 0.999375, held_usd: 0, spent_usd: 0.000625 },
    pending_settlement_usd: 0, markup_percent: 5, billing_available: false,
  };
  globalThis.fetch = async (url, options) => {
    assert.equal(new URL(String(url)).hostname, 'localhost');
    assert.equal(options?.redirect, 'error');
    const pathname = new URL(String(url)).pathname;
    if (pathname.endsWith('/entries/')) return new Response(csv, { status: 200 });
    assert.ok(pathname.endsWith('/statement/'));
    return new Response('line,key,count,amount_usd,markup_usd,estimated\r\nclosing_balance,,,0.999375,,\r\n', { status: 200 });
  };
  const probe = { async pg(sql: string, values: unknown[]) {
    assert.deepEqual(values, ['synthetic-org-A']);
    assert.ok(sql.includes('WHERE organization_id = $1'));
    if (sql.includes('api_key_id')) {
      assert.ok(sql.includes('agent_run_id'));
      assert.ok(sql.includes("NULL::text AS agent_id"));
      assert.ok(!sql.includes('SELECT *'));
      identityReads++;
      return structuredClone(storedIdentities);
    }
    if (sql.includes('FROM usage_managedcreditentry')) return structuredClone(rows);
    assert.ok(sql.includes('FROM usage_managedcreditaccount'));
    return [{ settled_spend_micros: '625' }];
  } };
  const actor = { api: { async get(path: string) {
    if (path.endsWith('/entries/')) return { result: page };
    if (path.endsWith('/statement/')) return { result: structuredClone(statement) };
    assert.equal(path, '/agentcc/managed-credits/'); return { result: summary };
  } } };
  try {
    await readWallet(
      { headers: () => ({}) } as any, actor as any, role,
      { receipt: { verdict: (row: any) => { assert.equal(row.verdict, 'PASS'); checks.push(row.feature); } }, open: async () => { throw stop; } } as any,
      probe as any,
      { credits: async (org: string) => { assert.equal(org, 'synthetic-org-A'); return { available_micros: '999375' }; } } as any,
      'synthetic-org-A', '2026-11',
    );
    assert.fail('wallet must stop before opening a browser');
  } catch (error) {
    if (error !== stop) throw error;
    assert.deepEqual(checks, [`W02-N12-${role}`, `W02-N18-${role}`]);
    assert.equal(identityReads, 1, 'independent identity query must run for both roles');
  } finally {
    globalThis.fetch = oldFetch;
    if (oldFirstFail === undefined) delete process.env.E2E_WALK_FIRST_FAIL;
    else process.env.E2E_WALK_FIRST_FAIL = oldFirstFail;
  }
}
// RF01: field types are from managed_credits.py at the exact c2.6 pin.
// No boolean field exists in this 15-field entry. Boolean/string confusion is
// nevertheless probed on every field; no statement/summary contract is changed.
const wireTypes: Record<string, string> = {
  id: 'string', org_id: 'string', api_key_id: 'string|null', agent_run_id: 'string|null',
  agent_id: 'null', occurred_at: 'string', kind: 'string', amount_usd: 'number',
  request_id: 'string', model: 'string', provider: 'string', markup_usd: 'number|null',
  markup_bps: 'integer|null', cost_source: 'string', stripe_ref: 'string',
};
const equivalents: Record<string, unknown> = Object.fromEntries(
  Object.entries(spend).map(([field, value]) => [field,
    field === 'occurred_at' ? Date.parse(String(value)) :
    field === 'agent_id' ? 'null' :
    typeof value === 'number' ? String(value) : [value],
  ]),
);
for (const [field, wireType] of Object.entries(wireTypes)) {
  const wrongTypes: [string, unknown][] = [
    ['equivalent', equivalents[field]], ['object', { synthetic: 'wrong-type' }],
    ['array', []], ['boolean-true', true], ['boolean-false', false], ['undefined', undefined],
  ];
  if (wireType === 'string') wrongTypes.push(['null', null], ['number', 7]);
  if (wireType === 'string|null') wrongTypes.push(['number', 7]);
  if (wireType === 'null') wrongTypes.push(['number', 0], ['string-true', 'true']);
  if (wireType.startsWith('number') || wireType.startsWith('integer')) {
    wrongTypes.push(['string-true', 'true'], ['string-false', 'false'], ['nan', NaN], ['infinity', Infinity]);
  }
  if (wireType === 'number') wrongTypes.push(['null', null]);
  if (wireType === 'integer|null') wrongTypes.push(['fraction', 500.5]);
  for (const [label, value] of wrongTypes) {
    test(`offline: RF01 reader refuses ${field} ${label} (${wireType})`, () => {
      const page: any = baseline(); page.entries[0][field] = value;
      assert.throws(() => read(page, expected()));
    });
    for (const role of ['owner', 'admin']) {
      test(`offline: RF01 ${role} wallet refuses ${field} ${label}`, async () => {
        const page: any = baseline(); page.entries[0][field] = value;
        await assert.rejects(wallet(role, page));
      });
    }
  }
  test(`offline: RF01 ${field} absent is not a legal null`, () => {
    const page: any = baseline(); delete page.entries[0][field];
    assert.throws(() => read(page, expected()));
  });
}
for (const role of ['owner', 'admin']) {
  test(`offline: RF01 ${role} exact wire shape passes without normalization`, async () => {
    const page = baseline(); const before = structuredClone(page);
    assert.strictEqual(read(page, expected()), page.entries);
    assert.deepEqual(page, before);
    await wallet(role, page);
    assert.deepEqual(page, before);
  });
}
for (const field of ['api_key_id', 'agent_run_id', 'agent_id', 'markup_usd', 'markup_bps']) {
  test(`offline: RF01 ${field} legal null stays legal but present-undefined and absence fail`, () => {
    // The grant provides legal nulls for every nullable field at the pin.
    const page: any = baseline(); const inventory = expected();
    assert.equal(page.entries[1][field], null);
    assert.strictEqual(read(page, inventory), page.entries);
    page.entries[1][field] = undefined;
    assert.throws(() => read(page, inventory));
    delete page.entries[1][field];
    assert.throws(() => read(page, inventory));
  });
}
// Isolate runtime type checks from identity equality: a defective oracle agreeing
// on a non-string identity must not admit an invalid JSON wire type.
for (const field of ['id', 'api_key_id', 'agent_run_id']) {
  test(`offline: RF01 matching wrong-type oracle cannot admit ${field}`, () => {
    const page: any = baseline(); const inventory: any = expected();
    page.entries[0][field] = 7; inventory.entries[1][field] = 7;
    assert.throws(() => read(page, inventory));
  });
}
