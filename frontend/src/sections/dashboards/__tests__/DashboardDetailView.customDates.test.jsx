import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import DashboardDetailView from "../DashboardDetailView";

// OBS-SW-168: the dashboard's Custom dates ride the URL to the widget editor
// and back. Returning used to restore the Custom chip with no dates, so the
// charts silently fell back to their own saved ranges.

const h = vi.hoisted(() => ({ navigate: vi.fn(), props: {}, widgets: [] }));

vi.mock("src/hooks/useDashboards", () => ({
  useDashboardDetail: () => ({
    data: { id: "dash-1", name: "My Dash", widgets: h.widgets },
    isLoading: false,
  }),
  useUpdateDashboard: () => ({ mutate: vi.fn() }),
  useUpdateWidget: () => ({ mutate: vi.fn() }),
  useDeleteWidget: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteDashboard: () => ({ mutate: vi.fn(), isPending: false }),
  useReorderWidgets: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
  useDuplicateWidget: () => ({ mutate: vi.fn() }),
  useCreateWidget: () => ({ mutate: vi.fn(), mutateAsync: vi.fn() }),
}));
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig()),
  useParams: () => ({ dashboardId: "dash-1" }),
  useNavigate: () => h.navigate,
}));
vi.mock("../hooks/useCanEditDashboard", () => ({
  default: () => ({
    canCreate: true,
    canUpdate: true,
    canDelete: true,
    isReadOnly: false,
  }),
}));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("../WidgetChart", async () => {
  const { default: PropTypes } = await import("prop-types");
  function MockWidgetChart(props) {
    h.props[props.widget.id] = props;
    return <div data-testid="widget-chart" />;
  }
  MockWidgetChart.propTypes = {
    widget: PropTypes.shape({ id: PropTypes.string.isRequired }).isRequired,
  };
  return { default: MockWidgetChart };
});

const START = "2026-10-01T00:00:00.000Z";
const END = "2026-10-15T00:00:00.000Z";
const CUSTOM = { timePreset: "custom", customStart: START, customEnd: END };

const lastNavigation = () => {
  expect(h.navigate).toHaveBeenCalled();
  const [url] = h.navigate.mock.calls.at(-1);
  const parsed = new URL(url, "http://localhost");
  return { path: parsed.pathname, search: parsed.searchParams };
};

const expectCustomDates = (search) => {
  expect(search.get("timePreset")).toBe("custom");
  expect(search.get("customStart")).toBe(START);
  expect(search.get("customEnd")).toBe(END);
};

beforeEach(() => {
  vi.clearAllMocks();
  h.props = {};
  h.widgets = [
    {
      id: "w-1",
      name: "Tokens",
      position: 0,
      width: 6,
      height: 300,
      query_config: { metrics: [{ name: "tokens" }] },
    },
    {
      id: "w-2",
      name: "Cost",
      position: 1,
      width: 6,
      height: 300,
      query_config: { metrics: [{ name: "cost" }] },
    },
  ];
  window.history.replaceState(
    {},
    "",
    `/dashboard/dashboards/dash-1?${new URLSearchParams(CUSTOM).toString()}`,
  );
});

describe("DashboardDetailView — Custom dates across the editor (OBS-SW-168)", () => {
  it("restores the Custom range a return from the editor carries", () => {
    render(<DashboardDetailView />);

    expect(h.props["w-1"].globalDateRange).toEqual({ start: START, end: END });
    expect(h.props["w-2"].globalDateRange).toEqual({ start: START, end: END });
  });

  it("carries the Custom dates into a new widget", () => {
    render(<DashboardDetailView />);

    fireEvent.click(screen.getByRole("button", { name: "Add Widget" }));

    const { path, search } = lastNavigation();
    expect(path).toBe("/dashboard/dashboards/dash-1/widget/new");
    expectCustomDates(search);
  });

  it("carries the Custom dates into a new widget for a row", () => {
    render(<DashboardDetailView />);

    fireEvent.click(screen.getByRole("button", { name: "Add widget to row" }));

    const { path, search } = lastNavigation();
    expect(path).toBe("/dashboard/dashboards/dash-1/widget/new");
    expect(search.get("addAfter")).toBe("w-2");
    expectCustomDates(search);
  });

  it("carries the Custom dates into an existing widget", () => {
    render(<DashboardDetailView />);

    fireEvent.click(screen.getByText("Tokens"));

    const { path, search } = lastNavigation();
    expect(path).toBe("/dashboard/dashboards/dash-1/widget/w-1");
    expectCustomDates(search);
  });
});
