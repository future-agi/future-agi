import { describe, expect, it } from "vitest";
import { normalizeProviderStatus } from "./KeysHelper";

describe("normalizeProviderStatus", () => {
  it("adds camelCase aliases for canonical provider-status rows", () => {
    const row = normalizeProviderStatus({
      provider: "openai",
      display_name: "OpenAI",
      has_key: true,
      masked_key: "sk-p**********dtMA",
      logo_url: "/logo.svg",
      type: "text",
    });

    expect(row.displayName).toBe("OpenAI");
    expect(row.hasKey).toBe(true);
    expect(row.maskedKey).toBe("sk-p**********dtMA");
    expect(row.logoUrl).toBe("/logo.svg");
  });

  it("preserves existing camelCase fields", () => {
    const row = normalizeProviderStatus({
      provider: "custom",
      displayName: "Custom",
      hasKey: false,
      maskedKey: null,
      logoUrl: "/custom.svg",
    });

    expect(row.display_name).toBe("Custom");
    expect(row.hasKey).toBe(false);
    expect(row.maskedKey).toBeNull();
    expect(row.logoUrl).toBe("/custom.svg");
  });
});
