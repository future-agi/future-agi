import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { RuleFilterSection } from "../create-rule-dialog";
import { SESSION_RULE_FILTER_FIELDS } from "../../constants";

const traceFilterPanelPropsMock = vi.hoisted(() => vi.fn());
const projectDetailsMock = vi.hoisted(() => vi.fn(() => ({ data: undefined })));

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn() },
  endpoints: { dashboard: { metrics: "/dashboard/metrics/" } },
}));

vi.mock("src/hooks/useDashboards", () => ({
  PROPERTY_CATALOG_REQUEST_TIMEOUT_MS: 9_000,
  isPropertyCatalogNotReadyError: (error) =>
    error?.response?.status === 503 &&
    error?.response?.data?.code === "property_catalog_not_ready",
  usePropertyCatalog: () => ({
    error: {
      response: {
        status: 503,
        data: { code: "property_catalog_not_ready" },
      },
    },
    legacyFallbackRequired: true,
    metrics: [],
  }),
}));

vi.mock("src/api/project/project-detail", () => ({
  useGetProjectDetails: (...args) => projectDetailsMock(...args),
}));

vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: (props) => {
    traceFilterPanelPropsMock(props);
    if (!props.open) return null;
    return (
      <div data-testid="simulation-properties">
        {(props.properties || []).map((property) => property.name).join("|")}
      </div>
    );
  },
  buildTraceFilterProperties: (metrics) =>
    metrics.map((metric) => ({
      id: metric.name,
      name: metric.display_name,
      category: metric.category === "eval_metric" ? "eval" : "system",
      type: "number",
    })),
}));

function renderRuleFilters({
  sourceType = "call_execution",
  scope = { project_id: "agent-1" },
  queue = {},
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RuleFilterSection
        sourceType={sourceType}
        filters={[]}
        setFilters={vi.fn()}
        scope={scope}
        setScope={vi.fn()}
        queue={queue}
        onInteraction={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

function mockTwoLegacyEvalPages() {
  axios.get.mockImplementation((_url, { params }) =>
    Promise.resolve({
      data: {
        result: {
          metrics: [
            {
              name: params.page === 1 ? "eval-1" : "eval-2",
              display_name: params.page === 1 ? "First Eval" : "Second Eval",
              category: "eval_metric",
            },
          ],
          page: params.page,
          page_size: 200,
          total: 201,
          has_more: params.page === 1,
        },
      },
    }),
  );
}

describe("simulation automation metric pagination", () => {
  beforeEach(() => {
    axios.get.mockReset();
    traceFilterPanelPropsMock.mockClear();
    projectDetailsMock.mockClear();
  });

  it("delegates eval-property cursor continuation to the shared property picker", async () => {
    mockTwoLegacyEvalPages();

    renderRuleFilters();

    await waitFor(() => {
      expect(axios.get).toHaveBeenCalledTimes(1);
      expect(
        traceFilterPanelPropsMock.mock.calls.at(-1)[0].properties,
      ).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ name: "First Eval" }),
        ]),
      );
    });
    expect(axios.get).toHaveBeenCalledWith("/dashboard/metrics/", {
      params: {
        agent_definition_id: "agent-1",
        exclude_custom_attributes: true,
        page: 1,
        page_size: 200,
      },
      signal: expect.anything(),
      timeout: 9_000,
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Open rule filters" }),
    );
    const propertyList = await screen.findByTestId("simulation-properties");
    expect(propertyList).toHaveTextContent("First Eval");
    expect(propertyList).not.toHaveTextContent("Second Eval");
    expect(axios.get).toHaveBeenCalledTimes(1);
    const pickerProps = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
    expect(pickerProps.hasNextCatalogPage).toBe(true);
    expect(pickerProps.catalogContinuationKey).toBe("legacy-page:2");
    expect(pickerProps.catalogNextPageError).toBe(false);
    expect(
      screen.queryByRole("button", { name: "Load more eval properties" }),
    ).not.toBeInTheDocument();

    await act(async () => pickerProps.loadNextCatalogPage());

    await waitFor(() => expect(propertyList).toHaveTextContent("Second Eval"));
    expect(axios.get).toHaveBeenLastCalledWith("/dashboard/metrics/", {
      params: expect.objectContaining({ page: 2, page_size: 200 }),
      signal: expect.anything(),
      timeout: 9_000,
    });
    expect(
      screen.queryByRole("button", { name: "Load more eval properties" }),
    ).not.toBeInTheDocument();
  });
});

describe("session automation filter adapter", () => {
  it("retains static session fields without replacing dynamic catalog properties", () => {
    traceFilterPanelPropsMock.mockClear();

    renderRuleFilters({
      sourceType: "trace_session",
      scope: {},
      queue: { project: { id: "session-project" } },
    });

    const props = traceFilterPanelPropsMock.mock.calls.at(-1)[0];
    expect(props).toMatchObject({
      projectId: "session-project",
      source: "sessions",
      attributeSource: "spans",
      filterFields: SESSION_RULE_FILTER_FIELDS,
    });
    expect(props).not.toHaveProperty("properties");
    expect(props).not.toHaveProperty("categories");
    expect(projectDetailsMock).toHaveBeenCalledWith("session-project", false);
  });
});
