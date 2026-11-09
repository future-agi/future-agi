/* eslint-disable react/prop-types */
// L3: dev's Data page inside the launch routes. The classic wrapper renders
// the Data view itself (no <Outlet />), so none of the DSX child routes of
// experiment/:experimentId (index redirect, RunResultsView, matrix, paths,
// summary, traces) can mount, and every sub-path lands on /data.
import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";
import { madeRealSurfaceIds } from "src/launch/madeReal";

const summaryGet = vi.hoisted(() => vi.fn());
vi.mock("src/utils/axios", async (original) => ({
  ...(await original()),
  default: { get: summaryGet },
}));
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentSummaryTable",
  () => ({
    default: ({ rows, columns, evalsList }) => (
      <div data-testid="classic-summary-table">
        {JSON.stringify({
          rows,
          columns: columns.map((c) => c.field),
          legacyToolbar: evalsList !== undefined,
        })}
      </div>
    ),
  }),
);
vi.mock(
  "src/sections/experiment-detail/ExperimentSummary/ExperimentEvaluationChart",
  () => ({
    default: ({ col }) => (
      <div data-testid="classic-eval-chart">{col.name}</div>
    ),
  }),
);
afterEach(() => setLaunchOverrideForTest(null));

const experimentDetail = vi.hoisted(() => ({
  value: undefined,
  error: null,
  calls: [],
}));
const dsxChildMounts = vi.hoisted(() => ({ count: 0 }));

vi.mock("src/hooks/useGetExperimentDetails", () => ({
  useGetExperimentDetails: (id) => {
    experimentDetail.calls.push(id);
    if (id && experimentDetail.error) {
      return { data: undefined, isError: true, error: experimentDetail.error };
    }
    return { data: id ? experimentDetail.value : undefined, isError: false };
  },
}));
vi.mock(
  "src/components/custom-audio/context-provider/AudioPlaybackContext",
  () => ({
    AudioPlaybackProvider: ({ children }) => <>{children}</>,
  }),
);
vi.mock("../ExperimentRow", () => ({
  default: () => <div data-testid="classic-row" />,
}));
vi.mock("../ExperimentBar", () => ({
  default: () => <div data-testid="classic-bar" />,
}));
vi.mock("../ExperimentData/ExperimentDataView", () => ({
  default: () => <div data-testid="classic-data-view" />,
}));

import ExperimentWrapperView from "../ExperimentWrapperView";
import { classicDataPath, classicLandingSearch } from "../classicRoutes";

const DsxChild = ({ name }) => {
  dsxChildMounts.count += 1;
  return <div data-testid={`dsx-child-${name}`} />;
};

let lastLocation = null;
const LocationProbe = () => {
  lastLocation = useLocation();
  return null;
};

