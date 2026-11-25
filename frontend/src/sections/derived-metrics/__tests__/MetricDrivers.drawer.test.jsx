import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import MetricDetailPage from "../MetricDetailPage";
import { driver, field, kpi, metric, trend } from "./metricPageFixtures";

const mock = vi.hoisted(() => ({ get: vi.fn(), read: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: { get: mock.get },
  readQuery: mock.read,
  endpoints: {
    derivedMetrics: Object.fromEntries(
      ["detail", "kpi", "trend", "drivers", "fields"].map((name) => [
        name,
        (id) => `/metrics/${id}/${name}`,
      ]),
    ),
    project: { traceSessionQuery: (id) => `/sessions/${id}` },
  },
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    currentOrganizationId: "org-a",
    isReady: true,
  }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({
    currentWorkspaceId: "ws-a",
    currentWorkspaceRole: "workspace_member",
    isReady: true,
  }),
}));
vi.mock("../useMetricsEnabled", () => ({
  useMetricsEnabled: () => ({ enabled: true }),
}));
vi.mock("react-apexcharts", () => ({ default: () => null }));
// Use the real TracesDrawer, including its local session/tab state and query.
// Only replace leaf content unrelated to the drawer's lifecycle.
vi.mock("src/sections/projects/TracesDrawer/Header", () => ({
  default: ({ hasNextTrace, handleNextTrace, onClose }) => (
    <>
      <button disabled={!hasNextTrace} onClick={handleNextTrace}>
        Next session
      </button>
      <button onClick={onClose}>Close source</button>
    </>
  ),
}));
vi.mock("src/sections/projects/TracesDrawer/SessionHistory", () => ({
  default: ({ activeSessionId }) => (
    <div data-testid="session-history">{activeSessionId}</div>
  ),
}));
vi.mock("src/sections/projects/TracesDrawer/SessionEvalsList", () => ({
  default: ({ sessionId }) => (
    <div data-testid="session-evals">{sessionId}</div>
  ),
}));
vi.mock("src/sections/projects/TracesDrawer/Filters", () => ({
  default: () => null,
}));
vi.mock("src/components/InlineAnnotator", () => ({ default: () => null }));
vi.mock("src/components/traceDetail/TraceDetailPanel", () => ({
  CaptureCoverageNotice: () => null,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/hooks/use-scroll-end", () => ({
  useScrollEnd: () => ({ current: null }),
}));
vi.mock("src/utils/logger", () => ({ default: { debug: vi.fn() } }));

let client;
let responses;
const readMetric = async (url) => ({
  data: responses[url.split("/").at(-1)],
});

beforeEach(() => {
  window.history.replaceState({}, "", "/dashboard/metrics/metric-a");
  responses = structuredClone({
    detail: metric,
    kpi,
    trend,
    fields: { results: [field] },
    drivers: {
      provenance: kpi.provenance,
      effective_window: kpi.effective_window,
      results: [driver],
      next_cursor: null,
    },
  });
  mock.get.mockReset().mockImplementation(readMetric);
  mock.read.mockReset().mockImplementation(async (url) => ({
    data: {
      result: {
        response: [],
        session_metadata: {
          next_session_id: url.endsWith("session-a") ? "session-b" : null,
        },
      },
    },
  }));
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 300000 } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
});

it.each(
  ["detail", "drivers", "fields"].flatMap((resource) =>
    ["success", "error"].map((outcome) => ({ resource, outcome })),
  ),
)(
  "preserves the real drawer's session and Evals tab through a $resource refetch and $outcome",
  async ({ resource, outcome }) => {
    render(
      <QueryClientProvider client={client}>
        <Routes>
          <Route
            path="/dashboard/metrics/:metricId"
            element={<MetricDetailPage />}
          />
        </Routes>
      </QueryClientProvider>,
    );
    await screen.findByTestId("metric-hero");
    fireEvent.click(screen.getByRole("tab", { name: "Drivers" }));
    fireEvent.click(await screen.findByRole("button", { name: "session-a" }));
    expect(await screen.findByTestId("session-history")).toHaveTextContent(
      "session-a",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Next session" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next session" }));
    await waitFor(() =>
      expect(screen.getByTestId("session-history")).toHaveTextContent(
        "session-b",
      ),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Evals" }));
    expect(screen.getByTestId("session-evals")).toHaveTextContent("session-b");

    let resolve;
    let reject;
    const pending = new Promise((resolvePromise, rejectPromise) => {
      resolve = resolvePromise;
      reject = rejectPromise;
    });
    mock.get.mockImplementation((url) =>
      url.endsWith(`/${resource}`) ? pending : readMetric(url),
    );
    await act(async () => {
      void client.invalidateQueries({
        predicate: ({ queryKey }) => queryKey[4] === resource,
      });
    });
    const label = {
      detail: "metric",
      drivers: "drivers",
      fields: "field metadata",
    }[resource];
    expect(await screen.findByText(`Loading ${label}…`)).toBeVisible();
    await waitFor(() =>
      expect(screen.queryByTestId("session-evals")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("passed: false")).not.toBeInTheDocument();

    if (outcome === "error") {
      await act(async () => reject({ statusCode: 503 }));
      expect(
        await screen.findByText(
          `Could not load ${label}. Retry or reload the metric.`,
        ),
      ).toBeVisible();
      expect(screen.queryByTestId("session-evals")).not.toBeInTheDocument();
      mock.get.mockImplementation(readMetric);
      fireEvent.click(screen.getByRole("button", { name: `Retry ${label}` }));
    } else {
      await act(async () => resolve({ data: responses[resource] }));
    }

    expect(await screen.findByTestId("session-evals")).toHaveTextContent(
      "session-b",
    );
    expect(screen.getByRole("tab", { name: "Evals" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByTestId("session-history")).not.toBeInTheDocument();
    expect(mock.read).toHaveBeenCalledWith("/sessions/session-b", {
      params: {
        page_number: 0,
        page_size: 10,
        navigation_context: JSON.stringify({
          workspace_id: "ws-a",
          project_id: "project-a",
          filters: [],
          sort_params: [],
        }),
      },
    });

    // An intentional close still clears navigation, unlike a masked read.
    fireEvent.click(screen.getByRole("button", { name: "Close source" }));
    fireEvent.click(await screen.findByRole("button", { name: "session-a" }));
    expect(await screen.findByTestId("session-history")).toHaveTextContent(
      "session-a",
    );
    expect(
      screen.getByRole("tab", { name: "Session History" }),
    ).toHaveAttribute("aria-selected", "true");
  },
);
