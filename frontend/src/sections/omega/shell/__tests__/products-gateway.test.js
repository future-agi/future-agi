import { afterEach, describe, expect, it } from "vitest";

import {
  PRODUCT,
  PRODUCTS,
  hostReturnPath,
  isGatewayPath,
  pathForProduct,
  productForPath,
  rememberHostPath,
  resetHostPathForTests,
} from "../products";

// The AI Gateway is a product of its own in the sidebar's product switcher,
// as the Omega Console is: the route decides which product is open.
describe("AI Gateway product", () => {
  afterEach(() => resetHostPathForTests());

  it("is listed after Future AGI with its own label", () => {
    const ids = PRODUCTS.map((product) => product.id);
    expect(ids[0]).toBe(PRODUCT.FUTURE_AGI);
    expect(ids).toContain(PRODUCT.AI_GATEWAY);
    expect(PRODUCTS.find((p) => p.id === PRODUCT.AI_GATEWAY).label).toBe(
      "AI Gateway",
    );
  });

  it("owns every /dashboard/gateway route and nothing else", () => {
    for (const path of [
      "/dashboard/gateway",
      "/dashboard/gateway/",
      "/dashboard/gateway/models/auto",
      "/dashboard/gateway/logs",
    ]) {
      expect(isGatewayPath(path)).toBe(true);
      expect(productForPath(path)).toBe(PRODUCT.AI_GATEWAY);
    }
    for (const path of [
      "/dashboard/gateways",
      "/dashboard/develop",
      "/dashboard/settings/billing",
      "",
    ]) {
      expect(isGatewayPath(path)).toBe(false);
      expect(productForPath(path)).toBe(PRODUCT.FUTURE_AGI);
    }
  });

  it("opens the gateway overview and returns to the last Future AGI page", () => {
    expect(pathForProduct(PRODUCT.AI_GATEWAY)).toBe("/dashboard/gateway");
    rememberHostPath("/dashboard/observe");
    rememberHostPath("/dashboard/gateway/logs");
    expect(hostReturnPath()).toBe("/dashboard/observe");
    expect(pathForProduct(PRODUCT.FUTURE_AGI)).toBe("/dashboard/observe");
  });

  it("loads in a build without the Omega routes", () => {
    // paths.js on this line has no console, studio, deployments, sandboxes
    // or hub entries; the module must still answer for them.
    expect(productForPath("/dashboard/console")).toBe(PRODUCT.OMEGA_CONSOLE);
    expect(pathForProduct(PRODUCT.OMEGA_CONSOLE)).toBe("/dashboard/console");
  });
});
