import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { format } from "date-fns";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen, waitFor } from "src/utils/test-utils";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";

// An alert that has never fired has no trigger time. The details response
// sends none for it and sends the time the alert was last checked as its own
// field. The sheet says "Never" for the trigger, shows the check on its own
// row, and runs the chart's time axis from its points, the selected range and
// the graph read only, never out to the check.

// The sheet read the graph between whole minutes, so its axis ends with that
// minute.
const READ_AT = Date.UTC(2026, 10, 11, 12, 34, 40);
const READ_MINUTE_END = Date.UTC(2026, 10, 11, 12, 35, 0);
// A check that finished after the sheet's last graph read. It falls inside
// the sheet's default seven-day range in every browser zone.
const CHECKED_AT = "2026-11-11T12:40:00Z";
// A firing after the graph read, inside the same range.
const FIRED_AT = "2026-11-11T12:38:00Z";
const BUCKETS = [
  { timestamp: "2026-11-11T12:25:10Z", value: 1 },
  { timestamp: "2026-11-11T12:29:50Z", value: 1 },
];
const RANGE = ["2026-11-04 12:34:40", "2026-11-11 23:59:59"];

// An alert as the details response sends it.
const savedAlert = (overrides = {}) => ({
  id: "alert-1",
  name: "Judge fails",
  project: "project-1",
  created_by: { name: "Alert author" },
  created_at: "2026-11-01T09:00:00Z",
  metric_type: "count_of_errors",
  metric_name: "Count of errors",
  threshold_type: "static",
  threshold_operator: "greater_than",
  critical_threshold_value: 20,
  warning_threshold_value: 10,
  notification_emails: [],
  last_triggered_at: null,
  last_checked_at: CHECKED_AT,
  ...overrides,
});
const neverFired = savedAlert();
const fired = savedAlert({ last_triggered_at: FIRED_AT });

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

import { getAlertChartXAxisRange, normalizeAlertDetail } from "../common";
import AlertDetails from "../components/AlertsSheetView/AlertDetails";
import AlertsSheetView from "../components/AlertsSheetView/AlertsSheetView";

// The sheet's back button is the only Iconify glyph left once the chart and
// the issues are stubbed.
for (const name of ["solar:arrow-left-linear"]) {
  registerOfflineIcon(name, bundled[name]);
}

// A time as the rule details print it, in the browser's own zone.
const shown = (iso) => format(new Date(iso), "dd-MM-yyyy, HH:mm");

// The text the rule details show beside a label, or null with no such row.
const detailValue = (label) => {
  const row = screen.queryByText(label)?.closest(".MuiGrid-item");
  return row ? row.nextElementSibling.textContent : null;
};

const lastChartProps = () => mocks.chart.mock.calls.at(-1)?.[0];

let client;

const renderDetails = (detail) => {
  mocks.sheet.alertRuleDetails = detail ? normalizeAlertDetail(detail) : null;
  render(<AlertDetails />);
};

const renderSheet = (detail) => {
  mocks.sheet.alertRuleDetails = normalizeAlertDetail(detail);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AlertsSheetView />
    </QueryClientProvider>,
  );
};

afterEach(() => {
  client?.clear();
  client = undefined;
  mocks.chart.mockClear();
  mocks.sheet.alertRuleDetails = null;
});

describe("an alert's trigger and check times", () => {
  it("keeps no trigger time for an alert that has never fired, and keeps its check time apart", () => {
    const detail = normalizeAlertDetail(neverFired);

    expect(detail.lastTriggered).toBeNull();
    expect(detail.lastChecked).toBe(CHECKED_AT);
  });

  it("keeps the newest issue's time as a fired alert's trigger time", () => {
    expect(normalizeAlertDetail(fired).lastTriggered).toBe(FIRED_AT);
  });
});

describe("the alert rule details", () => {
  it("reads Never as the last trigger of an alert that has never fired", () => {
    renderDetails(neverFired);

    expect(detailValue("Last triggered")).toBe("Never");
  });

  it("shows when the alert was last checked on its own row", () => {
    renderDetails(neverFired);

    expect(detailValue("Last checked")).toBe(shown(CHECKED_AT));
  });

  it("says an alert that has never been checked has not been checked yet", () => {
    renderDetails(savedAlert({ last_checked_at: null }));

    expect(detailValue("Last triggered")).toBe("Never");
    expect(detailValue("Last checked")).toBe("Not checked yet");
  });

  it("shows a fired alert's trigger time", () => {
    renderDetails(fired);

    expect(detailValue("Last triggered")).toBe(shown(FIRED_AT));
  });

  it("shows no trigger text before the alert has loaded", () => {
    renderDetails(null);

    expect(detailValue("Last triggered")).toBe("");
    expect(screen.queryByText("Never")).toBeNull();
    expect(screen.queryByText("Not checked yet")).toBeNull();
  });
});

describe("the time axis of an alert that has never fired", () => {
  it("ends at the last graph read, not at a later check", () => {
    const { lastTriggered } = normalizeAlertDetail(neverFired);
    const range = getAlertChartXAxisRange(
      { result: BUCKETS },
      { dateRange: RANGE, lastTriggered, readAt: READ_AT },
    );

    expect(range).toEqual({ max: READ_MINUTE_END });
    expect(range.max).toBeLessThan(Date.parse(CHECKED_AT));
  });

  it("still reaches a fired alert's trigger time after the last graph read", () => {
    const { lastTriggered } = normalizeAlertDetail(fired);
    const range = getAlertChartXAxisRange(
      { result: BUCKETS },
      { dateRange: RANGE, lastTriggered, readAt: READ_AT },
    );

    expect(range).toEqual({ max: Date.parse(FIRED_AT) });
  });
});

describe("the alert sheet", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(READ_AT);
    mocks.get.mockReset();
    mocks.get.mockResolvedValue({ data: { result: BUCKETS } });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("reads Never beside a chart whose axis ends at the last graph read for an alert that has never fired", async () => {
    renderSheet(neverFired);

    await waitFor(() =>
      expect(lastChartProps()?.options?.xaxis?.max).toBeDefined(),
    );
    expect(lastChartProps().options.xaxis.max).toBe(READ_MINUTE_END);
    await screen.findByText("Alert Rule Details");
    expect(detailValue("Last triggered")).toBe("Never");
    expect(detailValue("Last checked")).toBe(shown(CHECKED_AT));
  });

  it("shows a fired alert's trigger time and runs the axis out to it", async () => {
    renderSheet(fired);

    await waitFor(() =>
      expect(lastChartProps()?.options?.xaxis?.max).toBeDefined(),
    );
    expect(lastChartProps().options.xaxis.max).toBe(Date.parse(FIRED_AT));
    await screen.findByText("Alert Rule Details");
    expect(detailValue("Last triggered")).toBe(shown(FIRED_AT));
  });
});
