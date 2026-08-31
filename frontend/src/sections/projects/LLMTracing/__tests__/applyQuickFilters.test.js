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

// The number-popover branches hand their filter to openQuickFilter instead of
// applying it, so capture that payload rather than setFilters'.
function runPopoverQuickFilter(col, value) {
  let payload;
  const noop = () => {};
  applyQuickFilters(
    noop,
    (p) => {
      payload = p;
    },
    noop,
  )({ col, value, filterAnchor: {} });
  return payload?.filter;
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
  it("maps a Pass/Fail eval cell onto the passed/failed token the backend expects", () => {
    const col = {
      id: "eval-1",
      name: "task completion",
      groupBy: "Evaluation Metrics",
      outputType: "Pass/Fail",
    };

    const passed = runQuickFilter(col, 100);
    expect(passed.column_id).toBe("eval-1");
    expect(passed.display_name).toBe("task completion");
    expect(passed.filter_config.col_type).toBe("EVAL_METRIC");
    expect(passed.filter_config.filter_type).toBe("text");
    expect(passed.filter_config.filter_value).toBe("Passed");

    expect(runQuickFilter(col, 0).filter_config.filter_value).toBe("Failed");
  });

  it("emits nothing for an averaged Pass/Fail rate or an empty cell", () => {
    const col = {
      id: "eval-1",
      name: "task completion",
      groupBy: "Evaluation Metrics",
      outputType: "Pass/Fail",
    };
    expect(runQuickFilter(col, 66.67)).toBeUndefined();
    expect(runQuickFilter(col, "")).toBeUndefined();
    expect(runQuickFilter(col, null)).toBeUndefined();
  });

  it("sends voice system metrics as a number with col_type SYSTEM_METRIC (the PG path skips anything else)", () => {
    const f = runPopoverQuickFilter(
      { id: "turn_count", name: "Turn count", groupBy: "System Metrics" },
      5,
    );
    expect(f.column_id).toBe("turn_count");
    expect(f.display_name).toBe("Turn count");
    expect(f.filter_config.col_type).toBe("SYSTEM_METRIC");
    expect(f.filter_config.filter_type).toBe("number");
  });

  it("attaches col_type EVAL_METRIC to score evals (without it the eval id is read as a column)", () => {
    const f = runPopoverQuickFilter(
      {
        id: "eval-2",
        name: "toxicity",
        groupBy: "Evaluation Metrics",
        outputType: "score",
      },
      80,
    );
    expect(f.column_id).toBe("eval-2");
    expect(f.display_name).toBe("toxicity");
    expect(f.filter_config.col_type).toBe("EVAL_METRIC");
  });
});

// Runs the updater against an existing filter list so the dedupe branch is
// exercised, rather than always starting from [].
function runQuickFilterOver(prev, col, value) {
  let produced;
  const setFilters = (updater) => {
    produced = typeof updater === "function" ? updater(prev) : updater;
  };
  const noop = () => {};
  applyQuickFilters(setFilters, noop, noop)({ col, value, filterAnchor: {} });
  return produced;
}

describe("applyQuickFilters — one filter per column", () => {
  const col = { id: "ended_reason", name: "Ended reason" };

  it("replaces the existing filter when the same column is clicked with a new value", () => {
    // Two values for one column would be ANDed and match nothing, leaving two
    // indistinguishable chips for the same column.
    const first = runQuickFilterOver([], col, "customer-ended-call");
    const second = runQuickFilterOver(first, col, "silence-timed-out");

    expect(second).toHaveLength(1);
    expect(second[0].column_id).toBe("ended_reason");
    expect(second[0].filter_config.filter_value).toBe("silence-timed-out");
  });

  it("leaves filters on other columns alone", () => {
    const prev = runQuickFilterOver(
      [],
      { id: "provider", name: "Provider" },
      "anthropic",
    );
    const next = runQuickFilterOver(prev, col, "silence-timed-out");

    expect(next).toHaveLength(2);
    expect(next.map((f) => f.column_id).sort()).toEqual([
      "ended_reason",
      "provider",
    ]);
  });

  it("is a no-op when the same column and value are clicked twice", () => {
    const first = runQuickFilterOver([], col, "customer-ended-call");
    const second = runQuickFilterOver(first, col, "customer-ended-call");
    expect(second).toHaveLength(1);
    expect(second[0].filter_config.filter_value).toBe("customer-ended-call");
  });
});

// Review comment on PR #2064: NumberQuickFilterPopover renders
// `Where {filter.display_name || "value"} is`, so a payload without it reads
// "Where value is". Two of the four openQuickFilter call sites omitted it.
describe("applyQuickFilters — the number popover always gets a label", () => {
  it("labels a NUMBER_FILTER_FIELDS column", () => {
    const f = runPopoverQuickFilter(
      { id: "median_cost", name: "Median Cost" },
      5,
    );
    expect(f.display_name).toBe("Median Cost");
  });

  it("labels a numeric annotation column, whose id is a UUID", () => {
    const f = runPopoverQuickFilter(
      {
        id: "3f7c1e64-0a2b-4d55-9c31-2b6f8a4e1d90",
        name: "helpfulness",
        groupBy: "Annotation Metrics",
        annotationLabelType: AnnotationLabelTypes.NUMERIC,
      },
      4,
    );
    expect(f.display_name).toBe("helpfulness");
  });

  it("labels the chip for non-numeric annotation columns too", () => {
    // FilterChips reads display_name at the top level; without it these show
    // the raw UUID.
    const f = runQuickFilter(
      {
        id: "9a1d2c3b-4e5f-6789-abcd-ef0123456789",
        name: "tone",
        groupBy: "Annotation Metrics",
        annotationLabelType: AnnotationLabelTypes.CATEGORICAL,
      },
      "warm",
    );
    expect(f.display_name).toBe("tone");
  });
});
