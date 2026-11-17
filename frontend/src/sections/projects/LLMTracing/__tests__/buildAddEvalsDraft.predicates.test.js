import { afterEach, describe, expect, it, vi } from "vitest";
import { NewTaskValidationSchema } from "src/sections/common/EvalsTasks/NewTaskDrawer/validation";
import { serializeTaskFilterRowsForApi } from "src/sections/common/EvalsTasks/task_filter_serialization";

import { buildAddEvalsDraft, toAddEvalsFormRows } from "../buildAddEvalsDraft";

const expectPreservedPredicate = (source) => {
  const rows = toAddEvalsFormRows([source]);
  expect(rows).toHaveLength(1);
  expect(rows[0].filterConfig).toMatchObject({
    filterType: source.filter_config.filter_type,
    filterOp: source.filter_config.filter_op,
    filterValue: source.filter_config.filter_value,
  });
  expect(serializeTaskFilterRowsForApi(rows)).toEqual([source]);
};

// These assert the whole predicate, not merely its first value. The old
// builder emitted one row per value, so ranges and alternatives changed
// meaning even though their individual operators still looked correct.
describe("Add Evals predicate preservation", () => {
  afterEach(() => vi.restoreAllMocks());

  it.each(["between", "not_between"])(
    "preserves numeric %s as one range predicate",
    (filterOp) => {
      expectPreservedPredicate({
        column_id: "latency",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "number",
          filter_op: filterOp,
          filter_value: [100, 500],
        },
      });
    },
  );

  it.each([
    ["text", "in"],
    ["text", "not_in"],
    ["categorical", "in"],
    ["categorical", "not_in"],
  ])(
    "preserves %s %s alternatives in one predicate",
    (filterType, filterOp) => {
      expectPreservedPredicate({
        column_id: "label",
        property_id: "annotation:label",
        filter_config: {
          col_type: "ANNOTATION",
          filter_type: filterType,
          filter_op: filterOp,
          filter_value: ["needs, review", "accepted"],
        },
      });
    },
  );

  it("preserves a boolean false operand and its original type", () => {
    expectPreservedPredicate({
      column_id: "flagged",
      property_id: "custom_attribute:flagged",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "boolean",
        filter_op: "equals",
        filter_value: false,
      },
    });
  });

  it.each(["between", "not_between"])(
    "preserves datetime %s endpoints and their original type",
    (filterOp) => {
      expectPreservedPredicate({
        column_id: "start_time",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "datetime",
          filter_op: filterOp,
          filter_value: [
            "2026-11-01T00:00:00.000Z",
            "2026-11-02T00:00:00.000Z",
          ],
        },
      });
    },
  );

  it("preserves commas and whitespace in a scalar string", () => {
    expectPreservedPredicate({
      column_id: "region",
      property_id: "custom_attribute:region",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "text",
        filter_op: "equals",
        filter_value: "  sales, west  ",
      },
    });
  });

  it.each(["is_null", "is_not_null"])(
    "preserves a %s predicate without an operand",
    (filterOp) => {
      expectPreservedPredicate({
        column_id: "latency",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "number",
          filter_op: filterOp,
          filter_value: null,
        },
      });
    },
  );

  it("carries main and extra predicates through the persisted task draft", () => {
    const storedDrafts = new Map();
    vi.spyOn(window.localStorage, "setItem").mockImplementation(
      (key, value) => {
        storedDrafts.set(key, String(value));
      },
    );
    const mainFilter = {
      column_id: "latency",
      filter_config: {
        col_type: "SYSTEM_METRIC",
        filter_type: "number",
        filter_op: "between",
        filter_value: [100, 500],
      },
    };
    const extraFilter = {
      column_id: "region",
      property_id: "custom_attribute:region",
      filter_config: {
        col_type: "SPAN_ATTRIBUTE",
        filter_type: "text",
        filter_op: "in",
        filter_value: ["east", "west"],
      },
    };
    const url = buildAddEvalsDraft({
      observeId: "project-1",
      rowType: "spans",
      mainFilters: [mainFilter],
      extraFilters: [extraFilter],
      dateFilter: {
        dateFilter: ["2026-11-01T00:00:00.000Z", "2026-11-02T00:00:00.000Z"],
        dateOption: "Custom",
      },
      returnTo: "/dashboard/observe/project-1",
    });
    const params = new URLSearchParams(url.split("?")[1]);
    const draft = JSON.parse(
      storedDrafts.get(`task-draft-${params.get("draft")}`),
    );
    const payload = NewTaskValidationSchema().parse({
      ...draft.values,
      name: "Latency evaluation",
      evalsDetails: [{ id: "evaluator-1" }],
    });

    expect(draft.values.filters).toHaveLength(2);
    expect(payload.filters.filters).toEqual([mainFilter, extraFilter]);
    expect(payload.filters.date_range).toEqual([
      "2026-11-01T00:00:00.000Z",
      "2026-11-02T00:00:00.000Z",
    ]);
    expect(payload.rowType).toBe("spans");
    expect(params.get("returnTo")).toBe("/dashboard/observe/project-1");
  });

  it("keeps separate same-column predicates and a numeric zero", () => {
    const source = [
      {
        column_id: "latency",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "number",
          filter_op: "greater_than_or_equal",
          filter_value: 0,
        },
      },
      {
        column_id: "latency",
        filter_config: {
          col_type: "SYSTEM_METRIC",
          filter_type: "number",
          filter_op: "less_than",
          filter_value: 500,
        },
      },
    ];
    const rows = toAddEvalsFormRows(source);
    expect(rows).toHaveLength(2);
    expect(serializeTaskFilterRowsForApi(rows)).toEqual(source);
  });

  it("still ignores missing fields and the separately handled created_at field", () => {
    expect(toAddEvalsFormRows()).toEqual([]);
    expect(toAddEvalsFormRows(null)).toEqual([]);
    expect(
      toAddEvalsFormRows([
        null,
        {},
        {
          column_id: "created_at",
          filter_config: {
            filter_type: "datetime",
            filter_op: "between",
            filter_value: ["2026-11-01", "2026-11-02"],
          },
        },
      ]),
    ).toEqual([]);
  });
});
