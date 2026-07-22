import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import PrimaryGraph from "../PrimaryGraph";

vi.mock("react-apexcharts", () => ({
  default: () => <div data-testid="apex-chart" />,
}));

vi.mock("src/components/custom-datepicker/DatePicker", () => ({
  default: () => null,
}));

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
  endpoints: {
    dashboard: {
      metrics: "/dashboard/metrics/",
    },
    project: {
      getTraceGraphData: () => "/tracer/trace/get_graph_methods/",
      getSpanGraphData: () => "/tracer/observation-span/get_graph_methods/",
    },
  },
}));

function renderWithQueryClient(ui) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("PrimaryGraph", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    axios.get.mockResolvedValue({
      data: {
        result: {
          metrics: [
            {
              category: "system_metric",
              name: "latency",
              displayName: "Latency",
              type: "number",
            },
          ],
        },
      },
    });
    axios.post.mockResolvedValue({
      data: {
        result: {
          metric_name: "latency",
          data: [],
        },
      },
    });
  });

  it("uses observeIdOverride as the graph project id", async () => {
    renderWithQueryClient(
      <PrimaryGraph observeIdOverride="project-override" />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    expect(axios.post).toHaveBeenCalledWith(
      "/tracer/trace/get_graph_methods/",
      expect.objectContaining({
        project_id: "project-override",
      }),
    );
  });

  it("uses the supplied graph endpoint for span graphs", async () => {
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        graphEndpoint="/tracer/observation-span/get_graph_methods/"
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    expect(axios.post).toHaveBeenCalledWith(
      "/tracer/observation-span/get_graph_methods/",
      expect.objectContaining({
        project_id: "project-override",
      }),
    );
  });

  const statusFilter = {
    column_id: "status",
    filter_config: {
      col_type: "NORMAL",
      filter_type: "text",
      filter_op: "equals",
      filter_value: "SUCCESS",
    },
  };

  const metricFilter = {
    id: "fe-react-key",
    column_id: "latency",
    filter_config: {
      col_type: "SYSTEM_METRIC",
      filter_type: "number",
      filter_op: "greater_than",
      filter_value: 2,
    },
  };

  const postedFilters = () => axios.post.mock.calls.at(-1)[1].filters;

  it("keeps non-date filters when extraFilters is omitted (users/sessions)", async () => {
    // Regression guard for the round-1 review bug: UsersView and
    // SessionsView render PrimaryGraph WITHOUT extraFilters, and their
    // graph must receive the same chip filters as their table.
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        filters={[statusFilter]}
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    expect(postedFilters()).toEqual([statusFilter]);
  });

  it("strips col-level filters when extraFilters is passed, even empty (trace/span)", async () => {
    // Regression guard for the round-2 review bug: the mode gate must be
    // prop PRESENCE — an empty toolbar filter list is still trace mode.
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        filters={[statusFilter]}
        extraFilters={[]}
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    expect(postedFilters()).toEqual([]);
  });

  it("forwards toolbar extraFilters and strips the FE-only id (trace/span)", async () => {
    renderWithQueryClient(
      <PrimaryGraph
        observeIdOverride="project-override"
        filters={[statusFilter]}
        extraFilters={[metricFilter]}
      />,
    );

    await waitFor(() => expect(axios.post).toHaveBeenCalled());

    const { id: _id, ...metricFilterWithoutId } = metricFilter;
    expect(postedFilters()).toEqual([metricFilterWithoutId]);
  });
});
