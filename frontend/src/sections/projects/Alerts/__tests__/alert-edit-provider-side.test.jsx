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

// A provider error rate alert, as the server returns it after the update.
const providerAlert = {
  id: "alert-provider",
  name: "Providers failing",
  project: "project-provider",
  metric_type: "service_provider_error_rates",
  metric: null,
  metric_name: "Service provider error rates",
  threshold_metric_value: "",
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 0.6,
  warning_threshold_value: 0.2,
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

describe("edit form for a service provider error rate alert", () => {
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

  it("says beside the thresholds which share they are on", async () => {
    await openAlert(providerAlert);

    expect(
      screen.getAllByText("Share of providers that had an error, from 0 to 1"),
    ).toHaveLength(2);
  });

  it("names the share with no errors for a row left on the earlier rule", async () => {
    await openAlert({
      ...providerAlert,
      threshold_rule: "earlier",
      critical_threshold_value: 95,
      warning_threshold_value: 90,
    });

    expect(
      screen.getAllByText("Share of providers with no errors, from 0 to 1"),
    ).toHaveLength(2);
  });

  // Changing a threshold moves a saved row to the current rule when it is
  // saved, so the words beside the numbers follow the edit.
  it("names the share with errors once a threshold of that row is changed", async () => {
    await openAlert({
      ...providerAlert,
      threshold_rule: "earlier",
      critical_threshold_value: 95,
      warning_threshold_value: 90,
    });

    // Above 90 stays a valid pair, so no reason replaces the words.
    await act(async () => {
      fireEvent.change(field("critical-threshold-value"), {
        target: { value: "96" },
      });
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(
      screen.getAllByText("Share of providers that had an error, from 0 to 1"),
    ).toHaveLength(2);
  });
});
