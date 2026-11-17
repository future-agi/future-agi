import type { StateProbe } from '../../../../lib/state-probe';
import type { ApprovalRecord } from '../../_shared/postgres-mcp';
import { requireId, requireUuid } from '../../_shared/identifiers';

/** All columns/statuses at da4c81cc623f909ce38f53e01b557ee8374d5adb:
 * futureagi/agentcc/models/mcp_approval.py:AgentccMCPApproval and
 * migrations/0038_mcp_approval.py. This is audit storage, NOT live enforcement.
 * At that pin urls_mcp_approvals.py urlpatterns=[], gateway
 * internal/server/routes_mcp_approvals.go registers nothing, and
 * internal/mcp/hook_{approval,ratelimit}.go return nil. ApprovalsTab.jsx is an
 * empty state, hidden by shell/page-status.js. There is NO decision/list/detail
 * serializer or generated wire envelope to pin. Do not guess DRF pagination,
 * status codes, approval_id/approve_url response or a privileged expiry route.
 */
export const APPROVAL_STATUSES = ['pending', 'approved', 'denied', 'expired', 'used'] as const;
export const APPROVAL_WIRE_AVAILABILITY = Object.freeze({ sourceRef: 'da4c81cc623f909ce38f53e01b557ee8374d5adb',
  state: 'ABSENT_AT_PREP_PIN', decision: false, list: false, detail: false,
  expiry: false, oneUse: false, argsBinding: false, rateLimit: false });

export function requireApprovalWireContract(_operation: string): never {
  throw new Error('OWED_F11_PRODUCT_CONTRACT: pinned approval/limit routes and enforcement are absent');
}

type Probe = Pick<StateProbe, 'pg'>;
const PROJECTION = `approval_id, virtual_server_id, tool, args_hash, agent_run_id,
  api_key_id, status, decided_by_id::text, requested_at::text, decided_at::text, expires_at::text`;
function validate(row: ApprovalRecord, run: string): ApprovalRecord {
  const keys = ['approval_id', 'virtual_server_id', 'tool', 'args_hash', 'agent_run_id', 'api_key_id',
    'status', 'decided_by_id', 'requested_at', 'decided_at', 'expires_at'];
  if (!row || Object.keys(row).sort().join(',') !== keys.sort().join(',') ||
      row.agent_run_id !== run || !APPROVAL_STATUSES.includes(row.status as typeof APPROVAL_STATUSES[number])) {
    throw new Error('Invalid approval audit projection');
  }
  for (const key of ['approval_id', 'virtual_server_id', 'tool', 'args_hash', 'agent_run_id', 'api_key_id'] as const) {
    if (typeof row[key] !== 'string' || row[key].length > 255) throw new Error('Invalid approval audit field');
  }
  requireId(row.approval_id);
  if (row.approval_id.length > 64) throw new Error('Invalid approval ID length');
  if (row.decided_by_id !== null) requireUuid(row.decided_by_id);
  for (const key of ['requested_at', 'decided_at', 'expires_at'] as const) {
    if (key !== 'requested_at' && row[key] === null) continue;
    if (typeof row[key] !== 'string' || !Number.isFinite(Date.parse(row[key] as string))) {
      throw new Error('Invalid approval audit timestamp');
    }
  }
  return row;
}

/** Full run-scoped audit census by keyset, INCLUDING unexpected rows.
 * A single readMcpApprovals query remains preferable for a small cohort. Use this
 * bounded reader for large/forced-small-page audit census while the run is
 * quiescent; concurrent product writes require polling the WHOLE census again.
 * This explicitly does NOT prove product list/detail UI or API pagination.
 */
export async function readApprovalAuditPages(probe: Probe, organizationId: string, runId: string,
  options: { pageSize: number; maxPages: number },
): Promise<ApprovalRecord[]> {
  requireUuid(organizationId); requireId(runId);
  if (!Number.isSafeInteger(options.pageSize) || options.pageSize < 1 || options.pageSize > 200 ||
      !Number.isSafeInteger(options.maxPages) || options.maxPages < 1 || options.maxPages > 100) {
    throw new Error('Invalid approval audit page budget');
  }
  const result: ApprovalRecord[] = [];
  let after: string | null = null;
  for (let page = 0; page < options.maxPages; page++) {
    const rows = await probe.pg<ApprovalRecord>(`SELECT ${PROJECTION} FROM agentcc_mcp_approval
      WHERE organization_id = $1 AND agent_run_id = $2
      AND ($3::text IS NULL OR approval_id COLLATE "C" > $3::text COLLATE "C")
      ORDER BY approval_id COLLATE "C" LIMIT $4`, [organizationId, runId, after, options.pageSize]);
    if (!Array.isArray(rows) || rows.length > options.pageSize) throw new Error('Invalid approval audit page');
    for (const row of rows) {
      validate(row, runId);
      if (after !== null && row.approval_id <= after) throw new Error('Approval audit page repeated or regressed');
      result.push(row); after = row.approval_id;
    }
    if (rows.length < options.pageSize) return result;
  }
  // Never quietly return the truncated prefix, even when all expected IDs fit.
  throw new Error('Approval audit page budget exhausted');
}

/** Model-backed read-only detail, tenant AND run bounded; NOT a product endpoint.
 * Null is actual row absence, not an HTTP 404 or an authorization verdict.
 */
export async function readApprovalAuditDetail(probe: Probe, organizationId: string, runId: string,
  approvalId: string,
): Promise<ApprovalRecord | null> {
  const rows = await probe.pg<ApprovalRecord>(`SELECT ${PROJECTION} FROM agentcc_mcp_approval
    WHERE organization_id = $1 AND agent_run_id = $2 AND approval_id = $3`,
  [requireUuid(organizationId), requireId(runId), requireId(approvalId)]);
  if (rows.length > 1) throw new Error('Duplicate approval audit identity');
  return rows.length ? validate(rows[0], runId) : null;
}
