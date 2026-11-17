import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

// Vite and its compiler plugins load esbuild / swc; only the pure helpers of
// the prototype-statics plugin matter here.
vi.mock("vite", () => ({ defineConfig: (config) => config }));
vi.mock("@vitejs/plugin-react-swc", () => ({
  default: () => ({ name: "vite:react-swc" }),
}));
vi.mock("vite-plugin-checker", () => ({
  default: () => ({ name: "vite-plugin-checker" }),
}));

const {
  PROTOTYPE_PUBLIC_DIR,
  byteRangeOf,
  default: config,
  filesUnder,
  prototypeBuildFromEnv,
  prototypeStaticFileOf,
  prototypeStaticsPlugin,
} = await import("../../../vite.config.js");

let dir;
beforeAll(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "proto-statics-"));
  fs.mkdirSync(path.join(dir, "mock", "multimodal"), { recursive: true });
  fs.writeFileSync(path.join(dir, "prototype-mock-sw.js"), "// sw");
  fs.writeFileSync(path.join(dir, "mock", "multimodal", "call.wav"), "RIFF");
});
afterAll(() => fs.rmSync(dir, { recursive: true, force: true }));

describe("prototypeBuildFromEnv", () => {
  it("is on only for a local build with prototype mode on", () => {
    expect(
      prototypeBuildFromEnv({
        VITE_ENVIRONMENT: "local",
        VITE_PROTOTYPE_MODE: "true",
      }),
    ).toBe(true);
    expect(
      prototypeBuildFromEnv({
        VITE_ENVIRONMENT: "local",
        VITE_PROTOTYPE_MODE: "",
      }),
    ).toBe(false);
    expect(
      prototypeBuildFromEnv({
        VITE_ENVIRONMENT: "production",
        VITE_PROTOTYPE_MODE: "true",
      }),
    ).toBe(false);
    expect(prototypeBuildFromEnv()).toBe(false);
  });
});

describe("filesUnder", () => {
  it("lists every file, relative and sorted", () => {
    expect(filesUnder(dir)).toEqual([
      "mock/multimodal/call.wav",
      "prototype-mock-sw.js",
    ]);
  });

  it("is empty for a folder that does not exist", () => {
    expect(filesUnder(path.join(dir, "nope"))).toEqual([]);
  });
});

describe("prototypeStaticFileOf", () => {
  it("maps a root URL to the file under the folder, ignoring query and hash", () => {
    expect(prototypeStaticFileOf(dir, "/mock/multimodal/call.wav?v=1#t")).toBe(
      path.join(path.resolve(dir), "mock", "multimodal", "call.wav"),
    );
    expect(prototypeStaticFileOf(dir, "/prototype-mock-sw.js")).toBe(
      path.join(path.resolve(dir), "prototype-mock-sw.js"),
    );
  });

  it("refuses paths that leave the folder or are not root URLs", () => {
    expect(prototypeStaticFileOf(dir, "/../etc/passwd")).toBeNull();
    expect(prototypeStaticFileOf(dir, "/%2e%2e/secret")).toBeNull();
    expect(prototypeStaticFileOf(dir, "mock/x")).toBeNull();
    expect(prototypeStaticFileOf(dir, "/%E0%A4%A")).toBeNull();
    expect(prototypeStaticFileOf(dir, undefined)).toBeNull();
  });
});

describe("byteRangeOf", () => {
  it("reads open, closed and suffix ranges within the size", () => {
    expect(byteRangeOf("bytes=0-", 100)).toEqual({ start: 0, end: 99 });
    expect(byteRangeOf("bytes=10-19", 100)).toEqual({ start: 10, end: 19 });
    expect(byteRangeOf("bytes=90-500", 100)).toEqual({ start: 90, end: 99 });
    expect(byteRangeOf("bytes=-10", 100)).toEqual({ start: 90, end: 99 });
  });

  it("is null for no header, a malformed one or an unsatisfiable range", () => {
    expect(byteRangeOf(undefined, 100)).toBeNull();
    expect(byteRangeOf("bytes=-", 100)).toBeNull();
    expect(byteRangeOf("items=0-1", 100)).toBeNull();
    expect(byteRangeOf("bytes=200-", 100)).toBeNull();
    expect(byteRangeOf("bytes=20-10", 100)).toBeNull();
  });
});

describe("prototypeStaticsPlugin", () => {
  const emitted = (env) => {
    const plugin = prototypeStaticsPlugin();
    plugin.configResolved({ env, root: dir });
    const files = [];
    plugin.generateBundle.call({ emitFile: (f) => files.push(f.fileName) });
    return files;
  };

  it("is registered in the Vite config", () => {
    expect(config.plugins.flat().map((p) => p?.name)).toContain(
      "prototype-statics",
    );
  });

  it("emits nothing when prototype mode is off", () => {
    expect(
      emitted({ VITE_ENVIRONMENT: "production", VITE_PROTOTYPE_MODE: "" }),
    ).toEqual([]);
  });

  it("emits the prototype folder at its root URLs when prototype mode is on", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "proto-root-"));
    try {
      fs.cpSync(dir, path.join(root, PROTOTYPE_PUBLIC_DIR), {
        recursive: true,
      });
      const plugin = prototypeStaticsPlugin();
      plugin.configResolved({
        env: { VITE_ENVIRONMENT: "local", VITE_PROTOTYPE_MODE: "true" },
        root,
      });
      const files = [];
      plugin.generateBundle.call({ emitFile: (f) => files.push(f.fileName) });
      expect(files).toEqual([
        "mock/multimodal/call.wav",
        "prototype-mock-sw.js",
      ]);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
