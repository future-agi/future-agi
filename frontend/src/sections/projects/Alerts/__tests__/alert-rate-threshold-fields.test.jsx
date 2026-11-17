import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SnackbarProvider } from "notistack";
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
import axios from "src/utils/axios";
import { registerOfflineIcon } from "src/components/iconify/registry";
import bundled from "src/components/iconify/offline-icons.json";
import { normalizeAlertDetail } from "../common";
import AlertSettingsForm from "../components/AlertSettingsForm";
import {
  ALERT_CONFIG_DEFAULTS,
  AlertConfigValidationSchema,
  getDefaultAlertConfigValues,
  getThresholdValueDefaults,
} from "../components/validation";
import { resetAlertStoreState, useAlertStore } from "../store/useAlertStore";
import {
  resetAlertSheetStoreState,
  useAlertSheetStore,
} from "../store/useAlertSheetStore";

// LLM API failure rates, error rates for function calling and the error-free
// session rate are shares from 0 to 1, as the service provider error rate is,
// and a static alert compares the raw share with its thresholds. Their value
// fields say what the share counts and that it runs from 0 to 1, and refuse a
// value outside that range with the range sentence. The two failure shares
// rise as calls fail, so a new alert on them starts Above 0.2 and 0.1. The
// error-free session rate falls as sessions fail and sits near 1 on a healthy
// project, so a new alert on it starts Below, with critical 0.9 and warning
// 0.95, and its range sentence gives 0.9 as the example. A percentage change
// alert on them compares a percent, so none of that applies to it.
const RATE_METRICS = [
  {
    metricType: "llm_api_failure_rates",
    help: "Share of LLM calls that failed, from 0 to 1",
    outOfRange:
      "Enter a share from 0 to 1, for example 0.2 for 20% of LLM calls.",
  },
  {
    metricType: "error_rates_for_function_calling",
    help: "Share of function calls that had an error, from 0 to 1",
    outOfRange:
      "Enter a share from 0 to 1, for example 0.2 for 20% of function calls.",
  },
  {
    metricType: "error_free_session_rates",
    help: "Share of sessions with no errors, from 0 to 1",
    outOfRange:
      "Enter a share from 0 to 1, for example 0.9 for 90% of sessions.",
  },
];

const ERROR_FREE = RATE_METRICS.find(
  ({ metricType }) => metricType === "error_free_session_rates",
);
const FAILURE_RATE_METRICS = RATE_METRICS.filter((rate) => rate !== ERROR_FREE);

const BELOW_CRITICAL =
  "Critical threshold must be less than warning threshold for Below";
const BELOW_WARNING =
  "Warning threshold must be greater than critical threshold for Below";

// The server fires a static alert when the share is strictly past a level in
// the alert's direction.
const breaches = (share, operator, level) =>
  operator === "less_than" ? share < level : share > level;
const DIRECTIONS = { Above: "greater_than", Below: "less_than" };

const PROVIDER = "service_provider_error_rates";
const PROVIDER_HELP = "Share of providers that had an error, from 0 to 1";
const PROVIDER_OUT_OF_RANGE =
  "Enter a share from 0 to 1, for example 0.2 for 20% of providers.";
const PLACEHOLDERS = {
  critical: ALERT_CONFIG_DEFAULTS.critical_threshold_value,
  warning: ALERT_CONFIG_DEFAULTS.warning_threshold_value,
};

// The form's Iconify glyphs: the pickers' arrow and clear buttons, and the
// filter bar's add button.
for (const name of [
  "solar:alt-arrow-up-linear",
  "solar:close-linear",
  "solar:add-linear",
]) {
  registerOfflineIcon(name, bundled[name]);
}

