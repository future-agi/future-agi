import { describe, expect, it, vi } from "vitest";
import { buildCellContextMenu } from "../gridContextMenu";

const handlers = () => ({
  filterTo: vi.fn(),
  exclude: vi.fn(),
  showAllIn: vi.fn(),
  openRow: vi.fn(),
  copyValue: vi.fn(),
  copyRowLink: vi.fn(),
  addToQueue: vi.fn(),
  toggleSplit: vi.fn(),
  manageSplits: vi.fn(),
});

const params = ({ colDef, value, data }) => ({
  column: { getColDef: () => colDef, getColId: () => colDef.field },
  node: { data },
  value,
});

const splits = [
  { id: "S1", name: "smoke" },
  { id: "S3", name: "hard" },
];
const row = { row_id: "r12", row_meta: { split_ids: ["S1"] } };
const names = (items) => items.map((i) => (typeof i === "string" ? i : i.name));

describe("buildCellContextMenu (G7)", () => {
  it("text cell: filter, exclude, then the row items and splits", () => {
    const h = handlers();
    const items = buildCellContextMenu(
      params({
        colDef: {
          field: "c1",
          col: {},
          dataType: "text",
          originType: "OTHERS",
          headerName: "intent",
        },
        value: "refund_request",
        data: row,
      }),
      { splits, ops: [], handlers: h },
    );
    expect(names(items)).toEqual([
      "Filter to “refund_request”",
      "Exclude “refund_request”",
      "separator",
      "Open row",
      "Copy value",
      "Copy row link",
      "Add row to annotation queue…",
      "Assign split",
    ]);
    items[0].action();
    expect(h.filterTo).toHaveBeenCalledWith(
      "c1",
      "refund_request",
      expect.any(Object),
    );
    items.find((i) => i.name === "Open row").action();
    expect(h.openRow).toHaveBeenCalledWith("r12");
  });

  it("JSON cells offer no Exclude", () => {
    const items = buildCellContextMenu(
      params({
        colDef: {
          field: "c2",
          col: {},
          dataType: "json",
          originType: "OTHERS",
        },
        value: [{ name: "lookup_order" }],
        data: row,
      }),
      { splits, ops: [], handlers: handlers() },
    );
    expect(names(items).some((n) => n.startsWith("Exclude"))).toBe(false);
  });

  it("pass/fail eval cells read `Show all Failed in Correctness`", () => {
    const h = handlers();
    const items = buildCellContextMenu(
      params({
        colDef: {
          field: "e1",
          col: {},
          dataType: "boolean",
          originType: "evaluation",
          headerName: "Correctness",
        },
        value: "Failed",
        data: row,
      }),
      { splits, ops: [], handlers: h },
    );
    expect(items[0].name).toBe("Show all Failed in Correctness");
    items[0].action();
    expect(h.showAllIn).toHaveBeenCalledWith("e1", "Failed");
  });

  it("the split submenu reflects pending set_splits ops", () => {
    const h = handlers();
    const items = buildCellContextMenu(
      params({
        colDef: {
          field: "c1",
          col: {},
          dataType: "text",
          originType: "OTHERS",
        },
        value: "x",
        data: row,
      }),
      {
        splits,
        ops: [
          { op: "set_splits", row_ids: ["r12"], add: ["S3"], remove: ["S1"] },
        ],
        handlers: h,
      },
    );
    const sub = items.find((i) => i.name === "Assign split").subMenu;
    expect(sub.slice(0, 2).map((i) => [i.name, i.checked])).toEqual([
      ["smoke", false],
      ["hard", true],
    ]);
    sub[1].action();
    expect(h.toggleSplit).toHaveBeenCalledWith("r12", "S3", false);
    expect(sub.at(-1).name).toBe("Manage splits…");
  });

  it("pending new rows and empty rows get no menu", () => {
    expect(
      buildCellContextMenu(
        params({
          colDef: { field: "c1", col: {} },
          value: "x",
          data: { row_id: "new-1", __pending: "new" },
        }),
        { splits, ops: [], handlers: handlers() },
      ),
    ).toEqual([]);
    expect(
      buildCellContextMenu(
        params({ colDef: { field: "c1" }, value: "x", data: null }),
        {
          handlers: handlers(),
        },
      ),
    ).toEqual([]);
  });
});
