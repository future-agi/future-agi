import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import AddDataset from "./add-dataset";

vi.mock("src/utils/axios", () => ({
  default: {
    get: vi.fn(),
  },
  endpoints: {
    develop: {
      getDatasetList: () => "/develop/datasets/",
    },
    project: {
      getObservationSpanField:
        "/tracer/observation-span/get_observation_span_fields/",
    },
  },
}));

vi.mock("../../iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify">{icon}</span>,
}));

vi.mock("./AddExistingDataset", () => ({
  default: ({ observationFields }) => (
    <div data-testid="existing-fields">
      {observationFields.map((field) => field.name).join(",")}
    </div>
  ),
}));

vi.mock("./AddNewDataset", () => ({
  default: ({ observationFields }) => (
    <div data-testid="new-fields">
      {observationFields.map((field) => field.name).join(",")}
    </div>
  ),
}));

const theme = createTheme();

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: Infinity,
      },
    },
  });
}

function renderAddDataset(
  route,
  queryClient,
  routePath = "/dashboard/observe/:observeId/llm-tracing",
) {
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider theme={theme}>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route
              path={routePath}
              element={<AddDataset actionToDataset handleClose={vi.fn()} />}
            />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("AddDataset observation fields cache scope", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("fetches observation fields per observe project instead of reusing another project's cached fields", async () => {
    const queryClient = createQueryClient();
    const observationFieldResponses = [
      [{ name: "input", type: "text" }],
      [{ name: "output", type: "text" }],
    ];
    let observationFieldFetches = 0;

    axios.get.mockImplementation((url) => {
      if (url === endpoints.develop.getDatasetList()) {
        return Promise.resolve({ data: { result: { datasets: [] } } });
      }

      if (url === endpoints.project.getObservationSpanField) {
        const result = observationFieldResponses[observationFieldFetches] || [];
        observationFieldFetches += 1;
        return Promise.resolve({ data: { result } });
      }

      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });

    const firstRender = renderAddDataset(
      "/dashboard/observe/project-a/llm-tracing",
      queryClient,
    );

    await waitFor(() =>
      expect(screen.getByTestId("existing-fields")).toHaveTextContent("input"),
    );
    expect(screen.getByTestId("existing-fields")).not.toHaveTextContent(
      "output",
    );

    firstRender.unmount();

    renderAddDataset("/dashboard/observe/project-b/llm-tracing", queryClient);

    await waitFor(() =>
      expect(screen.getByTestId("existing-fields")).toHaveTextContent("output"),
    );
    expect(screen.getByTestId("existing-fields")).not.toHaveTextContent(
      "input",
    );
    expect(observationFieldFetches).toBe(2);
    expect(
      queryClient.getQueryData(["observationFields", "project-a"]),
    ).toEqual({ result: [{ name: "input", type: "text" }] });
    expect(
      queryClient.getQueryData(["observationFields", "project-b"]),
    ).toEqual({ result: [{ name: "output", type: "text" }] });
  });

  it("uses the projectId route param when observeId is not present", async () => {
    const queryClient = createQueryClient();
    let observationFieldFetches = 0;

    axios.get.mockImplementation((url) => {
      if (url === endpoints.develop.getDatasetList()) {
        return Promise.resolve({ data: { result: { datasets: [] } } });
      }

      if (url === endpoints.project.getObservationSpanField) {
        observationFieldFetches += 1;
        return Promise.resolve({
          data: { result: [{ name: "status", type: "text" }] },
        });
      }

      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });

    renderAddDataset(
      "/dashboard/projects/project-c/datasets",
      queryClient,
      "/dashboard/projects/:projectId/datasets",
    );

    await waitFor(() =>
      expect(screen.getByTestId("existing-fields")).toHaveTextContent("status"),
    );
    expect(observationFieldFetches).toBe(1);
    expect(
      queryClient.getQueryData(["observationFields", "project-c"]),
    ).toEqual({ result: [{ name: "status", type: "text" }] });
  });

  it("keeps a deterministic unscoped cache key when no project route param exists", async () => {
    const queryClient = createQueryClient();
    let observationFieldFetches = 0;

    axios.get.mockImplementation((url) => {
      if (url === endpoints.develop.getDatasetList()) {
        return Promise.resolve({ data: { result: { datasets: [] } } });
      }

      if (url === endpoints.project.getObservationSpanField) {
        observationFieldFetches += 1;
        return Promise.resolve({
          data: { result: [{ name: "model", type: "text" }] },
        });
      }

      return Promise.reject(new Error(`Unexpected URL: ${url}`));
    });

    renderAddDataset(
      "/legacy-dataset-drawer",
      queryClient,
      "/legacy-dataset-drawer",
    );

    await waitFor(() =>
      expect(screen.getByTestId("existing-fields")).toHaveTextContent("model"),
    );
    expect(observationFieldFetches).toBe(1);
    expect(queryClient.getQueryData(["observationFields", "unscoped"])).toEqual(
      { result: [{ name: "model", type: "text" }] },
    );
  });
});
