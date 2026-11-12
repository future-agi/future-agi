import { afterEach, describe, expect, it, vi } from "vitest";

import mainSource from "./main.jsx?raw";

vi.mock("src/routes/paths", () => ({
  paths: { dashboard: { falconAI: "/dashboard/falcon-ai" } },
}));

const originalRuntimeConfig = window.__FUTURE_AGI_CONFIG__;
const licenseName = "VITE_AG_GRID_LICENSE_KEY";
const controlPlaneName = "VITE_OMEGA_DEPLOY_CONTROL_PLANE";
const buildControlPlane = "https://build.example.invalid/omega";

async function loadConfig(runtimeConfig, buildEnv = {}) {
  vi.resetModules();
  if (runtimeConfig === undefined) delete window.__FUTURE_AGI_CONFIG__;
  else window.__FUTURE_AGI_CONFIG__ = runtimeConfig;
  vi.stubEnv(licenseName, buildEnv[licenseName]);
  vi.stubEnv(controlPlaneName, buildEnv[controlPlaneName]);
  return import("./config-global");
}

afterEach(() => {
  vi.unstubAllEnvs();
  if (originalRuntimeConfig === undefined) delete window.__FUTURE_AGI_CONFIG__;
  else window.__FUTURE_AGI_CONFIG__ = originalRuntimeConfig;
  vi.resetModules();
});

describe("AG Grid runtime licence", () => {
  it("uses the nonblank runtime licence without altering its bytes", async () => {
    const runtimeLicense = "  synthetic-runtime-licence  ";
    const config = await loadConfig(
      { [licenseName]: runtimeLicense },
      { [licenseName]: "synthetic-build-licence" },
    );
    expect(config.AG_GRID_LICENSE_KEY).toBe(runtimeLicense);
  });

  it.each([undefined, null, "", "   ", "\t\n"])(
    "falls back to the build licence when runtime is %j",
    async (runtimeValue) => {
      const config = await loadConfig(
        { [licenseName]: runtimeValue },
        { [licenseName]: "synthetic-build-licence" },
      );
      expect(config.AG_GRID_LICENSE_KEY).toBe("synthetic-build-licence");
    },
  );

  it("uses the build licence when no runtime bootstrap exists", async () => {
    const config = await loadConfig(undefined, {
      [licenseName]: "synthetic-build-licence",
    });
    expect(config.AG_GRID_LICENSE_KEY).toBe("synthetic-build-licence");
  });

  it("preserves an unset licence and an explicitly empty build licence", async () => {
    expect((await loadConfig(undefined)).AG_GRID_LICENSE_KEY).toBeUndefined();
    expect(
      (await loadConfig({}, { [licenseName]: "" })).AG_GRID_LICENSE_KEY,
    ).toBe("");
  });

  it("passes the resolved configuration to LicenseManager in the real entrypoint", () => {
    expect(mainSource).toMatch(
      /import\s*\{[^}]*\bAG_GRID_LICENSE_KEY\b[^}]*\}\s*from\s*["']\.\/config-global["']/,
    );
    expect(mainSource).toMatch(
      /LicenseManager\.setLicenseKey\(AG_GRID_LICENSE_KEY\)/,
    );
    expect(mainSource).not.toContain("import.meta.env.VITE_AG_GRID_LICENSE_KEY");
  });
});

describe("Omega deploy runtime control plane", () => {
  it.each([
    "https://runtime.example.invalid/omega",
    "http://localhost:8787",
  ])("uses the runtime http(s) URL %s ahead of the build URL", async (url) => {
    const config = await loadConfig(
      { [controlPlaneName]: `  ${url}  ` },
      { [controlPlaneName]: buildControlPlane },
    );
    expect(config.OMEGA_DEPLOY_CONTROL_PLANE).toBe(url);
  });

  it.each([undefined, null, "", "   ", "\t\n"])(
    "falls back to the build URL when runtime is %j",
    async (runtimeValue) => {
      const config = await loadConfig(
        { [controlPlaneName]: runtimeValue },
        { [controlPlaneName]: buildControlPlane },
      );
      expect(config.OMEGA_DEPLOY_CONTROL_PLANE).toBe(buildControlPlane);
    },
  );

  it("preserves the build URL without runtime configuration", async () => {
    const config = await loadConfig(undefined, {
      [controlPlaneName]: buildControlPlane,
    });
    expect(config.OMEGA_DEPLOY_CONTROL_PLANE).toBe(buildControlPlane);
  });

  it.each([
    "1",
    "true",
    "not-a-url",
    "/relative/path",
    "//example.invalid",
    "https://",
    "http:example.invalid",
    "ftp://example.invalid",
    // eslint-disable-next-line no-script-url -- refusal case
    "javascript:alert(1)",
  ])("ignores invalid runtime/build control plane %s", async (value) => {
    // A nonblank invalid runtime value must not resurrect a compiled endpoint.
    const runtimeConfig = await loadConfig(
      { [controlPlaneName]: value },
      { [controlPlaneName]: buildControlPlane },
    );
    expect(runtimeConfig.OMEGA_DEPLOY_CONTROL_PLANE).toBe("");
    const buildConfig = await loadConfig({}, { [controlPlaneName]: value });
    expect(buildConfig.OMEGA_DEPLOY_CONTROL_PLANE).toBe("");
  });

  it("keeps the optional direct deploy command unset by default", async () => {
    expect((await loadConfig(undefined)).OMEGA_DEPLOY_CONTROL_PLANE).toBe("");
    expect(
      (await loadConfig({ [controlPlaneName]: " " })).OMEGA_DEPLOY_CONTROL_PLANE,
    ).toBe("");
  });
});
