import { describe, it, expect } from "vitest";
import { applyQuickFilters } from "../common";
import { AnnotationLabelTypes } from "src/utils/constants";

// applyQuickFilters is curried: (setFilters, openQuickFilter, setFilterOpen)
// => ({ col, value, filterAnchor }). It appends the built filter via
// setFilters(updater); run the updater against [] to capture what it emits.
function runQuickFilter(col, value) {
  let produced;
  const setFilters = (updater) => {
    produced = typeof updater === "function" ? updater([]) : updater;
  };
  const noop = () => {};
  applyQuickFilters(setFilters, noop, noop)({ col, value, filterAnchor: {} });
  return produced?.[0];
}

describe("applyQuickFilters", () => {
  it("attaches col_type SYSTEM_METRIC for a system column (without it the list 400s)", () => {
    const f = runQuickFilter({ id: "provider", name: "Provider" }, "anthropic");
    expect(f.column_id).toBe("provider");
    expect(f.filter_config.col_type).toBe("SYSTEM_METRIC");
    expect(f.filter_config.filter_op).toBe("equals");
    expect(f.filter_config.filter_value).toBe("anthropic");
    expect(f.display_name).toBeUndefined();
  });

  it("remaps the trace_name cell to the canonical `name` field as in + array", () => {
    const f = runQuickFilter(
      { id: "trace_name", name: "Trace Name" },
      "my-trace",
    );
    expect(f.column_id).toBe("name");
    expect(f.display_name).toBe("Trace Name");
    expect(f.filter_config.col_type).toBe("SYSTEM_METRIC");
    expect(f.filter_config.filter_op).toBe("in");
    expect(f.filter_config.filter_value).toEqual(["my-trace"]);
  });

  it("uses col_type ANNOTATION for annotation-metric columns", () => {
    const f = runQuickFilter(
      {
        id: "ann-1",
        groupBy: "Annotation Metrics",
        annotationLabelType: AnnotationLabelTypes.TEXT,
      },
      "good",
    );
    expect(f.column_id).toBe("ann-1");
    expect(f.filter_config.col_type).toBe("ANNOTATION");
  });
});
