// L7c g6 (sum:export): the worse-tasks CSV is filled; pass/fail deltas
// also in the points the All runs table shows.
import { describe, expect, it } from "vitest";
import { analysisCsv, rowPreviewsOf, worseTasksCsv } from "../analysisCsv";

describe("Summary CSVs (L7c g6)", () => {
  it("fills row and input_preview for every worse task", () => {
    const previews = rowPreviewsOf([
      {
        example_id: "e1",
        row_order: 12,
        inputs: { customer_message: "Where is   my order A-7781?" },
      },
    ]);
    const csv = worseTasksCsv(
      { worklist: {} },
      [{ run: { label: "#2B" }, base: { label: "#1A" }, worseIds: ["e1"] }],
      previews,
    );
    expect(csv.split("\n")[1]).toBe("#2B,#1A,e1,12,Where is my order A-7781?");
  });
  it("adds delta_pts for pass/fail metrics, rounded once", () => {
    const csv = analysisCsv({
      baseline_run_id: "a",
      runs: [
        { run_id: "a", label: "#2A" },
        { run_id: "b", label: "#2B" },
      ],
      metrics: [
        {
          key: "eval:correctness",
          output_type: "pass_fail",
          per_run: [{ run_id: "b", value: 0.8 }],
          vs_baseline: [{ run_id: "b", delta: -0.021186 }],
        },
        {
          key: "eval:tone",
          output_type: "score",
          per_run: [],
          vs_baseline: [],
        },
      ],
    });
    const [head, , line] = csv.split("\n");
    expect(head).toMatch(
      /eval:correctness:delta,eval:correctness:delta_pts,eval:tone/,
    );
    expect(line).toMatch(/,0\.8,-0\.021186,-2\.1,/);
  });
});
