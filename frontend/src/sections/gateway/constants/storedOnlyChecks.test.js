import { describe, it, expect } from "vitest";

import { MODEL_GUARDRAIL_CHECKS } from "../settings/GuardrailConfigTab";
import { STORED_ONLY_CHECKS, isStoredOnlyCheck } from "./storedOnlyChecks";

// The tests that drive the editors list these names themselves so they also
// run against a tree without this module; this one keeps the lists equal.
describe("the checks the console keeps only as stored", () => {
  it("are the fourteen partner checks and the tool permissions check", () => {
    expect([...STORED_ONLY_CHECKS].sort()).toEqual(
      [
        "llama-guard",
        "azure-content-safety",
        "presidio-pii",
        "bedrock-guardrails",
        "hiddenlayer-guard",
        "aporia-guard",
        "pangea-guard",
        "dynamoai-guard",
        "enkrypt-guard",
        "ibm-ai-detector",
        "grayswan-guard",
        "lasso-guard",
        "crowdstrike-aidr",
        "zscaler-guard",
        "tool-permissions",
      ].sort(),
    );
  });

  it("are all catalog checks, and leave Lakera, Future AGI Eval and MCP Security as they are", () => {
    const catalog = new Set(MODEL_GUARDRAIL_CHECKS.map((c) => c.name));
    for (const name of STORED_ONLY_CHECKS) expect(catalog.has(name)).toBe(true);
    for (const name of ["lakera-guard", "futureagi-eval", "mcp-security"]) {
      expect(isStoredOnlyCheck(name)).toBe(false);
    }
  });
});
