import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "src/utils/test-utils";
import ObserveToolbar from "../ObserveToolbar";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";
import { toBackendFilters } from "src/sections/projects/LLMTracing/common";
import { buildUsersRequestFilters } from "src/sections/projects/UsersView/common";

const traceFilterPanelPropsMock = vi.hoisted(() => vi.fn());

vi.mock("../TraceFilterPanel", () => ({
  default: (props) => {
    traceFilterPanelPropsMock(props);
    return null;
  },
}));

vi.mock("../DisplayPanel", () => ({ default: () => null }));
vi.mock("../BulkActionsBar", () => ({ default: () => null }));
vi.mock("../tabStore", () => ({
  useTabStoreShallow: (selector) => selector({ openCreateModal: vi.fn() }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));

const renderToolbar = (props = {}) =>
  render(
    <ObserveToolbar
      inline
      tab="trace"
      isFilterOpen={false}
      onFilterToggle={vi.fn()}
      onApplyExtraFilters={vi.fn()}
      {...props}
    />,
  );

describe("ObserveToolbar status filter registry", () => {
  it.each(["trace_id", "span_id", "session", "annotator"].flatMap((key) =>
    ["NORMAL", "SYSTEM_METRIC", "ANNOTATION", "EVAL_METRIC"].map((colType) => [key, colType]),
  ))("retains explicit %s source %s during edit/apply", (key, colType) => {
    const annotatorControl = key === "annotator" && colType !== "EVAL_METRIC";
    const graphFilters = [{ column_id: key, display_name: key, filter_config: { col_type: colType, filter_type: annotatorControl ? "annotator" : "text", filter_op: "equals", filter_value: ["native-value"] } }];
    const onApplyExtraFilters = vi.fn();
    renderToolbar({ graphFilters, onApplyExtraFilters });
    const panel = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
    act(() => panel.onApply(panel.currentFilters));
    expect(serializeFilterListForApi(onApplyExtraFilters.mock.calls.at(-1)[0])).toEqual(serializeFilterListForApi(graphFilters));
  });

  it.each(["trace_id", "span_id", "session"])("preserves source-less legacy native %s", (key) => {
    const graphFilters = [{ column_id: key, filter_config: { filter_type: "text", filter_op: "not_in", filter_value: ["native-a", "native-b"] } }];
    const onApplyExtraFilters = vi.fn();
    renderToolbar({ graphFilters, onApplyExtraFilters });
    const panel = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
    act(() => panel.onApply(panel.currentFilters));
    expect(serializeFilterListForApi(onApplyExtraFilters.mock.calls.at(-1)[0])).toEqual(graphFilters);
  });

  it("keeps source-less legacy annotator routing", () => {
    const onApplyExtraFilters = vi.fn();
    renderToolbar({ graphFilters: [{ column_id: "annotator", filter_config: { filter_type: "annotator", filter_op: "equals", filter_value: ["reviewer"] } }], onApplyExtraFilters });
    const panel = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
    act(() => panel.onApply(panel.currentFilters));
    expect(onApplyExtraFilters.mock.calls.at(-1)[0][0].filter_config).toEqual({ col_type: "SYSTEM_METRIC", filter_type: "annotator", filter_op: "equals", filter_value: "reviewer" });
  });

  it("honors nested raw source over conflicting root metadata", () => {
    renderToolbar({ graphFilters: [{ column_id: "annotator", col_type: "SYSTEM_METRIC", filter_config: { col_type: "attribute", filter_type: "number", filter_op: "greater_than", filter_value: 2 } }] });
    const row = traceFilterPanelPropsMock.mock.calls.at(-1)[0].currentFilters[0];
    expect(row).toMatchObject({ apiColType: "SPAN_ATTRIBUTE", fieldCategory: "attribute", fieldType: "number", operator: "greater_than" });
  });

  it.each([
    ["Trace", { mode: "traces", tab: "trace" }, toBackendFilters],
    ["Span", { mode: "traces", tab: "spans" }, toBackendFilters],
    ["Session", { mode: "sessions" }, toBackendFilters],
    ["Users", { mode: "users" }, buildUsersRequestFilters],
  ].flatMap(([mode, props, serialize]) =>
    ["trace_id", "span_id", "session", "annotator"].map((key) => [mode, key, props, serialize]),
  ))("source audit: %s keeps explicit raw %s through hydration/apply", (_mode, key, props, serialize) => {
    const graphFilters = [{
      column_id: key, property_id: `custom_attribute:${key}`,
      filter_config: {
        col_type: "SPAN_ATTRIBUTE", filter_type: "text", filter_op: "not_in",
        filter_value: ["001", 1, false], attribute_value_types: ["string", "number", "boolean"],
      },
    }];
    const onApplyExtraFilters = vi.fn();
    renderToolbar({ ...props, graphFilters, onApplyExtraFilters });
    const panel = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
    act(() => panel.onApply(panel.currentFilters));
    expect(serialize(onApplyExtraFilters.mock.calls.at(-1)[0])).toEqual(serializeFilterListForApi(graphFilters));
  });

  beforeEach(() => {
    traceFilterPanelPropsMock.mockClear();
  });

  it.each([
    ["Trace", { mode: "traces", tab: "trace" }, toBackendFilters],
    ["Span", { mode: "traces", tab: "spans" }, toBackendFilters],
    ["Session", { mode: "sessions" }, toBackendFilters],
    ["Users", { mode: "users" }, buildUsersRequestFilters],
  ])(
    "preserves exact typed AND rows through %s hydration and serialization",
    (_label, props, serialize) => {
      const leaf = (
        col_type,
        column_id,
        filter_type,
        filter_op,
        filter_value,
        valueTypes,
      ) => ({
        column_id,
        property_id: `${col_type}:${column_id}`,
        filter_config: {
          col_type,
          filter_type,
          filter_op,
          filter_value,
          ...(valueTypes && { attribute_value_types: valueTypes }),
        },
      });
      const graphFilters = [
        leaf("SPAN_ATTRIBUTE", "attempt", "number", "between", [0, 10]),
        leaf("SPAN_ATTRIBUTE", "attempt", "number", "between", [5, 20]),
        leaf(
          "SPAN_ATTRIBUTE",
          "mixed",
          "text",
          "in",
          [" leading,tail ", 0, false],
          ["string", "number", "boolean"],
        ),
        leaf(
          "SPAN_ATTRIBUTE",
          "mixed",
          "text",
          "in",
          ["0", true],
          ["string", "boolean"],
        ),
        leaf(
          "SPAN_ATTRIBUTE",
          "message",
          "text",
          "contains",
          " exact, scalar ",
        ),
        leaf(
          "SPAN_ATTRIBUTE",
          "message",
          "text",
          "in",
          [` ${"x".repeat(600)},end `],
          ["string"],
        ),
        leaf("SPAN_ATTRIBUTE", "flag", "boolean", "equals", false),
        leaf("SPAN_ATTRIBUTE", "flag", "boolean", "not_equals", true),
        leaf("ANNOTATION", "review", "categorical", "in", [
          "needs,review",
          " approved ",
        ]),
        leaf("ANNOTATION", "review", "categorical", "in", [
          " approved ",
          "ready",
        ]),
        leaf("EVAL_METRIC", "quality", "categorical", "in", [
          "pass,with-note",
          " pass ",
        ]),
        leaf("EVAL_METRIC", "quality", "categorical", "in", [
          " pass ",
          "retry",
        ]),
        leaf("EVAL_METRIC", "score", "number", "between", [0, 10]),
        leaf("EVAL_METRIC", "score", "number", "between", [5, 20]),
      ];
      const before = structuredClone(graphFilters);
      const onApplyExtraFilters = vi.fn();
      renderToolbar({ ...props, graphFilters, onApplyExtraFilters });
      const panel = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
      expect(panel.currentFilters).toHaveLength(graphFilters.length);

      // The panel is mocked; exercise the real toolbar hydration, onApply
      // callback, canonical row builder and the actual list serializer.
      act(() => panel.onApply(panel.currentFilters));
      const applied = onApplyExtraFilters.mock.calls.at(-1)[0];
      expect(serialize(applied)).toEqual(
        serializeFilterListForApi(graphFilters),
      );
      expect(graphFilters).toEqual(before);
    },
  );

  it("uses voice-call fields when the rendered trace grid is a simulator call log", () => {
    renderToolbar({ isSimulator: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        tab: "voiceCalls",
        isSimulator: true,
      }),
    );
  });

  it.each(["trace", "spans"])(
    "keeps the %s registry for ordinary tracing grids",
    (tab) => {
      renderToolbar({ tab, isSimulator: false });

      expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ tab, isSimulator: false }),
      );
    },
  );

  it("forwards an explicit project scope for routes without observeId", () => {
    renderToolbar({ projectId: "project-from-query-string" });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: "project-from-query-string" }),
    );
  });

  it("forwards workspace property scope for cross-project user detail", () => {
    renderToolbar({ allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });

  it("keeps workspace Users values session-scoped and attributes trace-scoped", () => {
    renderToolbar({ mode: "users", allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source: "sessions",
        propertyNamespace: "users",
        attributeSource: "traces",
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });

  it("keeps user-detail Sessions values session-scoped and attributes trace-scoped", () => {
    renderToolbar({ mode: "sessions", allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source: "sessions",
        propertyNamespace: "sessions",
        attributeSource: "traces",
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });

  it("keeps user-detail Trace values, namespace, and attributes trace-scoped", () => {
    renderToolbar({ mode: "traces", allowWorkspaceScope: true });

    expect(traceFilterPanelPropsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        source: "traces",
        propertyNamespace: "traces",
        attributeSource: undefined,
        projectId: undefined,
        allowWorkspaceScope: true,
      }),
    );
  });
});
