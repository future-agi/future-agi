// options-truth L3 G3 (R3, S5 / I2): identity names the model a created
// run used and the per-try cap each try ran with.
import { describe, expect, it } from "vitest";
import { provenanceLine, provenanceOf, settingsText } from "../provenanceModel";

describe("identity of created runs (config.engine)", () => {
  it("a harness run names its engine model, the helper apart", () => {
    const p = provenanceOf({
      config: {
        kind: "harness",
        harness: {
          id: "claude_code",
          name: "Claude Code",
          model: "claude-sonnet-4-5",
          subcall_model: "claude-haiku-4-5",
        },
        models: [
          { id: "claude-sonnet-4-5", role: "main" },
          { id: "claude-haiku-4-5", role: "helper" },
        ],
        engine: { model: "claude-haiku-4-5" },
        limits: { tries: 1, max_cost_per_try_usd: 0.02, max_cost_usd: 3 },
        execution: { mode: "local", label: "This machine" },
      },
    });
    const line = provenanceLine(p);
    expect(line).toContain(
      "Claude Code · claude-haiku-4-5 · helper claude-haiku-4-5",
    );
    expect(line).not.toContain("claude-sonnet-4-5 +1");
    expect(line).toContain("$0.02 per try");
    expect(line).toContain("cap $3");
  });

  it("a code run leads with the model it used, marked when undeclared", () => {
    const p = provenanceOf({
      config: {
        kind: "code",
        framework: { id: "custom_python", name: "Custom Python" },
        models: [{ id: "gpt-4.1-mini", role: "main", source: "declared" }],
        engine: { model: "gpt-4o" },
        execution: { mode: "local", label: "This machine" },
      },
    });
    expect(provenanceLine(p)).toContain("gpt-4o (seen in this run)");
    expect(provenanceLine(p)).not.toContain("gpt-4.1-mini");
  });

  it("settings carry the per-try cap", () => {
    expect(
      settingsText({ limits: { tries: 2, max_cost_per_try_usd: 0.02 } }),
    ).toBe("2 tries · $0.02 per try");
  });
});
