// v2.1 g8 (assign:results-selection): rows assigned from a run's results
// show their assignment in the dataset Row view when the row has none of
// its own; a row-level assignment still wins.
import { describe, expect, it } from "vitest";
import { openAssignmentOf, openAssignmentOnAnyRun } from "../assignModel";

const A = (id, row_id, run_id, extra = {}) => ({
  id,
  row_id,
  run_id,
  status: "open",
  assignee: { id: "u-ravi", name: "Ravi Menon" },
  created_at: "2026-10-20T10:00:00Z",
  ...extra,
});

describe("results-made assignments in the Row view", () => {
  it("falls back to the newest open result assignment", () => {
    const list = [
      A("a1", "r3", "run-4a", { created_at: "2026-10-20T10:00:00Z" }),
      A("a2", "r3", "run-4b", { created_at: "2026-10-21T10:00:00Z" }),
      A("a3", "r3", "run-4c", { status: "done" }),
    ];
    expect(openAssignmentOf(list, "r3")).toBeNull();
    expect(openAssignmentOnAnyRun(list, "r3").id).toBe("a2");
  });

  it("a row-level assignment is found first; other rows don't leak in", () => {
    const list = [
      A("a1", "r3", "run-4a"),
      A("a9", "r3", null),
      A("a5", "r4", "run-4a"),
    ];
    expect(openAssignmentOf(list, "r3").id).toBe("a9");
    expect(openAssignmentOnAnyRun(list, "r5")).toBeNull();
  });
});
