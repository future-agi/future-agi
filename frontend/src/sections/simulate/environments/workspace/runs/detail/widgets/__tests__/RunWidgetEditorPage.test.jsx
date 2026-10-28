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
const { defaultSpanOf } = await import("../../analytics/panelRegistry");
const { newId, readLayoutWidget, resetLayouts, saveLayoutWidget } =
  await import("../../analytics/useRunLayout");

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

// Pins the ids new widgets get, so a test can read the saved one back.
const savedId = () => newId("query");

let setItem;
// Iconify caches its icons in localStorage on its own.
const ownWrites = (spy) =>
  spy.mock.calls.filter(([key]) => !String(key).startsWith("iconify"));
let random;
beforeEach(() => {
  random = vi.spyOn(Math, "random").mockReturnValue(0.5);
  resetLayouts();
  setItem = vi.spyOn(Storage.prototype, "setItem");
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
  // The editor keeps widgets in the page's memory, never in the browser.
  expect(ownWrites(setItem)).toEqual([]);
  setItem.mockRestore();
  random.mockRestore();
  vi.clearAllMocks();
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

  it("Save sits beside a UI only flag: the widget isn't saved to an API", async () => {
    renderEditor();
    await screen.findByText("3 calls in this run");
    const actions = screen
      .getByRole("button", { name: "Save" })
      .closest(".MuiStack-root");
    expect(within(actions).getByText("UI only")).toBeInTheDocument();
  });

  it("the series table reads a duration in seconds and minutes, not K ms", async () => {
    renderEditor();
    await screen.findByText("3 calls in this run");
    await pickMetric(/^Call duration(?! \(s\))/);
    const table = screen.getByRole("table");
    expect(table).toHaveTextContent("1m 1s");
    expect(table).not.toHaveTextContent(/K ms/);
    // Each call's cell too, not raw milliseconds like "61,000".
    expect(table).not.toHaveTextContent(/\d{1,3},\d{3}/);
  });

  it("Save adds the widget to the Custom widgets section", async () => {
    // Views can't be saved yet, so a stale ?view= falls back to Default.
    renderEditor("new", "?view=Mine");
    await screen.findByText("3 calls in this run");

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await pickMetric(/^Calls/);
    fireEvent.click(screen.getByRole("button", { name: "Untitled widget" }));
    fireEvent.change(screen.getByPlaceholderText("Untitled widget"), {
      target: { value: "Calls by outcome" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(readLayoutWidget(ENV_ID, savedId())).toMatchObject({
      kind: "query",
      title: "Calls by outcome",
      chart_type: "line",
      query: {
        range: "this",
        metrics: [expect.objectContaining({ id: "calls" })],
      },
    });
    expect(screen.getByTestId("location")).toHaveTextContent(
      `${RUN_URL}?tab=analytics`,
    );
    expect(screen.getByTestId("location")).not.toHaveTextContent("view=");
  });

  it("saving an edited copy keeps it in its section", async () => {
    saveLayoutWidget(ENV_ID, {
      id: "copy-1",
      of: "goal_outcome",
      title: "Goal outcome breakdown (copy)",
      section: "breakdowns",
    });
    renderEditor("copy-1");
    await screen.findByText("3 calls in this run");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const saved = readLayoutWidget(ENV_ID, "copy-1");
    expect(saved).toMatchObject({ kind: "query", section: "breakdowns" });
    // And keeps the built-in's width rather than taking the whole row.
    expect(defaultSpanOf("copy-1", saved, 4)).toBe(
      defaultSpanOf("goal_outcome", null, 4),
    );
    expect(defaultSpanOf("copy-1", saved, 4)).toBeLessThan(4);
  });

  it("opening a built-in starts from its query and saves a new custom widget", async () => {
    renderEditor("goal_outcome");
    await screen.findByText("3 calls in this run");
    expect(
      screen.getByRole("button", { name: "Goal outcome breakdown" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("apex-chart")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(readLayoutWidget(ENV_ID, savedId())).toMatchObject({
      kind: "query",
      title: "Goal outcome breakdown",
      chart_type: "pie",
    });
    expect(screen.getByTestId("location")).toHaveTextContent(
      `${RUN_URL}?tab=analytics`,
    );
  });
});
