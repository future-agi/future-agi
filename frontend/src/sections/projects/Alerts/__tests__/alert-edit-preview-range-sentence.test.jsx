import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import { normalizeAlertDetail } from "../common";
import AlertConfiguration from "../components/AlertConfiguration";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// The server refuses any edit of a saved static share alert whose levels are
// outside 0 to 1 until they are fixed, the edit's preview included. When the
// edit drawer's preview is refused (400), the drawer shows the server's
// sentence where the chart's error goes, so the person sees what to fix. A
// failure on the server's side (5xx), or no answer at all, keeps the generic
// line.

const GENERIC = "Monitor graph data is temporarily unavailable. Please retry.";

const RANGE_SENTENCES = {
  service_provider_error_rates:
    "Enter a share from 0 to 1, for example 0.2 for 20% of providers.",
  llm_api_failure_rates:
    "Enter a share from 0 to 1, for example 0.2 for 20% of LLM calls.",
  error_rates_for_function_calling:
    "Enter a share from 0 to 1, for example 0.2 for 20% of function calls.",
  error_free_session_rates:
    "Enter a share from 0 to 1, for example 0.9 for 90% of sessions.",
};

const mocks = vi.hoisted(() => ({
  chart: vi.fn(() => null),
  // What the drawer's form asks to preview after the edit below.
  previewBody: null,
}));

vi.mock("../components/AlertsChart", async (importOriginal) => ({
  ...(await importOriginal()),
  default: mocks.chart,
}));
// The drawer's form, reduced to one edit that leaves the levels as saved (a
// new name): it makes the form dirty and asks for a preview of the alert.
vi.mock("../components/AlertSettingsForm", async (importOriginal) => ({
  ...(await importOriginal()),
  // eslint-disable-next-line react/prop-types
  default: ({ setFormIsDirty, onPayloadChange }) => (
    <button
      type="button"
      data-testid="rename-alert"
      onClick={() => {
        setFormIsDirty(true);
        onPayloadChange(mocks.previewBody, true, false);
      }}
    >
      Rename
    </button>
  ),
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

// Saved before the server checked the range, at levels a share never passes.
const savedAt400 = (metricType) => ({
  id: `alert-${metricType}`,
  name: "Saved share alert",
  project: "project-shares",
  metric_type: metricType,
  metric: null,
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 400,
  warning_threshold_value: 300,
  alert_frequency: 5,
  filters: {},
});

// The preview the form asks for once the alert is renamed: every value as
// saved, the levels included.
const renamedPreviewOf = (detail) => ({
  name: "Renamed share alert",
  project: detail.project,
  metric_type: detail.metric_type,
  threshold_operator: detail.threshold_operator,
  threshold_type: detail.threshold_type,
  critical_threshold_value: detail.critical_threshold_value,
  warning_threshold_value: detail.warning_threshold_value,
  alert_frequency: detail.alert_frequency,
  filters: {},
});

// A failed request as the app's axios client rejects it: the server's error
// body, with the response's status beside it.
const refusal = (statusCode, message, code) => ({
  status: false,
  code,
  message,
  detail: message,
  error: message,
  result: message,
  statusCode,
});

const POINTS = [
  { timestamp: "2026-03-02T09:00:00Z", value: 0.25 },
  { timestamp: "2026-03-02T09:05:00Z", value: 0.5 },
];
const DATE_FILTER = {
  dateFilter: ["2026-03-02 00:00:00", "2026-03-02 23:59:59"],
};

let queryClient;

const lastChart = () => mocks.chart.mock.calls.at(-1)?.[0];

// Opens the drawer on a saved alert, as the sheet does, with its saved graph
// drawn and the preview of an edit answered with the given failure.
const openDrawer = async (detail, failure) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: POINTS } });
  vi.spyOn(axios, "post").mockRejectedValue(failure);
  mocks.previewBody = renamedPreviewOf(detail);
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
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <AlertConfiguration dateFilter={DATE_FILTER} setDateFilter={vi.fn()} />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(lastChart()?.series?.at(-1)?.data).toHaveLength(POINTS.length),
  );
};

// Renames the alert in the drawer's form and returns the error the drawer
// shows once the preview fails.
const renameAndReadError = async () => {
  fireEvent.click(await screen.findByTestId("rename-alert"));
  await waitFor(() => expect(axios.post).toHaveBeenCalled());
  return screen.findByRole("alert");
};

beforeEach(() => {
  resetAlertStoreState();
  resetAlertSheetStoreState();
  mocks.chart.mockClear();
});

afterEach(() => {
  cleanup();
  queryClient?.clear();
  vi.restoreAllMocks();
  resetAlertStoreState();
  resetAlertSheetStoreState();
  mocks.previewBody = null;
});

describe("the edit drawer's chart when the preview of an edit fails", () => {
  it.each(Object.entries(RANGE_SENTENCES))(
    "shows the range sentence of a %s alert saved at 400 and 300, not the generic line",
    async (metricType, sentence) => {
      const detail = savedAt400(metricType);
      await openDrawer(detail, refusal(400, sentence, "invalid"));

      const error = await renameAndReadError();

      expect(axios.post).toHaveBeenCalledWith(
        endpoints.project.getAlertGraphPreview,
        expect.objectContaining({
          metric_type: metricType,
          critical_threshold_value: 400,
          warning_threshold_value: 300,
        }),
        expect.any(Object),
      );
      expect(error).toHaveTextContent(sentence);
      expect(error).not.toHaveTextContent(GENERIC);
    },
  );

  it.each([
    ["a 500", refusal(500, "Failed to get monitor preview", "server_error")],
    ["a 503", refusal(503, GENERIC, "monitor_graph_unavailable")],
    ["no answer", { message: "Network Error", transportCode: "ERR_NETWORK" }],
  ])("keeps the generic line for %s", async (_, failure) => {
    await openDrawer(savedAt400("llm_api_failure_rates"), failure);

    const error = await renameAndReadError();

    expect(error).toHaveTextContent(GENERIC);
    expect(error).not.toHaveTextContent("Failed to get monitor preview");
    expect(error).not.toHaveTextContent("Network Error");
  });
});
