import type { TestInfo } from '@playwright/test';
import type { StateProbe } from '../../../lib/state-probe';
import type { GatewayAdmin } from '../../../lib/gateway-admin';
import { attachSeededIds } from '../_shared/contract';
import { requireCohort, requireUuid } from '../_shared/identifiers';
import { readCreditAccount } from '../_shared/postgres-config';
import { readGatewayCredits } from '../_shared/gateway-state';
import { readRequests, readAttempts } from '../_shared/postgres-requests';

type Probe = Pick<StateProbe, 'pg'>;
type Admin = Pick<GatewayAdmin, 'config' | 'orgConfig' | 'credits' | 'key' | 'mcpServed'>;

/** Existing read-only projections, reusable after the accepted runner supplies
 * the fixture clients. No client is constructed and no grant/key is minted here.
 * Endpoint provenance: GatewayAdmin.credits -> GET /-/orgs/{org}/credits;
 * agentcc-gateway/internal/server/credits_admin.go:creditsAdminView (credits-v1).
 * PG provenance: _shared/postgres-config.ts:readCreditAccount and public
 * futureagi/agentcc/management/commands/agentcc_managed_credits.py:_show/_report;
 * futureagi/agentcc/models/request_log.py and request_attempt.py.
 * All queries use the existing parameter-bound, organization-scoped readers.
 *
 * IMPORTANT: gateway aggregates and request logs are NOT the private per-request
 * monetary ledger, Redis holds, worker delivery evidence or a fence witness.
 * These separate reads are NOT an atomic snapshot. A real event barrier and
 * authoritative private/Redis projections remain F02/F04/F05, not guessed SQL.
 * Invoke only at a runner-owned deterministic stable barrier. During Redis loss,
 * a failed admin read propagates; never return zero/empty success for an outage.
 * No poll is hidden here: settlement/recovery deadlines are still unpinned.
 */
export async function readJ12SupportingState(
  probe: Probe,
  admin: Admin,
  organizationId: string,
  requestIds: readonly string[],
) {
  const org = requireUuid(organizationId);
  const cohort = requireCohort(requestIds);
  const accounts = await readCreditAccount(probe, org);
  if (accounts.length !== 1) throw new Error('J12 requires exactly one funded fixture account');
  const gateway = await readGatewayCredits(admin, org);
  const requests = await readRequests(probe, org, cohort);
  const attempts = await readAttempts(probe, org, cohort);
  // Do not deduplicate: multiplicity is evidence. Do not equate empty logs with
  // zero spend or success; the caller must check its admitted exact inventory.
  if (requests.some(row => row.organization_id !== org || !cohort.includes(row.request_id)) ||
      attempts.some(row => !cohort.includes(row.request_id))) {
    throw new Error('J12 supporting read returned a foreign request cohort');
  }
  return {
    schema: 'j12-supporting-state/v1',
    authority: 'SUPPORTING_ONLY', atomic: false,
    organizationId: org, requestIds: cohort,
    account: accounts[0], gateway, requests, attempts,
  } as const;
}

/** Safe supporting attachment, not cases/<selection>/receipt.json and never
 * ledger-invariants.json. Readers omit bodies, headers, secrets and metadata.
 * The runner alone owns create-only output roots, originals, binding and cleanup.
 * Pair each attachment with the caller's exact row/barrier, not an inferred wait.
 */
export async function attachJ12SupportingState(
  info: TestInfo,
  state: Awaited<ReturnType<typeof readJ12SupportingState>>,
): Promise<void> {
  await attachSeededIds(info, 'J12', {
    organizationIds: [state.organizationId], requestIds: state.requestIds,
  });
  await info.attach('j12-supporting-state', {
    contentType: 'application/json', body: JSON.stringify(state),
  });
}
