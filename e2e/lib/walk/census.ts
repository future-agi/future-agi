// H13: the backend half of the W00 default-off census (flows/walks/
// default-off-census.spec.ts). Every read is read-only and scoped to an
// organization the census owns.
//
// Three verdicts per feature (lib/walk/receipt.ts): OFF (the feature is there and
// off: pass), ON (fails the census), ABSENT (this head has no such table, column,
// route or config key: recorded, not a failure). A head that lacks a launch
// feature reads ABSENT for it, so one spec runs on every line (INT-2, the MCP
// line, the converged head) and says which features each one has.
//
// Tables and columns are the verified models (futureagi/agentcc/models/,
// ee/usage/models/managed_credits.py, integrations/models/integration_connection.py,
// accounts/models/audit_log.py). The cap activation table is looked up by name
// (AgentccCapActivation, G8-B; not on INT-2).
import type { GatewayAdmin } from '../gateway-admin';
import type { RedisProbe } from '../redis-probe';
import type { StateProbe } from '../state-probe';
import type { Verdict, VerdictRow } from './receipt';

/** Feature tables a fresh organization has no row in until someone turns the feature on. */
export const ZERO_ROW_TABLES: readonly { table: string; feature: string }[] = [
  { table: 'usage_managedcreditaccount', feature: 'managed_models (credit account: opt-in creates it, GW-E2E-007)' },
  { table: 'usage_managedcreditentry', feature: 'managed credits ledger' },
  { table: 'agentcc_mcp_credential', feature: 'mcp credentials' },
  { table: 'agentcc_mcp_connect_state', feature: 'mcp oauth connect' },
  { table: 'agentcc_mcp_approval', feature: 'mcp approvals' },
  { table: 'agentcc_tool_call', feature: 'mcp tool calls' },
  { table: 'integrations_connection', feature: 'integrations exports' },
];
const CAP_ACTIVATION = '%capactivation%';
export const TRACE_CONTENT_AUDIT_ACTION = 'agentcc.trace_content_changed';

/** Whether `table` exists with an organization_id column; null when the table is absent. */
async function orgColumn(probe: StateProbe, table: string): Promise<boolean | null> {
  const rows = await probe.pg<{ has_org: boolean }>(
    `SELECT bool_or(column_name = 'organization_id') AS has_org FROM information_schema.columns
       WHERE table_schema = 'public' AND table_name = $1`, [table]);
  return rows[0]?.has_org ?? null;
}

async function capActivationTables(probe: StateProbe): Promise<string[]> {
  return (await probe.pg<{ t: string }>(`SELECT table_name AS t FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name LIKE $1 ORDER BY 1`, [CAP_ACTIVATION])).map((r) => r.t);
}

/** Every zero-row table's count for `org`: OFF at zero, ON above, ABSENT without the table. */
export async function zeroRowVerdicts(probe: StateProbe, org: string): Promise<VerdictRow[]> {
  const out: VerdictRow[] = [];
  const tables = [...ZERO_ROW_TABLES, ...(await capActivationTables(probe)).map((table) =>
    ({ table, feature: 'admission caps (AgentccCapActivation)' }))];
  if (!tables.some((t) => t.table.includes('capactivation'))) {
    out.push({ feature: 'admission caps (AgentccCapActivation)', lane: 'storage', where: CAP_ACTIVATION, verdict: 'ABSENT' });
  }
  for (const { table, feature } of tables) {
    const hasOrg = await orgColumn(probe, table);
    if (hasOrg === null) { out.push({ feature, lane: 'storage', where: table, verdict: 'ABSENT' }); continue; }
    if (!hasOrg) throw new Error(`census: ${table} has no organization_id column; read its model before scoping it`);
    // The name comes from the constant list or information_schema, never from input.
    const [{ n }] = await probe.pg<{ n: string }>(`SELECT count(*)::text AS n FROM "${table}" WHERE organization_id = $1`, [org]);
    out.push({ feature, lane: 'storage', where: table, verdict: Number(n) === 0 ? 'OFF' : 'ON', detail: { rows: Number(n) } });
  }
  const audit = await orgColumn(probe, 'accounts_audit_log');
  if (audit === null) {
    out.push({ feature: 'trace content consent audit', lane: 'storage', where: 'accounts_audit_log', verdict: 'ABSENT' });
  } else {
    const [{ n }] = await probe.pg<{ n: string }>(`SELECT count(*)::text AS n FROM accounts_audit_log
        WHERE organization_id = $1 AND action = $2`, [org, TRACE_CONTENT_AUDIT_ACTION]);
    out.push({ feature: 'trace content consent audit', lane: 'storage', where: `accounts_audit_log ${TRACE_CONTENT_AUDIT_ACTION}`,
      verdict: Number(n) === 0 ? 'OFF' : 'ON', detail: { rows: Number(n) } });
  }
  return out;
}

