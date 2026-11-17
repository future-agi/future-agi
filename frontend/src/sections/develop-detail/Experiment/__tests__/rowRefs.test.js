import { describe, expect, it } from "vitest";
import { resolveRowRefs, rowRefsNote } from "../rowRefs";

const rows = Array.from({ length: 14 }, (_, i) => ({
  row_id: `r-${i}`,
  order: i,
}));

describe("resolveRowRefs (entry:deep-link rows=)", () => {
  it("accepts labels (T5, Row 7, 12) and ids, in link order", () => {
    const res = resolveRowRefs(["T5", "r-0", "Row 7", "12"], rows);
    expect(res.ids).toEqual(["r-4", "r-0", "r-6", "r-11"]);
    expect(res.missing).toEqual([]);
    expect(res.found).toBe(4);
    expect(res.asked).toBe(4);
  });

  it("drops values that name no row and counts them", () => {
    const res = resolveRowRefs(["T5", "T12", "zzz"], rows);
    expect(res.ids).toEqual(["r-4", "r-11"]);
    expect(res.missing).toEqual(["zzz"]);
    expect(rowRefsNote(res)).toBe(
      "2 of 3 rows found · not in this dataset: zzz",
    );
  });

  it("counts a row named twice once", () => {
    const res = resolveRowRefs(["T5", "5", "r-4", "T5"], rows);
    expect(res.ids).toEqual(["r-4"]);
    expect(res.asked).toBe(3);
  });

  it("a label past the last row is missing; nothing found falls back", () => {
    const res = resolveRowRefs(["T99"], rows);
    expect(res.ids).toEqual([]);
    expect(rowRefsNote(res, { noun: "task" })).toBe(
      "0 of 1 task found · not in this dataset: T99 · running on all tasks",
    );
  });

  it("keeps an unknown id when only the first page is loaded", () => {
    const res = resolveRowRefs(["T2", "row-far-down", "T40"], rows, {
      complete: false,
    });
    expect(res.ids).toEqual(["r-1", "row-far-down"]);
    expect(res.missing).toEqual(["T40"]);
  });

  it("no note when every value named a row", () => {
    expect(rowRefsNote(resolveRowRefs(["T1"], rows))).toBeNull();
    expect(rowRefsNote(null)).toBeNull();
  });
});
