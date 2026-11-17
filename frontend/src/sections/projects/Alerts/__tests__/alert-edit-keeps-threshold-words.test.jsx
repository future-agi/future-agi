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

// A level saved as infinity or not-a-number has no JSON number, so the server
// sends it as null with its words beside it. The edit form keeps that level:
// its field opens empty with the words in it, and an update leaves the level
// out until the person types a number there. A duplicate is saved as a new
// alert, so it starts from a number.

// An alert as the server returns it; each case says which level has words.
const savedAlert = (overrides = {}) => ({
  id: "alert-words",
  name: "Errors up",
  project: "project-words",
  metric_type: "count_of_errors",
  metric: null,
  metric_name: "Count of errors",
  threshold_metric_value: "",
  threshold_type: "static",
  threshold_operator: "greater_than",
  threshold_rule: "current",
  critical_threshold_value: 10,
  warning_threshold_value: 5,
  alert_frequency: 5,
  auto_threshold_time_window: 5,
  filters: {},
  notification_emails: ["alerts@example.test"],
  slack_webhook_url: "",
  slack_notes: "",
  ...overrides,
});

const infiniteCritical = savedAlert({
  critical_threshold_value: null,
  critical_threshold_label: "Infinity",
  warning_threshold_value: 95,
});

let queryClient;
let onPayloadChange;

const field = (name) => document.querySelector(`[data-alert-field="${name}"]`);

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
          onPayloadChange={onPayloadChange}
        />
      </SnackbarProvider>
    </QueryClientProvider>,
  );
};

const openAlert = async (detail, duplicateAlertName = "") => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  const patch = vi
    .spyOn(axios, "patch")
    .mockResolvedValue({ data: { result: "ok" } });
  onPayloadChange = vi.fn();

  useAlertStore.setState({
    openSheetView: detail.id,
    selectedProject: detail.project,
    duplicateAlertName,
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
  // Until the alert arrives the warning holds the new-alert default.
  await waitFor(() =>
    expect(field("warning-threshold-value")).toHaveValue(
      detail.warning_threshold_value,
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
// finish, so a save or a preview that is missing is really missing.
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

describe("edit form for an alert with a level saved with no number", () => {
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

  it("opens an infinite critical level empty with its words and leaves it out of an update", async () => {
    const patch = await openAlert(infiniteCritical);

    expect(field("critical-threshold-value")).toHaveValue(null);
    expect(field("critical-threshold-value")).toHaveAttribute(
      "placeholder",
      "Infinity",
    );

    await typeValue("warning-threshold-value", "90");
    await clickUpdate();

    const body = await sentBody(patch, infiniteCritical);
    expect(body).not.toHaveProperty("critical_threshold_value");
    expect(body.warning_threshold_value).toBe(90);
  });

  it("saves the number the person types in place of the words", async () => {
    const patch = await openAlert(infiniteCritical);

    await typeValue("critical-threshold-value", "500");
    await clickUpdate();

    const body = await sentBody(patch, infiniteCritical);
    expect(body).toMatchObject({
      critical_threshold_value: 500,
      warning_threshold_value: 95,
    });
  });

  it("keeps a not-a-number critical level with no warning when only the name changes", async () => {
    const detail = savedAlert({
      critical_threshold_value: null,
      critical_threshold_label: "Not a number",
      warning_threshold_value: null,
    });
    const patch = await openAlert(detail);
    expect(field("critical-threshold-value")).toHaveValue(null);
    expect(field("critical-threshold-value")).toHaveAttribute(
      "placeholder",
      "Not a number",
    );

    await typeValue("name", "Errors up, renamed");
    await clickUpdate();

    const body = await sentBody(patch, detail);
    expect(body.name).toBe("Errors up, renamed");
    expect(body).not.toHaveProperty("critical_threshold_value");
    expect(body).not.toHaveProperty("warning_threshold_value");
  });

  it("keeps an infinite warning level beside a numeric critical", async () => {
    const detail = savedAlert({
      threshold_operator: "less_than",
      critical_threshold_value: 5,
      warning_threshold_value: null,
      warning_threshold_label: "Infinity",
    });
    const patch = await openAlert(detail);
    expect(field("warning-threshold-value")).toHaveAttribute(
      "placeholder",
      "Infinity",
    );

    await typeValue("critical-threshold-value", "4");
    await clickUpdate();

    const body = await sentBody(patch, detail);
    expect(body.critical_threshold_value).toBe(4);
    expect(body).not.toHaveProperty("warning_threshold_value");
  });

  it("previews nothing while the words stand for the critical level", async () => {
    await openAlert(
      savedAlert({
        critical_threshold_value: null,
        critical_threshold_label: "Not a number",
        warning_threshold_value: null,
      }),
    );

    await chooseDirection("Below");

    // The preview draws every level from a number, and this one has none.
    await waitFor(() =>
      expect(onPayloadChange.mock.calls.at(-1)[0].threshold_operator).toBe(
        "less_than",
      ),
    );
    await settleValidation();
    const [, enabled] = onPayloadChange.mock.calls.at(-1);
    expect(enabled).toBe(false);
  });

  it("starts a duplicate from a number, since it is saved as a new alert", async () => {
    await openAlert(infiniteCritical, "Errors up, copy");

    expect(field("critical-threshold-value")).toHaveValue(400);
    expect(field("critical-threshold-value")).not.toHaveAttribute(
      "placeholder",
    );
  });
});
