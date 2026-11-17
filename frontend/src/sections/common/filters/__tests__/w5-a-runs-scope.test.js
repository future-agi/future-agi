// W5-1 · the runs catalogue (leaderboards / tags) and the expression pill.
import { describe, expect, it } from "vitest";
import { filterRuns } from "../runsScope";
import {
  expressionPill,
  expressionPillLabel,
  replacePill,
} from "../expressionPill";

const RUNS = [
  {
    run_id: "a",
    label: "#1A",
    name: "v1",
    target_type: "prompt",
    target: { model: "gpt-4o-mini" },
    status: "Completed",
    created_at: "2026-10-01T10:00:00Z",
    created_by: { name: "Ravi Menon" },
    dataset_id: "d1",
    dataset_version: { number: 5 },
    metadata: { source: "ui" },
    is_dataset_baseline: true,
    tags: [{ name: "prod" }],
  },
  {
    run_id: "b",
    label: "#2A",
    name: "v2",
    target_type: "agent",
    target: {},
    status: "Completed",
    created_at: "2026-10-12T10:00:00Z",
    created_by: { name: "Asha Rao" },
    dataset_id: "d2",
    dataset_version: { number: 6 },
    metadata: { source: "ci", pr: 412 },
    dataset_metadata: { team: "support" },
    tags: [],
  },
];
const metricDefs = [
  { key: "eval:correctness", name: "Correctness", output_type: "pass_fail" },
];
const VALUES = { a: { value: 0.8, n: 248 }, b: { value: 0.86, n: 30 } };
const ctx = {
  metricDefs,
  datasetNames: { d1: "Golden set", d2: "Refunds" },
  metricValue: (run) => VALUES[run.run_id],
  cost: (run) => (run.run_id === "a" ? 0.002 : 0.004),
};
const labels = (expr) => {
  const r = filterRuns(expr, RUNS, ctx);
  if (r.error) throw new Error(r.error.message);
  return r.runs.map((x) => x.label);
};

describe("runs catalogue", () => {
  it("reads every run field", () => {
    expect(labels('kind = "agent"')).toEqual(["#2A"]);
    expect(labels('model contains "4o"')).toEqual(["#1A"]);
    expect(labels('created_at >= "2026-10-10"')).toEqual(["#2A"]);
    expect(labels('created_by = "Asha Rao"')).toEqual(["#2A"]);
    expect(labels('dataset = "Golden set" and dataset_version = 5')).toEqual([
      "#1A",
    ]);
    expect(labels("is_baseline")).toEqual(["#1A"]);
    expect(labels('tags contains "prod"')).toEqual(["#1A"]);
    expect(labels("correctness > 0.85 and correctness.n >= 30")).toEqual([
      "#2A",
    ]);
    expect(labels("cost_usd < 0.003")).toEqual(["#1A"]);
    expect(labels('metadata.source = "ci" and metadata.pr = 412')).toEqual([
      "#2A",
    ]);
    expect(labels('dataset_metadata.team = "support"')).toEqual(["#2A"]);
    expect(labels('label in ("#1A", "#2A")')).toEqual(["#1A", "#2A"]);
  });

  it("names unknown fields", () => {
    expect(filterRuns("modle = 1", RUNS, ctx).error.message).toBe(
      "Unknown field “modle”. Did you mean model?",
    );
  });
});

describe("expression pill", () => {
  it("labels with the first 60 characters", () => {
    const long =
      '#2B.correctness < 0.5 and #2A.correctness >= 0.5 and metadata.language in ("es", "de")';
    const label = expressionPillLabel(expressionPill(long));
    expect(
      label.startsWith(
        "Expression: #2B.correctness < 0.5 and #2A.correctness >= 0.5 and",
      ),
    ).toBe(true);
    expect(label.endsWith("…")).toBe(true);
    expect(label.length).toBeLessThanOrEqual("Expression: ".length + 61);
    expect(expressionPillLabel(expressionPill("  a = 1 "))).toBe(
      "Expression: a = 1",
    );
  });

  it("replaces a pill in place", () => {
    const a = { type: "split", values: ["x"] };
    const b = expressionPill("a = 1");
    const c = expressionPill("a = 2");
    expect(replacePill([a, b], b, c)).toEqual([a, c]);
  });
});
