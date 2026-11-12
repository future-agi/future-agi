import { describe, expect, it } from "vitest";
import {
  editChangeSummary,
  evalFromDatasetDefault,
  experimentExtras,
  metadataObject,
  valuesFromExperiment,
} from "../experimentFormValues";
import { flattenRuns } from "../runPlan";

const detail = {
  name: "Support agent v3 · 3 trials",
  description: "d",
  column_id: "c3",
  dataset_version: { id: "VER_6", number: 6 },
  splits: [{ id: "S_HARD", name: "hard" }],
  trials: 3,
  metadata: { source: "ui", ticket: "TH-1" },
  user_eval_metrics: [{ id: "uem1", template_id: "t1", name: "Correctness" }],
  prompt_configs: [],
  runs: [
    {
      run_id: "RUN_4A",
      label: "#4A",
      letter: "A",
      experiment_ordinal: 4,
      target_type: "agent",
      target: {
        graph_id: "G",
        graph_name: "Support Agent",
        version_id: "GV3",
        version_name: "v3",
      },
      concurrency: 10,
    },
    {
      run_id: "RUN_4B",
      label: "#4B",
      letter: "B",
      experiment_ordinal: 4,
      target_type: "prompt",
      target: {
        prompt_id: "P",
        prompt_name: "Support Reply",
        prompt_version_id: "PV5",
        prompt_version_name: "V5",
        model: "gpt-4o",
        model_params: { temperature: 0.3 },
      },
    },
  ],
};

describe("experimentFormValues", () => {
  it("builds edit values with run ids and duplicate values without", () => {
    const edit = valuesFromExperiment(detail, { mode: "edit" });
    expect(edit.promptConfig.map((c) => c.targetType)).toEqual([
      "agent",
      "prompt",
    ]);
    expect(edit.promptConfig[0].runId).toBe("RUN_4A");
    expect(edit.promptConfig[1].model[0].runId).toBe("RUN_4B");
    expect(edit.splitIds).toEqual(["S_HARD"]);
    expect(edit.metadata).toEqual([{ key: "ticket", value: "TH-1" }]);
    const dup = valuesFromExperiment(detail, { mode: "duplicate" });
    expect(dup.name).toBe("Support agent v3 · 3 trials (copy)");
    expect(dup.promptConfig[0].runId).toBeUndefined();
    expect(dup.userEvalMetrics[0].actualEvalCreatedId).toBeUndefined();
  });

  it("summarises an edit in one line", () => {
    const original = valuesFromExperiment(detail, { mode: "edit" });
    const current = structuredClone(original);
    current.promptConfig[1].modelParams["gpt-4o"] = { temperature: 0.9 };
    current.promptConfig[1].model.push({ value: "claude-sonnet-4-5" });
    current.userEvalMetrics.push({ name: "Tone" });
    const s = editChangeSummary(original, current, {
      runs: detail.runs,
      flatten: flattenRuns,
    });
    expect(s.text).toBe(
      "Re-run: #4B (params temperature 0.3 → 0.9) · New: #4C · Kept: #4A · Evaluations: + Tone",
    );
  });

  it("writes the create extras and metadata", () => {
    expect(
      metadataObject([
        { key: "a", value: "1" },
        { key: "source", value: "x" },
        { key: " ", value: "y" },
      ]),
    ).toEqual({ a: "1" });
    expect(
      experimentExtras({
        name: " n ",
        description: "",
        datasetVersionId: "VER_6",
        splitIds: ["S"],
        trials: "3",
        maxConcurrency: 5,
        metadata: [],
      }),
    ).toEqual({
      name: "n",
      description: "",
      dataset_version_id: "VER_6",
      split_ids: ["S"],
      trials: 3,
      max_concurrency: 5,
      metadata: {},
    });
    expect(experimentExtras({ name: "n" }, { isEditing: true })).toEqual({
      name: "n",
      description: "",
    });
  });

  it("points a dataset default's output at the run output", () => {
    const e = evalFromDatasetDefault(
      {
        id: "uem",
        template_id: "t1",
        name: "Correctness",
        mapping: { output: "c10", expected: "c3", input: "c1" },
        output_type: "pass_fail",
        higher_is_better: true,
        is_experiment_default: true,
      },
      [{ id: "c10", origin_type: "run_prompt" }],
    );
    expect(e.mapping).toEqual({
      output: "output",
      expected: "c3",
      input: "c1",
    });
    expect(e.isDefault).toBe(true);
    expect(e.higherIsBetter).toBe(true);
  });
});
