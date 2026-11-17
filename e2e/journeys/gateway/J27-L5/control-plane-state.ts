import { isDeepStrictEqual } from 'node:util';
import type { TestInfo } from '@playwright/test';
import type { StateProbe } from '../../../lib/state-probe';
import type { GatewayAdmin } from '../../../lib/gateway-admin';
import {
  readConfigVersions, readKeyRecords, readCreditAccount,
  type ConfigVersion, type KeyRecord, type CreditAccount,
} from '../_shared/postgres-config';
import { readGatewayCredits, type GatewayCreditState } from '../_shared/gateway-state';
import { requireUuid } from '../_shared/identifiers';
import { attachSeededIds } from '../_shared/contract';
import { PACKET } from './obligations';

// SUPPLEMENT ONLY to L5-A03/A05/A06. These real source-pinned reads do not expose
// Redis identity, request holds, per-scope budgets, epochs or per-request debits.
// Consequently this module declares/emits NO complete journey-assertion row.
// It constructs no client, grants no credits, creates no actor and starts no
// lifecycle action. Call only inside the admitted runtime's stopped-writer window.
// There is no default endpoint, retry or poll budget: the missing barrier/TTL and
// lifecycle deadlines must be bound by the runtime, never borrowed from UI waits.

export interface PublicStateFixture {
  organizationId: string;
  // Exact already-minted personal-key record/owner cohort, NOT token material.
  keys: readonly { recordId: string; userId: string }[];
}
export interface PublicStateCheckpoint {
  organizationId: string;
  configVersions: ConfigVersion[];
  keyRecords: KeyRecord[];
  creditAccount: CreditAccount;
  gatewayCredits: GatewayCreditState;
}

type Probe = Pick<StateProbe, 'pg'>;
type Admin = Pick<GatewayAdmin, 'config' | 'orgConfig' | 'credits' | 'key' | 'mcpServed'>;

function requireState(ok: boolean, code: string): asserts ok {
  if (!ok) throw new Error(`L5 public-state supplement: ${code}`);
}

function integer(value: string): bigint {
  requireState(typeof value === 'string' && /^-?(?:0|[1-9][0-9]*)$/.test(value), 'invalid-integer');
  return BigInt(value);
}

function fixtureIds(fixtures: readonly PublicStateFixture[]) {
  requireState(fixtures.length === 2, 'two-synthetic-organizations-required');
  const organizationIds = fixtures.map(f => requireUuid(f.organizationId));
  requireState(new Set(organizationIds).size === 2, 'duplicate-organization');
  const keyRecordIds: string[] = [];
  const userIds: string[] = [];
  for (const fixture of fixtures) {
    requireState(fixture.keys.length > 0, 'nonempty-personal-key-cohort-required');
    for (const key of fixture.keys) {
      keyRecordIds.push(requireUuid(key.recordId));
      userIds.push(requireUuid(key.userId));
    }
  }
  requireState(new Set(keyRecordIds).size === keyRecordIds.length, 'duplicate-key-record');
  // A person can own multiple keys. Only the display attachment deduplicates user
  // IDs; the exact key/owner association below retains every key and multiplicity.
  return { organizationIds, keyRecordIds, userIds: [...new Set(userIds)] };
}

function exactKeyOwners(rows: readonly KeyRecord[], fixture: PublicStateFixture): void {
  // Source: futureagi/agentcc/models/api_key.py:AgentccAPIKey, db_table
  // agentcc_api_key. Include deleted/revoked records: no active-only subset read.
  const actual = rows.map(row => ({ recordId: row.id, userId: row.user_id }))
    .sort((a, b) => a.recordId.localeCompare(b.recordId));
  const expected = fixture.keys.map(key => ({ recordId: key.recordId, userId: key.userId }))
    .sort((a, b) => a.recordId.localeCompare(b.recordId));
  requireState(rows.every(row => row.organization_id === fixture.organizationId), 'foreign-key-organization');
  requireState(isDeepStrictEqual(actual, expected), 'key-owner-exact-set-mismatch');
}

