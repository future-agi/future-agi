import { describe, expect, it } from "vitest";
import { getZeroBasedGridPage } from "../agGridPagination";

describe("getZeroBasedGridPage", () => {
  it("uses the requested block size for zero-based page arithmetic", () => {
    expect(getZeroBasedGridPage({ startRow: 30, endRow: 60 }, 10)).toEqual({
      pageNumber: 1,
      pageSize: 30,
    });
    expect(getZeroBasedGridPage({ startRow: 60, endRow: 90 }, 10)).toEqual({
      pageNumber: 2,
      pageSize: 30,
    });
  });

  it("uses a validated fallback when AG Grid omits an end row", () => {
    expect(getZeroBasedGridPage({ startRow: 20 }, 10)).toEqual({
      pageNumber: 2,
      pageSize: 10,
    });
    expect(() => getZeroBasedGridPage({}, 0)).toThrow(
      "AG Grid page size must be a positive integer",
    );
  });
});
