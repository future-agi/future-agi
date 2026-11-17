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
} from "src/utils/test-utils";
import { SnackbarProvider } from "notistack";
import axios from "src/utils/axios";
import { normalizeAlertDetail } from "../common";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// A monitor saved with no warning, as the server returns it.
const noWarningAlert = {
  id: "alert-no-warning",
  name: "Errors up",
  project: "project-no-warning",
  metric_type: "count_of_errors",
  metric: null,
  metric_name: "Count of errors",
  threshold_metric_value: "",
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 20,
  warning_threshold_value: null,
  alert_frequency: 5,
  auto_threshold_time_window: 5,
  filters: {},
  notification_emails: ["alerts@example.test"],
  slack_webhook_url: "",
  slack_notes: "",
};

let queryClient;
let onPayloadChange;

const field = (name) => document.querySelector(`[data-alert-field="${name}"]`);

const openAlert = async (detail) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  onPayloadChange = vi.fn();

  useAlertStore.setState({
    openSheetView: detail.id,
    selectedProject: detail.project,
    duplicateAlertName: "",
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
          onPayloadChange={onPayloadChange}
        />
      </SnackbarProvider>
    </QueryClientProvider>,
  );

  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });
  await waitFor(() =>
    expect(field("critical-threshold-value")).toHaveValue(
      detail.critical_threshold_value,
    ),
  );
};

describe("edit form for an alert saved without a warning", () => {
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

  it("opens with the warning empty", async () => {
    await openAlert(noWarningAlert);

    expect(field("warning-threshold-value")).toHaveValue(null);
  });

  it("previews a new critical with no warning and asks for none", async () => {
    await openAlert(noWarningAlert);

    await act(async () => {
      fireEvent.change(field("critical-threshold-value"), {
        target: { value: "25" },
      });
    });

    // The preview runs on the new critical and sends no warning.
    await waitFor(() => {
      const [payload, enabled] = onPayloadChange.mock.calls.at(-1);
      expect(Number(payload.critical_threshold_value)).toBe(25);
      expect(payload).not.toHaveProperty("warning_threshold_value");
      expect(enabled).toBe(true);
    });
    expect(screen.queryByText("Warning value is required")).toBeNull();
    expect(
      screen.queryByText(/Critical threshold must be greater than warning/),
    ).toBeNull();
  });

  it("previews a Below alert's new critical with no warning too", async () => {
    await openAlert({ ...noWarningAlert, threshold_operator: "less_than" });

    await act(async () => {
      fireEvent.change(field("critical-threshold-value"), {
        target: { value: "15" },
      });
    });

    await waitFor(() => {
      const [payload, enabled] = onPayloadChange.mock.calls.at(-1);
      expect(Number(payload.critical_threshold_value)).toBe(15);
      expect(payload).not.toHaveProperty("warning_threshold_value");
      expect(enabled).toBe(true);
    });
  });
});
