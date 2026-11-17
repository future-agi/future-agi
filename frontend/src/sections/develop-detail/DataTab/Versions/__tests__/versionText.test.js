import { describe, expect, it } from "vitest";
import { applyOp } from "../changeSetStore";
import {
  changesTitle,
  compactAgo,
  dayBucket,
  describeChangeSet,
  groupOpsByRow,
  groupVersions,
  pendingCountsText,
  pendingTotal,
  shortValue,
  tagError,
  tagMoveText,
} from "../versionText";

const NOW = new Date("2026-10-15T15:00:00").getTime();
const DAY = 24 * 3600 * 1000;
const at = (msAgo) => new Date(NOW - msAgo).toISOString();

const names = {
  rowLabel: (id) => ({ R241: "Row 241", R12: "Row 12" })[id] ?? id,
  columnName: (id) =>
    ({ C3: "expected_reply", C1: "customer_message" })[id] ?? id,
  splitName: (id) => ({ S_HARD: "hard", S_TEST: "test" })[id] ?? id,
};

const build = (...ops) => ops.reduce((acc, op) => applyOp(acc, op), []);

describe("pendingCountsText", () => {
  it("reads the bar's counts and drops zeros", () => {
    expect(
      pendingCountsText({ added: 2, edited: 5, deleted: 1, split_changes: 3 }),
    ).toBe("2 added · 5 edited · 1 deleted · 3 split changes");
    expect(
      pendingCountsText({ added: 0, edited: 1, deleted: 0, split_changes: 1 }),
    ).toBe("1 edited · 1 split change");
    expect(
      pendingCountsText({
        added: 0,
        edited: 0,
        deleted: null,
        split_changes: 0,
      }),
    ).toBe("matching rows deleted");
  });

  it("totals the changes for the interstitial", () => {
    expect(
      pendingTotal({ added: 0, edited: 1, deleted: 0, split_changes: 1 }),
    ).toBe(2);
  });
});

describe("History grouping", () => {
  it("buckets Today / Yesterday / This week / Older", () => {
    expect(dayBucket(at(2 * 3600 * 1000), NOW)).toBe("Today");
    expect(dayBucket(at(DAY), NOW)).toBe("Yesterday");
    expect(dayBucket(at(3 * DAY), NOW)).toBe("This week");
    expect(dayBucket(at(14 * DAY), NOW)).toBe("Older");
  });

  it("keeps order and drops empty groups", () => {
    const groups = groupVersions(
      [
        { number: 7, created_at: at(60_000) },
        { number: 6, created_at: at(3 * DAY) },
        { number: 5, created_at: at(6 * DAY) },
        { number: 4, created_at: at(14 * DAY) },
      ],
      NOW,
    );
    expect(
      groups.map((g) => [g.label, g.versions.map((v) => v.number)]),
    ).toEqual([
      ["Today", [7]],
      ["This week", [6, 5]],
      ["Older", [4]],
    ]);
  });

  it("formats compact ages", () => {
    expect(compactAgo(at(10_000), NOW)).toBe("just now");
    expect(compactAgo(at(5 * 60_000), NOW)).toBe("5m ago");
    expect(compactAgo(at(2 * 3600_000), NOW)).toBe("2h ago");
    expect(compactAgo(at(3 * DAY), NOW)).toBe("3d ago");
  });
});

describe("Save dialog change list", () => {
  it("groups ops by row in first-touched order", () => {
    const ops = build(
      {
        op: "update_cell",
        row_id: "R241",
        column_id: "C3",
        value: "Fixed",
        before: "",
      },
      { op: "set_splits", row_ids: ["R241"], add: ["S_HARD"] },
      { op: "add_row", temp_id: "new-1", cells: { C1: "Where is my parcel?" } },
      { op: "delete_rows", row_ids: ["R12"] },
    );
    const { rows, selectionDelete } = groupOpsByRow(ops);
    expect(selectionDelete).toBeNull();
    expect(
      rows.map((g) => [g.rowId, g.isNew, g.items.map((i) => i.kind)]),
    ).toEqual([
      ["R241", false, ["cell", "splits"]],
      ["new-1", true, ["added"]],
      ["R12", false, ["deleted"]],
    ]);
  });

  it("returns a whole-selection delete on its own", () => {
    const ops = build({
      op: "delete_rows",
      selection: { selected_all_rows: true, exclude_row_ids: [], filters: [] },
    });
    expect(groupOpsByRow(ops).selectionDelete).not.toBeNull();
  });
});

