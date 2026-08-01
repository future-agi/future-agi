import { describe, expect, it } from "vitest";

import { buildProjectListApiFilters, projectOperatorFilter } from "./common";

const parse = (json) => (json == null ? json : JSON.parse(json));

describe("buildProjectListApiFilters", () => {
  it("returns null for empty input", () => {
    expect(buildProjectListApiFilters(null)).toBeNull();
    expect(buildProjectListApiFilters(undefined)).toBeNull();
    expect(buildProjectListApiFilters([])).toBeNull();
  });

  it("maps panel in/not_in to backend equals/not_equals", () => {
    expect(
      parse(
        buildProjectListApiFilters([
          { field: "name", operator: "in", value: "checkout" },
          { field: "name", operator: "not_in", value: "legacy" },
        ]),
      ),
    ).toEqual([
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "checkout",
        },
      },
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "not_equals",
          filter_value: "legacy",
        },
      },
    ]);
  });

  it("passes contains/not_contains through unchanged", () => {
    expect(
      parse(
        buildProjectListApiFilters([
          { field: "tags", operator: "contains", value: "prod" },
          { field: "tags", operator: "not_contains", value: "test" },
        ]),
      ),
    ).toEqual([
      {
        column_id: "tags",
        filter_config: {
          filter_type: "text",
          filter_op: "contains",
          filter_value: "prod",
        },
      },
      {
        column_id: "tags",
        filter_config: {
          filter_type: "text",
          filter_op: "not_contains",
          filter_value: "test",
        },
      },
    ]);
  });

  it("drops operators the project-list backend can't honor", () => {
    expect(
      buildProjectListApiFilters([
        { field: "name", operator: "starts_with", value: "check" },
        { field: "name", operator: "ends_with", value: "out" },
        { field: "name", operator: "is_null", value: "" },
        { field: "name", operator: "is_not_null", value: "" },
      ]),
    ).toBeNull();
  });

  it("keeps supported rows and drops unsupported ones in a mixed set", () => {
    expect(
      parse(
        buildProjectListApiFilters([
          { field: "name", operator: "starts_with", value: "check" },
          { field: "name", operator: "contains", value: "service" },
        ]),
      ),
    ).toEqual([
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "contains",
          filter_value: "service",
        },
      },
    ]);
  });

  it("collapses array values to the first entry", () => {
    expect(
      parse(
        buildProjectListApiFilters([
          { field: "name", operator: "in", value: ["checkout", "billing"] },
        ]),
      ),
    ).toEqual([
      {
        column_id: "name",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "checkout",
        },
      },
    ]);
  });

  it("skips rows with empty or nullish values", () => {
    expect(
      buildProjectListApiFilters([
        { field: "name", operator: "in", value: "" },
        { field: "name", operator: "in", value: null },
        { field: "name", operator: "in", value: [""] },
      ]),
    ).toBeNull();
  });
});

describe("projectOperatorFilter", () => {
  it("allows only the backend-supported operators", () => {
    for (const value of ["in", "not_in", "contains", "not_contains"]) {
      expect(projectOperatorFilter({ value })).toBe(true);
    }
  });

  it("rejects operators the project-list backend can't honor", () => {
    for (const value of [
      "starts_with",
      "ends_with",
      "is_null",
      "is_not_null",
    ]) {
      expect(projectOperatorFilter({ value })).toBe(false);
    }
  });
});
