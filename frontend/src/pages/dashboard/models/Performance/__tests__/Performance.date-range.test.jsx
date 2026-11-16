import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import Performance from "../Performance";

vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: {
    post: vi.fn(async () => ({ data: { result: [], isNext: false } })),
  },
}));
vi.mock("src/api/model/model", () => ({
  useGetModelDetail: () => ({
    data: { isMetricAdded: true, isDatasetAdded: true },
  }),
}));
vi.mock("src/api/model/metric", () => ({
  useGetAllCustomMetrics: () => ({ data: [] }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn(), closeSnackbar: vi.fn() }),
}));
vi.mock("../PerformanceTable", () => ({ default: () => null }));
vi.mock("../PerformanceGraph", () => ({ default: () => null }));
vi.mock("../PerformanceNoData", () => ({ default: () => null }));
vi.mock("../../ConfigureDatasetModal", () => ({ default: () => null }));
vi.mock("../../ConfigureMetricModal", () => ({ default: () => null }));
vi.mock("../PerformanceSidebar/PerformanceSidebar", () => ({
  default: () => null,
}));
vi.mock("../PerformanceTableHeader", () => ({ default: () => null }));
vi.mock("../PerformanceTagDistribution", () => ({ default: () => null }));
vi.mock("../PerformanceGraphDatapoints", () => ({ default: () => null }));
vi.mock("../SaveReport", () => ({ default: () => null }));

// Real Performance, React Query, picker, inputs and request builder. Only the
// transport and unrelated panels are stubbed. Reverting the boundary opt-in
// sends ISO/exclusive bounds here, not the backend's accepted inclusive pair.
describe("Performance custom date request contract", () => {
  it("posts inclusive local timestamps and restores the applied range on reopen", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    axios.post.mockClear();
    const { unmount } = render(
      <QueryClientProvider client={client}>
        <MemoryRouter
          initialEntries={[
            {
              pathname: "/models/model-1/performance",
              state: {
                report: {
                  startDate: "2026-11-01 00:00:00",
                  endDate: "2026-11-05 00:00:00",
                  datasets: [
                    {
                      metricId: "metric-1",
                      environment: "production",
                      version: "v1",
                      filters: [],
                    },
                  ],
                  filters: [],
                  breakdown: [],
                },
              },
            },
          ]}
        >
          <Routes>
            <Route path="/models/:id/performance" element={<Performance />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const graphUrl = endpoints.performance.graphData("model-1");
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(graphUrl, expect.any(Object)),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Custom", exact: true }),
    );
    for (const label of ["Start Date", "End Date"]) {
      fireEvent.change(screen.getByLabelText(label), {
        target: { value: "2026-11-02" },
      });
    }
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith(graphUrl, {
        start_date: "2026-11-02 00:00:00",
        end_date: "2026-11-02 00:00:00",
        agg_by: "daily",
        datasets: [
          {
            metric_id: "metric-1",
            environment: "production",
            version: "v1",
            filters: [],
          },
        ],
        filters: [],
        breakdown: [],
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "02/11/2026 - 02/11/2026" }),
    );
    expect(screen.getByLabelText("Start Date")).toHaveValue("2026-11-02");
    expect(screen.getByLabelText("End Date")).toHaveValue("2026-11-02");
    expect(screen.getByRole("button", { name: "Done" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(
      axios.post.mock.calls.filter(([url]) => url === graphUrl).at(-1)[1]
        .end_date,
    ).toBe("2026-11-02 00:00:00");
    unmount();
    client.clear();
  });
});