export interface OrgConfigRow { id: string; version: number; is_active: boolean; managed_models: any; budgets: any;
  privacy: any; mcp: any }

/** The org's live (not deleted) agentcc_org_config versions, newest first. */
export async function orgConfigRows(probe: StateProbe, org: string): Promise<OrgConfigRow[]> {
  return probe.pg<OrgConfigRow>(`SELECT id::text, version, is_active, managed_models, budgets, privacy, mcp
      FROM agentcc_org_config WHERE organization_id = $1 AND deleted = false ORDER BY version DESC`, [org]);
}

const off = (value: unknown) => value === undefined || value === null || value === false;

/** The four org-config switches W00 names, from one config object (PG row or the gateway's copy). */
export function configSwitchVerdicts(cfg: { managed_models?: any; budgets?: any; privacy?: any; mcp?: any } | null,
  lane: 'storage' | 'gateway', where: string): VerdictRow[] {
  const read = (feature: string, value: unknown, absentOk: boolean): VerdictRow => ({ feature, lane, where,
    verdict: (absentOk ? off(value) : value === undefined) ? 'OFF' : 'ON', detail: { value: value ?? null } });
  return [
    read('managed_models.enabled', cfg?.managed_models?.enabled, true),
    read('budgets.admission_caps_enabled', cfg?.budgets?.admission_caps_enabled, true),
    read('privacy.trace_content', cfg?.privacy?.trace_content, true),
    // mcp.search must be absent, not merely false (hybrid search, G17).
    read('mcp.search', cfg?.mcp?.search, false),
    read('mcp.enabled', cfg?.mcp?.enabled, true),
    ...(['servers', 'virtual_servers'] as const).map(key => {
      const value = cfg?.mcp?.[key];
      const empty = value == null || (typeof value === 'object' && Object.keys(value).length === 0);
      return { feature: `mcp.${key}`, lane, where, verdict: empty ? 'OFF' as const : 'ON' as const,
        detail: { entries: value && typeof value === 'object' ? Object.keys(value).length : null } };
    }),
  ];
}

/** The gateway node's view: GET /-/config public_price_feed (G8-A) against the declaration. */
export function priceFeedVerdict(nodeConfig: Record<string, any>, declaredOn: boolean): VerdictRow {
  const feed = nodeConfig.public_price_feed;
  if (feed === undefined) return { feature: 'public_price_feed', lane: 'gateway', where: 'GET /-/config', verdict: 'ABSENT' };
  const on = feed?.enabled === true;
  // On a profile that declares the feed on, ON is the operator's opt-in, read as expected.
  const verdict: Verdict = on === declaredOn ? (on ? 'RECORDED' : 'OFF') : 'ON';
  return { feature: 'public_price_feed', lane: 'gateway', where: 'GET /-/config', verdict, detail: { enabled: on, declaredOn } };
}

/** Everything of `org` the census can read, for a byte comparison at start and end. */
export async function orgSnapshot(probe: StateProbe, admin: GatewayAdmin, redis: RedisProbe | null, org: string) {
  const tables: Record<string, string[] | 'absent'> = {};
  for (const table of ['agentcc_org_config', 'agentcc_api_key', ...ZERO_ROW_TABLES.map((t) => t.table),
    ...(await capActivationTables(probe))]) {
    const hasOrg = await orgColumn(probe, table);
    tables[table] = hasOrg ? (await probe.pg<{ j: string }>(
      `SELECT row_to_json(x)::text AS j FROM "${table}" x WHERE organization_id = $1 ORDER BY 1`, [org])).map((r) => r.j)
      : 'absent';
  }
  const audit = (await probe.pg<{ j: string }>(`SELECT row_to_json(x)::text AS j FROM accounts_audit_log x
      WHERE organization_id = $1 AND action LIKE 'agentcc.%' ORDER BY 1`, [org])).map((r) => r.j);
  let redisState: unknown;
  try { redisState = redis ? redis.snapshot(org) : 'not probed'; }
  catch (error) { redisState = { error: (error as Error).message.slice(0, 200) }; }
  return { tables, audit, gatewayConfig: await admin.orgConfig(org), redis: redisState };
}
