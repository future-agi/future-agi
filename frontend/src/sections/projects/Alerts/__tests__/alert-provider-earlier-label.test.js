import { describe, expect, it } from "vitest";

import {
  formatConditionText,
  normalizeAlertDetail,
  transformAlertToConditions,
} from "../common";

// A service provider error rate alert saved before the update and not
// converted (a percentage alert, or a threshold outside 0 to 1) keeps the
// meaning its numbers were saved in: the server still judges it on the share
// of providers with NO errors. Its conditions say so, so "above 0.95" is not
// read as "more than 95% of providers had an error".
const providerAlert = (overrides = {}) =>
  normalizeAlertDetail({
    id: "monitor-p",
    metric_type: "service_provider_error_rates",
    metric_name: "Service provider error rates",
    threshold_type: "static",
    threshold_operator: "greater_than",
    critical_threshold_value: 95,
    warning_threshold_value: null,
    notification_emails: [],
    ...overrides,
  });

const criticalCondition = (alert) => transformAlertToConditions(alert)[0];

describe("service provider error rate conditions", () => {
  it("name the share with no errors while the alert keeps its earlier rule", () => {
    const { condition } = criticalCondition(
      providerAlert({ threshold_rule: "earlier" }),
    );
    expect(condition.metric).toBe("Share of providers with no errors");
    expect(formatConditionText(condition)).toBe(
      "If Share of providers with no errors > 95",
    );
  });

  it("name the share with no errors on a percentage alert that keeps its earlier rule", () => {
    const { condition } = criticalCondition(
      providerAlert({
        threshold_rule: "earlier",
        threshold_type: "percentage_change",
        critical_threshold_value: 400,
      }),
    );
    expect(formatConditionText(condition)).toMatch(
      /^If Share of providers with no errors is more than 5 standard deviations above its average/,
    );
  });

  it("keep the metric's own name on the current rule, after conversion or for a new alert", () => {
    const { condition } = criticalCondition(
      providerAlert({
        threshold_rule: "current",
        critical_threshold_value: 0.05,
      }),
    );
    expect(condition.metric).toBe("Service provider error rates");
  });

  it("keep the metric's own name when the server sends no rule", () => {
    const { condition } = criticalCondition(providerAlert());
    expect(condition.metric).toBe("Service provider error rates");
  });

  it("never rename another metric on its earlier rule", () => {
    const { condition } = criticalCondition(
      providerAlert({
        metric_type: "count_of_errors",
        metric_name: "Count of errors",
        threshold_rule: "earlier",
      }),
    );
    expect(condition.metric).toBe("Count of errors");
  });
});
