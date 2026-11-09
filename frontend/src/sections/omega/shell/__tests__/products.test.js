import { afterEach, describe, expect, it } from "vitest";

import {
  PRODUCT,
  hostReturnPath,
  isAgentInfrastructurePath,
  isConsolePath,
  isOmegaShellPath,
  pathForProduct,
  productForPath,
  rememberHostPath,
  resetHostPathForTests,
} from "../products";

describe("product routing", () => {
  afterEach(() => resetHostPathForTests());

  it("derives the product from the route only", () => {
    expect(productForPath("/dashboard/console")).toBe(PRODUCT.OMEGA_CONSOLE);
    expect(productForPath("/dashboard/console/agents/a1")).toBe(
      PRODUCT.OMEGA_CONSOLE,
    );
    expect(productForPath("/dashboard/develop")).toBe(PRODUCT.FUTURE_AGI);
    expect(productForPath("/dashboard/deployments")).toBe(PRODUCT.FUTURE_AGI);
    // A prefix that is not a whole segment is not the Console.
    expect(isConsolePath("/dashboard/consoles")).toBe(false);
  });

  it("marks only the new routes as Agent infrastructure", () => {
    for (const path of [
      "/dashboard/studio",
      "/dashboard/studio/local/runs",
      "/dashboard/deployments",
      "/dashboard/deployments/new",
      "/dashboard/deployments/dep_1/logs",
      "/dashboard/sandboxes",
      "/dashboard/sandboxes/sbx_1/files",
      "/dashboard/settings/sandboxes",
      "/dashboard/hub",
      "/dashboard/hub/future-agi/first-agent/files",
      "/dashboard/hub/review/reports",
    ]) {
      expect(isAgentInfrastructurePath(path)).toBe(true);
      expect(isOmegaShellPath(path)).toBe(true);
    }
    for (const path of [
      "/dashboard/develop",
      "/dashboard/observe",
      "/dashboard/agents",
      "/dashboard/studios",
      "/dashboard/hubs",
      "/dashboard/settings/profile-settings",
      "/dashboard/settings/integrations",
      "/dashboard/settings/sandboxes-old",
    ]) {
      expect(isAgentInfrastructurePath(path)).toBe(false);
    }
    expect(isOmegaShellPath("/dashboard/console/inbox")).toBe(true);
  });

  it("returns to the last Future AGI page, never to a Console page", () => {
    expect(hostReturnPath()).toBe("/dashboard/develop");
    rememberHostPath("/dashboard/observe");
    rememberHostPath("/dashboard/console/agents");
    rememberHostPath("/auth/jwt/login");
    expect(hostReturnPath()).toBe("/dashboard/observe");
    expect(pathForProduct(PRODUCT.FUTURE_AGI)).toBe("/dashboard/observe");
    expect(pathForProduct(PRODUCT.OMEGA_CONSOLE)).toBe("/dashboard/console");
  });
});
