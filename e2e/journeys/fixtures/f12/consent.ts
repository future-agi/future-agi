import type { PaymentScope } from './contract.ts';
import { requireScope } from './contract.ts';
import { count, fail, integer, list, ORACLE_ONLY, record, schema, timestamp, uuid } from './validation.ts';

/** Public model only: candidate agentcc/models/auto_reload_consent.py:19-45. */
export function consentQuery(s: PaymentScope, databaseSchema: unknown) {
  requireScope(s); const name = schema(databaseSchema);
  const text = `SELECT id::text AS id, organization_id::text AS organization_id,
actor_id::text AS actor_id, actor_identifier::text AS actor_identifier,
consent_version, to_char(accepted_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS accepted_at,
threshold_micros::text AS threshold_micros, amount_micros::text AS amount_micros
FROM "${name}"."agentcc_auto_reload_consent"
WHERE organization_id = $1::uuid ORDER BY accepted_at, id`;
  return Object.freeze({ text, values: Object.freeze([s.organizationId]) });
}
function receipts(s: PaymentScope, input: unknown) {
  const rows = list(input).map(input => {
    const r = record(input, ['id', 'organization_id', 'actor_id', 'actor_identifier', 'consent_version',
      'accepted_at', 'threshold_micros', 'amount_micros']);
    if (r.organization_id !== s.organizationId || r.actor_identifier !== s.personId
      || (r.actor_id !== null && r.actor_id !== s.personId)) return fail('CONSENT');
    if (typeof r.consent_version !== 'string' || !/^auto-reload-v[0-9]{1,4}$/.test(r.consent_version)) return fail('CONSENT');
    return { id: uuid(r.id), organization_id: s.organizationId, actor_id: r.actor_id, actor_identifier: s.personId,
      consent_version: r.consent_version, accepted_at: timestamp(r.accepted_at),
      threshold_micros: String(integer(r.threshold_micros, true)), amount_micros: String(integer(r.amount_micros, true)) };
  });
  if (new Set(rows.map(r => r.id)).size !== rows.length) return fail('CONSENT');
  return rows.sort((a, b) => a.id.localeCompare(b.id));
}
// Proposed private-account capture. This is not a guessed SQL query.
function state(input: unknown) {
  const r = record(input, ['enabled', 'version', 'acceptedAt', 'thresholdMicros', 'amountMicros']);
  if (typeof r.enabled !== 'boolean' || typeof r.version !== 'string'
    || (r.version !== '' && !/^auto-reload-v[0-9]{1,4}$/.test(r.version))) return fail('CONSENT');
  return { enabled: r.enabled, version: r.version, acceptedAt: r.acceptedAt === null ? null : timestamp(r.acceptedAt),
    thresholdMicros: String(integer(r.thresholdMicros, true)), amountMicros: String(integer(r.amountMicros, true)) };
}
/** Full fresh-person history must be captured independently in a consistent read-only snapshot. */
export function assertConsentTransition(s: PaymentScope, input: unknown) {
  requireScope(s);
  const r = record(input, ['action', 'expectedReceiptId', 'before', 'after', 'beforeReceipts', 'afterReceipts']);
  const before = state(r.before); const after = state(r.after);
  const oldRows = receipts(s, r.beforeReceipts); const newRows = receipts(s, r.afterReceipts);
  // History is append-only regardless of action; deletion, rewriting and ID reuse fail.
  for (const old of oldRows) {
    const current = newRows.find(row => row.id === old.id);
    if (!current || JSON.stringify(current) !== JSON.stringify(old)) return fail('CONSENT');
  }
  if (r.action === 'enable') {
    const id = uuid(r.expectedReceiptId);
    if (!after.enabled || newRows.length !== oldRows.length + 1 || oldRows.some(row => row.id === id)) return fail('CONSENT');
    const fresh = newRows.find(row => row.id === id);
    if (!fresh || fresh.actor_id !== s.personId || fresh.consent_version !== 'auto-reload-v1'
      || fresh.accepted_at !== after.acceptedAt || fresh.threshold_micros !== after.thresholdMicros
      || fresh.amount_micros !== after.amountMicros) return fail('CONSENT');
  } else if (r.action === 'revoke' || r.action === 'reject') {
    if (r.expectedReceiptId !== null || JSON.stringify(oldRows) !== JSON.stringify(newRows)) return fail('CONSENT');
    if (r.action === 'revoke' && (after.enabled || after.version !== '' || after.acceptedAt !== null)) return fail('CONSENT');
    if (r.action === 'reject' && JSON.stringify(before) !== JSON.stringify(after)) return fail('CONSENT');
  } else return fail('CONSENT');
  const active = after.enabled && after.version === 'auto-reload-v1' && after.acceptedAt !== null
    && newRows.some(row => row.actor_id === row.actor_identifier && row.consent_version === after.version
      && row.accepted_at === after.acceptedAt && row.threshold_micros === after.thresholdMicros && row.amount_micros === after.amountMicros);
  if (r.action === 'enable' && !active) return fail('CONSENT');
  return Object.freeze({ kind: ORACLE_ONLY, active, receiptCount: newRows.length });
}

/**
 * Proposed ordering witness only. No timer, in-memory lock or self-declared flag
 * can prove the private worker used the real account-row serialization boundary.
 */
export function assertClaimRace(s: PaymentScope, input: unknown) {
  requireScope(s);
  const r = record(input, ['scenario', 'policyEnabled', 'disableCommitted', 'claimLockAcquired',
    'receiptCurrentUnderLock', 'claimCount', 'paymentStartedCount']);
  if (r.scenario === 'policy-off') {
    if (r.policyEnabled !== false || r.disableCommitted !== null || r.claimLockAcquired !== null
      || r.receiptCurrentUnderLock !== false || r.claimCount !== 0 || r.paymentStartedCount !== 0) return fail('CLAIM');
    return Object.freeze({ kind: ORACLE_ONLY, positiveClaimCovered: false });
  }
  if (r.policyEnabled !== true) return fail('CLAIM');
  const disable = count(r.disableCommitted); const lock = count(r.claimLockAcquired);
  if (r.scenario === 'disable-before-claim') {
    if (disable >= lock || r.receiptCurrentUnderLock !== false || r.claimCount !== 0 || r.paymentStartedCount !== 0) return fail('CLAIM');
  } else if (r.scenario === 'claim-before-disable') {
    if (lock >= disable || r.receiptCurrentUnderLock !== true || r.claimCount !== 1 || r.paymentStartedCount !== 1) return fail('CLAIM');
  } else return fail('CLAIM');
  return Object.freeze({ kind: ORACLE_ONLY, positiveClaimCovered: r.scenario === 'claim-before-disable' });
}
