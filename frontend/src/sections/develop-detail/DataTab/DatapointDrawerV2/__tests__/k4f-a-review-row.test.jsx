// integration review fixes (Row view side):
//   1. after a restore the open screen re-reads: the selection clears, the
//      as-of grid re-reads (purged), and the Row view's banner / Restore come
//      from the fresh get-row-data, not the grid node's stale change;
//   4. a row this version deleted that is back in the latest version says
//      `Deleted in v5 · restored in v7.` and offers no Restore;
//   7. a column is recorded with the pending changes only while changes are
//      pending, and Discard ticks `Also delete …` only for pasted columns.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { asOfRowOf } from "../useRowNavigation";
import { deletedRowText } from "../RowViewStates";
import { afterRestore } from "../../Versions/RestoreRowsDialog";
import { restorableRows } from "../../Versions/versionText";
import {
  dropCreatedColumnsByDefault,
  useChangeSetStore,
  useCreatedColumnsStore,
} from "../../Versions/changeSetStore";
import { useDevelopSelectedRowsStore } from "../../../states";

const stale = {
  row_id: "r31",
  order: 30,
  values: { c1: "as of v4" },
  row_meta: {
    split_ids: ["s1"],
    change: { kind: "deleted_later", version: 5 },
  },
};
const fresh = {
  row_id: "r31",
  order: 30,
  values: { c1: "as of v4" },
  row_meta: { split_ids: ["s1"], change: null, counts: { runs: 2 } },
};

describe("must-fix 1 · the open Row view reads the fresh row after a restore", () => {
  it("the fresh as-of read wins over the grid node's stale change", () => {
    const row = asOfRowOf({
      asOf: true,
      gridRow: stale,
      current: fresh,
      fresh: true,
    });
    expect(row.row_meta.change).toBeNull();
    expect(restorableRows([row], 4)).toBeNull();
  });

  it("before the read answers, the grid's copy shows (counts row-wide)", () => {
    const row = asOfRowOf({
      asOf: true,
      gridRow: stale,
      current: fresh,
      fresh: false,
    });
    expect(row.row_meta.change).toEqual(stale.row_meta.change);
    expect(row.row_meta.counts).toEqual({ runs: 2 });
    expect(restorableRows([row], 4)).toEqual({
      rows: [{ row_id: "r31", n: 31 }],
      from: 4,
    });
  });

  it("latest (not as of) is the read itself", () => {
    expect(
      asOfRowOf({ asOf: false, gridRow: stale, current: fresh, fresh: false }),
    ).toBe(fresh);
  });

  it("a restore clears the selection, purges the grid and re-reads the row", () => {
    useDevelopSelectedRowsStore.setState({
      toggledNodes: ["r31", "r77"],
      selectAll: false,
    });
    const api = {
      deselectAll: vi.fn(),
      refreshServerSide: vi.fn(),
    };
    const queryClient = { invalidateQueries: vi.fn() };
    afterRestore({ queryClient, gridApi: { current: api }, datasetId: "GS" });
    expect(useDevelopSelectedRowsStore.getState().toggledNodes).toEqual([]);
    expect(useDevelopSelectedRowsStore.getState().selectAll).toBe(false);
    expect(api.deselectAll).toHaveBeenCalled();
    expect(api.refreshServerSide).toHaveBeenCalledWith({ purge: true });
    const keys = queryClient.invalidateQueries.mock.calls.map(
      ([q]) => q.queryKey,
    );
    expect(keys).toContainEqual(["develop", "row-view", "GS"]);
    expect(keys).toContainEqual(["dataset-detail", "GS"]);
  });
});

describe("must-fix 4 · a deleted row that is back in the latest version", () => {
  it("says so, and is not restorable", () => {
    expect(deletedRowText({ version: 5, latest: 7 })).toBe(
      "Deleted in v5 · not in the latest version (v7).",
    );
    expect(
      deletedRowText({ version: 5, latest: 7, inLatest: true, restoredIn: 7 }),
    ).toBe("Deleted in v5 · restored in v7.");
    expect(
      restorableRows(
        [
          {
            row_id: "r31",
            order: 30,
            row_meta: {
              change: {
                kind: "deleted",
                version: 5,
                in_latest: true,
                restored_in: 7,
              },
            },
          },
        ],
        5,
      ),
    ).toBeNull();
  });
});

describe("must-fix 7 · columns recorded with pending changes", () => {
  const DS = "ds-k4f-review";
  beforeEach(() => {
    useChangeSetStore.setState({ sets: {} });
    useCreatedColumnsStore.setState({ cols: {} });
  });

  it("Add column with nothing pending is not recorded (Discard never offers it)", () => {
    useCreatedColumnsStore
      .getState()
      .add(DS, { id: "c-notes", name: "notes", from: "add_column" });
    expect(useCreatedColumnsStore.getState().of(DS)).toEqual([]);
  });

  it("while changes are pending it is recorded; emptying the changes drops it", () => {
    useChangeSetStore.getState().addOp(DS, {
      op: "add_row",
      temp_id: "new-1",
      cells: {},
      split_ids: [],
    });
    useCreatedColumnsStore
      .getState()
      .add(DS, { id: "c-urgency", name: "urgency", from: "paste" });
    expect(useCreatedColumnsStore.getState().of(DS)).toEqual([
      { id: "c-urgency", name: "urgency", from: "paste" },
    ]);
    useChangeSetStore.setState({ sets: {} });
    expect(useCreatedColumnsStore.getState().of(DS)).toEqual([]);
  });

  it("Discard's Also delete starts ticked only for pasted columns", () => {
    expect(dropCreatedColumnsByDefault([])).toBe(false);
    expect(
      dropCreatedColumnsByDefault([{ id: "a", name: "a", from: "paste" }]),
    ).toBe(true);
    expect(
      dropCreatedColumnsByDefault([
        { id: "a", name: "a", from: "paste" },
        { id: "b", name: "b", from: "add_column" },
      ]),
    ).toBe(false);
  });
});
