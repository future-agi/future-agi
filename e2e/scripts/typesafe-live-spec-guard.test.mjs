import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

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
  for (const m of code.matchAll(/owner\.api\.(post|put|patch|get)\(([\s\S]*?)\);/g)) {
    assert.doesNotMatch(m[2], /TYPESAFE_KEY/, "a Playwright request carries the TypeSafe key");
  }
  assert.match(code, /http\(E2E\.apiUrl, 'POST', `\/agentcc\/provider-credentials\/\$\{String\(typesafe\.id\)\}\/rotate\/`/);
});

test("trace, screenshots and video stay off", () => {
  assert.match(code, /test\.use\(\{ trace: 'off', screenshot: 'off', video: 'off' \}\)/);
});

test("a run stops when the real key did not reach the gateway", () => {
  assert.match(code, /if \(typesafe && !rotate\.ok\) throw new Error\(/);
  assert.match(code, /gatewayHoldsKey\(configs, owner\.organizationId, PROVIDER, TYPESAFE_KEY\)/);
});