describe("describeChangeSet", () => {
  it("names a single filled cell and its split (J3)", () => {
    const ops = build(
      {
        op: "update_cell",
        row_id: "R241",
        column_id: "C3",
        value: "Fixed",
        before: "",
      },
      { op: "set_splits", row_ids: ["R241"], add: ["S_HARD"] },
    );
    expect(describeChangeSet(ops, names)).toBe(
      "Filled expected_reply on Row 241; added Row 241 to split hard",
    );
  });

  it("uses the import description when every op is an imported row", () => {
    const hint =
      "Added 2 rows from support-prod sessions as tasks (Resolution = Fail, last 7 days)";
    const ops = build(
      { op: "add_row", temp_id: "n1", description_hint: hint },
      { op: "add_row", temp_id: "n2", description_hint: hint },
    );
    expect(describeChangeSet(ops, names)).toBe(hint);
    const mixed = build(
      { op: "add_row", temp_id: "n1", description_hint: hint },
      { op: "set_splits", row_ids: ["a"], add: ["S_HARD"] },
    );
    expect(describeChangeSet(mixed, names)).not.toBe(hint);
  });

  it("describes split assignment of many rows (J12)", () => {
    const ops = build({
      op: "set_splits",
      row_ids: ["a", "b", "c"],
      add: ["S_HARD"],
    });
    expect(describeChangeSet(ops, names)).toBe("Added 3 rows to split hard");
  });

  it("summarises mixed edits", () => {
    const ops = build(
      {
        op: "update_cell",
        row_id: "R12",
        column_id: "C3",
        value: "x",
        before: "y",
      },
      {
        op: "update_cell",
        row_id: "R241",
        column_id: "C3",
        value: "z",
        before: "w",
      },
      { op: "add_row", temp_id: "new-1", cells: {} },
      { op: "delete_rows", row_ids: ["R9"] },
    );
    expect(describeChangeSet(ops, names)).toBe(
      "Edited 2 rows, added 1, deleted 1",
    );
  });
});

describe("tags", () => {
  const versions = [
    { number: 6, tags: [] },
    { number: 5, tags: ["golden-v3"] },
  ];
  it("explains a move, a new tag and a no-op", () => {
    expect(tagMoveText("golden-v3", versions, 7)).toBe(
      "golden-v3 moves from v5 to v7",
    );
    expect(tagMoveText("golden-v4", versions, 7)).toBe(
      "New tag · points at v7",
    );
    expect(tagMoveText("golden-v3", versions, 5)).toBe("Already on v5");
  });
  it("validates the tag pattern", () => {
    expect(tagError("golden-v4")).toBeNull();
    expect(tagError("Golden V4")).toMatch(/lowercase/);
  });
});

describe("small formatters", () => {
  it("titles an N4 result", () => {
    expect(
      changesTitle({
        from: 3,
        to: 6,
        counts: { added: 26, modified: 8, deleted: 2 },
      }),
    ).toBe("v3 → v6 · 26 added · 8 edited · 2 deleted");
    expect(changesTitle({ from: 0, to: 1, counts: { added: 200 } })).toBe(
      "v1 · 200 added · 0 edited · 0 deleted",
    );
  });
  it("flattens and clips values", () => {
    expect(shortValue("a\n  b", 10)).toBe("a b");
    expect(shortValue(null)).toBe("empty");
    expect(shortValue("x".repeat(20), 10)).toHaveLength(10);
  });
});
