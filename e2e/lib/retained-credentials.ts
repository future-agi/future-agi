import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { E2E } from './env';
import type { TestActor } from './provisioning';

export interface RetainedCredential { id: string; apiKey: string; secretKey: string }
export interface RetainedCredentials { system: RetainedCredential; retired: RetainedCredential }

export function requireRetainedFixtureTarget(env: NodeJS.ProcessEnv,
  endpoints: readonly string[]): void {
  if (env.E2E_RETAINED_CREDENTIAL_FIXTURES !== '1'
    || !/^futureagi-e2e-obs61-[a-z0-9-]+$/.test(env.E2E_COMPOSE_PROJECT ?? '')
    || (env.E2E_STACK && env.E2E_STACK !== 'distributed')
    || (env.DOCKER_CONTEXT && env.DOCKER_CONTEXT !== 'default')
    || (env.DOCKER_HOST && !env.DOCKER_HOST.startsWith('unix:///'))) {
    throw new Error('Retained credentials require an opted-in disposable distributed stack');
  }
  for (const endpoint of endpoints) {
    const url = new URL(endpoint);
    if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
      || !['http:', 'postgresql:'].includes(url.protocol)) {
      throw new Error('Retained credentials require loopback-only endpoints');
    }
  }
}

export function parseRetainedCredentials(raw: string): RetainedCredentials {
  try {
    // Settings modules may print a startup line to stdout first; the fixture's JSON is the last line.
    const lastLine = raw.trim().split('\n').pop() ?? '';
    const value = JSON.parse(lastLine) as RetainedCredentials;
    if (Object.keys(value).sort().join(',') !== 'retired,system') throw new Error();
    const ids = new Set<string>();
    for (const row of [value.system, value.retired]) {
      if (Object.keys(row).sort().join(',') !== 'apiKey,id,secretKey'
        || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(row.id)
        || !/^[0-9a-f]{32}$/.test(row.apiKey) || !/^[0-9a-f]{32}$/.test(row.secretKey)) throw new Error();
      ids.add(row.id);
    }
    if (ids.size !== 2 || value.system.apiKey === value.retired.apiKey) throw new Error();
    return value;
  } catch {
    throw new Error('Invalid retained fixture result (values withheld)');
  }
}

/** Explicit E2E-image-only retained state, not a new production credential issuer.
 * The pipe is memory-only. Neither subprocess output nor credential material is attached.
 * Flow probes must independently read every row from primary Postgres. */
export function seedRetainedCredentials(actor: TestActor, name: string): RetainedCredentials {
  requireRetainedFixtureTarget(process.env,
    [E2E.appUrl, E2E.apiUrl, E2E.collectorUrl, E2E.pgUrl, E2E.chUrl]);
  const bin = fileURLToPath(new URL('../../bin/e2e', import.meta.url));
  let stage = 'exec';
  try {
    const raw = execFileSync(bin, ['compose', 'exec', '-T',
      '-e', 'E2E_RETAINED_CREDENTIAL_FIXTURES=1',
      // The backend container keeps NO_STARTUP_DB_MUTATIONS=true; a fixture exec that writes needs false,
      // as lib/ledger-seed.ts and lib/temporal-probe.ts already pass.
      '-e', 'NO_STARTUP_DB_MUTATIONS=false',
      '-e', 'DJANGO_SETTINGS_MODULE=tfc.settings.e2e',
      'backend', 'python', '-m', 'tfc.e2e_retained_credentials'], {
      encoding: 'utf8', timeout: 30_000, maxBuffer: 64 * 1024, stdio: ['pipe', 'pipe', 'pipe'],
      input: JSON.stringify({ email: actor.email, organizationId: actor.organizationId,
        workspaceId: actor.workspaceId, name }),
    });
    stage = 'parse';
    const value = parseRetainedCredentials(raw);
    stage = 'registry';
    if (process.env.E2E_VALUE_FREE === '1') {
      const registry = (globalThis as unknown as {
        __futureagiE2eValueFree?: { registerAs(what: string, ...values: string[]): unknown };
      }).__futureagiE2eValueFree;
      if (!registry) throw new Error();
      for (const row of [value.system, value.retired]) {
        registry.registerAs('credential', row.apiKey, row.secretKey,
          Buffer.from(`${row.apiKey}:${row.secretKey}`).toString('base64'));
      }
    }
    return value;
  } catch (error) {
    // Value-free diagnosis only: the exit status and the Python exception class from the last stderr
    // line. The fixture's output and messages may carry credentials, so nothing else is reported.
    const failure = error as { status?: number | null; stderr?: string | Buffer };
    // The last stderr line that names an exception class (a traceback may be followed by other lines).
    const classLine = /^([A-Za-z_][\w.]*(?:Error|Exception|Exit|DoesNotExist|Refused|Denied))\b/;
    const kind = String(failure.stderr ?? '').trim().split('\n').reverse()
      .map((line) => classLine.exec(line.trim())?.[1]).find(Boolean) ?? 'unknown';
    throw new Error(`Retained credential fixture failed (stage ${stage}, exit ${failure.status ?? 'none'}, ${kind}; subprocess details withheld)`);
  }
}
