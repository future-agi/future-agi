import React from "react";
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import AnalyticsKpis from "../AnalyticsKpis";

// What the overview answers for a window with no traffic (the demo stack on
// 10-21: every figure 0, every trend -100).
const NO_TRAFFIC = {
  total_requests: { value: 0, trend: -100 },
  total_tokens: { value: 0, trend: -100 },
  total_cost: { value: "0", trend: -100 },
  avg_latency_ms: { value: 0, trend: -100 },
  p95_latency_ms: { value: 0, trend: -100 },
  error_rate: { value: 0, trend: -100 },
  cache_hit_rate: { value: 0, trend: -100 },
  active_models: { value: 0, trend: -100 },
};

function figure(kpis, label) {
  return kpis.getByText(label).nextSibling;
}

// Zero and "no data" are different. With no requests a
// count or a spend is a true 0, but a latency or a rate was never measured:
// it reads "—", never "0 ms" or "0.0%".
describe("Analytics KPIs with no requests", () => {
  it("shows — for latency, error rate and cache hit rate, and 0 for counts and spend", () => {
    render(
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <AnalyticsKpis overview={NO_TRAFFIC} />
      </ThemeProvider>,
    );
    const kpis = within(screen.getByRole("list", { name: "Key metrics" }));
    expect(figure(kpis, "Requests")).toHaveTextContent(/^0/);
    expect(figure(kpis, "Tokens")).toHaveTextContent(/^0/);
    expect(figure(kpis, "Cost")).toHaveTextContent(/^\$0\.00/);
    expect(figure(kpis, "Models used")).toHaveTextContent(/^0/);
    for (const label of [
      "Avg latency",
      "P95 latency",
      "Error rate",
      "Cache hit rate",
    ]) {
      expect(figure(kpis, label).textContent, label).toBe("—");
    }
    expect(screen.queryByText(/^0 ms/)).toBeNull();
    expect(screen.queryByText(/^0\.0%/)).toBeNull();
  });
});

// A red and green delta wall on a 17-request sample, and a
// cost cut off by noWrap. A delta shows only when both periods have at least
// 20 requests; only cost, latency and error rate are coloured (up is bad).
const LIGHT = createTheme({ palette: palette("light"), typography });

function renderKpis(overview, period = "24 hours") {
  render(
    <ThemeProvider theme={LIGHT}>
      <AnalyticsKpis overview={overview} period={period} />
    </ThemeProvider>,
  );
  return within(screen.getByRole("list", { name: "Key metrics" }));
}

const DELTA = /^[+-]\d+(\.\d)?%$/;

describe("Analytics KPI deltas", () => {
  it("shows no delta on a 17-request window, even against a busy previous one", () => {
    // 17 now, about 586 before: the demo's -97.1%.
    const kpis = renderKpis({
      total_requests: { value: 17, trend: -97.1 },
      total_tokens: { value: 900, trend: -96 },
      total_cost: { value: "0.000039", trend: -95 },
      avg_latency_ms: { value: 820, trend: 12 },
      p95_latency_ms: { value: 1900, trend: 30 },
      error_rate: { value: 0, trend: -100 },
      cache_hit_rate: { value: 5.9, trend: 40 },
      active_models: { value: 2, trend: -77.8 },
    });
    expect(kpis.queryAllByText(DELTA)).toHaveLength(0);
  });

  it("shows no delta when the previous window had under 20 requests", () => {
    // 300 now, 12 before (+2400%).
    const kpis = renderKpis({
      total_requests: { value: 300, trend: 2400 },
      total_cost: { value: "0.42", trend: 900 },
    });
    expect(kpis.queryAllByText(DELTA)).toHaveLength(0);
  });

  it("colours only cost, latency and error rate; counts read text.secondary", () => {
    // 500 now, 400 before.
    const kpis = renderKpis({
      total_requests: { value: 500, trend: 25 },
      total_tokens: { value: 90000, trend: 20 },
      total_cost: { value: "0.42", trend: 10 },
      avg_latency_ms: { value: 700, trend: -5 },
      error_rate: { value: 1.2, trend: -50 },
      cache_hit_rate: { value: 30, trend: 15 },
      active_models: { value: 4, trend: 33.3 },
    });
    const secondary = LIGHT.palette.text.secondary;
    expect(kpis.getByText("+25.0%")).toHaveStyle({ color: secondary });
    expect(kpis.getByText("+20.0%")).toHaveStyle({ color: secondary });
    expect(kpis.getByText("+15.0%")).toHaveStyle({ color: secondary });
    expect(kpis.getByText("+33.3%")).toHaveStyle({ color: secondary });
    // Up is bad for cost (error.dark), down is good for latency and errors
    // (green.700): both clear 4.5:1 on white, unlike error.main / success.main.
    expect(kpis.getByText("+10.0%")).toHaveStyle({
      color: LIGHT.palette.error.dark,
    });
    expect(kpis.getByText("-5.0%")).toHaveStyle({
      color: LIGHT.palette.green[700],
    });
    expect(kpis.getByText("-50.0%")).toHaveStyle({
      color: LIGHT.palette.green[700],
    });
  });

  it("names the period a delta compares with", async () => {
    const kpis = renderKpis(
      { total_requests: { value: 500, trend: 25 } },
      "7 days",
    );
    await userEvent.hover(kpis.getByText("+25.0%"));
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      "vs previous 7 days",
    );
  });

  it("lets a long value wrap instead of cutting it off", () => {
    const kpis = renderKpis({
      total_requests: { value: 500, trend: 25 },
      total_cost: { value: "0.0000390", trend: 10 },
    });
    const cost = figure(kpis, "Cost");
    expect(cost.className).not.toMatch(/noWrap/);
  });
});

