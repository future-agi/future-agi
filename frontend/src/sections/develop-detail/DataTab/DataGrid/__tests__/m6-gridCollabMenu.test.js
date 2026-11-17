// M6 (W4 §9, the grid integration): the cell menu offers Comment on row (the
// row's comments with the caret in the composer) and Assign row to… (which
// needs edit; refused with the reason otherwise).
import { describe, expect, it, vi } from "vitest";
import { buildCellContextMenu } from "../gridContextMenu";
import { commentDeepLink } from "src/sections/common/Collab/commentModel";

const params = () => ({
  column: {
    getColDef: () => ({ col: {}, headerName: "customer_message" }),
    getColId: () => "c1",
  },
  node: { data: { row_id: "row-7", row_meta: { split_ids: [] } } },
  value: "Where is my order?",
});
const base = () => ({
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
const find = (items, name) =>
  items.find((i) => typeof i === "object" && i.name === name);

describe("grid cell menu: comment and assign", () => {
  it("both items act on the row", () => {
    const handlers = {
      ...base(),
      commentOnRow: vi.fn(),
      assignRow: vi.fn(),
    };
    const items = buildCellContextMenu(params(), {
      handlers,
      collab: { canAssign: true },
    });
    find(items, "Comment on row").action();
    expect(handlers.commentOnRow).toHaveBeenCalledWith("row-7");
    const assign = find(items, "Assign row to…");
    expect(assign.disabled).toBe(false);
    assign.action();
    expect(handlers.assignRow).toHaveBeenCalledWith("row-7");
  });

  it("Assign is refused with the server's reason without edit", () => {
    const why = "Everyone in Support Agents has View access to this dataset.";
    const items = buildCellContextMenu(params(), {
      handlers: { ...base(), commentOnRow: vi.fn(), assignRow: vi.fn() },
      collab: { canAssign: false, assignReason: why },
    });
    expect(find(items, "Assign row to…")).toMatchObject({
      disabled: true,
      tooltip: why,
    });
    expect(find(items, "Comment on row").disabled).toBeUndefined();
  });

  it("without the handlers the menu is as before", () => {
    const items = buildCellContextMenu(params(), { handlers: base() });
    expect(find(items, "Comment on row")).toBeUndefined();
    expect(find(items, "Assign row to…")).toBeUndefined();
  });

  it("commentDeepLink opens the row's comments with the composer", () => {
    const next = commentDeepLink(new URLSearchParams("tab=data"), "row-7", 42);
    expect(Object.fromEntries(next)).toEqual({
      tab: "data",
      row: "row-7",
      rowTab: "activity",
      ract: "comments",
      rcs: "open",
      rcompose: "42",
    });
  });
});
