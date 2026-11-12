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
import { act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { render } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";

// The alert detail chart's time axis has to reach the last firing and the
// time of the graph read. The graph endpoint returns only the buckets that
// hold data, so an axis fitted to the points stopped at the newest bucket
// (12:29:50) and left out the 12:34 firing.

const NOW = Date.UTC(2026, 10, 11, 12, 35, 0);
const LAST_FIRED = "2026-11-11T12:34:10Z";
const BUCKETS = [
  { timestamp: "2026-11-11T12:25:10Z", value: 1 },
  { timestamp: "2026-11-11T12:27:30Z", value: 1 },
  { timestamp: "2026-11-11T12:29:50Z", value: 1 },
];
const NEWEST_BUCKET = Date.parse("2026-11-11T12:29:50Z");

// The static graph sends its buckets without an offset, meaning UTC.
const OFFSET_LESS_BUCKETS = [
  { timestamp: "2026-11-11T12:25:10", value: 1 },
  { timestamp: "2026-11-11T12:27:30", value: 1 },
  { timestamp: "2026-11-11T12:29:50", value: 1 },
];
// A graph read between whole minutes, and the next poll in the same minute.
const READ_AT = Date.UTC(2026, 10, 11, 12, 34, 40);
const NEXT_POLL = READ_AT + 10 * 1000;
// From the newest bucket (12:29:50) to the end of the read minute (12:35:00).
const NEWEST_TO_READ_MINUTE_END = 5 * 60 * 1000 + 10 * 1000;

// Where the chart draws a UTC wall-clock time when its points carry no
// offset: the same text read as browser-local time, as new Date() reads it.
const onChart = (wallClock) => new Date(wallClock).getTime();
// Where the chart draws a true instant when its points carry no offset.
const onChartAt = (instant) => onChart(instant.slice(0, 19));

// A custom range is a run of the browser's local calendar days, sent the way
// the date picker sends it: the start of each day as an instant with a Z.
const ZONES = [
  {
    zone: "Asia/Kolkata",
    offsetMinutes: -330,
    // Today, 2026-11-11 in Kolkata. The graph was read at 01:25 local and the
    // alert fired at 01:28, before the next poll, so the firing comes after
    // the read and early in the range.
    customToday: {
      buckets: [
        { timestamp: "2026-11-10T19:40:00", value: 1 },
        { timestamp: "2026-11-10T19:45:00", value: 1 },
        { timestamp: "2026-11-10T19:50:00", value: 1 },
      ],
      options: {
        dateRange: ["2026-11-10T18:30:00.000Z", "2026-11-11T18:30:00.000Z"],
        readAt: Date.UTC(2026, 10, 10, 19, 55, 20),
        lastTriggered: "2026-11-10T19:58:00Z",
      },
      axisEnd: "2026-11-10T19:58:00Z",
    },
    // 2026-11-08 and 2026-11-09 in Kolkata.
    customPast: {
      buckets: [
        { timestamp: "2026-11-09T11:50:00", value: 1 },
        { timestamp: "2026-11-09T12:00:00", value: 1 },
      ],
      dateRange: ["2026-11-07T18:30:00.000Z", "2026-11-09T18:30:00.000Z"],
    },
  },
  {
    zone: "America/New_York",
    offsetMinutes: 300,
    // Today, 2026-11-11 in New York. The alert fired at 20:58 local and the
    // graph was read at 21:00, late in the range.
    customToday: {
      buckets: [
        { timestamp: "2026-11-12T01:40:00", value: 1 },
        { timestamp: "2026-11-12T01:45:00", value: 1 },
        { timestamp: "2026-11-12T01:50:00", value: 1 },
      ],
      options: {
        dateRange: ["2026-11-11T05:00:00.000Z", "2026-11-12T05:00:00.000Z"],
        readAt: Date.UTC(2026, 10, 12, 2, 0, 20),
        lastTriggered: "2026-11-12T01:58:00Z",
      },
      axisEnd: "2026-11-12T02:01:00Z",
    },
    // 2026-11-08 and 2026-11-09 in New York.
    customPast: {
      buckets: [
        { timestamp: "2026-11-10T03:50:00", value: 1 },
        { timestamp: "2026-11-10T04:00:00", value: 1 },
      ],
      dateRange: ["2026-11-08T05:00:00.000Z", "2026-11-10T05:00:00.000Z"],
    },
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

import { endpoints } from "src/utils/axios";
import { getAlertChartXAxisRange } from "../common";
import AlertsSheetView from "../components/AlertsSheetView/AlertsSheetView";

// The sheet's back button is the only Iconify glyph left once the chart,
// issues and details are stubbed.
for (const name of ["solar:arrow-left-linear"]) {
  registerOfflineIcon(name, bundled[name]);
}

const lastChartProps = () => mocks.chart.mock.calls.at(-1)?.[0];

// Freezes the clock (Date only, so waitFor keeps real timers) and serves the
// given buckets for every graph read of a static-threshold alert.
function primeSheet({ now, buckets }) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(now);
  mocks.get.mockReset();
  mocks.get.mockResolvedValue({ data: { result: buckets } });
  mocks.chart.mockClear();
  mocks.sheet.alertRuleDetails = {
    id: "alert-1",
    name: "Judge fails",
    project: "project-1",
    metricType: "evaluation_metrics",
    thresholdOperator: "greater_than",
    criticalThresholdValue: 0.5,
    warningThresholdValue: 0.2,
    lastTriggered: LAST_FIRED,
  };
}

function renderSheet() {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <AlertsSheetView />
    </QueryClientProvider>,
  );
  return client;
}

describe("getAlertChartXAxisRange", () => {
  const sevenDays = ["2026-11-04T12:35:00Z", "2026-11-12T00:00:00Z"];

  it("ends at the read time when the last firing came after the newest bucket", () => {
    const range = getAlertChartXAxisRange(
      { result: BUCKETS },
      { dateRange: sevenDays, lastTriggered: LAST_FIRED, readAt: NOW },
    );

    expect(range).toEqual({ max: NOW });
    expect(range.max).toBeGreaterThanOrEqual(Date.parse(LAST_FIRED));
    expect(range.max).toBeGreaterThan(NEWEST_BUCKET);
  });

  it("still covers the firing when the read time is behind it", () => {
    const readAt = Date.UTC(2026, 10, 11, 12, 33, 0);
    const range = getAlertChartXAxisRange(
      { result: BUCKETS },
      { dateRange: sevenDays, lastTriggered: LAST_FIRED, readAt },
    );

    expect(range).toEqual({ max: Date.parse(LAST_FIRED) });
  });

  it("stops at the end of a past range and leaves out a later firing", () => {
    const pastRange = ["2026-11-08 00:00:00", "2026-11-10 00:00:00"];
    const range = getAlertChartXAxisRange(
      { result: [{ timestamp: "2026-11-09T00:00:00Z", value: 1 }] },
      { dateRange: pastRange, lastTriggered: LAST_FIRED, readAt: NOW },
    );

    // The server reads the range's offset-less text as UTC.
    expect(range).toEqual({ max: Date.parse("2026-11-10T00:00:00Z") });
    expect(range.max).toBeLessThan(Date.parse(LAST_FIRED));
  });

  it("reads the percentage-change graph points too", () => {
    const range = getAlertChartXAxisRange(
      { result: { graph_data: BUCKETS, alert_bar_data: [] } },
      { dateRange: sevenDays, lastTriggered: LAST_FIRED, readAt: NOW },
    );

    expect(range).toEqual({ max: NOW });
  });

  it("never ends before the newest point", () => {
    const newest = "2026-11-11T12:36:00Z";
    const range = getAlertChartXAxisRange(
      { result: [...BUCKETS, { timestamp: newest, value: 1 }] },
      { dateRange: sevenDays, lastTriggered: LAST_FIRED, readAt: NOW },
    );

    expect(range).toEqual({ max: Date.parse(newest) });
  });

  it("leaves the axis alone when there are no points", () => {
    const options = {
      dateRange: sevenDays,
      lastTriggered: LAST_FIRED,
      readAt: NOW,
    };

    expect(getAlertChartXAxisRange({ result: [] }, options)).toEqual({});
    expect(getAlertChartXAxisRange(undefined, options)).toEqual({});
  });

  it("gives the same axis end for two reads in the same minute and moves it once the next minute starts", () => {
    const options = { dateRange: sevenDays, lastTriggered: LAST_FIRED };
    const first = getAlertChartXAxisRange(
      { result: BUCKETS },
      { ...options, readAt: READ_AT },
    );
    const second = getAlertChartXAxisRange(
      { result: BUCKETS },
      { ...options, readAt: NEXT_POLL },
    );
    const nextMinute = getAlertChartXAxisRange(
      { result: BUCKETS },
      { ...options, readAt: NOW + 5 * 1000 },
    );

    expect(first).toEqual({ max: NOW });
    expect(second).toEqual(first);
    expect(nextMinute).toEqual({ max: NOW + 60 * 1000 });
  });
});

describe("AlertsSheetView chart time axis", () => {
  beforeEach(() => {
    primeSheet({ now: NOW, buckets: BUCKETS });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("runs the axis past the newest bucket to the read time and the last firing", async () => {
    renderSheet();

    await waitFor(() =>
      expect(lastChartProps()?.options?.xaxis?.max).toBe(NOW),
    );
    const { options, series } = lastChartProps();
    expect(series[0].data.at(-1).x).toBe("2026-11-11T12:29:50Z");
    expect(options.xaxis.max).toBeGreaterThanOrEqual(Date.parse(LAST_FIRED));
    expect(options.xaxis.max).toBeGreaterThan(NEWEST_BUCKET);
    expect(options.xaxis.type).toBe("datetime");
    expect(mocks.get).toHaveBeenCalledWith(
      endpoints.project.getAlertGraph("alert-1"),
      expect.objectContaining({ params: expect.any(Object) }),
    );
  });
});

// The normal Vitest fork pool applies a TZ change. Every case checks the
// effective zone first, so a runner that ignores TZ fails instead of passing
// in UTC.
describe.each(ZONES)(
  "alert chart time axis with the browser in $zone",
  ({ zone, offsetMinutes, customToday, customPast }) => {
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

    describe("getAlertChartXAxisRange", () => {
      const wideRange = ["2026-11-04 00:00:00", "2026-11-12 23:59:59"];

      it("ends at the end of the read minute on the chart's clock for buckets sent without an offset", () => {
        const { max } = getAlertChartXAxisRange(
          { result: OFFSET_LESS_BUCKETS },
          { dateRange: wideRange, lastTriggered: LAST_FIRED, readAt: READ_AT },
        );

        expect(max).toBe(onChart("2026-11-11T12:35:00"));
        expect(max - onChart("2026-11-11T12:29:50")).toBe(
          NEWEST_TO_READ_MINUTE_END,
        );
        expect(max).toBeGreaterThanOrEqual(onChart("2026-11-11T12:34:10"));
      });

      it("ends at the last firing on the chart's clock when the read came before it", () => {
        const readAt = Date.UTC(2026, 10, 11, 12, 33, 0);
        const { max } = getAlertChartXAxisRange(
          { result: OFFSET_LESS_BUCKETS },
          { dateRange: wideRange, lastTriggered: LAST_FIRED, readAt },
        );

        expect(max).toBe(onChart("2026-11-11T12:34:10"));
      });

      it("gives the same axis end for two polls in the same minute", () => {
        const options = { dateRange: wideRange, lastTriggered: LAST_FIRED };
        const first = getAlertChartXAxisRange(
          { result: OFFSET_LESS_BUCKETS },
          { ...options, readAt: READ_AT },
        );
        const second = getAlertChartXAxisRange(
          { result: OFFSET_LESS_BUCKETS },
          { ...options, readAt: NEXT_POLL },
        );

        expect(first).toEqual({ max: onChart("2026-11-11T12:35:00") });
        expect(second).toEqual(first);
      });

      it("keeps points that carry an offset on the true clock", () => {
        const { max } = getAlertChartXAxisRange(
          { result: { graph_data: BUCKETS, alert_bar_data: [] } },
          { dateRange: wideRange, lastTriggered: LAST_FIRED, readAt: READ_AT },
        );

        expect(max).toBe(NOW);
      });

      it("reaches the last firing on the chart's clock for a custom range that ends after it", () => {
        const { buckets, options, axisEnd } = customToday;
        const { max } = getAlertChartXAxisRange({ result: buckets }, options);

        expect(max).toBe(onChartAt(axisEnd));
        expect(max).toBeGreaterThanOrEqual(onChartAt(options.lastTriggered));
        expect(max).toBeLessThanOrEqual(onChartAt(options.dateRange[1]));
      });

      it("ends a past custom range where the chart draws its end and leaves out a later firing", () => {
        const { buckets, dateRange } = customPast;
        const { max } = getAlertChartXAxisRange(
          { result: buckets },
          { dateRange, lastTriggered: LAST_FIRED, readAt: READ_AT },
        );

        expect(max).toBe(onChartAt(dateRange[1]));
        expect(max).toBeGreaterThan(onChart(buckets.at(-1).timestamp));
        expect(max).toBeLessThan(onChartAt(LAST_FIRED));
      });

      it("ends a past preset range where the server's window ends for points that carry an offset", () => {
        const pastRange = ["2026-11-08 00:00:00", "2026-11-10 00:00:00"];
        const points = [{ timestamp: "2026-11-09T00:00:00+00:00", value: 1 }];
        const { max } = getAlertChartXAxisRange(
          { result: { graph_data: points, alert_bar_data: [] } },
          { dateRange: pastRange, lastTriggered: LAST_FIRED, readAt: READ_AT },
        );

        expect(max).toBe(Date.parse("2026-11-10T00:00:00Z"));
      });
    });

    describe("AlertsSheetView", () => {
      beforeEach(() => {
        primeSheet({ now: READ_AT, buckets: OFFSET_LESS_BUCKETS });
      });

      afterEach(() => {
        vi.useRealTimers();
      });

      it("runs the axis to the end of the read minute on the chart's clock, past the newest bucket and the last firing", async () => {
        renderSheet();

        await waitFor(() =>
          expect(lastChartProps()?.options?.xaxis?.max).toBe(
            onChart("2026-11-11T12:35:00"),
          ),
        );
        const { options, series } = lastChartProps();
        const newest = series[0].data.at(-1).x;
        expect(newest).toBe("2026-11-11T12:29:50");
        expect(options.xaxis.max - new Date(newest).getTime()).toBe(
          NEWEST_TO_READ_MINUTE_END,
        );
        expect(options.xaxis.max).toBeGreaterThanOrEqual(
          onChart("2026-11-11T12:34:10"),
        );
        expect(options.xaxis.type).toBe("datetime");
      });

      it("keeps xaxis.max when a poll in the same minute brings the same buckets", async () => {
        const client = renderSheet();
        const axisEnd = onChart("2026-11-11T12:35:00");

        await waitFor(() =>
          expect(lastChartProps()?.options?.xaxis?.max).toBe(axisEnd),
        );
        const queryCache = client.getQueryCache();
        const [graphQuery] = queryCache.findAll({ queryKey: ["alert-graph"] });
        expect(graphQuery.state.dataUpdatedAt).toBe(READ_AT);
        const rendersBeforePoll = mocks.chart.mock.calls.length;

        vi.setSystemTime(NEXT_POLL);
        await act(async () => {
          await client.refetchQueries({ queryKey: ["alert-graph"] });
        });

        expect(graphQuery.state.dataUpdatedAt).toBe(NEXT_POLL);
        // The query tells the sheet about the poll on a later tick, and the
        // sheet then renders from the query's latest result.
        await waitFor(() =>
          expect(mocks.chart.mock.calls.length).toBeGreaterThan(
            rendersBeforePoll,
          ),
        );
        expect(lastChartProps().options.xaxis.max).toBe(axisEnd);
      });
    });
  },
);
