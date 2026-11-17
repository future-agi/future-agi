import { describe, expect, it } from "vitest";

import {
  ARCADE_REQUIREMENTS,
  registrationHold,
  registrationVendor,
} from "../registration-copy";

describe("the registration a sign-in needs", () => {
  it("names Google, Microsoft and Arcade, and nothing else", () => {
    expect(registrationVendor("google")).toBe("Google");
    expect(registrationVendor("microsoft")).toBe("Microsoft");
    expect(registrationVendor("arcade")).toBe("Arcade");
    expect(registrationVendor("slate")).toBe(null);
    expect(registrationVendor("constructor")).toBe(null);
    expect(registrationVendor(undefined)).toBe(null);
  });

  it("says the organization's own Google or Microsoft app is required, never ours", () => {
    expect(registrationHold("Gmail", "google")).toBe(
      "Gmail requires your organization's own Google OAuth app registration; Future AGI's Google app is not used here. Add it below to connect.",
    );
    expect(registrationHold("Teams", "microsoft")).toContain(
      "your organization's own Microsoft OAuth app registration",
    );
  });

  it("states Arcade's project limits rather than promising every end user", () => {
    const text = registrationHold("Arcade gateway", "arcade");
    expect(text).toContain("requires your Arcade app registration");
    expect(text).toContain(ARCADE_REQUIREMENTS);
    expect(ARCADE_REQUIREMENTS).toContain("Tool Recommendation turned off");
    expect(ARCADE_REQUIREMENTS).toContain(
      "require each end user to be a member of your Arcade project",
    );
  });

  it("keeps the generic text for any other group", () => {
    expect(registrationHold("Slate Mail", "slate")).toBe(
      "Slate Mail signs in only through your organization's own OAuth app. Add it below to connect.",
    );
  });
});
