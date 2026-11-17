import { z } from "zod";
import { v4 as uuidv4 } from "uuid";

// What each share metric counts, as the reason under a value outside 0 to 1
// names it.
const SHARE_SCALE_UNITS = {
  service_provider_error_rates: "providers",
  llm_api_failure_rates: "LLM calls",
  error_rates_for_function_calling: "function calls",
  error_free_session_rates: "sessions",
};

// The error-free session rate sits near 1 when healthy, so its sentence gives
// 0.9 as the example; the other shares give 0.2.
const shareRangeMessage = (metricType) =>
  metricType === "error_free_session_rates"
    ? "Enter a share from 0 to 1, for example 0.9 for 90% of sessions."
    : `Enter a share from 0 to 1, for example 0.2 for 20% of ${SHARE_SCALE_UNITS[metricType]}.`;

// A static alert on the service provider error rate, LLM API failure rates,
// error rates for function calling or the error-free session rate compares a
// share, from 0 to 1, with its thresholds; a percentage change one compares a
// percent and has no such range.
export const isShareScale = (metricType, thresholdType) =>
  thresholdType === "static" &&
  Object.keys(SHARE_SCALE_UNITS).includes(metricType);

// The error-free session rate is the one share that is higher when healthy:
// near 1 on a project whose sessions mostly finish without an error. A static
// alert on it watches for the share falling, so a new one starts Below.
const isFallingShareScale = (metricType, thresholdType) =>
  metricType === "error_free_session_rates" && thresholdType === "static";

