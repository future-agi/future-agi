/** options-truth L2 G9: a door's `change=` is drafted, ready to run. */
import { describe, expect, it } from "vitest";
import { changeOfEntry, initialDraft } from "../useIterateDock";
import { collectChanges, presetOf } from "../iterateModel";

const promptRun = {
  run_id: "A",
  config: {
    model: { id: "gpt-4o-mini", params: {} },
    prompt: { messages: [] },
  },
};
const harnessRun = {
  run_id: "H",
  config: { kind: "harness", harness: { model: "claude-sonnet-4-5" } },
};
const agentRun = {
  run_id: "B",
  target_type: "agent",
  config: {
    agent: {
      nodes: [{ id: "classify_intent", type: "llm", prompt: "Extract it." }],
    },
  },
};

describe("changeOfEntry / initialDraft", () => {
  it("model:<id> opens Model & params with that model, a real change", () => {
    const d = initialDraft(promptRun, { runId: "A", change: "model:gpt-4o" });
    expect(d.tab).toBe("model");
    expect(d.model).toEqual({ id: "gpt-4o" });
    expect(collectChanges(promptRun.config, { model: d.model })).toEqual([
      { axis: "model", field: "id", value: "gpt-4o" },
    ]);
  });
  // dataset integration FE-0: a harness run has no Test a fix tab (no change applies).
  it("model:<id> on a harness run changes nothing", () => {
    const d = initialDraft(harnessRun, {
      runId: "H",
      change: "model:gemini-2.5-flash",
    });
    expect(d.tab).toBeUndefined();
    expect(d.harness).toBeNull();
  });
  it("a preset fills its node's prompt with the named chip (or the preset's)", () => {
    const keep = initialDraft(agentRun, { runId: "B", change: "RC_PREFIX" });
    const chip = presetOf("RC_PREFIX").chips.find((c) => c.id === "keep");
    expect(keep.tab).toBe("agent");
    expect(keep.prompts).toEqual({ classify_intent: chip.text });
    const revert = initialDraft(agentRun, {
      runId: "B",
      change: "RC_PREFIX:revert",
    });
    expect(revert.prompts.classify_intent).toBe(
      presetOf("RC_PREFIX").chips.find((c) => c.id === "revert").text,
    );
  });
  it("nothing is drafted when the run has no place for the change", () => {
    expect(changeOfEntry("model:gpt-4o", { tabs: ["agent", "tools"] })).toEqual(
      {},
    );
    expect(changeOfEntry(null)).toEqual({});
    const cause = initialDraft(agentRun, { runId: "B", cause: "RC_PREFIX" });
    expect(cause.prompts).toEqual({});
  });
});
