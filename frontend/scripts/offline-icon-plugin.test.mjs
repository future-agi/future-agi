import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { offlineIconPlugin } from "./offline-icon-plugin.mjs";
import { backendIconConsumers } from "./backend-icon-consumers.mjs";

const consumer = "virtual:offline-icons/consumer/";
const icon = "virtual:offline-icons/icon/";
async function fixture(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "offline-icon-test-"));
  const registryFile = path.join(
    root,
    "src/components/iconify/offline-icons.json",
  );
  const countriesFile = path.join(root, "src/assets/data/countries.js");
  const write = (value) =>
    fs.writeFileSync(registryFile, JSON.stringify(value));
  try {
    fs.mkdirSync(path.dirname(registryFile), { recursive: true });
    fs.mkdirSync(path.dirname(countriesFile), { recursive: true });
    write({ "mdi:check": { body: "<path/>" } });
    fs.writeFileSync(countriesFile, "export default [];");
    const plugin = offlineIconPlugin();
    plugin.error = (message) => {
      throw new Error(message);
    };
    plugin.configResolved({ root, mode: "development", command: "serve" });
    plugin.buildStart();
    await run({ root, plugin, registryFile, countriesFile, write });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("shared glyph chunks cannot absorb a lazy renderer or the registry", () =>
  fixture(({ root, plugin }) => {
    const shared = "\u0000" + icon + "mdi:check";
    const exclusive = "\u0000" + icon + "mdi:star";
    const runtime = path.join(root, "src/components/iconify/registry.js");
    plugin.getModuleIds = () => [shared, exclusive, runtime];
    plugin.getModuleInfo = (id) => ({
      importers: id === shared ? ["drawer", "renderer"] : ["renderer"],
    });
    const options = { manualChunks: { vendor: ["react"] } };
    const result = plugin.outputOptions(options);
    assert.deepEqual(result.manualChunks, {
      vendor: ["react"],
      "offline-icon-runtime": [runtime],
      "offline-icon-mdi_check": [shared],
    });
    assert.deepEqual(options, { manualChunks: { vendor: ["react"] } });
    const context = {};
    const functional = plugin.outputOptions({
      manualChunks: (id, ctx) => {
        assert.equal(ctx, context);
        return id === "react" ? "vendor" : undefined;
      },
    });
    assert.equal(
      functional.manualChunks(shared, context),
      "offline-icon-mdi_check",
    );
    assert.equal(
      functional.manualChunks(runtime, context),
      "offline-icon-runtime",
    );
    assert.equal(functional.manualChunks("react", context), "vendor");
    assert.equal(functional.manualChunks(exclusive, context), undefined);
    plugin.getModuleIds = () => [exclusive];
    assert.equal(plugin.outputOptions(options), null);
  }));

test("Rollup emits shared registrations without importing another lazy renderer", () =>
  fixture(async ({ root, plugin }) => {
    const { rollup } = await import("rollup");
    const runtime = path.join(root, "src/components/iconify/registry.js");
    const modules = {
      entry: `export { registered } from ${JSON.stringify(runtime)};
        export const drawer = () => import('drawer');
        export const renderer = () => import('renderer');`,
      drawer: `import '${icon}mdi:check'; export const view = 'drawer';`,
      renderer: `import '${icon}mdi:check'; import 'heavy'; export const view = 'renderer';`,
      heavy: "globalThis.heavyRendererLoaded = true;",
      [runtime]: `export const registered = [];
        export function registerOfflineIcon(name) { registered.push(name); }`,
    };
    const build = await rollup({
      input: "entry",
      plugins: [
        {
          name: plugin.name,
          resolveId: plugin.resolveId,
          outputOptions: plugin.outputOptions,
          load: (id) => plugin.load(id),
        },
        {
          name: "fixture-source",
          resolveId: (id) => (Object.hasOwn(modules, id) ? id : null),
          load: (id) => modules[id] ?? null,
        },
      ],
    });
    try {
      const { output } = await build.generate({
        format: "es",
        manualChunks: {},
      });
      const chunks = output.filter((row) => row.type === "chunk");
      const closure = (id, seen = new Set()) => {
        if (seen.has(id)) return seen;
        seen.add(id);
        chunks
          .find((row) => row.fileName === id)
          .imports.forEach((dep) => closure(dep, seen));
        return seen;
      };
      const glyph = chunks.find((row) =>
        Object.keys(row.modules).includes("\u0000" + icon + "mdi:check"),
      );
      const drawer = chunks.find((row) => row.facadeModuleId === "drawer");
      const entry = chunks.find((row) => row.isEntry);
      assert.ok(closure(drawer.fileName).has(glyph.fileName));
      assert.equal(closure(entry.fileName).has(glyph.fileName), false);
      assert.deepEqual(Object.keys(glyph.modules), [
        "\u0000" + icon + "mdi:check",
      ]);
      for (const file of closure(drawer.fileName)) {
        const ids = Object.keys(
          chunks.find((row) => row.fileName === file).modules,
        );
        assert.equal(ids.includes("heavy") || ids.includes("renderer"), false);
      }
    } finally {
      await build.close();
    }
  }));

test("coverage records final chunk filenames and import edges", () =>
  fixture(({ root, plugin }) => {
    const asset = {
      type: "asset",
      fileName: "offline-icon-coverage.json",
      source: JSON.stringify({
        chunks: [{ file: "old.js" }],
        consumers: [],
        missingData: [],
      }),
    };
    const chunk = {
      type: "chunk",
      fileName: "final.js",
      facadeModuleId: path.join(root, "src/main.jsx"),
      isEntry: true,
      isDynamicEntry: false,
      imports: ["shared-final.js"],
      dynamicImports: [],
      modules: { ["\u0000" + icon + "mdi:check"]: {} },
    };
    plugin.writeBundle(
      { dir: root },
      { "offline-icon-coverage.json": asset, "final.js": chunk },
    );
    const saved = JSON.parse(
      fs.readFileSync(path.join(root, asset.fileName), "utf8"),
    );
    assert.equal(saved.chunks[0].file, "final.js");
    assert.deepEqual(saved.chunks[0].imports, ["shared-final.js"]);
    assert.deepEqual(saved.chunks[0].icons, ["mdi:check"]);
    assert.deepEqual(saved.missingData, []);
  }));

test("frontend-only builds use the checked-in backend map without a Python tree", () =>
  fixture(({ plugin, write }) => {
    write(
      Object.fromEntries(
        Object.keys(backendIconConsumers).map((name) => [
          name,
          { body: "<path/>" },
        ]),
      ),
    );
    plugin.buildStart();
    const emitted = [];
    plugin.emitFile = (asset) => emitted.push(asset);
    plugin.generateBundle({}, {});
    const coverage = JSON.parse(emitted[0].source);
    assert.deepEqual(coverage.missingData, []);
    assert.deepEqual(
      coverage.sourceOnlyNames,
      Object.keys(backendIconConsumers).sort(),
    );
  }));

test("regenerated data and country lists invalidate stale dev registration", () =>
  fixture(({ plugin, registryFile, countriesFile, write }) => {
    const watched = [],
      messages = [];
    let invalidations = 0;
    const server = {
      watcher: { add: (files) => watched.push(...files) },
      moduleGraph: { invalidateAll: () => invalidations++ },
      ws: { send: (message) => messages.push(message) },
    };
    plugin.configureServer(server);
    assert.deepEqual(watched, [registryFile, countriesFile]);
    write({ "mdi:check": { body: "<circle/>" } });
    for (const file of watched)
      assert.deepEqual(plugin.handleHotUpdate({ file, server }), []);
    assert.equal(invalidations, 2);
    assert.deepEqual(messages, [
      { type: "full-reload" },
      { type: "full-reload" },
    ]);
    assert.match(
      plugin.load(plugin.resolveId(icon + "mdi:check").id),
      /<circle\/>/,
    );
  }));

test("watch builds reread glyph data rather than keeping config-time bytes", () =>
  fixture(({ plugin, write }) => {
    write({ "mdi:check": { body: "<circle/>" } });
    plugin.buildStart();
    assert.match(
      plugin.load(plugin.resolveId(icon + "mdi:check").id),
      /<circle\/>/,
    );
  }));

test("consumer edits invalidate their registration module before HMR", () =>
  fixture(({ root, plugin }) => {
    const relative = "src/view.jsx",
      file = path.join(root, relative);
    const registration = {
      id: plugin.resolveId(consumer + encodeURIComponent(relative)).id,
    };
    fs.writeFileSync(file, 'export const name = "mdi:check";');
    plugin.transform(fs.readFileSync(file, "utf8"), file);
    assert.match(plugin.load(registration.id), /mdi:check/);
    fs.writeFileSync(file, 'export const name = "";');
    const invalidated = [];
    const result = plugin.handleHotUpdate({
      file,
      modules: ["consumer"],
      server: {
        moduleGraph: {
          getModuleById: (id) => (id === registration.id ? registration : null),
          invalidateModule: (module) => invalidated.push(module),
        },
      },
    });
    assert.deepEqual(result, ["consumer", registration]);
    assert.deepEqual(invalidated, [registration]);
    assert.equal(plugin.load(registration.id), "");
  }));

test("only source files can be virtual consumers", () =>
  fixture(({ plugin }) => {
    for (const file of [
      "../outside.jsx",
      "src/../../outside.jsx",
      "package.json",
    ]) {
      assert.throws(
        () =>
          plugin.load(plugin.resolveId(consumer + encodeURIComponent(file)).id),
        /Invalid offline icon consumer/,
      );
    }
  }));

test("unregistered literal data fails closed at build time", () =>
  fixture(({ root, plugin }) => {
    assert.throws(
      () =>
        plugin.transform(
          'export const name = "mdi:star";',
          path.join(root, "src/view.jsx"),
        ),
      /Offline icon data missing: mdi:star/,
    );
  }));

test("stories and prototype source files use the same synchronous transform", () =>
  fixture(({ root, plugin }) => {
    for (const file of ["src/view.stories.jsx", "src/prototype/view.jsx"]) {
      const result = plugin.transform(
        'export const name = "mdi:check";',
        path.join(root, file),
      );
      assert.ok(result.code.startsWith(`import "${consumer}`));
      const id = plugin.resolveId(consumer + encodeURIComponent(file)).id;
      assert.equal(plugin.load(id), `import "${icon}mdi:check";`);
    }
  }));
