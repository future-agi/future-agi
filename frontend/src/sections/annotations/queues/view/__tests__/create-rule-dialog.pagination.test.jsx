import React from "react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { RuleFilterSection } from "../create-rule-dialog";

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

vi.mock("src/sections/projects/LLMTracing/TraceFilterPanel", () => ({
  default: ({ properties }) => (
    <div data-testid="simulation-properties">
      {properties.map((property) => property.name).join("|")}
    </div>
  ),
  buildTraceFilterProperties: (metrics) =>
    metrics.map((metric) => ({
      id: metric.name,
      name: metric.display_name,
      category: metric.category === "eval_metric" ? "eval" : "system",
      type: "number",
    })),
}));

function renderSimulationRuleFilters() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RuleFilterSection
        sourceType="call_execution"
        filters={[]}
        setFilters={vi.fn()}
        scope={{ project_id: "agent-1" }}
        setScope={vi.fn()}
        queue={{}}
        onInteraction={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe("simulation automation metric pagination", () => {
  it("keeps additional eval properties behind an explicit continuation", async () => {
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

    renderSimulationRuleFilters();

    expect(await screen.findByText(/First Eval/)).toBeVisible();
    expect(screen.queryByText(/Second Eval/)).not.toBeInTheDocument();
    expect(axios.get).toHaveBeenCalledTimes(1);
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

    fireEvent.click(await screen.findByText("Load more eval properties"));

    expect(await screen.findByText(/Second Eval/)).toBeVisible();
    expect(axios.get).toHaveBeenLastCalledWith("/dashboard/metrics/", {
      params: expect.objectContaining({ page: 2, page_size: 200 }),
      signal: expect.anything(),
      timeout: 9_000,
    });
    expect(
      screen.queryByText("Load more eval properties"),
    ).not.toBeInTheDocument();
  });
});
