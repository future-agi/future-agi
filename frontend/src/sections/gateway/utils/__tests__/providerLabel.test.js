import { describe, expect, it } from "vitest";

import {
  BILLING_MODE_LABEL,
  BILLING_MODE_LABELS,
  MANAGED_PROVIDER_LABEL,
  billingModeLabel,
  providerLabel,
} from "../providerLabel";

// One name per billing mode on every screen, and the
// provider a managed call names.
describe("billing mode names", () => {
  it("names the modes Future AGI credits and Own key, under Billing mode", () => {
    expect(BILLING_MODE_LABEL).toBe("Billing mode");
    expect(BILLING_MODE_LABELS).toEqual({
      managed: "Future AGI credits",
      byok: "Own key",
    });
    expect(billingModeLabel("managed")).toBe("Future AGI credits");
    expect(billingModeLabel("byok")).toBe("Own key");
  });

  it("reads a mode in any case, and keeps one it does not know", () => {
    expect(billingModeLabel("MANAGED")).toBe("Future AGI credits");
    expect(billingModeLabel("Byok")).toBe("Own key");
    expect(billingModeLabel("platform")).toBe("platform");
  });

  it("names nothing for a missing mode", () => {
    expect(billingModeLabel(undefined)).toBe("");
    expect(billingModeLabel(null)).toBe("");
    expect(billingModeLabel("")).toBe("");
  });

  it("keeps the provider label apart: a credit call's provider is Future AGI", () => {
    expect(MANAGED_PROVIDER_LABEL).toBe("Future AGI");
    expect(providerLabel("openrouter", "managed")).toBe("Future AGI");
    expect(providerLabel("openrouter", "byok")).toBe("openrouter");
  });
});
