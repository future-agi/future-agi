import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";
import ts from "typescript";

// Use E2E's existing compiler for parameter properties unsupported by Node's
// strip-only TS loader. Import the real modules/pg.Pool; mock only HTTP below.
const modules = ["../lib/state-probe.ts", "../lib/env.ts"].map(
  (p) => new URL(p, import.meta.url).href,
);
registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (!modules.includes(url.split("?")[0])) return loaded;
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
const { StateProbe } = await import("../lib/state-probe.ts");

const source = "http://localhost:28125";
const catalog = "http://localhost:28123";
const database = "attached_catalog";
for (const [name, options, expectedHost, expectedDatabase] of [
  [
    "defaults to source host and property_catalog",
    {},
    source,
    "property_catalog",
  ],
  [
    "routes catalog separately without changing source",
    { catalogChUrl: catalog, catalogChDatabase: database },
    catalog,
    database,
  ],
]) {
  test(`probe ${name}`, async (t) => {
    // Pool connects lazily; this test never opens a database connection.
    const probe = new StateProbe({
      api: {},
      chUrl: source,
      chDatabase: "source_db",
      pgUrl: "postgresql://unused:unused@localhost:1/unused",
      ...options,
    });
    t.after(() => probe.dispose());
    const calls = [];
    t.mock.method(globalThis, "fetch", async (url, init) => {
      calls.push({ url: new URL(url), init });
      return new Response('{"value_json":"7"}\n');
    });
    const sql =
      "SELECT value_json FROM observed_attribute_values WHERE project_id={p:String}";
    assert.deepEqual(
      await probe.catalogCh(sql, { p: "project & unicode 日本" }),
      [{ value_json: "7" }],
    );
    await probe.ch("SELECT id FROM spans");
    assert.equal(calls[0].url.origin, expectedHost);
    assert.equal(calls[0].url.searchParams.get("database"), expectedDatabase);
    assert.equal(
      calls[0].url.searchParams.get("default_format"),
      "JSONEachRow",
    );
    assert.equal(
      calls[0].url.searchParams.get("param_p"),
      "project & unicode 日本",
    );
    assert.deepEqual(calls[0].init, { method: "POST", body: sql });
    assert.equal(calls[1].url.origin, source);
    assert.equal(calls[1].url.searchParams.get("database"), "source_db");
  });
}

const keys = [
  "E2E_CH_URL",
  "E2E_CH_DB",
  "E2E_CATALOG_CH_URL",
  "E2E_CATALOG_CH_DB",
];
for (const [index, [name, overrides, host, db]] of [
  ["managed defaults", {}, "http://localhost:28123", "property_catalog"],
  [
    "inherits only the source host",
    { E2E_CH_URL: source, E2E_CH_DB: "source_db" },
    source,
    "property_catalog",
  ],
  [
    "explicit catalog overrides",
    {
      E2E_CH_URL: source,
      E2E_CH_DB: "source_db",
      E2E_CATALOG_CH_URL: catalog,
      E2E_CATALOG_CH_DB: database,
    },
    catalog,
    database,
  ],
].entries()) {
  test(`env ${name}`, async (t) => {
    const before = Object.fromEntries(
      keys.map((key) => [key, process.env[key]]),
    );
    t.after(() => {
      for (const key of keys) {
        if (before[key] === undefined) delete process.env[key];
        else process.env[key] = before[key];
      }
    });
    for (const key of keys) delete process.env[key];
    Object.assign(process.env, overrides);
    const { E2E } = await import(`../lib/env.ts?case=${index}`);
    assert.equal(E2E.catalogChUrl, host);
    assert.equal(E2E.catalogChDatabase, db);
    assert.equal(E2E.chUrl, overrides.E2E_CH_URL ?? "http://localhost:28123");
    assert.equal(E2E.chDatabase, overrides.E2E_CH_DB ?? "default");
  });
}
