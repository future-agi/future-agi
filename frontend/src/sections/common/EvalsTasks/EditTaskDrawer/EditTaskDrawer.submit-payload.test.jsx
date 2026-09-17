// @vitest-environment jsdom
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import DetailsEdit from "./DetailsEdit";
import EditTaskDrawerV2 from "./EditTaskDrawerV2";

const state = vi.hoisted(() => ({
  patch: vi.fn(), get: vi.fn(), post: vi.fn(), rows: [], empty: [],
  project: "10000000-0000-4000-8000-000000000001",
  task: "20000000-0000-4000-8000-000000000001",
  eval: "30000000-0000-4000-8000-000000000001",
}));
vi.mock("src/utils/axios", () => ({
  default: { get: (...args) => state.get(...args), patch: (...args) => state.patch(...args), post: (...args) => state.post(...args) },
  endpoints: { project: {
    patchEvalTask: () => "/task/update", getEvalTaskConfig: () => "/task/configured",
    listProjects: () => "/projects", createEvalTask: () => "/task/create",
    createEvalTaskConfig: () => "/task/configure",
  } },
}));
vi.mock("../common", () => ({
  FIELD_CATEGORY_TO_COL_TYPE: { attribute: "SPAN_ATTRIBUTE", system: "SYSTEM_METRIC", eval: "EVAL_METRIC", annotation: "ANNOTATION" },
  ANNOTATION_COLUMN_IDS: new Set(["annotator", "my_annotations"]),
  getDefaultTaskValues: () => ({
    name: "Mounted task", project: state.project, rowType: "traces", runType: "continuous",
    spansLimit: "10", samplingRate: 100, evalsDetails: [{ id: state.eval, name: "Quality" }],
    startDate: "2026-09-01T00:00:00.000Z", endDate: "2026-09-02T00:00:00.000Z", filters: [],
  }),
  useGetTaskData: () => ({ data: { id: state.task, project_id: state.project }, isLoading: false, isError: false }),
}));
vi.mock("src/auth/hooks", () => ({ useAuthContext: () => ({ role: "admin" }) }));
vi.mock("src/utils/rolePermissionMapping", () => ({
  PERMISSIONS: { ADD_TASKS_ALERTS: "edit" }, RolePermission: { OBSERVABILITY: { edit: { admin: true } } },
}));
vi.mock("src/api/project/evals-task", () => ({ useGetProjectById: () => ({ data: { name: "Project" } }) }));
vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/FormTextField/FormTextFieldV2", () => ({ default: () => null }));
vi.mock("src/components/FormSelectField", () => ({ FormSelectField: () => null }));
vi.mock("src/sections/develop-detail/AccordianElements", async () => {
  const { default: PropTypes } = await import("prop-types");
  const MockAccordion = ({ children }) => <div>{children}</div>;
  MockAccordion.propTypes = { children: PropTypes.node };
  return {
    Accordion: MockAccordion,
    AccordionSummary: MockAccordion,
    AccordionDetails: MockAccordion,
  };
});
vi.mock("src/components/ComplexFilter/FilterErrorBoundary", () => ({ default: ({ children }) => children }));
vi.mock("../NewTaskDrawer/NewTaskFilterBox", async () => {
  const { useWatch } = await import("react-hook-form");
  return { default: function MockTaskFilterBox({ setValue, control }) {
    const rows = useWatch({ control, name: "filters" });
    return <button type="button" onClick={() => setValue("filters", structuredClone(state.rows))}>
      Use fixture filters ({rows?.length || 0})
    </button>;
  } };
});
vi.mock("../NewTaskDrawer/ScheduledRuns", () => ({ default: () => null }));
vi.mock("../NewTaskDrawer/EvaluationSection", () => ({ default: () => null }));
vi.mock("./TaskLogs", () => ({ default: () => null }));
vi.mock("../TaskLogsView", () => ({ default: () => null }));
vi.mock("src/components/show", () => ({ ShowComponent: ({ condition, children }) => condition ? children : null }));
vi.mock("../../EvaluationDrawer/context/EvaluationContext", () => ({ useEvaluationContext: () => ({ visibleSection: "list" }) }));
vi.mock("../../EvaluationDrawer/EvaluationDrawer", () => ({ default: ({ listComponent }) => listComponent }));
vi.mock("src/sections/develop-detail/Common/ConfiguredEvaluationType/ConfiguredEvaluationType", () => ({ default: () => null }));
vi.mock("src/sections/common/EvalPicker/serializeEvalConfig", () => ({ sanitizeEvalMapping: (value) => value }));
vi.mock("src/sections/evals/store/useEvalStore", () => ({ resetEvalStore: vi.fn() }));
vi.mock("src/sections/projects/LLMTracing/AttributeInventoryControls", () => ({ default: () => null }));
vi.mock("src/sections/projects/LLMTracing/useCursorAttributeInventory", () => ({
  attributeInventoryKey: (value) => value,
  useCursorAttributeInventory: () => ({ filteredAttributes: state.empty, inventoryControlProps: {} }),
}));
vi.mock("../use_task_eval_attribute_inventory", () => ({
  useTaskEvalAttributeInventory: () => ({ sourceColumns: state.empty, attributeFields: state.empty }),
}));
vi.mock("../../EvalPicker", () => ({ EvalPickerDrawer: () => null, serializeEvalConfig: (value) => value }));
vi.mock("src/sections/evals/utils/evalMappingPath", () => ({ mappingChipLabel: (value) => value }));
vi.mock("src/utils/errorUtils", () => ({ getSafeActionErrorMessage: (_error, fallback) => fallback }));
vi.mock("src/theme/palette", () => ({ red: { 500: "#f00" } }));

