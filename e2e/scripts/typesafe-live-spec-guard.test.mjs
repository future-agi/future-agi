import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { assertNoTypeSafeKey, ownerApiCalls } from './typesafe-live-spec-guard.mjs';

// The live TypeSafe harness is skipped in CI (it needs a key), so nothing ran
// its source there. These checks fail in CI if a change brings back what kept
// the key out of Playwright's reports, or lets a run go on with the
// dashboard's placeholder key.
const spec = readFileSync(new URL("../harness/typesafe-live.spec.ts", import.meta.url), "utf8");
const code = spec.split("\n").filter((line) => !line.trim().startsWith("//")).join("\n");

test("no key is typed into the page", () => {
  // Playwright titles an input step with the value typed.
  for (const m of code.matchAll(/\.(fill|type|pressSequentially|press|setInputFiles)\(([^)]*)\)/g)) {
    assert.doesNotMatch(m[2], /TYPESAFE_KEY|api_?key|\.key\b|secret/i, `${m[0]} types a key`);
  }
  assert.match(code, /getByLabel\('API Key'\)\.fill\(`ts-placeholder-\$\{suffix\}`\)/);
});

test("no step title or Playwright request carries the TypeSafe key", () => {
  for (const m of code.matchAll(/step\(([^,]*),/g)) {
    assert.doesNotMatch(m[1], /TYPESAFE_KEY/, `step title ${m[1]} records the key`);
  }
  // The rotate call carrying the key goes through Node's fetch (http()), never
  // through the owner's Playwright request context.
  assert.equal(ownerApiCalls(spec).length, 10, 'update the per-call-shape fixtures when adding request sites');
  assertNoTypeSafeKey(spec);
  assert.match(code, /http\(E2E\.apiUrl, 'POST', `\/agentcc\/provider-credentials\/\$\{String\(typesafe\.id\)\}\/rotate\/`/);
});

test("trace, screenshots and video stay off", () => {
  assert.match(code, /test\.use\(\{ trace: 'off', screenshot: 'off', video: 'off' \}\)/);
});

test("a run stops when the real key did not reach the gateway", () => {
  assert.match(code, /if \(typesafe && !rotate\.ok\) throw new Error\(/);
  assert.match(code, /gatewayHoldsKey\(configs, owner\.organizationId, PROVIDER, TYPESAFE_KEY\)/);
});

test('the browser spec wires cleanup, ownership tracking and independent reporting', () => {
  assert.match(code, /await typeSafeLifecycle\.run\(/);
  assert.match(code, /await typeSafeLifecycleFixture\(use, testInfo\)/);
  assert.match(code, /timeout: TYPE_SAFE_FIXTURE_TIMEOUT/);
  assert.match(code, /teardownTypeSafe\(resources, cleanupIO\(owner, signal\)\)/);
  assert.match(code, /report\.outcome = result; writeReport\(\)/);
  assert.match(code, /resources = createTypeSafeResources\(providers, keys, \{/);
  assert.match(code, /organizationId: owner\.organizationId, gatewayKeys: await io\.listGatewayKeys\(\)/);
  assert.ok(code.indexOf('resources.limits = await cleanupIO(owner).captureLimits()') < code.indexOf('await pushConfig({ rate_limiting: { enabled: true'));
  assert.doesNotMatch(code, /pushConfig\(\{ rate_limiting: \{\}, budgets: \{\} \}/);
  assert.equal([...code.matchAll(/resources\.providerNames\.add\(/g)].length, 3);
  assert.equal([...code.matchAll(/resources\.keyNames\.add\(/g)].length, 4);
  assert.match(code, /## Independent results/);
  assert.match(code, /## Preset coverage gap/);
});

test('body cancellation gates API writes and Node fetch without cancelling cleanup', () => {
  assert.match(code, /api: typeSafeLifecycle\.guardApi\(scopeActors\.ownerA\.api\)/);
  assert.match(code, /bodySignal = typeSafeLifecycle\.signal/);
  assert.match(code, /const signal = opts\.signal \?\? bodySignal/);
  assert.match(code, /AbortSignal\.any\(\[signal, timeout\]\)/);
  assert.match(code, /credentials: \{ api_key: TYPESAFE_KEY \} \}\), drain: true/);
  const save = code.indexOf("const saved = page.waitForResponse");
  assert.ok(code.slice(0, save).trimEnd().endsWith('bodySignal?.throwIfAborted();'));
});

test('injected lifecycle failure is mock-only and occurs after a decision', () => {
  assert.match(code, /BASE_URL !== 'http:\/\/mock-llm:8080'/);
  assert.match(code, /TYPESAFE_KEY !== \['e2e', 'decision', 'typesafe', 'key'\]\.join\('-'\)/);
  assert.match(code, /if \(LIFECYCLE === 'fail'\) throw new Error\('Injected TypeSafe lifecycle failure after provider\/key creation'\)/);
  assert.ok(code.indexOf("await call('mock lifecycle decision'") < code.indexOf("if (LIFECYCLE === 'fail')"));
});
