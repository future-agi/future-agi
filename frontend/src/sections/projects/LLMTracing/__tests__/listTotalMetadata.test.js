import { describe, expect, it } from "vitest";

import {
  formatSelectionCount,
  getListReadMessage,
  getListTotalState,
  getSelectionCountState,
} from "../listTotalMetadata";
import { QUERY_READ_RETRY_MESSAGE } from "src/utils/queryReadState";

describe("list total metadata", () => {
  it("does not warn when only the aggregate count is a lower bound", () => {
    const payload = {
      result: {
        metadata: {
          query_complete: true,
          query_status: "complete",
          total_rows: 26,
          total_rows_is_lower_bound: true,
        },
      },
    };

    expect(getListReadMessage(payload)).toBeNull();
  });

  it("does not warn when both page rows and the total are exact", () => {
    expect(
      getListReadMessage({
        result: {
          metadata: {
            query_complete: true,
            query_status: "complete",
            total_rows_is_lower_bound: false,
          },
        },
      }),
    ).toBeNull();
  });

  it("prioritizes an incomplete read over lower-bound count copy", () => {
    expect(
      getListReadMessage({
        result: {
          metadata: {
            query_complete: false,
            query_status: "degraded",
            total_rows_is_lower_bound: true,
          },
        },
      }),
    ).toBe(QUERY_READ_RETRY_MESSAGE);
  });

  it("does not show a sample banner on list surfaces", () => {
    expect(
      getListReadMessage({
        result: {
          metadata: {
            query_complete: false,
            query_status: "sampled",
            query_sampling_strategy: "time_stratified_latest_state",
            query_sampling_strata: 8,
            query_sampling_strata_completed: 8,
            total_rows_is_lower_bound: true,
          },
        },
      }),
    ).toBeNull();
  });

  it("does not warn when a degraded marker accompanies genuine rows", () => {
    expect(
      getListReadMessage({
        result: {
          table: [{ trace_id: "trace-a" }],
          metadata: {
            query_complete: false,
            query_status: "degraded",
            total_rows_is_lower_bound: true,
          },
        },
      }),
    ).toBeNull();
  });

  it("keeps an exact total available to exact-count consumers", () => {
    expect(
      getListTotalState({
        total_rows: 25,
        total_rows_exact: 25,
        total_rows_is_lower_bound: false,
      }),
    ).toEqual({
      totalRowCount: 25,
      totalRowCountLowerBound: null,
      totalRowCountIsLowerBound: false,
    });
  });

  it("never exposes a lower bound through the exact-total field", () => {
    expect(
      getListTotalState({
        total_rows: 26,
        total_rows_exact: null,
        total_rows_is_lower_bound: true,
      }),
    ).toEqual({
      totalRowCount: null,
      totalRowCountLowerBound: 26,
      totalRowCountIsLowerBound: true,
    });
  });

  it("preserves lower-bound semantics after select-all exclusions", () => {
    const selection = getSelectionCountState({
      selectAll: true,
      toggledNodes: ["trace-a", "trace-b"],
      totalRowCount: null,
      totalRowCountLowerBound: 26,
      totalRowCountIsLowerBound: true,
    });

    expect(selection).toEqual({ count: 24, isLowerBound: true });
    expect(formatSelectionCount(selection)).toBe("≥24");
  });

  it("keeps explicit row selection exact even when the list total is not", () => {
    expect(
      getSelectionCountState({
        selectAll: false,
        toggledNodes: ["trace-a", "trace-b"],
        totalRowCount: null,
        totalRowCountLowerBound: 26,
        totalRowCountIsLowerBound: true,
      }),
    ).toEqual({ count: 2, isLowerBound: false });
  });

  it("preserves the existing minimum count for exact select-all state", () => {
    expect(
      getSelectionCountState({
        selectAll: true,
        toggledNodes: ["trace-a"],
        totalRowCount: 1,
        totalRowCountLowerBound: null,
        totalRowCountIsLowerBound: false,
      }),
    ).toEqual({ count: 1, isLowerBound: false });
  });
});
