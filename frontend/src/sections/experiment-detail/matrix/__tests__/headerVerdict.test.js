import { describe, expect, it } from "vitest";
import { headerStatusText, nothingRanYet } from "../headerVerdict";
import { runProgressOf } from "src/sections/common/Runs/runStatus";

describe("IT-R5 G6 P1 header status (rule 36, S3)", () => {
  const running = { key: "Running", label: "Running", active: true };
  const queued = { key: "Queued", label: "Queued", active: true };
  it("reads the runs' own progress and never says Queued once a row is done", () => {
    expect(headerStatusText(queued, { done: 23, total: 312 })).toBe(
      "Running · 23/312",
    );
    expect(headerStatusText(running, { done: 0, total: 312 })).toBe("Waiting");
    expect(headerStatusText({ key: "Completed", label: "Completed" }, {})).toBe(
      "Completed",
    );
    expect(
      headerStatusText(
        {
          key: "WaitingForResults",
          label: "Waiting for results",
          waiting: true,
        },
        { done: 0, total: 30 },
      ),
    ).toBe("Waiting");
  });
  it("nothing ran yet only when no run has a result", () => {
    expect(
      nothingRanYet([{ progress: { done: 0, total: 30 } }], runProgressOf),
    ).toBe(true);
    expect(
      nothingRanYet(
        [
          { progress: { done: 0, total: 30 } },
          { progress: { done: 3, total: 30 } },
        ],
        runProgressOf,
      ),
    ).toBe(false);
    expect(nothingRanYet([], runProgressOf)).toBe(false);
  });
});
