import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "src/utils/test-utils";
import CreateRuleDialog from "../create-rule-dialog";

const harness = vi.hoisted(() => ({
  createRule: vi.fn(),
}));

const mixedPanelFilters = [
  {
    field: "status",
    fieldName: "Status",
    fieldCategory: "system",
    fieldType: "text",
    apiColType: "SYSTEM_METRIC",
    operator: "equals",
    value: "OK",
  },
  {
    field: "quality_eval",
    registryId: "eval:quality-eval",
    fieldName: "Quality Eval",
    fieldCategory: "eval",
    fieldType: "number",
    apiColType: "EVAL_METRIC",
    operator: "greater_than",
    value: 0.8,
  },
  {
    field: "quality_label",
    registryId: "annotation:quality-label",
    fieldName: "Quality Label",
    fieldCategory: "annotation",
    fieldType: "categorical",
    apiColType: "ANNOTATION",
    operator: "in",
    value: ["approved"],
  },
  {
    field: "customer.tier",
    registryId: "custom_attribute:customer.tier",
    fieldName: "Customer Tier",
    fieldCategory: "attribute",
    fieldType: "text",
    apiColType: "SPAN_ATTRIBUTE",
    operator: "is_not_null",
  },
];

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  extractErrorMessage: (_error, fallback) => fallback,
  useCreateAutomationRule: () => ({
    mutate: harness.createRule,
    isPending: false,
  }),
}));

vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: () => ({ data: { source: "observe" } }),
}));

vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: ({ onApply }) => (
    <button type="button" onClick={() => onApply(mixedPanelFilters)}>
      Apply mixed rule filters
    </button>
  ),
  buildTraceFilterProperties: () => [],
}));

vi.mock("src/sections/projects/LLMTracing/FilterChips", () => ({
  default: () => null,
}));

vi.mock("src/components/svg-color", () => ({ default: () => null }));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn().mockResolvedValue({
      data: {
        result: {
          projects: [{ id: "project-1", name: "Project One" }],
        },
      },
    }),
  },
  endpoints: {
    agentDefinitions: { list: "/agent-definitions/" },
    dashboard: { metrics: "/dashboard/metrics/" },
    project: { listProjects: () => "/projects/" },
  },
}));

vi.mock("src/hooks/useDashboards", () => ({
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS: 9_000,
  isPropertyCatalogNotReadyError: () => false,
  usePropertyCatalog: () => ({
    error: null,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isError: false,
    isFetchingNextPage: false,
    metrics: [],
  }),
}));

vi.mock("src/api/develop/develop-detail", () => ({
  getDatasetQueryOptions: () => ({
    queryKey: ["dataset-rule-test"],
    queryFn: async () => ({ data: { result: {} } }),
    enabled: false,
  }),
}));

vi.mock(
  "src/sections/develop-detail/DataTab/DevelopFilters/DevelopFilterBox",
  () => ({
    DEVELOP_FILTER_CATEGORIES: [],
    DatasetColumnValuePicker: () => null,
    buildProperties: () => [],
    panelFilterToStore: (filter) => filter,
    storeFilterToPanel: (filter) => filter,
  }),
);

describe("Create Automation Rule mixed catalog submission", () => {
  beforeEach(() => {
    harness.createRule.mockReset();
  });

  it("submits all property families and retains is_not_null without a value", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <CreateRuleDialog
          open
          onClose={vi.fn()}
          queueId="queue-1"
          queue={{
            id: "queue-1",
            is_default: false,
            project: { id: "project-1" },
          }}
        />
      </QueryClientProvider>,
    );

    await user.type(
      screen.getByTestId("automation-rule-name-input"),
      "Mixed property rule",
    );
    await user.click(
      screen.getByRole("button", { name: "Apply mixed rule filters" }),
    );
    await user.click(screen.getByTestId("automation-rule-create-submit"));

    await waitFor(() => {
      expect(harness.createRule).toHaveBeenCalledWith(
        {
          queueId: "queue-1",
          name: "Mixed property rule",
          source_type: "trace",
          trigger_frequency: "manual",
          conditions: {
            operator: "and",
            scope: {
              project_id: "project-1",
              is_voice_call: false,
              remove_simulation_calls: false,
            },
            filter: [
              {
                column_id: "status",
                display_name: "Status",
                filter_config: {
                  filter_type: "text",
                  filter_op: "equals",
                  filter_value: "OK",
                  col_type: "SYSTEM_METRIC",
                },
              },
              {
                column_id: "quality_eval",
                property_id: "eval:quality-eval",
                display_name: "Quality Eval",
                filter_config: {
                  filter_type: "number",
                  filter_op: "greater_than",
                  filter_value: 0.8,
                  col_type: "EVAL_METRIC",
                },
              },
              {
                column_id: "quality_label",
                property_id: "annotation:quality-label",
                display_name: "Quality Label",
                filter_config: {
                  filter_type: "categorical",
                  filter_op: "in",
                  filter_value: ["approved"],
                  col_type: "ANNOTATION",
                },
              },
              {
                column_id: "customer.tier",
                property_id: "custom_attribute:customer.tier",
                display_name: "Customer Tier",
                filter_config: {
                  filter_type: "text",
                  filter_op: "is_not_null",
                  filter_value: null,
                  col_type: "SPAN_ATTRIBUTE",
                },
              },
            ],
          },
          enabled: true,
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });
  });
});
