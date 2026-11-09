// W4-2 (Lane B): assignment pills, due quick picks, overdue and copy.
import { describe, expect, it } from "vitest";
import {
  assignTitle,
  assignedToast,
  dateInputValue,
  dueFromDateInput,
  dueLabel,
  dueQuickPicks,
  footerAssignedText,
  moveLines,
  openAssignmentOf,
  overdueDays,
  pillOf,
} from "../assignModel";

// Thu 22 Oct 2026, 15:00 local.
const NOW = new Date(2026, 9, 22, 15, 0, 0).getTime();
const endOf = (y, m, d) => new Date(y, m, d, 23, 59, 59, 999).toISOString();
const a = (over) => ({
  id: "a1",
  row_id: "r1",
  run_id: null,
  status: "open",
  assignee: { id: "u-meera", name: "Meera Iyer" },
  assigned_by: { id: "u-me", name: "Maya Chen" },
  due_at: endOf(2026, 9, 23),
  ...over,
});

describe("assignModel", () => {
  it("labels due dates and counts overdue days from the end of the due day", () => {
    expect(dueLabel(endOf(2026, 9, 23), NOW)).toBe("Fri 23 Oct");
    expect(dueLabel(endOf(2027, 0, 4), NOW)).toBe("Mon 4 Jan 2027");
    expect(overdueDays(a({ due_at: endOf(2026, 9, 22) }), NOW)).toBe(0);
    expect(overdueDays(a({ due_at: endOf(2026, 9, 20) }), NOW)).toBe(2);
    expect(
      overdueDays(a({ due_at: endOf(2026, 9, 20), status: "done" }), NOW),
    ).toBe(0);
    expect(overdueDays(a({ due_at: null }), NOW)).toBe(0);
  });

  it("writes the Row view pill", () => {
    expect(pillOf(a(), "u-me", NOW)).toEqual({
      text: "Assigned to Meera Iyer · due Fri 23 Oct",
      tone: "default",
      mine: false,
    });
    expect(pillOf(a({ due_at: endOf(2026, 9, 20) }), "u-me", NOW)).toEqual({
      text: "Assigned to Meera Iyer · Overdue · 2 days",
      tone: "red",
      mine: false,
    });
    const mine = a({
      assignee: { id: "u-me", name: "Maya Chen" },
      assigned_by: { id: "u-meera", name: "Meera Iyer" },
    });
    expect(pillOf(mine, "u-me", NOW)).toEqual({
      text: "Assigned to you by Meera Iyer · due Fri 23 Oct",
      tone: "amber",
      mine: true,
    });
    expect(pillOf(a({ status: "done" }), "u-me", NOW).text).toBe(
      "Done · Meera Iyer",
    );
    expect(pillOf(null, "u-me", NOW)).toBeNull();
  });

  it("quick picks: Today · Tomorrow · Friday · Next week", () => {
    const picks = dueQuickPicks(NOW);
    expect(picks.map((p) => p.label)).toEqual([
      "Today",
      "Tomorrow",
      "Friday",
      "Next week",
    ]);
    expect(picks.map((p) => dateInputValue(p.iso))).toEqual([
      "2026-10-22",
      "2026-10-23",
      "2026-10-23",
      "2026-10-26",
    ]);
    // On a Friday, "Friday" is next week's.
    const fri = new Date(2026, 9, 23, 9).getTime();
    expect(dateInputValue(dueQuickPicks(fri)[2].iso)).toBe("2026-10-30");
    expect(dateInputValue(dueFromDateInput("2026-10-24"))).toBe("2026-10-24");
    expect(dueFromDateInput("24/10")).toBeNull();
  });

  it("says which rows move and from whom", () => {
    const list = [
      a({ row_id: "r1", assignee: { id: "u-asha", name: "Asha Rao" } }),
      a({ row_id: "r2", assignee: { id: "u-asha", name: "Asha Rao" } }),
      a({ row_id: "r3", assignee: { id: "u-meera", name: "Meera Iyer" } }),
      a({ row_id: "r9", assignee: { id: "u-ravi", name: "Ravi Menon" } }),
    ];
    expect(
      moveLines(["r1", "r2", "r3", "r4", "r5"], list, "Meera Iyer", "u-meera"),
    ).toEqual([
      "2 of these rows are assigned to Asha Rao; they move to Meera Iyer.",
    ]);
    expect(moveLines(["r1"], list, "Meera Iyer", "u-meera")).toEqual([
      "This row is assigned to Asha Rao; it moves to Meera Iyer.",
    ]);
    expect(moveLines(["r1"], list, "Meera Iyer", "u-meera", "u-asha")).toEqual([
      "This row is assigned to you; it moves to Meera Iyer.",
    ]);
    expect(moveLines(["r1", "r2"], list, "Ravi Menon", "u-ravi")).toEqual([
      "All 2 rows are assigned to Asha Rao; they move to Ravi Menon.",
    ]);
  });

  it("titles, toasts and footer text", () => {
    expect(assignTitle(5)).toBe("Assign 5 rows");
    expect(assignTitle(1)).toBe("Assign 1 row");
    expect(assignedToast(5, "Meera Iyer", endOf(2026, 9, 23), NOW)).toBe(
      "Assigned 5 rows to Meera Iyer · due Fri 23 Oct",
    );
    expect(assignedToast(1, "Meera Iyer", null, NOW)).toBe(
      "Assigned 1 row to Meera Iyer",
    );
    expect(footerAssignedText({ open: 5, overdue: 1 })).toBe(
      "5 assigned to you · 1 overdue",
    );
    expect(footerAssignedText({ open: 5, overdue: 0 })).toBe(
      "5 assigned to you",
    );
    expect(footerAssignedText({ open: 0 })).toBe("");
    expect(
      openAssignmentOf([a(), a({ id: "a2", run_id: "run" })], "r1", "run").id,
    ).toBe("a2");
    expect(openAssignmentOf([a({ status: "done" })], "r1")).toBeNull();
  });
});

describe("footer assigned text with rows", () => {
  it("names the rows when a result row is assigned too", () => {
    expect(footerAssignedText({ open: 6, overdue: 1 }, 5)).toBe(
      "6 assigned to you on 5 rows · 1 overdue",
    );
    expect(footerAssignedText({ open: 5, overdue: 0 }, 5)).toBe(
      "5 assigned to you",
    );
    expect(footerAssignedText({ open: 2, overdue: 0 }, 1)).toBe(
      "2 assigned to you on 1 row",
    );
  });
});
