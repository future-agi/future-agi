import { describe, expect, it } from "vitest";
import { joinLabels, readAsOf, recordErrorOf, whyLine } from "../whyLine";

const K = "eval:task_success";
const row = {
  example_id: "T26",
  expected: {
    value: {
      checks: [
        {
          key: "order_found",
          name: "Order found",
          assert: { value: "A-8830" },
          description: "lookup_order returned A-8830",
        },
      ],
    },
  },
  results: {
    B: {
      scores: {
        [K]: {
          trials: [0, 0, 0],
          reason: "Order found failed; later evals were not reached.",
        },
      },
    },
    C: { scores: { [K]: { trials: [1, 1, 1] } } },
    A: { scores: { [K]: { trials: [0, 1, 0] } } },
  },
};
const runs = [
  { run_id: "B", label: "#2B" },
  { run_id: "A", label: "#2A" },
  { run_id: "C", label: "#2C" },
];
const causes = [
  {
    run_id: "B",
    title: 'Order id loses its "A-" prefix',
    rows: [{ row_id: "T26" }],
    first_divergence: { step_n: 1, step_name: "classify_intent" },
  },
];

describe("whyLine", () => {
  it("says why the baseline failed when the selected run passed", () => {
    const w = whyLine({
      row,
      runs,
      focusedRunId: "C",
      baselineId: "B",
      primaryKey: K,
      causes,
      returned: "8830",
    });
    expect(w.text).toBe(
      'Why it passed: #2C passed all 3 trials; baseline #2B failed: read A-8830 as "8830" (step 1, classify_intent).',
    );
    expect(w.marker).toMatchObject({ stepN: 1 });
  });
  it("falls back to the cause title, then the eval reason", () => {
    expect(
      whyLine({
        row,
        runs,
        focusedRunId: "B",
        baselineId: "B",
        primaryKey: K,
        causes,
        candidateId: "C",
      }).text,
    ).toBe(
      'Why it failed: #2B order id loses its "A-" prefix (step 1, classify_intent). #2C passed.',
    );
    expect(
      whyLine({
        row,
        runs,
        focusedRunId: "B",
        baselineId: "B",
        primaryKey: K,
        candidateId: "C",
      }).text,
    ).toBe("Why it failed: #2B order found failed. #2C passed.");
    // IT-R4 G3 (P5): never a third run — #2A is in view but not the pair.
    expect(
      whyLine({ row, runs, focusedRunId: "B", baselineId: "B", primaryKey: K })
        .text,
    ).toBe("Why it failed: #2B order found failed.");
  });
  it("names mixed and passing runs", () => {
    expect(
      whyLine({ row, runs, focusedRunId: "A", baselineId: "A", primaryKey: K })
        .text,
    ).toMatch(/^Mixed: #2A passed 1 of 3 trials/);
    const ok = { ...row, results: { B: row.results.C, C: row.results.C } };
    expect(
      whyLine({
        row: ok,
        runs: runs.filter((r) => r.run_id !== "A"),
        focusedRunId: "C",
        baselineId: "B",
        primaryKey: K,
      }).text,
    ).toBe("Why it passed: #2C passed all 3 trials. Baseline #2B passed too.");
  });
  it("joins labels and reads the wrong value", () => {
    expect(joinLabels(["#2A", "#2C", "#2D"])).toBe("#2A, #2C and #2D");
    expect(readAsOf(row.expected, "8830")).toEqual({
      expected: "A-8830",
      got: "8830",
    });
    expect(readAsOf(row.expected, "A-8830")).toBeNull();
  });
});

describe("whyLine (iterate r3 G3 P1)", () => {
  const T = "eval:task_completion";
  const tcRow = {
    example_id: "R11",
    expected: {
      value: {
        expected_tool_calls: [
          {
            name: "cancel_order",
            args: { order_id: "A-6118", item: "kettle" },
          },
        ],
      },
    },
    results: {
      A: {
        output: "I've taken care of A-6118 for you.",
        steps: { steps: 2, tool_calls: 0 },
        scores: {
          [T]: { value: 0, reason: "Task not completed: wrong args." },
        },
        movement: { [T]: "regressed" },
      },
      B: {
        output: "Cancelled the kettle on A-6118.",
        steps: { steps: 5, tool_calls: 1 },
        scores: { [T]: { value: 1 } },
      },
    },
  };
  const tcRuns = [
    { run_id: "B", label: "#1A" },
    { run_id: "A", label: "#2A" },
  ];
  it("cross-checks the steps: no tool call where the row expects one", () => {
    expect(
      whyLine({
        row: tcRow,
        runs: tcRuns,
        focusedRunId: "A",
        baselineId: "B",
        primaryKey: T,
      }).text,
    ).toBe(
      "Why it failed: #2A never called cancel_order (expected cancel_order{order_id: A-6118, item: kettle}); replied as if it had. Baseline #1A passed.",
    );
  });
  it("never claims an endpoint run 'never called' a tool it cannot see (L7c merge)", () => {
    const text = whyLine({
      row: tcRow,
      runs: [
        { run_id: "B", label: "#1A" },
        { run_id: "A", label: "#8A", config: { kind: "http" } },
      ],
      focusedRunId: "A",
      baselineId: "B",
      primaryKey: T,
    }).text;
    expect(text).not.toMatch(/never called/);
    expect(text).toBe(
      "Why it failed: #8A task not completed: wrong args. Baseline #1A passed.",
    );
  });
  it("names the regression on a regressions list", () => {
    expect(
      whyLine({
        row: tcRow,
        runs: tcRuns,
        focusedRunId: "A",
        baselineId: "B",
        primaryKey: T,
        regression: true,
      }).text,
    ).toBe(
      "Regression in #2A: Never called cancel_order (expected cancel_order{order_id: A-6118, item: kettle}); replied as if it had. Baseline #1A passed.",
    );
  });
  it("never says `Why it passed` for a row of a regressions list", () => {
    const r = {
      ...tcRow,
      results: {
        ...tcRow.results,
        C: {
          scores: {
            [T]: { trials: [1, 1, 1] },
            "eval:rubric": { display: "2/3" },
          },
          movement: { "eval:rubric": "regressed" },
        },
        B: {
          ...tcRow.results.B,
          scores: {
            ...tcRow.results.B.scores,
            "eval:rubric": { display: "3/3" },
          },
        },
      },
    };
    const w = whyLine({
      row: r,
      runs: [...tcRuns, { run_id: "C", label: "#2C" }],
      focusedRunId: "C",
      baselineId: "B",
      primaryKey: T,
      regression: true,
      defsByKey: {
        "eval:rubric": { name: "Reply rubric" },
        [T]: { name: "Task success" },
      },
    });
    expect(w.text).toBe(
      "Regression in #2C: Reply rubric 3/3 → 2/3 vs #1A; Task success still passed all 3 trials.",
    );
    expect(w.tone).not.toBe("pass");
  });
  it("IT-R4 G3 P5: names the pair's other run (`Baseline #1A failed too`)", () => {
    const one = {
      example_id: "X",
      results: {
        A: { scores: { [T]: { value: 0, reason: "Wrong answer." } } },
        B: { scores: { [T]: { value: 0 } } },
      },
    };
    expect(
      whyLine({
        row: one,
        runs: tcRuns,
        focusedRunId: "A",
        baselineId: "B",
        primaryKey: T,
      }).text,
    ).toBe("Why it failed: #2A wrong answer. Baseline #1A failed too.");
  });
});

describe("options-truth L3 G10: the why line prefers the record's error", () => {
  const P = "eval:task_success";
  const runs = [
    { run_id: "A", label: "#1A" },
    { run_id: "B", label: "#2B" },
  ];
  const rowWith = (resultB) => ({
    example_id: "T9",
    expected: { value: {} },
    results: {
      A: { scores: { [P]: { trials: [1] } } },
      B: resultB,
    },
  });
  it("names the try's record error, not the judge's reason", () => {
    const out = whyLine({
      row: rowWith({
        scores: { [P]: { trials: [0], reason: "The reply did not refund." } },
        trials_detail: [
          { trial: 1, error: "Network off: api.stripe.com unreachable" },
        ],
      }),
      runs,
      focusedRunId: "B",
      baselineId: "A",
      primaryKey: P,
    });
    expect(out.text).toContain("Network off: api.stripe.com unreachable");
    expect(out.text).not.toContain("did not refund");
  });
  it("reads {message} errors and the result's own error", () => {
    expect(
      recordErrorOf(
        {
          trials_detail: [
            { trial: 1, error: null },
            {
              trial: 2,
              error: { message: "No recording for lookup_order on this row" },
            },
          ],
        },
        2,
      ),
    ).toBe("No recording for lookup_order on this row");
    expect(recordErrorOf({ error: { text: "Timed out after 30 s" } })).toBe(
      "Timed out after 30 s",
    );
    expect(recordErrorOf({ error: null, trials_detail: [] })).toBeNull();
  });
  it("falls back to the eval reason when the try ran clean", () => {
    const out = whyLine({
      row: rowWith({
        scores: { [P]: { trials: [0], reason: "The reply did not refund." } },
      }),
      runs,
      focusedRunId: "B",
      baselineId: "A",
      primaryKey: P,
    });
    expect(out.text).toContain("the reply did not refund");
  });
});
