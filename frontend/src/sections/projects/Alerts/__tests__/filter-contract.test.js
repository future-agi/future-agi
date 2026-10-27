import { describe, expect, it } from "vitest";

import {
  convertFiltersToPayload,
  getAlertFilterValue,
  isAlertMuted,
  isSpanAttrFilterValid,
  normalizeAlertDetail,
  normalizeAlertListRow,
} from "../common";
import {
  ALERT_CONFIG_DEFAULTS,
  getDefaultAlertConfigValues,
  transformFilterResponse,
} from "../components/validation";
import { savedAlert } from "./fixtures";

describe("alert filter contract", () => {
  it("sends canonical span attribute filters to the API", () => {
    const payload = convertFiltersToPayload([
      {
        property: "observationType",
        filterConfig: { filterValue: "llm" },
      },
      {
        property: "attributes",
        propertyId: "customer_tier",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: "enterprise",
        },
      },
    ]);

    expect(payload).toEqual({
      observation_type: ["llm"],
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
    expect(payload.span_attributes_filters[0]).not.toHaveProperty("columnId");
    expect(payload.span_attributes_filters[0]).not.toHaveProperty(
      "filterConfig",
    );
  });

  it("validates canonical span attribute filters before submit", () => {
    expect(
      isSpanAttrFilterValid([
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
          },
        },
      ]),
    ).toBe(true);
    expect(
      isSpanAttrFilterValid([
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "",
          },
        },
      ]),
    ).toBe(false);
  });

  it("hydrates canonical filters from the API into local form state", () => {
    const filters = transformFilterResponse({
      observation_type: ["llm"],
      span_attributes_filters: [
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
          },
        },
      ],
    });

    expect(filters).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          property: "observationType",
          filterConfig: expect.objectContaining({ filterValue: "llm" }),
        }),
        expect.objectContaining({
          propertyId: "customer_tier",
          property: "attributes",
          filterConfig: {
            filterType: "text",
            filterOp: "equals",
            filterValue: "enterprise",
          },
        }),
      ]),
    );
  });

  it("normalizes alert list rows from API snake_case into UI fields", () => {
    const row = normalizeAlertListRow({
      id: "alert-1",
      metric_type: "Count of errors",
      last_triggered: "2026-05-24T00:00:00Z",
      no_of_alerts: 3,
      is_mute: true,
      filters: {
        observation_type: ["llm"],
        span_attributes_filters: [
          {
            column_id: "customer_tier",
            filter_config: {
              filter_type: "text",
              filter_op: "equals",
              filter_value: "enterprise",
            },
          },
        ],
      },
    });

    expect(row.metricType).toBe("Count of errors");
    expect(row.lastTriggered).toBe("2026-05-24T00:00:00Z");
    expect(row.noOfAlerts).toBe(3);
    expect(isAlertMuted(row)).toBe(true);
    expect(getAlertFilterValue(row)).toEqual([
      "Span Type is LLM",
      "Custom attribute is (customer_tier)",
    ]);
  });
});

const editFormValues = (detail) =>
  getDefaultAlertConfigValues(normalizeAlertDetail(detail));

describe("saved alert config hydration", () => {
  it("opens the edit form on the stored alert rather than the defaults", () => {
    const values = editFormValues(savedAlert);

    expect(values.name).toBe(savedAlert.name);
    expect(values.metric_type).toBe(savedAlert.metric_type);
    expect(values.metric).toBe(savedAlert.metric);
    expect(values.alert_frequency).toBe(savedAlert.alert_frequency);
    expect(values.threshold_type).toBe(savedAlert.threshold_type);
    expect(values.threshold_operator).toBe(savedAlert.threshold_operator);
    expect(values.threshold_metric_value).toBe(
      savedAlert.threshold_metric_value,
    );
    expect(values.auto_threshold_time_window).toBe(
      savedAlert.auto_threshold_time_window,
    );
    expect(values.critical_threshold_value).toBe(
      savedAlert.critical_threshold_value,
    );
    expect(values.warning_threshold_value).toBe(
      savedAlert.warning_threshold_value,
    );
    expect(values.notification).toEqual({
      method: "slack",
      emails: [],
      slack: {
        webhookUrl: savedAlert.slack_webhook_url,
        notes: savedAlert.slack_notes,
      },
    });

    expect(values.alert_frequency).not.toBe(
      ALERT_CONFIG_DEFAULTS.alert_frequency,
    );
    expect(values.threshold_type).not.toBe(
      ALERT_CONFIG_DEFAULTS.threshold_type,
    );
    expect(values.auto_threshold_time_window).not.toBe(
      ALERT_CONFIG_DEFAULTS.auto_threshold_time_window,
    );
    expect(values.threshold_metric_value).not.toBe(
      ALERT_CONFIG_DEFAULTS.threshold_metric_value,
    );
    expect(values.threshold_operator).not.toBe(
      ALERT_CONFIG_DEFAULTS.threshold_operator,
    );
  });

  it("reads the serializer's own snake_case keys, not only the camelCase aliases", () => {
    const values = getDefaultAlertConfigValues(savedAlert);

    expect(values.alert_frequency).toBe(savedAlert.alert_frequency);
    expect(values.threshold_type).toBe(savedAlert.threshold_type);
    expect(values.threshold_metric_value).toBe(
      savedAlert.threshold_metric_value,
    );
    expect(values.auto_threshold_time_window).toBe(
      savedAlert.auto_threshold_time_window,
    );
    expect(values.notification.method).toBe("slack");
  });

  it("keeps a stored zero threshold instead of the fallback", () => {
    const values = editFormValues({
      ...savedAlert,
      critical_threshold_value: 0,
      warning_threshold_value: 0,
    });

    expect(values.critical_threshold_value).toBe(0);
    expect(values.warning_threshold_value).toBe(0);
  });

  it("falls back to the named defaults when a stored value is unusable", () => {
    const values = editFormValues({
      id: "alert-2",
      alert_frequency: null,
      threshold_type: "",
      auto_threshold_time_window: "",
      threshold_operator: null,
      threshold_metric_value: null,
      critical_threshold_value: "not-a-number",
      warning_threshold_value: undefined,
      slack_webhook_url: "",
      notification_emails: null,
    });

    expect(values.alert_frequency).toBe(ALERT_CONFIG_DEFAULTS.alert_frequency);
    expect(values.threshold_type).toBe(ALERT_CONFIG_DEFAULTS.threshold_type);
    expect(values.auto_threshold_time_window).toBe(
      ALERT_CONFIG_DEFAULTS.auto_threshold_time_window,
    );
    expect(values.threshold_operator).toBe(
      ALERT_CONFIG_DEFAULTS.threshold_operator,
    );
    expect(values.threshold_metric_value).toBe(
      ALERT_CONFIG_DEFAULTS.threshold_metric_value,
    );
    expect(values.critical_threshold_value).toBe(
      ALERT_CONFIG_DEFAULTS.critical_threshold_value,
    );
    expect(values.warning_threshold_value).toBe(
      ALERT_CONFIG_DEFAULTS.warning_threshold_value,
    );
    expect(values.notification.method).toBe("email");
    expect(values.notification.emails).toEqual([]);
    expect(values.notification.slack).toEqual({ webhookUrl: "", notes: "" });
  });
});
