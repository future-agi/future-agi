import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import { SnackbarProvider } from "notistack";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import axios, { endpoints } from "src/utils/axios";
import {
  CATEGORIES,
  SPAN_TYPE_PROPERTY,
  toFormRows,
  toPanelRows,
  toPanelType,
} from "../components/alertFilterRows";
import {
  convertFiltersToPayload,
  intervalOptions,
  normalizeAlertDetail,
  timeOptions,
} from "../common";
import {
  ALERT_CONFIG_DEFAULTS,
  transformFilterResponse,
} from "../components/validation";
import AlertSettingsForm from "../components/AlertSettingsForm";
import { savedAlert as baseSavedAlert } from "./fixtures";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

const properties = [
  SPAN_TYPE_PROPERTY,
  {
    id: "confidence_score",
    name: "confidence_score",
    category: "attribute",
    rawCategory: "custom_attribute",
    type: "number",
    typeSelectable: true,
    apiColType: "SPAN_ATTRIBUTE",
  },
  {
    id: "region",
    name: "region",
    category: "attribute",
    rawCategory: "custom_attribute",
    type: toPanelType("string"),
    apiColType: "SPAN_ATTRIBUTE",
  },
  {
    id: "customer_tier",
    name: "customer_tier",
    category: "attribute",
    rawCategory: "custom_attribute",
    type: toPanelType("string"),
    apiColType: "SPAN_ATTRIBUTE",
  },
];

// Round-trip a saved alert through the *real panel*, not just the conversion
// helpers: the panel rewrites operators on hydration, which the helper-only
// round-trip test cannot see.
const openPanelWith = (payload) => {
  const onApply = vi.fn();
  renderWithRouter(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TraceFilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        onApply={onApply}
        currentFilters={toPanelRows(transformFilterResponse(payload))}
        properties={properties}
        categories={CATEGORIES}
        projectId="test-project"
        showAi={false}
        showQueryTab={false}
      />
    </QueryClientProvider>,
  );
  return onApply;
};

describe("saved alert survives a visit to the filter panel", () => {
  it("emits nothing when the panel is merely opened", async () => {
    const onApply = openPanelWith({
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    });
    await new Promise((r) => setTimeout(r, 700));
    expect(onApply).not.toHaveBeenCalled();
  });

  it("does not rewrite an untouched row's saved operator when another row is edited", async () => {
    const payload = {
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
        {
          column_id: "region",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "us-east",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    };

    const onApply = openPanelWith(payload);

    // Edit the SECOND row's operator; the first row must survive verbatim.
    const combos = screen.getAllByRole("combobox");
    fireEvent.mouseDown(combos[combos.length - 1]);
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByText("contains"));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    // The panel row legitimately holds `in`; what must not drift is the
    // payload the API receives for the row nobody touched.
    const sent = convertFiltersToPayload(
      toFormRows(onApply.mock.calls.at(-1)[0]),
    );
    const untouched = sent.span_attributes_filters.find(
      (f) => f.column_id === "customer_tier",
    );
    expect(untouched.filter_config.filter_op).toBe("equals");
    expect(untouched.filter_config.filter_value).toBe("enterprise");
  });

  it("saves an edited numeric value as a number, not the input's string", async () => {
    // The panel's numeric input is a plain TextField. The old form ran
    // parseFloat before storing, so an edit must not change filter_value's
    // type on save.
    const payload = {
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "confidence_score",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: 0.8,
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    };

    const onApply = openPanelWith(payload);

    const input = screen.getByDisplayValue("0.8");
    fireEvent.change(input, { target: { value: "0.95" } });

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const sent = convertFiltersToPayload(
      toFormRows(onApply.mock.calls.at(-1)[0]),
    );
    const value = sent.span_attributes_filters[0].filter_config.filter_value;
    expect(value).toBe(0.95);
    expect(typeof value).toBe("number");
  });
});

const savedAlert = {
  ...baseSavedAlert,
  critical_threshold_value: 0,
  filters: {},
};

const evaluations = [
  { id: "eval-1", name: "Groundedness", choices: ["Passed", "Failed"] },
];

const labelFor = (options, value) =>
  options.find((option) => option.value === value)?.label;

const fieldValue = (field) =>
  document.querySelector(`[data-alert-field="${field}"]`)?.value;

const checkedRadio = (name) =>
  document.querySelector(`input[name="${name}"]:checked`)?.value;

