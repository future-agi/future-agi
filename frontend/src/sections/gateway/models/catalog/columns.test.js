import { describe, expect, it, vi } from "vitest";

import { decisionColumns } from "../auto/decisionColumns";
import { catalogColumns, compareCallable, fromV2Layout } from "./columns";
import { SORT_PRESETS } from "./queryState";

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

describe("catalog sort facts", () => {
  it("hands ordinary links to browse navigation and leaves modified clicks native", () => {
    const onOpenModel = vi.fn();
    const data = { id: "openai/example", name: "Example" };
    const cell = byId(catalogColumns({ onOpenModel }), "name").cellRenderer({
      data,
    });
    const event = {
      button: 0,
      target: { closest: () => ({}) },
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
    };
    for (const modifiers of [
      { ctrlKey: true },
      { metaKey: true },
      { shiftKey: true },
      { altKey: true },
      { button: 1 },
    ]) {
      cell.props.onClickCapture({ ...event, ...modifiers });
    }
    expect(onOpenModel).not.toHaveBeenCalled();
    expect(event.preventDefault).not.toHaveBeenCalled();
    cell.props.onClickCapture(event);
    expect(onOpenModel).toHaveBeenCalledTimes(1);
    expect(onOpenModel).toHaveBeenCalledWith({ data });
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(event.stopPropagation).toHaveBeenCalledOnce();
  });
  it("puts credits-off managed-only rows with unavailable rows, not usable rows", () => {
    const node = (access, managedOn = false) => ({
      data: { access, managedOn },
    });
    const off = node("managed");
    expect(compareCallable(null, null, off, node("none"))).toBe(0);
    for (const usable of [
      node("own_key"),
      node("both"),
      node("managed", true),
    ]) {
      expect(compareCallable(null, null, usable, off)).toBeLessThan(0);
    }
    expect(compareCallable(null, null, {}, node("none"))).toBe(0);
  });

  it.each(["pending", "error"])(
    "does not rank %s own access as usable, but retains confirmed managed access",
    (providerAccessState) => {
      const unavailable = { data: { access: "none" } };
      for (const access of ["own_key", "both"]) {
        const unknown = {
          data: { access, providerAccessState, managedOn: false },
        };
        expect(compareCallable(null, null, unknown, unavailable)).toBe(0);
      }
      const managed = {
        data: { access: "both", providerAccessState, managedOn: true },
      };
      expect(compareCallable(null, null, managed, unavailable)).toBeLessThan(0);
    },
  );

  it.each(["input", "output", "context", "released", "maxOutput"])(
    "keeps unknown %s values last in either direction",
    (field) => {
      const compare = byId(catalogColumns(), field).comparator;
      for (const descending of [false, true]) {
        for (const unknown of [null, undefined, NaN, Infinity, "0"]) {
          const result = compare(
            unknown,
            1,
            { data: {} },
            { data: {} },
            descending,
          );
          expect(descending ? -result : result).toBeGreaterThan(0);
        }
      }
    },
  );

  it.each(["input", "output"])(
    "does not rank inclusive/request-only %s prices as free",
    (field) => {
      const compare = byId(catalogColumns(), field).comparator;
      for (const data of [{ inclusivePricing: true }, { requestPrice: 0.2 }]) {
        for (const descending of [false, true]) {
          const result = compare(0, 0.5, { data }, { data: {} }, descending);
          expect(descending ? -result : result).toBeGreaterThan(0);
        }
      }
      expect(compare(0, 0.5, { data: {} }, { data: {} }, false)).toBeLessThan(
        0,
      );
    },
  );
});

describe("header room", () => {
  it("the catalog's Released column, sorted by default, keeps its label", () => {
    const released = byId(catalogColumns({ markupPercent: 5 }), "released");
    expect(released.sort).toBe("desc");
    expect(released.minWidth).toBeGreaterThanOrEqual(
      room(released, { sorted: true }),
    );
  });

  // The Sort models presets sort these columns from one select, so their
  // headers show the sort arrow at any width. Where a column's minimum leaves
  // no room for the arrow, its label can be cut, and the header must still say
  // what it is on hover.
  it("a column a Sort models preset sorts keeps its sorted label or says it on hover", () => {
    const cols = catalogColumns({ markupPercent: 5 });
    const sorted = new Set(
      SORT_PRESETS.flatMap((preset) => preset.columns.map(([id]) => id)),
    );
    expect(sorted.size).toBeGreaterThan(0);
    sorted.forEach((id) => {
      const col = byId(cols, id);
      expect(col, id).toBeTruthy();
      if (col.minWidth >= room(col, { sorted: true })) return;
      expect(typeof col.headerTooltip, id).toBe("string");
      expect(col.headerTooltip.length, id).toBeGreaterThan(0);
    });
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
