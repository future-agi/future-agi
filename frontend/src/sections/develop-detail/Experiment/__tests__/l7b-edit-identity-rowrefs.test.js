// options-truth L7b G2 (data:rows:refs-note): `rows=` is read against every
// row of the pinned version (complete), so a value that names no row is
// missing and never posted, and rows past the first grid page are found.
import { describe, expect, it } from "vitest";
import { resolveRowRefs, rowRefsNote } from "../rowRefs";

// 60 rows of a version (the grid's first page holds 50).
const rows = Array.from({ length: 60 }, (_, i) => ({
  row_id: `row-${i + 1}`,
  order: i,
}));

describe("rows= deep link against the whole version", () => {
  it("rows=12,57,zzz → ids of rows 12 and 57; zzz missing", () => {
    const res = resolveRowRefs(["12", "57", "zzz"], rows, { complete: true });
    expect(res.ids).toEqual(["row-12", "row-57"]);
    expect(res.missing).toEqual(["zzz"]);
    expect(rowRefsNote(res)).toBe(
      "2 of 3 rows found · not in this dataset: zzz",
    );
  });

  it("rows=Row 5,T9,abc → the note names abc", () => {
    const res = resolveRowRefs(["Row 5", "T9", "abc"], rows, {
      complete: true,
    });
    expect(res.ids).toEqual(["row-5", "row-9"]);
    expect(rowRefsNote(res)).toBe(
      "2 of 3 rows found · not in this dataset: abc",
    );
  });

  it("only the first page would have lost row 57", () => {
    const page = rows.slice(0, 50);
    const res = resolveRowRefs(["12", "57"], page, { complete: true });
    expect(res.ids).toEqual(["row-12"]);
    const all = resolveRowRefs(["12", "57"], rows, { complete: true });
    expect(all.ids).toHaveLength(2);
  });
});
