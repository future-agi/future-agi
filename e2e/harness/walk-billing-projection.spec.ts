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

for (const role of ['owner', 'admin']) {
  test(`offline: ${role} accepts exact pinned fifteen-field ledger and retains every value`, () => {
    const page = baseline();
    const before = structuredClone(page);
    assert.equal(Object.keys(page.entries[0]).length, 15);
    assert.strictEqual(read(page, expected()), page.entries);
    assert.deepEqual(page, before);
  });
}
for (const field of ['org_id', 'api_key_id', 'agent_run_id', 'agent_id']) {
  test(`offline: attribution ${field} is required`, () => {
    const page: any = baseline(); delete page.entries[0][field];
    assert.throws(() => read(page, expected()));
  });
  for (const value of [7, false, {}, []]) {
    test(`offline: attribution ${field} refuses ${JSON.stringify(value)}`, () => {
      const page: any = baseline(); page.entries[0][field] = value;
      assert.throws(() => read(page, expected()));
    });
  }
  test(`offline: foreign attribution ${field} is refused`, () => {
    const page: any = baseline(); page.entries[0][field] = `synthetic-foreign-${field}`;
    assert.throws(() => read(page, expected()));
  });
  test(`offline: blank attribution ${field} is refused`, () => {
    const page: any = baseline(); page.entries[0][field] = '';
    assert.throws(() => read(page, expected()));
  });
}
for (const field of ['org_id', 'api_key_id', 'agent_run_id']) {
  test(`offline: unexpected null ${field} is refused`, () => {
    const page: any = baseline(); page.entries[0][field] = null;
    assert.throws(() => read(page, expected()));
  });
}
test('offline: key/run nulls pass only when independent storage is also null', () => {
  const page: any = baseline(); const inventory = expected();
  page.entries[0].api_key_id = null; page.entries[0].agent_run_id = null;
  inventory.entries[1].api_key_id = null; inventory.entries[1].agent_run_id = null;
  assert.strictEqual(read(page, inventory), page.entries);
});
test('offline: exact agent_id remains null even if an oracle supplies an agent', () => {
  const page: any = baseline(); const inventory: any = expected();
  page.entries[0].agent_id = 'synthetic-agent'; inventory.entries[1].agent_id = 'synthetic-agent';
  assert.throws(() => read(page, inventory));
});
test('offline: unknown row and page fields are rejected', () => {
  assert.throws(() => read({ ...baseline(), unexpected: true }, expected()));
  const page: any = baseline(); page.entries[0].unexpected = true;
  assert.throws(() => read(page, expected()));
});
for (const field of ['id', ...ENTRY_CSV_COLUMNS]) {
  test(`offline: original field ${field} is still required`, () => {
    const page: any = baseline(); delete page.entries[0][field];
    assert.throws(() => read(page, expected()));
  });
}
for (const field of ['amount_usd', 'markup_usd']) {
  for (const value of [0.0000009, null, NaN, Infinity, true]) {
    if (field === 'markup_usd' && value === null) continue;
    test(`offline: original exact-micro guard ${field} refuses ${String(value)}`, () => {
      const page: any = baseline(); page.entries[0][field] = value;
      assert.throws(() => read(page, expected()));
    });
  }
}
for (const cursor of ['synthetic-next', '', 0, false, undefined]) {
  test(`offline: incomplete cursor ${String(cursor)} is rejected`, () => {
    assert.throws(() => read({ ...baseline(), next_cursor: cursor }, expected()));
  });
}
test('offline: non-array and over-200 inventory remain rejected', () => {
  assert.throws(() => read({ entries: {}, next_cursor: null }, expected()));
  assert.throws(() => read({ entries: Array.from({ length: 201 }, () => ({ ...spend })), next_cursor: null }, expected()));
});
test('offline: empty complete inventory passes with independent empty inventory', () => {
  assert.deepEqual(read({ entries: [], next_cursor: null }, { orgId: 'synthetic-org-A', entries: [] }), []);
});
test('offline: populated inventory cannot be dropped, duplicated or rebound to another row', () => {
  assert.throws(() => read({ entries: [], next_cursor: null }, expected()));
  assert.throws(() => read({ entries: [spend, spend], next_cursor: null }, expected()));
  const page = baseline(); page.entries[0].id = '33333333-3333-4333-8333-333333333333';
  assert.throws(() => read(page, expected()));
});
test('offline: inventory requires independent organization-bound expectations', () => {
  assert.throws(() => read(baseline()));
  const inventory = expected(); inventory.orgId = 'synthetic-org-B';
  assert.throws(() => read(baseline(), inventory));
  inventory.orgId = ''; assert.throws(() => read(baseline(), inventory));
});
test('offline: CSV columns and quote parsing are unchanged', () => {
  assert.deepEqual([...ENTRY_CSV_COLUMNS], csvHeader.split(','));
  assert.deepEqual(parseCsv('a,b\r\n"x,y","say ""hi"""\r\n'), [['a', 'b'], ['x,y', 'say "hi"']]);
});
test('offline: statement currency and exact field set remain guarded', () => {
  assert.throws(() => statementFromApiShape({ ...statement, currency: 'EUR' }));
  assert.throws(() => statementFromApiShape({ ...statement, unexpected: 1 } as any));
  assert.throws(() => statementFromApiShape({ ...statement, opening_balance: 0.0000009 }));
});

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
for (const role of ['owner', 'admin']) {
  test(`offline: real ${role} wallet preserves money/CSV checks and wires identity oracle`, async () => {
    await wallet(role);
  });
  for (const field of Object.keys(spend)) {
    test(`offline: real ${role} wallet refuses changed ${field}`, async () => {
      const page: any = baseline();
      if (field === 'amount_usd' || field === 'markup_usd') page.entries[0][field] += 0.000001;
      else if (field === 'markup_bps') page.entries[0][field]++;
      else if (field === 'occurred_at') page.entries[0][field] = '2026-11-02T00:00:01Z';
      else page.entries[0][field] = 'synthetic-mutated';
      await assert.rejects(wallet(role, page));
    });
  }
  test(`offline: real ${role} wallet rejects a changed CSV value`, async () => {
    await assert.rejects(wallet(role, baseline(), ledgerCsv.replace('-0.000625', '-0.000624')));
  });
  test(`offline: real ${role} wallet rejects changed CSV columns`, async () => {
    await assert.rejects(wallet(role, baseline(), ledgerCsv.replace('markup_bps,', 'unexpected,')));
  });
  test(`offline: real ${role} wallet rejects a cross-org storage oracle`, async () => {
    const entries = expected().entries; entries[1].org_id = 'synthetic-org-B';
    await assert.rejects(wallet(role, baseline(), ledgerCsv, entries));
  });
}
