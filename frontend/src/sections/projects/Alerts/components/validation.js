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
      metric_type,
      metric,
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

// Eval metrics compare on a 0-1 fraction, but only under a static threshold —
// percentage_change divides the same field by 100, so it wants a percent
// like a system metric does. Reusing one number across scales is exactly
// the TH-7789 bug (a value typed for one scale silently means something
// else once the field's scale changes), so callers should re-derive these
// on every metric_type/threshold_type change rather than leaving a stale
// value in place.
export function getThresholdValueDefaults(metricType, thresholdType) {
  if (metricType === "evaluation_metrics" && thresholdType === "static") {
    return { critical: 0.4, warning: 0.3 };
  }
  // Every other combination keeps the pre-existing placeholder rather than
  // going blank — the form validates on change and AlertSettingsForm
  // re-triggers these two fields after a mode switch, so an empty value
  // shows a red "required" error before the user has had a chance to type
  // anything. A wrong-but-present placeholder is less jarring than that,
  // even though it's still a guess for these modes.
  return { critical: 400, warning: 300 };
}

export function getDefaultAlertConfigValues(existingConfig = {}) {
  const metricType = existingConfig?.metricType || "";
  const thresholdType = existingConfig?.thresholdType || "static";
  // metricType often arrives pre-selected here (e.g. from the "Select Alert
  // Type" step, before AlertSettingsForm even mounts), so the threshold
  // fields must start on the right scale from the first render — the
  // onChange-time re-derivation in AlertSettingsForm never fires for a
  // value that was never "changed" from the user's perspective.
  const scaleDefaults = getThresholdValueDefaults(metricType, thresholdType);

  return {
    name: existingConfig?.name || "",
    metric_type: metricType,
    metric: existingConfig?.metric || "",
    alert_frequency: existingConfig?.alertFrequency || 5,
    filters: transformFilterResponse(existingConfig?.filters),
    threshold_type: thresholdType,
    auto_threshold_time_window: existingConfig?.autoThresholdTimeWindow || 5,
    threshold_operator: existingConfig?.thresholdOperator || "greater_than",
    threshold_metric_value: existingConfig?.thresholdMetricValue || "",
    critical_threshold_value:
      existingConfig?.criticalThresholdValue ?? scaleDefaults.critical,
    warning_threshold_value:
      existingConfig?.warningThresholdValue ?? scaleDefaults.warning,
    notification: {
      method: existingConfig?.slackWebhookUrl
        ? "slack"
        : existingConfig?.notificationEmails?.length
          ? "email"
          : "email",
      emails: existingConfig?.notificationEmails || [],
      slack: {
        webhookUrl: existingConfig?.slackWebhookUrl || "",
        notes: existingConfig?.slackNotes || "",
      },
    },
  };
}
