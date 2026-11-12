import assert from 'node:assert/strict';

export type BillingMode = 'byok' | 'managed';
export type Role = 'Owner' | 'Admin' | 'Member' | 'Viewer';
export type Org = 'A' | 'B';
export type SearchConfig = {
  enabled: boolean;
  model?: string;
  calls_per_minute?: number;
};
export interface SearchState {
  saved: SearchConfig | null;
  gateway: SearchConfig | null;
  savedVersion: number;
  gatewayVersion: number;
  uiOff: boolean;
  disposition: 'OFF/NOT_QUALIFIED' | 'QUALIFIED';
}
export interface CallWitness {
  org: Org;
  gatewayRequestId: string;
  model: string;
  method: string;
  path: string;
  local: boolean;
}
export interface RequestWitness {
  org: Org;
  gatewayRequestId: string;
  billingMode: BillingMode;
}
export interface LedgerWitness {
  org: Org;
  gatewayRequestId: string;
  holdMicros: number;
  settledMicros: number;
}
export interface Observations {
  calls: CallWitness[];
  requests: RequestWitness[];
  ledger: LedgerWitness[];
  admissionRefusals: number;
}

// A test-only consumer seam, not a runner or product accounting adapter.
// An existing accepted fixture/runtime is bound to these operations;
// value-free native request-log/ledger/grant projections are supplied to it. Never
// infer gatewayRequestId from a caller header, exception text or an estimate.
export interface W16SyntheticPort {
  readonly fixtureKind: 'mock-contract' | 'admitted-local';
  reset(mode: BillingMode): Promise<void>;
  setMinute(minute: number): Promise<void>;
  save(role: Role, org: Org, config: SearchConfig | null): Promise<number>;
  state(org: Org): Promise<SearchState>;
  discover(org: Org): Promise<void>;
  search(org: Org): Promise<void>;
  observe(): Promise<Observations>;
  teardown(): Promise<void>;
}

const complete: SearchConfig = {
  enabled: true,
  model: 'synthetic/embed',
  calls_per_minute: 2,
};

