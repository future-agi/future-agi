import { describe, expect, it } from "vitest";

import {
  MODEL_TABS,
  modelIdFromParams,
  modelPath,
  modelTabFrom,
  splitModelId,
} from "./modelLinks";

// The public model browser (futureagi.com/models) links to these URLs from
// every model page, so they are a contract: the console model page is
// /dashboard/gateway/models/<provider>/<model>[?tab=try|api], the segments
// URL-encoded exactly as the gateway model id splits at its first "/".
describe("model page deep links", () => {
  it("splits a gateway model id at its first slash", () => {
    expect(splitModelId("anthropic/claude-haiku-5.5")).toEqual({
      provider: "anthropic",
      model: "claude-haiku-5.5",
    });
    expect(splitModelId("openrouter/x-ai/grok-5")).toEqual({
      provider: "openrouter",
      model: "x-ai/grok-5",
    });
    expect(splitModelId("gpt-4o")).toBeNull();
    expect(splitModelId("/gpt-4o")).toBeNull();
    expect(splitModelId("openai/")).toBeNull();
    expect(splitModelId("")).toBeNull();
    expect(splitModelId(undefined)).toBeNull();
  });

  it("builds the pinned URLs, encoding each segment", () => {
    expect(modelPath("anthropic/claude-haiku-5.5")).toBe(
      "/dashboard/gateway/models/anthropic/claude-haiku-5.5",
    );
    expect(modelPath("anthropic/claude-haiku-5.5", "try")).toBe(
      "/dashboard/gateway/models/anthropic/claude-haiku-5.5?tab=try",
    );
    expect(modelPath("openai/gpt-4o", "api")).toBe(
      "/dashboard/gateway/models/openai/gpt-4o?tab=api",
    );
    // The overview is the page itself, with no tab parameter.
    expect(modelPath("openai/gpt-4o", "overview")).toBe(
      "/dashboard/gateway/models/openai/gpt-4o",
    );
    expect(modelPath("meta-llama/llama-4-scout:free")).toBe(
      "/dashboard/gateway/models/meta-llama/llama-4-scout%3Afree",
    );
    expect(modelPath("~perplexity/pplx-decider-flash-latest")).toBe(
      "/dashboard/gateway/models/~perplexity/pplx-decider-flash-latest",
    );
    expect(modelPath("openrouter/x-ai/grok-5")).toBe(
      "/dashboard/gateway/models/openrouter/x-ai%2Fgrok-5",
    );
    expect(modelPath("gpt-4o")).toBeNull();
  });

  it("reads the model id back from decoded route params", () => {
    for (const id of [
      "anthropic/claude-haiku-5.5",
      "meta-llama/llama-4-scout:free",
      "openrouter/x-ai/grok-5",
      "~perplexity/pplx-decider-flash-latest",
    ]) {
      const { provider, model } = splitModelId(id);
      expect(modelIdFromParams({ provider, model })).toBe(id);
    }
    expect(modelIdFromParams({ provider: "openai" })).toBeNull();
    expect(modelIdFromParams({})).toBeNull();
  });

  it("knows the page tabs and falls back to the overview", () => {
    expect(MODEL_TABS).toEqual(["overview", "try", "api"]);
    expect(modelTabFrom("?tab=try")).toBe("try");
    expect(modelTabFrom("?tab=api")).toBe("api");
    expect(modelTabFrom("?tab=nope")).toBe("overview");
    expect(modelTabFrom("")).toBe("overview");
  });
});
