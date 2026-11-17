import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import {
  afterAll,
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import DatasetOptimizationDetail from "../DatasetOptimizationDetail";

// Install before app imports. No socket, TLS, browser fetch or mutation may escape.
const guard = await vi.hoisted(async () => {
  const { default: net } = await import("node:net");
  const { default: tls } = await import("node:tls");
  const deny = vi.fn(() => {
    throw new Error(
      "Unexpected network or mutation in qualification unit fixture",
    );
  });
  const socket = vi
    .spyOn(net.Socket.prototype, "connect")
    .mockImplementation(deny);
  const secureSocket = vi.spyOn(tls, "connect").mockImplementation(deny);
  return { deny, socket, secureSocket };
});
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: http,
  endpoints: {
    develop: {
      datasetOptimization: {
        detail: (id) => `/unit/dataset-optimizations/${id}`,
      },
    },
  },
}));
// Keep the real useQuery/select, normalizer, ShowComponent, trial panel and loader.
// Isolate unrelated UI subtrees and the common module's unused utility dependency.
vi.mock("src/utils/utils", () => ({ camelToSnakeCase: () => guard.deny() }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({
  enqueueSnackbar: () => guard.deny(),
}));
vi.mock("../DatasetOptimizationHeaderComponent", () => ({
  default: () => null,
}));
vi.mock("../DatasetOptimizingSteps", () => ({ default: () => null }));
vi.mock("../DatasetOptimizationResultGraph", () => ({
  default: () => <div data-testid="final-graph" />,
}));
vi.mock("../DatasetOptimizationResultGrid", () => ({
  default: () => <div data-testid="final-grid" />,
}));
vi.mock("../DatasetOptimizationResultBar", () => ({
  default: () => <div data-testid="final-bar" />,
}));
vi.mock("../context/DatasetOptimizationResultProvider", () => ({
  // eslint-disable-next-line react/prop-types -- unit boundary only
  default: ({ children }) => children,
}));
vi.mock("../context/DatasetOptimizationResultContext", () => ({
  useDatasetOptimizationResultContext: () => guard.deny(),
}));
vi.mock("../DatasetOptimizationCancelled", () => ({
  default: () => <div data-testid="cancelled-view" />,
}));
vi.mock("recharts", () => {
  // eslint-disable-next-line react/prop-types -- bypass layout, not trial logic
  const Container = ({ children }) => children;
  const Empty = () => null;
  return {
    ResponsiveContainer: Container,
    LineChart: Container,
    CartesianGrid: Empty,
    Legend: Empty,
    Line: Empty,
    Tooltip: Empty,
    XAxis: Empty,
    YAxis: Empty,
  };
});

const QUALIFICATION = "Incomplete — best-so-far trials are not a final result.";
const OPTIMIZATION_ID = "qualification-unit";
const DETAIL_URL = `/unit/dataset-optimizations/${OPTIMIZATION_ID}`;
const NAME = "Fixture optimization";
const REASON = "Fixture evaluator timed out";
let client;

// The actual BASE normalizer spreads w6 and maps these snake_case fields.
// w6={} is not valid for the real trial panel: it reads baseline.score and trials.
function detailResult(status, withTrials = true) {
  return {
    status,
    optimiser_name: NAME,
    error_message: status === "failed" ? REASON : undefined,
    table: [{ id: "trial-unit", prompt: "Synthetic trial prompt" }],
    ...(withTrials
      ? {
          w6: {
            experiment_id: "experiment-unit",
            experiment_name: "Fixture experiment",
            dataset_id: "dataset-unit",
            algorithm: "random_search",
            budget: 2,
            rows: 2,
            prompt: {
              id: "prompt-unit",
              name: "Fixture prompt",
              version: "v1",
            },
            baseline: {
              run_id: "baseline-unit",
              label: "Baseline fixture",
              score: 0.4,
              status: "completed",
            },
            trials: [
              {
                trial: 1,
                trial_id: "trial-unit",
                run_id: "trial-run-unit",
                label: "Trial fixture",
                score: 0.6,
                best_so_far: 0.6,
                is_best: true,
                status: "completed",
                lessons: [],
              },
            ],
          },
        }
      : {}),
  };
}

beforeEach(() => {
  guard.deny.mockClear();
  for (const method of Object.values(http))
    method.mockReset().mockImplementation(guard.deny);
  for (const name of ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"])
    vi.stubGlobal(name, guard.deny);
});
afterEach(() => {
  try {
    cleanup();
    client?.clear();
    expect(http.post).not.toHaveBeenCalled();
    expect(http.put).not.toHaveBeenCalled();
    expect(guard.deny).not.toHaveBeenCalled();
  } finally {
    client = undefined;
    vi.unstubAllGlobals();
  }
});
afterAll(() => {
  guard.socket.mockRestore();
  guard.secureSocket.mockRestore();
});

async function renderDetail(status, withTrials = true) {
  const result = detailResult(status, withTrials);
  http.get.mockImplementation((url) => {
    if (url !== DETAIL_URL) return guard.deny();
    // Real queryFn consumes Axios's data.result envelope and real select normalizes it.
    return Promise.resolve({ data: { result } });
  });
  client = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DatasetOptimizationDetail
          optimizationId={OPTIMIZATION_ID}
          onBack={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // Waiting for normalized optimiser_name prevents negative assertions at pending state.
  expect(await screen.findByText(NAME)).toBeTruthy();
  expect(http.get).toHaveBeenCalledWith(DETAIL_URL);
  expect(http.get).toHaveBeenCalledTimes(1);
}

function expectNoFinalResults() {
  for (const id of ["final-graph", "final-grid", "final-bar"])
    expect(screen.queryByTestId(id)).toBeNull();
}

function expectRetainedTrials() {
  const panel = screen.getByTestId("optimization-trial-runs");
  expect(within(panel).getAllByTestId("optimization-trial-row")).toHaveLength(
    2,
  );
  expect(within(panel).getByText("Trial fixture")).toBeTruthy();
  expect(within(panel).getByTestId("optimization-best-so-far")).toBeTruthy();
}

describe("dataset optimization incomplete qualification", () => {
  it("FAILED with trial payload shows the exact incomplete/not-final qualification", async () => {
    await renderDetail("failed");
    expectRetainedTrials();
    expect(screen.getByText("Failed to optimize")).toBeTruthy();
    expect(screen.getByText(REASON)).toBeTruthy();
    expect(screen.queryByTestId("optimization-promote")).toBeNull();
    expectNoFinalResults();
    // Expected RED at BASE: only this new qualification is missing.
    const qualification = screen.getByText(QUALIFICATION, { exact: true });
    expect(qualification.getAttribute("role")).toBe("status");
    expect(qualification.textContent).toBe(QUALIFICATION);
  });

  it("COMPLETED with trial payload has no qualification and keeps final results", async () => {
    await renderDetail("completed");
    expect(screen.queryByText(QUALIFICATION, { exact: true })).toBeNull();
    expectRetainedTrials();
    expect(screen.getByTestId("optimization-promote")).toBeTruthy();
    for (const id of ["final-graph", "final-grid", "final-bar"])
      expect(screen.getByTestId(id)).toBeTruthy();
    expect(screen.queryByText("Failed to optimize")).toBeNull();
    expect(screen.queryByText(REASON)).toBeNull();
  });

  it("CANCELLED with trial payload preserves the cancelled-only body", async () => {
    await renderDetail("cancelled");
    expect(screen.getByTestId("cancelled-view")).toBeTruthy();
    expect(screen.queryByText(QUALIFICATION, { exact: true })).toBeNull();
    expect(screen.queryByTestId("optimization-trial-runs")).toBeNull();
    expect(screen.queryByTestId("optimization-promote")).toBeNull();
    expect(screen.queryByText("Failed to optimize")).toBeNull();
    expectNoFinalResults();
  });

  it("FAILED without w6 preserves the failure loader without a qualification or panel", async () => {
    await renderDetail("failed", false);
    expect(screen.getByText("Failed to optimize")).toBeTruthy();
    expect(screen.getByText(REASON)).toBeTruthy();
    expect(screen.queryByText(QUALIFICATION, { exact: true })).toBeNull();
    expect(screen.queryByTestId("optimization-trial-runs")).toBeNull();
    expect(screen.queryByTestId("optimization-promote")).toBeNull();
    expectNoFinalResults();
  });
});
