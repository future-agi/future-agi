import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { CHART_TYPES } from "src/sections/dashboards/widgetEditorConstants";

const useEnvironment = vi.fn();
vi.mock(
  "src/api/simulate-environments/environment",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useEnvironment: (...args) => useEnvironment(...args),
  }),
);
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunDetail: () => ({ identity: { ordinal: 4 }, isLoading: false }),
}));
const fetchRunCalls = vi.fn();
vi.mock("src/api/simulate-environments/runCalls", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchRunCalls: (...args) => fetchRunCalls(...args),
}));
vi.mock("react-apexcharts", () => ({
  default: ({ type, series }) => (
    <div data-testid="apex-chart" data-type={type}>
      {series.map((item) => item.name).join(",")}
    </div>
  ),
}));

const { default: RunWidgetEditorPage } = await import("../RunWidgetEditorPage");
const { default: RunWidgetChart } = await import("../RunWidgetChart");
const { layoutStorageKey } = await import("../../analytics/useRunLayout");

const ENV_ID = "env-1";
const RUN_URL = `/dashboard/simulate/environments/${ENV_ID}/runs/test-1/exec-1`;

const callRow = (id, outcome, extra = {}) => ({
  id,
  outcome,
  source_scenario_key: `scenario-${id}`,
  goal: "Refunds",
  persona: "Dana",
  csat: 7,
  turn_count: 5,
  latency_ms: 420,
  duration_seconds: 61,
  ...extra,
});

const theme = createTheme({ palette: palette("light") });

function LocationProbe() {
  const location = useLocation();
  return (
    <div data-testid="location">{location.pathname + location.search}</div>
  );
}

function renderEditor(widgetId = "new", search = "") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider theme={theme}>
        <MemoryRouter
          initialEntries={[`${RUN_URL}/widgets/${widgetId}${search}`]}
        >
          <Routes>
            <Route
              path="/dashboard/simulate/environments/:envId/runs/:testId/:executionId/widgets/:widgetId"
              element={<RunWidgetEditorPage />}
            />
            <Route path="*" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const pickMetric = async (name) => {
  fireEvent.click(screen.getByRole("button", { name: "Add metric" }));
  const picker = await screen.findByRole("dialog", { name: "Pick a metric" });
  fireEvent.click(within(picker).getByRole("button", { name: name }));
};

