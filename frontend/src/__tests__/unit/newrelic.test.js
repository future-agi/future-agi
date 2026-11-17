import { describe, it, expect, vi, afterEach } from "vitest";

// The config is read from import.meta.env when the module loads.
const load = async (env) => {
  vi.resetModules();
  Object.entries(env).forEach(([key, value]) => vi.stubEnv(key, value));
  return import("src/newrelic");
};

afterEach(() => vi.unstubAllEnvs());

describe("newRelicTracing", () => {
  it("starts no agent in a build without New Relic keys", async () => {
    const { newRelicTracing } = await load({
      VITE_NEWRELIC_LICENSE_KEY: "",
      VITE_NEWRELIC_PROD_APP_ID: "",
      VITE_NEWRELIC_DEV_APP_ID: "",
    });

    expect(newRelicTracing("production")).toBeNull();
    expect(newRelicTracing("dev")).toBeNull();
  });

  it("needs the application ID as well as the key", async () => {
    const { newRelicTracing } = await load({
      VITE_NEWRELIC_LICENSE_KEY: "NRJS-test",
      VITE_NEWRELIC_PROD_APP_ID: "",
    });

    expect(newRelicTracing("production")).toBeNull();
  });

  it("uses the environment's config when the build has its keys", async () => {
    const { newRelicTracing, prodTracing } = await load({
      VITE_NEWRELIC_LICENSE_KEY: "NRJS-test",
      VITE_NEWRELIC_PROD_APP_ID: "123",
    });

    expect(newRelicTracing("production")).toBe(prodTracing);
    expect(newRelicTracing("production").info.licenseKey).toBe("NRJS-test");
    expect(newRelicTracing("staging")).toBeNull();
  });
});
