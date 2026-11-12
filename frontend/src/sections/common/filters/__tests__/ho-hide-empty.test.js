// HO results R2: the Hide rows with no output switch is the expression pill.
import { describe, expect, it, vi } from "vitest";
import {
  HIDE_EMPTY_EXPR,
  HIDE_EMPTY_LABEL,
  hideEmptyHint,
  hideEmptyViewExtra,
  hidesEmpty,
  isHideEmptyPill,
  withHideEmpty,
} from "../hideEmpty";
import { fitsRow, rowSlack } from "../rowRoom";
import { resultsQuickPicks } from "../resultsDoorModel";

describe("hide empty", () => {
  const other = { type: "split", values: ["smoke"] };
  it("is the dialog's quick pick, added once and removed alone", () => {
    const pick = resultsQuickPicks({ runs: [{ run_id: "a" }] }).find(
      (q) => q.label === "Hide rows with no output",
    );
    expect(pick.expr).toBe(HIDE_EMPTY_EXPR);
    const on = withHideEmpty([other], true);
    expect(on).toEqual([other, { type: "expression", expr: HIDE_EMPTY_EXPR }]);
    expect(withHideEmpty(on, true)).toEqual(on);
    expect(hidesEmpty(on)).toBe(true);
    expect(withHideEmpty(on, false)).toEqual([other]);
    expect(
      isHideEmptyPill({
        type: "expression",
        expr: " not  (all.output is empty) ",
      }),
    ).toBe(true);
    expect(
      isHideEmptyPill({
        type: "expression",
        expr: "not (any.output is empty)",
      }),
    ).toBe(false);
  });
  it("says how many rows it leaves out", () => {
    expect(hideEmptyHint(null)).toBeNull();
    expect(hideEmptyHint({ without: 30, with: 30 })).toBe(
      "Every row in view has an output",
    );
    expect(hideEmptyHint({ without: 30, with: 29 })).toBe(
      "Hides 1 row where no run has an output",
    );
    expect(hideEmptyHint({ without: 1250, with: 20 })).toBe(
      "Hides 1,230 rows where no run has an output",
    );
  });
  // HO R2 review: the toolbar ROW is measured, never the viewport; the
  // switch shows with its words whenever that row has room for it, on the
  // single-run and the compare toolbar alike.
  it("measures the row's free room beside its flexible parts", () => {
    // Search · Filter · Sort, then the action bar (flex: 1) whose buttons
    // take 300 of its 900 px: 600 px free.
    expect(
      rowSlack({
        width: 1400,
        gap: 8,
        kids: [{ width: 492 }, { width: 900, grow: true, content: 300 }],
      }),
    ).toBe(600);
    // An action bar capped narrower than the row leaves the rest outside.
    expect(
      rowSlack({
        width: 1200,
        gap: 8,
        kids: [{ width: 400 }, { width: 310, grow: true, content: 300 }],
      }),
    ).toBe(1200 - 400 - 310 - 8 + 10);
    // A full row: nothing free.
    expect(
      rowSlack({
        width: 1200,
        gap: 8,
        kids: [{ width: 892 }, { width: 300, grow: true, content: 300 }],
      }),
    ).toBe(0);
    expect(
      rowSlack({ width: 500, gap: 10, kids: [{ width: 200 }, { width: 100 }] }),
    ).toBe(190);
    // Shown, the switch squeezed the action bar below its pinned buttons
    // (they spill 60 px under it): the row is short, so it must go.
    const squeezed = rowSlack({
      width: 1216,
      gap: 8,
      kids: [{ width: 1000 }, { width: 200, grow: true, content: 260 }],
    });
    expect(squeezed).toBe(1216 - 1200 - 8 - 60);
    expect(fitsRow({ slack: squeezed, shown: 190, need: 190 })).toBe(false);
  });
  it("fits when the room holds the whole labelled switch, and stays once shown", () => {
    expect(fitsRow({ slack: 600, shown: 0, need: 190 })).toBe(true);
    expect(fitsRow({ slack: 120, shown: 0, need: 190 })).toBe(false);
    // Shown, the row's slack is what is left beside it: still fits.
    expect(fitsRow({ slack: 410, shown: 190, need: 190 })).toBe(true);
    expect(fitsRow({ slack: 0, shown: 190, need: 190 })).toBe(true);
    expect(fitsRow({ slack: Number.NaN, shown: 0, need: 190 })).toBe(false);
  });
  it("the View menu line is the same pill, on and off", () => {
    const setPills = vi.fn();
    const line = hideEmptyViewExtra([other], setPills);
    expect(line.label).toBe(HIDE_EMPTY_LABEL);
    expect(line.checked).toBe(false);
    line.onChange(true);
    expect(setPills).toHaveBeenLastCalledWith(withHideEmpty([other], true));
    const onLine = hideEmptyViewExtra(withHideEmpty([other], true), setPills);
    expect(onLine.checked).toBe(true);
    onLine.onChange(false);
    expect(setPills).toHaveBeenLastCalledWith([other]);
  });
});
