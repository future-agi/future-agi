import type { StateProbe } from '../../../../lib/state-probe';
import { requireUuid } from '../../_shared/identifiers';

export const PREP_SOURCE = 'da4c81cc623f909ce38f53e01b557ee8374d5adb';
/** Source inspection, NOT a successful runtime schema read. Public agentcc
 * models/migrations/contracts contain neither model at PREP_SOURCE, and
 * futureagi/agentcc/urls_budgets.py has urlpatterns = []. Do NOT infer tables
 * from these class names, read private EE source, or turn config version into
 * a policy revision. Activation/revision/effective-UTC/overrun readers remain
 * F06 until a reviewed model/migration/capture is supplied at the admitted pin.
 */
export const CAP_SCHEMA_GAP = Object.freeze({
  status: 'OWED_SCHEMA_ABSENT_AT_PREP_PIN', sourceRef: PREP_SOURCE,
  models: ['AgentccCapActivation', 'AgentccCapPolicyRevision'],
  unavailable: ['activation and revision table/columns', 'effective-budget UTC response',
    'waived-overrun anomaly schema', 'same-key scope GET/close contract'],
  substituteAllowed: false,
});
export interface BudgetConfigObservation {
  id: string; organization_id: string; config_version: number; is_active: boolean;
  deleted: boolean; created_at: string; updated_at: string; budgets_match: boolean;
}
const TOP = new Set(['enabled', 'default_period', 'warn_threshold', 'org_limit', 'org_period',
  'hard_limit', 'action', 'action_mode', 'on_exceed', 'throttle_rpm', 'organization', 'org',
  'teams', 'users', 'keys', 'tags', 'fail_mode', 'no_max_tokens']);
const LEVEL = new Set(['limit', 'period', 'hard', 'action', 'action_mode', 'on_exceed',
  'per_model', 'include_byok', 'alert_thresholds', 'throttle_rpm']);
function object(value: unknown): asserts value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new Error('EXPECTED_BUDGET_OBJECT');
  }
}
function keys(value: Record<string, unknown>, allowed: Set<string>) {
  for (const name of Object.keys(value)) if (!allowed.has(name)) throw new Error('UNKNOWN_BUDGET_FIELD');
}
function level(value: unknown) {
  object(value); keys(value, LEVEL);
  for (const [name, field] of Object.entries(value)) {
    if (name === 'per_model') {
      object(field);
      if (Object.values(field).some(n => typeof n !== 'number' || !Number.isFinite(n))) throw new Error('INVALID_MODEL_LIMIT');
    } else if (name === 'alert_thresholds') {
      if (!Array.isArray(field) || field.some(n => typeof n !== 'number' || !Number.isFinite(n))) throw new Error('INVALID_THRESHOLDS');
    } else if (field === undefined || (typeof field === 'object' && field !== null) ||
        (typeof field === 'number' && !Number.isFinite(field))) throw new Error('INVALID_BUDGET_SCALAR');
  }
}
/** Exact equality reader for the EXISTING NON-max_charge budget JSON only.
 * Table: models/org_config.py AgentccOrgConfig.Meta.db_table; migration
 * 0024_rename_prism_to_agentcc.py. Fields: models/org_config.py and
 * tfc/utils/base_model.py; JSON keys: contracts/gateway_admin.py BudgetsConfig /
 * BudgetLevelConfig and serializers/org_config.py validate_budgets, all at
 * PREP_SOURCE under futureagi/. Only canonical snake_case fixture expectations
 * are accepted. No mutations, normalization, absent defaults or raw config dump.
 * Returns all versions/tombstones so a write cannot hide behind active-row reads.
 * This is explicitly NOT F06 cap-policy-revision or gateway-effective equality.
 */
export async function readBudgetConfig(
  probe: Pick<StateProbe, 'pg'>, organizationId: string, expected: unknown,
): Promise<BudgetConfigObservation[]> {
  const org = requireUuid(organizationId);
  object(expected); keys(expected, TOP);
  for (const [name, value] of Object.entries(expected)) {
    if (['organization', 'org'].includes(name)) level(value);
    else if (['teams', 'users', 'keys', 'tags'].includes(name)) {
      object(value);
      for (const item of Object.values(value)) level(item);
    } else if (value === undefined || (typeof value === 'object' && value !== null) ||
        (typeof value === 'number' && !Number.isFinite(value))) throw new Error('INVALID_BUDGET_SCALAR');
  }
  return probe.pg<BudgetConfigObservation>(`SELECT id::text, organization_id::text,
    version AS config_version, is_active, deleted, created_at::text, updated_at::text,
    budgets = $2::jsonb AS budgets_match FROM agentcc_org_config
    WHERE organization_id = $1 ORDER BY version, id`, [org, JSON.stringify(expected)]);
}
