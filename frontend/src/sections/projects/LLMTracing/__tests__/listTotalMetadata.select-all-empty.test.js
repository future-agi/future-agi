import { describe, expect, it } from "vitest";

import { getSelectionCountState } from "../listTotalMetadata";

// OBS-SW-116: select-all on an exact empty list, or with every row unticked,
// selects nothing. Only a total that is not known yet keeps the one-row floor.

const exactSelectAll = (totalRowCount, toggledNodes = []) =>
  getSelectionCountState({
    selectAll: true,
    toggledNodes,
    totalRowCount,
    totalRowCountLowerBound: null,
    totalRowCountIsLowerBound: false,
  });

describe("select-all count against an exact total", () => {
  it("selects nothing when select-all runs on an exact empty list", () => {
    expect(exactSelectAll(0)).toEqual({ count: 0, isLowerBound: false });
  });

  it("selects nothing after the only row of an exact list is unticked", () => {
    expect(exactSelectAll(1, ["trace-a"])).toEqual({
      count: 0,
      isLowerBound: false,
    });
  });

  it("does not go below zero when exclusions outnumber the exact total", () => {
    expect(exactSelectAll(2, ["trace-a", "trace-b", "trace-c"])).toEqual({
      count: 0,
      isLowerBound: false,
    });
  });

  it("counts the rows left after exclusions", () => {
    expect(exactSelectAll(7, ["trace-a"])).toEqual({
      count: 6,
      isLowerBound: false,
    });
  });

  it.each([null, undefined])(
    "keeps one selected while the total is unknown (%s)",
    (totalRowCount) => {
      expect(exactSelectAll(totalRowCount)).toEqual({
        count: 1,
        isLowerBound: false,
      });
    },
  );

  it("keeps a fully excluded lower bound at zero", () => {
    expect(
      getSelectionCountState({
        selectAll: true,
        toggledNodes: ["trace-a", "trace-b"],
        totalRowCount: null,
        totalRowCountLowerBound: 2,
        totalRowCountIsLowerBound: true,
      }),
    ).toEqual({ count: 0, isLowerBound: true });
  });
});
