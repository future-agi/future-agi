import React, { forwardRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  DEMO_OVERVIEW,
  DEMO_RULE_STATS,
  MockAgGrid,
  fixedHeaders,
  renderPage,
} from "./__tests__/helpers";
import GuardrailAnalyticsTab from "./GuardrailAnalyticsTab";

const { get, chart } = vi.hoisted(() => ({
  get: vi.fn(),
  chart: { props: null },
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get } };
});
vi.mock("react-apexcharts", () => ({
  default: (props) => {
    chart.props = props;
    return <div data-testid="chart" />;
  },
}));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function Grid(props, _ref) {
    return <MockAgGrid {...props} />;
  }),
}));

// The API cut these days in the viewer's zone (it took `tz`).
const TRENDS = {
  granularity: "day",
  time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  series: [
    { timestamp: "2026-10-19T00:00:00+00:00", trigger_count: 0 },
    { timestamp: "2026-10-20T00:00:00+00:00", trigger_count: 43 },
  ],
};

const pipeline = vi.hoisted(() => ({ off: false }));
vi.mock("./hooks/useGuardrailsOff", () => ({
  useGuardrailsOff: () => pipeline.off,
}));

let fail;
let failPanel;
let ruleStats;
function answer(url) {
  if (fail || (failPanel && url.includes(failPanel))) return Promise.reject(new Error("down"));
  if (url.includes("guardrail-overview")) {
    return Promise.resolve({ data: { result: DEMO_OVERVIEW } });
  }
  if (url.includes("guardrail-rules")) {
    return Promise.resolve({ data: { result: ruleStats } });
  }
  if (url.includes("guardrail-trends")) {
    return Promise.resolve({ data: { result: TRENDS } });
  }
  return Promise.reject(new Error(`unexpected ${url}`));
}

describe("Guardrails > Analytics", () => {
  beforeEach(() => {
    fail = false;
    failPanel = null;
    pipeline.off = false;
    ruleStats = structuredClone(DEMO_RULE_STATS);
    get.mockReset();
    get.mockImplementation(answer);
  });

  it("shows the window's numbers without a latency the gateway never measures", async () => {
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    const strip = await screen.findByRole("group", { name: "Last 7 days" });
    expect(await within(strip).findByText("592")).toBeInTheDocument();
    expect(within(strip).getByText("43")).toBeInTheDocument();
    expect(within(strip).getByText("7.3%")).toBeInTheDocument();
    expect(within(strip).getByText("42")).toBeInTheDocument();
    expect(screen.queryByText(/latency|0ms/i)).toBeNull();
    // Every request in the window, not only those the rules read.
    expect(within(strip).getByText("Requests")).toBeInTheDocument();
    expect(within(strip).queryByText(/checked/i)).toBeNull();
  });

  it("retains historical counts, trends and the range picker while checks are off now", async () => {
    pipeline.off = true;
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    expect(
      await screen.findByText(/recorded history remains available/),
    ).toBeInTheDocument();
    const strip = await screen.findByRole("group", { name: "Last 7 days" });
    expect(await within(strip).findByText("592")).toBeInTheDocument();
    expect(await screen.findByTestId("row-pii-detection")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "24H" })).toBeEnabled();
    expect(chart.props.series).toEqual([{ name: "Matches", data: [0, 43] }]);
    expect(screen.queryByText(/latency|0ms/i)).toBeNull();
  });

  it("charts only the matches the trends endpoint returns, not empty blocked and warned lines", async () => {
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    await screen.findByTestId("chart");
    expect(chart.props.series).toEqual([{ name: "Matches", data: [0, 43] }]);
  });

  // The trend plots the viewer's zone, and says so.
  it("plots matches in the viewer's time zone and names it", async () => {
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    await screen.findByTestId("chart");
    expect(chart.props.options.xaxis.labels.datetimeUTC).toBe(false);
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const section = screen.getByRole("region", { name: "Matches over time" });
    expect(
      within(section).getByText(`Times are in ${zone}.`),
    ).toBeInTheDocument();
  });

  it("ranks rules in a grid with their share of matches", async () => {
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    const pii = await screen.findByTestId("row-pii-detection");
    expect(within(pii).getAllByText("24")).toHaveLength(2);
    expect(within(pii).getByText("55.8%")).toBeInTheDocument();
  });

  it("gives every number column the width its header needs, so none is cut off", async () => {
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    await screen.findByTestId("row-pii-detection");
    const headers = fixedHeaders(screen.getByTestId("ag-grid"));
    expect(headers.map((h) => h.label)).toContain("Share of matches");
    headers.forEach((header) =>
      expect(header.width, header.label).toBeGreaterThanOrEqual(header.needs),
    );
  });

  it("asks for the range it shows", async () => {
    const user = userEvent.setup();
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    await screen.findByTestId("row-pii-detection");
    await user.click(screen.getByRole("button", { name: "24H" }));
    expect(
      await screen.findByRole("group", { name: "Last 24 hours" }),
    ).toBeInTheDocument();
    const trendCalls = get.mock.calls.filter(([url]) =>
      url.includes("guardrail-trends"),
    );
    expect(trendCalls.at(-1)[1].params.granularity).toBe("hour");
  });

  // The gateway refuses an answer an After LLM check blocks with a 403, so a
  // block there is a block like any other: no "not enforced" note.
  it("counts a block on the model's answer as a block, with no not-enforced note", async () => {
    ruleStats = {
      rules: [
        ...DEMO_RULE_STATS.rules,
        {
          rule: "hallucination-detection",
          trigger_count: 2,
          block_count: 2,
          warn_count: 0,
        },
      ],
    };
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    const row = await screen.findByTestId("row-hallucination-detection");
    expect(within(row).getAllByText("2").length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/not enforced/i)).toBeNull();
    expect(screen.queryByText(/still returned/)).toBeNull();
  });

  it.each([
    ["guardrail-overview", "Retry overview"],
    ["guardrail-rules", "Retry rules"],
    ["guardrail-trends", "Retry trend"],
  ])("R3-CONTROLS-04 isolates a failed %s read and retries only that panel", async (panel, retryLabel) => {
    failPanel = panel;
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    const retry = await screen.findByRole("button", { name: retryLabel });
    expect(screen.queryByText("No requests in this window")).not.toBeInTheDocument();
    expect(screen.queryByText("No rule matched in this window")).not.toBeInTheDocument();
    if (panel !== "guardrail-rules") expect(await screen.findByTestId("row-pii-detection")).toBeInTheDocument();
    if (panel !== "guardrail-trends") expect(await screen.findByTestId("chart")).toBeInTheDocument();
    const otherReads = get.mock.calls.filter(([url]) => !url.includes(panel)).length;
    failPanel = null;
    await userEvent.click(retry);
    expect(await screen.findByTestId("row-pii-detection")).toBeInTheDocument();
    expect(get.mock.calls.filter(([url]) => !url.includes(panel))).toHaveLength(otherReads);
  });

  it("has an error state that is not an empty one", async () => {
    fail = true;
    renderPage(<GuardrailAnalyticsTab gatewayId="gw-1" />);
    expect(
      await screen.findByText("Guardrail analytics could not be loaded"),
    ).toBeInTheDocument();
  });
});
