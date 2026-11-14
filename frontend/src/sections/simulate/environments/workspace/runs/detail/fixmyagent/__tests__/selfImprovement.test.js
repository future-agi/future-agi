import { describe, expect, it } from "vitest";
import {
  SELF_IMPROVEMENT_BETA_OPEN,
  selfImprovementOpen,
} from "../selfImprovement";

describe("self-improvement beta switch", () => {
  it("is still in beta (opening it? read this first)", () => {
    // Orgs created after the TH-8602 cutoff can only use Environments, and an
    // optimization's results open under Run Simulation, which is a 404 for
    // them. Opening the beta keeps those orgs locked out via
    // selfImprovementOpen. Give the results a page under Environments first,
    // or accept that new orgs stay locked, then update this test.
    expect(SELF_IMPROVEMENT_BETA_OPEN).toBe(false);
  });
});

describe("selfImprovementOpen", () => {
  it("stays closed while the beta is closed", () => {
    expect(selfImprovementOpen({ betaOpen: false, restricted: false })).toBe(
      false,
    );
  });

  it("opens for an unrestricted org once the beta opens", () => {
    expect(selfImprovementOpen({ betaOpen: true, restricted: false })).toBe(
      true,
    );
  });

  // Its optimization results open under Run Simulation, which these orgs can't
  // reach (TH-8602), so opening the beta must not open it for them.
  it("stays closed for an org restricted to Environments even when the beta opens", () => {
    expect(selfImprovementOpen({ betaOpen: true, restricted: true })).toBe(
      false,
    );
  });
});
