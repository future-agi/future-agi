// A lock on the running stack, held across Playwright workers. The suite
// runs fullyParallel (playwright.config.ts), so two flows can act on the same
// stack at once; isolation by fresh orgs covers almost everything, but a few
// product actions reach every org at once. The one a flow takes:
//
// - an Owner or Admin's Health Check re-pushes every org's config to the
//   gateway (futureagi/agentcc/views/gateway.py health_check ->
//   _repush_org_configs -> services/config_push.py push_all_org_configs, at
//   most once per 5 minutes), which stamps meta.pushed_at on every org's copy.
//   A flow whose admin clicks Health Check holds ORG_CONFIG_REPUSH from just
//   before the click until its response (the push runs inside the request);
//   a flow that needs no such push inside a window holds it for that window
//   (GW-E2E-009 #3: "the member's Health Check pushes nothing"; GW-E2E-016 #5:
//   a push rebuilds the org's failover with its circuits closed, so none may
//   land between the request that opens the breaker and the one it refuses).
//
// The lock is a Postgres session-level advisory lock on the stack's own
// database (E2E.pgUrl, the probe's), on a connection of its own: the stack is
// the shared resource, so a lock scoped to it holds for every worker and every
// runner attached to that stack, and the session's end frees it (a worker that
// dies mid-window cannot leave it held). It writes no row.
import pg from 'pg';
import { E2E } from './env';

/** Every lock a flow takes, by name (one place, so no two flows spell one differently). */
export const STACK_LOCKS = {
  /** An admin Health Check's re-push of every org's config (see above). */
  ORG_CONFIG_REPUSH: 'org-config-repush',
} as const;

// The two-key advisory lock form: this class id keeps the harness's locks apart
// from any single-key lock the product takes (pg_advisory_lock(bigint)).
const LOCK_CLASS = 0x0e2e;
const POLL_MS = 250;

/** The (class, object) keys a name locks; exported for the harness test. */
export const lockSql = {
  tryLock: 'SELECT pg_try_advisory_lock($1::int, hashtext($2)) AS locked',
  unlock: 'SELECT pg_advisory_unlock($1::int, hashtext($2)) AS unlocked',
  params: (name: string) => [LOCK_CLASS, `futureagi-e2e:${name}`],
};

export class StackLockTimeout extends Error {
  constructor(readonly lockName: string, readonly waitedMs: number) {
    super(`stack lock ${lockName} not acquired within ${waitedMs} ms (another worker holds it)`);
  }
}

export class StackLock {
  private released = false;

  private constructor(readonly name: string, private readonly client: pg.Client, readonly waitedMs: number) {}

  /** Waits until no other session holds `name` (polling every 250 ms), then holds it. */
  static async acquire(name: string, { timeoutMs, pgUrl = E2E.pgUrl }: { timeoutMs: number; pgUrl?: string }): Promise<StackLock> {
    const client = new pg.Client({ connectionString: pgUrl });
    await client.connect();
    const started = Date.now();
    try {
      for (;;) {
        const { rows } = await client.query<{ locked: boolean }>(lockSql.tryLock, lockSql.params(name));
        if (rows[0]?.locked === true) return new StackLock(name, client, Date.now() - started);
        if (Date.now() - started >= timeoutMs) throw new StackLockTimeout(name, Date.now() - started);
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    } catch (err) {
      await client.end().catch(() => undefined);
      throw err;
    }
  }

  /** Frees the lock and closes its session. Safe to call more than once. */
  async release(): Promise<void> {
    if (this.released) return;
    this.released = true;
    try {
      await this.client.query(lockSql.unlock, lockSql.params(this.name));
    } finally {
      await this.client.end().catch(() => undefined);
    }
  }
}
