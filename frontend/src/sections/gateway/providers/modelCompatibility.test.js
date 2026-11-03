import { describe, expect, it } from "vitest";

import {
  catalogueIsResponsesOnly,
  defaultSelectableModels,
  responsesOnlyModelSet,
} from "./modelCompatibility";

// The live Perplexity catalogue, 2026-10-06: 6 of its own plus 45 resold. Every
// one of them answers on /v1/agent and returns 400 on /chat/completions, so all
// 51 are tagged — including the perplexity/* ones, which look native and are
// the easiest to pick by mistake.
const PERPLEXITY_CATALOGUE = [
  "perplexity/sonar",
  "perplexity/glm-5.3",
  "perplexity/kimi-k3",
  "anthropic/claude-sonnet-5",
  "openai/gpt-5",
  "google/gemini-3.5-flash",
  "xai/grok-4.7",
];

describe("catalogueIsResponsesOnly", () => {
  it("recognises Perplexity by base URL, which is how it is configured", () => {
    // It has no preset, so create mode only ever has the URL to go on.
    expect(catalogueIsResponsesOnly({ baseUrl: "https://api.perplexity.ai" })).toBe(true);
    expect(catalogueIsResponsesOnly({ baseUrl: "https://api.perplexity.ai/v1" })).toBe(true);
  });

  it("recognises it by saved provider name in edit mode", () => {
    expect(catalogueIsResponsesOnly({ providerName: "perplexity" })).toBe(true);
    expect(catalogueIsResponsesOnly({ providerName: "Perplexity" })).toBe(true);
  });

  it("is false for any other provider, so their models render untagged", () => {
    expect(catalogueIsResponsesOnly({ baseUrl: "https://api.openai.com/v1" })).toBe(false);
    expect(catalogueIsResponsesOnly({ providerName: "openai" })).toBe(false);
    expect(catalogueIsResponsesOnly({})).toBe(false);
  });

  it("treats a half-typed URL as no match rather than an error", () => {
    expect(catalogueIsResponsesOnly({ baseUrl: "https:/" })).toBe(false);
    expect(catalogueIsResponsesOnly({ baseUrl: "" })).toBe(false);
  });
});

describe("responsesOnlyModelSet", () => {
  it("tags the whole Perplexity catalogue, perplexity/* included", () => {
    const tagged = responsesOnlyModelSet(PERPLEXITY_CATALOGUE, {
      baseUrl: "https://api.perplexity.ai/v1",
    });
    expect(tagged.size).toBe(PERPLEXITY_CATALOGUE.length);
    // The regression this fixes: these six looked native and so looked safe.
    expect(tagged.has("perplexity/sonar")).toBe(true);
    expect(tagged.has("perplexity/glm-5.3")).toBe(true);
    expect(tagged.has("perplexity/kimi-k3")).toBe(true);
  });

  it("tags nothing for a provider with no known rule", () => {
    expect(
      responsesOnlyModelSet(["gpt-4o", "gpt-4o-mini"], {
        baseUrl: "https://api.openai.com/v1",
      }).size,
    ).toBe(0);
  });

  it("tolerates a missing listing", () => {
    expect(responsesOnlyModelSet(undefined, { providerName: "perplexity" }).size).toBe(0);
  });
});

describe("defaultSelectableModels", () => {
  it("covers nothing for Perplexity, because none of its catalogue serves chat", () => {
    expect(
      defaultSelectableModels(PERPLEXITY_CATALOGUE, {
        baseUrl: "https://api.perplexity.ai/v1",
      }),
    ).toEqual([]);
  });

  it("covers everything for a provider with no rule", () => {
    const models = ["gpt-4o", "gpt-4o-mini"];
    expect(defaultSelectableModels(models, { providerName: "openai" })).toEqual(models);
  });
});
