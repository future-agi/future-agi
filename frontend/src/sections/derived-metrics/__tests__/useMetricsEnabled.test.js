import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { useCapabilities } from "src/hooks/useCapabilities";
import dashboardSource from "src/routes/sections/dashboard.jsx?raw";
import { useMetricsEnabled } from "../useMetricsEnabled";
import MetricsGate from "../MetricsGate";

vi.mock("src/hooks/useCapabilities", () => ({ useCapabilities: vi.fn() }));
vi.mock("src/components/loading-screen", () => ({
  LoadingScreen: () =>
    React.createElement("div", { role: "status" }, "Loading"),
}));

const h = React.createElement;
const refetch = vi.fn();

function capabilities(overrides = {}) {
  useCapabilities.mockReturnValue({
    data: { metrics_enabled: false },
    isLoading: false,
    isError: false,
    refetch,
    ...overrides,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  capabilities();
});
afterEach(cleanup);

describe("useMetricsEnabled", () => {
  it.each([
    undefined,
    null,
    {},
    { metrics_enabled: false },
    { metrics_enabled: 1 },
    { metrics_enabled: "true" },
  ])("fails closed without an explicit boolean true: %j", (data) => {
    capabilities({ data });
    expect(renderHook(useMetricsEnabled).result.current.enabled).toBe(false);
  });

  it("reads the existing capability response and follows changes", () => {
    const { result, rerender } = renderHook(useMetricsEnabled);
    expect(result.current.enabled).toBe(false);
    capabilities({ data: { metrics_enabled: true } });
    rerender();
    expect(result.current.enabled).toBe(true);
    expect(result.current.refetch).toBe(refetch);
    capabilities();
    rerender();
    expect(result.current.enabled).toBe(false);
  });

  it.each([{ isLoading: true }, { isError: true }])(
    "does not enable from stale data during %j",
    (state) => {
      capabilities({ data: { metrics_enabled: true }, ...state });
      expect(renderHook(useMetricsEnabled).result.current.enabled).toBe(false);
    },
  );
});

function renderRoute(path, child) {
  return render(
    h(
      MemoryRouter,
      { initialEntries: [path] },
      h(
        Routes,
        null,
        h(
          Route,
          {
            path: "/dashboard/metrics",
            element: h(MetricsGate, null, h(Outlet)),
          },
          h(Route, { index: true, element: child }),
          ...["new", ":metricId", ":metricId/edit"].map((part) =>
            h(Route, { key: part, path: part, element: child }),
          ),
        ),
        h(Route, {
          path: "/dashboard/derived-metrics/*",
          element: h(MetricsGate, null, child),
        }),
        h(Route, { path: "/404", element: h("div", null, "Not found") }),
      ),
    ),
  );
}

const paths = [
  "/dashboard/metrics",
  "/dashboard/metrics/new",
  "/dashboard/metrics/example",
  "/dashboard/metrics/example/edit",
  "/dashboard/derived-metrics/example?window=30d",
];

describe("metrics route gate", () => {
  it("wraps the actual canonical parent and legacy redirect, not just the index", () => {
    expect(dashboardSource).toMatch(
      /path: "metrics",\s*element: \(\s*<MetricsGate>\s*<Outlet \/>\s*<\/MetricsGate>/,
    );
    expect(dashboardSource).toMatch(
      /path: "derived-metrics\/\*",\s*element: \(\s*<MetricsGate>\s*<LegacyMetricsRedirect \/>\s*<\/MetricsGate>/,
    );
  });

  it.each(paths)("does not mount %s when off", (path) => {
    const page = vi.fn(() => h("div", null, "Metric page"));
    renderRoute(path, h(page));
    expect(screen.getByText("Not found")).toBeInTheDocument();
    expect(page).not.toHaveBeenCalled();
  });

  it.each(paths)("mounts %s when on", (path) => {
    capabilities({ data: { metrics_enabled: true } });
    renderRoute(path, h("div", null, "Metric page"));
    expect(screen.getByText("Metric page")).toBeInTheDocument();
    expect(screen.queryByText("Not found")).not.toBeInTheDocument();
  });

  it("waits for availability without mounting the page or redirecting", () => {
    capabilities({ isLoading: true, data: undefined });
    const page = vi.fn(() => h("div", null, "Metric page"));
    renderRoute(paths[0], h(page));
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.queryByText("Not found")).not.toBeInTheDocument();
    expect(page).not.toHaveBeenCalled();
  });

  it("offers retry on lookup failure, never an upgrade or a stale allowed page", () => {
    capabilities({ data: { metrics_enabled: true }, isError: true });
    const page = vi.fn(() => h("div", null, "Metric page"));
    renderRoute(paths[0], h(page));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't check Metrics availability",
    );
    expect(page).not.toHaveBeenCalled();
    expect(screen.queryByText(/upgrade|Not found/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledOnce();
  });
});
