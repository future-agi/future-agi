import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, writeFileSync, rmSync, cpSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import test from 'node:test';

const require = createRequire(import.meta.url);
const playwright = require.resolve('@playwright/test');
const cli = require.resolve('@playwright/test/cli');
const lib = fileURLToPath(new URL('../lib/typesafe-live.ts', import.meta.url));
const fixture = fileURLToPath(new URL('../lib/typesafe-lifecycle.ts', import.meta.url));

for (const [mode, bodyVerdict, cleanupVerdict, error] of [
  ['timeout-rate', 'FAIL', 'PASS', 'Test timeout'],
  ['timeout-budget', 'FAIL', 'PASS', 'Test timeout'],
  ['timeout-reset-failure', 'FAIL', 'FAIL', 'Test timeout'],
  ['timeout-gateway-unreachable', 'FAIL', 'FAIL', 'Test timeout'],
  ['timeout-fetch', 'FAIL', 'PASS', 'Test timeout'],
  ['timeout-write', 'FAIL', 'PASS', 'Test timeout'],
  ['timeout-cleanup', 'FAIL', 'FAIL', 'Test timeout'],
  ['hard-cleanup', 'FAIL', 'FAIL', 'original body failure'],
  ['hard-report', 'FAIL', 'PASS', 'original body failure'],
  ['soft-cleanup', 'FAIL', 'FAIL', 'original soft failure'],
  ['cleanup-only', 'PASS', 'FAIL', 'TypeSafe teardown failed'],
  ['report-only', 'PASS', 'PASS', 'TypeSafe report could not be written'],
  ['pass', 'PASS', 'PASS', undefined],
]) {
  test(`real Playwright fixture: ${mode} records body ${bodyVerdict} / cleanup ${cleanupVerdict}`, () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'typesafe-runner-timeout-'));
    try {
      writeFileSync(path.join(dir, 'playwright.config.ts'), `export default {
        testDir: '.', testMatch: 'timeout.spec.ts', timeout: 500, workers: 1, retries: 0,
        reporter: 'json', outputDir: ${JSON.stringify(path.join(dir, 'results'))},
      };`);
      writeFileSync(path.join(dir, 'timeout.spec.ts'), `
        import { test as base, expect } from ${JSON.stringify(playwright)};
        import { TypeSafeLifecycle, createTypeSafeResources, teardownTypeSafe } from ${JSON.stringify(lib)};
        import { typeSafeLifecycleFixture } from ${JSON.stringify(fixture)};
        import { writeFileSync, appendFileSync } from 'node:fs';
        import { createServer } from 'node:http';
        const dir = ${JSON.stringify(dir)}, mode = ${JSON.stringify(mode)};
        const mark = (name: string) => {
          writeFileSync(dir + '/' + name, 'yes');
          appendFileSync(dir + '/events', name + '\\n');
        };
        const test = base.extend<{ lifecycle: TypeSafeLifecycle }>({
          lifecycle: [async ({}, use, info) => {
            await typeSafeLifecycleFixture(use, info, new TypeSafeLifecycle({
              drain: 2000, close: 500, cleanup: 100, record: 100,
            }));
          }, { timeout: 4000 }],
        });
        test('resource lifecycle', async ({ lifecycle }) => {
          let server: ReturnType<typeof createServer> | undefined;
          let providers = [{ id: 'baseline-provider', provider_name: 'unrelated' }];
          const keys = [{ id: 'baseline-key', name: 'existing', status: 'active', gateway_key_id: 'old' }];
          const resources = createTypeSafeResources(providers, keys, { organizationId: 'owned', gatewayKeys: [] });
          const originalLimits = { backend: { id: 'baseline', rate_limiting: { per_key_rpm: null }, budgets: {} }, gateway: {} };
          let backendLimits = structuredClone(originalLimits.backend), gatewayLimits = structuredClone(originalLimits.gateway);
          resources.limits = structuredClone(originalLimits);
          resources.providerNames.add('run-provider');
          resources.providerNames.add('pending-provider');
          resources.keyNames.add('run-key');
          const api = lifecycle.guardApi({
            async create() {
              // Simulate a write already accepted by a server at runner timeout.
              await new Promise(resolve => setTimeout(resolve, 800));
              providers.push({ id: 'pending', provider_name: 'pending-provider' });
              mark('write-settled');
            },
          });
          await lifecycle.run({
            run: async () => {
              try {
                providers.push({ id: 'run', provider_name: 'run-provider' });
                keys.push({ id: 'run', name: 'run-key', status: 'active', gateway_key_id: 'new' });
                mark('resource-created');
                if (['timeout-rate', 'timeout-budget', 'timeout-reset-failure', 'timeout-gateway-unreachable'].includes(mode)) {
                  const temporary = mode === 'timeout-budget' ? { budgets: { enabled: true, org_limit: 0.000001 } }
                    : { rate_limiting: { enabled: true, per_key_rpm: 1 } };
                  backendLimits = { ...backendLimits, ...temporary }; gatewayLimits = structuredClone(temporary);
                  mark('limits-mutated');
                }
                if (mode === 'timeout-write') await api.create();
                else if (mode.startsWith('timeout')) {
                  server = createServer(() => {});
                  await new Promise<void>(resolve => server!.listen(0, '127.0.0.1', resolve));
                  await fetch('http://127.0.0.1:' + (server.address() as { port: number }).port,
                    { signal: lifecycle.signal });
                }
                if (mode.startsWith('timeout')) mark('late-mutation');
                if (mode.startsWith('hard')) throw new Error('original body failure');
                if (mode.startsWith('soft')) expect.soft(false, 'original soft failure').toBe(true);
              } finally { mark('body-settled'); }
            },
            close: async () => {
              server?.closeAllConnections();
              if (server) await new Promise<void>(resolve => server!.close(() => resolve()));
            },
            teardown: async signal => {
              mark('cleanup-ran');
              if (mode === 'timeout-cleanup') {
                await new Promise<void>((_, reject) => signal.addEventListener('abort', () => {
                  mark('cleanup-aborted'); reject(new Error(['private', 'cleanup', 'sentinel'].join('-')));
                }, { once: true }));
              }
              if (mode.includes('cleanup')) throw new Error(['private', 'cleanup', 'sentinel'].join('-'));
              const result = await teardownTypeSafe(resources, {
                listProviders: async () => providers,
                listKeys: async () => keys,
                listGatewayKeys: async () => keys.map(k => ({ id: k.gateway_key_id, name: k.name, status: k.status, metadata: { org_id: 'owned' } })),
                revokeGatewayKey: async id => { keys.find(k => k.gateway_key_id === id)!.status = 'revoked'; },
                deleteProvider: async id => { providers = providers.filter(p => p.id !== id); gatewayLimits = structuredClone(backendLimits); mark('provider-push'); },
                restoreLimits: async snapshot => {
                  expect(lifecycle.signal.aborted).toBe(true); expect(signal.aborted).toBe(false);
                  if (mode === 'timeout-reset-failure') throw new Error(['private', 'cleanup', 'sentinel'].join('-'));
                  backendLimits = structuredClone(snapshot.backend); gatewayLimits = structuredClone(snapshot.gateway); mark('limits-restored');
                },
                backendLimitsRestored: async snapshot => JSON.stringify(backendLimits) === JSON.stringify(snapshot.backend),
                gatewayLimitsRestored: async snapshot => {
                  if (mode === 'timeout-gateway-unreachable') throw new Error(['private', 'cleanup', 'sentinel'].join('-'));
                  return JSON.stringify(gatewayLimits) === JSON.stringify(snapshot.gateway);
                },
                revokeKey: async id => { keys.find(k => k.id === id)!.status = 'revoked'; },
                gatewayProvidersAbsent: async names => !providers.some(p => names.includes(p.provider_name)),
                gatewayKeyRevoked: async key => key.status === 'revoked',
                waitUntil: async check => { if (!await check()) throw new Error('verification failed'); },
              });
              writeFileSync(dir + '/remaining.json', JSON.stringify({ providers, keys, backendLimits, gatewayLimits, originalLimits }));
              return result;
            },
            record: result => {
              writeFileSync(dir + '/report.json', JSON.stringify(result));
              mark('report-ran');
              if (mode.includes('report')) throw new Error(['private', 'report', 'sentinel'].join('-'));
            },
          });
        });
      `);
      const child = spawnSync(process.execPath, [cli, 'test', '-c', path.join(dir, 'playwright.config.ts')], {
        encoding: 'utf8', timeout: 20_000, env: { ...process.env, FORCE_COLOR: '0' },
      });
      writeFileSync(path.join(dir, 'runner.json'), child.stdout ?? '');
      writeFileSync(path.join(dir, 'runner.stderr'), child.stderr ?? '');
      assert.ifError(child.error);
      assert.equal(child.status, error ? 1 : 0, child.stderr);
      const results = JSON.parse(child.stdout);
      assert.equal(results.stats.unexpected, error ? 1 : 0);
      const result = results.suites[0].specs[0].tests[0].results[0];
      assert.equal(result.status, mode.startsWith('timeout') ? 'timedOut' : error ? 'failed' : 'passed');
      if (error) {
        assert.equal(result.errors.length, 1, 'cleanup/report failure must not add to the original runner error');
        assert.ok(result.errors[0].message.includes(error), result.errors[0].message);
      }
      assert.doesNotMatch(child.stdout, /private-(?:cleanup|report)-sentinel/);
      assert.equal(existsSync(path.join(dir, 'resource-created')), true);
      assert.equal(existsSync(path.join(dir, 'cleanup-ran')), true, 'runner must await cleanup even while the body is pending');
      assert.equal(existsSync(path.join(dir, 'late-mutation')), false);
      const events = readFileSync(path.join(dir, 'events'), 'utf8').trim().split('\n');
      assert.ok(events.indexOf('body-settled') < events.indexOf('cleanup-ran'));
      assert.ok(events.indexOf('cleanup-ran') < events.indexOf('report-ran'));
      if (mode === 'timeout-write') assert.ok(events.indexOf('write-settled') < events.indexOf('cleanup-ran'));
      if (mode === 'timeout-cleanup') assert.ok(events.includes('cleanup-aborted'));
      const report = JSON.parse(readFileSync(path.join(dir, 'report.json'), 'utf8'));
      assert.equal(report.testVerdict, bodyVerdict);
      assert.equal(report.teardown.verdict, cleanupVerdict);
      if (cleanupVerdict === 'PASS') {
        const remaining = JSON.parse(readFileSync(path.join(dir, 'remaining.json'), 'utf8'));
        assert.deepEqual(remaining.providers, [{ id: 'baseline-provider', provider_name: 'unrelated' }]);
        assert.equal(remaining.keys[0].status, 'active');
        assert.equal(remaining.keys[1].status, 'revoked');
        assert.deepEqual(remaining.backendLimits, remaining.originalLimits.backend);
        assert.deepEqual(remaining.gatewayLimits, remaining.originalLimits.gateway);
        assert.ok(events.lastIndexOf('provider-push') < events.indexOf('limits-restored'));
      }
    } finally {
      // Optional sanitized evidence for a local handoff; no stack credentials.
      const evidence = process.env.TYPESAFE_TIMEOUT_EVIDENCE_DIR;
      if (evidence) {
        mkdirSync(evidence, { recursive: true });
        cpSync(dir, path.join(evidence, mode), { recursive: true });
      }
      rmSync(dir, { recursive: true, force: true });
    }
  });
}