const openSavedAlertForEditing = async (detail = savedAlert) => {
  renderWithRouter(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
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

  // The sheet mounts before the alert request resolves, so the saved alert
  // reaches the form only after the form already exists.
  act(() => {
    useAlertSheetStore.setState({
      alertRuleDetails: normalizeAlertDetail(detail),
    });
  });

  await waitFor(() =>
    expect(screen.getAllByLabelText("Choice")).not.toHaveLength(0),
  );
};

describe("saved alert reopens in the settings form", () => {
  beforeEach(() => {
    vi.spyOn(axios, "get").mockImplementation((url) =>
      Promise.resolve({
        data: {
          result: url === endpoints.project.getTraceEvals() ? evaluations : [],
        },
      }),
    );
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

  it("shows the stored values instead of the blank-form defaults", async () => {
    await openSavedAlertForEditing();

    expect(fieldValue("name")).toBe(savedAlert.name);
    expect(fieldValue("interval")).toBe(
      labelFor(intervalOptions, savedAlert.alert_frequency),
    );
    expect(fieldValue("interval")).not.toBe(
      labelFor(intervalOptions, ALERT_CONFIG_DEFAULTS.alert_frequency),
    );

    expect(checkedRadio("threshold_type")).toBe(savedAlert.threshold_type);
    expect(checkedRadio("threshold_type")).not.toBe(
      ALERT_CONFIG_DEFAULTS.threshold_type,
    );
    expect(
      screen.getByDisplayValue(
        labelFor(timeOptions, savedAlert.auto_threshold_time_window),
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByDisplayValue(
        labelFor(timeOptions, ALERT_CONFIG_DEFAULTS.auto_threshold_time_window),
      ),
    ).toBeNull();

    expect(
      screen.getAllByLabelText("Choice").map((choice) => choice.value),
    ).toEqual([
      savedAlert.threshold_metric_value,
      savedAlert.threshold_metric_value,
    ]);

    expect(fieldValue("critical-threshold-value")).toBe(
      String(savedAlert.critical_threshold_value),
    );
    expect(fieldValue("critical-threshold-value")).not.toBe(
      String(ALERT_CONFIG_DEFAULTS.critical_threshold_value),
    );
    expect(fieldValue("warning-threshold-value")).toBe(
      String(savedAlert.warning_threshold_value),
    );
    expect(fieldValue("warning-threshold-value")).not.toBe(
      String(ALERT_CONFIG_DEFAULTS.warning_threshold_value),
    );

    expect(checkedRadio("notification.method")).toBe("slack");
    expect(
      screen.getByDisplayValue(savedAlert.slack_webhook_url),
    ).toBeInTheDocument();
    expect(
      screen.getByDisplayValue(savedAlert.slack_notes),
    ).toBeInTheDocument();
  });

  it("clears the Slack webhook when the alert is switched to email", async () => {
    const patch = vi
      .spyOn(axios, "patch")
      .mockResolvedValue({ data: { result: "ok" } });

    await openSavedAlertForEditing({
      ...savedAlert,
      notification_emails: ["alerts@futureagi.com"],
    });

    await act(async () => {
      fireEvent.click(
        document.querySelector(
          'input[name="notification.method"][value="email"]',
        ),
      );
    });

    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-alert-form-submit="update"]'),
      );
    });

    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch).toHaveBeenCalledWith(
      `${endpoints.project.createMonitor}${savedAlert.id}/`,
      expect.objectContaining({
        notification_emails: ["alerts@futureagi.com"],
        slack_webhook_url: "",
        slack_notes: "",
      }),
    );
  });

  it("keeps the stored values after an update instead of blanking the form", async () => {
    const patch = vi
      .spyOn(axios, "patch")
      .mockResolvedValue({ data: { result: "ok" } });
    const onUpdated = vi.fn();
    useAlertStore.setState({ _refreshFn: onUpdated });

    await openSavedAlertForEditing();

    fireEvent.change(document.querySelector('[data-alert-field="name"]'), {
      target: { value: "Groundedness dip v2" },
    });

    await act(async () => {
      fireEvent.click(
        document.querySelector('[data-alert-form-submit="update"]'),
      );
    });

    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch).toHaveBeenCalledWith(
      `${endpoints.project.createMonitor}${savedAlert.id}/`,
      expect.objectContaining({
        name: "Groundedness dip v2",
        alert_frequency: savedAlert.alert_frequency,
        threshold_type: savedAlert.threshold_type,
        threshold_operator: savedAlert.threshold_operator,
        auto_threshold_time_window: savedAlert.auto_threshold_time_window,
        threshold_metric_value: savedAlert.threshold_metric_value,
        critical_threshold_value: savedAlert.critical_threshold_value,
        warning_threshold_value: savedAlert.warning_threshold_value,
        slack_webhook_url: savedAlert.slack_webhook_url,
        slack_notes: savedAlert.slack_notes,
      }),
    );

    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 400));
    });

    expect(fieldValue("name")).toBe("Groundedness dip v2");
    expect(fieldValue("interval")).toBe(
      labelFor(intervalOptions, savedAlert.alert_frequency),
    );
    expect(fieldValue("warning-threshold-value")).toBe(
      String(savedAlert.warning_threshold_value),
    );
    expect(checkedRadio("notification.method")).toBe("slack");
  });
});
