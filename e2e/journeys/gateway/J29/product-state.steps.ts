import { expect, type TestInfo } from '@playwright/test';
import type { StateProbe } from '../../../lib/state-probe';
import type { GatewayAdmin } from '../../../lib/gateway-admin';
import { attachSeededIds, type Phase } from '../_shared/contract';
import { requireUuid } from '../_shared/identifiers';
import { readConfigVersions, readKeyRecords, readActiveConfigEquality } from '../_shared/postgres-config';
import { readGatewayOrgIdentity } from '../_shared/gateway-state';
import { readExportSyncs } from '../_shared/postgres-exports';
import { POLL } from '../../../lib/state-probe';
import { partialStep } from './partial-step';

type Probe = Pick<StateProbe, 'pg'>;

/** Provenance: shared PG projections and gateway admin_orgs.go identity;
 * futureagi/agentcc/services/managed_models_default.py:ensure_first_org_config
 * and views/api_key.py:create on da4c81cc623f909ce38f53e01b557ee8374d5adb.
 * Managed config creation follows the explicit first key/config write, NOT signup.
 * Both admitted profiles therefore have this same pre-write census. The distinct
 * managed post-write singleton/managed-enabled mirror clause remains J29-O03:
 * bind the composed 733 lifecycle and exact minted key/config IDs before adding it.
 * Never call this pre-write helper after creating a key or seed a config to pass it.
 * This is ONLY agentcc census, not accounts personal-key/733 ownership proof.
 * Stage A must supply a genuinely fresh org; provisionActor is NOT a substitute.
 */
export async function checkGatewaySignupCensus(
  probe: Probe, admin: GatewayAdmin, info: TestInfo, organizationId: string,
  profile: 'default-off' | 'managed-new-org', phase: Phase,
): Promise<void> {
  requireUuid(organizationId);
  if (!['default-off', 'managed-new-org'].includes(profile)) throw new Error('Unbound W00 profile');
  await attachSeededIds(info, 'J29', { organizationIds: [organizationId] });
  await partialStep(info, 'J29-GW-A03', 'gateway-signup-census', phase, async () => {
    expect(await readKeyRecords(probe, organizationId), 'J29: no automatically minted gateway key records').toEqual([]);
    const active = (await readConfigVersions(probe, organizationId)).filter(row => row.is_active && !row.deleted);
    expect(active, 'J29: signup has no active config before the first explicit key/config write').toEqual([]);
    expect(await readGatewayOrgIdentity(admin, organizationId),
      'J29: signup mirror is null before the first explicit key/config write').toBeNull();
  });
}

/** PG-only rule witness. Expected guardrails must be source-pinned by the actual
 * UI edit's owner; never substitute the gateway-transformed JSON. Gateway request
 * effects, activation refusal, reload, roles and second tenant remain owed A05.
 */
export async function checkActiveRuleVersion(
  probe: Probe, info: TestInfo, organizationId: string,
  expected: { id: string; version: number; guardrails: unknown }, phase: Phase,
): Promise<void> {
  requireUuid(expected.id);
  expect(Number.isSafeInteger(expected.version) && expected.version > 0).toBe(true);
  await attachSeededIds(info, 'J29', { organizationIds: [requireUuid(organizationId)] });
  await partialStep(info, 'J29-GW-A05', 'active-rule-version', phase, async () => {
    // Exact JSONB equality in PG, no secret-bearing config in artifacts.
    expect(await readActiveConfigEquality(probe, organizationId, 'guardrails', expected.guardrails))
      .toEqual([{ id: expected.id, version: expected.version, matches: true }]);
  });
}

/** Source: integrations/models/sync_log.py via the shared scoped reader.
 * Read EVERY run for the minted connection, not just known expected IDs: extra
 * coalescing/replay writes cannot hide. These are backend counts, NOT proof of
 * local-sink delivery. Sink multiplicities, actual outage and UI stay owed A06.
 */
export async function checkSuccessfulSyncSet(
  probe: Probe, info: TestInfo, organizationId: string, connectionId: string,
  expected: readonly { id: string; traces_fetched: number; spans_synced: number }[], phase: Phase,
): Promise<void> {
  if (expected.length === 0 || new Set(expected.map(run => run.id)).size !== expected.length) {
    throw new Error('Expected a nonempty unique minted sync cohort');
  }
  for (const run of expected) {
    requireUuid(run.id);
    for (const count of [run.traces_fetched, run.spans_synced]) {
      if (!Number.isSafeInteger(count) || count < 0) throw new Error('Invalid expected sync count');
    }
  }
  await attachSeededIds(info, 'J29', { organizationIds: [requireUuid(organizationId)],
    connectionIds: [requireUuid(connectionId)], runIds: expected.map(run => run.id) });
  await partialStep(info, 'J29-GW-A06', 'persisted-sync-set', phase, async () => {
    const byId = (a: { id: string }, b: { id: string }) => a.id.localeCompare(b.id);
    await expect.poll(async () => (await readExportSyncs(probe, organizationId, connectionId))
      .map(run => ({ id: run.id, traces_fetched: run.traces_fetched, spans_synced: run.spans_synced,
        status: run.status, completed: run.completed_at !== null, deleted: run.deleted })).sort(byId),
    POLL.ASYNC_JOB).toEqual(expected.map(run => ({ ...run, status: 'success', completed: true, deleted: false })).sort(byId));
  });
}
