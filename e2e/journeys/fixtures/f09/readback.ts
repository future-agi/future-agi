import { assertRoster, type IdentityRoster } from './identity.ts';
import { isUuid, record } from './validation.ts';

export interface ReadOnlyQuery {
  readonly text: string;
  readonly values: readonly string[];
}
function orgIds(roster: IdentityRoster): readonly string[] {
  assertRoster(roster);
  return Object.freeze([roster.actors[0].organizationId, roster.actors[4].organizationId]);
}

/** Fixed projection for the accepted read-only PG adapter; no SQL/URL/credentials
 * accepted here, and no connection opened. Counts disabled AND deleted rows. */
export function signupCensusQuery(roster: IdentityRoster): ReadOnlyQuery {
  return Object.freeze({ values: orgIds(roster), text: `
WITH expected(organization_id) AS (VALUES ($1::uuid), ($2::uuid))
SELECT e.organization_id::text AS organization_id,
       COUNT(k.id)::int AS total_rows,
       (COUNT(k.id) FILTER (WHERE k.type = 'system'))::int AS system_rows,
       (COUNT(k.id) FILTER (WHERE k.type = 'user'))::int AS user_rows,
       (COUNT(k.id) FILTER (WHERE k.type NOT IN ('system', 'user')))::int AS other_rows
FROM expected e
LEFT JOIN accounts_orgapikey k ON k.organization_id = e.organization_id
GROUP BY e.organization_id
ORDER BY e.organization_id` });
}

/** Data-only full-cohort oracle after bootstrap, BEFORE any explicit issue.
 * Missing rows, unknown fields and non-numeric counts are not zero. */
export function assertZeroKeySignup(roster: IdentityRoster, input: unknown) {
  const ids = orgIds(roster);
  if (!Array.isArray(input) || input.length !== ids.length) throw new Error('F09-ZERO-KEY');
  const seen = new Set<string>();
  for (const item of input) {
    const row = record(item, ['organization_id', 'total_rows', 'system_rows', 'user_rows', 'other_rows'], 'F09-ZERO-KEY');
    if (typeof row.organization_id !== 'string' || !ids.includes(row.organization_id)
      || seen.has(row.organization_id)) throw new Error('F09-ZERO-KEY');
    seen.add(row.organization_id);
    for (const name of ['total_rows', 'system_rows', 'user_rows', 'other_rows']) {
      if (row[name] !== 0) throw new Error('F09-ZERO-KEY');
    }
  }
  return Object.freeze({ isolationId: roster.isolationId, organizationIds: ids, totalKeys: 0 });
}

function person(roster: IdentityRoster, slot: string, keyId: string) {
  assertRoster(roster);
  const actor = roster.actors.find(a => a.slot === slot);
  if (!actor || !isUuid(keyId)) throw new Error('F09-KEY');
  return actor;
}

/** Pins the ACTUAL early-candidate table fields, not an invented scopes schema.
 * This checks attribution only; it does not authorize issuance or gateway use. */
export function personalOwnershipQuery(roster: IdentityRoster, slot: string, keyId: string): ReadOnlyQuery {
  const actor = person(roster, slot, keyId);
  return Object.freeze({ values: Object.freeze([actor.organizationId, keyId]), text: `
SELECT id::text AS id, user_id::text AS user_id,
       organization_id::text AS organization_id, type, enabled, deleted
FROM accounts_orgapikey
WHERE organization_id = $1::uuid AND id = $2::uuid` });
}

export function assertPersonalOwnership(roster: IdentityRoster, slot: string, keyId: string, input: unknown) {
  const actor = person(roster, slot, keyId);
  if (!Array.isArray(input) || input.length !== 1) throw new Error('F09-KEY');
  const row = record(input[0], ['id', 'user_id', 'organization_id', 'type', 'enabled', 'deleted'], 'F09-KEY');
  if (row.id !== keyId || row.user_id !== actor.userId || row.organization_id !== actor.organizationId
    || row.type !== 'user' || row.enabled !== true || row.deleted !== false) throw new Error('F09-KEY');
  return Object.freeze({ isolationId: roster.isolationId, slot: actor.slot,
    userId: actor.userId, organizationId: actor.organizationId, keyId });
}
