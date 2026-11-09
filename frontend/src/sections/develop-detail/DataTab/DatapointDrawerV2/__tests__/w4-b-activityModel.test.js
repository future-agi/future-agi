// W4-1 (Lane B): the Activity tab's Comments chip (additive exports).
import { describe, expect, it } from "vitest";
import {
  ACTIVITY_FILTERS,
  COLLAB_ACTIVITY_FILTERS,
  COMMENT_STATUS_FILTERS,
  collabActivityCounts,
  commentStatusCounts,
  filterCollabActivity,
} from "../activityModel";

const ev = [
  { type: "run", at: "2026-10-22T10:00:00Z" },
  { type: "comment", at: "2026-10-22T09:00:00Z", resolved: false },
  { type: "comment", at: "2026-10-21T09:00:00Z", resolved: true },
  { type: "edit", at: "2026-10-20T09:00:00Z" },
];

describe("activityModel W4-1", () => {
  it("adds Comments after All, keeps the old chips as they were", () => {
    expect(COLLAB_ACTIVITY_FILTERS.map((f) => f.label)).toEqual([
      "All",
      "Comments",
      "Runs",
      "Edits",
      "Annotations",
      "Splits",
    ]);
    expect(ACTIVITY_FILTERS.map((f) => f.key)).toEqual([
      "all",
      "runs",
      "edits",
      "annotations",
      "splits",
    ]);
  });

  it("counts and filters comment threads", () => {
    expect(collabActivityCounts(ev)).toEqual({
      all: 4,
      comments: 2,
      runs: 1,
      edits: 1,
      annotations: 0,
      splits: 0,
    });
    expect(filterCollabActivity(ev, "comments").map((e) => e.at)).toEqual([
      ev[1].at,
      ev[2].at,
    ]);
    expect(filterCollabActivity(ev, "nope")).toHaveLength(4);
    expect(commentStatusCounts(ev)).toEqual({ open: 1, resolved: 1, all: 2 });
    expect(COMMENT_STATUS_FILTERS.map((f) => f.label)).toEqual([
      "Open",
      "Resolved",
      "All",
    ]);
  });
});

describe("activityModel W4-1 deleted roots", () => {
  it("a deleted root with replies counts under All only", () => {
    const withDeleted = [
      ...ev,
      { type: "comment", at: "2026-10-19T09:00:00Z", deleted: true },
    ];
    expect(commentStatusCounts(withDeleted)).toEqual({
      open: 1,
      resolved: 1,
      all: 3,
    });
  });
});