export const AlertConfigValidationSchema = z
  .object({
    name: z.string().min(1, {
      message: "Name is required",
    }),
    metric_type: z.string().min(1, "Metric is required"),
    metric: z.string().optional(),
    alert_frequency: z.coerce.number().min(1, "Interval is required"),
    filters: z
      .array(
        z.object({
          id: z.string().optional(),
          propertyId: z.string().optional(),
          property: z.string().optional(),
          filterConfig: z
            .object({
              filterType: z.string().optional(),
              filterOp: z.any().optional(),
              filterValue: z.any().optional(),
            })
            .optional(),
        }),
      )
      .optional(),
    threshold_type: z.enum(
      ["static", "percentage_change", "anomaly_detection"],
      {
        required_error: "Select an alert type",
      },
    ),
    auto_threshold_time_window: z.union([z.string(), z.number()]).optional(),
    threshold_operator: z.enum(["greater_than", "less_than"], {
      message: "Select a critical threshold",
    }),
    threshold_metric_value: z.string().optional(),
    critical_threshold_value: z.preprocess(
      (val) =>
        val === "" || val === null || val === undefined
          ? undefined
          : Number(val),
      z
        .number({
          message: "Critical value is required",
          invalid_type_error: "Critical value must be a number",
        })
        .optional(),
    ),
    warning_threshold_value: z.preprocess(
      (val) =>
        val === "" || val === null || val === undefined
          ? undefined
          : Number(val),
      z
        .number({
          required_error: "Warning value is required",
          invalid_type_error: "Warning value must be a number",
        })
        .optional(),
    ),
    // Set by getDefaultAlertConfigValues for an alert saved without a warning:
    // it keeps no warning unless the person adds one.
    warning_optional: z.boolean().optional(),
    // Set by getDefaultAlertConfigValues for a level saved as infinity or
    // not-a-number: the words its empty field shows. While the field stays
    // empty the level keeps what was saved, and saving leaves it out.
    critical_threshold_label: z.string().optional(),
    warning_threshold_label: z.string().optional(),
    notification: z
      .object({
        method: z.enum(["email", "slack", "webhook", "pagerduty"], {
          required_error: "Select notification method",
        }),
        emails: z
          .array(z.string().email("Invalid email address"))
          .max(5, "To add more email id's contact sales")
          .optional(),
        slack: z
          .object({
            webhookUrl: z.string().optional(),
            notes: z.string().optional(),
          })
          .optional(),
        webhook: z
          .object({
            url: z.string().optional(),
          })
          .optional(),
        pagerduty: z
          .object({
            routingKey: z.string().trim().max(255).optional(),
          })
          .optional(),
      })
      .superRefine((notif, ctx) => {
        if (notif.method === "pagerduty" && !notif.pagerduty?.routingKey) {
          ctx.addIssue({
            path: ["pagerduty", "routingKey"],
            code: "custom",
            message: "PagerDuty routing key is required",
          });
        }

        if (notif.method === "email") {
          if (!notif.emails || notif.emails.length === 0) {
            ctx.addIssue({
              path: ["emails"],
              code: "custom",
              message: "Emails are required",
            });
          }
        }

        if (notif.method === "slack") {
          if (!notif.slack || !notif.slack.webhookUrl) {
            ctx.addIssue({
              path: ["slack", "webhookUrl"],
              code: "custom",
              message: "Webhook URL is required",
            });
          } else {
            const urlPattern = /^(https?:\/\/)[^\s/$.?#].[^\s]*$/i;
            if (!urlPattern.test(notif.slack.webhookUrl)) {
              ctx.addIssue({
                path: ["slack", "webhookUrl"],
                code: "custom",
                message: "Invalid Slack webhook URL",
              });
            }
          }
        }

        if (notif.method === "webhook") {
          if (!notif.webhook || !notif.webhook.url) {
            ctx.addIssue({
              path: ["webhook", "url"],
              code: "custom",
              message: "Webhook URL is required",
            });
          } else {
            const urlPattern = /^(https?:\/\/)[^\s/$.?#].[^\s]*$/i;
            if (!urlPattern.test(notif.webhook.url)) {
              ctx.addIssue({
                path: ["webhook", "url"],
                code: "custom",
                message: "Invalid webhook URL",
              });
            }
          }
        }
      }),
  })
  .superRefine((data, ctx) => {
    const {
      warning_threshold_value,
      critical_threshold_value,
      threshold_operator,
      threshold_type,
      metric_type,
      metric,
      warning_optional,
      critical_threshold_label,
    } = data;

    // `metric` is conditionally required, and the condition is inverted:
    // the backend requires it for evaluation_metrics ("Metric is required
    // for evaluation metrics.") and rejects it for every other metric type
    // ("Metric and threshold_metric_value are not allowed..."). The payload
    // builder already omits it for non-eval types, so only the required
    // half needs enforcing here — without it an eval alert submits with
    // metric:"" and fails server-side instead of inline.
    // See tracer/serializers/monitor.py::_validate_metric_type.
    if (metric_type === "evaluation_metrics" && !metric) {
      ctx.addIssue({
        path: ["metric"],
        code: "custom",
        message: "Metric is required for evaluation metrics",
      });
    }

    // Threshold values are NOT required for anomaly_detection
    const needsThresholds = threshold_type !== "anomaly_detection";

    if (needsThresholds) {
      // Check presence. An empty critical with saved words keeps the level.
      if (critical_threshold_value === undefined && !critical_threshold_label) {
        ctx.addIssue({
          path: ["critical_threshold_value"],
          code: "custom",
          message: "Critical value is required",
        });
      }

      if (warning_threshold_value === undefined && !warning_optional) {
        ctx.addIssue({
          path: ["warning_threshold_value"],
          code: "custom",
          message: "Warning value is required",
        });
      }

      // Before the order checks: the form shows a field's first reason, and
      // a value outside 0 to 1 is the one to fix first. An empty optional
      // warning is not a number and is left alone.
      if (isShareScale(metric_type, threshold_type)) {
        [
          ["critical_threshold_value", critical_threshold_value],
          ["warning_threshold_value", warning_threshold_value],
        ].forEach(([path, value]) => {
          if (typeof value === "number" && (value < 0 || value > 1)) {
            ctx.addIssue({
              path: [path],
              code: "custom",
              message: shareRangeMessage(metric_type),
            });
          }
        });
      }

      // Logical comparison - Add validation errors to BOTH fields
      if (
        typeof warning_threshold_value === "number" &&
        typeof critical_threshold_value === "number"
      ) {
        if (threshold_operator === "greater_than") {
          if (warning_threshold_value >= critical_threshold_value) {
            ctx.addIssue({
              path: ["warning_threshold_value"],
              code: "custom",
              message:
                "Warning threshold must be less than critical threshold for Above",
            });
            ctx.addIssue({
              path: ["critical_threshold_value"],
              code: "custom",
              message:
                "Critical threshold must be greater than warning threshold for Above",
            });
          }
        }

        if (threshold_operator === "less_than") {
          if (warning_threshold_value <= critical_threshold_value) {
            ctx.addIssue({
              path: ["warning_threshold_value"],
              code: "custom",
              message:
                "Warning threshold must be greater than critical threshold for Below",
            });
            ctx.addIssue({
              path: ["critical_threshold_value"],
              code: "custom",
              message:
                "Critical threshold must be less than warning threshold for Below",
            });
          }
        }
      }
    }

    // Time window required for percentage change
    if (
      threshold_type === "percentage_change" &&
      !data.auto_threshold_time_window
    ) {
      ctx.addIssue({
        path: ["auto_threshold_time_window"],
        code: "custom",
        message: "Compare percentage is required for percentage alerts",
      });
    }
  });

// The same blank-or-number rule the schema's threshold preprocessors apply on
// Save, for callers that read raw form values (Historical Preview): a blank
// level is not sent, a typed level is sent as a number.
const toThresholdNumber = (value) =>
  value === "" || value === null || value === undefined ? undefined : Number(value);

export function previewThresholdValues(values = {}) {
  return {
    ...values,
    critical_threshold_value: toThresholdNumber(values.critical_threshold_value),
    warning_threshold_value: toThresholdNumber(values.warning_threshold_value),
  };
}

export function transformFilterResponse(rawFilter) {
  if (!rawFilter) return [];

  const filters = [];

  // Observation types → multiple filters
  const observationTypes =
    rawFilter?.observationType || rawFilter?.observation_type;
  if (Array.isArray(observationTypes)) {
    observationTypes.forEach((type) => {
      filters.push({
        id: uuidv4(),
        propertyId: "",
        property: "observationType",
        filterConfig: {
          filterType: "text",
          filterOp: "equals",
          filterValue: type,
        },
      });
    });
  }

  const spanAttributeFilters =
    rawFilter?.spanAttributesFilters || rawFilter?.span_attributes_filters;
  if (Array.isArray(spanAttributeFilters)) {
    spanAttributeFilters.forEach((filter) => {
      const filterConfig = filter?.filterConfig || filter?.filter_config || {};
      filters.push({
        id: uuidv4(),
        propertyId: filter.columnId || filter.column_id,
        property: "attributes",
        filterConfig: {
          filterType: filterConfig.filterType || filterConfig.filter_type,
          filterOp: filterConfig.filterOp || filterConfig.filter_op,
          filterValue:
            "filterValue" in filterConfig
              ? filterConfig.filterValue
              : filterConfig.filter_value,
        },
      });
    });
  }

  return filters;
}

export const ALERT_CONFIG_DEFAULTS = Object.freeze({
  alert_frequency: 5,
  threshold_type: "static",
  auto_threshold_time_window: 5,
  threshold_operator: "greater_than",
  threshold_metric_value: "",
  critical_threshold_value: 400,
  warning_threshold_value: 300,
});

const readAlertField = (config, camelKey, snakeKey) =>
  config?.[camelKey] ?? config?.[snakeKey];

// A saved 0 is a value, not an absence, so validity beats truthiness here.
const numberOr = (value, fallback) => {
  const parsed =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
};

// The words the server sends beside a saved level that is infinity or
// not-a-number, whose value it sends as null; "" for a level with a number
// and for an alert not yet saved.
const savedLevelWords = (config, level) => {
  const value = readAlertField(
    config,
    `${level}ThresholdValue`,
    `${level}_threshold_value`,
  );
  const words = readAlertField(
    config,
    `${level}ThresholdLabel`,
    `${level}_threshold_label`,
  );
  return config?.id != null && value == null && words ? words : "";
};

// Eval metrics compare on a 0-1 fraction, but only under a static threshold —
// percentage_change divides the same field by 100, so it wants a percent
// like a system metric does. Reusing one number across scales is exactly
// the TH-7789 bug (a value typed for one scale silently means something
// else once the field's scale changes), so callers should re-derive these
// on every metric_type/threshold_type change rather than leaving a stale
// value in place.
function getScaleThresholdDefaults(metricType, thresholdType) {
  if (metricType === "evaluation_metrics" && thresholdType === "static") {
    return { critical: 0.4, warning: 0.3 };
  }
  // A static error-free session rate is a share from 0 to 1 that falls as
  // sessions fail, and a new alert on it starts Below. Written the Above way
  // round like every pair here, Below reads critical 0.9 and warning 0.95: as
  // the share falls, the warning fires first. A healthy share near 1 is past
  // neither level.
  if (isFallingShareScale(metricType, thresholdType)) {
    return { critical: 0.95, warning: 0.9 };
  }
  // A static service provider error rate, LLM API failure rate or error rate
  // for function calling is a share, also 0 to 1, that rises as calls fail: a
  // new alert starts at 0.2 and 0.1. An alert being edited opens with its
  // saved values; switching its metric or alert type onto or off a share
  // replaces them with that scale's values, as for eval alerts.
  if (isShareScale(metricType, thresholdType)) {
    return { critical: 0.2, warning: 0.1 };
  }
  // Every other combination keeps the pre-existing placeholder rather than
  // going blank — the form validates on change and AlertSettingsForm
  // re-triggers these two fields after a mode switch, so an empty value
  // shows a red "required" error before the user has had a chance to type
  // anything. A wrong-but-present placeholder is less jarring than that,
  // even though it's still a guess for these modes.
  return {
    critical: ALERT_CONFIG_DEFAULTS.critical_threshold_value,
    warning: ALERT_CONFIG_DEFAULTS.warning_threshold_value,
  };
}

// The pair is ordered for the alert's direction: Above wants critical over
// warning, Below wants critical under warning. Handing a Below alert the
// Above pair fails the schema's own check, and the form can't be saved.
export function getThresholdValueDefaults(
  metricType,
  thresholdType,
  thresholdOperator,
) {
  const { critical, warning } = getScaleThresholdDefaults(
    metricType,
    thresholdType,
  );
  return thresholdOperator === "less_than"
    ? { critical: warning, warning: critical }
    : { critical, warning };
}

// The direction a new alert starts with: Below for a static error-free
// session rate, Above for every other metric and alert type.
const getDefaultThresholdOperator = (metricType, thresholdType) =>
  isFallingShareScale(metricType, thresholdType)
    ? "less_than"
    : ALERT_CONFIG_DEFAULTS.threshold_operator;

// The direction after a switch of metric or alert type. A direction that is
// still the one the metric and type being left start with moves to the one
// the new metric and type start with, as the two values move to the new
// defaults; any other direction is kept. Every combination but a static
// error-free session rate starts Above, so only a switch onto or off that one
// can move it.
export function getThresholdOperatorAfterSwitch(thresholdOperator, from, to) {
  const leftStart = getDefaultThresholdOperator(
    from.metricType,
    from.thresholdType,
  );
  return thresholdOperator === leftStart
    ? getDefaultThresholdOperator(to.metricType, to.thresholdType)
    : thresholdOperator;
}

// keepSavedWords: an alert being edited keeps a level saved as infinity or
// not-a-number. A duplicate is saved as a new alert, which needs a number for
// each level, so it starts from the numbers a new alert gets.
export function getDefaultAlertConfigValues(
  existingConfig = {},
  { keepSavedWords = true } = {},
) {
  const pagerdutyRoutingKey = readAlertField(
    existingConfig,
    "pagerdutyRoutingKey",
    "pagerduty_routing_key",
  );
  const webhookUrl = readAlertField(
    existingConfig,
    "webhookUrl",
    "webhook_url",
  );
  const slackWebhookUrl = readAlertField(
    existingConfig,
    "slackWebhookUrl",
    "slack_webhook_url",
  );
  const notificationEmails = readAlertField(
    existingConfig,
    "notificationEmails",
    "notification_emails",
  );
  const metricType =
    readAlertField(existingConfig, "metricType", "metric_type") || "";
  const thresholdType =
    readAlertField(existingConfig, "thresholdType", "threshold_type") ||
    ALERT_CONFIG_DEFAULTS.threshold_type;
  const thresholdOperator =
    readAlertField(existingConfig, "thresholdOperator", "threshold_operator") ||
    getDefaultThresholdOperator(metricType, thresholdType);
  // metricType often arrives pre-selected here (e.g. from the "Select Alert
  // Type" step, before AlertSettingsForm even mounts), so the threshold
  // fields must start on the right scale from the first render — the
  // onChange-time re-derivation in AlertSettingsForm never fires for a
  // value that was never "changed" from the user's perspective.
  const scaleDefaults = getThresholdValueDefaults(
    metricType,
    thresholdType,
    thresholdOperator,
  );
  const savedWarning = readAlertField(
    existingConfig,
    "warningThresholdValue",
    "warning_threshold_value",
  );
  const savedWithoutWarning =
    existingConfig?.id != null &&
    (savedWarning === null || savedWarning === "");
  // A level saved as infinity or not-a-number opens empty with its words, and
  // keeps what was saved until a number is typed there.
  const criticalWords = keepSavedWords
    ? savedLevelWords(existingConfig, "critical")
    : "";
  const warningWords = keepSavedWords
    ? savedLevelWords(existingConfig, "warning")
    : "";

  return {
    name: existingConfig?.name || "",
    metric_type: metricType,
    metric: existingConfig?.metric || "",
    alert_frequency: numberOr(
      readAlertField(existingConfig, "alertFrequency", "alert_frequency"),
      ALERT_CONFIG_DEFAULTS.alert_frequency,
    ),
    filters: transformFilterResponse(existingConfig?.filters),
    threshold_type: thresholdType,
    auto_threshold_time_window: numberOr(
      readAlertField(
        existingConfig,
        "autoThresholdTimeWindow",
        "auto_threshold_time_window",
      ),
      ALERT_CONFIG_DEFAULTS.auto_threshold_time_window,
    ),
    threshold_operator: thresholdOperator,
    threshold_metric_value:
      readAlertField(
        existingConfig,
        "thresholdMetricValue",
        "threshold_metric_value",
      ) ?? ALERT_CONFIG_DEFAULTS.threshold_metric_value,
    critical_threshold_value: criticalWords
      ? ""
      : numberOr(
          readAlertField(
            existingConfig,
            "criticalThresholdValue",
            "critical_threshold_value",
          ),
          scaleDefaults.critical,
        ),
    // A saved alert with no warning opens with the warning empty, so saving
    // it never adds a warning nobody set; a new alert gets the default.
    warning_threshold_value: savedWithoutWarning
      ? ""
      : numberOr(savedWarning, scaleDefaults.warning),
    warning_optional: savedWithoutWarning,
    ...(criticalWords && { critical_threshold_label: criticalWords }),
    ...(warningWords && { warning_threshold_label: warningWords }),
    notification: {
      method: pagerdutyRoutingKey
        ? "pagerduty"
        : webhookUrl
          ? "webhook"
          : slackWebhookUrl
            ? "slack"
            : "email",
      emails: notificationEmails || [],
      slack: {
        webhookUrl: slackWebhookUrl || "",
        notes:
          readAlertField(existingConfig, "slackNotes", "slack_notes") || "",
      },
      webhook: {
        url: webhookUrl || "",
      },
      pagerduty: {
        routingKey: pagerdutyRoutingKey || "",
      },
    },
  };
}
