import { isDeepStrictEqual } from 'node:util';
import { expect } from '@playwright/test';
import type { StateProbe } from '../../../lib/state-probe';
import { requireCohort, requireId, requireUuid } from '../_shared/identifiers';

type Probe = Pick<StateProbe, 'pg'>;

export interface GuardrailRequestFacts {
  id: string;
  request_id: string;
  status_code: number;
  is_stream: boolean;
  guardrail_triggered: boolean;
  action_present: boolean;
  blocked_action: boolean;
  guardrail_metadata_count: number;
  deleted: boolean;
}

/** Public schema: agentcc/models/request_log.py:AgentccRequestLog.
 * Emitter: agentcc-gateway/internal/guardrails/plugin.go and server/chat_stream.go
 * set metadata.guardrail_action='blocked'; log_ingestion.py retains metadata.
 * Never export free-form metadata, prompt/output, guardrail messages or results.
 * Read every row in the minted request cohort, including tombstones/duplicates.
 */
export function readGuardrailRequests(
  probe: Probe, organizationId: string, requestIds: readonly string[],
): Promise<GuardrailRequestFacts[]> {
  return probe.pg<GuardrailRequestFacts>(`
    SELECT id::text, request_id, status_code, is_stream, guardrail_triggered, deleted,
      metadata ? 'guardrail_action' AS action_present,
      COALESCE(metadata->>'guardrail_action' = 'blocked', false) AS blocked_action,
      (SELECT count(*)::int FROM jsonb_object_keys(metadata) AS k(key)
        WHERE left(k.key, 9) = 'guardrail') AS guardrail_metadata_count
    FROM agentcc_request_log
    WHERE organization_id = $1 AND request_id = ANY($2::text[])
    ORDER BY request_id, id`, [requireUuid(organizationId), requireCohort(requestIds)]);
}

/** Only the PG half of W18.A02. The stream's whole answer / zero text and UI
 * action still require the accepted local streaming fixture (J15-O10). Never
 * present matching log metadata as proof that blocked text did not escape.
 */
export function assertGuardrailLogPair(
  rows: readonly GuardrailRequestFacts[], passingRequest: string, blockedRequest: string,
): void {
  const ids = requireCohort([passingRequest, blockedRequest]).sort();
  expect(isDeepStrictEqual(rows.map(row => row.request_id).sort(), ids),
    'W18.A02 EXACT_REQUEST_COHORT').toBe(true);
  const passing = rows.find(row => row.request_id === passingRequest)!;
  const blocked = rows.find(row => row.request_id === blockedRequest)!;
  expect(passing.status_code === 200 && passing.is_stream === true &&
    passing.guardrail_triggered === false && passing.deleted === false,
    'W18.A02 PASSING_STREAM_LOG').toBe(true);
  expect(blocked.status_code === 403 && blocked.is_stream === true &&
    blocked.guardrail_triggered === true && blocked.action_present === true &&
    blocked.blocked_action === true && blocked.deleted === false,
    'W18.A02 BLOCKED_STREAM_LOG').toBe(true);
}

/** W18.A03 PG component: the null-enabled stored-only check must not execute.
 * The caller must independently prove which saved check/actual call produced the
 * minted cohort. This does not prove partner no-egress or the UI's Off state.
 */
export function assertNoGuardrailMetadata(
  rows: readonly GuardrailRequestFacts[], expectedRequests: readonly string[],
): void {
  const ids = requireCohort(expectedRequests).sort();
  expect(isDeepStrictEqual(rows.map(row => row.request_id).sort(), ids),
    'W18.A03 EXACT_STORED_ONLY_COHORT').toBe(true);
  expect(rows.every(row => row.guardrail_triggered === false && row.action_present === false &&
    row.guardrail_metadata_count === 0 && row.deleted === false),
    'W18.A03 NO_GUARDRAIL_METADATA').toBe(true);
}

export interface ExpectedPolicy {
  id: string;
  name: string;
  scope: 'global' | 'project' | 'key';
  mode: 'enforce' | 'monitor';
  checks: unknown[];
  is_active: boolean;
  priority: number;
  applied_keys: unknown[];
  applied_projects: unknown[];
  deleted: boolean;
}

/** agentcc/models/guardrail_policy.py:AgentccGuardrailPolicy. Exact full row set
 * across the minted organization, not selected expected IDs. Comparison stays
 * inside PG and returns only equality/count; no checks/config/ciphertext dumps.
 * expected is the independently pinned saved synthetic public representation,
 * not a guessed POST body. Config-push equality is a separate shared reader.
 */
export async function readGuardrailPolicyEquality(
  probe: Probe, organizationId: string, expected: readonly ExpectedPolicy[],
): Promise<{ row_count: number; matches: boolean }> {
  for (const row of expected) {
    requireUuid(row.id);
    requireId(row.name);
  }
  if (new Set(expected.map(row => row.id)).size !== expected.length) {
    throw new Error('Expected guardrail policy IDs must be distinct');
  }
  const ordered = [...expected].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const result = await probe.pg<{ row_count: number; matches: boolean }>(`
    SELECT count(*)::int AS row_count,
      COALESCE(jsonb_agg(jsonb_build_object(
        'id', id::text, 'name', name, 'scope', scope, 'mode', mode, 'checks', checks,
        'is_active', is_active, 'priority', priority, 'applied_keys', applied_keys,
        'applied_projects', applied_projects, 'deleted', deleted) ORDER BY id), '[]'::jsonb)
        = $2::jsonb AS matches
    FROM agentcc_guardrail_policy WHERE organization_id = $1`,
  [requireUuid(organizationId), JSON.stringify(ordered)]);
  if (result.length !== 1) throw new Error('Guardrail policy comparison returned no aggregate');
  return result[0];
}
