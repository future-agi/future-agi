import { describe, it, expect } from "vitest";
import { getGuardrailStage } from "./guardrailStage";

describe("getGuardrailStage", () => {
  it.each([
    "futureagi-eval",
    "lakera-guard",
    "azure-content-safety",
    "presidio-pii",
    "llama-guard",
    "bedrock-guardrails",
    "hiddenlayer-guard",
    "dynamoai-guard",
    "enkrypt-guard",
    "ibm-ai-detector",
    "pangea-guard",
    "zscaler-guard",
    "crowdstrike-aidr",
    "aporia-guard",
    "lasso-guard",
    "grayswan-guard",
  ])("runs %s at the stage saved on the rule", (name) => {
    // Rules saved from the dashboard carry no provider; the name implies it.
    expect(getGuardrailStage({ name, stage: "post", config: {} })).toEqual({
      configurable: true,
      stage: "post",
    });
  });

  it("recognises an external provider from its config under any name", () => {
    expect(
      getGuardrailStage({
        name: "response-screening",
        stage: "both",
        config: { provider: "aporia" },
      }),
    ).toEqual({ configurable: true, stage: "both" });
  });

  it.each([
    [" Post ", "post"],
    ["BOTH", "both"],
    [undefined, "pre"],
    ["", "pre"],
    ["during", "pre"],
    [1, "pre"],
  ])("reads a saved stage of %j as %s", (saved, stage) => {
    expect(getGuardrailStage({ name: "lakera-guard", stage: saved })).toEqual({
      configurable: true,
      stage,
    });
  });

  it("ignores the legacy phase, which never reaches the gateway", () => {
    expect(
      getGuardrailStage({ name: "lakera-guard", stage: "post", phase: "pre" })
        .stage,
    ).toBe("post");
    expect(
      getGuardrailStage({ name: "lakera-guard", phase: "both" }).stage,
    ).toBe("pre");
  });

  it.each(["hallucination-detection", "data-leakage-prevention"])(
    "always runs %s after the LLM",
    (name) => {
      expect(getGuardrailStage({ name, stage: "pre" })).toEqual({
        configurable: false,
        stage: "post",
      });
    },
  );

  it.each([
    ["pii-detector", {}],
    ["pii-detection", {}],
    ["injection-detector", {}],
    ["secrets-detector", {}],
    ["content-moderation", {}],
    ["keyword-blocklist", { words: ["forbidden"] }],
    ["topic-restriction", {}],
    ["language-detection", {}],
    ["system-prompt-protection", {}],
    ["input-validation", {}],
    ["tool-permissions", { provider: "tool_permission" }],
    ["mcp-security", { provider: "mcp_security" }],
    ["audit-hook", { url: "https://hooks.example.com/guardrail" }],
  ])("always runs %s before the LLM", (name, config) => {
    expect(getGuardrailStage({ name, stage: "post", config })).toEqual({
      configurable: false,
      stage: "pre",
    });
  });

  it("ignores a provider in a built-in guardrail's config", () => {
    // The gateway resolves the built-in name first and never calls Presidio.
    expect(
      getGuardrailStage({
        name: "pii-detection",
        stage: "post",
        config: { provider: "presidio" },
      }),
    ).toEqual({ configurable: false, stage: "pre" });
  });

  it("treats a missing guardrail as running before the LLM", () => {
    expect(getGuardrailStage(null)).toEqual({
      configurable: false,
      stage: "pre",
    });
  });
});