function creditArithmetic(gateway: GatewayCreditState, account: CreditAccount): void {
  // GET wire: agentcc-gateway/internal/server/credits_admin.go:creditsAdminView.
  // Arithmetic: internal/billing/ledger.go:Account.AvailableMicros. Negative
  // available is legal after overshoot; do not clamp it or use float tolerance.
  const granted = integer(gateway.granted_total_micros);
  const spent = integer(gateway.spent_micros);
  const held = integer(gateway.held_micros);
  requireState(integer(gateway.available_micros) === granted - spent - held, 'account-arithmetic-mismatch');
  integer(gateway.granted_version);
  integer(gateway.outbox_pending);
  // PG field/table provenance: public agentcc_managed_credits.py:_report/_show
  // and e2e/flows/gateway/managed-optin-credits.spec.ts, via shared readCreditAccount.
  // Validate exact integers; DO NOT equate PG settled spend to Redis spent while
  // settlement is intentionally outstanding, or infer an empty outbox from it.
  integer(account.granted_total_micros);
  integer(account.settled_spend_micros);
  integer(account.version);
  integer(account.gateway_acked_version);
}

export async function readPublicStateCheckpoint(
  info: TestInfo, probe: Probe, admin: Admin, fixtures: readonly PublicStateFixture[],
): Promise<PublicStateCheckpoint[]> {
  const ids = fixtureIds(fixtures);
  await attachSeededIds(info, PACKET, ids);
  const result: PublicStateCheckpoint[] = [];
  for (const fixture of [...fixtures].sort((a, b) => a.organizationId.localeCompare(b.organizationId))) {
    // Shared parameter-bound read-only queries read ALL selected org records, not
    // only expected output IDs, so extra keys/config versions remain observable.
    // No response fallback, catch-to-empty, full config, header or secret dump.
    let configVersions: ConfigVersion[];
    let keyRecords: KeyRecord[];
    let accounts: CreditAccount[];
    let gatewayCredits: GatewayCreditState;
    try {
      configVersions = await readConfigVersions(probe, fixture.organizationId);
      keyRecords = await readKeyRecords(probe, fixture.organizationId);
      accounts = await readCreditAccount(probe, fixture.organizationId);
      gatewayCredits = await readGatewayCredits(admin, fixture.organizationId);
    } catch {
      // GatewayAdminError carries a response body. Fail with a value-free emitter
      // BEFORE the reporter sees it; never redact/rewrite a captured original.
      // Unavailability is not an empty account, a passing comparison or a red kill.
      throw new Error('L5 public-state supplement: backend-read-unavailable-or-invalid');
    }
    // AgentccOrgConfig in public models/org_config.py: one active nondeleted row.
    requireState(configVersions.every(row => row.organization_id === fixture.organizationId), 'foreign-config-organization');
    requireState(configVersions.filter(row => row.is_active && !row.deleted).length === 1, 'active-config-cardinality');
    exactKeyOwners(keyRecords, fixture);
    requireState(accounts.length === 1, 'credit-account-cardinality');
    creditArithmetic(gatewayCredits, accounts[0]);
    result.push({ organizationId: fixture.organizationId, configVersions, keyRecords,
      creditAccount: accounts[0], gatewayCredits });
  }
  return result;
}

/** Only compare BEFORE/AFTER-ROLLBACK while the SAME reviewed writer barrier is
 * active. This is intentionally not a restore-after-settlement oracle: legitimate
 * post-release spend changes must be reconciled per request by the owed ledger
 * probe. Complete preservation additionally requires authoritative state and TTL,
 * lifecycle/no-flush evidence. Equality here alone NEVER passes L5-A03/A05/A06.
 */
export function assertFrozenPublicStateUnchanged(
  before: readonly PublicStateCheckpoint[], after: readonly PublicStateCheckpoint[],
): void {
  requireState(before.length === 2 && after.length === 2, 'checkpoint-cardinality');
  const canonical = (rows: readonly PublicStateCheckpoint[]) =>
    [...rows].sort((a, b) => a.organizationId.localeCompare(b.organizationId));
  for (const rows of [before, after]) {
    requireState(new Set(rows.map(row => requireUuid(row.organizationId))).size === 2, 'checkpoint-organization-identity');
    for (const row of rows) {
      requireState(row.keyRecords.length > 0 && row.configVersions.length > 0, 'empty-checkpoint');
      creditArithmetic(row.gatewayCredits, row.creditAccount);
    }
  }
  // Exact arrays retain multiplicity, tombstones, versions, timestamps, personal
  // owners, account version/ack and totals. No contains/subset/count-only match.
  requireState(isDeepStrictEqual(canonical(after), canonical(before)), 'frozen-public-state-changed');
}
