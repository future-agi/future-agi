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
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// A static service provider error rate alert compares a share of providers,
// from 0 to 1, with its thresholds. The form says so under a value outside
// that range and will not save it; a new alert starts at 0.2 and 0.1; an
// alert being edited opens with the values it was saved with.
const OUT_OF_RANGE =
  "Enter a share from 0 to 1, for example 0.2 for 20% of providers.";

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

// A row the update could not convert: saved at 95 and 90, on the earlier rule.
const earlierProviderAlert = {
  ...providerAlert,
  id: "alert-provider-earlier",
  threshold_rule: "earlier",
  critical_threshold_value: 95,
  warning_threshold_value: 90,
};

let queryClient;

const field = (name) => document.querySelector(`[data-alert-field="${name}"]`);

// The text a value field shows under itself, as its helper text.
const underField = (name) =>
  within(field(name).closest(".MuiFormControl-root"));

const renderForm = () => {
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
};

const renderNewProviderAlert = () => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  useAlertStore.setState({
    openSheetView: null,
    selectedProject: "project-new",
    alertType: "service_provider_error_rates",
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
  renderForm();
};

const openAlertForEditing = async (detail) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  const patch = vi
    .spyOn(axios, "patch")
    .mockResolvedValue({ data: { result: "ok" } });

  useAlertStore.setState({
    openSheetView: detail.id,
    selectedProject: detail.project,
    duplicateAlertName: "",
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
  renderForm();

  // The sheet mounts before the alert request resolves, so the saved alert
  // reaches the form only after the form already exists.
  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });
  await waitFor(() => expect(field("name")).toHaveValue(detail.name));
  await waitFor(() =>
    expect(field("critical-threshold-value")).toHaveValue(
      detail.critical_threshold_value,
    ),
  );
  return patch;
};

const typeValue = async (name, value) => {
  await act(async () => {
    fireEvent.change(field(name), { target: { value } });
  });
};

// Both Threshold pickers edit the one shared direction.
const chooseDirection = async (label) => {
  fireEvent.click(field("critical-threshold-operator"));
  const option = await screen.findByRole("menuitem", { name: label });
  await act(async () => {
    fireEvent.click(within(option).getByText(label));
  });
  await waitFor(() =>
    expect(field("critical-threshold-operator")).toHaveValue(label),
  );
};

// The form checks the values on promises; one timer tick lets that check
// finish and any reason render, so a reason that is missing is really missing.
const settleValidation = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const clickUpdate = async () => {
  await act(async () => {
    fireEvent.click(
      document.querySelector('[data-alert-form-submit="update"]'),
    );
  });
  await settleValidation();
};

// The body the update sent, as it goes over the wire.
const sentBody = async (patch, detail) => {
  await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
  expect(patch.mock.calls[0][0]).toBe(
    `${endpoints.project.createMonitor}${detail.id}/`,
  );
  return JSON.parse(JSON.stringify(patch.mock.calls[0][1]));
};

describe("a static provider error rate threshold in the edit form", () => {
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

  // The form checks the values as they change, not when it opens: a row saved
  // at 95 and 90 shows the reason under both values when Update is clicked.
  it("refuses a row saved at 95 and 90 on save, under both values", async () => {
    const patch = await openAlertForEditing(earlierProviderAlert);
    expect(field("warning-threshold-value")).toHaveValue(90);

    await clickUpdate();

    await waitFor(() =>
      expect(
        underField("critical-threshold-value").queryByText(OUT_OF_RANGE),
      ).not.toBeNull(),
    );
    expect(
      underField("warning-threshold-value").queryByText(OUT_OF_RANGE),
    ).not.toBeNull();
    expect(patch).not.toHaveBeenCalled();
  });

  it("says so under a typed 96 and sends nothing on save", async () => {
    const patch = await openAlertForEditing(providerAlert);

    await typeValue("critical-threshold-value", "96");

    await waitFor(() =>
      expect(
        underField("critical-threshold-value").queryByText(OUT_OF_RANGE),
      ).not.toBeNull(),
    );
    expect(
      underField("warning-threshold-value").queryByText(OUT_OF_RANGE),
    ).toBeNull();

    await clickUpdate();

    expect(
      underField("critical-threshold-value").queryByText(OUT_OF_RANGE),
    ).not.toBeNull();
    expect(patch).not.toHaveBeenCalled();
  });

  it("saves 0.2 once both values are inside 0 to 1", async () => {
    const patch = await openAlertForEditing(earlierProviderAlert);

    await typeValue("warning-threshold-value", "0.1");
    await typeValue("critical-threshold-value", "0.2");
    await settleValidation();
    expect(screen.queryByText(OUT_OF_RANGE)).toBeNull();

    await clickUpdate();

    const body = await sentBody(patch, earlierProviderAlert);
    expect(body).toMatchObject({
      metric_type: "service_provider_error_rates",
      threshold_type: "static",
      threshold_operator: "greater_than",
      critical_threshold_value: 0.2,
      warning_threshold_value: 0.1,
    });
  });

  it("keeps an alert saved without a warning valid with no warning", async () => {
    const detail = { ...providerAlert, warning_threshold_value: null };
    const patch = await openAlertForEditing(detail);
    expect(field("warning-threshold-value")).toHaveValue(null);

    await typeValue("critical-threshold-value", "0.5");
    await settleValidation();
    expect(screen.queryByText(OUT_OF_RANGE)).toBeNull();
    expect(screen.queryByText("Warning value is required")).toBeNull();

    await clickUpdate();

    const body = await sentBody(patch, detail);
    expect(body.critical_threshold_value).toBe(0.5);
    expect(body).not.toHaveProperty("warning_threshold_value");
  });

  // The defaults are for a new alert only: a saved alert keeps its numbers.
  it("opens a saved alert with its saved values, not the new-alert ones", async () => {
    await openAlertForEditing({
      ...providerAlert,
      critical_threshold_value: 0.35,
      warning_threshold_value: 0.15,
    });

    expect(field("critical-threshold-value")).toHaveValue(0.35);
    expect(field("warning-threshold-value")).toHaveValue(0.15);
  });

  it("leaves a Count of errors alert at 95 with no range to check", async () => {
    const detail = {
      ...providerAlert,
      id: "alert-errors",
      metric_type: "count_of_errors",
      metric_name: "Count of errors",
      critical_threshold_value: 95,
      warning_threshold_value: 90,
    };
    const patch = await openAlertForEditing(detail);

    await typeValue("critical-threshold-value", "96");
    await settleValidation();
    expect(screen.queryByText(OUT_OF_RANGE)).toBeNull();

    await clickUpdate();

    const body = await sentBody(patch, detail);
    expect(body).toMatchObject({
      metric_type: "count_of_errors",
      critical_threshold_value: 96,
      warning_threshold_value: 90,
    });
  });
});

describe("a new static provider error rate alert", () => {
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

  it("starts at 0.2 and 0.1, and at 0.1 and 0.2 for Below", async () => {
    renderNewProviderAlert();

    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(0.2),
    );
    expect(field("warning-threshold-value")).toHaveValue(0.1);

    await chooseDirection("Below");

    expect(field("critical-threshold-value")).toHaveValue(0.1);
    expect(field("warning-threshold-value")).toHaveValue(0.2);
    await settleValidation();
    expect(screen.queryByText(OUT_OF_RANGE)).toBeNull();
  });
});
