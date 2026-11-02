import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cloneElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { voiceAnalytics } from "./analyticsFixture";

const useRunAnalytics = vi.fn();
const useRunWidgetCalls = vi.fn();
vi.mock("recharts", async (importOriginal) => ({
  ...(await importOriginal()),
  ResponsiveContainer: ({ children }) =>
    cloneElement(children, { width: 600, height: 250 }),
}));
vi.mock("react-apexcharts", () => ({
  default: ({ type, series }) => (
    <div data-testid="apex-chart" data-type={type}>
      {series
        .map((item) => `${item.name}:${(item.data || []).join("|")}`)
        .join(",")}
    </div>
  ),
}));
vi.mock("src/api/simulate-environments/runAnalytics", () => ({
  useRunAnalytics: (...args) => useRunAnalytics(...args),
}));
vi.mock("src/api/simulate-environments/runDetail", () => ({
  useRunDetail: () => ({ identity: { ordinal: 4 }, isLoading: false }),
}));
vi.mock("../../widgets/useRunWidgetCalls", () => ({
  useRunWidgetCalls: (...args) => useRunWidgetCalls(...args),
}));
vi.mock("src/api/simulate-environments/environment", () => ({
  useEnvironment: () => ({
    env: { id: "env-1", name: "Refund desk" },
    isLoading: false,
    notFound: false,
  }),
}));

const { default: RunAnalytics } = await import("../../RunAnalytics");
const { default: RunWidgetEditorPage } = await import(
  "../../widgets/RunWidgetEditorPage"
);
const { resetLayouts, saveLayoutWidget } = await import("../useRunLayout");

const EDITOR =
  "/dashboard/simulate/environments/env-1/runs/test-1/execution-1/widgets";
const QUERY_WIDGET = {
  id: "query-1",
  kind: "query",
  title: "Calls per outcome",
  chart_type: "line",
  query: {
    range: "this",
    xAxis: "run",
    metrics: [
      {
        id: "calls",
        name: "Calls",
        type: "system",
        unit: "",
        aggregation: "count",
        filters: [],
      },
    ],
    filters: [],
    breakdowns: [{ id: "status", name: "Outcome" }],
  },
};

let setItem;
// Iconify caches its icons in localStorage on its own.
const ownWrites = (spy) =>
  spy.mock.calls.filter(([key]) => !String(key).startsWith("iconify"));
const show = () => {
  useRunAnalytics.mockReturnValue({
    data: voiceAnalytics(),
    isPending: false,
    isError: false,
  });
  return render(
    <RunAnalytics executionId="execution-1" envId="env-1" testId="test-1" />,
  );
};
const openMenu = (title) =>
  fireEvent.click(screen.getByRole("button", { name: `${title} actions` }));
const pick = (name) => fireEvent.click(screen.getByRole("menuitem", { name }));

describe("analytics entry to the widget editor", () => {
  beforeEach(() => {
    resetLayouts();
    setItem = vi.spyOn(Storage.prototype, "setItem");
    window.history.replaceState({}, "", "/");
    useRunWidgetCalls.mockReturnValue({
      tasks: [
        { id: "c1", status: "passed", evalResults: [] },
        { id: "c2", status: "failed", evalResults: [] },
        { id: "c3", status: "passed", evalResults: [] },
      ],
      count: 3,
      capped: false,
      isLoading: false,
      isError: false,
    });
  });
  afterEach(() => {
    expect(ownWrites(setItem)).toEqual([]);
    setItem.mockRestore();
  });

  it("Edit opens the widget in the editor", () => {
    show();
    openMenu("Weakest scenarios");
    pick("Edit");
    expect(window.location.pathname).toBe(`${EDITOR}/risk`);
  });

  it("Add widget opens a new widget in the editor", () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    expect(window.location.pathname).toBe(`${EDITOR}/new`);
    expect(window.location.search).toBe("");
  });

  it("a saved widget draws from the run's calls under Custom widgets", () => {
    saveLayoutWidget("env-1", QUERY_WIDGET);
    show();
    expect(
      screen.getByRole("heading", { name: "Custom widgets" }),
    ).toBeInTheDocument();
    const widget = screen.getByRole("region", { name: "Calls per outcome" });
    expect(useRunWidgetCalls).toHaveBeenCalledWith("execution-1");
    expect(within(widget).getByTestId("apex-chart")).toHaveTextContent(
      "Calls / Failed (count):1,Calls / Passed (count):2",
    );

    openMenu("Calls per outcome");
    pick("Edit");
    expect(window.location.pathname).toBe(`${EDITOR}/query-1`);
  });

  it("Duplicate keeps a saved widget's query", () => {
    saveLayoutWidget("env-1", QUERY_WIDGET);
    show();
    openMenu("Calls per outcome");
    pick("Duplicate");
    const copy = screen.getByRole("region", {
      name: "Calls per outcome (copy)",
    });
    expect(within(copy).getByTestId("apex-chart")).toHaveTextContent(
      "Calls / Failed (count):1,Calls / Passed (count):2",
    );
  });

  it("an editor save shows on the analytics page for the visit, not after a reload", async () => {
    const RUN =
      "/dashboard/simulate/environments/env-1/runs/test-1/execution-1";
    useRunAnalytics.mockReturnValue({
      data: voiceAnalytics(),
      isPending: false,
      isError: false,
    });
    const visit = () =>
      render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={[`${RUN}/widgets/goal_outcome`]}>
            <Routes>
              <Route
                path="/dashboard/simulate/environments/:envId/runs/:testId/:executionId/widgets/:widgetId"
                element={<RunWidgetEditorPage />}
              />
              <Route
                path="/dashboard/simulate/environments/:envId/runs/:testId/:executionId"
                element={<RunAnalytics executionId="execution-1" />}
              />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
        {
          wrapper: ({ children }) => (
            <ThemeProvider theme={createTheme({ palette: palette("light") })}>
              {children}
            </ThemeProvider>
          ),
        },
      );

    const first = visit();
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Custom widgets" }),
      ).toBeInTheDocument(),
    );
    const custom = screen
      .getByRole("heading", { name: "Custom widgets" })
      .closest("[data-section-id]");
    expect(
      within(custom).getByRole("region", { name: "Goal outcome breakdown" }),
    ).toBeInTheDocument();
    first.unmount();

    resetLayouts();
    render(<RunAnalytics executionId="execution-1" envId="env-1" />);
    expect(
      screen.queryByRole("heading", { name: "Custom widgets" }),
    ).not.toBeInTheDocument();
  });
});
