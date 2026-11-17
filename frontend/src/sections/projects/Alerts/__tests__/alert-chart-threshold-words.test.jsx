import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, renderWithRouter, waitFor } from "src/utils/test-utils";
import axios from "src/utils/axios";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { normalizeAlertDetail } from "../common";
import AlertConfiguration from "../components/AlertConfiguration";
import AlertsSheetView from "../components/AlertsSheetView/AlertsSheetView";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// A static alert's chart shades a band from the top of the chart to the first
// level, between the two levels, and from the last level to the bottom. A
// level saved as infinity or not-a-number comes as null with its words, and
// the edit form keeps its field empty. It has no number, so the chart draws
// no line for it and no band that starts or ends there, instead of one at 0.

const mocks = vi.hoisted(() => ({
  chart: vi.fn(() => null),
  formLevels: null,
}));

vi.mock("../components/AlertsChart", () => ({ default: mocks.chart }));
// The drawer's form, reduced to what it hands the chart: the alert's
// direction and the values its two level fields hold.
vi.mock("../components/AlertSettingsForm", async () => {
  const { useEffect } = await import("react");
  return {
    /* eslint-disable react/prop-types */
    default: function LevelFields({
      setThresholdOperator,
      setCriticalValue,
      setWarningValue,
    }) {
      useEffect(() => {
        setThresholdOperator(mocks.formLevels.operator);
        setCriticalValue(mocks.formLevels.critical);
        setWarningValue(mocks.formLevels.warning);
      }, [setThresholdOperator, setCriticalValue, setWarningValue]);
      return null;
    },
    /* eslint-enable react/prop-types */
  };
});
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
vi.mock("../components/DuplicateALert", () => ({ default: () => null }));
vi.mock("../components/AlertsSheetView/Issues", () => ({
  default: () => null,
}));
vi.mock("../components/AlertsSheetView/AlertDetails", () => ({
  default: () => null,
}));
vi.mock("../useMuteAlerMutation", () => ({
  useMuteAlertsMutation: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The sheet's back button is its only Iconify glyph once the rest is stubbed.
registerOfflineIcon(
  "solar:arrow-left-linear",
  bundled["solar:arrow-left-linear"],
);

const POINTS = [
  { timestamp: "2026-11-11T12:00:00Z", value: 10 },
  { timestamp: "2026-11-11T12:05:00Z", value: 20 },
];
const STATIC_GRAPH = { result: POINTS };
const DATE_FILTER = {
  dateFilter: ["2026-11-11 00:00:00", "2026-11-11 23:59:59"],
};
// The top or the bottom of the chart, which the chart works out from its data.
const EDGE = expect.any(Number);

// An alert as the server returns it; each case says which level has words.
const savedAlert = (overrides = {}) => ({
  id: "alert-words",
  name: "Errors up",
  project: "project-words",
  metric_type: "count_of_errors",
  metric: null,
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 50,
  warning_threshold_value: 30,
  alert_frequency: 5,
  filters: {},
  ...overrides,
});

const infiniteCritical = savedAlert({
  critical_threshold_value: null,
  critical_threshold_label: "Infinity",
  warning_threshold_value: 95,
});

let queryClient;

const lastChart = () => mocks.chart.mock.calls.at(-1)?.[0];
// Each shaded band as [from, to]; a level's line is the edge of its bands.
const bands = () =>
  (lastChart()?.options?.annotations?.yaxis ?? []).map(({ y, y2 }) => [y, y2]);

const waitForLine = () =>
  waitFor(() =>
    expect(lastChart()?.series?.at(-1)?.data).toHaveLength(POINTS.length),
  );

// Puts a saved alert in the stores the way the sheet does, before the chart
// reads it, and serves its saved graph.
const openSavedAlert = (detail) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: STATIC_GRAPH });
  useAlertStore.setState({
    openSheetView: detail.id,
    openCreateAlerts: false,
    selectedProject: detail.project,
    alertType: detail.metric_type,
    mainPage: false,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: normalizeAlertDetail(detail),
    gridRef: { current: null },
  });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
};

const renderDrawer = () =>
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <AlertConfiguration dateFilter={DATE_FILTER} setDateFilter={vi.fn()} />
    </QueryClientProvider>,
  );

const renderSheet = () =>
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <AlertsSheetView />
    </QueryClientProvider>,
  );

beforeEach(() => {
  resetAlertStoreState();
  resetAlertSheetStoreState();
  mocks.chart.mockClear();
  mocks.formLevels = null;
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.restoreAllMocks();
  resetAlertStoreState();
  resetAlertSheetStoreState();
});

describe("the sheet's chart for a level saved with no number", () => {
  it("draws no line and no band at an infinite critical level", async () => {
    openSavedAlert(infiniteCritical);
    renderSheet();

    await waitForLine();
    // Above: only the band below the warning level is left.
    expect(bands()).toEqual([[95, EDGE]]);
  });

  it("draws no line and no band at an infinite warning level", async () => {
    openSavedAlert(
      savedAlert({
        threshold_operator: "less_than",
        critical_threshold_value: 5,
        warning_threshold_value: null,
        warning_threshold_label: "Infinity",
      }),
    );
    renderSheet();

    await waitForLine();
    // Below: only the band below the critical level is left.
    expect(bands()).toEqual([[5, EDGE]]);
  });

  it("keeps every band of an alert whose levels are numbers", async () => {
    openSavedAlert(savedAlert());
    renderSheet();

    await waitForLine();
    expect(bands()).toEqual([
      [EDGE, 50],
      [50, 30],
      [30, EDGE],
    ]);
  });
});

describe("the edit drawer's chart for a level the form keeps", () => {
  it("draws no line and no band at an infinite critical level left empty", async () => {
    openSavedAlert(infiniteCritical);
    mocks.formLevels = { operator: "greater_than", critical: "", warning: 95 };
    renderDrawer();

    await waitFor(() => expect(bands().flat()).toContain(95));
    expect(bands()).toEqual([[95, EDGE]]);
  });

  it("draws the number typed in place of the words", async () => {
    openSavedAlert(infiniteCritical);
    mocks.formLevels = { operator: "greater_than", critical: 500, warning: 95 };
    renderDrawer();

    await waitFor(() => expect(bands().flat()).toContain(95));
    expect(bands()).toEqual([
      [EDGE, 500],
      [500, 95],
      [95, EDGE],
    ]);
  });
});
