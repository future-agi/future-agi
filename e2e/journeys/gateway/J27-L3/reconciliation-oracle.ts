import { isDeepStrictEqual } from 'node:util';

/**
 * DATA-ONLY SUB-ORACLE, not an authoritative reader or a journey verdict.
 * Provenance: J27/L3/PACKET.json, state_protocol.reconciliation_fields and
 * negative_and_restored_controls[L3-C02-ORACLE-FALLIBILITY]. This in-memory
 * representation is authored for synthetic detector controls. It is NOT a
 * product wire body, Redis schema, ledger schema or accepted capture format.
 * A reviewed projection from real captures is still owed (L3-O02/O05/O07).
 * No I/O, credentials, response bodies, repair, reseed or runtime hooks here.
 */
export interface DeliveryObservation {
  request_id: string;
  before_count: number;
  fault_count: number;
  after_count: number;
}
export interface HoldLink { hold_id: string; request_id: string }
export interface HoldTerminal extends HoldLink { terminal_state: 'settled' | 'released' }
export interface DebitObservation { request_id: string; debit_count: number }
export interface CredentialObservation {
  credential_record_id: string;
  credential_epoch: number;
  revocation_state: 'live' | 'revoked' | 'superseded';
}
export interface OracleInput {
  // Independent fixture inventory, not inferred from the observations under test.
  request_ids: readonly string[];
  managed_outage_request_ids: readonly string[];
  mcp_outage_request_ids: readonly string[];
  billable_request_ids: readonly string[];
  holds_before: readonly HoldLink[];
  credentials_before: readonly CredentialObservation[];
  managed_deliveries: readonly DeliveryObservation[];
  mcp_deliveries: readonly DeliveryObservation[];
  hold_terminals: readonly HoldTerminal[];
  debits_after: readonly DebitObservation[];
  credentials_after: readonly CredentialObservation[];
}
export interface OracleFailure {
  row: 'L3-A04' | 'L3-A05' | 'L3-A07' | 'L3-A08' | 'L3-A09';
  code: 'invalid_fixture_inventory' | 'delivery_inventory' | 'post_stop_delivery' |
    'hold_inventory' | 'hold_terminal' | 'debit_inventory' | 'debit_multiplicity' |
    'credential_inventory' | 'credential_changed';
}

const boundedId = (id: string) => typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$/.test(id);
const count = (n: number) => Number.isSafeInteger(n) && n >= 0;
const exact = (actual: readonly string[], expected: readonly string[]) =>
  isDeepStrictEqual([...actual].sort(), [...expected].sort());
const unique = (ids: readonly string[]) => ids.length > 0 &&
  ids.every(boundedId) && new Set(ids).size === ids.length;
const credentialValid = (c: CredentialObservation) => boundedId(c.credential_record_id) &&
  count(c.credential_epoch) && ['live', 'revoked', 'superseded'].includes(c.revocation_state);

/**
 * Check five C02 detector predicates without making the larger row pass.
 * In particular this does NOT check typed HTTP refusals, real Redis failure,
 * money amounts/conservation, idempotency, worker results, authoritative sources,
 * phase/tuple joins, positive recovery, runtime certification or full coverage.
 * Empty failures means ONLY these predicates found no defect in these inputs.
 * A clean synthetic input cannot satisfy L3-A04/A05/A07/A08/A09 or C02 itself.
 */
export function reconciliationDefects(input: OracleInput): OracleFailure[] {
  const faults: OracleFailure[] = [];
  const add = (row: OracleFailure['row'], code: OracleFailure['code']) => faults.push({ row, code });
  const all = input.request_ids;
  const outageIds = [...input.managed_outage_request_ids, ...input.mcp_outage_request_ids];
  const holdIds = input.holds_before.map(h => h.hold_id);
  const credentialIds = input.credentials_before.map(c => c.credential_record_id);
  const validInventory = unique(all) && unique(input.managed_outage_request_ids) &&
    unique(input.mcp_outage_request_ids) && unique(outageIds) && unique(input.billable_request_ids) &&
    unique(holdIds) && unique(input.holds_before.map(h => h.request_id)) && unique(credentialIds) &&
    [...outageIds, ...input.billable_request_ids, ...input.holds_before.map(h => h.request_id)]
      .every(id => all.includes(id)) &&
    input.billable_request_ids.every(id => !outageIds.includes(id)) &&
    input.holds_before.every(h => !outageIds.includes(h.request_id)) &&
    input.credentials_before.every(credentialValid) &&
    ['live', 'revoked', 'superseded'].every(state =>
      input.credentials_before.some(c => c.revocation_state === state));
  if (!validInventory) return [{ row: 'L3-A09', code: 'invalid_fixture_inventory' }];

  const deliveries = (rows: readonly DeliveryObservation[], ids: readonly string[], row: 'L3-A04' | 'L3-A05') => {
    if (!exact(rows.map(r => r.request_id), ids)) add(row, 'delivery_inventory');
    // These IDs are minted for NEW post-stop attempts only. M-HELD's pre-stop
    // delivery is deliberately not in this set (packet inflight_semantics).
    if (rows.some(r => !count(r.before_count) || !count(r.fault_count) || !count(r.after_count) ||
        r.before_count !== 0 || r.fault_count !== 0 || r.after_count !== 0)) {
      add(row, 'post_stop_delivery');
    }
  };
  deliveries(input.managed_deliveries, input.managed_outage_request_ids, 'L3-A04');
  deliveries(input.mcp_deliveries, input.mcp_outage_request_ids, 'L3-A05');

  // Preserve multiplicity: do not deduplicate observed IDs, select the first row
  // or query only expected settlement IDs. Every baseline hold has ONE terminal.
  if (!exact(input.hold_terminals.map(h => h.hold_id), holdIds)) add('L3-A07', 'hold_inventory');
  if (input.hold_terminals.some(h => {
    const before = input.holds_before.find(b => b.hold_id === h.hold_id);
    return !before || before.request_id !== h.request_id ||
      !['settled', 'released'].includes(h.terminal_state);
  })) add('L3-A07', 'hold_terminal');

  if (!exact(input.debits_after.map(d => d.request_id), all)) add('L3-A07', 'debit_inventory');
  if (input.debits_after.some(d => !count(d.debit_count) ||
      d.debit_count !== (input.billable_request_ids.includes(d.request_id) ? 1 : 0))) {
    add('L3-A07', 'debit_multiplicity');
  }

  if (!exact(input.credentials_after.map(c => c.credential_record_id), credentialIds)) {
    add('L3-A08', 'credential_inventory');
  }
  // These fixed fixtures finish revoke/rotation BEFORE baseline; there is no
  // permitted credential mutation during the measured interval. PG row version
  // is not a substitute for this future authoritative epoch/tombstone projection.
  if (input.credentials_after.some(c => {
    const before = input.credentials_before.find(b => b.credential_record_id === c.credential_record_id);
    return !credentialValid(c) || !before || c.credential_epoch !== before.credential_epoch ||
      c.revocation_state !== before.revocation_state;
  })) add('L3-A08', 'credential_changed');
  return faults;
}
