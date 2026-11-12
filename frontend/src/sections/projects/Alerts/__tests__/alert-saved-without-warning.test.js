import { describe, expect, it } from "vitest";

import { normalizeAlertDetail } from "../common";
import {
  ALERT_CONFIG_DEFAULTS,
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
} from "../components/validation";

// A monitor can be saved with no warning (through the API, or by Omega). Its
// edit form opens with the warning empty and saves without one; it never gets
// a warning nobody set. A new alert still starts with both values filled in
// and needs both.
const savedWithoutWarning = normalizeAlertDetail({
  id: "monitor-no-warning",
  name: "Errors up",
  metric_type: "count_of_errors",
  threshold_type: "static",
  threshold_operator: "greater_than",
  critical_threshold_value: 20,
  warning_threshold_value: null,
  alert_frequency: 5,
  notification_emails: ["alerts@example.test"],
});

const warningIssues = (values) => {
  const result = AlertConfigValidationSchema.safeParse(values);
  if (result.success) return [];
  return result.error.issues
    .filter((issue) => issue.path.join(".") === "warning_threshold_value")
    .map((issue) => issue.message);
};

describe("an alert saved without a warning", () => {
  it("opens with the warning empty, not the new-alert default", () => {
    const values = getDefaultAlertConfigValues(savedWithoutWarning);
    expect(values.critical_threshold_value).toBe(20);
    expect(values.warning_threshold_value).toBe("");
    expect(values.warning_optional).toBe(true);
  });

  it("saves with no warning, and a changed critical alone is valid", () => {
    const values = {
      ...getDefaultAlertConfigValues(savedWithoutWarning),
      critical_threshold_value: 25,
    };
    expect(warningIssues(values)).toEqual([]);
    expect(AlertConfigValidationSchema.safeParse(values).success).toBe(true);
  });

  it("still checks a warning the person adds against the critical", () => {
    const values = {
      ...getDefaultAlertConfigValues(savedWithoutWarning),
      warning_threshold_value: 30,
    };
    expect(warningIssues(values)).toEqual([
      "Warning threshold must be less than critical threshold for Above",
    ]);
  });
});

describe("alerts that have a warning, and new alerts", () => {
  it("keep a saved warning and still require one", () => {
    const saved = normalizeAlertDetail({
      ...savedWithoutWarning,
      warning_threshold_value: 10,
    });
    const values = getDefaultAlertConfigValues(saved);
    expect(values.warning_threshold_value).toBe(10);
    expect(values.warning_optional).toBe(false);
    expect(warningIssues({ ...values, warning_threshold_value: "" })).toEqual([
      "Warning value is required",
    ]);
  });

  it("start a new alert with the default warning and require it", () => {
    const values = getDefaultAlertConfigValues({
      metricType: "count_of_errors",
    });
    expect(values.warning_threshold_value).toBe(
      ALERT_CONFIG_DEFAULTS.warning_threshold_value,
    );
    expect(values.warning_optional).toBe(false);
    expect(warningIssues({ ...values, warning_threshold_value: "" })).toEqual([
      "Warning value is required",
    ]);
  });
});
