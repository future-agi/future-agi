import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { render, screen, fireEvent } from "src/utils/test-utils";
import { createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import CostAnalytics from "./CostAnalytics";
import { VALUE_LABEL_ROOM } from "./barLabels";

const mockUseAnalyticsCost = vi.fn();
const chartSpy = vi.fn();

vi.mock("./hooks/useAnalyticsCost", () => ({
  useAnalyticsCost: (...args) => mockUseAnalyticsCost(...args),
}));

vi.mock("react-apexcharts", () => ({
  default: (props) => {
    chartSpy(props);
    return <div data-testid={`chart-${props.type}`} />;
  },
}));

describe("Cost group request links", () => {
  const start = "2026-11-09T01:02:03.456Z";
  const end = "2026-11-09T02:03:04.567Z";
  it.each([
    ["Model", "model", "vendor/model+v2", "vendor/model+v2"],
    ["Provider", "provider", "Future AGI", "Future AGI"],
    ["Billing mode", "billing_mode", "Future AGI credits", "managed"],
    ["Billing mode", "billing_mode", "Own key", "byok"],
    ["Application", "application", "checkout & café", "checkout & café"],
    ["Service", "service", "search/API?version=2", "search/API?version=2"],
    ["API key", "api_key_id", "gw-key-123", "gw-key-123"],
    ["User", "user_id", "customer/42 & café", "customer/42 & café"],
  ])(
    "opens the exact %s group in the displayed range",
    (label, key, name, expected) => {
      mockUseAnalyticsCost.mockReturnValue({
        isLoading: false,
        data: { total_cost: "1", breakdown: [{ name, total_cost: "1" }] },
      });
      render(<CostAnalytics start={start} end={end} gatewayId="gw-1" />);
      if (key !== "model")
        fireEvent.click(screen.getByRole("button", { name: label }));
      const link = screen.getByRole("link", {
        name: `View requests for ${name}`,
      });
      expect(link.tagName).toBe("A");
      expect(Object.fromEntries(new URL(link.href).searchParams)).toEqual({
        started_after: start,
        started_before: end,
        gateway_id: "gw-1",
        [key]: expected,
      });
    },
  );

  it.each(["Other", "Unknown"])("labels %s as a range-only link", (name) => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: { breakdown: [{ name, total_cost: "1" }] },
    });
    render(<CostAnalytics start={start} end={end} />);
    const link = screen.getByRole("link", { name: `View range for ${name}` });
    expect(link).toHaveTextContent("View range");
    expect(Object.fromEntries(new URL(link.href).searchParams)).toEqual({
      started_after: start,
      started_before: end,
    });
    expect(link.title).toMatch(/cannot be filtered exactly/);
  });

  it("uses controlled grouping and requests a URL-owned change without mutating it locally", () => {
    mockUseAnalyticsCost.mockReturnValue({ data: { breakdown: [] } });
    const onGroupByChange = vi.fn();
    render(
      <CostAnalytics
        start={start}
        end={end}
        groupBy="provider"
        onGroupByChange={onGroupByChange}
      />,
    );
    expect(mockUseAnalyticsCost).toHaveBeenLastCalledWith(
      expect.objectContaining({ groupBy: "provider" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Application" }));
    expect(onGroupByChange).toHaveBeenCalledWith("application");
    expect(screen.getByRole("button", { name: "Provider" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("never links previous-group placeholder data using the new grouping or range", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      isPlaceholderData: true,
      isFetching: true,
      data: {
        total_cost: "1",
        breakdown: [{ name: "old-model", total_cost: "1" }],
      },
    });
    render(<CostAnalytics start={start} end={end} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText(/spent in this range/)).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Updating Cost");
  });
});

describe("CostAnalytics", () => {
  beforeEach(() => {
    chartSpy.mockClear();
    mockUseAnalyticsCost.mockReset();
  });

  it("uses a safer donut configuration and filters out zero-cost slices", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "125.5",
        breakdown: [
          { name: "gpt-4o", total_cost: "100" },
          { name: "free-model", total_cost: "0" },
          { name: "gpt-4o-mini", total_cost: "25.5" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );

    const donutCall = chartSpy.mock.calls
      .map(([props]) => props)
      .find((props) => props.type === "donut");

    expect(donutCall).toBeDefined();
    expect(donutCall.series).toEqual([100, 25.5]);
    expect(donutCall.options.dataLabels.enabled).toBe(false);
    expect(donutCall.options.legend.position).toBe("bottom");
    expect(donutCall.options.plotOptions.pie.customScale).toBe(0.9);
    expect(donutCall.options.plotOptions.pie.donut.labels.name.show).toBe(
      false,
    );
  });

  // The cost breakdown drew a palette of its own (raw hex,
  // ten colours). It takes the gateway's series colours: the top 8 in FA
  // hues, Other in grey, and a model keeps its colour across bar and donut.
  it("colours the breakdown with the gateway's series hues, the top 8 and Other in grey", () => {
    const names = Array.from({ length: 8 }, (_, i) => `model-${i + 1}`);
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "45",
        breakdown: [
          ...names.map((name, i) => ({ name, total_cost: String(9 - i) })),
          { name: "Other", total_cost: "1" },
        ],
      },
    });
    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );
    expect(mockUseAnalyticsCost).toHaveBeenCalledWith(
      expect.objectContaining({ topN: 8 }),
    );
    const props = chartSpy.mock.calls.map(([p]) => p);
    const bar = props.find((p) => p.type === "bar");
    const donut = props.find((p) => p.type === "donut");
    expect(bar.options.colors).toHaveLength(9);
    expect(new Set(bar.options.colors).size).toBe(9);
    expect(donut.options.colors).toEqual(bar.options.colors);
    expect(bar.options.colors[0]).toBe("#7857FC");
    expect(bar.options.colors[8]).toBe("#666666");
    for (const raw of ["#2563EB", "#DC2626", "#059669", "#D97706"]) {
      expect(bar.options.colors).not.toContain(raw);
    }
  });

  it("shows empty state when all donut slices are zero", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "0",
        breakdown: [
          { name: "gpt-4o", total_cost: "0" },
          { name: "gpt-4o-mini", total_cost: "0" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );

    expect(screen.getByText("No cost data available.")).toBeInTheDocument();
  });

  it("groups cost by application when that option is picked", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "12",
        breakdown: [
          { name: "checkout", total_cost: "9" },
          { name: "search", total_cost: "3" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Application" }));

    expect(mockUseAnalyticsCost).toHaveBeenLastCalledWith(
      expect.objectContaining({ groupBy: "application" }),
    );
    expect(screen.getByText("Cost by Application")).toBeInTheDocument();
  });

  it("keeps the cost axis to a few short ticks so the labels never run together", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "0.9",
        breakdown: [
          { name: "checkout", total_cost: "0.8" },
          { name: "search", total_cost: "0.1" },
        ],
      },
    });

    render(
      <CostAnalytics start="2026-01-01" end="2026-01-02" gatewayId="gw_123" />,
    );

    const barCall = chartSpy.mock.calls
      .map(([props]) => props)
      .find((props) => props.type === "bar");
    const { tickAmount, labels } = barCall.options.xaxis;

    expect(tickAmount).toBe(5);
    expect([0, 0.05, 0.2, 1.5, 0.0005].map(labels.formatter)).toEqual([
      "$0.00",
      "$0.05",
      "$0.20",
      "$1.50",
      "$0.0005",
    ]);
  });
});

