import { describe, expect, it } from "vitest";

import {
  formatConditionText,
  normalizeAlertDetail,
  providerThresholdHelp,
  transformAlertToConditions,
} from "../common";

// Service provider error rate now measures the share of providers that had an
// error. The conditions and the edit form say that in words beside the
// number, so "above 0.6" is not read without knowing which share it is. Rows
// the update left on the earlier rule still read the share with no errors.
const providerAlert = (overrides = {}) =>
  normalizeAlertDetail({
    id: "monitor-p",
    metric_type: "service_provider_error_rates",
    metric_name: "Service provider error rates",
    threshold_type: "static",
    threshold_operator: "greater_than",
    threshold_rule: "current",
    critical_threshold_value: 0.6,
    warning_threshold_value: 0.2,
    notification_emails: [],
    ...overrides,
  });

const conditionTexts = (alert) =>
  transformAlertToConditions(alert).map(({ condition }) =>
    formatConditionText(condition),
  );

describe("service provider error rate conditions name the share", () => {
  it("read a converted or new alert as the share of providers with errors", () => {
    expect(conditionTexts(providerAlert())).toEqual([
      "If the share of providers with errors is above 0.6",
      "If the share of providers with errors is above 0.2",
    ]);
  });

  it("read Below the same way", () => {
    expect(
      conditionTexts(
        providerAlert({
          threshold_operator: "less_than",
          critical_threshold_value: 0.1,
          warning_threshold_value: 0.3,
        }),
      ),
    ).toEqual([
      "If the share of providers with errors is below 0.1",
      "If the share of providers with errors is below 0.3",
    ]);
  });

  it("read a percentage alert on the current rule as a change in that share", () => {
    expect(
      conditionTexts(
        providerAlert({
          threshold_type: "percentage_change",
          critical_threshold_value: 50,
          warning_threshold_value: null,
        }),
      ),
    ).toEqual([
      "If the share of providers with errors is more than 50% higher compared to previous period",
    ]);
  });

  it("keep the share with no errors on a row left on the earlier rule", () => {
    expect(
      conditionTexts(
        providerAlert({
          threshold_rule: "earlier",
          critical_threshold_value: 95,
          warning_threshold_value: null,
        }),
      ),
    ).toEqual(["If Share of providers with no errors > 95"]);
  });

  it("leave other metrics as they were", () => {
    expect(
      conditionTexts(
        providerAlert({
          metric_type: "count_of_errors",
          metric_name: "Count of errors",
          critical_threshold_value: 20,
          warning_threshold_value: null,
        }),
      ),
    ).toEqual(["If Count of errors > 20"]);
  });
});

describe("the words beside the threshold in the edit form", () => {
  it("name the share with errors for a provider alert", () => {
    expect(
      providerThresholdHelp({
        metricType: "service_provider_error_rates",
        thresholdType: "static",
      }),
    ).toBe("Share of providers that had an error, from 0 to 1");
    expect(
      providerThresholdHelp({
        metricType: "service_provider_error_rates",
        thresholdType: "percentage_change",
      }),
    ).toBe("Change in the share of providers that had an error");
  });

  it("name the share with no errors for a saved row left on the earlier rule", () => {
    expect(
      providerThresholdHelp({
        metricType: "service_provider_error_rates",
        thresholdType: "static",
        earlier: true,
      }),
    ).toBe("Share of providers with no errors, from 0 to 1");
  });

  it("name only the share for a saved percentage row on the earlier rule", () => {
    expect(
      providerThresholdHelp({
        metricType: "service_provider_error_rates",
        thresholdType: "percentage_change",
        earlier: true,
      }),
    ).toBe("Share of providers with no errors");
  });

  it("add nothing for other metrics", () => {
    expect(
      providerThresholdHelp({
        metricType: "count_of_errors",
        thresholdType: "static",
      }),
    ).toBeUndefined();
  });
});
