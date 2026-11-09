import { beforeEach, describe, expect, it, vi } from "vitest";
import { useChangeSetStore } from "../changeSetStore";
import { versionedCellEdit } from "../versionedCellEdit";

const DS = "ds-edit";
const event = (newValue, oldValue, extra = {}) => ({
  type: "cellValueChanged",
  source: "edit",
  newValue,
  oldValue,
  data: { row_id: "ROW_241" },
  column: { colId: "C_03", colDef: { dataType: "text" } },
  ...extra,
});

describe("versionedCellEdit", () => {
  beforeEach(() => useChangeSetStore.getState().discard(DS));

  it("turns a text edit into a pending update_cell op", () => {
    const fallback = vi.fn();
    versionedCellEdit({ datasetId: DS, fallback })(event("Fixed", ""));
    const ops = useChangeSetStore.getState().getOps(DS);
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({
      op: "update_cell",
      row_id: "ROW_241",
      column_id: "C_03",
      value: "Fixed",
      before: "",
    });
    expect(fallback).not.toHaveBeenCalled();
  });

  it("stringifies JSON values and ignores grid-internal changes", () => {
    const edit = versionedCellEdit({ datasetId: DS, fallback: vi.fn() });
    edit(event([{ name: "lookup_order" }], "[]"));
    edit({ ...event("x", "y"), source: undefined });
    const ops = useChangeSetStore.getState().getOps(DS);
    expect(ops).toHaveLength(1);
    expect(ops[0].value).toBe('[{"name":"lookup_order"}]');
  });

  it("leaves media edits on the existing writer", () => {
    const fallback = vi.fn();
    versionedCellEdit({ datasetId: DS, fallback })(
      event("data:image/png;base64,AAA", null, {
        column: { colId: "C_09", colDef: { dataType: "image" } },
      }),
    );
    expect(fallback).toHaveBeenCalledTimes(1);
    expect(useChangeSetStore.getState().getOps(DS)).toHaveLength(0);
  });
});