// The form's values for a static alert, as the form holds them.
const formValues = (overrides = {}) => ({
  name: "Calls failing",
  metric_type: "llm_api_failure_rates",
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

describe("a static threshold on these shares runs from 0 to 1", () => {
  it.each(RATE_METRICS)(
    "refuses 1.5 under the critical of a $metricType alert",
    ({ metricType, outOfRange }) => {
      const values = formValues({
        metric_type: metricType,
        critical_threshold_value: 1.5,
        warning_threshold_value: 0.1,
      });

      expect(issuesAt(values, "critical_threshold_value")).toEqual([
        outOfRange,
      ]);
      expect(issuesAt(values, "warning_threshold_value")).toEqual([]);
    },
  );

  it.each(RATE_METRICS)(
    "refuses 1.5 under the warning of a Below $metricType alert",
    ({ metricType, outOfRange }) => {
      const values = formValues({
        metric_type: metricType,
        threshold_operator: "less_than",
        critical_threshold_value: 0.1,
        warning_threshold_value: 1.5,
      });

      expect(issuesAt(values, "warning_threshold_value")).toEqual([outOfRange]);
      expect(issuesAt(values, "critical_threshold_value")).toEqual([]);
    },
  );

  it.each(RATE_METRICS)(
    "refuses a $metricType alert saved at 400 and 300 under both values",
    ({ metricType, outOfRange }) => {
      const values = formValues({
        metric_type: metricType,
        critical_threshold_value: 400,
        warning_threshold_value: 300,
      });

      expect(issuesAt(values, "critical_threshold_value")).toEqual([
        outOfRange,
      ]);
      expect(issuesAt(values, "warning_threshold_value")).toEqual([outOfRange]);
    },
  );

  it.each(RATE_METRICS)(
    "accepts 0, 1 and the values between for $metricType",
    ({ metricType }) => {
      expect(
        isValid(
          formValues({
            metric_type: metricType,
            critical_threshold_value: 1,
            warning_threshold_value: 0,
          }),
        ),
      ).toBe(true);
      expect(
        isValid(
          formValues({
            metric_type: metricType,
            threshold_operator: "less_than",
            critical_threshold_value: 0,
            warning_threshold_value: 1,
          }),
        ),
      ).toBe(true);
      expect(
        isValid(
          formValues({
            metric_type: metricType,
            critical_threshold_value: 0.2,
            warning_threshold_value: 0.1,
          }),
        ),
      ).toBe(true);
    },
  );

  it.each(RATE_METRICS)(
    "leaves a percentage change $metricType alert with no range to check",
    ({ metricType }) => {
      expect(
        isValid(
          formValues({
            metric_type: metricType,
            threshold_type: "percentage_change",
            critical_threshold_value: 50,
            warning_threshold_value: 20,
          }),
        ),
      ).toBe(true);
    },
  );
});

describe("the values a static alert on these shares starts with", () => {
  it.each(FAILURE_RATE_METRICS)(
    "are 0.2 and 0.1 for a new $metricType alert, swapped for Below",
    ({ metricType }) => {
      expect(
        getThresholdValueDefaults(metricType, "static", "greater_than"),
      ).toEqual({ critical: 0.2, warning: 0.1 });
      expect(
        getThresholdValueDefaults(metricType, "static", "less_than"),
      ).toEqual({ critical: 0.1, warning: 0.2 });

      const above = getDefaultAlertConfigValues({ metricType });
      expect(above.critical_threshold_value).toBe(0.2);
      expect(above.warning_threshold_value).toBe(0.1);

      const below = getDefaultAlertConfigValues({
        metricType,
        thresholdOperator: "less_than",
      });
      expect(below.critical_threshold_value).toBe(0.1);
      expect(below.warning_threshold_value).toBe(0.2);
    },
  );

  it("are Below, critical 0.9 and warning 0.95, for a new error_free_session_rates alert, swapped for Above", () => {
    const { metricType } = ERROR_FREE;
    expect(
      getThresholdValueDefaults(metricType, "static", "less_than"),
    ).toEqual({ critical: 0.9, warning: 0.95 });
    expect(
      getThresholdValueDefaults(metricType, "static", "greater_than"),
    ).toEqual({ critical: 0.95, warning: 0.9 });

    const opened = getDefaultAlertConfigValues({ metricType });
    expect(opened.threshold_operator).toBe("less_than");
    expect(opened.critical_threshold_value).toBe(0.9);
    expect(opened.warning_threshold_value).toBe(0.95);
    // The form's own check takes the pair in that order.
    expect(
      isValid(
        formValues({
          metric_type: metricType,
          threshold_operator: opened.threshold_operator,
          critical_threshold_value: opened.critical_threshold_value,
          warning_threshold_value: opened.warning_threshold_value,
        }),
      ),
    ).toBe(true);

    const above = getDefaultAlertConfigValues({
      metricType,
      thresholdOperator: "greater_than",
    });
    expect(above.threshold_operator).toBe("greater_than");
    expect(above.critical_threshold_value).toBe(0.95);
    expect(above.warning_threshold_value).toBe(0.9);
  });

  it.each(RATE_METRICS)(
    "keep the placeholders for a percentage change $metricType alert",
    ({ metricType }) => {
      expect(
        getThresholdValueDefaults(
          metricType,
          "percentage_change",
          "greater_than",
        ),
      ).toEqual(PLACEHOLDERS);
    },
  );

  it.each(RATE_METRICS)(
    "are the saved values for a $metricType alert being edited",
    ({ metricType }) => {
      const saved = (critical, warning) =>
        getDefaultAlertConfigValues(
          normalizeAlertDetail({
            id: "monitor-rate",
            name: "Calls failing",
            metric_type: metricType,
            threshold_type: "static",
            threshold_operator: "greater_than",
            alert_frequency: 5,
            notification_emails: ["alerts@example.test"],
            critical_threshold_value: critical,
            warning_threshold_value: warning,
          }),
        );

      const inRange = saved(0.35, 0.15);
      expect(inRange.critical_threshold_value).toBe(0.35);
      expect(inRange.warning_threshold_value).toBe(0.15);

      const outOfRange = saved(400, 300);
      expect(outOfRange.critical_threshold_value).toBe(400);
      expect(outOfRange.warning_threshold_value).toBe(300);
    },
  );
});

describe("the metrics outside these three keep their own scale", () => {
  it("leaves the service provider error rate's range sentence and values as they were", () => {
    expect(
      issuesAt(
        formValues({ metric_type: PROVIDER, critical_threshold_value: 1.5 }),
        "critical_threshold_value",
      ),
    ).toEqual([PROVIDER_OUT_OF_RANGE]);
    expect(
      getThresholdValueDefaults(PROVIDER, "static", "greater_than"),
    ).toEqual({ critical: 0.2, warning: 0.1 });
  });

  it.each([
    "count_of_errors",
    "span_response_time",
    "llm_response_time",
    "token_usage",
    "daily_tokens_spent",
    "monthly_tokens_spent",
  ])("starts %s at 400 and 300 and checks no range", (metricType) => {
    expect(
      getThresholdValueDefaults(metricType, "static", "greater_than"),
    ).toEqual(PLACEHOLDERS);
    expect(
      isValid(
        formValues({
          metric_type: metricType,
          critical_threshold_value: 95,
          warning_threshold_value: 90,
        }),
      ),
    ).toBe(true);
  });

  it("starts a static eval alert at 0.4 and 0.3", () => {
    expect(
      getThresholdValueDefaults("evaluation_metrics", "static", "greater_than"),
    ).toEqual({ critical: 0.4, warning: 0.3 });
  });
});

let queryClient;

const field = (name) => document.querySelector(`[data-alert-field="${name}"]`);

// The text a value field shows under itself, as its helper text.
const underField = (name) =>
  within(field(name).closest(".MuiFormControl-root"));

const renderNewAlert = (metricType) => {
  vi.spyOn(axios, "get").mockResolvedValue({ data: { result: [] } });
  useAlertStore.setState({
    openSheetView: null,
    selectedProject: "project-new",
    alertType: metricType,
  });
  useAlertSheetStore.setState({
    alertRuleDetails: null,
    gridRef: { current: null },
  });
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

const fieldsShown = async () => {
  await waitFor(() =>
    expect(field("critical-threshold-value")).toBeInTheDocument(),
  );
  expect(field("warning-threshold-value")).toBeInTheDocument();
};

const typeValue = async (name, value) => {
  await act(async () => {
    fireEvent.change(field(name), { target: { value } });
  });
};

// Picks a metric in the form's Metric picker, by the name the picker shows.
const chooseMetric = async (label) => {
  fireEvent.click(field("metric-type"));
  const option = await screen.findByRole("menuitem", { name: label });
  await act(async () => {
    fireEvent.click(within(option).getByText(label));
  });
  await waitFor(() => expect(field("metric-type")).toHaveValue(label));
};

// The form checks the values on promises; one timer tick lets that check
// finish and any reason render, so a reason that is missing is really missing.
const settleValidation = async () => {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};

// A value field with a reason is marked invalid, and the reason is the text
// under it in the same render.
const shownAsRefused = async (name) => {
  await waitFor(() =>
    expect(field(name)).toHaveAttribute("aria-invalid", "true"),
  );
};

describe("the value fields of a new static alert on these shares", () => {
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

  it.each(FAILURE_RATE_METRICS)(
    "start a new $metricType alert at 0.2 and 0.1, with what the share counts under both",
    async ({ metricType, help }) => {
      renderNewAlert(metricType);
      await fieldsShown();

      expect(field("critical-threshold-value")).toHaveValue(0.2);
      expect(field("warning-threshold-value")).toHaveValue(0.1);
      expect(
        underField("critical-threshold-value").queryByText(help),
      ).not.toBeNull();
      expect(
        underField("warning-threshold-value").queryByText(help),
      ).not.toBeNull();
    },
  );

  it("start a new error_free_session_rates alert Below at 0.9 and 0.95, with what the share counts under both", async () => {
    const { metricType, help } = ERROR_FREE;
    renderNewAlert(metricType);
    await fieldsShown();

    expect(field("critical-threshold-operator")).toHaveValue("Below");
    expect(field("warning-threshold-operator")).toHaveValue("Below");
    expect(field("critical-threshold-value")).toHaveValue(0.9);
    expect(field("warning-threshold-value")).toHaveValue(0.95);
    expect(
      underField("critical-threshold-value").queryByText(help),
    ).not.toBeNull();
    expect(
      underField("warning-threshold-value").queryByText(help),
    ).not.toBeNull();
  });

  it("opens a new error_free_session_rates alert that a healthy share of 0.99 breaches at neither level", async () => {
    renderNewAlert(ERROR_FREE.metricType);
    await fieldsShown();

    const opened = {
      direction: field("critical-threshold-operator").value,
      critical: Number(field("critical-threshold-value").value),
      warning: Number(field("warning-threshold-value").value),
    };
    expect(opened).toEqual({
      direction: "Below",
      critical: 0.9,
      warning: 0.95,
    });
    await settleValidation();
    expect(screen.queryByText(BELOW_CRITICAL)).toBeNull();
    expect(screen.queryByText(BELOW_WARNING)).toBeNull();

    const operator = DIRECTIONS[opened.direction];
    // Nearly every session free of errors: neither level fires.
    expect(breaches(0.99, operator, opened.warning)).toBe(false);
    expect(breaches(0.99, operator, opened.critical)).toBe(false);
    // As the share falls, the warning fires first, then the critical.
    expect(breaches(0.93, operator, opened.warning)).toBe(true);
    expect(breaches(0.93, operator, opened.critical)).toBe(false);
    expect(breaches(0.85, operator, opened.critical)).toBe(true);
  });

  it("moves a new alert to Below 0.9 and 0.95 on a switch to the error-free session rate, and to Above 0.2 and 0.1 on a switch on to LLM API failure rates", async () => {
    renderNewAlert("count_of_errors");
    await fieldsShown();
    expect(field("critical-threshold-operator")).toHaveValue("Above");
    expect(field("critical-threshold-value")).toHaveValue(400);

    await chooseMetric("Error free session rate");

    expect(field("critical-threshold-operator")).toHaveValue("Below");
    expect(field("critical-threshold-value")).toHaveValue(0.9);
    expect(field("warning-threshold-value")).toHaveValue(0.95);
    await settleValidation();
    expect(screen.queryByText(BELOW_CRITICAL)).toBeNull();
    expect(screen.queryByText(BELOW_WARNING)).toBeNull();

    await chooseMetric("LLM API failure rates");

    expect(field("critical-threshold-operator")).toHaveValue("Above");
    expect(field("critical-threshold-value")).toHaveValue(0.2);
    expect(field("warning-threshold-value")).toHaveValue(0.1);
  });

  it.each(RATE_METRICS)(
    "say why a typed 1.5 is refused in a new $metricType alert",
    async ({ metricType, outOfRange }) => {
      renderNewAlert(metricType);
      await fieldsShown();

      await typeValue("critical-threshold-value", "1.5");
      await waitFor(() =>
        expect(field("critical-threshold-value")).toHaveValue(1.5),
      );
      await shownAsRefused("critical-threshold-value");

      expect(
        underField("critical-threshold-value").queryByText(outOfRange),
      ).not.toBeNull();
      expect(
        underField("warning-threshold-value").queryByText(outOfRange),
      ).toBeNull();
    },
  );

  it.each(RATE_METRICS)(
    "keep a typed minus in a new $metricType alert's warning and say why it is refused",
    async ({ metricType, outOfRange }) => {
      renderNewAlert(metricType);
      await fieldsShown();

      await typeValue("warning-threshold-value", "-0.1");
      await settleValidation();

      expect(field("warning-threshold-value")).toHaveValue(-0.1);
      await shownAsRefused("warning-threshold-value");
      expect(
        underField("warning-threshold-value").queryByText(outOfRange),
      ).not.toBeNull();
    },
  );

  it.each(RATE_METRICS)(
    "drop the 0 to 1 words and go back to 400 and 300 when a new $metricType alert switches to percentage change",
    async ({ metricType, help }) => {
      renderNewAlert(metricType);
      await fieldsShown();

      await act(async () => {
        fireEvent.click(
          document.querySelector(
            'input[name="threshold_type"][value="percentage_change"]',
          ),
        );
      });
      await waitFor(() =>
        expect(field("critical-threshold-value")).toHaveValue(400),
      );
      expect(field("warning-threshold-value")).toHaveValue(300);
      await settleValidation();

      expect(
        underField("critical-threshold-value").queryByText(help),
      ).toBeNull();
      expect(
        underField("warning-threshold-value").queryByText(help),
      ).toBeNull();
    },
  );

  it("keeps the service provider error rate's own words and range sentence", async () => {
    renderNewAlert(PROVIDER);
    await fieldsShown();

    expect(field("critical-threshold-value")).toHaveValue(0.2);
    expect(field("warning-threshold-value")).toHaveValue(0.1);
    expect(
      underField("critical-threshold-value").queryByText(PROVIDER_HELP),
    ).not.toBeNull();

    await typeValue("critical-threshold-value", "1.5");
    await waitFor(() =>
      expect(field("critical-threshold-value")).toHaveValue(1.5),
    );
    await shownAsRefused("critical-threshold-value");

    expect(
      underField("critical-threshold-value").queryByText(PROVIDER_OUT_OF_RANGE),
    ).not.toBeNull();
  });

  it("leaves a new Count of errors alert at 400 and 300 with no 0 to 1 words", async () => {
    renderNewAlert("count_of_errors");
    await fieldsShown();

    expect(field("critical-threshold-value")).toHaveValue(400);
    expect(field("warning-threshold-value")).toHaveValue(300);
    expect(
      underField("critical-threshold-value").queryByText(/from 0 to 1/),
    ).toBeNull();
    expect(
      underField("warning-threshold-value").queryByText(/from 0 to 1/),
    ).toBeNull();
  });
});
