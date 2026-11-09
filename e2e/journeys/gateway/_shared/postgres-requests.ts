import type { StateProbe } from '../../../lib/state-probe';
import { requireCohort, requireUuid } from './identifiers';

type Probe = Pick<StateProbe, 'pg'>;
export interface RequestRecord {
  id: string; request_id: string; organization_id: string; workspace_id: string | null;
  model: string; provider: string; resolved_model: string; status_code: number;
  is_stream: boolean; is_error: boolean; fallback_used: boolean; guardrail_triggered: boolean;
  input_tokens: number; output_tokens: number; total_tokens: number;
  cost: string; cost_upstream: string | null; cost_markup: string | null; cost_landed: string | null;
  billing_mode: string | null; cost_source: string | null; usage_estimated: boolean | null;
  served_upstream_provider: string | null; catalog_snapshot_id: string | null;
  attempt_count: number; agent_run_id: string | null; budget_scope: string | null;
  deleted: boolean; created_at: string;
}

/** Source: futureagi/agentcc/models/request_log.py:AgentccRequestLog.
 * Authoritative gateway log storage on this candidate is Postgres, not an assumed
 * ClickHouse mirror. Keep decimal strings, all rows and tombstones; no body,
 * headers, key/hash, session identifier, metadata or free-form error_message.
 * A log is NOT the authoritative monetary ledger (FOUNDATION F05).
 */
export function readRequests(
  probe: Probe, organizationId: string, requestIds: readonly string[],
): Promise<RequestRecord[]> {
  return probe.pg<RequestRecord>(`SELECT id::text, request_id, organization_id::text,
    workspace_id::text, model, provider, resolved_model, status_code, is_stream, is_error,
    fallback_used, guardrail_triggered, input_tokens, output_tokens, total_tokens,
    cost::text, cost_upstream::text, cost_markup::text, cost_landed::text,
    billing_mode, cost_source, usage_estimated, served_upstream_provider, catalog_snapshot_id,
    attempt_count, agent_run_id, budget_scope, deleted, created_at::text
    FROM agentcc_request_log WHERE organization_id = $1 AND request_id = ANY($2::text[])
    ORDER BY request_id, created_at, id`, [requireUuid(organizationId), requireCohort(requestIds)]);
}

export interface AttemptRecord {
  attempt_id: string; request_id: string; index: number; kind: string; outcome: string;
  provider: string; model: string; status_code: number | null;
  error_class: string; error_origin: string;
  prompt_tokens: number | null; completion_tokens: number | null;
  upstream_cost_micros: string | null; billed_cost_micros: string | null;
  landed_cost_micros: string | null; cost_source: string; billable: boolean;
  usage_estimated: boolean;
}

/** Source: futureagi/agentcc/models/request_attempt.py:AgentccRequestAttempt.
 * ORDER BY index is intentional: fallback/retry proof requires attempt order.
 * This model does NOT inherit BaseModel and has no deleted column.
 */
export function readAttempts(
  probe: Probe, organizationId: string, requestIds: readonly string[],
): Promise<AttemptRecord[]> {
  return probe.pg<AttemptRecord>(`SELECT attempt_id, request_id, "index", kind, outcome,
    provider, model, status_code, error_class, error_origin, prompt_tokens, completion_tokens,
    upstream_cost_micros::text, billed_cost_micros::text, landed_cost_micros::text,
    cost_source, billable, usage_estimated
    FROM agentcc_request_attempt WHERE organization_id = $1 AND request_id = ANY($2::text[])
    ORDER BY request_id, "index", attempt_id`, [requireUuid(organizationId), requireCohort(requestIds)]);
}

export interface ToolCallRecord {
  event_id: string; tool_call_id: string; request_id: string;
  server_id: string; virtual_server_id: string; tool: string; via: string;
  user_id: string; agent_run_id: string; outcome: string; error_code: string;
  approval_id: string; dispatched: boolean; billing_mode: string;
  upstream_cost_micros: string; billed_cost_micros: string; markup_micros: string;
  content_mode: string;
}

/** Source: futureagi/agentcc/models/mcp_tool_call.py:AgentccToolCall.
 * Query by request IDs, NOT only expected event IDs: duplicate/unexpected events
 * for one request must remain observable to exactly-once/exact-set assertions.
 * No arguments/result/evidence/session_hash or free-form error text is fetched.
 */
export function readToolCalls(
  probe: Probe, organizationId: string, requestIds: readonly string[],
): Promise<ToolCallRecord[]> {
  return probe.pg<ToolCallRecord>(`SELECT event_id, tool_call_id, request_id, server_id,
    virtual_server_id, tool, via, user_id, agent_run_id, outcome, error_code, approval_id,
    dispatched, billing_mode, upstream_cost_micros::text, billed_cost_micros::text,
    markup_micros::text, content_mode FROM agentcc_tool_call
    WHERE organization_id = $1 AND request_id = ANY($2::text[])
    ORDER BY request_id, event_id`, [requireUuid(organizationId), requireCohort(requestIds)]);
}
