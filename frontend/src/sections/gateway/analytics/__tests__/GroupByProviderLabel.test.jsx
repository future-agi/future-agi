import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";

import UsageCharts from "../UsageCharts";
import CostAnalytics from "../CostAnalytics";
import ErrorAnalytics from "../ErrorAnalytics";

// Group by Provider puts managed calls under "Future AGI managed". A grouped
// answer carries no billing field per group, so the API names the groups
// from each request's billing_mode and the console draws them as named: it
// never renames a group from a provider's name. The org's own OpenRouter
// key's group keeps "openrouter".

vi.mock("ag-grid-react", () => import("../../logs/__tests__/fakeAgGrid"));

const charts = vi.hoisted(() => ({ props: [] }));
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    charts.props.push(props);
    return <div data-testid={`chart-${props.type}`} />;
  },
}));

const mocks = vi.hoisted(() => ({
  usage: vi.fn(),
  cost: vi.fn(),
  errors: vi.fn(),
}));
vi.mock("../hooks/useAnalyticsUsage", () => ({
  useAnalyticsUsage: (...args) => mocks.usage(...args),
}));
vi.mock("../hooks/useAnalyticsCost", () => ({
  useAnalyticsCost: (...args) => mocks.cost(...args),
}));
vi.mock("../hooks/useAnalyticsErrors", () => ({
  useAnalyticsErrors: (...args) => mocks.errors(...args),
}));

const MANAGED = "Future AGI managed";
const OWN_OPENROUTER = "openrouter";
const RANGE = {
  start: "2026-10-21T00:00:00Z",
  end: "2026-10-22T00:00:00Z",
  granularity: "hour",
};

const point = (bucket, requests) => ({
  bucket,
  request_count: requests,
  total_tokens: requests * 10,
});

const lastChart = (predicate) => charts.props.filter(predicate).at(-1);

beforeEach(() => {
  charts.props = [];
  mocks.usage.mockReset();
  mocks.cost.mockReset();
  mocks.errors.mockReset();
  mocks.usage.mockImplementation(({ groupBy }) => ({
    isLoading: false,
    data: groupBy
      ? {
          granularity: "hour",
          groups: {
            [MANAGED]: [point("2026-10-21T10:00:00Z", 6)],
            [OWN_OPENROUTER]: [point("2026-10-21T10:00:00Z", 3)],
          },
        }
      : { granularity: "hour", series: [point("2026-10-21T10:00:00Z", 9)] },
  }));
  mocks.cost.mockReturnValue({
    isLoading: false,
    data: {
      total_cost: "3",
      breakdown: [
        { name: MANAGED, total_cost: "2", request_count: 6 },
        { name: OWN_OPENROUTER, total_cost: "1", request_count: 3 },
      ],
    },
  });
  mocks.errors.mockReturnValue({
    isLoading: false,
    data: {
      total_requests: 9,
      total_errors: 3,
      overall_error_rate: 33.3,
      breakdown: [
        { name: MANAGED, error_count: 2, error_rate: 33.3 },
        { name: OWN_OPENROUTER, error_count: 1, error_rate: 33.3 },
      ],
      error_timeseries: [],
    },
  });
});

describe("Group by Provider names managed calls Future AGI managed", () => {
  it("Usage draws the API's groups as named", () => {
    render(<UsageCharts {...RANGE} />);
    fireEvent.click(screen.getByRole("button", { name: "Provider" }));

    expect(mocks.usage).toHaveBeenLastCalledWith(
      expect.objectContaining({ groupBy: "provider" }),
    );
    const requests = lastChart((p) => p.type === "area");
    expect(requests.series.map((s) => s.name)).toEqual([
      MANAGED,
      OWN_OPENROUTER,
    ]);
  });

  it("Cost labels its bars and slices with the API's groups", () => {
    render(<CostAnalytics start={RANGE.start} end={RANGE.end} />);
    fireEvent.click(screen.getByRole("button", { name: "Provider" }));

    expect(mocks.cost).toHaveBeenLastCalledWith(
      expect.objectContaining({ groupBy: "provider" }),
    );
    expect(screen.getByText("Cost by Provider")).toBeInTheDocument();
    const bar = lastChart((p) => p.type === "bar");
    expect(bar.options.xaxis.categories).toEqual([MANAGED, OWN_OPENROUTER]);
    const donut = lastChart((p) => p.type === "donut");
    expect(donut.options.labels).toEqual([MANAGED, OWN_OPENROUTER]);
  });

  it("Errors lists the API's groups as named", () => {
    render(<ErrorAnalytics {...RANGE} />);
    fireEvent.click(screen.getByRole("button", { name: "Provider" }));

    expect(mocks.errors).toHaveBeenLastCalledWith(
      expect.objectContaining({ groupBy: "provider" }),
    );
    const rows = within(screen.getByTestId("ag-grid"))
      .getAllByRole("row")
      .slice(1);
    expect(rows[0]).toHaveTextContent(MANAGED);
    expect(rows[1]).toHaveTextContent(OWN_OPENROUTER);
    expect(rows[1]).not.toHaveTextContent(MANAGED);
  });
});
