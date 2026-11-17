import { describe, expect, it } from "vitest";

import { normalizeAlertDetail } from "../common";
import {
  ALERT_CONFIG_DEFAULTS,
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
  getThresholdValueDefaults,
} from "../components/validation";

// A static service provider error rate alert compares a share of providers,
// a number from 0 to 1, with its thresholds. A value outside 0 to 1 is
// refused under its own field, and a new alert starts inside the range. An
// alert being edited keeps the values it was saved with.
const OUT_OF_RANGE =
  "Enter a share from 0 to 1, for example 0.2 for 20% of providers.";
const ABOVE_CRITICAL =
  "Critical threshold must be greater than warning threshold for Above";
const ABOVE_WARNING =
  "Warning threshold must be less than critical threshold for Above";

const PROVIDER = "service_provider_error_rates";

// The form's values for a static provider alert, as the edit form holds them.
const providerValues = (overrides = {}) => ({
  name: "Providers failing",
  metric_type: PROVIDER,
  metric: "",
  alert_frequency: 5,
  filters: [],
  threshold_type: "static",
  auto_threshold_time_window: 5,
  threshold_operator: "greater_than",
  threshold_metric_value: "",
  critical_threshold_value: 0.6,
  warning_threshold_value: 0.2,
  warning_optional: false,
  notification: {
    method: "email",
    emails: ["alerts@example.test"],
    slack: { webhookUrl: "", notes: "" },
  },
  ...overrides,
});

const issuesAt = (values, path) => {
  const result = AlertConfigValidationSchema.safeParse(values);
  if (result.success) return [];
  return result.error.issues
    .filter((issue) => issue.path.join(".") === path)
    .map((issue) => issue.message);
};

const isValid = (values) =>
  AlertConfigValidationSchema.safeParse(values).success;

// A saved static provider alert, as the server returns it.
const savedProvider = {
  id: "monitor-p",
  name: "Providers failing",
  metric_type: PROVIDER,
  threshold_type: "static",
  threshold_operator: "greater_than",
  alert_frequency: 5,
  notification_emails: ["alerts@example.test"],
};

describe("a static provider error rate threshold is a share from 0 to 1", () => {
  it("refuses a critical above 1 under the critical only", () => {
    const values = providerValues({ critical_threshold_value: 96 });

    expect(issuesAt(values, "critical_threshold_value")).toEqual([
      OUT_OF_RANGE,
    ]);
    expect(issuesAt(values, "warning_threshold_value")).toEqual([]);
  });

  it("refuses both values of a row saved at 95 and 90", () => {
    const values = providerValues({
      critical_threshold_value: 95,
      warning_threshold_value: 90,
    });

    expect(issuesAt(values, "critical_threshold_value")).toEqual([
      OUT_OF_RANGE,
    ]);
    expect(issuesAt(values, "warning_threshold_value")).toEqual([OUT_OF_RANGE]);
  });

  it("refuses a value below 0 for Below", () => {
    const values = providerValues({
      threshold_operator: "less_than",
      critical_threshold_value: -0.5,
      warning_threshold_value: 0.2,
    });

    expect(issuesAt(values, "critical_threshold_value")).toEqual([
      OUT_OF_RANGE,
    ]);
    expect(issuesAt(values, "warning_threshold_value")).toEqual([]);
  });

  it("names the range first and still checks the order of the two values", () => {
    const values = providerValues({
      critical_threshold_value: 0.5,
      warning_threshold_value: 2,
    });

    expect(issuesAt(values, "warning_threshold_value")).toEqual([
      OUT_OF_RANGE,
      ABOVE_WARNING,
    ]);
    expect(issuesAt(values, "critical_threshold_value")).toEqual([
      ABOVE_CRITICAL,
    ]);
  });

  it("checks the critical of an alert saved without a warning", () => {
    const values = providerValues({
      critical_threshold_value: 5,
      warning_threshold_value: "",
      warning_optional: true,
    });

    expect(issuesAt(values, "critical_threshold_value")).toEqual([
      OUT_OF_RANGE,
    ]);
    expect(issuesAt(values, "warning_threshold_value")).toEqual([]);
  });

  it("accepts 0, 1 and the values between", () => {
    expect(
      isValid(
        providerValues({
          critical_threshold_value: 1,
          warning_threshold_value: 0,
        }),
      ),
    ).toBe(true);
    expect(
      isValid(
        providerValues({
          threshold_operator: "less_than",
          critical_threshold_value: 0,
          warning_threshold_value: 1,
        }),
      ),
    ).toBe(true);
    expect(
      isValid(
        providerValues({
          critical_threshold_value: 0.2,
          warning_threshold_value: 0.1,
        }),
      ),
    ).toBe(true);
  });

  it("keeps an empty optional warning valid", () => {
    expect(
      isValid(
        providerValues({
          critical_threshold_value: 0.5,
          warning_threshold_value: "",
          warning_optional: true,
        }),
      ),
    ).toBe(true);
  });

  it("leaves a percentage change provider alert and other metrics alone", () => {
    expect(
      isValid(
        providerValues({
          threshold_type: "percentage_change",
          critical_threshold_value: 50,
          warning_threshold_value: 20,
        }),
      ),
    ).toBe(true);
    expect(
      isValid(
        providerValues({
          metric_type: "count_of_errors",
          critical_threshold_value: 95,
          warning_threshold_value: 90,
        }),
      ),
    ).toBe(true);
  });
});