// The gateway stores an unpriced request's cost as 0, so a
// Cost KPI over unpriced requests read "$0.00". All unpriced reads an em dash
// titled "No price found" (as a Logs row does); some reads a floor. A floor
// or an unknown has no period-over-period change.
describe("Analytics KPIs over requests with no price", () => {
  const busy = (cost) => ({
    ...NO_TRAFFIC,
    total_requests: { value: 600, trend: 20 },
    total_cost: cost,
  });
  const renderKpis = (overview) =>
    render(
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <AnalyticsKpis overview={overview} />
      </ThemeProvider>,
    );

  it("every request unpriced: the Cost reads an em dash titled No price found, never $0.00", () => {
    renderKpis(busy({ value: "0", trend: 0, unpriced_requests: 600 }));
    const kpis = within(screen.getByRole("list", { name: "Key metrics" }));
    const cost = figure(kpis, "Cost");
    expect(cost).toHaveTextContent(/^—$/);
    expect(within(cost).getByTitle("No price found")).toBeVisible();
  });

  it("some requests unpriced: the Cost reads the priced part as a floor, with no change shown", () => {
    renderKpis(busy({ value: "0.035", trend: 40, unpriced_requests: 3 }));
    const kpis = within(screen.getByRole("list", { name: "Key metrics" }));
    const cost = figure(kpis, "Cost");
    // Release-lead 595: a lower bound must not round $0.035 up.
    expect(cost).toHaveTextContent(/^≥\s\$0\.035$/);
    expect(
      within(cost).getByTitle(
        "3 of 600 requests had no price found, so this total counts only the priced ones.",
      ),
    ).toBeVisible();
  });
});

// The API answers trend null when there is no earlier period to compare
// with (no requests before, or a previous value of 0). That is shown as "—",
// never as a 0% change; 0.0% is shown only for a real 0.
describe("Analytics KPI change with no earlier period", () => {
  it("reads — for each change when there is no earlier period, never 0%", () => {
    const kpis = renderKpis({
      total_requests: { value: 33, trend: null },
      total_tokens: { value: 1200, trend: null },
      avg_latency_ms: { value: 300, trend: null },
      error_rate: { value: 12.12, trend: null },
    });
    expect(figure(kpis, "Requests").textContent).toBe("33—");
    expect(figure(kpis, "Tokens").textContent).toBe("1,200—");
    expect(figure(kpis, "Error rate").textContent).toBe("12.1%—");
    expect(kpis.queryByText(/^[+-]?0(\.0)?%$/)).toBeNull();
  });

  it("shows 0.0% only for a real 0 change, and — for a metric with none", () => {
    const kpis = renderKpis({
      total_requests: { value: 500, trend: 25 },
      avg_latency_ms: { value: 700, trend: 0 },
      error_rate: { value: 1.2, trend: null },
    });
    expect(kpis.getByText("0.0%")).toBeInTheDocument();
    expect(figure(kpis, "Error rate").textContent).toBe("1.2%—");
  });
});
