// IT-R5 G6 P5: one rerun estimator for the Rerun menu and the dialog.
import { describe, expect, it } from "vitest";
import {
  estimateText,
  rerunEstimate,
  runnerPays,
  sumEstimates,
} from "../rerunEstimate";

describe("rerunEstimate", () => {
  it("prints rows · tries · cost · time from the run's own per-try numbers", () => {
    const e = rerunEstimate({
      rows: 49,
      trials: 3,
      costPerTry: 1.79 / 147,
      msPerTry: 3200,
      concurrency: 4,
    });
    expect(e.tries).toBe(147);
    expect(estimateText(e, "row")).toBe(
      "49 rows · 147 tries · ~$1.79 · ~2 min",
    );
  });

  it("a CI / upload run: cost not tracked by Future AGI", () => {
    const run = {
      config: {
        kind: "upload",
        code: { mode: "upload", command: "python evals/run.py --upload" },
      },
      trials: 3,
    };
    expect(runnerPays(run)).toBe(true);
    const e = rerunEstimate({
      rows: 10,
      trials: 3,
      costPerTry: 0.01,
      runnerPaid: true,
    });
    expect(estimateText(e, "task")).toBe(
      "10 tasks · 30 tries · cost not tracked by Future AGI",
    );
  });

  it("several runs side by side: tries add up, time is the slowest", () => {
    const a = rerunEstimate({
      rows: 2,
      trials: 3,
      costPerTry: 0.1,
      msPerTry: 60000,
    });
    const b = rerunEstimate({
      rows: 1,
      trials: 3,
      costPerTry: 0.1,
      msPerTry: 120000,
    });
    const s = sumEstimates([a, b]);
    expect(s.tries).toBe(9);
    expect(s.cost).toBeCloseTo(0.9, 5);
    expect(s.ms).toBe(360000);
  });
});
