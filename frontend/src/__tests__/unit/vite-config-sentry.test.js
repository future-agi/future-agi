import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Vite and its compiler plugins load esbuild / swc, which cannot run under the
// jsdom test environment; only the real Sentry plugin matters here.
vi.mock("vite", () => ({ defineConfig: (config) => config }));
vi.mock("@vitejs/plugin-react-swc", () => ({
  default: () => ({ name: "vite:react-swc" }),
}));
vi.mock("vite-plugin-checker", () => ({
  default: () => ({ name: "vite-plugin-checker" }),
}));

// sentry-cli falls back to any credentials it can find on the machine
// (~/.sentryclirc, or a .sentryclirc in any parent directory of the build), so
// the Sentry plugin must stay off unless a build explicitly asks to upload.
const loadSentryPluginNames = async () => {
  vi.resetModules();
  const { default: config } = await import("../../../vite.config.js");
  return config.plugins
    .flat(Infinity)
    .map((plugin) => plugin?.name)
    .filter((name) => name?.startsWith("sentry-"));
};

describe("vite config Sentry source map upload", () => {
  beforeEach(() => {
    vi.stubEnv("SENTRY_UPLOAD_SOURCEMAPS", undefined);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("registers no Sentry upload plugins for a build that did not opt in", async () => {
    expect(await loadSentryPluginNames()).toEqual(["sentry-noop-plugin"]);
  });

  it("uploads only when SENTRY_UPLOAD_SOURCEMAPS is exactly 'true'", async () => {
    const { shouldUploadSentrySourceMaps } = await import(
      "../../../vite.config.js"
    );

    expect(shouldUploadSentrySourceMaps({})).toBe(false);
    expect(
      shouldUploadSentrySourceMaps({ CI: "true", SENTRY_AUTH_TOKEN: "x" }),
    ).toBe(false);
    expect(
      shouldUploadSentrySourceMaps({ SENTRY_UPLOAD_SOURCEMAPS: "1" }),
    ).toBe(false);
    expect(
      shouldUploadSentrySourceMaps({ SENTRY_UPLOAD_SOURCEMAPS: "true" }),
    ).toBe(true);
  });
});