// The bars' values were white, so a
// short bar's ran onto the white plot ("$0.0", or nothing) in light mode,
// and the donut's total was dark grey on near-black in dark mode. A first fix's
// pill (ApexCharts' label background) drew in Chromium but not in Firefox,
// whose getBBox reads 0 there: no pill, so the text kept the paper colour
// (white on white) and a short bar's label ran into the axis ("0066"). A
// bar's value is now the bar's total label: text.primary of its own, just
// past the bar's end, with room kept right of the plot so the longest bar's
// label is never cut; no label background. The donut's figures take the
// theme's text colours.
describe("CostAnalytics value labels", () => {
  const BREAKDOWN = {
    isLoading: false,
    data: {
      total_cost: "0.094948",
      breakdown: [
        { name: "openai/gpt-4o-mini", total_cost: "0.082795" },
        { name: "openai/gpt-4o", total_cost: "0.01206" },
        { name: "o1-mini", total_cost: "0.000093" },
      ],
    },
  };
  const chartOf = (type) =>
    chartSpy.mock.calls.map(([p]) => p).find((p) => p.type === type);

  beforeEach(() => {
    chartSpy.mockClear();
    mockUseAnalyticsCost.mockReset();
    mockUseAnalyticsCost.mockReturnValue(BREAKDOWN);
  });

  for (const mode of ["light", "dark"]) {
    const theme = createTheme({ palette: palette(mode), typography });

    it(`writes each bar's value past its end in the text colour in ${mode} mode, with no label background`, () => {
      render(<CostAnalytics start="2026-01-01" end="2026-01-02" />, {
        theme,
      });
      const { chart, dataLabels, plotOptions, grid } = chartOf("bar").options;
      // No ApexCharts value label: its readability hung on a background
      // rect Firefox does not draw.
      expect(dataLabels.enabled).toBe(false);
      expect(dataLabels.background?.enabled).not.toBe(true);
      // The total label of a one-series stack: anchored at the bar's end,
      // never pulled back onto the bar or into the axis labels.
      expect(chart.stacked).toBe(true);
      const { total } = plotOptions.bar.dataLabels;
      expect(total).toMatchObject({ enabled: true });
      expect(total.offsetX).toBeGreaterThan(0);
      expect(total.style.color).toBe(theme.palette.text.primary);
      // Room for the widest value ("≥ $1,234.56") right of the longest bar.
      expect(grid.padding.right).toBeGreaterThanOrEqual(VALUE_LABEL_ROOM);
      // A cell's two significant figures, the exact value in the tooltip.
      expect(total.formatter(0.01206, { dataPointIndex: 1 })).toBe("$0.01206");
    });

    it(`draws the donut's total in the theme's text colours in ${mode} mode`, () => {
      render(<CostAnalytics start="2026-01-01" end="2026-01-02" />, {
        theme,
      });
      const { labels } = chartOf("donut").options.plotOptions.pie.donut;
      expect(labels.value.color).toBe(theme.palette.text.primary);
      expect(labels.total.color).toBe(theme.palette.text.secondary);
    });
  }
});

