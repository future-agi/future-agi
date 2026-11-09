import type { StateProbe } from '../../../lib/state-probe';
import { requireUuid } from './identifiers';

type Probe = Pick<StateProbe, 'pg'>;
export interface ConfigVersion {
  id: string; organization_id: string; workspace_id: string | null;
  version: number; is_active: boolean; deleted: boolean;
  created_at: string; updated_at: string;
}

/** Source: futureagi/agentcc/models/org_config.py:AgentccOrgConfig;
 * timestamps/deleted: futureagi/tfc/utils/base_model.py:BaseModel.
 * Include tombstones/inactive versions so no-write and rollback checks see them.
 * Never return config JSON: mcp/providers/etc can contain credential material.
 */
export function readConfigVersions(probe: Probe, organizationId: string): Promise<ConfigVersion[]> {
  return probe.pg<ConfigVersion>(`
    SELECT id::text, organization_id::text, workspace_id::text, version, is_active,
           deleted, created_at::text, updated_at::text
    FROM agentcc_org_config WHERE organization_id = $1 ORDER BY version, id`,
  [requireUuid(organizationId)]);
}

// The SQL column identifier is selected only from this source-pinned closed set.
export const CONFIG_SECTIONS = [
  'guardrails', 'routing', 'cache', 'rate_limiting', 'budgets', 'cost_tracking',
  'ip_acl', 'alerting', 'privacy', 'tool_policy', 'mcp', 'a2a', 'audit',
  'model_database', 'model_map', 'managed_models', 'tools', 'identity',
] as const;
export type ConfigSection = (typeof CONFIG_SECTIONS)[number];

export function requireConfigSection(section: string): asserts section is ConfigSection {
  if (!(CONFIG_SECTIONS as readonly string[]).includes(section)) throw new Error('Unknown config section');
}

/** Exact JSONB equality, evaluated in PG. No shallow/subset match or secret dump.
 * expected is the synthetic section authored from its candidate serializer;
 * it is not the gateway-transformed section (config_push.py transforms it).
 * Zero or multiple active rows remain visible to the caller, never .first().
 */
export function readActiveConfigEquality(
  probe: Probe, organizationId: string, section: ConfigSection, expected: unknown,
): Promise<{ id: string; version: number; matches: boolean }[]> {
  requireConfigSection(section);
  if (expected === undefined) throw new Error('Expected config section must be explicit');
  return probe.pg(`SELECT id::text, version, ${section} = $2::jsonb AS matches
    FROM agentcc_org_config
    WHERE organization_id = $1 AND is_active = true AND deleted = false ORDER BY version, id`,
  [requireUuid(organizationId), JSON.stringify(expected)]);
}

export interface KeyRecord {
  id: string; organization_id: string; workspace_id: string | null; user_id: string | null;
  gateway_key_id: string; status: string; deleted: boolean;
  created_at: string; updated_at: string; expires_at: string | null;
}

/** Source: futureagi/agentcc/models/api_key.py:AgentccAPIKey.
 * All key records in the freshly minted org, including tombstones. Deliberately
 * omit prefix/hash/name/owner/metadata and every secret. user_id=NULL is observable,
 * not silently filtered out: detecting ownerless keys is a required assertion.
 */
export function readKeyRecords(probe: Probe, organizationId: string): Promise<KeyRecord[]> {
  return probe.pg<KeyRecord>(`SELECT id::text, organization_id::text, workspace_id::text,
    user_id::text, gateway_key_id, status, deleted, created_at::text, updated_at::text,
    expires_at::text FROM agentcc_api_key WHERE organization_id = $1 ORDER BY created_at, id`,
  [requireUuid(organizationId)]);
}

export interface CreditAccount {
  granted_total_micros: string; settled_spend_micros: string;
  version: string; gateway_acked_version: string;
}

/** Table and org column: e2e/flows/gateway/managed-optin-credits.spec.ts storage
 * assertion. Fields: futureagi/agentcc/management/commands/agentcc_managed_credits.py
 * _show/_report. PG int64s are text; do not round through Number.
 * This is an ACCOUNT read, not a request ledger or Redis hold/settlement proof.
 * Absent EE schema throws. No catch returning zero, no private source dependency.
 */
export function readCreditAccount(probe: Probe, organizationId: string): Promise<CreditAccount[]> {
  return probe.pg<CreditAccount>(`SELECT granted_total_micros::text, settled_spend_micros::text,
    version::text, gateway_acked_version::text FROM usage_managedcreditaccount
    WHERE organization_id = $1`, [requireUuid(organizationId)]);
}

export interface AuditRecord {
  id: string; actor_id: string | null; action: string; scope: string;
  target_id: string; created_at: string; deleted: boolean; changes_match: boolean;
}

/** Source: futureagi/accounts/models/audit_log.py:AuditLog. Match exact changes
 * in the database rather than emitting metadata (which may hold invite tokens).
 * The spec must pin action/changes from the actual emitter, not this table model.
 */
export function readAuditChangeEquality(
  probe: Probe, organizationId: string, targetId: string, action: string, expectedChanges: unknown,
): Promise<AuditRecord[]> {
  if (!/^[a-zA-Z0-9_.-]{1,64}$/.test(action) || expectedChanges === undefined) {
    throw new Error('Audit action and explicit expected changes required');
  }
  return probe.pg<AuditRecord>(`SELECT id::text, actor_id::text, action, scope, target_id::text,
    created_at::text, deleted, changes = $4::jsonb AS changes_match
    FROM accounts_audit_log WHERE organization_id = $1 AND target_id = $2 AND action = $3
    ORDER BY created_at, id`,
  [requireUuid(organizationId), requireUuid(targetId), action, JSON.stringify(expectedChanges)]);
}
