import { describe, expect, it } from "vitest";

import { ACCESS } from "./catalogRows";
import { callTarget } from "./detail/modelFacts";
import { orgModelPageId, orgServedRoutes, orgServedRow } from "./orgServed";

// The org's providers as GET /agentcc/gateways/<id>/providers/ lists them
// (views/gateway.py providers: id = provider_name, models = models_list).
const PROVIDERS = [
  {
    id: "my-vllm",
    name: "my-vllm",
    display_name: "My vLLM",
    models: ["qwen-chat", "qwen-embed"],
    pushed: true,
  },
  {
    id: "together",
    name: "together",
    display_name: "",
    models: ["meta-llama/llama-4-scout"],
    pushed: true,
  },
  {
    id: "broken",
    name: "broken",
    models: ["old-chat"],
    pushed: false,
    status_reason: "not pushed: the stored credential cannot be decrypted",
  },
];

describe("which org providers serve a model page's id", () => {
  // The gateway routes "<provider>/<model>" to that provider when it lists
  // <model> (server/handlers.go orgModelMatches; the prefix in any case).
  it("matches the provider named by the id's first segment that lists the rest", () => {
    expect(orgServedRoutes(PROVIDERS, "my-vllm/qwen-chat")).toEqual([
      {
        providerId: "my-vllm",
        providerName: "My vLLM",
        sendAs: "my-vllm/qwen-chat",
        listedAs: "qwen-chat",
        pushed: true,
        statusReason: "",
      },
    ]);
    expect(orgServedRoutes(PROVIDERS, "MY-VLLM/qwen-chat")).toHaveLength(1);
  });

  it("matches a provider that lists the whole id, slash included", () => {
    expect(orgServedRoutes(PROVIDERS, "meta-llama/llama-4-scout")).toEqual([
      {
        providerId: "together",
        providerName: "together",
        sendAs: "meta-llama/llama-4-scout",
        listedAs: "meta-llama/llama-4-scout",
        pushed: true,
        statusReason: "",
      },
    ]);
  });

  it("matches nothing for a model no provider lists, or another provider's model", () => {
    expect(orgServedRoutes(PROVIDERS, "my-vllm/llama")).toEqual([]);
    expect(orgServedRoutes(PROVIDERS, "together/qwen-chat")).toEqual([]);
    expect(orgServedRoutes(PROVIDERS, "openai/gpt-4o")).toEqual([]);
    expect(orgServedRoutes(undefined, "my-vllm/qwen-chat")).toEqual([]);
    expect(orgServedRoutes(PROVIDERS, null)).toEqual([]);
  });

  it("keeps a provider the gateway does not have yet, with the reason", () => {
    expect(orgServedRoutes(PROVIDERS, "broken/old-chat")).toEqual([
      {
        providerId: "broken",
        providerName: "broken",
        sendAs: "broken/old-chat",
        listedAs: "old-chat",
        pushed: false,
        statusReason: "not pushed: the stored credential cannot be decrypted",
      },
    ]);
  });
});

describe("the model page row for a model only the org's providers serve", () => {
  it("is an own-key model, called by the page's id so that provider serves it", () => {
    const row = orgServedRow("my-vllm/qwen-chat", PROVIDERS);
    expect(row).toMatchObject({
      id: "my-vllm/qwen-chat",
      name: "qwen-chat",
      access: ACCESS.OWN_KEY,
      accessReason: "",
      managedOn: false,
      uncatalogued: true,
    });
    expect(row.ownRoutes.map((r) => r.providerId)).toEqual(["my-vllm"]);
    expect(callTarget(row)).toEqual({
      model: "my-vllm/qwen-chat",
      managed: false,
    });
  });

  it("is null when no provider serves the id", () => {
    expect(orgServedRow("my-vllm/llama", PROVIDERS)).toBeNull();
    expect(orgServedRow("", PROVIDERS)).toBeNull();
  });
});

// The page a console link opens for a model one of the org's providers
// lists: "<provider>/<model>", which the gateway routes to that provider. A
// link is offered only where that page has a route, so it never lands on
// "No provider of your organization serves ...".
describe("the model page id for a model an org provider lists", () => {
  it("is the provider id and the model it lists", () => {
    expect(orgModelPageId(PROVIDERS, "my-vllm", "qwen-chat")).toBe(
      "my-vllm/qwen-chat",
    );
    // A listed model with a slash keeps it: the provider is the first segment.
    expect(
      orgModelPageId(PROVIDERS, "together", "meta-llama/llama-4-scout"),
    ).toBe("together/meta-llama/llama-4-scout");
    // A provider the gateway does not have yet still has the page, which
    // says why.
    expect(orgModelPageId(PROVIDERS, "broken", "old-chat")).toBe(
      "broken/old-chat",
    );
  });

  it("is null for a model no provider serves under that id", () => {
    expect(orgModelPageId(PROVIDERS, "my-vllm", "llama")).toBeNull();
    expect(orgModelPageId(PROVIDERS, "together", "qwen-chat")).toBeNull();
    expect(orgModelPageId(PROVIDERS, "openai", "gpt-4o")).toBeNull();
    expect(orgModelPageId(undefined, "my-vllm", "qwen-chat")).toBeNull();
    expect(orgModelPageId(PROVIDERS, "", "qwen-chat")).toBeNull();
    expect(orgModelPageId(PROVIDERS, "my-vllm", "")).toBeNull();
  });
});
