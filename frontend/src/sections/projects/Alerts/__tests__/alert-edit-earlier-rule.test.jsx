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

// A percentage alert saved before the update, as the server returns it.
const earlierAlert = {
  id: "alert-earlier",
  name: "Errors spike",
  project: "project-earlier",
  metric_type: "count_of_errors",
  metric: null,
  metric_name: "Count of errors",
  threshold_metric_value: "",
  threshold_type: "percentage_change",
  threshold_operator: "greater_than",
  threshold_rule: "earlier",
  critical_threshold_value: 200,
  warning_threshold_value: 100,
  alert_frequency: 15,
  auto_threshold_time_window: 10080,
  filters: {},
  notification_emails: ["alerts@example.test"],
  slack_webhook_url: "",
  slack_notes: "",
};

const EARLIER_RULE_LINE =
  "Saved before the update, so this alert keeps its earlier rule. Change a threshold and save to switch to the current rule: more than 200% higher than the previous period.";

let queryClient;

const field = (name) => document.querySelector(`[data-alert-field="${name}"]`);

const openAlert = async (detail, { duplicateAlertName = "" } = {}) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });

  useAlertStore.setState({
    openSheetView: detail.id,
    selectedProject: detail.project,
    duplicateAlertName,
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

  // The saved alert reaches the form after the form already exists.
  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });
  await waitFor(() =>
    expect(field("name")).toHaveValue(duplicateAlertName || detail.name),
  );
  await waitFor(() =>
    expect(field("critical-threshold-value")).toHaveValue(
      detail.critical_threshold_value,
    ),
  );
};

describe("edit form for a percentage alert on its earlier rule", () => {
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

  it("says the alert keeps its earlier rule and how to switch", async () => {
    await openAlert(earlierAlert);

    expect(await screen.findByText(EARLIER_RULE_LINE)).toBeInTheDocument();
  });

  it("drops the line when the alert is switched to a static value", async () => {
    await openAlert(earlierAlert);
    await screen.findByText(EARLIER_RULE_LINE);

    await act(async () => {
      fireEvent.click(
        document.querySelector('input[name="threshold_type"][value="static"]'),
      );
    });

    await waitFor(() =>
      expect(screen.queryByText(EARLIER_RULE_LINE)).not.toBeInTheDocument(),
    );
  });

  it("has no line for an alert on the current rule", async () => {
    await openAlert({ ...earlierAlert, threshold_rule: "current" });

    expect(
      screen.queryByText(/^Saved before the update/),
    ).not.toBeInTheDocument();
  });

  it("has no line on a duplicate, which is a new alert", async () => {
    await openAlert(earlierAlert, { duplicateAlertName: "Errors spike copy" });

    expect(
      screen.queryByText(/^Saved before the update/),
    ).not.toBeInTheDocument();
  });
});
