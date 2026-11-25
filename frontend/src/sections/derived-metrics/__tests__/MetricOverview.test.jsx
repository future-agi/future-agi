import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "src/utils/test-utils";
import MetricOverview from "../MetricOverview";
import { formatMetricDifference } from "../metricPageValues";
import MetricStatusLine from "../MetricStatusLine";
import { formatMetricWindow } from "../metricTime";
import { metric, kpi, trend, scope, window } from "./metricPageFixtures";
const mock = vi.hoisted(() => ({ kpi: {}, trend: {} }));
vi.mock("../api", () => ({
  useMetricKpi: () => mock.kpi,
  useMetricTrend: () => mock.trend,
}));
vi.mock("react-apexcharts", () => ({ default: () => <div /> }));
beforeEach(() => {
  mock.kpi = { readState: "ready", data: structuredClone(kpi) };
  mock.trend = { readState: "ready", data: structuredClone(trend) };
});
afterEach(cleanup);
const mount = () =>
  render(<MetricOverview metric={metric} scope={scope} window={window} />);

it("formats sampled totals as estimates in their declared units, not percent", () => {
  mock.kpi.data.current = {
    ...kpi.current,
    value: 4582,
    rate_eligible: false,
    value_format: "number",
    unit: "tokens",
    uncertainty: {
      status: "ok",
      ci_low: 4300,
      ci_high: 5010,
      confidence_level: 0.95,
    },
  };
  mock.kpi.data.previous = { ...mock.kpi.data.current, value: 4000 };
  mock.kpi.data.delta = 582;
  mount();
  expect(screen.getByTestId("metric-hero")).toHaveTextContent("≈ 4,582 tokens");
  expect(
    screen.getAllByText("95% interval: 4,300 tokens – 5,010 tokens").length,
  ).toBeGreaterThan(0);
  expect(screen.getByText("+582 tokens vs previous period")).toBeVisible();
  expect(screen.queryByText(/±/)).not.toBeInTheDocument();
});

it("does not turn an arbitrary ratio into a percentage", () => {
  mock.kpi.data.current = {
    ...kpi.current,
    value: 0.125,
    rate_eligible: false,
    value_format: "percent",
    uncertainty: { status: "unavailable", reason: "mixed_design" },
  };
  mount();
  expect(screen.getByTestId("metric-hero")).toHaveTextContent("≈ 0.125");
  expect(screen.getByTestId("metric-hero")).not.toHaveTextContent("%");
  expect(
    screen.getByText("Interval unavailable: mixed sampling designs"),
  ).toBeVisible();
});

it("states the window and rounding once, under the trend only when the KPI read failed", () => {
  const line = `${formatMetricWindow(kpi.effective_window)} · ${kpi.effective_window.rounding}`;
  mount();
  expect(screen.getAllByText(line)).toHaveLength(1);
  cleanup();
  mock.kpi = {
    readState: "unavailable",
    error: { statusCode: 503, retryable: true },
  };
  mount();
  expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
  expect(screen.getAllByText(line)).toHaveLength(1);
});

it("keeps the window line out of the trend while the KPI read is still loading", () => {
  const line = `${formatMetricWindow(kpi.effective_window)} · ${kpi.effective_window.rounding}`;
  // The trend arrived first: the line waits for the KPI card instead of
  // appearing under the trend and then jumping up.
  mock.kpi = { readState: "loading" };
  mount();
  expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "View data table" })).toBeVisible();
  expect(screen.queryByText(line)).not.toBeInTheDocument();
});

it("keeps a missing previous window unavailable without inventing a difference", () => {
  mock.kpi.data.previous = null;
  mock.kpi.data.delta = null;
  mount();
  expect(screen.getByText("Previous period unavailable")).toBeVisible();
  expect(screen.getByText("Comparison unavailable")).toBeVisible();
});

it("shows the actual asymmetric difference interval without claiming significance", () => {
  mock.kpi.data.delta_uncertainty = {
    status: "ok",
    ci_low: -0.02,
    ci_high: 0.18,
  };
  mount();
  expect(
    screen.getByText(
      "Difference interval: -2 percentage points to +18 percentage points",
    ),
  ).toBeVisible();
  expect(screen.queryByText(/significant/)).not.toBeInTheDocument();
});

it.each([
  [
    "metric_schema_not_installed",
    "Metrics schema not installed. An administrator needs to install it before metrics can be calculated or read.",
  ],
  ["user_reads_unavailable", "User-level metrics are not available yet."],
  [
    "window_not_calculated",
    "This window has not been calculated. Choose a later start date.",
  ],
  [
    "metric_recalculating",
    "Not calculated yet. A completed active definition is not available.",
  ],
  [
    "metric_version_changed",
    "The active definition changed. Reload the metric to read the new version.",
  ],
])("renders semantic read error %s without a KPI", (code, text) => {
  mock.kpi = { readState: "unavailable", error: { code, retryable: false } };
  mount();
  expect(screen.getByText(text)).toBeVisible();
  expect(screen.queryByTestId("metric-hero")).not.toBeInTheDocument();
});

it("does not use definition updated_at as committed freshness", () => {
  render(
    <MetricStatusLine
      definition={{
        ...metric.definition,
        updated_at: new Date().toISOString(),
      }}
    />,
  );
  expect(screen.getByText("Not calculated yet")).toBeVisible();
  expect(screen.queryByText(/^Updated /)).not.toBeInTheDocument();
});

it("does not claim a committed value is uncalculated when its freshness timestamp is missing", () => {
  render(
    <MetricStatusLine
      provenance={{ ...kpi.provenance, updated_at: null }}
      definition={metric.definition}
    />,
  );
  expect(screen.getByText("Update time unavailable")).toBeVisible();
  expect(screen.queryByText("Not calculated yet")).not.toBeInTheDocument();
});

it("reports actual partial progress and failed recalculation, never a completion promise", () => {
  render(
    <MetricStatusLine
      provenance={{
        ...kpi.provenance,
        backfill_days_done: 12,
        backfill_days_total: 30,
      }}
      definition={{ backfill_status: "failed" }}
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent(
    "History recalculation failed",
  );
  expect(
    screen.getByText("Recalculating history: 12 of 30 days"),
  ).toBeVisible();
  expect(screen.getByRole("progressbar")).toHaveAttribute(
    "aria-valuenow",
    "40",
  );
});

it.each([null, undefined, NaN, Infinity])(
  "does not format missing/nonfinite difference %s as zero",
  (value) => {
    expect(formatMetricDifference(value, kpi.current)).toBeNull();
  },
);
it("retains a real zero difference", () =>
  expect(formatMetricDifference(0, kpi.current)).toBe("0 percentage points"));
