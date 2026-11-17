// H3: a read-only view of the gateway's Redis state for the walks (W00 census,
// W01-W04 money, W08 revocation, W11 approvals). It reads, never writes:
//   - commands: only REDIS_READ_COMMANDS (XINFO only with GROUPS or CONSUMERS);
//   - keys: only the shapes in allowedKeys, each tied to an organization the walk
//     owns: <prefix>mc:{<org>}:..., <prefix>cap:{<org>}..., <prefix>approval:{<org>}...,
//     plus the two shared sets <prefix>mc:outbox_orgs and <prefix>mc:needs_push;
//   - SCAN: only MATCH on one of those shapes ending in `*`, with COUNT.
// Key names: agentcc-gateway/internal/redisstate/managedcredits.go (mc:{org}:acct,
// holds, holdexp, outbox, settled:<hold>; mc:outbox_orgs and mc:needs_push are sets)
// and spendguard.go (mc:{org}:paused), behind the gateway's global Redis prefix
// (config.go RedisStateConfig.Prefix, "agentcc:" by default). The cap and approval
// shapes are the walk plan's; W04 and W11 resolve them from internal/redisstate
// when they are written.
// Transport: `bin/e2e compose exec` (DOCKER_HOST, never a context). Standalone's
// Redis runs inside `app`, bound to its loopback with the stack's password
// (deploy/standalone/bin/start); Distributed has its `redis` service. The password
// never leaves the container: redis-cli reads REDISCLI_AUTH from its own env.
// On both E2E stacks the stock gateway keeps its credit ledger in memory
// (managed.require_redis false, no redis section), so its mc:* keys are absent by
// construction; the H8 replica overlay is what puts gateway state in Redis (db 4).
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../../', import.meta.url));

export const REDIS_READ_COMMANDS = ['GET', 'HGETALL', 'HGET', 'EXISTS', 'TTL', 'PTTL', 'SMEMBERS', 'XLEN', 'XRANGE',
  'XINFO', 'XPENDING', 'SCAN'] as const;
export type RedisReadCommand = typeof REDIS_READ_COMMANDS[number];

/** The gateway's default global key prefix (config.go RedisStateConfig.Prefix). */
export const GATEWAY_REDIS_PREFIX = 'agentcc:';
/** The database the H8 replica overlay gives both gateways (docker-compose.mcp-replica.yml). */
export const GATEWAY_REDIS_DB = 4;

const ORG = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SAFE_TAIL = /^[A-Za-z0-9_:.-]*$/;
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** A probe refusal: the command was never sent. */
export class RedisProbeRefused extends Error {}
const refuse = (why: string): never => { throw new RedisProbeRefused(`STOP: redis probe ${why}`); };

/** The key shapes a walk may read for `orgs` (each an organization UUID). */
export function allowedKeys(orgs: readonly string[], prefix = GATEWAY_REDIS_PREFIX): RegExp[] {
  if (!/^[A-Za-z0-9_:.-]*$/.test(prefix)) refuse('prefix is not a plain key prefix');
  for (const org of orgs) if (!ORG.test(org)) refuse(`org ${JSON.stringify(org)} is not an organization id`);
  const p = escapeRe(prefix);
  const shapes = orgs.flatMap((org) => ['mc', 'cap', 'approval'].map((kind) =>
    new RegExp(`^${p}${kind}:\\{${escapeRe(org)}\\}(:[A-Za-z0-9_:.-]*)?$`)));
  return [...shapes, new RegExp(`^${p}mc:outbox_orgs$`), new RegExp(`^${p}mc:needs_push$`)];
}

/** The SCAN MATCH patterns a walk may use for `orgs`: one per owned shape. */
export function allowedPatterns(orgs: readonly string[], prefix = GATEWAY_REDIS_PREFIX): string[] {
  allowedKeys(orgs, prefix);
  return orgs.flatMap((org) => ['mc', 'cap', 'approval'].map((kind) => `${prefix}${kind}:{${org}}*`));
}