// An unpriced request is stored at
// cost 0, so a group of them read "$0.00" on its bar and every total read
// as if they were free. The API counts each group's and the range's
// unpriced requests: a group with no priced request reads "No price", one
// with some reads its priced part as a floor, and so does the range total.
describe("CostAnalytics over requests with no price", () => {
  const UNPRICED = {
    isLoading: false,
    data: {
      total_cost: "0.035",
      unpriced_requests: 3,
      breakdown: [
        {
          name: "gpt-4o-mini",
          total_cost: "0.03",
          request_count: 2,
          unpriced_requests: 0,
        },
        {
          name: "gpt-4o",
          total_cost: "0.01206",
          request_count: 2,
          unpriced_requests: 1,
        },
        {
          name: "o1-mini",
          total_cost: "0",
          request_count: 2,
          unpriced_requests: 2,
        },
      ],
    },
  };
  const chartOf = (type) =>
    chartSpy.mock.calls.map(([p]) => p).findLast((p) => p.type === type);

  beforeEach(() => {
    chartSpy.mockClear();
    mockUseAnalyticsCost.mockReset();
    mockUseAnalyticsCost.mockReturnValue(UNPRICED);
  });

  it("labels a group's bar No price, or its priced part as a floor, never $0.00", () => {
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { plotOptions, tooltip } = chartOf("bar").options;
    const label = plotOptions.bar.dataLabels.total.formatter;
    const at = (i) => ({ dataPointIndex: i });
    expect(label(0.03, at(0))).toBe("$0.03");
    // Bound labels and tooltips preserve sub-dollar amounts.
    expect(label(0.01206, at(1))).toBe("≥ $0.01206");
    expect(label(0, at(2))).toBe("No price");
    expect(tooltip.y.formatter(0, at(2))).toBe("No price found (2 requests)");
    expect(tooltip.y.formatter(0.01206, at(1))).toBe(
      "≥ $0.01206 (1 of 2 requests had no price found)",
    );
  });

  it("reads the range's total as a floor and says how many requests had no price", () => {
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const total = screen.getByText(/spent in this range/).closest("p");
    expect(total).toHaveTextContent(
      /^≥\s\$0\.035 spent in this range, 3 of 6 requests had no price found$/,
    );
    const { labels } = chartOf("donut").options.plotOptions.pie.donut;
    expect(
      labels.total.formatter({ globals: { seriesTotals: [0.03, 0.01206] } }),
    ).toBe("≥ $0.04206");
  });

  it("says no price was found when no request in the range had one", () => {
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "0",
        unpriced_requests: 2,
        breakdown: [
          {
            name: "o1-mini",
            total_cost: "0",
            request_count: 2,
            unpriced_requests: 2,
          },
        ],
      },
    });
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    expect(
      screen.getByText("No price found for the 2 requests in this range"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\$0\.00/)).toBeNull();
    expect(
      screen.getByText("No price found for the requests in this range."),
    ).toBeInTheDocument();
  });
});

// A group or range whose priced part is 0 read "≥ $0.00".
describe("CostAnalytics when the priced part is 0", () => {
  beforeEach(() => {
    chartSpy.mockClear();
    mockUseAnalyticsCost.mockReset();
    mockUseAnalyticsCost.mockReturnValue({
      isLoading: false,
      data: {
        total_cost: "0",
        unpriced_requests: 20,
        breakdown: [
          {
            name: "o1-mini",
            total_cost: "0",
            request_count: 30,
            unpriced_requests: 20,
          },
        ],
      },
    });
  });

  it("reads No price, never ≥ $0.00, on the bar, its tooltip and the range", () => {
    render(<CostAnalytics start="2026-01-01" end="2026-01-02" />);
    const { plotOptions, tooltip } = chartSpy.mock.calls
      .map(([p]) => p)
      .findLast((p) => p.type === "bar").options;
    const at = { dataPointIndex: 0 };
    expect(plotOptions.bar.dataLabels.total.formatter(0, at)).toBe("No price");
    expect(tooltip.y.formatter(0, at)).toBe(
      "No price found for 20 of 30 requests",
    );
    expect(
      screen.getByText("No price found for 20 of 30 requests in this range"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/\$0\.00/)).toBeNull();
  });
});