const ID = "40000000-0000-4000-8000-000000000001";
const row = (property, op, value, extra = {}) => ({
  property, filterConfig: { filterType: "text", filterOp: op, filterValue: value }, ...extra,
});
const wire = (column_id, col_type, filter_op, filter_value, property_id, types) => ({
  column_id, ...(property_id ? { property_id } : {}),
  filter_config: { filter_type: "text", filter_op, filter_value, col_type,
    ...(types ? { attribute_value_types: types } : {}) },
});
const cases = [
  ["legacy positives", [
    row("trace_id", "in", [ID]), row("session_id", "equals", ID),
  ], { trace_id: [ID], session_id: [ID] }, []],
  ["native exclusions", [
    row("trace_id", "not_in", [ID], { apiColType: "SYSTEM_METRIC" }),
    row("session_id", "not_in", [ID]),
  ], {}, [
    wire("trace_id", "SYSTEM_METRIC", "not_in", [ID]),
    wire("session_id", "SYSTEM_METRIC", "not_in", [ID]),
  ]],
  ["raw and mixed sources", [
    row("trace_id", "not_in", [ID], { apiColType: "SYSTEM_METRIC" }),
    row("trace_id", "in", ["001", 1, false], {
      propertyId: "trace_id", apiColType: "SPAN_ATTRIBUTE", registryId: "custom_attribute:trace_id",
      filterConfig: { filterType: "text", filterOp: "in", filterValue: ["001", 1, false], attributeValueTypes: ["string", "number", "boolean"] },
    }),
    row("trace_id", "not_in", ["bad"], { apiColType: "EVAL_METRIC", registryId: "eval:trace_id" }),
  ], {}, [
    wire("trace_id", "SYSTEM_METRIC", "not_in", [ID]),
    wire("trace_id", "SPAN_ATTRIBUTE", "in", ["001", 1, false], "custom_attribute:trace_id", ["string", "number", "boolean"]),
    wire("trace_id", "EVAL_METRIC", "not_in", ["bad"], "eval:trace_id"),
  ]],
];
let client;
beforeEach(() => {
  state.patch.mockReset().mockResolvedValue({ data: { result: { message: "Updated" } } });
  state.post.mockReset().mockRejectedValue(new Error("Unexpected create request"));
  state.get.mockReset().mockImplementation(async (url) => ({ data: { result:
    url === "/task/configured" ? [{ id: state.eval, name: "Quality" }] : { projects: [] },
  } }));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
});
afterEach(() => { cleanup(); client.clear(); });

describe.each([
  ["DetailsEdit", DetailsEdit, "filters", { isEdit: true, isView: true }],
  ["EditTaskDrawerV2", EditTaskDrawerV2, "span_attributes_filters", {}],
])("%s mounted submit payload", (_label, Component, nestedKey, props) => {
  it.each(cases)("submits %s through Update Task and the real confirmation dialog", async (_case, rows, siblings, canonical) => {
    state.rows = rows;
    render(<QueryClientProvider client={client}>
      <Component open selectedRow={{ id: state.task }} taskDetails={{ id: state.task }}
        observeId={state.project} onClose={vi.fn()} refreshGrid={vi.fn()} {...props} />
    </QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Use fixture filters (0)" }));
    expect(await screen.findByRole("button", { name: "Use fixture filters (" + rows.length + ")" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Update Task" }));
    fireEvent.click(await screen.findByText("Edit & Re-run Existing Evals"));
    fireEvent.click(screen.getByRole("button", { name: "Run task" }));
    await waitFor(() => expect(state.patch).toHaveBeenCalledTimes(1));
    expect(state.patch).toHaveBeenCalledWith("/task/update", {
      eval_task_id: state.task, evals: [state.eval], project_id: state.project,
      name: "Mounted task", project: state.project, run_type: "continuous",
      sampling_rate: 100, spans_limit: "10", edit_type: "edit_rerun",
      filters: {
        project_id: state.project,
        date_range: ["2026-09-01T00:00:00.000Z", "2026-09-02T00:00:00.000Z"],
        ...siblings, ...(canonical.length ? { [nestedKey]: canonical } : {}),
      },
    });
    expect(state.post).not.toHaveBeenCalled();
  });
});
