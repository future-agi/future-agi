import { describe, expect, it } from "vitest";

import { decisionColumns } from "../auto/decisionColumns";
import { catalogColumns, fromV2Layout } from "./columns";

// A header shows its label and, when sorted, the sort arrow; the column menu
// button only where a column turns it back on (GatewayGrid hides it, as
// Observe does). The FA table header font needs about 9 px a character, plus
// 32 px of cell padding, 18 px for the menu and 22 px for the arrow. A column
// at its minimum width (the 390 px layout, or a narrow window) must not cut
// its label to "Relea…" or "Prefere…" (screenshot pass, 1440 px).
const room = (col, { sorted = false } = {}) =>
  col.headerName.length * 9 +
  32 +
  (col.suppressHeaderMenuButton === false ? 18 : 0) +
  (sorted ? 22 : 0);

const byId = (cols, id) => cols.find((c) => c.colId === id);

describe("header room", () => {
  it("the catalog's Released column, sorted by default, keeps its label", () => {
    const released = byId(catalogColumns({ markupPercent: 5 }), "released");
    expect(released.sort).toBe("desc");
    expect(released.minWidth).toBeGreaterThanOrEqual(
      room(released, { sorted: true }),
    );
  });

  it("auto routing's decision columns keep their labels", () => {
    ["at", "preference", "considered", "latencyMs", "tier"].forEach((id) => {
      const col = byId(decisionColumns, id);
      expect(col.minWidth, id).toBeGreaterThanOrEqual(
        room(col, { sorted: Boolean(col.sort) }),
      );
    });
  });
});

// The saved layout key moved to v3; a layout saved under v2 is carried
// over, not discarded.
describe("fromV2Layout", () => {
  const V2 = [
    // A column the user resized: AG Grid saves its width with no flex.
    { colId: "name", width: 312, flex: null, hide: false, pinned: "left" },
    // Flex columns: their v2 widths were the wider grid's own layout.
    { colId: "vendorName", width: 180, flex: 1, hide: false },
    { colId: "typeLabel", width: 150, flex: 0.8, hide: false },
    { colId: "released", width: 140, flex: 0.9, sort: "desc", sortIndex: 0 },
    { colId: "maxOutput", width: 120, flex: 0.8, hide: false },
    { colId: "accessLabel", width: 160, flex: 1.1, sort: null },
  ];

  it("keeps order, sort, pins, the user's own widths and shown columns", () => {
    const v3 = fromV2Layout(V2);
    expect(v3.map((c) => c.colId)).toEqual(V2.map((c) => c.colId));
    expect(v3[0]).toEqual({
      colId: "name",
      width: 312,
      flex: null,
      hide: false,
      pinned: "left",
    });
    const released = v3.find((c) => c.colId === "released");
    expect(released).toMatchObject({ sort: "desc", sortIndex: 0 });
    // A column the user showed (hidden by default) stays shown.
    expect(v3.find((c) => c.colId === "maxOutput").hide).toBe(false);
  });

  it("lets flex columns take v3's widths, and Type its v3 default (a facet now)", () => {
    const v3 = fromV2Layout(V2);
    const vendor = v3.find((c) => c.colId === "vendorName");
    expect(vendor).not.toHaveProperty("width");
    expect(vendor).not.toHaveProperty("flex");
    const type = v3.find((c) => c.colId === "typeLabel");
    expect(type).not.toHaveProperty("hide");
    expect(type).not.toHaveProperty("width");
  });

  it("returns nothing for a layout that is not a list", () => {
    expect(fromV2Layout(null)).toEqual([]);
  });
});
