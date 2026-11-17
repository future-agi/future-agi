import { describe, expect, it } from "vitest";
import {
  configuredRouteCandidates,
  discoverySnippets,
  quickstartChoice,
  quickstartModel,
  quickstartSnippets,
} from "./quickstart";

const URL = "https://gateway.example.test/v1";
const provider = (overrides = {}) => ({
  id: "own-provider", pushed: true, status: "healthy", request_count: 0,
  models: ["team/model"], ...overrides,
});

describe("configured quickstart routes", () => {
  it.each([0, 1, 4])("offers exact qualified IDs at %s requests without claiming health or Chat support", (count) => {
    const candidates = configuredRouteCandidates([provider({ request_count: count })]);
    expect(candidates).toHaveLength(1);
    expect(candidates[0]).toMatchObject({
      id: "own-provider/team/model", provider: "own-provider", model: "team/model",
      health: "too_few", healthLabel: "Too few requests", compatibility: "unverified",
    });
    for (const snippet of quickstartSnippets(URL, candidates[0].id)) {
      expect(snippet.code).toContain('"own-provider/team/model"');
      expect(snippet.code).toContain("AGENTCC_API_KEY");
      expect(snippet.code).not.toContain("YOUR_MODEL");
    }
  });

  it("prefers measured health and excludes unpushed, unknown-push and explicitly unhealthy routes", () => {
    const providers = [
      provider(), provider({ id: "not-pushed", pushed: false }),
      provider({ id: "unknown-push", pushed: undefined }),
      provider({ id: "unhealthy", status: "unhealthy" }),
      provider({ id: "false-health", healthy: false }),
      provider({ id: "measured", request_count: 6 }),
    ];
    expect(configuredRouteCandidates(providers).map((route) => route.id)).toEqual([
      "measured/team/model", "own-provider/team/model",
    ]);
    expect(quickstartChoice(providers)).toMatchObject({ provider: "measured", model: "team/model" });
    expect(quickstartModel(providers)).toBe("measured/team/model");
  });

  it("keeps colliding model names distinct and never fabricates metadata from a name", () => {
    const candidates = configuredRouteCandidates([
      provider({ id: "first", models: ["shared", "looks-like-chat", "shared"] }),
      provider({ id: "second", models: ["shared"] }),
    ]);
    expect(candidates.map((route) => route.id)).toEqual([
      "first/shared", "first/looks-like-chat", "second/shared",
    ]);
    expect(candidates.every((route) => route.compatibility === "unverified")).toBe(true);
  });

  it("handles represented types without passing model objects to the route helper", () => {
    const candidates = configuredRouteCandidates([provider({ models: [
      { id: "embed", type: "embedding" }, { id: "talk", type: "chat" },
      { name: "untyped" }, null, {}, "",
    ] })]);
    expect(candidates.map((route) => route.id)).toEqual(["own-provider/talk", "own-provider/untyped"]);
    expect(candidates[0].compatibility).toBe("reported-chat");
    expect(candidates[1].compatibility).toBe("unverified");
  });

  it.each([
    { providers: undefined }, { providers: [] },
    { providers: [provider({ pushed: false })] },
    { providers: [provider({ models: [] })] },
  ])(
    "has no ready-looking model placeholder without a usable route: %j", ({ providers }) => {
      expect(configuredRouteCandidates(providers)).toEqual([]);
      expect(quickstartChoice(providers)).toBeNull();
      expect(quickstartModel(providers)).toBeNull();
    },
  );

  it("escapes an apostrophe in the cURL request body", () => {
    const model = "own-provider/team's-model";
    const curl = quickstartSnippets(URL, model)[0].code;
    expect(curl).toContain("team'\\''s-model");
    expect(quickstartSnippets(URL, model)[1].code).toContain(JSON.stringify(model));
  });
});

describe("setup and key-scoped discovery recipes", () => {
  it.each([URL, `${URL}/`, `${URL}///`])("uses exactly one version prefix at %s", (url) => {
    const [curl, python, typescript] = discoverySnippets(url);
    expect(curl.code.match(/\/v1\/models/g)).toHaveLength(1);
    expect(curl.code).not.toContain("/v1/v1");
    for (const snippet of [curl, python, typescript]) {
      expect(snippet.code).toContain("AGENTCC_API_KEY");
      expect(snippet.code).not.toContain("/chat/completions");
      expect(snippet.code).not.toContain("YOUR_MODEL");
    }
    expect(python.code).toContain("python -m pip install openai");
    expect(python.code).toContain(`base_url="${URL}"`);
    expect(python.code).toContain("client.models.list()");
    expect(typescript.code).toContain("npm install openai");
    expect(typescript.code).toContain(`baseURL: "${URL}"`);
    expect(typescript.code).toContain("client.models.list()");
  });
});
