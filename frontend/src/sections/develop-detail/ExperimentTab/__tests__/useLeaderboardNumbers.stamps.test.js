// L3 finalize: the leaderboard matrix is keyed by its runs' progress, so a
// run created on the page re-reads the matrix as it moves and once it ends.
import { describe, expect, it } from "vitest";
import { runProgressStamps } from "../useLeaderboardNumbers";

describe("runProgressStamps", () => {
  it("stamps each run by status and done count", () => {
    expect(
      runProgressStamps([
        { run_id: "a", status: "Running", progress: { done: 4, total: 30 } },
        { run_id: "b", status: "Completed", progress: { done: 30 } },
        { run_id: "c" },
        { status: "Running" },
        null,
      ]),
    ).toEqual({ a: "Running:4", b: "Completed:30", c: ":" });
  });
  it("changes when a run moves or ends, not otherwise", () => {
    const running = runProgressStamps([
      { run_id: "a", status: "Running", progress: { done: 4 } },
    ]);
    const moved = runProgressStamps([
      { run_id: "a", status: "Running", progress: { done: 9 } },
    ]);
    const ended = runProgressStamps([
      { run_id: "a", status: "Completed", progress: { done: 30 } },
    ]);
    const same = runProgressStamps([
      { run_id: "a", status: "Running", progress: { done: 4 }, name: "x" },
    ]);
    expect(moved.a).not.toBe(running.a);
    expect(ended.a).not.toBe(moved.a);
    expect(same.a).toBe(running.a);
    expect(runProgressStamps()).toEqual({});
  });
});
