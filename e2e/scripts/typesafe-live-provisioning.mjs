// Native pytest bridge: stdin contains only test-stack administration context
// and sanitized inventories. No raw gateway response/key is logged or written.
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';
const live = new URL('../lib/typesafe-live.ts', import.meta.url).href;
const cleanup = new URL('../lib/typesafe-cleanup.ts', import.meta.url).href;
const target = process.env.TYPESAFE_CLEANUP_LIBRARY ? pathToFileURL(process.env.TYPESAFE_CLEANUP_LIBRARY).href : live;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === './typesafe-live' && context.parentURL === cleanup) return { url: live, shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (![live, cleanup, target].includes(url)) return nextLoad(url, context);
    return { shortCircuit: true, format: 'module', source: ts.transpileModule(readFileSync(new URL(url), 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    }).outputText };
  },
});
const { createTypeSafeResources, teardownTypeSafe } = await import(target);
const { createTypeSafeCleanupIO } = await import(cleanup);
let input = '';
for await (const chunk of process.stdin) input += chunk;
const fixture = JSON.parse(input);
const io = createTypeSafeCleanupIO({ ...fixture.options, signal: new AbortController().signal });
// These inventories were queried from the REAL Django ORM after provision_key
// raised. The test has no newly created Django key to revoke; all rows predate
// the fault. Gateway reads/revocations/verification use the production adapter.
io.listProviders = async () => [];
io.listKeys = async () => fixture.backendKeys;
const resources = createTypeSafeResources([], fixture.backendKeys,
  { organizationId: fixture.options.organizationId, gatewayKeys: fixture.baselineGatewayKeys });
resources.keyNames.add(fixture.name);
for (let pass = 0; pass < 2; pass++) {
  const result = await teardownTypeSafe(resources, io);
  assert.equal(result.verdict, 'PASS', 'native orphan cleanup failed (details withheld)');
  const keys = await io.listGatewayKeys();
  assert.equal(keys.find(k => k.id === fixture.orphanId)?.status, 'revoked', 'gateway-only orphan remains active');
  assert.ok(fixture.controlIds.every(id => keys.find(k => k.id === id)?.status === 'active'), 'cleanup changed a control key');
}
process.stdout.write('Native gateway-only orphan revoked; baseline/cross-org/lookalike controls preserved; repeated cleanup PASS.\n');
