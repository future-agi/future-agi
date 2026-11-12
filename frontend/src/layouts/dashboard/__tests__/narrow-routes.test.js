import { describe, expect, it } from "vitest";

import { relaxesMinWidth } from "../narrow-routes";

// Which routes drop the dashboard's 1200 px minimum width (and, below md,
// open the sidebar as a drawer): the AI Gateway and the central billing
// pages it links to. Every other Future AGI page keeps its layout.
describe("routes that work at phone width", () => {
  it("are the gateway and central billing pages", () => {
    for (const path of [
      "/dashboard/gateway",
      "/dashboard/gateway/logs",
      "/dashboard/settings/billing",
      "/dashboard/settings/usage-summary",
    ]) {
      expect(relaxesMinWidth(path)).toBe(true);
    }
  });

  it("are not any other page", () => {
    for (const path of [
      "/dashboard/develop",
      "/dashboard/settings/profile-settings",
      "/dashboard/settings/billing-old",
      "/dashboard/gateways",
    ]) {
      expect(relaxesMinWidth(path)).toBe(false);
    }
  });
});
