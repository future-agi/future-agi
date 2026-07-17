import { describe, expect, it } from "vitest";
import {
  CREATED_AT,
  buildDefaultDateEntry,
  combineGraphFilters,
  selectPanelGraphFilters,
} from "../graphFilterUtils";
import { FILTER_FOR_HAS_EVAL } from "../../common";

const dateFilter = {
  dateFilter: ["2026-07-01T00:00:00.000Z", "2026-07-08T00:00:00.000Z"],
};

const statusFilter = {
  id: "fe-key-1",
  column_id: "status",
  filter_config: {
    col_type: "NORMAL",
    filter_type: "text",
    filter_op: "equals",
    filter_value: "SUCCESS",
  },
};

const createdAtFilter = {
  column_id: CREATED_AT,
  filter_config: {
    filter_type: "datetime",
    filter_op: "between",
    filter_value: ["2026-07-01T00:00:00.000Z", "2026-07-02T00:00:00.000Z"],
  },
};

const metricFilter = {
  id: "fe-key-2",
  column_id: "latency",
  filter_config: {
    col_type: "SYSTEM_METRIC",
    filter_type: "number",
    filter_op: "greater_than",
    filter_value: 2,
  },
};

describe("combineGraphFilters", () => {
  it("users/sessions mode (extraFilters omitted): non-date filters survive", () => {
    const result = combineGraphFilters({
      filters: [statusFilter, createdAtFilter],
      extraFilters: undefined,
      dateFilter,
      hasEvalFilter: false,
    });
    expect(result.map((f) => f.column_id)).toEqual(["status", CREATED_AT]);
  });

  it("trace/span mode with EMPTY extraFilters still strips col-level filters", () => {
    // Regression: the mode gate must key off prop presence, not emptiness.
    // An `extraFilters = []` default made this branch run for users/sessions.
    const result = combineGraphFilters({
      filters: [statusFilter, createdAtFilter],
      extraFilters: [],
      dateFilter,
      hasEvalFilter: false,
    });
    expect(result.map((f) => f.column_id)).toEqual([CREATED_AT]);
  });

  it("trace/span mode: toolbar extraFilters are forwarded", () => {
    const result = combineGraphFilters({
      filters: [statusFilter],
      extraFilters: [metricFilter],
      dateFilter,
      hasEvalFilter: false,
    });
    expect(result.map((f) => f.column_id)).toEqual(["latency", CREATED_AT]);
  });

  it("adds a default created_at entry only when none exists", () => {
    const withExplicit = combineGraphFilters({
      filters: [createdAtFilter],
      extraFilters: [],
      dateFilter,
      hasEvalFilter: false,
    });
    expect(
      withExplicit.filter((f) => f.column_id === CREATED_AT),
    ).toHaveLength(1);

    const withDefault = combineGraphFilters({
      filters: [],
      extraFilters: [],
      dateFilter,
      hasEvalFilter: false,
    });
    expect(withDefault).toHaveLength(1);
    expect(withDefault[0].column_id).toBe(CREATED_AT);
    expect(withDefault[0].filter_config.filter_op).toBe("between");
  });

  it("appends the has-eval filter when enabled", () => {
    const result = combineGraphFilters({
      filters: [],
      extraFilters: [],
      dateFilter: undefined,
      hasEvalFilter: true,
    });
    expect(result).toEqual([FILTER_FOR_HAS_EVAL]);
  });
});

describe("buildDefaultDateEntry", () => {
  it("returns empty when a created_at filter already exists", () => {
    expect(buildDefaultDateEntry([createdAtFilter], dateFilter)).toEqual([]);
  });

  it("returns empty when the date range is incomplete", () => {
    expect(buildDefaultDateEntry([], { dateFilter: [null, null] })).toEqual([]);
    expect(buildDefaultDateEntry([], undefined)).toEqual([]);
  });
});

describe("selectPanelGraphFilters", () => {
  const primary = [metricFilter];
  const compare = [statusFilter];

  it("hydrates the panel from compare filters when editing the compare graph", () => {
    // Regression: the panel was always fed the primary extraFilters, so
    // opening the Compare Graph filter panel showed primary filters and
    // applying wiped the existing compare-only filters.
    expect(selectPanelGraphFilters("compare", primary, compare)).toBe(compare);
  });

  it("hydrates from primary filters otherwise", () => {
    expect(selectPanelGraphFilters("primary", primary, compare)).toBe(primary);
    expect(selectPanelGraphFilters(undefined, primary, compare)).toBe(primary);
  });
});
