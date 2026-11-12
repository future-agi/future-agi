// v2.1 g8 (grid:cell-edit): text typed into a number cell is refused at the
// grid (the Row view's one validator), not counted as an empty edit.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

import { enqueueSnackbar } from "notistack";
import { numberCellEdit, versionedCellEdit } from "../versionedCellEdit";
import { useChangeSetStore } from "../changeSetStore";

const DS = "ds-g8-num";
const edit = (newValue, oldValue, dataType = "float") => {
  const setDataValue = vi.fn();
  versionedCellEdit({ datasetId: DS })({
    type: "cellValueChanged",
    source: "edit",
    newValue,
    oldValue,
    data: { row_id: "r6" },
    column: {
      colId: "c-score",
      colDef: { dataType, headerName: "walk_score" },
    },
    node: { setDataValue },
  });
  return setDataValue;
};
const ops = () => useChangeSetStore.getState().getOps(DS);

beforeEach(() => {
  useChangeSetStore.getState().discard?.(DS);
  enqueueSnackbar.mockClear();
});

describe("numberCellEdit", () => {
  it("refuses words, NaN and fractions in an integer cell", () => {
    expect(numberCellEdit("seven", "float", "walk_score").refused).toMatch(
      /walk_score: enter a number/,
    );
    expect(numberCellEdit(Number.NaN, "integer", "n").refused).toMatch(
      /takes a number/,
    );
    expect(numberCellEdit("2.5", "integer", "n").refused).toMatch(
      /whole number/,
    );
  });
  it("passes numbers and clears; other types untouched", () => {
    expect(numberCellEdit("7", "float")).toEqual({ value: 7 });
    expect(numberCellEdit("", "integer")).toEqual({ value: "" });
    expect(numberCellEdit("seven", "text")).toEqual({ value: "seven" });
  });
});

describe("versionedCellEdit on a number cell", () => {
  it("text is refused: no op, the cell is put back, one error toast", () => {
    const put = edit("seven", "3");
    expect(ops()).toEqual([]);
    expect(put).toHaveBeenCalledWith("c-score", "3");
    expect(enqueueSnackbar).toHaveBeenCalledWith(
      expect.stringMatching(/walk_score/),
      { variant: "error" },
    );
  });
  it("an empty-to-empty edit records nothing", () => {
    edit(null, "");
    expect(ops()).toEqual([]);
  });
  it("a number is one pending edit", () => {
    edit("7", "3");
    expect(ops()).toHaveLength(1);
    expect(ops()[0]).toMatchObject({ value: "7", before: "3" });
  });
});

describe("media save on the dataset page (g8, stage:multimodal-data)", () => {
  it("is one pending op and never calls the immediate writer", () => {
    const fallback = vi.fn();
    versionedCellEdit({ datasetId: DS, fallback, mediaPending: true })({
      type: "cellValueChanged",
      source: "edit",
      newValue: "https://cdn.acme.test/p.png",
      oldValue: null,
      data: { row_id: "r2" },
      column: { colId: "c-img", colDef: { dataType: "image" } },
    });
    expect(fallback).not.toHaveBeenCalled();
    expect(ops().filter((o) => o.row_id === "r2")).toHaveLength(1);
  });
});
