// W6 C5: the human baseline's plan (pure).
import { describe, expect, it } from "vitest";
import {
  assignRows,
  humanBodyOf,
  humanRunName,
  p50Ms,
  progressLine,
  rowsPerPerson,
  validateHuman,
  withinLimit,
} from "../humanSourceModel";

const ROWS = ["r1", "r2", "r3", "r4", "r5", "r6"];
const MEMBERS = [{ id: "asha" }, { id: "ben" }, { id: "chen" }];

describe("W6 C5 human source model", () => {
  it("split gives each row to one person, round robin", () => {
    const a = assignRows(ROWS, ["asha", "ben"]);
    expect(a.map((x) => x.assignee)).toEqual([
      "asha",
      "ben",
      "asha",
      "ben",
      "asha",
      "ben",
    ]);
    expect(rowsPerPerson(a)).toEqual({ asha: 3, ben: 3 });
  });

  it("overlap k gives each row to k people, rotating", () => {
    const a = assignRows(ROWS.slice(0, 3), MEMBERS, {
      mode: "overlap",
      overlap: 2,
    });
    expect(a).toEqual([
      { row_id: "r1", assignee: "asha" },
      { row_id: "r1", assignee: "ben" },
      { row_id: "r2", assignee: "ben" },
      { row_id: "r2", assignee: "chen" },
      { row_id: "r3", assignee: "chen" },
      { row_id: "r3", assignee: "asha" },
    ]);
    expect(assignRows(ROWS, [])).toEqual([]);
  });

  it("names the run by its people", () => {
    expect(humanRunName(["asha", "ben", "chen"])).toBe("Humans (3 people)");
    expect(humanRunName([{ id: "asha" }])).toBe("Humans (1 person)");
  });

  it("validates people, overlap, time limit and rows", () => {
    const ctx = { rows: 6, members: MEMBERS };
    expect(validateHuman({ assignees: [] }, ctx).field).toBe("assignees");
    expect(validateHuman({ assignees: ["zed"] }, ctx).reason).toBe(
      "Only workspace members can answer rows",
    );
    expect(
      validateHuman(
        { assignees: ["asha", "ben"], mode: "overlap", overlap: 3 },
        ctx,
      ).field,
    ).toBe("overlap");
    expect(
      validateHuman({ assignees: ["asha"], time_limit_s: -5 }, ctx).field,
    ).toBe("time_limit_s");
    expect(
      validateHuman({ assignees: ["asha"] }, { ...ctx, rows: 0 }).field,
    ).toBe("rows");
    expect(
      validateHuman({ assignees: ["asha", "ben"], time_limit_s: 120 }, ctx)
        .valid,
    ).toBe(true);
  });

  it("builds the create body's human block without invented defaults", () => {
    expect(
      humanBodyOf({
        assignees: [{ id: "asha" }, "ben"],
        instructions: "  Reply as support. ",
      }),
    ).toEqual({
      assignees: ["asha", "ben"],
      mode: "split",
      overlap: 1,
      time_limit_s: null,
      instructions: "Reply as support.",
    });
    expect(
      humanBodyOf({
        assignees: ["a", "b"],
        mode: "overlap",
        overlap: 2,
        time_limit_s: "90",
      }).time_limit_s,
    ).toBe(90);
  });

  it("progress, p50 and the time limit", () => {
    expect(progressLine(12, 40)).toBe("12 of 40 rows answered");
    expect(p50Ms([3000, 1000, 2000])).toBe(2000);
    expect(p50Ms([1000, 3000])).toBe(2000);
    expect(p50Ms([])).toBeNull();
    expect(withinLimit(90000, 60)).toBe(false);
    expect(withinLimit(90000, null)).toBe(true);
  });
});
