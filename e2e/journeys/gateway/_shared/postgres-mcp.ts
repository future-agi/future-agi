import type { StateProbe } from '../../../lib/state-probe';
import { requireCohort, requireId, requireUuid } from './identifiers';

type Probe = Pick<StateProbe, 'pg'>;
export interface CredentialRecord {
  id: string; server_id: string; scope: string; user_id: string | null;
  kind: string; status: string; version: number; created_by_id: string | null;
  rotated_at: string | null; token_expires_at: string | null;
}

/** Source: futureagi/agentcc/models/mcp_credential.py:AgentccMCPCredential.
 * Select the full synthetic server cohort, including revoked versions. Never
 * read encrypted_secret/encrypted_refresh, even to make a prefix-only assertion.
 * Encryption and cleanup/grant-epoch witnesses need F10's qualified fixture hook;
 * version here is a row version, NOT an invented Redis credential epoch.
 */
export function readMcpCredentials(
  probe: Probe, organizationId: string, serverIds: readonly string[],
): Promise<CredentialRecord[]> {
  return probe.pg<CredentialRecord>(`SELECT id::text, server_id, scope, user_id::text,
    kind, status, version, created_by_id::text, rotated_at::text, token_expires_at::text
    FROM agentcc_mcp_credential WHERE organization_id = $1 AND server_id = ANY($2::text[])
    ORDER BY server_id, created_at, id`, [requireUuid(organizationId), requireCohort(serverIds)]);
}

export interface ApprovalRecord {
  approval_id: string; virtual_server_id: string; tool: string; args_hash: string;
  agent_run_id: string; api_key_id: string; status: string; decided_by_id: string | null;
  requested_at: string; decided_at: string | null; expires_at: string | null;
}

/** Source: futureagi/agentcc/models/mcp_approval.py:AgentccMCPApproval.
 * args_hash is the contract's argument digest, never a credential hash.
 * Run-scoped reads expose extra pending rows from changed args, not just known IDs.
 */
export function readMcpApprovals(
  probe: Probe, organizationId: string, runId: string,
): Promise<ApprovalRecord[]> {
  return probe.pg<ApprovalRecord>(`SELECT approval_id, virtual_server_id, tool, args_hash,
    agent_run_id, api_key_id, status, decided_by_id::text, requested_at::text,
    decided_at::text, expires_at::text FROM agentcc_mcp_approval
    WHERE organization_id = $1 AND agent_run_id = $2 ORDER BY requested_at, approval_id`,
  [requireUuid(organizationId), requireId(runId)]);
}

export interface ConsentRecord {
  id: string; user_id: string; client_id: string; granted_at: string;
  revoked_at: string | null; resource_matches: boolean;
}

/** Source: futureagi/agentcc/models/mcp_oauth.py:AgentccMCPOAuthConsent.
 * client_id here is the Django FK record ID, NOT OAuth's client_id string.
 * expectedResource is pinned by the spec to the local virtual-server URI.
 * All this user's consent rows remain visible; no scopes/resource URL is emitted.
 */
export function readMcpConsents(
  probe: Probe, organizationId: string, userId: string, expectedResource: string,
): Promise<ConsentRecord[]> {
  return probe.pg<ConsentRecord>(`SELECT id::text, user_id::text, client_id::text,
    granted_at::text, revoked_at::text, resource = $3 AS resource_matches
    FROM agentcc_mcp_oauth_consent WHERE organization_id = $1 AND user_id = $2
    ORDER BY granted_at, id`, [requireUuid(organizationId), requireUuid(userId), expectedResource]);
}

export interface RefreshRecord {
  id: string; user_id: string; client_id: string; rotated_from_id: string | null;
  expires_at: string; revoked_at: string | null; created_at: string;
}

/** Same public model file: AgentccMCPOAuthRefreshToken. No token_hash is read. */
export function readMcpRefreshLineage(
  probe: Probe, organizationId: string, userId: string,
): Promise<RefreshRecord[]> {
  return probe.pg<RefreshRecord>(`SELECT id::text, user_id::text, client_id::text,
    rotated_from_id::text, expires_at::text, revoked_at::text, created_at::text
    FROM agentcc_mcp_oauth_refresh_token WHERE organization_id = $1 AND user_id = $2
    ORDER BY created_at, id`, [requireUuid(organizationId), requireUuid(userId)]);
}
