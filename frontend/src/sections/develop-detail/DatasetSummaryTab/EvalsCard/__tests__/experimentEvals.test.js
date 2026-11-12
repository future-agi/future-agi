import { describe, expect, it } from "vitest";
import { evalRateRows, latestExperiment } from "../experimentEvals";

const run = (id, exp, letter, status, at) => ({
  run_id: id,
  experiment_id: exp,
  experiment_name: `Exp ${exp}`,
  letter,
  label: `#${exp}${letter}`,
  status,
  created_at: at,
});

describe("latestExperiment", () => {
  const runs = [
    run("1a", "1", "A", "Completed", "2026-10-01"),
    run("2b", "2", "B", "Completed", "2026-10-10"),
    run("2a", "2", "A", "Completed", "2026-10-10"),
    run("3a", "3", "A", "Running", "2026-10-12"),
  ];
  it("picks the newest experiment with a finished run, runs by letter", () => {
    expect(latestExperiment(runs, "2b")).toEqual({
      experimentId: "2",
      experimentName: "Exp 2",
      runIds: ["2a", "2b"],
      baselineId: "2b",
    });
  });
  it.each(["completed", "partial"])(
    "selects native experiments with %s runs",
    (status) => {
      const nativeRuns = [
        run("4b", "4", "B", status, "2026-10-14"),
        run("4a", "4", "A", status, "2026-10-14"),
      ];
      const expected = {
        experimentId: "4",
        experimentName: "Exp 4",
        runIds: ["4a", "4b"],
        baselineId: "4b",
      };
      expect(latestExperiment(nativeRuns, "4b")).toEqual(expected);
      expect(
        latestExperiment(
          [
            ...runs,
            ...nativeRuns,
            run("5a", "5", "A", "running", "2026-10-15"),
          ],
          "4b",
        ),
      ).toEqual(expected);
      expect(latestExperiment(nativeRuns, "1a").baselineId).toBe("4a");
    },
  );
  it("keeps legacy partially completed experiments eligible", () => {
    expect(
      latestExperiment([run("1a", "1", "A", "PartialCompleted", "2026-10-01")]),
    ).toMatchObject({ experimentId: "1", runIds: ["1a"], baselineId: "1a" });
  });
  it.each(["draft", "queued", "running", "failed", "cancelled", "unknown", null])(
    "ignores runs with non-showable status %s",
    (status) => {
      expect(
        latestExperiment([run("1a", "1", "A", status, "2026-10-01")]),
      ).toBeNull();
    },
  );
  it("falls back to run A when the pinned run is elsewhere; null without runs", () => {
    expect(latestExperiment(runs, "1a").baselineId).toBe("2a");
    expect(latestExperiment([], null)).toBeNull();
    expect(latestExperiment([run("x", "9", "A", "Running", "z")])).toBeNull();
  });
});

describe("evalRateRows", () => {
  const analysis = {
    runs: [
      { run_id: "a", success_rate: 0.533 },
      { run_id: "b", success_rate: 0.656 },
    ],
    metrics: [
      { key: "ops:cost", family: "ops", per_run: [] },
      {
        key: "check:order_found",
        name: "Order found",
        family: "eval",
        output_type: "pass_fail",
        per_run: [
          { run_id: "a", n: 30, value: 0.8, pass_rate: null },
          { run_id: "b", n: 30, value: 0.6, pass_rate: null },
        ],
      },
      {
        key: "eval:task_success",
        name: "Task success",
        family: "eval",
        per_run: [
          { run_id: "a", n: 30, value: 0.5, pass_rate: 0.567 },
          { run_id: "b", n: 30, value: 0.6, pass_rate: 0.667 },
        ],
      },
      {
        key: "eval:reply_rubric",
        name: "Task reply rubric v1",
        family: "eval",
        per_run: [{ run_id: "a", n: 0 }],
      },
    ],
  };
  it("lists scored evals, primary first, checks last, best run marked", () => {
    const rows = evalRateRows(analysis, { primaryKey: "eval:task_success" });
    expect(rows.map((r) => r.key)).toEqual([
      "eval:task_success",
      "check:order_found",
    ]);
    expect(rows[0]).toMatchObject({
      kind: "eval",
      rates: { a: 0.533, b: 0.656 },
      bestRunId: "b",
    });
    expect(rows[1]).toMatchObject({ kind: "check", bestRunId: "a" });
  });
});
