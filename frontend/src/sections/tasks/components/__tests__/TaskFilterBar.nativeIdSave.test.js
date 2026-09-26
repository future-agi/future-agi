// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
vi.mock("src/sections/evals/components/EvalResultDisplay", () => ({ default: () => null }));
import { convertNewToOld } from "src/sections/tasks/components/TaskFilterBar";
import { buildApiFilterArray } from "src/sections/tasks/components/TaskLivePreview";
import {
  getNewTaskFilters,
  NewTaskValidationSchema,
} from "src/sections/common/EvalsTasks/NewTaskDrawer/validation";

const PROJECT = "10000000-0000-4000-8000-000000000001";
const A = "20000000-0000-4000-8000-000000000001";
const B = "20000000-0000-4000-8000-000000000002";
const save = (filters) => getNewTaskFilters({ runType: "continuous", filters }, PROJECT, true);
const wire = (column_id, col_type, filter_op, filter_value, property_id, types) => ({
  column_id,
  ...(property_id ? { property_id } : {}),
  filter_config: {
    filter_type: "text", filter_op, filter_value, col_type,
    ...(types ? { attribute_value_types: types } : {}),
  },
});
const nativeRow = (property, filterOp, extra = {}) => ({
  property,
  filterConfig: { filterType: "text", filterOp, filterValue: [A, B] },
  ...extra,
});

describe("task native ID save contract", () => {
  it.each(["trace_id", "session_id"].flatMap((id) =>
    ["not_in", "not_equals"].flatMap((op) =>
      ["legacy", "system", "normal", "nested", "category"].map((source) => [id, op, source]),
    ),
  ))("keeps %s %s with %s source in canonical rows", (id, op, source) => {
    const row = nativeRow(id, op);
    if (source === "system") row.apiColType = "SYSTEM_METRIC";
    if (source === "normal") row.apiColType = "NORMAL";
    if (source === "nested") row.filterConfig.colType = "system";
    if (source === "category") row.fieldCategory = "system";
    const before = structuredClone(row);
    expect(save([row])).toEqual({
      filters: { project_id: PROJECT },
      attributeFilters: [wire(id, source === "normal" ? "NORMAL" : "SYSTEM_METRIC", "not_in", [A, B])],
    });
    expect(row).toEqual(before);
  });

  it.each(["trace_id", "session_id"].flatMap((id) =>
    [undefined, "equals", "in"].map((op) => [id, op]),
  ))("retains source-less positive legacy %s %s extraction", (id, op) => {
    const row = nativeRow(id, op);
    expect(save([row])).toEqual({
      filters: { project_id: PROJECT, [id]: [A, B] }, attributeFilters: [],
    });
  });

  it.each(["trace_id", "span_id", "session"].flatMap((id) =>
    ["spans", "traces", "sessions", "voiceCalls"].map((rowType) => [id, rowType]),
  ))("panel %s exclusion survives %s preview and save", (id, rowType) => {
    const rows = convertNewToOld([{
      field: id, fieldCategory: "system", apiColType: "SYSTEM_METRIC",
      fieldType: "string", operator: "not_in", value: [A, B],
    }], { rowType });
    expect(save(rows)).toEqual({
      filters: { project_id: PROJECT },
      attributeFilters: [wire(id, "SYSTEM_METRIC", "not_in", [A, B])],
    });
    expect(buildApiFilterArray(rows)).toEqual([{
      column_id: id,
      filter_config: { filter_type: "text", filter_op: "not_in", filter_value: [A, B] },
    }]);
  });

  it.each(["trace_id", "session_id", "span_id", "session"].flatMap((id) =>
    ["in", "not_in"].map((op) => [id, op]),
  ))("keeps explicit raw attribute %s %s typed membership", (id, op) => {
    const values = ["001", 1, false];
    const types = ["string", "number", "boolean"];
    const rows = convertNewToOld([{
      field: id, registryId: "custom_attribute:" + id,
      fieldCategory: "attribute", apiColType: "SPAN_ATTRIBUTE",
      fieldType: "string", operator: op, value: values, valueTypes: types,
    }]);
    const expected = wire(id, "SPAN_ATTRIBUTE", op, values, "custom_attribute:" + id, types);
    expect(save(rows)).toEqual({ filters: { project_id: PROJECT }, attributeFilters: [expected] });
    expect(buildApiFilterArray(rows)).toEqual([expected]);
  });

  it.each(["SPAN_ATTRIBUTE", "EVAL_METRIC", "ANNOTATION"].flatMap((source) =>
    ["trace_id", "session_id"].map((id) => [source, id]),
  ))("explicit %s outranks a legacy-named %s property", (source, id) => {
    const row = nativeRow(id, "not_in", { apiColType: source, registryId: source + ":" + id });
    expect(save([row])).toEqual({
      filters: { project_id: PROJECT },
      attributeFilters: [wire(id, source, "not_in", [A, B], source + ":" + id)],
    });
  });

  it("keeps independent mixed sources and opposite operators on one ID name", () => {
    const panel = [
      { field: "trace_id", fieldCategory: "system", apiColType: "SYSTEM_METRIC", fieldType: "string", operator: "in", value: [A, B] },
      { field: "trace_id", fieldCategory: "system", apiColType: "SYSTEM_METRIC", fieldType: "string", operator: "not_in", value: [B] },
      { field: "trace_id", fieldCategory: "eval", apiColType: "EVAL_METRIC", registryId: "eval:trace_id", fieldType: "string", operator: "not_in", value: ["fail"] },
      { field: "trace_id", fieldCategory: "annotation", apiColType: "ANNOTATION", registryId: "annotation:trace_id", fieldType: "string", operator: "in", value: ["reviewed"] },
      { field: "trace_id", fieldCategory: "attribute", apiColType: "SPAN_ATTRIBUTE", registryId: "custom_attribute:trace_id", fieldType: "string", operator: "not_in", value: ["001", 1, false], valueTypes: ["string", "number", "boolean"] },
    ];
    const rows = convertNewToOld(panel);
    expect(save(rows)).toEqual({
      filters: { project_id: PROJECT },
      attributeFilters: [
        wire("trace_id", "SYSTEM_METRIC", "in", [A, B]),
        wire("trace_id", "SYSTEM_METRIC", "not_in", [B]),
        wire("trace_id", "EVAL_METRIC", "not_in", ["fail"], "eval:trace_id"),
        wire("trace_id", "ANNOTATION", "in", ["reviewed"], "annotation:trace_id"),
        wire("trace_id", "SPAN_ATTRIBUTE", "not_in", ["001", 1, false], "custom_attribute:trace_id", ["string", "number", "boolean"]),
      ],
    });
  });

  it("schema transform sends the same canonical exclusion on create", () => {
    const result = NewTaskValidationSchema().parse({
      name: "Exclude selected trace", project: PROJECT, spansLimit: "10",
      samplingRate: 100, evalsDetails: [{ id: A }], startDate: "", endDate: "",
      runType: "continuous", rowType: "traces",
      filters: [nativeRow("trace_id", "not_in", { propertyId: "trace_id", apiColType: "SYSTEM_METRIC" })],
    });
    expect(result.filters).toEqual({
      project_id: PROJECT, filters: [wire("trace_id", "SYSTEM_METRIC", "not_in", [A, B])],
    });
  });

  it("keeps unrelated positive span-kind sibling aliases", () => {
    expect(save([{ property: "node_type", filterConfig: { filterType: "text", filterOp: "in", filterValue: ["llm"] } }])).toEqual({
      filters: { project_id: PROJECT, observation_type: ["llm"] }, attributeFilters: [],
    });
  });
});