describe("the values a provider error rate alert starts with", () => {
  it("are 0.2 and 0.1 for a new static alert, swapped for Below", () => {
    const staticDefaults = (operator) =>
      getThresholdValueDefaults(PROVIDER, "static", operator);

    expect(staticDefaults("greater_than")).toEqual({
      critical: 0.2,
      warning: 0.1,
    });
    expect(staticDefaults("less_than")).toEqual({
      critical: 0.1,
      warning: 0.2,
    });

    const above = getDefaultAlertConfigValues({ metricType: PROVIDER });
    expect(above.critical_threshold_value).toBe(0.2);
    expect(above.warning_threshold_value).toBe(0.1);

    const below = getDefaultAlertConfigValues({
      metricType: PROVIDER,
      thresholdOperator: "less_than",
    });
    expect(below.critical_threshold_value).toBe(0.1);
    expect(below.warning_threshold_value).toBe(0.2);
  });

  it("keep the placeholders for a percentage change alert", () => {
    expect(
      getThresholdValueDefaults(PROVIDER, "percentage_change", "greater_than"),
    ).toEqual({
      critical: ALERT_CONFIG_DEFAULTS.critical_threshold_value,
      warning: ALERT_CONFIG_DEFAULTS.warning_threshold_value,
    });
  });

  it("are the saved values for an alert being edited", () => {
    const saved = (overrides) =>
      getDefaultAlertConfigValues(
        normalizeAlertDetail({ ...savedProvider, ...overrides }),
      );

    const earlier = saved({
      threshold_rule: "earlier",
      critical_threshold_value: 95,
      warning_threshold_value: 90,
    });
    expect(earlier.critical_threshold_value).toBe(95);
    expect(earlier.warning_threshold_value).toBe(90);

    const current = saved({
      threshold_rule: "current",
      critical_threshold_value: 0.35,
      warning_threshold_value: 0.15,
    });
    expect(current.critical_threshold_value).toBe(0.35);
    expect(current.warning_threshold_value).toBe(0.15);

    const noWarning = saved({
      critical_threshold_value: 0.5,
      warning_threshold_value: null,
    });
    expect(noWarning.critical_threshold_value).toBe(0.5);
    expect(noWarning.warning_threshold_value).toBe("");
    expect(noWarning.warning_optional).toBe(true);
  });
});
