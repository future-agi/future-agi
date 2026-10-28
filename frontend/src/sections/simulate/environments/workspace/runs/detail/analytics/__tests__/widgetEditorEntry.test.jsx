import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cloneElement } from "react";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
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
      {series.map((item) => `${item.name}:${item.data.join("|")}`).join(",")}
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

const { default: RunAnalytics } = await import("../../RunAnalytics");
const { layoutStorageKey } = await import("../useRunLayout");

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

let storage;
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
    storage = new Map();
    vi.stubGlobal("localStorage", {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    });
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
  afterEach(() => vi.unstubAllGlobals());

  it("Edit opens the widget in the editor", () => {
    show();
    openMenu("Weakest scenarios");
    pick("Edit");
    expect(window.location.pathname).toBe(`${EDITOR}/risk`);
  });

  it("Add widget opens a new widget in the editor, in the open view", () => {
    storage.set(
      layoutStorageKey("env-1"),
      JSON.stringify({ activeView: "Ops", views: { Default: {}, Ops: {} } }),
    );
    show();
    fireEvent.click(screen.getByRole("button", { name: "Add widget" }));
    expect(window.location.pathname).toBe(`${EDITOR}/new`);
    expect(window.location.search).toBe("?view=Ops");
  });

  it("a saved widget draws from the run's calls under Custom widgets", () => {
    storage.set(
      layoutStorageKey("env-1"),
      JSON.stringify({
        activeView: "Default",
        views: { Default: { order: ["query-1"], custom: [QUERY_WIDGET] } },
      }),
    );
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
    storage.set(
      layoutStorageKey("env-1"),
      JSON.stringify({
        activeView: "Default",
        views: { Default: { order: ["query-1"], custom: [QUERY_WIDGET] } },
      }),
    );
    show();
    openMenu("Calls per outcome");
    pick("Duplicate");
    expect(
      screen.getByRole("region", { name: "Calls per outcome (copy)" }),
    ).toBeInTheDocument();
    const saved = JSON.parse(storage.get(layoutStorageKey("env-1")));
    expect(saved.views.Default.custom[1]).toMatchObject({
      kind: "query",
      query: QUERY_WIDGET.query,
      title: "Calls per outcome (copy)",
    });
  });
});
