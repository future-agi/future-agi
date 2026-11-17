import { expect } from '@playwright/test';
import type { ApprovalRecord, CredentialRecord } from '../_shared/postgres-mcp';
import type { ToolCallRecord } from '../_shared/postgres-requests';

/** Partial state assertions, NOT complete J14 rows or a fixture implementation.
 * Read with _shared/readMcpCredentials, readMcpApprovals and readToolCalls.
 * The caller must supply the entire minted server/run/request cohort, including
 * unexpected rows. Never filter actuals down to expected IDs before comparing.
 * Missing native identity/OAuth/Redis/cleanup hooks remain owed in AUTHOR.json.
 * Every comparison below projects only the foundation's non-secret fields.
 */
export interface CredentialIdentity {
  credentialId: string;
  serverId: string;
  creatorId: string;
}
export interface CredentialLifecycle {
  created: readonly CredentialRecord[];
  rotated: readonly CredentialRecord[];
  revoked: readonly CredentialRecord[];
}

const credentialMetadata = (row: CredentialRecord) => ({
  id: row.id, server_id: row.server_id, scope: row.scope, user_id: row.user_id,
  kind: row.kind, status: row.status, version: row.version,
  created_by_id: row.created_by_id, rotated_at: row.rotated_at,
  token_expires_at: row.token_expires_at,
});

/** W07.1 metadata subset only. Provenance at foundation head 7543e2d995fe:
 * futureagi/agentcc/models/mcp_credential.py:AgentccMCPCredential (v1/shared);
 * services/mcp_credentials.py:rotate/rotate_in_place (same id, version +1),
 * mark_lapsed (changes status only). No ciphertext, cleanup or masking claim.
 * Snapshots must be taken after EACH real operation; a final-state read cannot
 * manufacture earlier snapshots. This function performs no writes or waits.
 */
export function assertSharedBearerLifecycle(
  snapshots: CredentialLifecycle, identity: CredentialIdentity,
): void {
  const common = {
    id: identity.credentialId, server_id: identity.serverId, scope: 'shared',
    user_id: null, kind: 'bearer', created_by_id: identity.creatorId,
    token_expires_at: null,
  };
  expect(snapshots.created.map(credentialMetadata), 'W07.1 create exact cohort').toEqual([
    { ...common, status: 'active', version: 1, rotated_at: null },
  ]);
  expect(snapshots.rotated, 'W07.1 rotate exact cardinality').toHaveLength(1);
  const rotatedAt = snapshots.rotated[0].rotated_at;
  expect(typeof rotatedAt, 'W07.1 rotated_at is present').toBe('string');
  expect(Number.isFinite(Date.parse(rotatedAt as string)), 'W07.1 rotated_at is a timestamp').toBe(true);
  expect(snapshots.rotated.map(credentialMetadata), 'W07.1 rotate exact cohort').toEqual([
    { ...common, status: 'active', version: 2, rotated_at: rotatedAt },
  ]);
  expect(snapshots.revoked.map(credentialMetadata), 'W07.1 revoke exact cohort').toEqual([
    { ...common, status: 'revoked', version: 2, rotated_at: rotatedAt },
  ]);
}

const approvalMetadata = (row: ApprovalRecord) => ({
  approval_id: row.approval_id, virtual_server_id: row.virtual_server_id,
  tool: row.tool, args_hash: row.args_hash, agent_run_id: row.agent_run_id,
  api_key_id: row.api_key_id, status: row.status, decided_by_id: row.decided_by_id,
  requested_at: row.requested_at, decided_at: row.decided_at, expires_at: row.expires_at,
});

/** W11.1 audit subset only. Provenance: models/mcp_approval.py and the shared
 * readMcpApprovals query. expected must be independently derived from the
 * minted requests/authorized decisions, NOT copied from the actual audit read.
 * args_hash is an argument digest, not a credential hash. Live one-use/expiry,
 * args canonicalization and Redis enforcement are NOT implied by this audit.
 */
export function assertApprovalAuditExact(
  actual: readonly ApprovalRecord[], expected: readonly ApprovalRecord[],
): void {
  const order = (a: ApprovalRecord, b: ApprovalRecord) => a.approval_id.localeCompare(b.approval_id);
  expect([...actual].sort(order).map(approvalMetadata), 'W11.1 exact run-scoped approval audit')
    .toEqual([...expected].sort(order).map(approvalMetadata));
}

const callMetadata = (row: ToolCallRecord) => ({
  event_id: row.event_id, tool_call_id: row.tool_call_id, request_id: row.request_id,
  server_id: row.server_id, virtual_server_id: row.virtual_server_id, tool: row.tool,
  via: row.via, user_id: row.user_id, agent_run_id: row.agent_run_id,
  outcome: row.outcome, error_code: row.error_code, approval_id: row.approval_id,
  dispatched: row.dispatched, billing_mode: row.billing_mode,
  upstream_cost_micros: row.upstream_cost_micros, billed_cost_micros: row.billed_cost_micros,
  markup_micros: row.markup_micros, content_mode: row.content_mode,
});

/** W07.2/W09.1/W11.2 log subset only. Provenance: models/mcp_tool_call.py and
 * _shared/postgres-requests.ts:readToolCalls. All rows for the request cohort
 * must be passed, including refusals, duplicate/wrong-via rows and tombstones
 * where applicable. No Set/deduplication: cardinality is part of equality.
 * via/error/status expectations must come from an admitted wire/emitter pin;
 * this helper deliberately invents none. A log is not an upstream counter,
 * principal/key ownership proof, Redis state or the authoritative money ledger.
 */
export function assertToolCallCohortExact(
  actual: readonly ToolCallRecord[], expected: readonly ToolCallRecord[],
): void {
  const order = (a: ToolCallRecord, b: ToolCallRecord) =>
    a.request_id.localeCompare(b.request_id) || a.event_id.localeCompare(b.event_id);
  expect([...actual].sort(order).map(callMetadata), 'J14 exact request-cohort tool-call rows')
    .toEqual([...expected].sort(order).map(callMetadata));
}

/** A refusal's no-write audit compares a complete before/after snapshot,
 * including IDs, versions, decisions and timestamps. It is NOT a role/tenant
 * refusal test without the separately pinned HTTP status and upstream witness.
 */
export function assertCredentialMetadataUnchanged(
  before: readonly CredentialRecord[], after: readonly CredentialRecord[],
): void {
  const order = (a: CredentialRecord, b: CredentialRecord) => a.id.localeCompare(b.id);
  expect([...after].sort(order).map(credentialMetadata), 'W07.1 forbidden write leaves metadata unchanged')
    .toEqual([...before].sort(order).map(credentialMetadata));
}
