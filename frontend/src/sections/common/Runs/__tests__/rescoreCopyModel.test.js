// W1-2: the one place re-score confirms, cost lines and toasts are worded.
import { describe, expect, it } from "vitest";
import {
  mergeRescorePlans,
  mergeRescoreResults,
  rescoreActions,
  rescoreConfirmBody,
  rescoreCostLine,
  rescoreMessage,
  rescoreTitle,
  scoreWithChoices,
} from "../rescoreCopyModel";

const child = (over = {}) => ({
  run_id: "c2a",
  label: "#2A·2",
  from_run_id: "a2a",
  from_label: "#2A",
  flipped_rows: ["r1", "r2", "r3", "r4", "r5", "r6", "r7"],
  fail_to_pass: 2,
  pass_to_fail: 5,
  rows: 238,
  row_scoped: false,
  ...over,
});

describe("rescoreCopyModel", () => {
  it("words a child with its flips and keeps the parent's scores", () => {
    const r = {
      unchanged: false,
      eval_names: ["Tone"],
      runs: [child()],
      labels: ["#2A·2"],
      fail_to_pass: 2,
      pass_to_fail: 5,
    };
    expect(rescoreMessage(r)).toBe(
      "Re-scored #2A with Tone: 7 rows changed (5 Pass → Fail, 2 Fail → Pass). #2A keeps its scores.",
    );
    const a = rescoreActions(r);
    expect(a.open).toMatchObject({ label: "Open #2A·2", runId: "c2a" });
    expect(a.rows).toMatchObject({
      label: "See the 7 rows",
      baselineId: "a2a",
      runId: "c2a",
      pillLabel: "Changed by Tone re-score",
    });
  });

  it("says when no verdict moved, and when nothing changed at all", () => {
    expect(
      rescoreMessage({
        unchanged: false,
        eval_names: ["Groundedness"],
        runs: [child({ flipped_rows: [], fail_to_pass: 0, pass_to_fail: 0 })],
        fail_to_pass: 0,
        pass_to_fail: 0,
      }),
    ).toBe(
      "Re-scored #2A with Groundedness: no verdict changed. #2A keeps its scores.",
    );
    expect(
      rescoreMessage({
        unchanged: true,
        same_definition: true,
        eval_version: "v2",
        unchanged_labels: ["#2A"],
      }),
    ).toBe("Scores unchanged · same eval version (v2)");
    expect(
      rescoreMessage({
        unchanged: true,
        unchanged_labels: [],
        skipped: [
          {
            label: "#2A·2",
            reason: "still running; re-score it when it finishes",
          },
        ],
      }),
    ).toBe(
      "Nothing re-scored. #2A·2 is still running; re-score it when it finishes.",
    );
    expect(rescoreActions({ unchanged: true }).open).toBeNull();
  });

  it("words Score with… on a selection", () => {
    const r = {
      unchanged: false,
      eval_names: ["Correctness"],
      labels: ["#2A·2"],
      runs: [
        child({
          rows: 3,
          row_scoped: true,
          flipped_rows: [],
          fail_to_pass: 0,
          pass_to_fail: 0,
        }),
      ],
    };
    expect(rescoreMessage(r)).toBe(
      "Scored 3 rows of #2A with Correctness in #2A·2: no verdict changed. #2A keeps its scores.",
    );
    expect(
      rescoreTitle({
        runs: [{ label: "#2A" }],
        evalNames: ["Correctness"],
        rowIds: ["a", "b", "c"],
      }),
    ).toBe("Score 3 rows of #2A with Correctness?");
  });

  it("prices the plan with the estimator it is given", () => {
    const plan = { by_model: [{ model: "gpt-4o", calls: 744 }], cells: 744 };
    const line = rescoreCostLine(plan, (m) => (m === "gpt-4o" ? 0.0006 : 0));
    expect(line.text).toBe(`744 judge calls at gpt-4o · about $${line.usd}`);
    expect(line.usd).toBeCloseTo(0.4464, 6);
    expect(
      rescoreCostLine(
        { by_model: [], kept: 476, rule_based: 0, cells: 476 },
        () => 1,
      ).text,
    ).toBe("476 scores re-read at the new bar · no judge call");
    expect(
      rescoreCostLine({ by_model: [], rule_based: 30, cells: 30 }, () => 1)
        .text,
    ).toBe("30 checks · free (rule-based)");
    expect(rescoreCostLine({ by_model: [], cells: 0 }, () => 1).text).toBe(
      "Nothing to score again on these runs",
    );
  });

  it("names the new run in the confirm body", () => {
    expect(
      rescoreConfirmBody({
        runs: [{ label: "#2A", attempts_count: 1 }],
        evalNames: ["Tone"],
      }),
    ).toBe(
      "Outputs stay as they are. Tone scores the outputs of #2A again in a new run (#2A·2); #2A keeps its scores. When no score moves, no run is made.",
    );
  });

  it("merges several experiments' plans and results", () => {
    const p = mergeRescorePlans([
      { by_model: [{ model: "gpt-4o", calls: 3 }], cells: 3, kept: 0 },
      { by_model: [{ model: "gpt-4o", calls: 2 }], cells: 4, kept: 2 },
    ]);
    expect(p.by_model).toEqual([{ model: "gpt-4o", calls: 5 }]);
    expect(p.cells).toBe(7);
    const m = mergeRescoreResults([
      { data: { result: { unchanged: true, unchanged_labels: ["#1A"] } } },
      {
        data: {
          result: {
            unchanged: false,
            eval_names: ["Tone"],
            runs: [child()],
            labels: ["#2A·2"],
            fail_to_pass: 2,
            pass_to_fail: 5,
            unchanged_labels: [],
          },
        },
      },
    ]);
    expect(m.unchanged).toBe(false);
    expect(m.unchanged_labels).toEqual(["#1A"]);
    expect(rescoreMessage(m)).toMatch(/#1A unchanged\.$/);
  });

  it("lists the run's evals, then the dataset's evals it does not carry", () => {
    const run = { run_id: "a2a", label: "#2A" };
    const c = scoreWithChoices(run, {
      metrics: [
        { key: "eval:tone", name: "Tone", uem_ids: { a2a: "uem-tone" } },
        { key: "eval:x", name: "X", uem_ids: { other: "u" } },
      ],
      datasetEvals: [
        { id: "de-tone", key: "eval:tone", name: "Tone" },
        {
          id: "de-tool",
          key: "eval:tool_call_accuracy",
          name: "Tool-call accuracy",
        },
      ],
    });
    expect(c.map((x) => [x.id, x.label])).toEqual([
      ["uem-tone", "Tone · on #2A"],
      ["de-tool", "Tool-call accuracy · new on #2A"],
    ]);
  });
});

describe("rescoreCopyModel · a new eval", () => {
  it("says scored, not re-scored, for an eval the runs did not carry", () => {
    expect(
      rescoreMessage({
        unchanged: false,
        eval_names: ["Tool-call accuracy"],
        labels: ["#2A·2"],
        runs: [
          child({
            flipped_rows: [],
            fail_to_pass: 0,
            pass_to_fail: 0,
            eval_keys: ["eval:tool_call_accuracy"],
            new_eval_keys: ["eval:tool_call_accuracy"],
          }),
        ],
      }),
    ).toBe(
      "Scored #2A with Tool-call accuracy in #2A·2. #2A keeps its scores.",
    );
  });
});

describe("rescoreCopyModel · new run labels", () => {
  it("numbers the new runs per config", async () => {
    const { nextLabelsOf } = await import("../rescoreCopyModel");
    expect(
      nextLabelsOf([
        { label: "#2A", attempt: 1, attempts_count: 2 },
        { label: "#2A·2", attempt: 2, attempts_count: 2 },
        { label: "#2B" },
      ]),
    ).toEqual(["#2A·3", "#2A·4", "#2B·2"]);
  });
});

describe("rescoreCopyModel · error policy", () => {
  it("words the policy confirm and its result", async () => {
    const m = await import("../rescoreCopyModel");
    expect(
      m.rescoreConfirmBody({
        runs: [{ label: "#2A" }, { label: "#2E" }],
        errorPolicy: "exclude",
      }),
    ).toBe(
      "Outputs stay as they are. Errored tries get no score, so they leave n and every mean, in new runs (#2A·2 and #2E·2); #2A and #2E keep their scores. No judge is called.",
    );
    expect(
      m.rescoreTitle({ runs: [{ label: "#2A" }], errorPolicy: "exclude" }),
    ).toBe("Leave the errored tries of #2A out of scores?");
    expect(
      m.rescoreMessage({
        unchanged: false,
        labels: ["#2A·2"],
        runs: [
          {
            run_id: "c",
            label: "#2A·2",
            from_label: "#2A",
            flipped_rows: [],
            error_policy: "exclude",
            errored_tries: 4,
          },
        ],
      }),
    ).toBe(
      "Re-scored #2A in #2A·2: 4 errored tries left out of every score. #2A keeps its scores.",
    );
    expect(
      m.rescoreCostLine({ by_model: [], policy_tries: 4, cells: 8 }, () => 1)
        .text,
    ).toBe("4 errored tries rebuilt · no judge call");
  });
});

// Review M5: verdicts are counted per cell (an eval on a try of a run); the
// toast names that unit and the reason whenever cells outnumber rows.
describe("rescoreMessage counts (review M5)", () => {
  // 2 evals × 1 trial on 2 runs. #2A: Tone flips r1, r2; Groundedness flips
  // r1. #2B: Tone flips r1, r3. Rows changed: r1, r2, r3 (3); cells: 5.
  const two = {
    unchanged: false,
    eval_names: ["Groundedness", "Tone"],
    labels: ["#2A·2", "#2B·2"],
    runs: [
      child({
        flipped_rows: ["r1", "r2"],
        flipped_eval_keys: ["eval:tone", "eval:groundedness"],
        flipped_on_several_tries: false,
        fail_to_pass: 0,
        pass_to_fail: 3,
      }),
      child({
        run_id: "c2b",
        label: "#2B·2",
        from_run_id: "a2b",
        from_label: "#2B",
        flipped_rows: ["r1", "r3"],
        flipped_eval_keys: ["eval:tone"],
        flipped_on_several_tries: false,
        fail_to_pass: 1,
        pass_to_fail: 1,
      }),
    ],
    fail_to_pass: 1,
    pass_to_fail: 4,
  };

  it("2 evals and 2 runs: scores on rows, across evals and runs, never 'tries'", () => {
    const msg = rescoreMessage(two);
    expect(msg).toBe(
      "Re-scored #2A and #2B with Groundedness and Tone: 5 scores changed on 3 rows across 2 evals and 2 runs (4 Pass → Fail, 1 Fail → Pass). #2A and #2B keep their scores.",
    );
    expect(msg).not.toMatch(/tries/);
  });

  it("2 evals on one run with one trial says 'across 2 evals'", () => {
    const one = {
      ...two,
      labels: ["#2A·2"],
      runs: [two.runs[0]],
      fail_to_pass: 0,
      pass_to_fail: 3,
    };
    expect(rescoreMessage(one)).toBe(
      "Re-scored #2A with Groundedness and Tone: 3 scores changed on 2 rows across 2 evals (3 Pass → Fail). #2A keeps its scores.",
    );
  });

  it("one eval on 2 runs says 'across 2 runs'", () => {
    const r = {
      unchanged: false,
      eval_names: ["Groundedness"],
      labels: ["#2A·2", "#2B·2"],
      runs: [
        child({
          flipped_rows: ["r1"],
          flipped_eval_keys: ["g"],
          fail_to_pass: 0,
          pass_to_fail: 1,
        }),
        child({
          run_id: "c2b",
          label: "#2B·2",
          from_label: "#2B",
          flipped_rows: ["r1"],
          flipped_eval_keys: ["g"],
          fail_to_pass: 0,
          pass_to_fail: 1,
        }),
      ],
      fail_to_pass: 0,
      pass_to_fail: 2,
    };
    expect(rescoreMessage(r)).toBe(
      "Re-scored #2A and #2B with Groundedness: 2 scores changed on 1 row across 2 runs (2 Pass → Fail). #2A and #2B keep their scores.",
    );
  });

  it("several tries of one eval on one run say 'across tries'", () => {
    const r = {
      unchanged: false,
      eval_names: ["Tone"],
      labels: ["#2A·2"],
      runs: [
        child({
          flipped_rows: ["r1"],
          flipped_eval_keys: ["t"],
          flipped_on_several_tries: true,
          fail_to_pass: 0,
          pass_to_fail: 3,
        }),
      ],
      fail_to_pass: 0,
      pass_to_fail: 3,
    };
    expect(rescoreMessage(r)).toBe(
      "Re-scored #2A with Tone: 3 scores changed on 1 row across tries (3 Pass → Fail). #2A keeps its scores.",
    );
  });

  it("an unchanged re-score names each eval's version (review S4)", () => {
    expect(
      rescoreMessage({
        unchanged: true,
        same_definition: true,
        eval_version: "v3",
        eval_versions: [
          { key: "c", name: "Correctness", versions: ["v3"] },
          { key: "t", name: "Tone", versions: ["v1"] },
        ],
        unchanged_labels: ["#2A"],
      }),
    ).toBe("Scores unchanged · same eval versions (Correctness v3 · Tone v1)");
    expect(
      rescoreMessage({
        unchanged: true,
        same_definition: true,
        eval_versions: [
          { key: "c", name: "Correctness", versions: ["v2", "v3"] },
        ],
        unchanged_labels: ["#2A", "#2B"],
      }),
    ).toBe("Scores unchanged · same eval version (Correctness v2 and v3)");
  });
});

describe("rescoreActions with several new runs (review M5)", () => {
  it("names the run whose rows See opens", () => {
    const r = {
      unchanged: false,
      eval_names: ["Groundedness"],
      labels: ["#2A·2", "#2B·2"],
      runs: [
        child({ flipped_rows: ["r1", "r2"] }),
        child({
          run_id: "c2b",
          label: "#2B·2",
          from_run_id: "a2b",
          from_label: "#2B",
          flipped_rows: ["r1", "r3", "r4"],
        }),
      ],
      fail_to_pass: 0,
      pass_to_fail: 5,
    };
    expect(rescoreActions(r).rows).toMatchObject({
      label: "See the 2 rows in #2A·2",
      runId: "c2a",
      rowIds: ["r1", "r2"],
    });
  });

  it("an Add evaluation confirm says the eval joins and scores; an empty plan promises no run", () => {
    const runs = [
      { run_id: "a", label: "#2A", attempt: 1 },
      { run_id: "b", label: "#2B", attempt: 1 },
    ];
    expect(
      rescoreConfirmBody({
        runs,
        evalNames: ["Task completion"],
        adding: true,
      }),
    ).toBe(
      "Outputs stay as they are. Task completion joins the experiment and scores the outputs of #2A and #2B in new runs (#2A·2 and #2B·2); #2A and #2B keep their scores.",
    );
    expect(
      rescoreConfirmBody({ runs, evalNames: ["Tone"], nothing: true }),
    ).toBe(
      "Outputs stay as they are. Tone already reads this definition on #2A and #2B, so no new run is made.",
    );
  });
});

describe("rescoreConfirmBody with the plan's labels", () => {
  it("promises the label the server will give each new run", () => {
    // #2A·2 exists, so a re-score of #2A makes #2A·3 (the plan says so);
    // a run the plan finds nothing on makes no run.
    expect(
      rescoreConfirmBody({
        runs: [
          { run_id: "a", label: "#2A", next_label: "#2A·3" },
          { run_id: "b", label: "#2B", next_label: null },
        ],
        evalNames: ["Correctness"],
      }),
    ).toBe(
      "Outputs stay as they are. Correctness scores the outputs of #2A and #2B again in a new run (#2A·3); #2A and #2B keep their scores. When no score moves, no run is made.",
    );
  });
});