/** Throws RedisProbeRefused unless `args` is one allowed read of an allowed key. */
export function validateRedisCommand(args: readonly string[], orgs: readonly string[],
  prefix = GATEWAY_REDIS_PREFIX): void {
  const [raw, ...rest] = args;
  const cmd = String(raw ?? '').toUpperCase() as RedisReadCommand;
  if (!(REDIS_READ_COMMANDS as readonly string[]).includes(cmd)) refuse(`refuses ${JSON.stringify(raw)}: not a read in the allowlist`);
  const keys = allowedKeys(orgs, prefix);
  const keyOk = (key: string | undefined) => key !== undefined && keys.some((re) => re.test(key));
  const plain = (value: string | undefined, what: string) => {
    if (value === undefined || !SAFE_TAIL.test(value) && !/^[0-9+-]+$/.test(value)) refuse(`${cmd} ${what} is not plain`);
  };
  switch (cmd) {
    case 'SCAN': {
      const [cursor, match, pattern, count, n, ...extra] = rest;
      if (!/^\d+$/.test(cursor ?? '') || String(match).toUpperCase() !== 'MATCH' ||
          !allowedPatterns(orgs, prefix).includes(pattern ?? '') || String(count).toUpperCase() !== 'COUNT' ||
          !/^\d{1,4}$/.test(n ?? '') || extra.length) {
        refuse('SCAN must be SCAN <cursor> MATCH <owned pattern> COUNT <n>');
      }
      return;
    }
    case 'XINFO': {
      const [sub, key, ...extra] = rest;
      if (!['GROUPS', 'CONSUMERS'].includes(String(sub).toUpperCase())) refuse('XINFO takes GROUPS or CONSUMERS only');
      if (!keyOk(key)) refuse(`XINFO key ${JSON.stringify(key)} is not an owned key`);
      if (String(sub).toUpperCase() === 'CONSUMERS') plain(extra[0], 'group');
      if (extra.length > (String(sub).toUpperCase() === 'CONSUMERS' ? 1 : 0)) refuse('XINFO has extra arguments');
      return;
    }
    default: {
      const [key, ...extra] = rest;
      if (cmd === 'EXISTS' ? !rest.length || !rest.every(keyOk) : !keyOk(key)) {
        refuse(`${cmd} key ${JSON.stringify(key)} is not an owned key`);
      }
      const arity: Record<string, number> = { GET: 0, HGETALL: 0, TTL: 0, PTTL: 0, SMEMBERS: 0, XLEN: 0, HGET: 1,
        XRANGE: 2, XPENDING: 1 };
      if (cmd !== 'EXISTS' && extra.length !== arity[cmd]) refuse(`${cmd} takes ${arity[cmd]} arguments after the key`);
      for (const value of cmd === 'EXISTS' ? [] : extra) plain(value, 'argument');
    }
  }
}

/** One key's state as the allowed reads can see it (no TYPE, no DUMP: the reads
 * that answer WRONGTYPE are tried in turn; a sorted set reads as unreadable). */
export interface RedisKeyState { key: string; pttl: number; kind: 'string' | 'hash' | 'set' | 'stream' | 'other';
  value: unknown }

export interface RedisTransport { (args: string[]): string }

const WRONG = /^(\(error\) )?(WRONGTYPE|ERR|NOAUTH|WRONGPASS|NOPERM)\b/;

export class RedisProbe {
  readonly log: { args: string[]; ok: boolean }[] = [];

  constructor(readonly orgs: readonly string[], private readonly transport: RedisTransport = composeTransport(),
    readonly prefix = GATEWAY_REDIS_PREFIX) {
    allowedKeys(orgs, prefix);
  }

