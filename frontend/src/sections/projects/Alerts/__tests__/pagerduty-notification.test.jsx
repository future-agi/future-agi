import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
} from "src/utils/test-utils";
import axios, { endpoints } from "src/utils/axios";
import AlertSettingsForm from "../components/AlertSettingsForm";
import {
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
} from "../components/validation";
import { normalizeAlertDetail } from "../common";
import { alertEventFields } from "../useCreateAlertMutation";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

const routingKey = "test-pagerduty-routing-key";
const savedAlert = {
  id: "test-alert",
  name: "Test error alert",
  project: "test-project",
  metric_type: "count_of_errors",
  threshold_type: "static",
  threshold_operator: "greater_than",
  critical_threshold_value: 10,
  warning_threshold_value: 5,
  alert_frequency: 60,
  notification_emails: [],
  slack_webhook_url: "",
  webhook_url: "",
  pagerduty_routing_key: routingKey,
  filters: {},
};

const renderForm = () =>
  renderWithRouter(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
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

const openSavedAlert = async () => {
  renderForm();
  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(savedAlert),
    });
  });
  return screen.findByLabelText(/PagerDuty routing key/);
};

const submit = async (mode) => {
  await act(async () => {
    fireEvent.click(document.querySelector(`[data-alert-form-submit="${mode}"]`));
  });
};

describe("PagerDuty alert notification form", () => {
  beforeEach(() => {
    vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
    useAlertStore.setState({
      openSheetView: savedAlert.id,
      selectedProject: savedAlert.project,
    });
    useAlertSheetStore.setState({
      alertRuleDetails: null,
      gridRef: { current: null },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetAlertStoreState();
    resetAlertSheetStoreState();
  });

  it("renders the saved routing key in a password field and submits an update", async () => {
    const patch = vi.spyOn(axios, "patch").mockResolvedValue({ data: { result: "ok" } });
    const input = await openSavedAlert();
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue(routingKey);
    expect(screen.getByRole("radio", { name: "PagerDuty" })).toBeChecked();
    fireEvent.change(input, { target: { value: "test-replacement-routing-key" } });
    await submit("update");
    await waitFor(() => expect(patch).toHaveBeenCalledWith(
      `${endpoints.project.createMonitor}${savedAlert.id}/`,
      expect.objectContaining({
        pagerduty_routing_key: "test-replacement-routing-key",
        notification_emails: [],
        slack_webhook_url: "",
        webhook_url: "",
      }),
    ));
  });

  it("creates a PagerDuty-only alert from the notification selector", async () => {
    const post = vi.spyOn(axios, "post").mockResolvedValue({ data: { result: "ok" } });
    useAlertStore.setState({ openSheetView: null, alertType: "count_of_errors" });
    renderForm();
    fireEvent.change(document.querySelector('[data-alert-field="name"]'), {
      target: { value: "Test PagerDuty alert" },
    });
    fireEvent.click(screen.getByRole("radio", { name: "PagerDuty" }));
    fireEvent.change(await screen.findByLabelText(/PagerDuty routing key/), {
      target: { value: routingKey },
    });
    await submit("create");
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      endpoints.project.createMonitor,
      expect.objectContaining({
        pagerduty_routing_key: routingKey,
        notification_emails: [],
        slack_webhook_url: "",
        webhook_url: "",
      }),
    ));
  });

  it("clears the routing key when switching to email", async () => {
    const patch = vi.spyOn(axios, "patch").mockResolvedValue({ data: { result: "ok" } });
    renderForm();
    act(() => {
      useAlertSheetStore.setState({
        alertRuleDetails: normalizeAlertDetail({
          ...savedAlert,
          notification_emails: ["alerts@example.test"],
        }),
      });
    });
    await screen.findByLabelText(/PagerDuty routing key/);
    fireEvent.click(screen.getByRole("radio", { name: "Email" }));
    await submit("update");
    await waitFor(() => expect(patch).toHaveBeenCalledWith(
      `${endpoints.project.createMonitor}${savedAlert.id}/`,
      expect.objectContaining({
        pagerduty_routing_key: "",
        notification_emails: ["alerts@example.test"],
      }),
    ));
  });

  it("rejects an empty PagerDuty routing key before submission", async () => {
    const patch = vi.spyOn(axios, "patch").mockResolvedValue({ data: { result: "ok" } });
    const input = await openSavedAlert();
    fireEvent.change(input, { target: { value: "   " } });
    await submit("update");
    expect(await screen.findByText("PagerDuty routing key is required")).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();
  });
});

describe("PagerDuty notification data", () => {
  it.each(["snake", "camel"])("hydrates and validates %s case routing keys", (casing) => {
    const detail = { ...savedAlert };
    if (casing === "camel") {
      delete detail.pagerduty_routing_key;
      detail.pagerdutyRoutingKey = routingKey;
    }
    const values = getDefaultAlertConfigValues(normalizeAlertDetail(detail));
    expect(values.notification.method).toBe("pagerduty");
    expect(values.notification.pagerduty.routingKey).toBe(routingKey);
    expect(AlertConfigValidationSchema.safeParse(values).success).toBe(true);
  });

  it("reports the PagerDuty channel without including its routing key", () => {
    const fields = alertEventFields(savedAlert, savedAlert.metric_type);
    expect(fields.notification_method).toBe("pagerduty");
    expect(JSON.stringify(fields)).not.toContain(routingKey);
    expect(fields).not.toHaveProperty("pagerduty_routing_key");
  });
});
