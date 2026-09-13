import { z } from "zod";
import { v4 as uuidv4 } from "uuid";

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
    notification: z
      .object({
        method: z.enum(["email", "slack"], {
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
      })
      .superRefine((notif, ctx) => {
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
      }),
  })
  .superRefine((data, ctx) => {
    const {
      warning_threshold_value,
      critical_threshold_value,
      threshold_operator,
      threshold_type,
    } = data;

    // Threshold values are NOT required for anomaly_detection
    const needsThresholds = threshold_type !== "anomaly_detection";

    if (needsThresholds) {
      // Check presence
      if (critical_threshold_value === undefined) {
        ctx.addIssue({
          path: ["critical_threshold_value"],
          code: "custom",
          message: "Critical value is required",
        });
      }

      if (warning_threshold_value === undefined) {
        ctx.addIssue({
          path: ["warning_threshold_value"],
          code: "custom",
          message: "Warning value is required",
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

export function getDefaultAlertConfigValues(existingConfig = {}) {
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

  return {
    name: existingConfig?.name || "",
    metric_type:
      readAlertField(existingConfig, "metricType", "metric_type") || "",
    metric: existingConfig?.metric || "",
    alert_frequency: numberOr(
      readAlertField(existingConfig, "alertFrequency", "alert_frequency"),
      ALERT_CONFIG_DEFAULTS.alert_frequency,
    ),
    filters: transformFilterResponse(existingConfig?.filters),
    threshold_type:
      readAlertField(existingConfig, "thresholdType", "threshold_type") ||
      ALERT_CONFIG_DEFAULTS.threshold_type,
    auto_threshold_time_window: numberOr(
      readAlertField(
        existingConfig,
        "autoThresholdTimeWindow",
        "auto_threshold_time_window",
      ),
      ALERT_CONFIG_DEFAULTS.auto_threshold_time_window,
    ),
    threshold_operator:
      readAlertField(
        existingConfig,
        "thresholdOperator",
        "threshold_operator",
      ) || ALERT_CONFIG_DEFAULTS.threshold_operator,
    threshold_metric_value:
      readAlertField(
        existingConfig,
        "thresholdMetricValue",
        "threshold_metric_value",
      ) ?? ALERT_CONFIG_DEFAULTS.threshold_metric_value,
    critical_threshold_value: numberOr(
      readAlertField(
        existingConfig,
        "criticalThresholdValue",
        "critical_threshold_value",
      ),
      ALERT_CONFIG_DEFAULTS.critical_threshold_value,
    ),
    warning_threshold_value: numberOr(
      readAlertField(
        existingConfig,
        "warningThresholdValue",
        "warning_threshold_value",
      ),
      ALERT_CONFIG_DEFAULTS.warning_threshold_value,
    ),
    notification: {
      method: slackWebhookUrl ? "slack" : "email",
      emails: notificationEmails || [],
      slack: {
        webhookUrl: slackWebhookUrl || "",
        notes:
          readAlertField(existingConfig, "slackNotes", "slack_notes") || "",
      },
    },
  };
}
