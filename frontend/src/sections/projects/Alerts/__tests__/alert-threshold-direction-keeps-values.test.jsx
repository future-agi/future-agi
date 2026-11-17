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

// Critical and warning share one Threshold direction. Picking it used to swap
// the two values whenever they did not fit the direction, so a person who
// typed critical 20 while warning still held its default 300 saw critical
// jump to 300. The form now keeps both values as typed and says under each
// field what to fix; it will not save until the pair fits the direction.
// A new alert whose values nobody has edited is different: its two defaults
// follow the direction, so it never shows a reason for values nobody typed.

const ABOVE_CRITICAL =
  "Critical threshold must be greater than warning threshold for Above";
const ABOVE_WARNING =
  "Warning threshold must be less than critical threshold for Above";
const BELOW_CRITICAL =
  "Critical threshold must be less than warning threshold for Below";
const BELOW_WARNING =
  "Warning threshold must be greater than critical threshold for Below";

// A saved Count of errors alert that fires as errors rise.
const savedAboveAlert = {
  id: "alert-above",
  name: "Error spike",
  project: "project-above",
  metric_type: "count_of_errors",
  metric: null,
  metric_name: null,
  threshold_metric_value: "",
  threshold_type: "static",
  threshold_operator: "greater_than",
  critical_threshold_value: 400,
  warning_threshold_value: 300,
  alert_frequency: 5,
  auto_threshold_time_window: 60,
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

// A new Count of errors alert starts at Above with critical 400, warning 300.
const renderNewAlert = () => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  useAlertStore.setState({
    openSheetView: null,
    selectedProject: "project-new",
    alertType: "count_of_errors",
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
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
  renderForm();

  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });
  await waitFor(() => expect(field("name")).toHaveValue(detail.name));
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

// The form re-checks both values on promises; one timer tick lets that check
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
};

describe("Choosing the Threshold direction keeps the values the person typed", () => {
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

  it("keeps critical 20 and warning 300 after choosing Above and says what to fix", async () => {
    renderNewAlert();
    await waitFor(() =>
      expect(field("warning-threshold-value")).toHaveValue(300),
    );

    await typeValue("critical-threshold-value", "20");
    await chooseDirection("Above");

    expect(field("critical-threshold-value")).toHaveValue(20);
    expect(field("warning-threshold-value")).toHaveValue(300);
    await waitFor(() =>
      expect(
        underField("critical-threshold-value").getByText(ABOVE_CRITICAL),
      ).toBeInTheDocument(),
    );
    expect(
      underField("warning-threshold-value").getByText(ABOVE_WARNING),
    ).toBeInTheDocument();

    await typeValue("warning-threshold-value", "10");

    await waitFor(() => expect(screen.queryByText(ABOVE_CRITICAL)).toBeNull());
    expect(screen.queryByText(ABOVE_WARNING)).toBeNull();
    expect(field("critical-threshold-value")).toHaveValue(20);
    expect(field("warning-threshold-value")).toHaveValue(10);
  });

  it("keeps the typed values after choosing Below and then Above, and says what to fix", async () => {
    renderNewAlert();
    await waitFor(() =>
      expect(field("warning-threshold-value")).toHaveValue(300),
    );

    await typeValue("critical-threshold-value", "500");
    await chooseDirection("Below");

    expect(field("critical-threshold-value")).toHaveValue(500);
    expect(field("warning-threshold-value")).toHaveValue(300);
    await waitFor(() =>
      expect(
        underField("critical-threshold-value").getByText(BELOW_CRITICAL),
      ).toBeInTheDocument(),
    );
    expect(
      underField("warning-threshold-value").getByText(BELOW_WARNING),
    ).toBeInTheDocument();
    expect(screen.queryByText(ABOVE_CRITICAL)).toBeNull();

    await typeValue("warning-threshold-value", "600");

    await waitFor(() => expect(screen.queryByText(BELOW_CRITICAL)).toBeNull());
    expect(screen.queryByText(BELOW_WARNING)).toBeNull();
    expect(field("critical-threshold-value")).toHaveValue(500);
    expect(field("warning-threshold-value")).toHaveValue(600);

    // Typed values that happen to equal the Below defaults are still typed:
    // choosing Above keeps them and says what to fix.
    await typeValue("critical-threshold-value", "300");
    await typeValue("warning-threshold-value", "400");
    await chooseDirection("Above");

    expect(field("critical-threshold-value")).toHaveValue(300);
    expect(field("warning-threshold-value")).toHaveValue(400);
    await waitFor(() =>
      expect(
        underField("critical-threshold-value").getByText(ABOVE_CRITICAL),
      ).toBeInTheDocument(),
    );
    expect(
      underField("warning-threshold-value").getByText(ABOVE_WARNING),
    ).toBeInTheDocument();
  });

  it("moves an untouched new alert to the Below defaults and back, with nothing to fix", async () => {
    renderNewAlert();
    await waitFor(() =>
      expect(field("warning-threshold-value")).toHaveValue(300),
    );
    expect(field("critical-threshold-value")).toHaveValue(400);

    await chooseDirection("Below");

    expect(field("critical-threshold-value")).toHaveValue(300);
    expect(field("warning-threshold-value")).toHaveValue(400);
    await settleValidation();
    expect(screen.queryByText(BELOW_CRITICAL)).toBeNull();
    expect(screen.queryByText(BELOW_WARNING)).toBeNull();

    await chooseDirection("Above");

    expect(field("critical-threshold-value")).toHaveValue(400);
    expect(field("warning-threshold-value")).toHaveValue(300);
    await settleValidation();
    expect(screen.queryByText(ABOVE_CRITICAL)).toBeNull();
    expect(screen.queryByText(ABOVE_WARNING)).toBeNull();
  });

  it("will not save a saved alert switched to Below until critical sits under warning", async () => {
    const patch = await openAlertForEditing(savedAboveAlert);
    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(400),
    );

    await chooseDirection("Below");

    expect(field("critical-threshold-value")).toHaveValue(400);
    expect(field("warning-threshold-value")).toHaveValue(300);
    await waitFor(() =>
      expect(
        underField("critical-threshold-value").getByText(BELOW_CRITICAL),
      ).toBeInTheDocument(),
    );

    await clickUpdate();
    expect(screen.getByText(BELOW_CRITICAL)).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();

    await typeValue("critical-threshold-value", "200");
    await waitFor(() => expect(screen.queryByText(BELOW_CRITICAL)).toBeNull());

    await clickUpdate();
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0]).toBe(
      `${endpoints.project.createMonitor}${savedAboveAlert.id}/`,
    );
    expect(JSON.parse(JSON.stringify(patch.mock.calls[0][1]))).toMatchObject({
      threshold_operator: "less_than",
      critical_threshold_value: 200,
      warning_threshold_value: 300,
    });
  });
});
