import process from "node:process";
import React from "react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { render } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";

// The alert chart's time axis reads the browser's local time. The graph sends
// its static buckets without an offset, meaning UTC. The chart draws each
// bucket at that instant and prints its axis labels and its tooltip's time on
// the browser's clock, so the same buckets read 05:25 to 07:15 in UTC and
// 10:55 to 12:45 in Kolkata.

// The same graph answer in every zone: buckets from 05:25 to 07:15 UTC.
const BUCKETS = [
  { timestamp: "2026-11-12T05:25:00", value: 1 },
  { timestamp: "2026-11-12T05:55:00", value: 2 },
  { timestamp: "2026-11-12T06:25:00", value: 1 },
  { timestamp: "2026-11-12T06:55:00", value: 3 },
  { timestamp: "2026-11-12T07:15:00", value: 1 },
];
const FIRST_BUCKET = Date.UTC(2026, 10, 12, 5, 25, 0);
const NEWEST_BUCKET = Date.UTC(2026, 10, 12, 7, 15, 0);
// A percentage-change graph sends its buckets with a +00:00 offset.
const OFFSET_BUCKETS = BUCKETS.map((bucket) => ({
  ...bucket,
  timestamp: `${bucket.timestamp}+00:00`,
}));
// The alert last fired at 07:22:30 UTC and the graph was read at 07:23:20,
// so the read minute ends at 07:24.
const LAST_FIRED = "2026-11-12T07:22:30Z";
const READ_AT = Date.UTC(2026, 10, 12, 7, 23, 20);
const READ_MINUTE_END = Date.UTC(2026, 10, 12, 7, 24, 0);
const TOOLTIP_TIME_FORMAT = "dd MMM HH:mm";

const ZONES = [
  {
    zone: "UTC",
    offsetMinutes: 0,
    firstLabel: "05:25",
    newestLabel: "07:15",
    axisEnd: READ_MINUTE_END,
  },
  {
    zone: "Asia/Kolkata",
    offsetMinutes: -330,
    firstLabel: "10:55",
    newestLabel: "12:45",
    axisEnd: READ_MINUTE_END,
  },
  {
    // The read comes late in the evening here. The preset range ends at the
    // local end of the day, which the server reads as UTC, before these
    // buckets; the axis still runs to the newest one.
    zone: "America/Los_Angeles",
    offsetMinutes: 480,
    firstLabel: "21:25",
    newestLabel: "23:15",
    axisEnd: NEWEST_BUCKET,
  },
];

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  chart: vi.fn(() => null),
  sheet: {
    handleViewTrace: vi.fn(),
    alertRuleDetails: null,
    refreshGrid: vi.fn(),
    setAlertRuleDetails: vi.fn(),
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { get: mocks.get },
}));
vi.mock("src/utils/Mixpanel", async (importOriginal) => ({
  ...(await importOriginal()),
  trackEvent: vi.fn(),
}));
vi.mock("src/components/svg-color", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock(
  "src/sections/projects/LLMTracing/TracingControls",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock("../components/AlertsChart", async (importOriginal) => ({
  ...(await importOriginal()),
  default: mocks.chart,
}));
vi.mock("../components/AlertSettingsForm", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../components/DuplicateALert", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock("../components/AlertsSheetView/Issues", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => null,
}));
vi.mock(
  "../components/AlertsSheetView/AlertDetails",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => null,
  }),
);
vi.mock("../useMuteAlerMutation", async (importOriginal) => ({
  ...(await importOriginal()),
  useMuteAlertsMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../store/useAlertStore", async (importOriginal) => ({
  ...(await importOriginal()),
  useAlertStore: () => ({
    alertType: "evaluation_metrics",
    openCreateAlerts: false,
    openSheetView: "alert-1",
    handleCloseSheetView: vi.fn(),
    handleProjectChange: vi.fn(),
    handleStartCreatingAlerts: vi.fn(),
    handleChangeAlertType: vi.fn(),
    setCurrentTab: vi.fn(),
    setOpenCreateAlerts: vi.fn(),
    mainPage: false,
    refreshGrid: vi.fn(),
  }),
}));
vi.mock("../store/useAlertSheetView", async (importOriginal) => ({
  ...(await importOriginal()),
  useAlertSheetView: () => mocks.sheet,
}));

import { getDefaultDateRange } from "../common";
import AlertConfiguration from "../components/AlertConfiguration";
import AlertsSheetView from "../components/AlertsSheetView/AlertsSheetView";

// The sheet's back button is the only Iconify glyph left once the chart,
// issues, details and form are stubbed.
for (const name of ["solar:arrow-left-linear"]) {
  registerOfflineIcon(name, bundled[name]);
}

const lastChartProps = () => mocks.chart.mock.calls.at(-1)?.[0];
// The drawn line's points. A percentage-change chart puts a hidden band
// first, so the line is the last series.
const drawnLine = () => lastChartProps()?.series?.at(-1)?.data ?? [];

