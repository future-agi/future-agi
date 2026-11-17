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
import axios from "src/utils/axios";
import { normalizeAlertDetail } from "../common";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// A minus typed in a static service provider error rate threshold stays in
// the field, the range sentence says why the value is refused, and the form
// will not save it. Every other alert's number fields still drop the minus.
const OUT_OF_RANGE =
  "Enter a share from 0 to 1, for example 0.2 for 20% of providers.";

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

// The form checks the values on promises; one timer tick lets that check
// finish and any reason render, so a reason that is missing is really missing.
const settleValidation = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

const clickSubmit = async (kind) => {
  await act(async () => {
    fireEvent.click(
      document.querySelector(`[data-alert-form-submit="${kind}"]`),
    );
  });
  await settleValidation();
};

// Neither the field nor any reason under it may read NaN, and the form must
// not fall back to its "must be a number" reason for a half-typed value.
const expectNoNotANumber = (name) => {
  expect(field(name).value).not.toBe("NaN");
  expect(screen.queryByText(/NaN/)).toBeNull();
  expect(screen.queryByText(/must be a number/)).toBeNull();
};

describe("a minus typed in a static provider error rate threshold", () => {
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

  it("stays in Warning, shows the range sentence and sends nothing on save", async () => {
    const patch = await openAlertForEditing(providerAlert);

    await typeValue("warning-threshold-value", "-0.1");

    expect(field("warning-threshold-value")).toHaveValue(-0.1);
    await waitFor(() =>
      expect(
        underField("warning-threshold-value").queryByText(OUT_OF_RANGE),
      ).not.toBeNull(),
    );
    expect(
      underField("critical-threshold-value").queryByText(OUT_OF_RANGE),
    ).toBeNull();

    await clickSubmit("update");

    expect(field("warning-threshold-value")).toHaveValue(-0.1);
    expect(
      underField("warning-threshold-value").queryByText(OUT_OF_RANGE),
    ).not.toBeNull();
    expect(patch).not.toHaveBeenCalled();
  });

  it("typed a step at a time in a new alert, ends at -0.2 with the sentence", async () => {
    renderNewProviderAlert();
    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(0.2),
    );

    // A number field reports "" while it holds "-", "-." or "-0.", as a
    // browser does; "-0" is already a number.
    for (const step of ["-", "-0", "-0.", "-0.2"]) {
      await typeValue("warning-threshold-value", step);
      await settleValidation();
      expectNoNotANumber("warning-threshold-value");
    }

    expect(field("warning-threshold-value")).toHaveValue(-0.2);
    await waitFor(() =>
      expect(
        underField("warning-threshold-value").queryByText(OUT_OF_RANGE),
      ).not.toBeNull(),
    );
  });
});

describe("a minus typed in any other alert's threshold", () => {
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

  it("is dropped from a Count of errors critical value, as before", async () => {
    await openAlertForEditing({
      ...providerAlert,
      id: "alert-errors",
      metric_type: "count_of_errors",
      metric_name: "Count of errors",
      critical_threshold_value: 95,
      warning_threshold_value: 90,
    });

    await typeValue("critical-threshold-value", "-5");
    await settleValidation();

    expect(field("critical-threshold-value")).toHaveValue(5);
    expect(screen.queryByText(OUT_OF_RANGE)).toBeNull();
  });

  it("is dropped from a percentage change provider alert, as before", async () => {
    await openAlertForEditing({
      ...providerAlert,
      id: "alert-provider-change",
      threshold_type: "percentage_change",
      critical_threshold_value: 30,
      warning_threshold_value: 20,
    });

    await typeValue("warning-threshold-value", "-10");
    await settleValidation();

    expect(field("warning-threshold-value")).toHaveValue(10);
    expect(screen.queryByText(OUT_OF_RANGE)).toBeNull();
  });
});
