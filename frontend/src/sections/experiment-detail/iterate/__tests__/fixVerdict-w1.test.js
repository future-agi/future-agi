// W1-9 B6: one Failed count for the banner and the Failed chips; the
// unscored line from the run's own score cells.
import { describe, expect, it } from "vitest";
import { failedRowsOf, fixVerdict } from "../fixVerdict";
import { summarizeIteration } from "../iterateSummary";
import { unscoredOf } from "../unscoredModel";

const cell = (trials, extra = {}) => ({ trials, ...extra });
// #2A (base) vs #2H (fix on the base's failed rows; r5, r6 carried)
const MATRIX = {
  rows: [
    { row_id: "r1", cells: { A: cell([0, 1, 1]), H: cell([1, 1, 1]) } },
    { row_id: "r2", cells: { A: cell([0, 0, 0]), H: cell([0, 1, 1]) } },
    { row_id: "r3", cells: { A: cell(["error", 1, 1]), H: cell([1, 1, 1]) } },
    { row_id: "r4", cells: { A: cell([0, 0, 1]), H: cell([0, 0, 0]) } },
    {
      row_id: "r5",
      cells: {
        A: cell([1, 1, 1]),
        H: cell([1, 1, 1], { carried_from: "#2A" }),
      },
    },
    {
      row_id: "r6",
      cells: {
        A: cell([1, 1, 1]),
        H: cell([1, 1, 1], { carried_from: "#2A" }),
      },
    },
  ],
};

describe("failedRowsOf", () => {
  it("rows with a failing try (an error counts), per run", () => {
    expect(failedRowsOf(MATRIX, "A", "H")).toEqual({
      base: ["r1", "r2", "r3", "r4"],
      cand: ["r2", "r4"],
    });
    expect(failedRowsOf(MATRIX, "A", "H", { scope: "rerun" }).cand).toEqual([
      "r2",
      "r4",
    ]);
  });

  it("equals the verdict: failing = base Failed, still fail = child Failed", () => {
    const v = fixVerdict(summarizeIteration(MATRIX, "A", "H"), {
      notRechecked: 2,
    });
    const f = failedRowsOf(MATRIX, "A", "H");
    expect(v.failing).toBe(f.base.length);
    expect(v.still + v.broke).toBe(f.cand.length);
    expect(v.text).toBe(
      "Fixed 2 of 4 failing rows · 2 still fail · 2 rows not re-checked",
    );
  });

  it("a new failure on a rerun row is the child's Failed too", () => {
    const m = {
      rows: [
        ...MATRIX.rows,
        { row_id: "r7", cells: { A: cell([1, 1, 1]), H: cell([1, 0, 1]) } },
      ],
    };
    const v = fixVerdict(summarizeIteration(m, "A", "H"));
    expect(v.still + v.broke).toBe(failedRowsOf(m, "A", "H").cand.length);
  });
});

describe("unscoredOf", () => {
  const rows = [
    {
      example_id: "r1",
      results: {
        H: {
          scores: {
            "eval:correctness": { value: 1 },
            "eval:tone": { value: 1 },
          },
        },
      },
    },
    {
      example_id: "r2",
      results: {
        H: {
          scores: {
            "eval:correctness": {
              value: null,
              status: "skipped",
              skip_reason: "no expected value",
            },
            "eval:tone": { value: 0 },
          },
        },
      },
    },
    {
      example_id: "r3",
      results: {
        H: {
          scores: {
            "eval:correctness": {
              value: null,
              status: "skipped",
              skip_reason: "no expected value",
            },
          },
        },
      },
    },
    {
      example_id: "r4",
      results: {
        H: {
          scores: {
            "eval:reply_rubric": { value: null, display: "Not reached" },
          },
        },
      },
    },
    {
      example_id: "r5",
      results: {
        H: {
          scores: {
            "eval:correctness": {
              value: null,
              status: "skipped",
              skip_reason: "no expected value",
            },
          },
        },
      },
    },
  ];
  const nameOf = (k) =>
    ({ "eval:correctness": "Correctness", "eval:tone": "Tone" })[k] ?? k;

  it("names the rows no eval scored, carried rows left out", () => {
    const u = unscoredOf(rows, "H", { carried: new Set(["r5"]), nameOf });
    expect(u.rowIds).toEqual(["r2", "r3"]);
    expect(u.text).toBe(
      "2 rows not scored · Correctness skipped (no expected value)",
    );
  });

  it("N/A and score errors read their own words; several evals say how many", () => {
    const u = unscoredOf(
      [
        ...rows,
        {
          example_id: "r6",
          results: {
            H: {
              scores: {
                "eval:tone": {
                  value: null,
                  status: "na",
                  reason: "not a reply",
                },
              },
            },
          },
        },
        {
          example_id: "r7",
          results: {
            H: {
              scores: {
                "eval:tone": {
                  value: null,
                  status: "error",
                  reason: "judge returned 7",
                },
              },
            },
          },
        },
      ],
      "H",
      { nameOf },
    );
    expect(u.text).toBe(
      "5 rows not scored · Correctness skipped (no expected value) on 3 · Tone N/A (not a reply) on 1 · Tone score error (judge returned 7) on 1",
    );
  });

  it("nothing unscored → no line", () => {
    expect(unscoredOf(rows.slice(0, 1), "H").text).toBeNull();
  });
});
