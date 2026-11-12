import { describe, expect, it } from "vitest";

import { AUTO_LOGS } from "../test-fixtures/autoLogs";
import {
  candidatePageId,
  chosenShare,
  decisionsFromLogs,
  groupRejections,
  rejectionText,
} from "./autoDecisions";

describe("auto decisions from request logs", () => {
  const decisions = decisionsFromLogs(AUTO_LOGS.results);

  it("read the chosen model, tier, preference and pool size of each request", () => {
    expect(decisions).toHaveLength(4);
    const [latest] = decisions;
    expect(latest).toMatchObject({
      requestId: AUTO_LOGS.results[0].request_id,
      chosen: "mistralai/ministral-14b-2512",
      provider: "openrouter",
      tier: "simple",
      preference: "balanced",
      considered: 64,
      fallbacks: ["anthropic/claude-haiku-5.5", "openai/gpt-6-luna"],
    });
    expect(latest.ranked[0]).toMatchObject({
      model: "mistralai/ministral-14b-2512",
      score: 0.994,
    });
    expect(latest.rejected[0]).toEqual({
      model: "amazon/nova-2-lite-v1",
      reason: "managed_model_not_allowed",
    });
  });

  it("skip logs that carry no auto decision", () => {
    expect(
      decisionsFromLogs([{ id: "x", metadata: { feature: "kb" } }]),
    ).toEqual([]);
  });

  it("count how often each model was chosen", () => {
    expect(chosenShare(decisions)).toEqual([
      { model: "mistralai/ministral-8b-2512", count: 3, share: 0.75 },
      { model: "mistralai/ministral-14b-2512", count: 1, share: 0.25 },
    ]);
    expect(chosenShare([])).toEqual([]);
  });

  it("explain each rejection code in words, and keep unknown codes", () => {
    expect(rejectionText("managed_model_not_allowed")).toBe(
      "Your organization's Future AGI model policy does not allow it",
    );
    expect(rejectionText("price_cap")).toBe("Above your price caps");
    expect(rejectionText("something_new")).toBe("something_new");
  });

  it("group the set-aside models by reason, the largest group first", () => {
    expect(
      groupRejections([
        { model: "a/one", reason: "price_cap" },
        { model: "b/two", reason: "managed_model_not_allowed" },
        { model: "c/three", reason: "managed_model_not_allowed" },
        { model: "d/four", reason: "" },
      ]),
    ).toEqual([
      {
        reason: "managed_model_not_allowed",
        text: "Your organization's Future AGI model policy does not allow it",
        models: ["b/two", "c/three"],
      },
      { reason: "price_cap", text: "Above your price caps", models: ["a/one"] },
      { reason: "", text: "No reason recorded", models: ["d/four"] },
    ]);
    expect(groupRejections(undefined)).toEqual([]);
  });
});

// The model page a ranked candidate links to. An own-provider candidate
// arrives as the model its provider lists and that provider's id
// (server/auto_pool.go autoCandidateFor), so its page is "<provider>/<model>";
// a managed one has its catalog page while the catalog lists it.
describe("the model page of an auto candidate", () => {
  const providers = [
    { id: "openai", name: "openai", models: ["gpt-4o", "gpt-4o-mini"] },
  ];
  const catalogIds = new Set(["anthropic/claude-haiku-5.5"]);

  it("is the catalog page for a model the catalog lists", () => {
    expect(
      candidatePageId(
        { model: "anthropic/claude-haiku-5.5", provider: "openrouter" },
        { providers, catalogIds },
      ),
    ).toBe("anthropic/claude-haiku-5.5");
  });

  it("is the provider's page for a model an org provider serves", () => {
    expect(
      candidatePageId(
        { model: "gpt-4o-mini", provider: "openai" },
        { providers, catalogIds },
      ),
    ).toBe("openai/gpt-4o-mini");
    expect(
      candidatePageId(
        { model: "gpt-4o-mini", provider: "openai" },
        { providers },
      ),
    ).toBe("openai/gpt-4o-mini");
  });

  it("is null for a model neither the catalog nor an org provider has", () => {
    expect(
      candidatePageId(
        { model: "mistralai/ministral-14b-2512", provider: "openrouter" },
        { providers, catalogIds },
      ),
    ).toBeNull();
    expect(
      candidatePageId({ model: "llama-3", provider: "openai" }, { providers }),
    ).toBeNull();
    expect(
      candidatePageId(
        { model: "anthropic/claude-haiku-5.5", provider: "openrouter" },
        { providers },
      ),
    ).toBeNull();
    expect(candidatePageId({}, { providers, catalogIds })).toBeNull();
  });
});