  /** Raw lines of one allowed read; an error reply is returned as a thrown Error. */
  read(...args: string[]): string[] {
    validateRedisCommand(args, this.orgs, this.prefix);
    let out: string;
    try {
      out = this.transport(args).replace(/\n$/, '');
    } catch (error) {
      // Never the command line: only the status and the first stderr line.
      const failure = error as { status?: number; stderr?: unknown };
      this.log.push({ args: [args[0]], ok: false });
      throw new Error(`redis transport failed (status ${failure.status ?? 'unknown'}): ` +
        String(failure.stderr ?? '').split('\n')[0].slice(0, 200));
    }
    const lines = out === '' ? [] : out.split('\n');
    // redis-cli --raw may append a blank line after an error reply. Do not
    // misclassify WRONGTYPE as a successful string and suppress hash probing.
    const failed = lines.length > 0 && WRONG.test(lines[0]) && lines.slice(1).every(line => line === '');
    this.log.push({ args: [args[0], ...args.slice(1).map((a) => (a.length > 120 ? `${a.slice(0, 120)}...` : a))], ok: !failed });
    if (failed) throw new Error(`redis ${args[0]}: ${lines[0].slice(0, 200)}`);
    return lines;
  }

  /** Every key matching the org's owned shapes, sorted. */
  keys(org: string): string[] {
    const found = new Set<string>();
    for (const pattern of allowedPatterns([org], this.prefix)) {
      let cursor = '0';
      do {
        const [next, ...keys] = this.read('SCAN', cursor, 'MATCH', pattern, 'COUNT', '500');
        cursor = next ?? '0';
        // redis-cli --raw prints a blank line for an empty nested SCAN array.
        // Empty keys cannot match the owned prefix; preserve generic GET values.
        for (const key of keys) if (key !== '') found.add(key);
      } while (cursor !== '0');
    }
    return [...found].sort();
  }

  /** Whether `org` is a member of a shared set (mc:outbox_orgs, mc:needs_push). */
  inSet(setKey: 'mc:outbox_orgs' | 'mc:needs_push', org: string): boolean {
    return this.read('SMEMBERS', `${this.prefix}${setKey}`).includes(org);
  }

  /** The org's keys with their values: byte-comparable between two snapshots. */
  snapshot(org: string): RedisKeyState[] {
    return this.keys(org).map((key) => {
      const pttl = Number(this.read('PTTL', key)[0]);
      const attempt = (args: string[]): string[] | undefined => { try { return this.read(...args); } catch { return undefined; } };
      const str = attempt(['GET', key]);
      if (str) return { key, pttl, kind: 'string' as const, value: str.join('\n') };
      const hash = attempt(['HGETALL', key]);
      if (hash) return { key, pttl, kind: 'hash' as const, value: Object.fromEntries(
        hash.flatMap((_, i) => (i % 2 ? [] : [[hash[i], hash[i + 1]]]))) };
      const set = attempt(['SMEMBERS', key]);
      if (set) return { key, pttl, kind: 'set' as const, value: [...set].sort() };
      const stream = attempt(['XRANGE', key, '-', '+']);
      if (stream) return { key, pttl, kind: 'stream' as const, value: stream };
      return { key, pttl, kind: 'other' as const, value: null };
    });
  }
}

/** `bin/e2e compose exec -T <service> redis-cli -n <db> ...`, the stack's own password
 * read inside the container. The service is `app` on Standalone, `redis` on Distributed. */
export function composeTransport(db = Number(process.env.E2E_GATEWAY_REDIS_DB ?? GATEWAY_REDIS_DB),
  service = process.env.E2E_STACK === 'standalone' ? 'app' : 'redis'): RedisTransport {
  if (!Number.isInteger(db) || db < 0 || db > 15) refuse(`database ${db} is not 0-15`);
  const script = 'if [ -n "${REDIS_PASSWORD:-}" ]; then export REDISCLI_AUTH="$REDIS_PASSWORD"; fi; ' +
    'db=$1; shift; exec redis-cli -n "$db" "$@"';
  return (args) => {
    const env = { ...process.env };
    delete env.DOCKER_CONTEXT;
    return execFileSync(`${root}bin/e2e`, ['compose', 'exec', '-T', service, 'sh', '-c', script, 'redis-probe',
      String(db), ...args], { env, encoding: 'utf8', timeout: 30_000, maxBuffer: 8 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'pipe'] });
  };
}
