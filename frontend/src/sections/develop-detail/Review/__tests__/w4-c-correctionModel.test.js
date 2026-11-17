// W4 C1: the corrections block's pure helpers (version note, ops, defaults,
// titles, the submit event match).
import { describe, expect, it } from "vitest";
import {
  cellConflicts,
  changed,
  correctionOps,
  correctionTitle,
  defaultAccepted,
  isSubmitOf,
  reviewerNames,
  saveLabel,
  statusWords,
  versionNote,
} from "../correctionModel";

const c = (n, over = {}) => ({
  id: String(n),
  row_id: `r${n}`,
  row_label: `Row ${n}`,
  column_id: "col",
  column_name: "expected_reply",
  before: "old",
  after: `new ${n}`,
  status: "suggested",
  stale: false,
  in_dataset: false,
  updated_at: `2026-10-22T10:0${n}:00Z`,
  reviewer: { id: "u1", name: "Meera Iyer", outside: false },
  source: { queue_id: "q", queue_name: "Quality · #4A · Oct 12" },
  ...over,
});

describe("versionNote", () => {
  it("names the queue and every reviewer once, in order", () => {
    const list = [c(1), c(2, { reviewer: { name: "Asha Rao" } }), c(3)];
    expect(reviewerNames(list)).toEqual(["Meera Iyer", "Asha Rao"]);
    expect(versionNote(list)).toBe(
      "3 corrections from review “Quality · #4A · Oct 12” by Meera Iyer, Asha Rao",
    );
    expect(versionNote([c(1)])).toBe(
      "1 correction from review “Quality · #4A · Oct 12” by Meera Iyer",
    );
    expect(
      versionNote([c(1), c(2, { source: { queue_name: "Other" } })]),
    ).toMatch(/^2 corrections from 2 reviews by Meera Iyer$/);
  });

  it("saveLabel reads like the button", () => {
    expect(saveLabel(1, 7)).toBe("Save 1 correction as v7");
    expect(saveLabel(12, 7)).toBe("Save 12 corrections as v7");
  });
});

describe("ops and defaults", () => {
  it("one update_cell per cell, the last correction wins", () => {
    const a = c(1);
    const b = c(2, { row_id: "r1" });
    expect(correctionOps([a, b])).toEqual([
      { op: "update_cell", row_id: "r1", column_id: "col", value: "new 2" },
    ]);
    expect(cellConflicts([a, b])).toEqual([["1", "2"]]);
  });

  it("accepts suggested ones by default, not stale, in the dataset or decided", () => {
    const set = defaultAccepted([
      c(1),
      c(2, { stale: true }),
      c(3, { in_dataset: true }),
      c(4, { status: "dismissed" }),
      c(5, { row_id: "r1" }),
    ]);
    expect([...set]).toEqual(["5"]);
  });
});

describe("words", () => {
  it("titles and statuses", () => {
    expect(correctionTitle(c(33))).toBe(
      "Row 33 · expected_reply · by Meera Iyer",
    );
    expect(
      correctionTitle(
        c(1, { reviewer: { name: "qa1@vendor.test", outside: true } }),
      ),
    ).toBe("Row 1 · expected_reply · by qa1@vendor.test (outside reviewer)");
    expect(statusWords(c(1, { status: "applied", applied_version: 7 }))).toBe(
      "Saved in v7",
    );
    expect(statusWords(c(1, { stale: true }))).toMatch(/changed since/);
    expect(changed("a", "a")).toBe(false);
    expect(changed("a", "  ")).toBe(false);
    expect(changed("a", "b")).toBe(true);
  });

  it("matches only this item's successful annotation submit", () => {
    const ctx = { queue_id: "q", item_id: "i" };
    const ev = (v, type = "success") => ({
      type: "updated",
      action: { type },
      mutation: { state: { variables: v } },
    });
    expect(
      isSubmitOf(ev({ queueId: "q", itemId: "i", annotations: [] }), ctx),
    ).toBe(true);
    expect(
      isSubmitOf(ev({ queueId: "q", itemId: "x", annotations: [] }), ctx),
    ).toBe(false);
    expect(isSubmitOf(ev({ queueId: "q", itemId: "i" }), ctx)).toBe(false);
    expect(
      isSubmitOf(
        ev({ queueId: "q", itemId: "i", annotations: [] }, "error"),
        ctx,
      ),
    ).toBe(false);
  });
});
