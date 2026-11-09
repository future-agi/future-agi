import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import ts from "typescript";

// H5: grepInvertPattern() leaves out every gateway profile's tagged flows but the
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
  "E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS",
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
    {
      untagged: true,
      r7alt: false,
      stock: true,
      mcp: false,
      mcpCatalog: false,
      future: false,
    },
  ],
  [
    "stock declared",
    { E2E_GATEWAY_PROFILE: "stock" },
    {
      untagged: true,
      r7alt: false,
      stock: true,
      mcp: false,
      mcpCatalog: false,
      future: false,
    },
  ],
  [
    "r7alt declared",
    { E2E_GATEWAY_PROFILE: "r7alt" },
    {
      untagged: true,
      r7alt: true,
      stock: false,
      mcp: false,
      mcpCatalog: false,
      future: false,
    },
  ],
  [
    "mcp declared",
    { E2E_GATEWAY_PROFILE: "mcp" },
    {
      untagged: true,
      r7alt: false,
      stock: false,
      mcp: true,
      mcpCatalog: false,
      future: false,
    },
  ],
  [
    "mcp-catalog declared",
    { E2E_GATEWAY_PROFILE: "mcp-catalog" },
    {
      untagged: true,
      r7alt: false,
      stock: false,
      mcp: false,
      mcpCatalog: true,
      future: false,
    },
  ],
  [
    "the catalog's escape",
    { E2E_GATEWAY_PROFILE: "r7alt", E2E_ALL_GATEWAY_PROFILES: "1" },
    {
      untagged: true,
      r7alt: true,
      stock: true,
      mcp: true,
      mcpCatalog: true,
      future: true,
    },
  ],
]) {
  test(`gateway profile tags: ${name}`, async () => {
    const pattern = await patternWith(env);
    assert.deepEqual(
      {
        untagged: runs(pattern, ["@flow"]),
        r7alt: runs(pattern, ["@flow", "@gw-profile-r7alt"]),
        stock: runs(pattern, ["@flow", "@gw-profile-stock"]),
        mcp: runs(pattern, ["@flow", "@gw-profile-mcp"]),
        mcpCatalog: runs(pattern, ["@flow", "@gw-profile-mcp-catalog"]),
        future: runs(pattern, ["@flow", "@gw-profile-r7alt2"]),
      },
      expected,
    );
  });
}

// The new-org default (lib/profile.ts): a @new-org-default flow runs only on a
// stack declared with it on, exactly "1", and a @new-org-default-off flow (one
// that needs a new org to start with Future AGI models off) never runs there;
// the catalog lists both either way.
for (const [name, env, expected] of [
  [
    "undeclared",
    {},
    { untagged: true, onekey: false, off: true, longer: true },
  ],
  [
    "declared",
    { E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS: "1" },
    { untagged: true, onekey: true, off: false, longer: true },
  ],
  [
    "declared as anything but 1",
    { E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS: "true" },
    { untagged: true, onekey: false, off: true, longer: true },
  ],
  [
    "the catalog's escape",
    { E2E_ALL_GATEWAY_PROFILES: "1" },
    { untagged: true, onekey: true, off: true, longer: true },
  ],
  [
    "the catalog's escape on a declared stack",
    { E2E_ALL_GATEWAY_PROFILES: "1", E2E_MANAGED_MODELS_ON_FOR_NEW_ORGS: "1" },
    { untagged: true, onekey: true, off: true, longer: true },
  ],
]) {
  test(`new-org default tag: ${name}`, async () => {
    const pattern = await patternWith(env);
    assert.deepEqual(
      {
        untagged: runs(pattern, ["@flow"]),
        onekey: runs(pattern, ["@flow", "@new-org-default"]),
        off: runs(pattern, ["@flow", "@new-org-default-off"]),
        longer: runs(pattern, ["@flow", "@new-org-default-x"]),
      },
      expected,
    );
  });
}

test("gateway profile tags: an unknown profile throws at config load", async () => {
  await assert.rejects(
    patternWith({ E2E_GATEWAY_PROFILE: "r7" }),
    /E2E_GATEWAY_PROFILE="r7" is not one of stock, r7alt, mcp/,
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
