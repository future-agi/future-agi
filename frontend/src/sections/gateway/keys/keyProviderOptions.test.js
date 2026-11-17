import { describe, expect, it } from "vitest";

import { futureAgiProviderIds, keyProviderOptions } from "./keyProviderOptions";

const catalog = {
  models: [
    { id: "anthropic/claude-sonnet-5.5", provider: "openrouter" },
    { id: "openai/gpt-5-mini", provider: "openrouter" },
  ],
  providers: [{ provider_id: "openrouter", source: "live" }],
};

describe("keyProviderOptions", () => {
  it("offers the Future AGI provider after the org's own where the deployment offers it", () => {
    const { options, labelFor } = keyProviderOptions({
      providers: { openai: {}, anthropic: {} },
      futureAgiOffered: true,
      catalog,
    });
    expect(options).toEqual(["anthropic", "openai", "openrouter"]);
    expect(labelFor("openrouter")).toBe("Future AGI credits (openrouter)");
    expect(labelFor("openai")).toBe("openai");
  });

  it("offers it to an org with no provider of its own", () => {
    const { options } = keyProviderOptions({
      providers: undefined,
      futureAgiOffered: true,
      catalog,
    });
    expect(options).toEqual(["openrouter"]);
  });

  it("does not offer it where the deployment does not offer Future AGI models", () => {
    const { options, labelFor } = keyProviderOptions({
      providers: { openai: {} },
      futureAgiOffered: false,
      catalog,
    });
    expect(options).toEqual(["openai"]);
    expect(labelFor("openrouter")).toBe("openrouter");
  });

  it("keeps the org's own provider of the same id as its own", () => {
    const { options, labelFor } = keyProviderOptions({
      providers: { openrouter: {} },
      futureAgiOffered: true,
      catalog,
    });
    expect(options).toEqual(["openrouter"]);
    expect(labelFor("openrouter")).toBe("openrouter");
  });

  it("reads provider ids from the catalogue's models and sources", () => {
    expect(
      futureAgiProviderIds({
        models: [{ provider: "openrouter" }, { provider: "" }, {}],
        providers: [{ id: "anthropic-direct" }],
      }),
    ).toEqual(["openrouter", "anthropic-direct"]);
    expect(futureAgiProviderIds(undefined)).toEqual([]);
  });
});
