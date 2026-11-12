import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { SnackbarProvider } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { normalizeAlertDetail } from "../common";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { getThresholdValueDefaults } from "../components/validation";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

const COHERENCE = {
  id: "eval-coherence",
  name: "Coherence",
  output_type: "score",
  choices: null,
};

// An eval alert that fires as the score falls: under "Below" the critical
// value sits under the warning value.
const belowEvalAlert = {
  id: "alert-below",
  name: "Coherence drop",
  project: "project-below",
  metric_type: "evaluation_metrics",
  metric: COHERENCE.id,
  metric_name: COHERENCE.name,
  threshold_metric_value: "",
  threshold_type: "static",
  threshold_operator: "less_than",
  critical_threshold_value: 0.4,
  warning_threshold_value: 0.6,
  alert_frequency: 5,
  auto_threshold_time_window: 60,
  filters: {},
  notification_emails: ["alerts@example.test"],
  slack_webhook_url: "",
  slack_notes: "",
};

let queryClient;

const field = (name) => document.querySelector(`[data-alert-field="${name}"]`);

// Two pickers are labelled "Metric": the metric type, and for eval alerts the
// eval itself.
const evalPicker = () =>
  screen
    .getAllByLabelText(/^Metric/)
    .find((input) => input.getAttribute("data-alert-field") !== "metric-type");

const openAlertForEditing = async (detail, evaluations) => {
  vi.spyOn(axios, "get").mockImplementation((url) =>
    Promise.resolve({
      data: {
        result: url === endpoints.project.getTraceEvals() ? evaluations : [],
      },
    }),
  );
  const patch = vi
    .spyOn(axios, "patch")
    .mockResolvedValue({ data: { result: "ok" } });

  useAlertStore.setState({
    openSheetView: detail.id,
    selectedProject: detail.project,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <SnackbarProvider>
        <AlertSettingsForm
          onThresholdTypeChange={vi.fn()}
          setThresholdOperator={vi.fn()}
          setWarningValue={vi.fn()}
          setCriticalValue={vi.fn()}
          setFormIsDirty={vi.fn()}
          onPayloadChange={vi.fn()}
        />
      </SnackbarProvider>
    </QueryClientProvider>,
  );

  // The sheet mounts before the alert request resolves, so the saved alert
  // reaches the form only after the form already exists.
  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });
  await waitFor(() => expect(field("name")).toHaveValue(detail.name));
  return patch;
};

const submitUpdate = async (patch) => {
  await act(async () => {
    fireEvent.click(
      document.querySelector('[data-alert-form-submit="update"]'),
    );
  });
  await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
  expect(patch.mock.calls[0][0]).toBe(
    `${endpoints.project.createMonitor}${belowEvalAlert.id}/`,
  );
  // Check the wire payload: undefined would be omitted, not clear a field.
  return JSON.parse(JSON.stringify(patch.mock.calls[0][1]));
};

describe("OBS-SW-124: new thresholds after a scale change follow the alert's direction", () => {
  beforeEach(() => {
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  it("puts critical under warning for Below and over warning for Above", () => {
    expect(
      getThresholdValueDefaults("count_of_errors", "static", "less_than"),
    ).toEqual({ critical: 300, warning: 400 });
    expect(
      getThresholdValueDefaults("evaluation_metrics", "static", "less_than"),
    ).toEqual({ critical: 0.3, warning: 0.4 });
    expect(
      getThresholdValueDefaults("count_of_errors", "static", "greater_than"),
    ).toEqual({ critical: 400, warning: 300 });
  });

  it("saves a Below eval alert switched to Count of errors", async () => {
    const patch = await openAlertForEditing(belowEvalAlert, [COHERENCE]);
    await waitFor(() => expect(evalPicker()).toHaveValue(COHERENCE.name));

    fireEvent.click(field("metric-type"));
    const option = await screen.findByRole("menuitem", {
      name: "Count of errors",
    });
    fireEvent.click(within(option).getByText("Count of errors"));
    await waitFor(() =>
      expect(field("metric-type")).toHaveValue("Count of errors"),
    );

    expect(field("critical-threshold-operator")).toHaveValue("Below");
    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(300),
    );
    expect(field("warning-threshold-value")).toHaveValue(400);

    const body = await submitUpdate(patch);
    expect(body).toMatchObject({
      name: belowEvalAlert.name,
      metric_type: "count_of_errors",
      metric: null,
      threshold_metric_value: null,
      threshold_type: "static",
      threshold_operator: "less_than",
      critical_threshold_value: 300,
      warning_threshold_value: 400,
    });
  });

  it("saves a Below eval alert switched from static to percentage change", async () => {
    const patch = await openAlertForEditing(belowEvalAlert, [COHERENCE]);
    await waitFor(() => expect(evalPicker()).toHaveValue(COHERENCE.name));

    await act(async () => {
      fireEvent.click(
        document.querySelector(
          'input[name="threshold_type"][value="percentage_change"]',
        ),
      );
    });

    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(300),
    );
    expect(field("warning-threshold-value")).toHaveValue(400);

    const body = await submitUpdate(patch);
    expect(body).toMatchObject({
      metric_type: "evaluation_metrics",
      metric: COHERENCE.id,
      threshold_type: "percentage_change",
      threshold_operator: "less_than",
      critical_threshold_value: 300,
      warning_threshold_value: 400,
      auto_threshold_time_window: belowEvalAlert.auto_threshold_time_window,
    });
  });
});

describe("OBS-SW-125: the edit form names the alert's eval", () => {
  beforeEach(() => {
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  afterEach(() => {
    cleanup();
    queryClient?.clear();
    vi.restoreAllMocks();
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  // The eval list only holds evals that already have results, so an alert on
  // a new eval is not in it. The alert itself carries the eval's name; for a
  // choice eval the server appends the chosen label, which has its own field.
  it.each([
    {
      kind: "choice eval",
      metric_name: "Groundedness (Passed)",
      threshold_metric_value: "Passed",
      shown: "Groundedness",
    },
    {
      kind: "score eval",
      metric_name: "Coherence",
      threshold_metric_value: "",
      shown: "Coherence",
    },
  ])(
    "shows the $kind's name, not its id, when it has no results yet",
    async ({ metric_name, threshold_metric_value, shown }) => {
      const detail = {
        ...belowEvalAlert,
        metric: "f91a9d2a-416c-4867-b42d-02fb966f0381",
        metric_name,
        threshold_metric_value,
        threshold_operator: "greater_than",
        critical_threshold_value: 0.4,
        warning_threshold_value: 0.3,
      };
      await openAlertForEditing(detail, []);

      await waitFor(() => expect(evalPicker()).toHaveValue(shown));
      expect(screen.queryByDisplayValue(detail.metric)).toBeNull();
    },
  );
});
