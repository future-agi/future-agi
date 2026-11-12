// options-truth L3 G3 (runcard:r4): every card line says what the run uses.
import { describe, expect, it } from "vitest";
import { r4CardSummary } from "../R4RunCard";

describe("r4CardSummary", () => {
  it("an undeclared code run says where its model and tools come from", () => {
    const text = r4CardSummary({ entryId: "custom_python", mode: "local" });
    expect(text).toMatch(/^Model and tools are read from your first trace · /);
    expect(text).not.toContain("your tools");
  });

  it("a changed model param is the model the card names", () => {
    const text = r4CardSummary({
      entryId: "custom_python",
      mode: "local",
      uses: {
        models: [{ name: "gpt-4.1-mini" }],
        params: [{ name: "model", default: "gpt-4.1-mini" }],
      },
      params: { model: "gpt-4o" },
    });
    expect(text.startsWith("gpt-4o")).toBe(true);
  });

  it("a harness names its chosen model and its run cap", () => {
    const text = r4CardSummary(
      {
        entryId: "claude_code",
        mode: "local",
        slot: { harness: { model: "claude-haiku-4-5", max_cost_usd: 0.02 } },
        limits: { maxCostUsd: 3 },
      },
      { tries: 30 },
    );
    expect(text).toMatch(/^claude-haiku-4-5 · /);
    expect(text).toContain("$0.02 per try");
    expect(text).toContain("stops at $3.00 per run of 30");
  });

  // dataset integration FE-0: the voice card is gone; a voice entry has no summary.
  it("a voice entry has no card summary", () => {
    expect(r4CardSummary({ entryId: "vapi", mode: "remote", slot: {} })).toBe(
      "",
    );
  });
});
