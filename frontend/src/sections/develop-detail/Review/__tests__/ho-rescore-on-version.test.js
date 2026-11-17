// HO results R6: the Re-score on vN offer after Save corrections (pure).
import { describe, expect, it } from "vitest";
import { rescoreOnVersionTargets } from "../rescoreOnVersionModel";
import { rescoreBodies } from "src/sections/common/Runs/useRescore";
import {
  mergeRescorePlans,
  rescoreConfirmBody,
  versionPlanGuard,
} from "src/sections/common/Runs/rescoreCopyModel";

const run = (id, label, v, extra = {}) => ({
  run_id: id,
  label,
  experiment_id: "x1",
  status: "Completed",
  dataset_version: { number: v },
  metric_keys: ["eval:correctness"],
  ...extra,
});
const r1 = {
  results: [
    run("a", "#1A", 5),
    run("b", "#1B", 7),
    run("c", "#1C", 5, { listed: false }),
    run("d", "#1D", 5, { target_type: "external" }),
  ],
  metric_defs: [
    { key: "eval:correctness", name: "Correctness", user_eval_id: "u1" },
    { key: "eval:other", name: "Other", user_eval_id: "u2" },
  ],
};

describe("Re-score runs on vN", () => {
  it("offers the finished, listed runs on an older version, with their evals", () => {
    const t = rescoreOnVersionTargets(r1, 7);
    expect(t.runs.map((r) => r.label)).toEqual(["#1A"]);
    expect(t.evalIds).toEqual(["u1"]);
    expect(t.label).toBe("Re-score 1 run on v7");
    expect(t.title).toBe("Re-score #1A on v7?");
    expect(rescoreOnVersionTargets(r1, 5)).toBeNull();
    expect(rescoreOnVersionTargets(null, 7)).toBeNull();
  });
  it("sends dataset_version on the one re-score request and says so", () => {
    const [b] = rescoreBodies({
      runs: [r1.results[0]],
      evalIdsOf: () => ["u1"],
      datasetVersion: 7,
    });
    expect(b.body).toEqual({
      user_eval_metric_ids: ["u1"],
      run_ids: ["a"],
      dataset_version: 7,
    });
    const [plain] = rescoreBodies({
      runs: [r1.results[0]],
      evalIdsOf: () => ["u1"],
    });
    expect(plain.body.dataset_version).toBeUndefined();
    const text = rescoreConfirmBody({
      runs: [{ ...r1.results[0], next_label: "#1A·2" }],
      evalNames: ["Correctness"],
      datasetVersion: 7,
    });
    expect(text).toMatch(
      /^Outputs stay as they are\. Correctness scores the rows v7 changed against v7 in a new run \(#1A·2\);/,
    );
  });
  it("goes ahead only when the server plans the re-score on vN", () => {
    const planOn = (n) => ({
      runs: [{ run_id: "a", rows: 3 }],
      by_model: [{ model: "turing_large", calls: 6 }],
      judge_calls: 6,
      cells: 6,
      ...(n != null ? { dataset_version: n } : {}),
    });
    const both = mergeRescorePlans([planOn(7), planOn(7)]);
    expect(both.dataset_version).toBe(7);
    expect(versionPlanGuard(both, 7)).toBe(both);
    // A server that ignored dataset_version: the confirm refuses, sends nothing.
    const ignored = mergeRescorePlans([planOn(null)]);
    expect(ignored.dataset_version).toBeNull();
    expect(versionPlanGuard(ignored, 7)).toEqual({
      error: true,
      reason:
        "The server did not plan this re-score on v7, so nothing was sent.",
    });
    expect(
      mergeRescorePlans([planOn(7), planOn(6)]).dataset_version,
    ).toBeNull();
    // Any other re-score keeps its plan.
    expect(versionPlanGuard(ignored, undefined)).toBe(ignored);
    expect(versionPlanGuard(null, 7)).toBeNull();
  });
});
