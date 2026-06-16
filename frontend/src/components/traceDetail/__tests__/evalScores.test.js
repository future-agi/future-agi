import { describe, it, expect } from "vitest";
import { spanOwnEvalRows, collectSubtreeEvals } from "../evalScores";

// A trace entry in the new grouped eval_scores shape (scope: trace).
const entry = (id, evals) => ({
  observation_span: { id },
  eval_scores: { scope: "trace", eval_tasks: [{ eval_task_name: "t", evals }] },
});

const numericEval = (spans) => ({
  eval_name: "levenshtein",
  eval_config_id: "c1",
  output_type: "score",
  spans,
});

describe("spanOwnEvalRows", () => {
  it("flattens this span's own numeric rows with pass via the >=50 threshold", () => {
    const rows = spanOwnEvalRows(
      entry("s1", [numericEval([{ span_id: "s1", value: 30 }])]),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ spanId: "s1", score: 30, pass: false, label: "30%" });
  });

  it("filters out spans that aren't this entry's own", () => {
    const rows = spanOwnEvalRows(
      entry("s1", [
        numericEval([
          { span_id: "s1", value: 80 },
          { span_id: "s2", value: 10 },
        ]),
      ]),
    );
    expect(rows.map((r) => r.spanId)).toEqual(["s1"]);
    expect(rows[0].pass).toBe(true);
  });

  it("marks errored rows with pass=null (not scorable)", () => {
    const rows = spanOwnEvalRows(
      entry("s1", [numericEval([{ span_id: "s1", value: null, error: true }])]),
    );
    expect(rows[0]).toMatchObject({ error: true, pass: null, label: "Err" });
  });

  it("treats choice rows as non-scorable (pass=null) with joined labels", () => {
    const rows = spanOwnEvalRows(
      entry("s1", [
        {
          eval_name: "tone",
          eval_config_id: "c2",
          output_type: "choices",
          spans: [{ span_id: "s1", value: ["anger"] }],
        },
      ]),
    );
    expect(rows[0]).toMatchObject({ pass: null, label: "anger" });
  });

  // Boundary / null
  it("returns [] for an entry with no eval_scores", () => {
    expect(spanOwnEvalRows({ observation_span: { id: "s1" } })).toEqual([]);
    expect(spanOwnEvalRows(null)).toEqual([]);
  });

  // Defensive: the OLD array shape must not crash (reads .eval_tasks → [])
  it("does not crash on the legacy array-shaped eval_scores", () => {
    expect(
      spanOwnEvalRows({ observation_span: { id: "s1" }, eval_scores: [] }),
    ).toEqual([]);
  });
});

describe("collectSubtreeEvals", () => {
  it("counts only scorable rows and recurses into children", () => {
    const root = {
      ...entry("root", [
        numericEval([{ span_id: "root", value: 90 }]), // pass
      ]),
      children: [
        entry("s1", [numericEval([{ span_id: "s1", value: 10 }])]), // fail
      ],
    };
    expect(collectSubtreeEvals(root)).toEqual({ pass: 1, fail: 1, total: 2 });
  });

  it("excludes choice/errored rows from the totals", () => {
    const root = entry("root", [
      {
        eval_name: "tone",
        eval_config_id: "c2",
        output_type: "choices",
        spans: [{ span_id: "root", value: ["anger"] }],
      },
    ]);
    expect(collectSubtreeEvals(root)).toEqual({ pass: 0, fail: 0, total: 0 });
  });
});
