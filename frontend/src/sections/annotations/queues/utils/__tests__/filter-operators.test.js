import { describe, expect, it } from "vitest";

import {
  apiFilterHasValue,
  apiOpToPanel,
  isNumberFilterOp,
  isRangeFilterOp,
  normalizeApiFilterOp,
  panelOperatorAndValueToApi,
  panelOpToApi,
} from "../filter-operators";

describe("annotation queue filter operator contract", () => {
  it("serializes panel-only number operators to backend canonical operators", () => {
    expect(panelOpToApi("equal_to")).toBe("equals");
    expect(panelOpToApi("not_equal_to")).toBe("not_equals");
    expect(panelOpToApi("not_between")).toBe("not_between");
  });

  it("serializes panel-only text operators to backend canonical operators", () => {
    expect(panelOpToApi("is")).toBe("equals");
    expect(panelOpToApi("is_not")).toBe("not_equals");
    expect(panelOpToApi("contains")).toBe("contains");
  });

  it("keeps API operators strict when hydrating the panel", () => {
    expect(normalizeApiFilterOp("equals")).toBe("equals");
    expect(normalizeApiFilterOp("not_between")).toBe("not_between");
    expect(normalizeApiFilterOp("not_in_between")).toBe("not_in_between");
  });

  it("maps canonical backend number operators back to panel operators", () => {
    expect(apiOpToPanel("equals", "number")).toBe("equals");
    expect(apiOpToPanel("not_equals", "number")).toBe("not_equals");
    expect(apiOpToPanel("not_between", "number")).toBe("not_between");
  });

  it("maps canonical backend text and date operators back to panel operators", () => {
    expect(apiOpToPanel("equals", "text")).toBe("is");
    expect(apiOpToPanel("not_equals", "text")).toBe("is_not");
    expect(apiOpToPanel("equals", "date")).toBe("on");
    expect(apiOpToPanel("less_than", "date")).toBe("before");
  });

  it("classifies only canonical number/range operators", () => {
    expect(isNumberFilterOp("not_equals")).toBe(true);
    expect(isNumberFilterOp("not_equal_to")).toBe(false);
    expect(isRangeFilterOp("not_in_between")).toBe(false);
    expect(isRangeFilterOp("not_between")).toBe(true);
  });

  it("builds API operator/value pairs without leaking panel-only operators", () => {
    expect(panelOperatorAndValueToApi("equal_to", "45")).toEqual({
      filterOp: "equals",
      filterValue: "45",
    });
    expect(panelOperatorAndValueToApi("not_equal_to", "45")).toEqual({
      filterOp: "not_equals",
      filterValue: "45",
    });
    expect(panelOperatorAndValueToApi("not_between", [10, 50])).toEqual({
      filterOp: "not_between",
      filterValue: ["10", "50"],
    });
  });

  it("promotes multi-select equality to in/not_in while preserving ranges", () => {
    expect(panelOperatorAndValueToApi("is", ["ok"])).toEqual({
      filterOp: "in",
      filterValue: ["ok"],
    });
    expect(panelOperatorAndValueToApi("is", ["ok", "warning"])).toEqual({
      filterOp: "in",
      filterValue: ["ok", "warning"],
    });
    expect(panelOperatorAndValueToApi("is_not", ["ok", "warning"])).toEqual({
      filterOp: "not_in",
      filterValue: ["ok", "warning"],
    });
    expect(panelOperatorAndValueToApi("in", ["response"])).toEqual({
      filterOp: "in",
      filterValue: ["response"],
    });
    expect(panelOperatorAndValueToApi("not_in", ["response", "tool"])).toEqual({
      filterOp: "not_in",
      filterValue: ["response", "tool"],
    });
    expect(panelOperatorAndValueToApi("between", [1, 5])).toEqual({
      filterOp: "between",
      filterValue: ["1", "5"],
    });
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
