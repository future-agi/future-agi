import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import ts from "typescript";

// grepInvertPattern() leaves out every gateway profile's tagged flows but the
// declared profile's. Transpiled with the workspace's compiler, as
// state-probe.test.mjs does; each case imports fresh modules under its own env.
registerHooks({
  // lib/ imports siblings without an extension (tsconfig moduleResolution Bundler).
  resolve(specifier, context, nextResolve) {
    if (
      specifier === "./profile" &&
      /\/e2e\/lib\/quarantine\.ts/.test(context.parentURL ?? "")
    ) {
      return nextResolve("./profile.ts", context);
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (!/\/e2e\/lib\/(quarantine|profile)\.ts(\?|$)/.test(url)) return loaded;
    return {
      ...loaded,
      format: "module",
      source: ts.transpileModule(String(loaded.source), {
        compilerOptions: {
          target: ts.ScriptTarget.ES2022,
          module: ts.ModuleKind.ESNext,
        },
      }).outputText,
    };
  },
});

const KEYS = [
  "E2E_GATEWAY_PROFILE",
  "E2E_ALL_GATEWAY_PROFILES",
  "E2E_INCLUDE_QUARANTINED",
  "E2E_LIVE_LLM",
];
let n = 0;
async function patternWith(env) {
  const before = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]));
  try {
    for (const k of KEYS) delete process.env[k];
    Object.assign(
      process.env,
      { E2E_INCLUDE_QUARANTINED: "1", E2E_LIVE_LLM: "1" },
      env,
    );
    const { grepInvertPattern } = await import(
      `../lib/quarantine.ts?case=${n++}`
    );
    return grepInvertPattern();
  } finally {
    for (const k of KEYS) {
      if (before[k] === undefined) delete process.env[k];
      else process.env[k] = before[k];
    }
  }
}
// Playwright matches grep against the title with the tags appended.
const runs = (pattern, tags) =>
  !(pattern && pattern.test(`GW-E2E-0xx: a flow ${tags.join(" ")}`));

for (const [name, env, expected] of [
  [
    "stock by default",
    {},
    { untagged: true, alt: false, stock: true, future: false },
  ],
  [
    "stock declared",
    { E2E_GATEWAY_PROFILE: "stock" },
    { untagged: true, alt: false, stock: true, future: false },
  ],
  [
    "alt declared",
    { E2E_GATEWAY_PROFILE: "alt" },
    { untagged: true, alt: true, stock: false, future: false },
  ],
  [
    "the catalog's escape",
    { E2E_GATEWAY_PROFILE: "alt", E2E_ALL_GATEWAY_PROFILES: "1" },
    { untagged: true, alt: true, stock: true, future: true },
  ],
]) {
  test(`gateway profile tags: ${name}`, async () => {
    const pattern = await patternWith(env);
    assert.deepEqual(
      {
        untagged: runs(pattern, ["@flow"]),
        alt: runs(pattern, ["@flow", "@gw-profile-alt"]),
        stock: runs(pattern, ["@flow", "@gw-profile-stock"]),
        future: runs(pattern, ["@flow", "@gw-profile-alt2"]),
      },
      expected,
    );
  });
}

test("gateway profile tags: an unknown profile throws at config load", async () => {
  await assert.rejects(
    patternWith({ E2E_GATEWAY_PROFILE: "other" }),
    /E2E_GATEWAY_PROFILE="other" is not one of stock, alt/,
  );
});

test("the flow catalog lists every profile's flows", async () => {
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(
    new URL("./flow-catalog.mjs", import.meta.url),
    "utf8",
  );
  assert.match(source, /E2E_ALL_GATEWAY_PROFILES: "1"/);
});
