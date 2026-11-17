import { describe, expect, it } from "vitest";
import { SERVER_PROVIDERS } from "src/utils/__tests__/serverProviderList";
import { filterAndSortProviders, normalizeProviderStatus } from "./KeysHelper";

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
    expect(row.logoUrl).toBe("/icons/settings/openai.png");
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
    expect(row.logoUrl).toBe("/assets/icons/settings/AIProviders.svg");
  });
});

// A card reads "Hugging Face", and typing the provider id "huggingface" still
// finds it: a search matches the card's name or its provider id; case,
// spaces, "_" and "-" do not count.
describe("filterAndSortProviders: search by card name or provider id", () => {
  const cards = SERVER_PROVIDERS.map(normalizeProviderStatus);
  const found = (search) =>
    [
      ...filterAndSortProviders(cards, "text", search),
      ...filterAndSortProviders(cards, "json", search),
    ].map((card) => card.display_name);

  it.each([
    ["huggingface", "Hugging Face"],
    ["vertex_ai", "Google Vertex AI"],
    ["openai", "OpenAI"],
    ["Hugging-Face", "Hugging Face"],
    ["vertex ai", "Google Vertex AI"],
    ["cohere_chat", "Cohere Chat"],
  ])("typing %s finds the %s card", (search, name) => {
    expect(found(search)).toContain(name);
  });

  it("keeps every card for an empty search and none for a search that matches nothing", () => {
    expect(found("")).toHaveLength(SERVER_PROVIDERS.length);
    expect(found("no-such-provider")).toEqual([]);
  });
});
