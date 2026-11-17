import { describe, expect, it } from "vitest";
import { computeRows, getAddToRowPlacement } from "../dashboardRows";

// W10-DASHBOARDS-U04: the row "+" now defers placement to Save; the layout it
// writes must match what the dashboard used to write when the "+" was clicked.
const WIDGETS = [
  { id: "w-1", position: 0, width: 6, height: 300 },
  { id: "w-2", position: 1, width: 6, height: 300 },
  { id: "w-3", position: 2, width: 12, height: 320 },
];

describe("getAddToRowPlacement", () => {
  it("places the new widget at the end of the anchor's row and shares the width", () => {
    const placement = getAddToRowPlacement(WIDGETS, "w-2");

    expect(placement.layout).toEqual({ width: 4, height: 300, position: 2 });
    expect(placement.orderWith("w-new")).toEqual([
      { id: "w-1", width: 4 },
      { id: "w-2", width: 4 },
      { id: "w-new", width: 4 },
      { id: "w-3", width: 12 },
    ]);
  });

  it("matches the anchor id whatever its type", () => {
    const numeric = WIDGETS.map((w, i) => ({ ...w, id: i + 1 }));
    expect(getAddToRowPlacement(numeric, "1")?.layout.position).toBe(2);
  });

  it("returns null when the row is full, gone or not given", () => {
    const fullRow = [0, 1, 2, 3].map((i) => ({
      id: `q-${i}`,
      position: i,
      width: 3,
    }));
    expect(getAddToRowPlacement(fullRow, "q-0")).toBeNull();
    expect(getAddToRowPlacement(WIDGETS, "deleted")).toBeNull();
    expect(getAddToRowPlacement(WIDGETS, null)).toBeNull();
    expect(getAddToRowPlacement(undefined, "w-1")).toBeNull();
  });
});

describe("computeRows", () => {
  it("groups widgets into rows by cumulative width, in position order", () => {
    const rows = computeRows([WIDGETS[2], WIDGETS[0], WIDGETS[1]]);
    expect(rows.map((row) => row.map((w) => w.id))).toEqual([
      ["w-1", "w-2"],
      ["w-3"],
    ]);
  });
});
