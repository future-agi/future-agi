import pg from 'pg';
import { test as base, expect } from './fixtures';
import { E2E } from './env';

// The existing customer: an org dated before the Simulate cutoff, so it keeps
// the Simulate pages new orgs lose (#3502: restricted = cutoff set and
// org.created_at >= cutoff, accounts/services/simulate_access.py). The cutoff,
// 2026-10-01T00:00:00Z, is set only in e2e/stack/docker-compose.e2e.yml
// (backend) and e2e/stack/docker-compose.standalone-e2e.yml (app).
export const EXISTING_CUSTOMER_CREATED_AT = '2026-09-30T00:00:00.000Z';

/** Dates one org to EXISTING_CUSTOMER_CREATED_AT through its own connection;
 * the StateProbe stays read-only. Fails unless exactly one row changed and the
 * stored value the UPDATE returns matches, so a wrong id or a wrong
 * E2E_PG_URL (attach mode) is loud. */
export async function backdateToExistingCustomer(organizationId: string): Promise<void> {
  let where: string; // host:port only, never the credentials
  try {
    where = new URL(E2E.pgUrl).host;
  } catch {
    throw new Error('existing customer: E2E_PG_URL is missing or not a URL (is E2E_PG_URL the stack\'s database?)');
  }
  const client = new pg.Client({ connectionString: E2E.pgUrl });
  try {
    await client.connect();
    const updated = await client.query<{ created_at: Date }>(
      'UPDATE accounts_organization SET created_at = $2 WHERE id = $1 RETURNING created_at',
      [organizationId, EXISTING_CUSTOMER_CREATED_AT]);
    if (updated.rowCount !== 1) {
      throw new Error(`existing customer: dating org ${organizationId} changed ${updated.rowCount} rows `
        + `in Postgres at ${where}, expected exactly 1 (is E2E_PG_URL the stack's database?)`);
    }
    const stored = updated.rows[0]?.created_at.toISOString();
    if (stored !== EXISTING_CUSTOMER_CREATED_AT) {
      throw new Error(`existing customer: dating org ${organizationId} returned created_at ${stored}, `
        + `expected ${EXISTING_CUSTOMER_CREATED_AT}`);
    }
  } finally {
    await client.end();
  }
}

export const test = base.extend({
  // Overrides only the worker-scoped actor: lib/fixtures.ts signs it up as
  // usual, then its org is dated. context, page and probe take `actor` by
  // name, so they follow it. A different worker-fixture set gets its own
  // workers, so this actor never serves an ordinary spec.
  actor: [async ({ actor }, use) => {
    await backdateToExistingCustomer(actor.organizationId);
    await use(actor);
  }, { scope: 'worker' }],
});

export { expect };
