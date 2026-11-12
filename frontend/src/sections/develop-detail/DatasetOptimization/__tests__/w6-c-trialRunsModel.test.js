// W6 C6: the "Trials are runs" panel model.
import { describe, expect, it } from "vitest";
import {
  bestTrialOf,
  budgetLine,
  chartPoints,
  draftVersionOf,
  lessonLine,
  promoteBodies,
} from "../trialRunsModel";

const W6 = {
  experiment_id: "e9",
  experiment_name: "Optimization · Walk gepa",
  dataset_id: "d1",
  algorithm: "gepa",
  budget: 5,
  rows: 248,
  stop_reason:
    "No new candidate: every set this optimizer proposes was already measured.",
  prompt: {
    id: "p1",
    name: "Support Reply",
    version: "V3",
    user: "{{customer_message}}",
    model: "gpt-4o-mini",
  },
  baseline: { score: 0.8238, label: "#9A", run_id: "r0", status: "Completed" },
  trials: [
    {
      trial_id: "t1",
      trial: 1,
      score: 0.8616,
      best_so_far: 0.8616,
      label: "#9B",
      run_id: "r1",
      lessons: [],
    },
    {
      trial_id: "t2",
      trial: 2,
      score: 0.85,
      best_so_far: 0.8616,
      label: "#9C",
      run_id: "r2",
      lessons: [],
    },
    {
      trial_id: "t3",
      trial: 3,
      score: 0.8748,
      best_so_far: 0.8748,
      label: "#9D",
      run_id: "r3",
      is_best: true,
      lessons: [],
    },
  ],
};

describe("W6 C6 trial runs panel model", () => {
  it("chart points: baseline then trials, score and best so far in %", () => {
    expect(chartPoints(W6)).toEqual([
      { name: "Baseline", label: "#9A", score: 82.4, best: 82.4 },
      { name: "Trial 1", label: "#9B", score: 86.2, best: 86.2 },
      { name: "Trial 2", label: "#9C", score: 85, best: 86.2 },
      { name: "Trial 3", label: "#9D", score: 87.5, best: 87.5 },
    ]);
    expect(chartPoints(null)).toEqual([]);
  });

  it("the best trial only when it beats the baseline", () => {
    expect(bestTrialOf(W6).label).toBe("#9D");
    expect(bestTrialOf({ ...W6, baseline: { score: 0.9 } })).toBeNull();
  });

  it("budget line names trials, rows and why it stopped", () => {
    expect(budgetLine(W6)).toBe(
      "3 of 5 trials on 248 rows · stopped: No new candidate: every set this optimizer proposes was already measured.",
    );
    expect(
      budgetLine({
        ...W6,
        budget: 1,
        trials: W6.trials.slice(0, 1),
        stop_reason: null,
      }),
    ).toBe("1 of 1 trial on 248 rows");
  });

  it("lesson line quotes the failing row it came from", () => {
    expect(
      lessonLine({
        text: "Be brief.",
        rows: 1,
        example: { row: 7, reason: "Too long" },
      }),
    ).toBe("Be brief. · from 1 failing row (row 7: “Too long”)");
  });

  it("promote bodies: a draft with the trial text, its commit and the baseline pin", () => {
    const b = promoteBodies(
      W6,
      W6.trials[2],
      "Base.\n\nRules",
      W6.experiment_name,
    );
    expect(b.draft.new_prompts[0].prompt_config[0]).toEqual({
      messages: [
        { role: "system", content: "Base.\n\nRules" },
        { role: "user", content: "{{customer_message}}" },
      ],
      configuration: { model: "gpt-4o-mini" },
    });
    expect(b.commit("V7")).toEqual({
      version_name: "V7",
      message: "Optimization · Walk gepa · Trial 3 (#9D)",
    });
    expect(b.baseline).toEqual({ run_id: "r3" });
    expect(
      draftVersionOf({ data: { result: [{ template_version: "V7" }] } }),
    ).toBe("V7");
  });
});
