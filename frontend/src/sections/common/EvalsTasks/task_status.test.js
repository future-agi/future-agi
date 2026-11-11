import { describe, expect, it } from "vitest";
import { completedTaskHint } from "./task_status";

const progress = (overrides) => ({
  dispatched: 160,
  completed: 160,
  skipped: 0,
  missing: 0,
  percent: 100,
  ...overrides,
});

describe("completedTaskHint", () => {
  it("says a completed run evaluated nothing when it created no runs", () => {
    expect(
      completedTaskHint("completed", progress({ dispatched: 0, completed: 0 })),
    ).toEqual({
      label: "Nothing evaluated",
      title: "The last run created no evaluation runs.",
    });
  });

  it("names a run where every evaluation was skipped", () => {
    expect(
      completedTaskHint("Completed", progress({ completed: 0, skipped: 160 })),
    ).toEqual({
      label: "All 160 skipped",
      title: "Nothing was evaluated: all 160 evaluation runs were skipped.",
    });
    expect(
      completedTaskHint(
        "completed",
        progress({ dispatched: 1, completed: 0, skipped: 1 }),
      ),
    ).toEqual({
      label: "1 skipped",
      title: "Nothing was evaluated: the only evaluation run was skipped.",
    });
  });

  it("counts a partly skipped run", () => {
    expect(
      completedTaskHint(
        "completed",
        progress({ dispatched: 1500, completed: 1450, skipped: 50 }),
      ),
    ).toEqual({
      label: "50 skipped",
      title: "50 of 1,500 evaluation runs skipped.",
    });
  });

  it("shows nothing for a clean run, an unfinished task or missing counts", () => {
    expect(completedTaskHint("completed", progress())).toBeNull();
    // Running and pending tasks have not dispatched yet; 0 is not an outcome.
    expect(
      completedTaskHint("running", progress({ dispatched: 0, completed: 0 })),
    ).toBeNull();
    expect(
      completedTaskHint("pending", progress({ dispatched: 0, completed: 0 })),
    ).toBeNull();
    // The list endpoints do not send progress yet: no hint, never a 0.
    expect(completedTaskHint("completed", undefined)).toBeNull();
    expect(completedTaskHint("completed", null)).toBeNull();
    expect(
      completedTaskHint("completed", progress({ dispatched: null })),
    ).toBeNull();
    expect(
      completedTaskHint("completed", progress({ skipped: "3" })),
    ).toBeNull();
    expect(
      completedTaskHint("completed", progress({ dispatched: 2, skipped: 5 })),
    ).toBeNull();
  });
});
