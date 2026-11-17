import { requireScope, type MoneyScope } from './contract.ts';
import { fail, integer, list, record, timestamp, uuid } from './validation.ts';

export interface ReadOnlyQuery {
  readonly text: string;
  readonly values: readonly string[];
}

/** Public source binds the table and these fields; README lists each citation.
 * Schema comes from the admitted local DB deployment, never search_path.
 * A query descriptor opens no connection and confers no read permission. */
export function accountQuery(scope: MoneyScope): ReadOnlyQuery {
  requireScope(scope);
  return Object.freeze({
    text: `SELECT organization_id::text, granted_total_micros::text, settled_spend_micros::text,
      version::text, gateway_acked_version::text,
      pg_catalog.to_char(updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS updated_at
      FROM "${scope.databaseSchema}"."usage_managedcreditaccount"
      WHERE organization_id = $1::uuid`,
    values: Object.freeze([scope.organizationId]),
  });
}

const FIELDS = ['organization_id', 'granted_total_micros', 'settled_spend_micros',
  'version', 'gateway_acked_version', 'updated_at'] as const;

export function assertAccount(scope: MoneyScope, rows: unknown) {
  requireScope(scope);
  const all = list(rows, 2);
  if (all.length !== 1) return fail('ACCOUNT-CARDINALITY');
  const r = record(all[0], FIELDS);
  if (uuid(r.organization_id) !== scope.organizationId) return fail('ACCOUNT-TENANT');
  const granted = integer(r.granted_total_micros);
  const spend = integer(r.settled_spend_micros, true);
  const version = integer(r.version, true);
  const acked = integer(r.gateway_acked_version, true);
  if (acked > version) return fail('ACCOUNT-VERSION');
  return Object.freeze({
    organizationId: scope.organizationId, grantedTotalMicros: granted.toString(),
    settledSpendMicros: spend.toString(), version: version.toString(),
    gatewayAckedVersion: acked.toString(), updatedAt: timestamp(r.updated_at),
    // Version comparison only; not proof of a push attempt/time or gateway liveness.
    pushState: acked === version ? 'acked' as const : 'pending' as const,
  });
}

/** Boundary equality only. Does not detect a transient write restored between reads. */
export function assertAccountUnchanged(scope: MoneyScope, before: unknown, after: unknown): void {
  if (JSON.stringify(assertAccount(scope, before)) !== JSON.stringify(assertAccount(scope, after))) fail('ACCOUNT-CHANGED');
}
