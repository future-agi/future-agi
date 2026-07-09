import { describe, it, expect } from "vitest";
import {
  formatProviderName,
  formatProviderIdentifier,
} from "./formatProviderName";

describe("formatProviderName", () => {
  // --- display_name priority ---
  it("returns display_name when present and non-empty", () => {
    expect(formatProviderName({ display_name: "My OpenAI Key" })).toBe(
      "My OpenAI Key",
    );
  });

  it("trims whitespace from display_name", () => {
    expect(formatProviderName({ display_name: "  My Key  " })).toBe("My Key");
  });

  it("ignores empty display_name and falls back to identifier", () => {
    expect(formatProviderName({ display_name: "", name: "openai" })).toBe(
      "OpenAI",
    );
  });

  it("ignores whitespace-only display_name", () => {
    expect(formatProviderName({ display_name: "   ", name: "openai" })).toBe(
      "OpenAI",
    );
  });

  // --- Known provider identifiers ---
  it("maps known provider names to display names", () => {
    const cases = {
      openai: "OpenAI",
      anthropic: "Anthropic",
      groq: "Groq",
      cohere: "Cohere",
      mistral: "Mistral AI",
      together: "Together AI",
      together_ai: "Together AI",
      perplexity: "Perplexity",
      deepseek: "DeepSeek",
      fireworks: "Fireworks AI",
      cerebras: "Cerebras",
      deepinfra: "DeepInfra",
      huggingface: "Hugging Face",
      azure: "Azure OpenAI",
      bedrock: "AWS Bedrock",
      gemini: "Google Gemini",
      google: "Google (Gemini)",
      openrouter: "OpenRouter",
      xai: "xAI",
      custom: "Custom / Self-hosted",
    };

    Object.entries(cases).forEach(([name, expected]) => {
      expect(formatProviderName({ name }), `provider "${name}"`).toBe(expected);
    });
  });

  it("uses provider_name when name is absent", () => {
    expect(formatProviderName({ provider_name: "openai" })).toBe("OpenAI");
  });

  it("uses id when name and provider_name are absent", () => {
    expect(formatProviderName({ id: "groq" })).toBe("Groq");
  });

  // --- Unknown provider identifiers ---
  it("title-cases unknown provider identifiers with underscores", () => {
    expect(formatProviderName({ name: "my_custom_provider" })).toBe(
      "My Custom Provider",
    );
  });

  it("title-cases unknown provider identifiers with hyphens", () => {
    expect(formatProviderName({ name: "my-custom-provider" })).toBe(
      "My Custom Provider",
    );
  });

  it("title-cases simple unknown identifiers", () => {
    expect(formatProviderName({ name: "newprovider" })).toBe("Newprovider");
  });

  // --- Fallback to "Provider N" ---
  it('returns "Provider N" when no identifier exists and index is given', () => {
    expect(formatProviderName({}, 0)).toBe("Provider 1");
    expect(formatProviderName({}, 3)).toBe("Provider 4");
  });

  it('returns "Unknown Provider" when no identifier and no index', () => {
    expect(formatProviderName({})).toBe("Unknown Provider");
  });

  // --- Null/undefined inputs ---
  it("handles null provider gracefully", () => {
    expect(formatProviderName(null, 0)).toBe("Provider 1");
    expect(formatProviderName(null)).toBe("Unknown Provider");
  });

  it("handles undefined provider gracefully", () => {
    expect(formatProviderName(undefined, 0)).toBe("Provider 1");
    expect(formatProviderName(undefined)).toBe("Unknown Provider");
  });

  // --- Case insensitivity ---
  it("is case-insensitive for provider identifiers", () => {
    expect(formatProviderName({ name: "OpenAI" })).toBe("OpenAI");
    expect(formatProviderName({ name: "GROQ" })).toBe("Groq");
    expect(formatProviderName({ name: "Anthropic" })).toBe("Anthropic");
  });
});

describe("formatProviderIdentifier", () => {
  it("maps known identifiers correctly", () => {
    expect(formatProviderIdentifier("openai")).toBe("OpenAI");
    expect(formatProviderIdentifier("together_ai")).toBe("Together AI");
  });

  it("title-cases unknown identifiers", () => {
    expect(formatProviderIdentifier("some_new_provider")).toBe(
      "Some New Provider",
    );
  });

  it("handles hyphenated identifiers", () => {
    expect(formatProviderIdentifier("text-completion-openai")).toBe(
      "Text Completion Openai",
    );
  });
});
