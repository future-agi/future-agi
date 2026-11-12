import { describe, expect, it } from "vitest";
import {
  canMoveColumnBy,
  compactColumnPrefs,
  fromAgColumnState,
  fromTanstackState,
  hideColumn,
  isDefaultLayout,
  moveColumn,
  moveColumnBy,
  pinColumn,
  resetColumns,
  resolveColumns,
  setColumnWidth,
  showAllColumns,
  showColumn,
  toAgColumnState,
  toTanstackState,
  toggleColumn,
} from "../columnLayout";

const COLS = [
  { id: "name", label: "Name", locked: true, defaultPinned: "left" },
  { id: "rows", label: "Rows" },
  { id: "kind", label: "Kind", groupable: true },
  { id: "updated", label: "Updated" },
  { id: "owner", label: "Owner", defaultVisible: false },
  { id: "tags", label: "Tags", defaultVisible: false, pinnable: false },
];

const ids = (resolved) => resolved.map((c) => c.id);
const visibleIds = (resolved) =>
  resolved.filter((c) => !c.hidden).map((c) => c.id);

describe("resolveColumns", () => {
  it("returns the catalog order and defaults with empty prefs", () => {
    const r = resolveColumns(COLS, {});
    expect(ids(r)).toEqual([
      "name",
      "rows",
      "kind",
      "updated",
      "owner",
      "tags",
    ]);
    expect(visibleIds(r)).toEqual(["name", "rows", "kind", "updated"]);
    expect(r[0].pinned).toBe("left");
    expect(r[1].pinned).toBe(null);
    expect(r.every((c) => c.width === null)).toBe(true);
    expect(r.map((c) => c.index)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("drops unknown ids from stored prefs", () => {
    const r = resolveColumns(COLS, {
      visible: { gone: true, owner: true },
      order: ["gone", "updated", "rows", "kind", "owner", "tags"],
      pinned: { gone: "left" },
      widths: { gone: 99 },
    });
    expect(ids(r)).toEqual([
      "name",
      "updated",
      "rows",
      "kind",
      "owner",
      "tags",
    ]);
    expect(visibleIds(r)).toContain("owner");
  });

  it("puts a NEW catalog column at its catalog position with its default", () => {
    const stored = { order: ["updated", "rows", "kind", "owner", "tags"] };
    const withNew = [
      ...COLS.slice(0, 2),
      { id: "fresh", label: "Fresh" },
      ...COLS.slice(2),
    ];
    const r = resolveColumns(withNew, stored);
    // After its catalog predecessor `rows`, wherever rows now sits.
    expect(ids(r)).toEqual([
      "name",
      "updated",
      "rows",
      "fresh",
      "kind",
      "owner",
      "tags",
    ]);
    expect(r.find((c) => c.id === "fresh").hidden).toBe(false);
  });

  it("a new hidden-by-default column stays hidden", () => {
    const r = resolveColumns(
      [...COLS, { id: "cost", label: "Cost", defaultVisible: false }],
      { visible: { owner: true } },
    );
    expect(r.find((c) => c.id === "cost").hidden).toBe(true);
  });

  it("survives garbage prefs", () => {
    expect(ids(resolveColumns(COLS, null))).toHaveLength(6);
    expect(ids(resolveColumns(COLS, "x"))).toHaveLength(6);
    expect(
      ids(
        resolveColumns(COLS, { order: "x", visible: [], widths: { rows: -3 } }),
      ),
    ).toHaveLength(6);
    expect(resolveColumns([], {})).toEqual([]);
  });

  it("pinned columns come first, in their relative order", () => {
    const p = pinColumn({}, COLS, "updated", "left");
    expect(ids(resolveColumns(COLS, p)).slice(0, 2)).toEqual([
      "name",
      "updated",
    ]);
  });
});

describe("hide / show", () => {
  it("hides and shows; locked columns never hide", () => {
    let p = hideColumn({}, COLS, "kind");
    expect(visibleIds(resolveColumns(COLS, p))).toEqual([
      "name",
      "rows",
      "updated",
    ]);
    p = showColumn(p, COLS, "kind");
    expect(p).toEqual({});
    expect(hideColumn({}, COLS, "name")).toEqual({});
  });

  it("stores only differences from the default", () => {
    expect(showColumn({}, COLS, "rows")).toEqual({});
    expect(showColumn({}, COLS, "owner")).toEqual({ visible: { owner: true } });
    expect(hideColumn({ visible: { owner: true } }, COLS, "owner")).toEqual({});
  });

  it("toggle flips, showAll shows every column", () => {
    const p = toggleColumn({}, COLS, "owner");
    expect(resolveColumns(COLS, p).find((c) => c.id === "owner").hidden).toBe(
      false,
    );
    expect(toggleColumn(p, COLS, "owner")).toEqual({});
    const all = showAllColumns(hideColumn({}, COLS, "rows"), COLS);
    expect(resolveColumns(COLS, all).every((c) => !c.hidden)).toBe(true);
  });

  it("ignores unknown ids", () => {
    expect(hideColumn({}, COLS, "nope")).toEqual({});
    expect(showColumn({}, COLS, "nope")).toEqual({});
  });
});

describe("move", () => {
  it("moves to an index of the resolved order", () => {
    const p = moveColumn({}, COLS, "updated", 1);
    expect(ids(resolveColumns(COLS, p))).toEqual([
      "name",
      "updated",
      "rows",
      "kind",
      "owner",
      "tags",
    ]);
  });

  it("never moves a locked column and keeps it in place", () => {
    expect(moveColumn({}, COLS, "name", 3)).toEqual({});
    const p = moveColumn({}, COLS, "rows", 0);
    expect(ids(resolveColumns(COLS, p))[0]).toBe("name");
  });

  it("moveBy jumps over hidden columns and stops at locked / edges", () => {
    // rows ↔ kind ↔ updated visible; owner, tags hidden
    let p = moveColumnBy({}, COLS, "updated", -1);
    expect(visibleIds(resolveColumns(COLS, p))).toEqual([
      "name",
      "rows",
      "updated",
      "kind",
    ]);
    p = moveColumnBy(p, COLS, "updated", -1);
    expect(visibleIds(resolveColumns(COLS, p))).toEqual([
      "name",
      "updated",
      "rows",
      "kind",
    ]);
    expect(canMoveColumnBy(COLS, p, "updated", -1)).toEqual({
      ok: false,
      reason: "Already first",
    });
    expect(canMoveColumnBy(COLS, {}, "updated", 1)).toEqual({
      ok: false,
      reason: "Already last",
    });
    expect(canMoveColumnBy(COLS, {}, "name", 1).ok).toBe(false);
    expect(canMoveColumnBy(COLS, {}, "name", 1).reason).toBe(
      "Name keeps its place",
    );
  });

  it("moving back to the catalog order is the default again", () => {
    const p = moveColumnBy(moveColumnBy({}, COLS, "kind", -1), COLS, "kind", 1);
    expect(p).toEqual({});
    expect(isDefaultLayout(COLS, p)).toBe(true);
  });
});

describe("pin / width / reset / isDefault", () => {
  it("freezes and unfreezes; unpinnable columns ignore it", () => {
    const p = pinColumn({}, COLS, "rows", "left");
    expect(resolveColumns(COLS, p).find((c) => c.id === "rows").pinned).toBe(
      "left",
    );
    expect(pinColumn(p, COLS, "rows", null)).toEqual({});
    expect(pinColumn({}, COLS, "tags", "left")).toEqual({});
  });

  it("unfreezing a default-pinned column is stored as an override", () => {
    const p = pinColumn({}, COLS, "name", null);
    expect(p).toEqual({ pinned: { name: null } });
    expect(resolveColumns(COLS, p)[0].pinned).toBe(null);
  });

  it("stores user widths and clears them", () => {
    const p = setColumnWidth({}, COLS, "rows", 120.4);
    expect(resolveColumns(COLS, p).find((c) => c.id === "rows").width).toBe(
      120,
    );
    expect(setColumnWidth(p, COLS, "rows", null)).toEqual({});
  });

  it("isDefault and reset", () => {
    expect(isDefaultLayout(COLS, {})).toBe(true);
    expect(isDefaultLayout(COLS, hideColumn({}, COLS, "rows"))).toBe(false);
    expect(isDefaultLayout(COLS, { visible: { rows: true } })).toBe(true);
    expect(resetColumns()).toEqual({});
  });

  it("compacts a stored order equal to the catalog", () => {
    expect(
      compactColumnPrefs(
        { order: ["rows", "kind", "updated", "owner", "tags"] },
        COLS,
      ),
    ).toEqual({});
  });
});

describe("AG Grid adapter", () => {
  it("toAgColumnState lists every column with hide / pinned, width only when set", () => {
    const p = setColumnWidth(hideColumn({}, COLS, "kind"), COLS, "rows", 140);
    const state = toAgColumnState(resolveColumns(COLS, p));
    expect(state.map((s) => s.colId)).toEqual([
      "name",
      "rows",
      "kind",
      "updated",
      "owner",
      "tags",
    ]);
    expect(state[0]).toEqual({ colId: "name", hide: false, pinned: "left" });
    expect(state[1]).toEqual({
      colId: "rows",
      hide: false,
      pinned: null,
      width: 140,
    });
    expect(state[2].hide).toBe(true);
  });

  it("round-trips through AG state (selection / auto-group columns ignored)", () => {
    let p = moveColumn({}, COLS, "updated", 1);
    p = hideColumn(p, COLS, "rows");
    p = showColumn(p, COLS, "owner");
    p = pinColumn(p, COLS, "kind", "left");
    const state = [
      { colId: "ag-Grid-SelectionColumn", hide: false, pinned: "left" },
      ...toAgColumnState(resolveColumns(COLS, p)),
      { colId: "ag-Grid-AutoColumn", hide: false },
    ];
    expect(fromAgColumnState(state, COLS)).toEqual(p);
  });

  it("reads widths from AG state only when asked (after a resize)", () => {
    const state = toAgColumnState(resolveColumns(COLS, {})).map((s) => ({
      ...s,
      width: 200,
    }));
    expect(fromAgColumnState(state, COLS)).toEqual({});
    expect(
      fromAgColumnState(state, COLS, { includeWidths: true }).widths.rows,
    ).toBe(200);
  });

  it("maps a right pin from AG to unpinned", () => {
    const state = toAgColumnState(resolveColumns(COLS, {}));
    state[1] = { ...state[1], pinned: "right" };
    expect(fromAgColumnState(state, COLS)).toEqual({});
  });
});

describe("TanStack adapter", () => {
  it("builds visibility, order, pinning and sizing", () => {
    const p = setColumnWidth(hideColumn({}, COLS, "kind"), COLS, "rows", 90);
    const t = toTanstackState(resolveColumns(COLS, p));
    expect(t.columnVisibility).toEqual({
      name: true,
      rows: true,
      kind: false,
      updated: true,
      owner: false,
      tags: false,
    });
    expect(t.columnOrder[0]).toBe("name");
    expect(t.columnPinning).toEqual({ left: ["name"], right: [] });
    expect(t.columnSizing).toEqual({ rows: 90 });
  });

  it("round-trips through TanStack state", () => {
    let p = moveColumn({}, COLS, "kind", 1);
    p = showColumn(p, COLS, "tags");
    p = pinColumn(p, COLS, "rows", "left");
    p = setColumnWidth(p, COLS, "updated", 180);
    expect(
      fromTanstackState(toTanstackState(resolveColumns(COLS, p)), COLS),
    ).toEqual(p);
  });
});
