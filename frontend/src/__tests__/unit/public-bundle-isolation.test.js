import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { afterEach, describe, expect, it } from "vitest";
import {
  MODULE_MANIFEST,
  checkPublicBundle,
  forbiddenModule,
  publicBundleIsolationPlugin,
} from "../../../scripts/public-bundle-isolation.mjs";

const dirs = [];
afterEach(() => {
  dirs
    .splice(0)
    .forEach((dir) => fs.rmSync(dir, { recursive: true, force: true }));
});

function fixture(modules = ["src/main.jsx"]) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "public-bundle-"));
  dirs.push(dir);
  fs.mkdirSync(path.join(dir, "assets"));
  fs.writeFileSync(
    path.join(dir, "assets/app.js"),
    "export const ready = true;",
  );
  fs.writeFileSync(
    path.join(dir, MODULE_MANIFEST),
    JSON.stringify({
      version: 1,
      chunks: [{ file: "assets/app.js", modules }],
    }),
  );
  return dir;
}

describe("public bundle isolation", () => {
  it("checks origins even when minification removes every mock marker", () => {
    for (const module of [
      "src/_mock/prototype/fixtures/world/prng.js",
      "src/test-fixtures/call-value.js",
      "node_modules/msw/lib/core/index.mjs",
      "node_modules/@mswjs/interceptors/lib/browser/index.mjs",
      "node_modules/.pnpm/msw@2.0.0/node_modules/msw/lib/index.js",
      "node_modules\\msw\\lib\\index.js",
    ]) {
      expect(forbiddenModule(module)).toBe(true);
      expect(() => checkPublicBundle(fixture([module]))).toThrow();
    }
    expect(forbiddenModule("src/utils/seededRandom.js")).toBe(false);
    expect(checkPublicBundle(fixture()).chunks).toBe(1);
  });

  it("requires a populated manifest and corresponding built chunks", () => {
    const dir = fixture();
    fs.unlinkSync(path.join(dir, MODULE_MANIFEST));
    expect(() => checkPublicBundle(dir)).toThrow();
    fs.writeFileSync(
      path.join(dir, MODULE_MANIFEST),
      JSON.stringify({ version: 1, chunks: [] }),
    );
    expect(() => checkPublicBundle(dir)).toThrow();
    const missingChunk = fixture();
    fs.unlinkSync(path.join(missingChunk, "assets/app.js"));
    expect(() => checkPublicBundle(missingChunk)).toThrow();
  });

  it("rejects copied workers and prototype chunks", () => {
    for (const file of [
      "mockServiceWorker.js",
      "prototype-mock-sw.js",
      "assets/PrototypeLabel-test.js",
    ]) {
      const dir = fixture();
      fs.writeFileSync(path.join(dir, file), "// fixture");
      expect(() => checkPublicBundle(dir)).toThrow();
    }
  });

  it("removes the worker after Vite copies public files, including local builds", () => {
    const dir = fixture();
    fs.writeFileSync(path.join(dir, "mockServiceWorker.js"), "// fixture");
    const plugin = publicBundleIsolationPlugin();
    expect(plugin.apply).toBe("build");
    plugin.writeBundle({ dir });
    expect(fs.existsSync(path.join(dir, "mockServiceWorker.js"))).toBe(false);
    expect(fs.existsSync(path.join(dir, "assets/app.js"))).toBe(true);
  });

  it("fails the build itself on forbidden chunk origins", () => {
    const plugin = publicBundleIsolationPlugin();
    plugin.configResolved({ root: "/project" });
    expect(() =>
      plugin.generateBundle.call(
        {
          error(message) {
            throw new Error(message);
          },
          emitFile() {
            throw new Error("must reject before emitting");
          },
        },
        {},
        {
          app: {
            type: "chunk",
            fileName: "assets/app.js",
            modules: { "/project/node_modules/msw/index.js": {} },
          },
        },
      ),
    ).toThrow("Mock dependencies in public bundle");
  });

  it("records the final written chunk names rather than earlier hashes", () => {
    const dir = fixture();
    const plugin = publicBundleIsolationPlugin();
    plugin.configResolved({ root: dir });
    plugin.generateBundle.call(
      {
        error(message) {
          throw new Error(message);
        },
      },
      {},
      {
        app: {
          type: "chunk",
          fileName: "assets/app-before.js",
          modules: { [path.join(dir, "src/main.jsx")]: {} },
        },
      },
    );
    plugin.writeBundle(
      { dir },
      {
        app: {
          type: "chunk",
          fileName: "assets/app.js",
          modules: { [path.join(dir, "src/main.jsx")]: {} },
        },
      },
    );
    const manifest = JSON.parse(
      fs.readFileSync(path.join(dir, MODULE_MANIFEST), "utf8"),
    );
    expect(manifest.chunks[0].file).toBe("assets/app.js");
    expect(checkPublicBundle(dir).chunks).toBe(1);
  });

  it("keeps mock startup development-only and explicitly opt-in", () => {
    const main = fs.readFileSync(
      path.join(process.cwd(), "src/main.jsx"),
      "utf8",
    );
    expect(main).toMatch(
      /import\.meta\.env\.DEV\s*&&\s*import\.meta\.env\.VITE_ENVIRONMENT === "local"\s*&&\s*import\.meta\.env\.VITE_ENABLE_MSW === "true"/,
    );
    expect(main).not.toContain('import("./_mock/prototype")');
    expect(main).not.toContain("startPrototypeMode");
    expect(main).not.toContain('VITE_ENABLE_MSW !== "false"');
  });
});
