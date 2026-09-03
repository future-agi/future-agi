import { describe, expect, it } from "vitest";
import {
  CREATED_AT,
  buildDefaultDateEntry,
  combineGraphFilters,
  isCreatedAtFilter,
  resolveAgentGraphProjectScopes,
  selectPanelGraphFilters,
  singleProjectIdFromFilters,
} from "../graphFilterUtils";
import {
  FILTER_FOR_ERRORS,
  FILTER_FOR_HAS_EVAL,
  FILTER_FOR_NON_ANNOTATED,
} from "../../common";

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

const nameFilter = {
  id: "fe-key-3",
  column_id: "trace_name",
  filter_config: {
    col_type: "NORMAL",
    filter_type: "text",
    filter_op: "contains",
    filter_value: "checkout",
  },
};

const propertyFilter = {
  id: "fe-key-4",
  registryId: "custom_attribute:customer.plan",
  column_id: "customer.plan",
  filter_config: {
    col_type: "SPAN_ATTRIBUTE",
    filter_type: "text",
    filter_op: "equals",
    filter_value: "enterprise",
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

  it("trace/span mode preserves validated filters when extraFilters is empty", () => {
    const result = combineGraphFilters({
      filters: [statusFilter, createdAtFilter],
      extraFilters: [],
      dateFilter,
      hasEvalFilter: false,
    });
    expect(result.map((f) => f.column_id)).toEqual(["status", CREATED_AT]);
  });

  it("preserves status, name, and property filters while applying graph filters", () => {
    const result = combineGraphFilters({
      filters: [statusFilter, nameFilter, propertyFilter, createdAtFilter],
      extraFilters: [metricFilter],
      dateFilter,
      hasEvalFilter: false,
    });
    expect(result).toEqual([
      statusFilter,
      nameFilter,
      propertyFilter,
      metricFilter,
      createdAtFilter,
    ]);
  });

  it("adds a default created_at entry only when none exists", () => {
    const withExplicit = combineGraphFilters({
      filters: [createdAtFilter],
      extraFilters: [],
      dateFilter,
      hasEvalFilter: false,
    });
    expect(withExplicit.filter((f) => f.column_id === CREATED_AT)).toHaveLength(
      1,
    );

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

  it("keeps one created_at filter with deterministic source precedence", () => {
    const explicitGraphDateFilter = {
      ...createdAtFilter,
      filter_config: {
        ...createdAtFilter.filter_config,
        filter_value: ["2026-06-01T00:00:00.000Z", "2026-06-02T00:00:00.000Z"],
      },
    };

    const result = combineGraphFilters({
      filters: [statusFilter, createdAtFilter],
      extraFilters: [metricFilter, explicitGraphDateFilter],
      dateFilter,
      hasEvalFilter: false,
    });

    expect(result).toEqual([statusFilter, metricFilter, createdAtFilter]);
    expect(result.filter(isCreatedAtFilter)).toHaveLength(1);
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

  it.each([
    [false, false, []],
    [true, false, ["status"]],
    [false, true, ["has_annotation"]],
    [true, true, ["status", "has_annotation"]],
  ])(
    "keeps Display filters aligned for errors=%s nonAnnotated=%s",
    (errors, nonAnnotated, expectedColumns) => {
      const metricFilters = [
        ...(errors ? [FILTER_FOR_ERRORS] : []),
        ...(nonAnnotated ? [FILTER_FOR_NON_ANNOTATED] : []),
      ];
      const result = combineGraphFilters({
        filters: [],
        extraFilters: [],
        metricFilters,
        dateFilter: undefined,
        hasEvalFilter: false,
      });

      expect(result.map((filter) => filter.column_id)).toEqual(expectedColumns);
    },
  );

  it("composes Basic, Display, eval-only, and date filters once", () => {
    const result = combineGraphFilters({
      filters: [],
      extraFilters: [metricFilter],
      metricFilters: [FILTER_FOR_ERRORS, FILTER_FOR_NON_ANNOTATED],
      dateFilter,
      hasEvalFilter: true,
    });

    expect(result.map((filter) => filter.column_id)).toEqual([
      "latency",
      "status",
      "has_annotation",
      "has_eval",
      CREATED_AT,
    ]);
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

describe("singleProjectIdFromFilters", () => {
  it.each([
    ["equals", "project-1"],
    ["is", "project-1"],
    ["in", ["project-1"]],
  ])("extracts one positive project scope for %s", (filterOp, filterValue) => {
    expect(
      singleProjectIdFromFilters([
        {
          column_id: "project_id",
          filter_config: {
            filter_type: "text",
            filter_op: filterOp,
            filter_value: filterValue,
          },
        },
      ]),
    ).toBe("project-1");
  });

  it.each([
    ["in", ["project-1", "project-2"]],
    ["not_in", ["project-1"]],
    ["not_equals", "project-1"],
  ])("does not invent a single scope for %s %j", (filterOp, filterValue) => {
    expect(
      singleProjectIdFromFilters([
        {
          column_id: "project_id",
          filter_config: { filter_op: filterOp, filter_value: filterValue },
        },
      ]),
    ).toBeNull();
  });

  it("fails closed when multiple Project filters are present", () => {
    const filter = {
      column_id: "project_id",
      filter_config: { filter_op: "in", filter_value: ["project-1"] },
    };
    expect(singleProjectIdFromFilters([filter, filter])).toBeNull();
  });
});

describe("resolveAgentGraphProjectScopes", () => {
  const projectFilter = (projectId) => ({
    column_id: "project_id",
    filter_config: {
      filter_op: "equals",
      filter_value: projectId,
    },
  });

  it("uses the route project for both panes on a project Observe page", () => {
    expect(
      resolveAgentGraphProjectScopes({
        routeProjectId: "route-project",
        primaryFilters: [projectFilter("ignored-primary")],
        compareFilters: [projectFilter("ignored-compare")],
      }),
    ).toEqual({
      primaryProjectId: "route-project",
      compareProjectId: "route-project",
    });
  });

  it("keeps primary and compare project filters independent in user detail", () => {
    expect(
      resolveAgentGraphProjectScopes({
        routeProjectId: null,
        primaryFilters: [projectFilter("primary-project")],
        compareFilters: [projectFilter("compare-project")],
      }),
    ).toEqual({
      primaryProjectId: "primary-project",
      compareProjectId: "compare-project",
    });
  });
});