export async function runW16Synthetic(
  port: W16SyntheticPort,
  mode: BillingMode,
): Promise<{ fixtureKind: W16SyntheticPort['fixtureKind']; walked: false }> {
  await port.reset(mode);
  try {
    await port.setMinute(120);
    const beforeB = await port.state('B');
    assert.equal(
      await port.save('Owner', 'A', null),
      200,
      'W16.A04 owner save',
    );
    await port.discover('A');
    await port.search('A');
    assert.equal(
      (await port.observe()).calls.length,
      0,
      'W16.A01 absent dispatch',
    );
    assert.equal((await port.state('A')).uiOff, true, 'W16.A01 absent UI');

    const incomplete = await port.save('Owner', 'A', { enabled: true });
    assert.ok([200, 400].includes(incomplete), 'W16.A01 incomplete response');
    await port.discover('A');
    await port.search('A');
    assert.equal(
      (await port.observe()).calls.length,
      0,
      'W16.A01 incomplete dispatch',
    );
    assert.equal((await port.state('A')).uiOff, true, 'W16.A01 incomplete UI');

    assert.equal(
      await port.save('Admin', 'A', complete),
      200,
      'W16.A04 admin save',
    );
    const enabled = await port.state('A');
    assert.deepEqual(enabled.saved, complete, 'W16.A04 saved config');
    assert.deepEqual(enabled.gateway, enabled.saved, 'W16.A04 config mismatch');
    assert.equal(
      enabled.gatewayVersion,
      enabled.savedVersion,
      'W16.A04 version mismatch',
    );
    assert.equal(enabled.uiOff, false, 'W16.A01 opt-in UI');
    for (const role of ['Member', 'Viewer'] as const) {
      assert.equal(
        await port.save(role, 'A', null),
        403,
        `W16.A04 ${role} write`,
      );
      assert.deepEqual(
        await port.state('A'),
        enabled,
        `W16.A04 ${role} mutation`,
      );
    }

    await port.discover('A');
    assert.equal(
      (await port.observe()).calls.length,
      1,
      'W16.A01 index dispatch',
    );
    await port.search('A');
    assert.equal(
      (await port.observe()).calls.length,
      2,
      'W16.A01 query dispatch',
    );
    await port.search('A');
    const bounded = await port.observe();
    assert.equal(bounded.calls.length, 2, 'W16.A02 third dispatch');
    assert.equal(bounded.admissionRefusals, 1, 'W16.A02 missing third refusal');

    await port.discover('B');
    await port.search('B');
    assert.equal(
      (await port.observe()).calls.filter((c) => c.org === 'B').length,
      0,
      'W16.A04 org B dispatch',
    );
    assert.deepEqual(await port.state('B'), beforeB, 'W16.A04 org B changed');

    assert.equal(
      await port.save('Owner', 'A', { ...complete, enabled: false }),
      200,
      'W16.A04 owner disable',
    );
    // A fresh minute ensures an exhausted quota cannot hide a broken disable.
    await port.setMinute(121);
    await port.discover('A');
    await port.search('A');
    const final = await port.observe();
    assert.equal(final.calls.length, 2, 'W16.A02 post-disable dispatch');
    assert.equal((await port.state('A')).uiOff, true, 'W16.A02 disabled UI');
    assertAccounting(final, mode);
  } finally {
    await port.teardown();
  }
  for (const org of ['A', 'B'] as const) {
    const restored = await port.state(org);
    assert.equal(
      restored.saved?.enabled ?? false,
      false,
      'W16.A05 restored saved enabled',
    );
    assert.equal(
      restored.gateway?.enabled ?? false,
      false,
      'W16.A05 restored gateway enabled',
    );
    assert.equal(restored.uiOff, true, 'W16.A05 restored UI');
    assert.equal(
      restored.disposition,
      'OFF/NOT_QUALIFIED',
      'W16.A05 qualification',
    );
  }
  // Execution/egress/browser receipts belong to the accepted runner. This
  // helper cannot confer WALKED_PASS, even when given an admitted-local port.
  return { fixtureKind: port.fixtureKind, walked: false };
}

function assertAccounting(observed: Observations, mode: BillingMode): void {
  assert.equal(
    observed.requests.length,
    observed.calls.length,
    'W16.A03 request cohort',
  );
  const ids = observed.calls.map((call) => call.gatewayRequestId);
  assert.equal(new Set(ids).size, ids.length, 'W16.A03 distinct gateway IDs');
  for (const call of observed.calls) {
    assert.ok(call.gatewayRequestId.length > 0, 'W16.A03 missing gateway ID');
    assert.equal(call.org, 'A', 'W16.A04 foreign accounting');
    assert.equal(call.model, 'synthetic/embed', 'W16.A01 synthetic model');
    assert.equal(call.method, 'POST', 'W16.A01 embedding method');
    assert.equal(call.path, '/v1/embeddings', 'W16.A01 embedding endpoint');
    assert.equal(call.local, true, 'W16.A01 local embedding only');
    const request = observed.requests.filter(
      (r) => r.org === call.org && r.gatewayRequestId === call.gatewayRequestId,
    );
    assert.equal(request.length, 1, 'W16.A03 request linkage');
    assert.equal(request[0].billingMode, mode, 'W16.A03 billing mode');
    const ledger = observed.ledger.filter(
      (r) => r.org === call.org && r.gatewayRequestId === call.gatewayRequestId,
    );
    assert.equal(
      ledger.length,
      mode === 'managed' ? 1 : 0,
      'W16.A03 ledger linkage',
    );
    for (const entry of ledger) {
      assert.ok(
        Number.isSafeInteger(entry.holdMicros) && entry.holdMicros > 0,
        'W16.A03 synthetic hold',
      );
      assert.ok(
        Number.isSafeInteger(entry.settledMicros) &&
          entry.settledMicros > 0 &&
          entry.settledMicros <= entry.holdMicros,
        'W16.A03 settle cap',
      );
    }
  }
  assert.equal(
    observed.ledger.length,
    mode === 'managed' ? ids.length : 0,
    'W16.A03 extra ledger/refusal charge',
  );
}
