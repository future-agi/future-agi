import { describe, expect, it } from "vitest";
import { launchedTitleOf } from "../launchedTitle";

describe("launchedTitleOf", () => {
  it("says Results in when every run finished", () => {
    expect(
      launchedTitleOf([
        { done: 24, total: 24 },
        { done: 6, total: 6 },
      ]),
    ).toEqual({
      title: "Results in · 30/30",
      helper: null,
      stopped: false,
    });
  });
  it("names a cost-cap stop, its tries and the tasks not run", () => {
    const r = launchedTitleOf(
      [
        {
          done: 26,
          total: 40,
          stop_line:
            "Stopped at cost cap $0.80 · 6/8 tasks × 5 tries · 22 passed",
          rows_done: 6,
          rows_total: 8,
        },
      ],
      { rowNoun: "task" },
    );
    expect(r.title).toBe("Stopped at cost cap $0.80 · 26 of 40 tries");
    expect(r.helper).toBe(
      "2 tasks were not run. Resume them or raise the cap.",
    );
    expect(r.stopped).toBe(true);
  });
  it("one row left reads singular; numbers follow the inputs", () => {
    const r = launchedTitleOf([
      {
        done: 9,
        total: 12,
        stop_line: "Stopped at cost cap $0.05 · 3/4 rows × 3 tries",
        rows_done: 3,
        rows_total: 4,
      },
    ]);
    expect(r.title).toBe("Stopped at cost cap $0.05 · 9 of 12 tries");
    expect(r.helper).toBe("1 row was not run. Resume it or raise the cap.");
  });
});
