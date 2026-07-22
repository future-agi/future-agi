import { describe, expect, it } from "vitest";

import {
  apiFilterHasValue,
  isNumberFilterOp,
  isRangeFilterOp,
  normalizeApiFilterOp,
} from "../filter-operators";

describe("annotation queue filter operator contract", () => {
  it("keeps API operators strict without legacy normalization", () => {
    expect(normalizeApiFilterOp("equals")).toBe("equals");
    expect(normalizeApiFilterOp("not_between")).toBe("not_between");
    expect(normalizeApiFilterOp("not_in_between")).toBe("not_in_between");
    expect(normalizeApiFilterOp()).toBe("");
  });

  it("classifies only canonical number/range operators", () => {
    expect(isNumberFilterOp("not_equals")).toBe(true);
    expect(isNumberFilterOp("not_equal_to")).toBe(false);
    expect(isRangeFilterOp("not_in_between")).toBe(false);
    expect(isRangeFilterOp("not_between")).toBe(true);
  });

  it("drops empty value filters while keeping valueless null checks", () => {
    expect(
      apiFilterHasValue({
        column_id: "status",
        filter_config: { filter_op: "in", filter_value: [] },
      }),
    ).toBe(false);
    expect(
      apiFilterHasValue({
        column_id: "status",
        filter_config: { filter_op: "not_in", filter_value: [""] },
      }),
    ).toBe(false);
    expect(
      apiFilterHasValue({
        column_id: "status",
        filter_config: { filter_op: "is_null" },
      }),
    ).toBe(true);
  });
});
