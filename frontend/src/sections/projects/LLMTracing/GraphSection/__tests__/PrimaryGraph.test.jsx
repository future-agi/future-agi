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
});