// The hours and minutes ApexCharts prints for an instant when its labels are
// not in UTC: the browser's own clock.
const localClock = (instant) => {
  const date = new Date(instant);
  const pad = (number) => String(number).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

// Freezes the clock (Date only, so waitFor keeps real timers) at the graph
// read and serves the given graph result for every read of the alert.
function primeAlert({ thresholdType = "static", result }) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(READ_AT);
  mocks.get.mockReset();
  mocks.get.mockResolvedValue({ data: { result } });
  mocks.chart.mockClear();
  mocks.sheet.alertRuleDetails = {
    id: "alert-1",
    name: "Judge fails",
    project: "project-1",
    metricType: "evaluation_metrics",
    thresholdType,
    thresholdOperator: "greater_than",
    criticalThresholdValue: 0.5,
    warningThresholdValue: 0.2,
    lastTriggered: LAST_FIRED,
  };
}

function renderWithClient(ui) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return client;
}

const waitForLine = () =>
  waitFor(() => expect(drawnLine()).toHaveLength(BUCKETS.length));

// The normal Vitest fork pool applies a TZ change. Every case checks the
// effective zone first, so a runner that ignores TZ fails instead of passing
// in UTC.
describe.each(ZONES)(
  "alert chart with the browser in $zone",
  ({ zone, offsetMinutes, firstLabel, newestLabel, axisEnd }) => {
    let previousZone;

    beforeAll(() => {
      previousZone = process.env.TZ;
      process.env.TZ = zone;
    });

    afterAll(() => {
      if (previousZone === undefined) delete process.env.TZ;
      else process.env.TZ = previousZone;
    });

    beforeEach(() => {
      expect(new Date(READ_AT).getTimezoneOffset()).toBe(offsetMinutes);
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    describe("in the alert sheet", () => {
      it("draws the first and newest buckets at their UTC instants", async () => {
        primeAlert({ result: BUCKETS });
        renderWithClient(<AlertsSheetView />);

        await waitForLine();
        expect(drawnLine()[0].x).toBe(FIRST_BUCKET);
        expect(drawnLine().at(-1).x).toBe(NEWEST_BUCKET);
      });

      it("prints the axis labels and the tooltip time in the browser's local time", async () => {
        primeAlert({ result: BUCKETS });
        renderWithClient(<AlertsSheetView />);

        await waitForLine();
        const { options } = lastChartProps();
        expect(options.xaxis?.labels?.datetimeUTC).toBe(false);
        expect(options.xaxis?.labels?.formatter).toBeUndefined();
        expect(options.xaxis?.labels?.style?.fontSize).toBe("12px");
        expect(options.tooltip?.x?.format).toBe(TOOLTIP_TIME_FORMAT);
        expect(options.tooltip?.x?.formatter).toBeUndefined();
        expect(localClock(drawnLine()[0].x)).toBe(firstLabel);
        expect(localClock(drawnLine().at(-1).x)).toBe(newestLabel);
      });

      it("ends the axis at the read minute's end within the selected range, never before the newest bucket", async () => {
        primeAlert({ result: BUCKETS });
        renderWithClient(<AlertsSheetView />);

        await waitForLine();
        const { options } = lastChartProps();
        expect(options.xaxis?.max).toBe(axisEnd);
        expect(options.xaxis?.max).toBeGreaterThanOrEqual(NEWEST_BUCKET);
        expect(options.xaxis?.type).toBe("datetime");
      });

      it("prints a percentage-change chart in local time too, with its buckets at their instants", async () => {
        primeAlert({
          thresholdType: "percentage_change",
          result: { graph_data: OFFSET_BUCKETS, alert_bar_data: [] },
        });
        renderWithClient(<AlertsSheetView />);

        await waitForLine();
        const { options } = lastChartProps();
        expect(drawnLine()[0].x).toBe(FIRST_BUCKET);
        expect(drawnLine().at(-1).x).toBe(NEWEST_BUCKET);
        expect(options.xaxis?.labels?.datetimeUTC).toBe(false);
        expect(options.xaxis?.labels?.formatter).toBeUndefined();
        expect(options.tooltip?.x?.format).toBe(TOOLTIP_TIME_FORMAT);
        expect(options.tooltip?.enabledOnSeries).toEqual([1]);
        expect(localClock(drawnLine()[0].x)).toBe(firstLabel);
        expect(options.xaxis?.max).toBe(axisEnd);
      });
    });

    describe("in the edit drawer's saved graph", () => {
      it("draws the buckets at their UTC instants and prints the browser's local time", async () => {
        primeAlert({ result: BUCKETS });
        renderWithClient(
          <AlertConfiguration
            dateFilter={getDefaultDateRange()}
            setDateFilter={vi.fn()}
          />,
        );

        await waitForLine();
        const { options } = lastChartProps();
        expect(drawnLine()[0].x).toBe(FIRST_BUCKET);
        expect(drawnLine().at(-1).x).toBe(NEWEST_BUCKET);
        expect(options.xaxis?.labels?.datetimeUTC).toBe(false);
        expect(options.xaxis?.labels?.style?.fontSize).toBe("12px");
        expect(options.tooltip?.x?.format).toBe(TOOLTIP_TIME_FORMAT);
        expect(localClock(drawnLine()[0].x)).toBe(firstLabel);
      });
    });
  },
);
