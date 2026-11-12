import { describe, expect, it } from "vitest";
import {
  bestPending,
  bestRunFor,
  isRunFinal,
  isRunPending,
  partialNote,
  partialProgress,
  rankPool,
  unrankedNote,
} from "../leaderboardModel";

const CORRECTNESS = {
  key: "eval:correctness",
  output_type: "pass_fail",
  higher_is_better: true,
};

const run = (label, status, done, total = 48, avg = 0.8) => ({
  run_id: label,
  label,
  status,
  progress: { done, total },
  metrics: { aggregates: { [CORRECTNESS.key]: { avg } } },
});

const A7 = run("#7A", "Running", 13, 48, 0.786);
const B7 = run("#7B", "Running", 14, 48, 0.55);
const DONE = run("#2B", "Completed", 48, 48, 0.866);
const PART = run("#2A", "PartialCompleted", 48, 48, 0.765);
const STOP = run("#8A", "Cancelled", 96, 248, 0.9);
const WAIT = run("#6A", "WaitingForResults", 0, 48, null);

describe("finished-only ranking (critic r3 #2)", () => {
  it("a run is final only when terminal-complete with every task done", () => {
    expect(isRunFinal(DONE)).toBe(true);
    expect(isRunFinal(PART)).toBe(true);
    expect(isRunFinal(A7)).toBe(false);
    expect(isRunFinal(STOP)).toBe(false);
    expect(isRunFinal(run("#9A", "Completed", 40, 48))).toBe(false);
    expect(isRunFinal({ status: "Completed" })).toBe(true);
  });

  it("pending = running, scoring, queued or waiting", () => {
    expect([A7, WAIT, STOP, DONE].map(isRunPending)).toEqual([
      true,
      true,
      false,
      false,
    ]);
  });

  it("rankPool keeps running runs out of the best pick", () => {
    const { finished, pending } = rankPool([A7, B7, DONE, STOP]);
    expect(finished.map((r) => r.label)).toEqual(["#2B"]);
    expect(pending.map((r) => r.label)).toEqual(["#7A", "#7B"]);
    // #7A has the higher raw value but is still running: #2B wins.
    expect(bestRunFor(finished, CORRECTNESS).label).toBe("#2B");
    expect(bestRunFor(rankPool([A7, B7]).finished, CORRECTNESS)).toBe(null);
  });

  it("says the best is pending, naming each run's progress", () => {
    expect(bestPending([A7, B7])).toEqual({
      label: "Best pending",
      tip: "2 runs are still running (#7A 13/48, #7B 14/48). The best run is named when they finish; until then the numbers move.",
    });
    expect(bestPending([A7]).tip).toMatch(
      /^1 run is still running .* when it finishes;/,
    );
    expect(bestPending([])).toBe(null);
    expect(unrankedNote([A7, B7])).toBe(
      "2 runs still running are not ranked yet.",
    );
    expect(unrankedNote([A7])).toBe("1 run still running is not ranked yet.");
    expect(unrankedNote([])).toBe(null);
  });

  it("partial runs carry how much their numbers cover", () => {
    expect(partialProgress(B7)).toEqual({ done: 14, total: 48 });
    expect(partialNote(B7)).toBe("Based on 14 of 48 tasks, may change");
    expect(partialNote(STOP, "rows")).toBe(
      "Based on 96 of 248 rows, may change",
    );
    expect(partialNote(DONE)).toBe(null);
    expect(partialProgress(run("#1A", "Running", 0, 0))).toBe(null);
  });
});