let storage;
beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  });
  useEnvironment.mockReturnValue({
    env: { id: ENV_ID, name: "Refund desk" },
    isLoading: false,
    notFound: false,
  });
  fetchRunCalls.mockResolvedValue({
    results: [
      callRow("c1", "passed"),
      callRow("c2", "failed", { persona: "Lee", csat: 3 }),
      callRow("c3", "passed"),
    ],
    count: 3,
    total_pages: 1,
    evaluation_columns: [],
  });
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Run widget editor", () => {
  it("This run queries the real calls of the run", async () => {
    renderEditor();
    expect(await screen.findByText("Refund desk")).toBeInTheDocument();
    expect(screen.getByText("Run 4 · Analytics")).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchRunCalls).toHaveBeenCalledWith(
        "exec-1",
        expect.objectContaining({ page: 1 }),
      ),
    );
    await screen.findByText("3 calls in this run");

    await pickMetric(/^Calls/);
    const table = screen.getByRole("table", { name: "Series" });
    const row = within(table).getByRole("row", { name: /Calls \(count\)/ });
    // The run's total, then one column per call.
    expect(
      within(row)
        .getAllByRole("cell")
        .slice(1)
        .map((cell) => cell.textContent),
    ).toEqual(["3.00", "1", "1", "1"]);
    expect(screen.getByTestId("apex-chart")).toHaveTextContent("Calls (count)");
  });

  it("Last N runs and All runs carry soon chips", async () => {
    renderEditor();
    const range = await screen.findByRole("group", { name: "Runs in range" });
    expect(
      within(range).getByRole("button", { name: "This run" }),
    ).toHaveAttribute("aria-pressed", "true");
    ["Last 3 runs", "Last 5 runs", "Last 10 runs", "All runs"].forEach(
      (label) => {
        const pill = within(range).getByRole("button", { name: label });
        expect(pill).toBeDisabled();
        expect(pill).toHaveAttribute("aria-pressed", "false");
      },
    );
    expect(screen.getByText("Run history soon")).toBeInTheDocument();
  });

  it("nine chart types render", () => {
    expect(CHART_TYPES).toHaveLength(9);
    const series = ["Passed", "Failed"].map((value, index) => ({
      name: `Calls / ${value} (count)`,
      key: `calls|count|${value}`,
      metricIndex: 0,
      metricName: "Calls",
      aggregation: "count",
      unit: "",
      breakdownName: value,
      total: index ? 1 : 2,
      data: [{ x: "Run 4", y: index ? 1 : 2 }],
    }));
    const buckets = [{ key: "run", label: "Run 4", sub: "" }];
    const apexType = {
      line: "line",
      stacked_line: "area",
      column: "bar",
      stacked_column: "bar",
      bar: "bar",
      stacked_bar: "bar",
    };
    CHART_TYPES.forEach(({ value }) => {
      const { unmount, container } = render(
        <ThemeProvider theme={theme}>
          <RunWidgetChart series={series} buckets={buckets} chartType={value} />
        </ThemeProvider>,
      );
      if (apexType[value]) {
        expect(screen.getByTestId("apex-chart")).toHaveAttribute(
          "data-type",
          apexType[value],
        );
      } else if (value === "table") {
        expect(screen.getByRole("table")).toHaveTextContent("Passed");
      } else if (value === "metric") {
        expect(screen.getByText("Calls / Passed (count)")).toBeInTheDocument();
        expect(screen.getByText("2.00")).toBeInTheDocument();
      } else {
        expect(value).toBe("pie");
        expect(container).toHaveTextContent("Calls");
      }
      unmount();
    });
  });

  it("Save adds the widget to the Custom widgets section of the current view", async () => {
    const view = (order) => ({
      order,
      custom: [],
      spans: {},
      sectionOverrides: {},
      sectionOrder: [],
      knownIds: order,
    });
    storage.set(
      layoutStorageKey(ENV_ID),
      JSON.stringify({
        activeView: "Default",
        views: {
          Default: view(["goal_outcome", "csat"]),
          Mine: view(["csat"]),
        },
      }),
    );
    renderEditor("new", "?view=Mine");
    await screen.findByText("3 calls in this run");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await pickMetric(/^Calls/);
    fireEvent.click(screen.getByRole("button", { name: "Untitled widget" }));
    fireEvent.change(screen.getByPlaceholderText("Untitled widget"), {
      target: { value: "Calls by outcome" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const stored = JSON.parse(storage.get(layoutStorageKey(ENV_ID)));
    const [widget] = stored.views.Mine.custom;
    expect(widget).toMatchObject({
      kind: "query",
      title: "Calls by outcome",
      chart_type: "line",
      query: {
        range: "this",
        metrics: [expect.objectContaining({ id: "calls" })],
      },
    });
    expect(stored.views.Mine.order).toEqual(["csat", widget.id]);
    expect(stored.views.Default.custom).toEqual([]);
    expect(screen.getByTestId("location")).toHaveTextContent(
      `${RUN_URL}?tab=analytics&view=Mine`,
    );
  });

  it("opening a built-in starts from its query and saves a new custom widget", async () => {
    renderEditor("goal_outcome");
    await screen.findByText("3 calls in this run");
    expect(
      screen.getByRole("button", { name: "Goal outcome breakdown" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("apex-chart")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const stored = JSON.parse(storage.get(layoutStorageKey(ENV_ID)));
    expect(stored.views.Default.custom).toEqual([
      expect.objectContaining({
        kind: "query",
        title: "Goal outcome breakdown",
        chart_type: "pie",
      }),
    ]);
    expect(screen.getByTestId("location")).toHaveTextContent(
      `${RUN_URL}?tab=analytics`,
    );
  });
});
