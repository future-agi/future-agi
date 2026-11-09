import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { test } from "node:test";
import { offlineVendorPlugin } from "./offline-vendor-plugin.mjs";
const require = createRequire(import.meta.url);
const grid = path.join(
  path.dirname(require.resolve("ag-grid-community")),
  "main.esm.mjs",
);
const monaco = path.join(
  path.dirname(require.resolve("@monaco-editor/loader/package.json")),
  "lib/es/config/index.js",
);

test("installed vendor defaults are red before the transform and absent after it", () => {
  const plugin = offlineVendorPlugin();
  const before = fs.readFileSync(grid, "utf8");
  assert.match(before, /fonts\.googleapis\.com/);
  const after = plugin.transform(before, grid).code;
  assert.doesNotMatch(after, /fonts\.googleapis\.com|googleFontsDomain/);
  const loader = after.match(/var loadGoogleFont = async \(\) => \{\};/)[0];
  const context = vm.createContext({});
  vm.runInContext(loader, context);
  // Even an accidental future loadThemeGoogleFonts:true cannot inject a request.
  assert.doesNotThrow(() => context.loadGoogleFont("Inter"));
  // All other grid code, including theme calculations, is byte-preserved.
  assert.equal(
    after.split("var loadGoogleFont")[0],
    before.split("var loadGoogleFont")[0],
  );
  assert.equal(
    after.split(
      "// packages/ag-grid-community/src/agStack/core/agContext.ts",
    )[1],
    before.split(
      "// packages/ag-grid-community/src/agStack/core/agContext.ts",
    )[1],
  );
  const config = fs.readFileSync(monaco, "utf8");
  assert.match(config, /cdn\.jsdelivr\.net/);
  const local = plugin.transform(config, monaco).code;
  assert.doesNotMatch(local, /cdn\.jsdelivr\.net/);
  assert.match(local, /vs: '\/assets\/monaco\/vs'/);
});
test("vendor drift fails closed; unrelated modules are never rewritten", () => {
  const plugin = offlineVendorPlugin();
  assert.throws(
    () => plugin.transform("var changed = true", grid),
    /contract changed/,
  );
  assert.throws(
    () => plugin.transform("var changed = true", monaco),
    /contract changed/,
  );
  assert.equal(
    plugin.transform(
      'const url="https://cdn.jsdelivr.net/x"',
      "/src/user-code.js",
    ),
    null,
  );
  assert.equal(
    plugin.transform(
      fs.readFileSync(grid, "utf8"),
      "/src/ag-grid-community/main.esm.mjs",
    ),
    null,
  );
});
