import pg from 'pg';
import { E2E } from './env';
import { createPersonalKey, type PersonalKey } from './personal-key';
import type { ScopeActor } from './scope-actors';

/** Historical credential fixture only. Public issuance runs first, so the
 * generated secret has already entered the shared value-free registry.
 * Never use the read-only StateProbe to mutate storage. */
export async function seedRetiredKey(actor: ScopeActor, name: string): Promise<PersonalKey> {
  if (!name.startsWith('e2e-') || !['localhost', '127.0.0.1'].includes(new URL(E2E.pgUrl).hostname)) {
    throw new Error('Legacy-key fixtures require a local synthetic test identity');
  }
  const key = await createPersonalKey(actor.api, name);
  const db = new pg.Client({ connectionString: E2E.pgUrl });
  await db.connect();
  try {
    const result = await db.query(
      `UPDATE accounts_orgapikey SET type = 'system', enabled = false,
       retired_at = now(), retired_reason = '733', prior_enabled = true
       WHERE id = $1 AND organization_id = $2 AND user_id = $3
         AND name = $4 AND type = 'user' AND NOT deleted
       RETURNING id`, [key.key_id, actor.organizationId, actor.userId, name]);
    if (result.rowCount !== 1) throw new Error('Legacy-key fixture did not select exactly its own issued row');
    return key;
  } finally { await db.end(); }
}