// The launch's route shape (routes/sections/dashboard.jsx): the DSX
// children under the experiment route.
const renderAt = (path) =>
  render(
    <HelmetProvider>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <MemoryRouter initialEntries={[path]}>
          <LocationProbe />
          <Routes>
            <Route
              path="/dashboard/develop/experiment/:experimentId"
              element={<ExperimentWrapperView />}
            >
              <Route index element={<DsxChild name="index-redirect" />} />
              <Route path="data" element={<DsxChild name="run-results" />} />
              <Route path="matrix" element={<DsxChild name="matrix" />} />
              <Route path="paths" element={<DsxChild name="paths" />} />
              <Route path="summary" element={<DsxChild name="summary" />} />
              <Route path="traces" element={<DsxChild name="traces" />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );

describe("L3 classic experiment wrapper", () => {
  beforeEach(() => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: madeRealSurfaceIds(),
    });
    summaryGet.mockReset();
    summaryGet.mockResolvedValue({
      data: {
        result: {
          metadata: { is_winner_chosen: true },
          table_data: [
            { experiment_dataset_name: "Prompt A", quality: 0.9, rank: 1 },
          ],
          column_config: [{ id: "quality", name: "quality" }],
        },
      },
    });
    experimentDetail.value = {
      id: "e1",
      name: "Exp One",
      dataset_id: "ds1",
    };
    experimentDetail.error = null;
    experimentDetail.calls = [];
    dsxChildMounts.count = 0;
    lastLocation = null;
  });

  it("renders dev's row, bar and Data view and no DSX child route on /data", async () => {
    renderAt("/dashboard/develop/experiment/e1/data?datasetId=ds1");
    expect(await screen.findByTestId("classic-data-view")).toBeTruthy();
    expect(screen.getByTestId("classic-row")).toBeTruthy();
    expect(screen.getByTestId("classic-bar")).toBeTruthy();
    expect(dsxChildMounts.count).toBe(0);
    expect(lastLocation.pathname).toBe("/dashboard/develop/experiment/e1/data");
    expect(lastLocation.search).toBe("?datasetId=ds1");
  });

  // J09 REVISED / 767: Summary gets its own visible unavailable state, not a redirect.
  it.each(["", "/matrix", "/paths", "/traces"])(
    "sends the sub-path '%s' to /data, keeping the query",
    async (sub) => {
      renderAt(`/dashboard/develop/experiment/e1${sub}?datasetId=ds1&x=1`);
      expect(await screen.findByTestId("classic-data-view")).toBeTruthy();
      expect(lastLocation.pathname).toBe(
        "/dashboard/develop/experiment/e1/data",
      );
      expect(new URLSearchParams(lastLocation.search).get("x")).toBe("1");
      expect(dsxChildMounts.count).toBe(0);
    },
  );

  it("keeps Summary visible without mounting the unavailable DSX analysis", async () => {
    renderAt("/dashboard/develop/experiment/e1/summary?datasetId=ds1");
    // Census §A supersedes the unavailable-summary disposition: the legacy
    // stats endpoint still returns scores and rankings at 3f.
    const table = await screen.findByTestId("classic-summary-table");
    expect(table).toHaveTextContent('"quality":0.9');
    expect(table).toHaveTextContent('"rank":1');
    expect(table).toHaveTextContent('"legacyToolbar":false');
    expect(screen.getByTestId("classic-eval-chart")).toHaveTextContent(
      "quality",
    );
    // Render the real chart, not a placeholder: stats scores are 0–100,
    // including values below one (0.9 is 0.9%, not 90%).
    const chart = screen.getByRole("img", { name: "quality: Prompt A 0.9%" });
    expect(chart).toBeVisible();
    const mark =
      within(chart).getByText("0.9%").previousElementSibling.firstElementChild;
    expect(parseFloat(window.getComputedStyle(mark).width)).toBeCloseTo(0.9);
    expect(summaryGet).toHaveBeenCalledWith(
      expect.stringMatching(/\/experiments\/v2\/e1\/stats\/$/),
    );
    expect(screen.queryByText(/This summary view is unavailable/)).toBeNull();
    expect(lastLocation.pathname).toBe(
      "/dashboard/develop/experiment/e1/summary",
    );
    expect(screen.queryByTestId("classic-data-view")).toBeNull();
    expect(dsxChildMounts.count).toBe(0);
  });

  it("plots both classic runs as grouped bars with percentage and numeric values", async () => {
    summaryGet.mockResolvedValue({
      data: {
        result: {
          table_data: [
            {
              dataset_id: "a",
              experiment_dataset_name: "Prompt A",
              quality: 25,
              numeric: 0,
            },
            {
              dataset_id: "b",
              experiment_dataset_name: "Prompt B",
              quality: 75,
              numeric: 88.8,
            },
          ],
          column_config: [
            { name: "quality", output_type: "score" },
            { name: "numeric", output_type: "numeric" },
          ],
        },
      },
    });
    renderAt("/dashboard/develop/experiment/e1/summary?datasetId=ds1");
    const chart = await screen.findByRole("img", {
      name: "quality: Prompt A 25.0%, Prompt B 75.0%. numeric: Prompt A 0.00, Prompt B 88.80",
    });
    expect(chart).toBeVisible();
    for (const [value, width] of [
      ["25.0%", "25%"],
      ["75.0%", "75%"],
      ["0.00", "0%"],
      ["88.80", "88.8%"],
    ]) {
      const mark =
        within(chart).getByText(value).previousElementSibling.firstElementChild;
      expect(mark).toHaveStyle({ width });
      // One plotted value and one legend value for each run/metric pair.
      expect(screen.getAllByText(value)).toHaveLength(2);
    }
    expect(within(chart).getAllByText("Prompt A")).toHaveLength(2);
    expect(within(chart).getAllByText("Prompt B")).toHaveLength(2);
    expect(dsxChildMounts.count).toBe(0);
  });

  it.each(["snake", "camel"])(
    "plots actual classic radar vertices and legend from %s-case stats",
    async (shape) => {
      const rows = [
        {
          [shape === "snake" ? "dataset_id" : "datasetId"]: "a",
          [shape === "snake"
            ? "experiment_dataset_name"
            : "experimentDatasetName"]: "Prompt A",
          quality: 86.6,
          pass: 100,
          numeric: 42.5,
          low: 0.9,
        },
        {
          [shape === "snake" ? "dataset_id" : "datasetId"]: "b",
          [shape === "snake"
            ? "experiment_dataset_name"
            : "experimentDatasetName"]: "Prompt B",
          quality: 50,
          pass: 0,
          numeric: 20,
          low: null,
        },
      ];
      const evals = [
        { id: "q-id", name: "quality", output_type: "score" },
        { id: "p-id", name: "pass", output_type: "boolean" },
        { id: "n-id", name: "numeric", output_type: "numeric" },
        { id: "l-id", name: "low", output_type: "score" },
      ];
      summaryGet.mockResolvedValue({
        data: {
          result: {
            [shape === "snake" ? "table_data" : "tableData"]: rows,
            [shape === "snake" ? "column_config" : "columnConfig"]: evals,
          },
        },
      });
      renderAt("/dashboard/develop/experiment/e1/summary?datasetId=ds1");
      const chart = await screen.findByRole("img", {
        name: "quality: Prompt A 86.6%, Prompt B 50.0%. pass: Prompt A 100%, Prompt B 0%. numeric: Prompt A 42.50, Prompt B 20.00. low: Prompt A 0.9%, Prompt B —",
      });
      expect(chart).toBeVisible();
      expect(chart.tagName.toLowerCase()).toBe("svg");
      for (const { name } of evals)
        expect(within(chart).getByText(name)).toBeVisible();
      // The real SVG has one polygon per run and one vertex per metric;
      // these fixed coordinates also pin the 0–100 to 0–1 conversion.
      expect(chart.querySelectorAll("polygon[fill-opacity]")).toHaveLength(2);
      const vertices = chart.querySelectorAll("circle");
      const expectedPoints = [
        [170, 71.276],
        [284, 170],
        [170, 218.45],
        [168.974, 170],
        [170, 113],
        [170, 170],
        [170, 192.8],
        [170, 170],
      ];
      expect(vertices).toHaveLength(expectedPoints.length);
      expectedPoints.forEach(([x, y], index) => {
        expect(Number(vertices[index].getAttribute("cx"))).toBeCloseTo(x, 3);
        expect(Number(vertices[index].getAttribute("cy"))).toBeCloseTo(y, 3);
      });
      expect(screen.getAllByText("Prompt A")).toHaveLength(4);
      expect(screen.getAllByText("Prompt B")).toHaveLength(4);
      for (const value of [
        "86.6%",
        "50.0%",
        "100%",
        "0%",
        "42.50",
        "20.00",
        "0.9%",
        "—",
      ])
        expect(screen.getByText(value)).toBeVisible();
      expect(screen.queryByText("baseline")).toBeNull();
      expect(screen.getByTestId("classic-summary-table")).toHaveTextContent(
        '"legacyToolbar":false',
      );
      expect(dsxChildMounts.count).toBe(0);
    },
  );

  it("fills datasetId and payload from the experiment when the DSX create lands with neither", async () => {
    renderAt("/dashboard/develop/experiment/e1/data");
    await waitFor(() =>
      expect(new URLSearchParams(lastLocation.search).get("datasetId")).toBe(
        "ds1",
      ),
    );
    const payload = JSON.parse(
      new URLSearchParams(lastLocation.search).get("payload"),
    );
    expect(payload).toEqual({ id: "e1", name: "Exp One" });
    expect(lastLocation.pathname).toBe("/dashboard/develop/experiment/e1/data");
    expect(experimentDetail.calls).toContain("e1");
  });

  it("an experiment the API answers 404 for (deleted, or another organization's) shows Experiment not found, no grid and no Rerun", async () => {
    experimentDetail.error = {
      statusCode: 404,
      message: "Experiment not found",
    };
    renderAt("/dashboard/develop/experiment/e1/data?datasetId=ds1");
    expect(await screen.findByTestId("experiment-not-found")).toBeTruthy();
    expect(screen.getByText("Experiment not found")).toBeTruthy();
    expect(screen.getByRole("link", { name: /All datasets/ })).toBeTruthy();
    expect(screen.queryByTestId("classic-data-view")).toBeNull();
    expect(screen.queryByTestId("classic-row")).toBeNull();
    expect(screen.queryByTestId("classic-bar")).toBeNull();
    expect(screen.queryByTestId("classic-experiment-page")).toBeNull();
    expect(dsxChildMounts.count).toBe(0);
  });

  it("any other failed read keeps dev's page (its own retry banner)", async () => {
    experimentDetail.error = { statusCode: 500 };
    renderAt("/dashboard/develop/experiment/e1/data?datasetId=ds1");
    expect(await screen.findByTestId("classic-data-view")).toBeTruthy();
    expect(screen.queryByTestId("experiment-not-found")).toBeNull();
  });

  it("classicLandingSearch leaves a URL that already names the dataset alone", () => {
    expect(classicLandingSearch("?datasetId=ds9", { dataset_id: "ds1" })).toBe(
      null,
    );
    expect(classicLandingSearch("", undefined)).toBe(null);
    expect(classicLandingSearch("", { id: "e1" })).toBe(null);
    const s = new URLSearchParams(
      classicLandingSearch("?keep=1", { id: "e1", dataset_id: "ds1" }),
    );
    expect(s.get("keep")).toBe("1");
    expect(s.get("datasetId")).toBe("ds1");
    expect(classicDataPath("e1")).toBe("/dashboard/develop/experiment/e1/data");
  });
});
