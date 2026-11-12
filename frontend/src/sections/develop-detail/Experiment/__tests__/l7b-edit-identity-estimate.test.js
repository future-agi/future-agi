// options-truth L7b G2 (edit:estimate): an Edit save prices only the runs it
// starts (re-run and new cards); kept runs are left out of the estimate.
import { describe, expect, it } from "vitest";
import { editRunScope, valuesFromExperiment } from "../experimentFormValues";
import { flattenRuns } from "../runPlan";

const detail = {
  name: "#2",
  trials: 1,
  runs: ["A", "B"].map((l, i) => ({
    run_id: `R2${l}`,
    label: `#2${l}`,
    letter: l,
    experiment_ordinal: 2,
    target_type: "prompt",
    target: {
      prompt_id: "p1",
      prompt_name: "Support",
      prompt_version_id: "pv4",
      prompt_version_name: "V4",
      model: i ? "gpt-4o" : "gpt-4o-mini",
      model_params: { temperature: 0.3 },
    },
    config: { kind: "prompt" },
  })),
};

describe("editRunScope", () => {
  const v = valuesFromExperiment(detail, { mode: "edit" });

  it("nothing changed → nothing to price", () => {
    const s = editRunScope(v, v, { runs: detail.runs, flatten: flattenRuns });
    expect(s.promptConfig).toEqual([]);
    expect(s.kept).toEqual(["#2A", "#2B"]);
  });

  it("a new card is priced, the kept models are not", () => {
    const next = {
      ...v,
      promptConfig: [
        ...v.promptConfig,
        { _itemId: "new", targetType: "http", http: { url: "https://x.test" } },
      ],
    };
    const s = editRunScope(v, next, {
      runs: detail.runs,
      flatten: flattenRuns,
    });
    expect(s.added).toEqual(["#2C"]);
    expect(s.promptConfig).toHaveLength(1);
    expect(s.promptConfig[0]._itemId).toBe("new");
  });

  it("one changed model of a card is priced alone", () => {
    const next = JSON.parse(JSON.stringify(v));
    next.promptConfig[0].modelParams["gpt-4o"] = { temperature: 0.9 };
    const s = editRunScope(v, next, {
      runs: detail.runs,
      flatten: flattenRuns,
    });
    expect(s.rerun).toEqual(["#2B"]);
    expect(s.promptConfig).toHaveLength(1);
    expect(s.promptConfig[0].model.map((m) => m.value)).toEqual(["gpt-4o"]);
  });
});
