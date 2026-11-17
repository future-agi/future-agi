import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { useOmegaPart } = vi.hoisted(() => ({ useOmegaPart: vi.fn() }));
vi.mock("src/sections/omega/shell/useOmegaPart", () => ({ useOmegaPart }));
vi.mock("src/sections/omega/shell/rollout", () => ({
  OMEGA_PART: { DEPLOY_CLI: "deploy-cli" },
}));
vi.mock("src/routes/paths", () => ({
  paths: { dashboard: { falconAI: "/dashboard/falcon-ai" } },
}));
vi.mock("../components/CopyField", () => ({
  default: ({ value, testId }) => <output data-testid={testId}>{value}</output>,
}));
vi.mock("../components/DeployIcon", () => ({ default: () => null }));
vi.mock("./SampleProject", () => ({ default: () => null }));
vi.mock("./HubProvenance", () => ({ default: () => null }));
vi.mock("../lib/bundle", () => ({
  BundleError: class BundleError extends Error {},
  bundleFolder: vi.fn(),
  readBundleArchive: vi.fn(),
  formatBytes: vi.fn(),
}));
vi.mock("../lib/format", () => ({ shortDigest: vi.fn() }));

const originalRuntimeConfig = window.__FUTURE_AGI_CONFIG__;
const controlPlaneName = "VITE_OMEGA_DEPLOY_CONTROL_PLANE";
const buildUrl = "https://build.example.invalid/omega";

async function renderSource(runtimeValue, buildValue) {
  vi.resetModules();
  window.__FUTURE_AGI_CONFIG__ = { [controlPlaneName]: runtimeValue };
  vi.stubEnv(controlPlaneName, buildValue);
  const { default: SourceStep } = await import("./SourceStep");
  render(<SourceStep onBundle={vi.fn()} />);
}

beforeEach(() => {
  useOmegaPart.mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  if (originalRuntimeConfig === undefined) delete window.__FUTURE_AGI_CONFIG__;
  else window.__FUTURE_AGI_CONFIG__ = originalRuntimeConfig;
  vi.resetModules();
});

describe("CLI source runtime control-plane command", () => {
  it("renders the runtime URL instead of the compiled URL", async () => {
    const runtimeUrl = "https://runtime.example.invalid/control-plane";
    await renderSource(runtimeUrl, buildUrl);
    fireEvent.click(screen.getByTestId("source-cli"));
    expect(screen.getByTestId("cli-deploy-command").textContent).toBe(
      `omega deploy --project . --control-plane ${runtimeUrl} --token-file <token-file>`,
    );
  });

  it.each([undefined, "", "  "])(
    "preserves the compiled URL when runtime is %j",
    async (runtimeValue) => {
      await renderSource(runtimeValue, buildUrl);
      fireEvent.click(screen.getByTestId("source-cli"));
      expect(screen.getByTestId("cli-deploy-command").textContent).toContain(
        `--control-plane ${buildUrl}`,
      );
    },
  );

  it.each(["1", "true", "not-a-url", "ftp://example.invalid", undefined])(
    "omits the direct deploy command for %j while keeping the bundle CLI",
    async (runtimeValue) => {
      await renderSource(runtimeValue, undefined);
      fireEvent.click(screen.getByTestId("source-cli"));
      expect(screen.queryByTestId("cli-deploy-command")).toBeNull();
      expect(screen.getByTestId("cli-bundle-command").textContent).toBe(
        "omega deploy bundle --project . --out omega-project.tar.gz",
      );
    },
  );

  it("does not use a build URL when the runtime URL is nonblank but invalid", async () => {
    await renderSource("true", buildUrl);
    fireEvent.click(screen.getByTestId("source-cli"));
    expect(screen.queryByTestId("cli-deploy-command")).toBeNull();
  });

  it("preserves the existing CLI rollout gate even with a runtime URL", async () => {
    useOmegaPart.mockReturnValue(false);
    await renderSource("https://runtime.example.invalid", buildUrl);
    expect(screen.queryByTestId("source-cli")).toBeNull();
    expect(screen.queryByTestId("cli-deploy-command")).toBeNull();
  });
});
