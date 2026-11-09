// HO S2: builds whose VITE_ENVIRONMENT is not local drop the legacy local
// mock's worker (public/mockServiceWorker.js) after Vite copies public/.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("vite", () => ({ defineConfig: (config) => config }));
vi.mock("@vitejs/plugin-react-swc", () => ({
  default: () => ({ name: "vite:react-swc" }),
}));
vi.mock("vite-plugin-checker", () => ({
  default: () => ({ name: "vite-plugin-checker" }),
}));

const { MOCK_WORKER_FILE, prototypeStaticsPlugin, shipsMockWorker } =
  await import("../../../vite.config.js");

const dirs = [];
afterEach(() => {
  dirs.splice(0).forEach((d) => fs.rmSync(d, { recursive: true, force: true }));
});

function outDirWithWorker() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "msw-worker-"));
  dirs.push(dir);
  fs.writeFileSync(path.join(dir, MOCK_WORKER_FILE), "// msw");
  fs.writeFileSync(path.join(dir, "index.html"), "<html></html>");
  return dir;
}

function writeWith(env, dir) {
  const plugin = prototypeStaticsPlugin();
  plugin.configResolved({ env, root: dir });
  plugin.writeBundle({ dir });
}

describe("shipsMockWorker", () => {
  it("ships the worker only in local builds", () => {
    expect(MOCK_WORKER_FILE).toBe("mockServiceWorker.js");
    expect(shipsMockWorker({ VITE_ENVIRONMENT: "local" })).toBe(true);
    expect(
      shipsMockWorker({
        VITE_ENVIRONMENT: "local",
        VITE_PROTOTYPE_MODE: "true",
      }),
    ).toBe(true);
    expect(shipsMockWorker({ VITE_ENVIRONMENT: "production" })).toBe(false);
    expect(shipsMockWorker({ VITE_ENVIRONMENT: "development" })).toBe(false);
    expect(shipsMockWorker({})).toBe(false);
    expect(shipsMockWorker()).toBe(false);
  });
});

describe("prototypeStaticsPlugin writeBundle", () => {
  it("removes the worker from a production build and keeps everything else", () => {
    const dir = outDirWithWorker();
    writeWith({ VITE_ENVIRONMENT: "production" }, dir);
    expect(fs.existsSync(path.join(dir, MOCK_WORKER_FILE))).toBe(false);
    expect(fs.existsSync(path.join(dir, "index.html"))).toBe(true);
  });

  it("removes it from a build with no environment set", () => {
    const dir = outDirWithWorker();
    writeWith({}, dir);
    expect(fs.existsSync(path.join(dir, MOCK_WORKER_FILE))).toBe(false);
  });

  it("keeps it in local builds, with or without prototype mode", () => {
    const local = outDirWithWorker();
    writeWith({ VITE_ENVIRONMENT: "local" }, local);
    expect(fs.existsSync(path.join(local, MOCK_WORKER_FILE))).toBe(true);
    const proto = outDirWithWorker();
    writeWith(
      { VITE_ENVIRONMENT: "local", VITE_PROTOTYPE_MODE: "true" },
      proto,
    );
    expect(fs.existsSync(path.join(proto, MOCK_WORKER_FILE))).toBe(true);
  });

  it("does nothing when the out dir has no worker", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "msw-worker-"));
    dirs.push(dir);
    expect(() =>
      writeWith({ VITE_ENVIRONMENT: "production" }, dir),
    ).not.toThrow();
  });
});
