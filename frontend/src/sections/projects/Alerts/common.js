import { formatExactMoney } from "src/sections/common/format/money";
import { endOfToday, sub } from "date-fns";
import _ from "lodash";
import { formatDate } from "src/utils/report-utils";
import { alpha } from "@mui/material/styles";
import { statusColors } from "src/utils/runStatus";
import { seriesColor } from "src/theme/data-colors";
import { getYAxisUnit } from "../LLMTracing/GraphSection/common";
import { apiFilterHasValue } from "src/sections/annotations/queues/utils/filter-operators";

// Helper function to format y-axis values with proper precision
const formatYAxisValue = (value, money = false) => {
  if (money) return formatExactMoney(value, { prefix: "" });
  if (value == null) return "0.00";

  const absValue = Math.abs(value);

  // Handle very large numbers with abbreviations
  if (absValue >= 1000000) {
    return `${(value / 1000000).toFixed(2)}M`;
  }
  if (absValue >= 1000) {
    return value.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  // Handle very small non-zero numbers with more precision
  if (absValue < 0.01 && value !== 0) {
    return value.toFixed(4);
  }

  // Default: 2 decimal places
  return value.toFixed(2);
};

export const normalizeAlertFilters = (filters = {}) => {
  const observationType =
    filters?.observationType ?? filters?.observation_type ?? [];
  const spanAttributesFilters =
    filters?.spanAttributesFilters ?? filters?.span_attributes_filters ?? [];

  return {
    ...filters,
    observationType,
    observation_type: filters?.observation_type ?? observationType,
    spanAttributesFilters,
    span_attributes_filters:
      filters?.span_attributes_filters ?? spanAttributesFilters,
  };
};

export const normalizeAlertListRow = (row = {}) => ({
  ...row,
  metricType: row?.metricType ?? row?.metric_type,
  lastTriggered: row?.lastTriggered ?? row?.last_triggered,
  noOfAlerts: row?.noOfAlerts ?? row?.no_of_alerts ?? 0,
  isMute: row?.isMute ?? row?.is_mute ?? false,
  thresholdType: row?.thresholdType ?? row?.threshold_type,
  thresholdRule: row?.thresholdRule ?? row?.threshold_rule,
  filters: normalizeAlertFilters(row?.filters),
});

export const normalizeAlertDetail = (detail = {}) => ({
  ...detail,
  createdBy: detail?.createdBy ?? detail?.created_by?.name,
  createdAt: detail?.createdAt ?? detail?.created_at,
  // When the newest issue was raised; none for an alert that has never
  // fired. The time the alert was last checked is a separate fact.
  lastTriggered: detail?.lastTriggered ?? detail?.last_triggered_at,
  lastChecked: detail?.lastChecked ?? detail?.last_checked_at,
  metricType: detail?.metricType ?? detail?.metric_type,
  metricName: detail?.metricName ?? detail?.metric_name,
  thresholdType: detail?.thresholdType ?? detail?.threshold_type,
  thresholdOperator: detail?.thresholdOperator ?? detail?.threshold_operator,
  thresholdRule: detail?.thresholdRule ?? detail?.threshold_rule,
  thresholdMetricValue:
    detail?.thresholdMetricValue ?? detail?.threshold_metric_value,
  criticalThresholdValue:
    detail?.criticalThresholdValue ?? detail?.critical_threshold_value,
  warningThresholdValue:
    detail?.warningThresholdValue ?? detail?.warning_threshold_value,
  // A threshold saved as infinity or not-a-number comes as a null value with
  // its words beside it.
  criticalThresholdLabel:
    detail?.criticalThresholdLabel ?? detail?.critical_threshold_label,
  warningThresholdLabel:
    detail?.warningThresholdLabel ?? detail?.warning_threshold_label,
  alertFrequency: detail?.alertFrequency ?? detail?.alert_frequency,
  // The span across every fire, aggregated server-side before the issue grid's
  // type filter and pagination — so it stays put whichever slice is loaded.
  windowStart: detail?.windowStart ?? detail?.window_start,
  windowEnd: detail?.windowEnd ?? detail?.window_end,
  autoThresholdTimeWindow:
    detail?.autoThresholdTimeWindow ?? detail?.auto_threshold_time_window,
  notificationEmails: detail?.notificationEmails ?? detail?.notification_emails,
  slackWebhookUrl: detail?.slackWebhookUrl ?? detail?.slack_webhook_url,
  slackNotes: detail?.slackNotes ?? detail?.slack_notes,
  isMute: detail?.isMute ?? detail?.is_mute ?? false,
  filters: normalizeAlertFilters(detail?.filters),
});

export const isAlertMuted = (row) => Boolean(row?.isMute ?? row?.is_mute);

export const getAlertFilterValue = (data) => {
  const filters = [];
  const normalizedFilters = normalizeAlertFilters(data?.filters);
  const observationTypes = normalizedFilters.observationType ?? [];
  if (observationTypes?.length > 0) {
    filters.push(`Span Type is ${_.toUpper(observationTypes.join(", "))}`);
  }
  const spanAttributes = normalizedFilters.spanAttributesFilters ?? [];
  if (spanAttributes.length > 0) {
    const customAttributeString = `Custom attribute is ${spanAttributes
      .map((f) => `(${f.columnId ?? f.column_id})`)
      .join(",")}`;
    filters.push(customAttributeString);
  }
  return filters;
};

export const alertTypes = [
  {
    category: "Application performance alerts",
    options: [
      { label: "Count of errors", value: "count_of_errors" },
      { label: "Span response time", value: "span_response_time" },
      { label: "LLM response time", value: "llm_response_time" },
      { label: "LLM API failure rates", value: "llm_api_failure_rates" },
      {
        label: "Error rates for function calling",
        value: "error_rates_for_function_calling",
      },
      { label: "Error free session rate", value: "error_free_session_rates" },
      // { label: "User experience error", value: "user_experience_error" }, // removed (not in metric_type)
      {
        label: "Service provider error rates",
        value: "service_provider_error_rates",
      },
    ],
  },
  {
    category: "Metric alerts",
    options: [
      { label: "Evaluation Metrics", value: "evaluation_metrics" },
      { label: "Token usage", value: "token_usage" },
      { label: "Daily tokens spent", value: "daily_tokens_spent" },
      { label: "Monthly tokens spent", value: "monthly_tokens_spent" },
    ],
  },
  // {
  // category: "Infrastructure Alerts",
  // options: [
  // { label: "Credit Exhaustion", value: "credit_exhaustion" }, // removed
  // { label: "Rate limit alert", value: "rate_limit_alert" }, // removed
  // ],
  // },
];

export const intervalOptions = [
  { label: "5 minute interval", value: 5 },
  { label: "10 minute interval", value: 10 },
  { label: "15 minute interval", value: 15 },
  { label: "1 hour interval", value: 60 },
  { label: "2 hour interval", value: 120 },
  { label: "4 hour interval", value: 240 },
];

export const thresholdOptions = [
  {
    label: "Above",
    value: "greater_than",
  },
  {
    label: "Below",
    value: "less_than",
  },
];

export const notificationOptions = [
  {
    label: "Email",
    value: "email",
  },
  {
    label: "Slack",
    value: "slack",
  },
  {
    label: "Webhook",
    value: "webhook",
  },
  {
    label: "PagerDuty",
    value: "pagerduty",
  },
];

export const timeOptions = [
  { label: "Same time 5 minutes ago", value: 5 },
  { label: "Same time 15 minutes ago", value: 15 },
  { label: "Same time one hour ago", value: 60 },
  { label: "Same time one day ago", value: 24 * 60 },
  { label: "Same time one week ago", value: 7 * 24 * 60 },
  { label: "Same time one month ago", value: 30 * 24 * 60 },
];

export const levelOfResponsiveness = [
  {
    label: "Low (alerts less often)",
    value: "low",
  },
  {
    label: "Medium",
    value: "medium",
  },
  {
    label: "High (alerts more often)",
    value: "high",
  },
];

export const directionOfAnomaly = [
  {
    label: "Above bounds only ",
    value: "greater_than",
  },
  {
    label: "Below bounds only",
    value: "less_than",
  },
];

// Each choice says what the alert does with the numbers typed below it, one
// for critical and one for warning, so it names no single value.
export const alertDefinitionOptions = [
  {
    label:
      "Static value: alert when the metric goes above or below a number you set",
    value: "static",
  },
  {
    label:
      "Percentage change: alert when the metric rises or falls by more than a percentage you set, compared to the previous period",
    value: "percentage_change",
  },
  // {
  //   label: "Anomaly Detection: when values fall outside the expected range",
  //   value: "anomaly_detection",
  // },
];

export const getDefaultDateRange = () => {
  const getDateArray = () => {
    return [
      formatDate(
        sub(new Date(), {
          days: 7,
        }),
      ),
      formatDate(endOfToday()),
    ];
  };

  return {
    dateFilter: getDateArray(),
    dateOption: "7D",
  };
};

// dummy
export const alertDetails = {
  count_of_errors: {
    title: "Count of errors",
    markdown: `
Count of errors measures the total number of error events recorded during a defined period.

Common causes include:
- HTTP errors (4xx client errors, 5xx server errors).
- Model-level failures like unhandled exceptions or timeouts.
- Pipeline errors during pre/post-processing.
- Dependency errors (e.g., database or API failures).

> Example: "Trigger an alert if errors exceed 50 in 5 minutes."
    `,
  },
  span_response_time: {
    title: "Span response time",
    markdown: `
Span response time measures the latency of spans (operations) in your system.

You can monitor:
- Average latency per span.
- Bottlenecks in async tasks.
- Slow API calls or DB queries.

> Example: "Trigger an alert if span response time exceeds 1 second."
    `,
  },
  llm_response_time: {
    title: "LLM response time",
    markdown: `
LLM response time tracks how quickly your LLM responds to requests.

Why monitor?
- Detect sudden spikes in response time.
- Identify high-latency prompts.

> Example: "Alert if response time exceeds 1s for 3 consecutive calls."
    `,
  },
  llm_api_failure_rates: {
    title: "LLM API failure rates",
    markdown: `
LLM API failure rate tracks the percentage of failed calls to the LLM provider.

Failure types:
- Timeout errors.
- 5xx server errors.
- Quota or rate-limit errors.

> Example: "Alert if failure rate exceeds 10% in 10 minutes."
    `,
  },
  error_rates_for_function_calling: {
    title: "Error rates for function calling",
    markdown: `
Error rate for function calling checks how often your LLM tool or function calls fail.

Failure reasons:
- Misconfigured tool schemas.
- Timeout or unexpected return formats.

> Example: "Alert if function call errors > 5% in last 1 hour."
    `,
  },
  error_free_session_rates: {
    title: "Error free session rate",
    markdown: `
Error-free session rate measures how many sessions complete successfully without any errors.

Why important?
- A low rate means frequent failures.
- Great for tracking user experience.

> Example: "Alert if error-free sessions fall below 90%."
    `,
  },
  user_experience_error: {
    title: "User experience error",
    markdown: `
User experience error tracks errors directly affecting end-users.

Examples:
- UI failure messages.
- Session interruptions.

> Example: "Alert if user-visible errors exceed 5%."
    `,
  },
  service_provider_error_rates: {
    title: "Service provider error rates",
    markdown: `
Service provider error rate is the share of your providers that had at least one error in the period, from 0 to 1.

Errors include:
- API outages.
- Rate limit responses.

> Example: "Alert if more than 20% of providers had an error (above 0.2)."
    `,
  },

  // Metric alerts
  evaluation_metrics: {
    title: "Evaluation Metrics",
    markdown: `
Evaluation metrics track the performance of your model or service using custom metrics.

Examples:
- Accuracy, BLEU, or ROUGE scores.
- Quality scores from evaluators.

> Example: "Alert if evaluation score < 0.8."
    `,
  },
  token_usage: {
    title: "Token usage",
    markdown: `
Token usage monitors the number of tokens processed by your LLM.

Why monitor?
- Prevent unexpected cost spikes.
- Detect inefficient prompts.

> Example: "Alert if daily token usage exceeds 50,000 tokens."
    `,
  },
  daily_tokens_spent: {
    title: "Daily tokens spent",
    markdown: `
Daily tokens spent monitors daily consumption of tokens.

> Example: "Alert if daily tokens > 1 million tokens."
    `,
  },
  monthly_tokens_spent: {
    title: "Monthly tokens spent",
    markdown: `
Monthly tokens spent monitors token usage over the month.

> Example: "Alert if monthly tokens exceed 10 million tokens."
    `,
  },

  // Infrastructure Alerts
  credit_exhaustion: {
    title: "Credit Exhaustion",
    markdown: `
Credit exhaustion alerts when your credits are about to run out.

> Example: "Alert when remaining credits < 500."
    `,
  },
  rate_limit_alert: {
    title: "Rate limit alert",
    markdown: `
Rate limit alerts notify when requests hit rate limits.

> Example: "Alert when 429 (rate-limit) errors exceed 20 in 5 minutes."
    `,
  },
};

// modal
export const getActionTitle = (type, count, totalCount = null) => {
  const actualCount = totalCount ?? count;
  const alertText = actualCount === 1 ? "Alert" : "Alerts";

  const actionPhrases = {
    mute: totalCount
      ? `Are you sure you want to mute ${actualCount} ${alertText}?`
      : `Are you sure you want to mute the following ${alertText}?`,
    unmute: totalCount
      ? `Are you sure you want to unmute ${actualCount} ${alertText}?`
      : `Are you sure you want to unmute the following ${alertText}?`,
    delete: totalCount
      ? `Are you sure you want to delete ${actualCount} ${alertText}?`
      : `Are you sure you want to delete the following ${alertText}?`,
  };

  return actionPhrases[type] || "";
};

const actionButton = {
  delete: {
    cancel: {
      title: "Cancel",
      color: "default",
    },
    action: {
      baseTitle: "Delete",
      color: "error",
    },
  },
  mute: {
    cancel: {
      title: "Cancel",
      color: "default",
    },
    action: {
      baseTitle: "Mute",
      color: "primary",
    },
  },
  unmute: {
    cancel: {
      title: "Cancel",
      color: "default",
    },
    action: {
      baseTitle: "Unmute",
      color: "primary",
    },
  },
};

export const getActionButtonConfig = (type, count) => {
  const config = actionButton[type];

  if (!config) {
    return {
      cancel: { title: "Cancel", color: "default" },
      action: { title: "Proceed", color: "primary" },
    };
  }

  const alertText = count === 1 ? "Alert" : "Alerts";

  return {
    cancel: {
      ...config.cancel,
    },
    action: {
      title: `${config.action.baseTitle} ${alertText}`,
      color: config.action.color,
    },
  };
};

// Percentage change rules compare with the previous period, as the create
// form says: the metric rises (higher) or falls (lower) by more than a set
// percentage.
const PERCENTAGE_CHANGE_DIRECTION = {
  greater_than: "higher",
  less_than: "lower",
};

// A percentage alert saved before the update keeps its earlier rule until a
// threshold changes: it fires past its average plus or minus stddev x
// (1 + p/100). A server without threshold_rule judges every alert by the
// current rule.
export const usesEarlierRule = (alert) =>
  (alert?.thresholdRule ?? alert?.threshold_rule) === "earlier" &&
  (alert?.thresholdType ?? alert?.threshold_type) === "percentage_change";

export const EARLIER_RULE_ROW_TEXT = "Uses the rule it was saved with";

const EARLIER_RULE_SIDE = {
  greater_than: "above",
  less_than: "below",
};

// The earlier rule's band in standard deviations: 400 reads 5, 50 reads 1.5.
const earlierRuleDeviations = (percent) =>
  Number(((100 + Number(percent)) / 100).toFixed(6));

// History window in minutes, as the create form offers it: 60 reads "hour",
// 10080 reads "7 days".
const formatHistoryWindow = (minutes) => {
  const value = Number(minutes);
  if (!Number.isFinite(value) || value <= 0) return "";
  const [count, unit] =
    value % (24 * 60) === 0
      ? [value / (24 * 60), "day"]
      : value % 60 === 0
        ? [value / 60, "hour"]
        : [value, "minute"];
  return count === 1 ? unit : `${count} ${unit}s`;
};

// One calm line for an alert that keeps its earlier rule: why, and what the
// current rule would be for its critical percentage.
export const earlierRuleNote = (alert) => {
  if (!usesEarlierRule(alert)) return "";
  const reason =
    "Saved before the update, so this alert keeps its earlier rule. Change a threshold and save to switch to the current rule";
  const critical =
    alert?.criticalThresholdValue ?? alert?.critical_threshold_value;
  const criticalLabel =
    alert?.criticalThresholdLabel ?? alert?.critical_threshold_label;
  // A critical level saved as infinity or not-a-number has words, not a
  // percentage to quote.
  if (critical == null && criticalLabel) return `${reason}.`;
  const percent =
    critical ?? alert?.warningThresholdValue ?? alert?.warning_threshold_value;
  const operator = alert?.thresholdOperator ?? alert?.threshold_operator;
  const direction = PERCENTAGE_CHANGE_DIRECTION[operator];
  if (percent == null || !direction) return `${reason}.`;
  return `${reason}: more than ${percent}% ${direction} than the previous period.`;
};

// The name of an alert chart's line. For service provider error rate it says
// which share is drawn, as the conditions do; other metrics keep their name.
export const alertChartSeriesName = ({ metricType, earlier }) => {
  if (metricType === "service_provider_error_rates") {
    return earlier
      ? "Share of providers with no errors"
      : "Share of providers with errors";
  }
  return _.startCase(_.toLower(metricType));
};

// The words beside a service provider error rate threshold in the edit form:
// which share the number is on. Other metrics get none.
export const providerThresholdHelp = ({
  metricType,
  thresholdType,
  earlier,
}) => {
  if (metricType !== "service_provider_error_rates") return undefined;
  // A saved percentage row on the earlier rule scales a band of standard
  // deviations (the note above the fields says so); name only the share.
  if (earlier) {
    return thresholdType === "percentage_change"
      ? "Share of providers with no errors"
      : "Share of providers with no errors, from 0 to 1";
  }
  return thresholdType === "percentage_change"
    ? "Change in the share of providers that had an error"
    : "Share of providers that had an error, from 0 to 1";
};

// What the other share metrics count, beside a static threshold, which is
// typed from 0 to 1 as the provider one is.
const SHARE_THRESHOLD_HELP = {
  llm_api_failure_rates: "Share of LLM calls that failed, from 0 to 1",
  error_rates_for_function_calling:
    "Share of function calls that had an error, from 0 to 1",
  error_free_session_rates: "Share of sessions with no errors, from 0 to 1",
};

// The words beside a threshold in the form: which share the number is on.
// A percentage change on the other share metrics, and every metric that is
// not a share, get none.
export const shareThresholdHelp = ({ metricType, thresholdType, earlier }) =>
  metricType === "service_provider_error_rates"
    ? providerThresholdHelp({ metricType, thresholdType, earlier })
    : thresholdType === "static"
      ? SHARE_THRESHOLD_HELP[metricType]
      : undefined;

export const formatConditionText = (condition) => {
  if (!condition) return "";

  const metric = _.capitalize(condition.metric);
  const operator = condition.operator ?? "";
  const value = condition.value ?? "";

  // A level saved as infinity or not-a-number shows its words where the number
  // would be. No percentage or standard deviations can be worked out from
  // words, so every rule and threshold type reads this plain form.
  if (condition.valueLabel) {
    return `If ${metric} ${operator} ${condition.valueLabel}`;
  }

  const side = EARLIER_RULE_SIDE[condition.thresholdOperator];
  // Service provider error rate now measures the share of providers that had
  // an error; say so beside the number. A row still on the earlier rule reads
  // its own metric name, the share with no errors.
  if (
    condition.metricType === "service_provider_error_rates" &&
    condition.thresholdRule !== "earlier" &&
    side
  ) {
    const providerDirection =
      PERCENTAGE_CHANGE_DIRECTION[condition.thresholdOperator];
    return condition.thresholdType === "percentage_change"
      ? `If the share of providers with errors is more than ${value}% ${providerDirection} compared to previous period`
      : `If the share of providers with errors is ${side} ${value}`;
  }
  if (usesEarlierRule(condition) && side) {
    const deviations = earlierRuleDeviations(value);
    const unit =
      deviations === 1 ? "standard deviation" : "standard deviations";
    const history = formatHistoryWindow(condition.autoThresholdTimeWindow);
    const over = history ? ` over the last ${history}` : "";
    return `If ${metric} is more than ${deviations} ${unit} ${side} its average${over}`;
  }

  const direction = PERCENTAGE_CHANGE_DIRECTION[condition.thresholdOperator];
  if (condition.thresholdType === "percentage_change" && direction) {
    return `If ${metric} is more than ${value}% ${direction} compared to previous period`;
  }

  return `If ${metric} ${operator} ${value}`;
};

// A saved level as a number; an empty one is not a number.
const levelNumber = (value) =>
  typeof value === "string" && value.trim() !== "" ? Number(value) : value;

// A static service provider error rate alert compares a share of providers,
// from 0 to 1, on either rule, and the server compares strictly: no share is
// above 1 or below 0. A level Above 1 or more, or Below 0 or less, can never
// fire.
const providerLevelCannotFire = (operator, value) => {
  const number = levelNumber(value);
  if (!Number.isFinite(number)) return false;
  if (operator === "greater_than") return number >= 1;
  if (operator === "less_than") return number <= 0;
  return false;
};

// LLM API failure rates, error rates for function calling and the error-free
// session rate are shares from 0 to 1 too. A level outside that range in the
// alert's direction, Above more than 1 or Below less than 0, can never fire.
const levelOutsideShare = (operator, value) => {
  const number = levelNumber(value);
  if (!Number.isFinite(number)) return false;
  if (operator === "greater_than") return number > 1;
  if (operator === "less_than") return number < 0;
  return false;
};

// What each of those shares counts, as its note names it, and a share to enter
// instead: the failure shares sit near 0 when healthy, the error-free session
// rate near 1.
const SHARE_CANNOT_FIRE = {
  llm_api_failure_rates: { counts: "LLM calls", example: "0.1 for 10%" },
  error_rates_for_function_calling: {
    counts: "function calls",
    example: "0.1 for 10%",
  },
  error_free_session_rates: { counts: "sessions", example: "0.9 for 90%" },
};

// One line for a static share alert none of whose levels can fire: the
// critical cannot, and the warning is empty or cannot either. A level outside
// 0 to 1 that always fires, or a level that can still fire, gets none. The
// provider's line is unchanged; the other shares' says the levels are outside
// 0 to 1 and how to fix them.
export const shareCannotFireNote = (alert) => {
  const share = SHARE_CANNOT_FIRE[alert?.metricType];
  const isProvider = alert?.metricType === "service_provider_error_rates";
  if ((!share && !isProvider) || alert?.thresholdType !== "static") {
    return "";
  }
  const levelCannotFire = isProvider
    ? providerLevelCannotFire
    : levelOutsideShare;
  const operator = alert?.thresholdOperator;
  const warning = alert?.warningThresholdValue;
  const noWarning = warning === null || warning === undefined || warning === "";
  const cannotFire =
    levelCannotFire(operator, alert?.criticalThresholdValue) &&
    (noWarning || levelCannotFire(operator, warning));
  if (!cannotFire) return "";
  if (isProvider) {
    return "This alert cannot fire: a share of providers is never above 1 or below 0.";
  }
  const levels = noWarning ? "its level is" : "its levels are";
  return `This alert cannot fire: ${levels} outside 0 to 1, and a share of ${share.counts} never is. Edit the alert and enter a share, for example ${share.example}.`;
};

export const issueColumns = [
  {
    id: "message",
    name: "Issue",
    isVisible: true,
  },
  {
    id: "type",
    name: "Trigger type",
    isVisible: true,
  },
  {
    id: "resolved",
    name: "Status",
    isVisible: true,
  },
  {
    id: "created_at",
    name: "Triggered at",
    isVisible: true,
  },
];

// A service provider error rate alert saved before the update and not
// converted (a percentage alert, or a threshold outside 0 to 1) is still judged
// on the share of providers with no errors, so its conditions name that share.
const conditionMetricName = (alertObj) =>
  alertObj?.metricType === "service_provider_error_rates" &&
  alertObj?.thresholdRule === "earlier"
    ? "Share of providers with no errors"
    : alertObj?.metricName ?? "unknown";

// A level's value: the saved number or, for a threshold saved as infinity or
// not-a-number, the words the server sends beside its null.
const conditionValue = (value, label) =>
  value == null && label ? { value, valueLabel: label } : { value };

export function transformAlertToConditions(alertObj) {
  if (!alertObj || typeof alertObj !== "object") return [];

  const operatorMap = {
    greater_than: ">",
    less_than: "<",
    equal: "=",
    not_equal: "≠",
  };

  const metricName = conditionMetricName(alertObj);
  const operator =
    operatorMap[alertObj?.thresholdOperator] ??
    alertObj?.thresholdOperator ??
    "=";

  const conditions = [];
  const rule = {
    metric: metricName,
    operator,
    thresholdOperator: alertObj?.thresholdOperator,
    thresholdType: alertObj?.thresholdType,
    thresholdRule: alertObj?.thresholdRule,
    autoThresholdTimeWindow: alertObj?.autoThresholdTimeWindow,
    metricType: alertObj?.metricType,
  };

  const critical = conditionValue(
    alertObj?.criticalThresholdValue,
    alertObj?.criticalThresholdLabel,
  );
  if (critical.value != null || critical.valueLabel) {
    conditions.push({
      id: 1,
      condition: { ...rule, ...critical },
      description: "A critical issue is created",
      action: "Then send a notification to these emails",
    });
  }

  const warning = conditionValue(
    alertObj?.warningThresholdValue,
    alertObj?.warningThresholdLabel,
  );
  if (warning.value != null || warning.valueLabel) {
    conditions.push({
      id: 2,
      condition: { ...rule, ...warning },
      description: "A warning issue is created",
      action: "Then send a notification to these emails",
    });
  }

  return conditions;
}

// Alert-period status -> role in the one status map; the band colour is
// resolved from theme.palette.status.
const ALERT_STATUS_ROLE = {
  healthy: "success",
  warning: "warning",
  critical: "danger",
};

// Threshold band for the alert chart: tinted fill, solid status edge.
export function alertThresholdBand(theme, role, fillAlpha) {
  const solid = statusColors(theme, role).solid;
  return { fillColor: alpha(solid, fillAlpha), borderColor: solid };
}

// Error rates and the error-free session rate are shares from 0 to 1, and
// their static thresholds are typed and compared on that scale. Their axis
// names that scale, never a percent.
const SHARE_METRIC_TYPES = [
  "llm_api_failure_rates",
  "error_rates_for_function_calling",
  "error_free_session_rates",
  "service_provider_error_rates",
];
const SHARE_AXIS_TITLE = "Share (0-1)";

// The y-axis unit of an alert chart: from the metric type when the caller
// gives it, otherwise from the line's name. An eval alert draws a mean score
// or the share of one label, neither a percent, so its axis reads a plain
// value, as its threshold fields do.
const alertChartUnit = (customOptions) => {
  const key =
    customOptions?.unitKey ?? _.snakeCase(_.toLower(customOptions?.seriesName));
  if (SHARE_METRIC_TYPES.includes(key)) return SHARE_AXIS_TITLE;
  if (key === "evaluation_metrics") return "Value";
  return getYAxisUnit(key);
};

export function getCompareChartConfig(
  apiData,
  customOptions = {},
  { isDark = false, theme } = {},
) {
  if (!apiData?.result?.graph_data || !apiData?.result?.alert_bar_data) {
    throw new Error("Invalid API data structure");
  }

  const { graph_data: graphData, alert_bar_data: alertBarData } =
    apiData.result;

  // Points and band edges sit at the instants the server means.
  const totalTimeSpan =
    toUtcEpochMs(graphData[graphData.length - 1].timestamp) -
    toUtcEpochMs(graphData[0].timestamp);
  const startTime = toUtcEpochMs(graphData[0].timestamp);

  const colorStops = [];
  alertBarData.forEach((period) => {
    const periodStart = toUtcEpochMs(period.start_timestamp);
    const periodEnd = toUtcEpochMs(period.end_timestamp);
    const startPercent = ((periodStart - startTime) / totalTimeSpan) * 100;
    const endPercent = ((periodEnd - startTime) / totalTimeSpan) * 100;

    const color = theme
      ? statusColors(theme, ALERT_STATUS_ROLE[period.status] || "neutral").solid
      : undefined;

    colorStops.push({ offset: startPercent, color, opacity: 1 });
    if (endPercent !== startPercent) {
      colorStops.push({ offset: endPercent, color, opacity: 1 });
    }
  });

  const lineColor = theme ? seriesColor(theme, 0) : undefined;

  const maxY = Math.max(...graphData.map((d) => d.value));
  const alertHeight = maxY * 0.02; // 2% of max

  const series = [
    {
      name: "HIDDEN",
      type: "area",
      data: graphData.map((item) => ({
        x: toUtcEpochMs(item.timestamp),
        y: alertHeight, // only 2% height
      })),
    },
    {
      name: customOptions?.seriesName,
      type: "line",
      data: graphData.map((item) => ({
        x: toUtcEpochMs(item.timestamp),
        y: item.value,
      })),
    },
  ];

  const options = {
    chart: {
      id: "compare-chart",
      height: 350,
      type: "line",
      toolbar: {
        show: false,
      },
    },
    stroke: {
      width: [3, 3],
      curve: "smooth",
    },
    colors: ["transparent", lineColor], // 🔹 match legend + marker to series
    dataLabels: {
      enabled: false,
    },
    plotOptions: {
      bar: {
        columnWidth: "100%",
        barHeight: "100%",
        distributed: false,
        dataLabels: {
          position: "top",
        },
      },
    },
    xaxis: {
      type: "datetime",
      labels: {
        formatter: function (value) {
          return new Date(value).toLocaleDateString("en-US", {
            month: "short",
            day: "2-digit",
          });
        },
      },
    },
    yaxis: {
      max: maxY * 1.1,
      min: 0,
      title: {
        text: "Value",
      },
      labels: {
        formatter: (value) =>
          formatYAxisValue(value, alertChartUnit(customOptions) === "$"),
      },
    },
    fill: {
      type: ["gradient", "solid"],
      gradient: {
        type: "horizontal",
        opacityFrom: 0.3,
        opacityTo: 0.3,
        colorStops: colorStops,
      },
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      enabledOnSeries: [1],
      marker: {
        show: true,
      },
      followCursor: true,
    },
    legend: {
      show: true,
      position: "top",
      horizontalAlign: "left",
      onItemClick: {
        toggleDataSeries: false,
      },
      onItemHover: {
        highlightDataSeries: false,
      },
      markers: {
        fillColors: [lineColor],
      },
      labels: {
        colors: theme?.palette.text.primary,
      },
    },
  };

  return { series, options };
}

export function transformSimpleLineChart(apiData) {
  if (!apiData?.result || !Array.isArray(apiData.result)) {
    throw new Error("Invalid API data structure - expected result array");
  }

  const data = apiData.result;
  const chartData = [];
  let sum = 0;
  let maxValue = -Infinity;
  let minValue = Infinity;

  for (const item of data) {
    if (item?.timestamp != null) {
      const y = item.value ?? 0;
      // The point sits at the instant the server means: a bucket sent
      // without an offset is UTC.
      chartData.push({ x: toUtcEpochMs(item.timestamp) ?? item.timestamp, y });

      sum += y;
      if (y > maxValue) maxValue = y;
      if (y < minValue) minValue = y;
    }
  }

  const avgValue = chartData.length > 0 ? sum / chartData.length : 0;

  return {
    lineData: chartData,
    metadata: {
      totalPoints: chartData.length,
      maxValue,
      minValue,
      avgValue: parseFloat(avgValue.toFixed(2)),
      timeRange: {
        start: chartData[0]?.x,
        end: chartData[chartData.length - 1]?.x,
      },
    },
  };
}

export function getSimpleLineChartConfig(
  apiData,
  customOptions = {},
  { isDark = false, theme } = {},
) {
  const transformed = transformSimpleLineChart(apiData);
  const axisLabelColor = theme?.palette.text.tertiary;
  const lineColor = theme ? seriesColor(theme, 0) : undefined;
  const { lineData, metadata } = transformed;

  // Determine Y-axis range based on data: the top padded by 10% and rounded
  // up, the bottom never negative. A share is drawn on its whole 0 to 1 scale
  // instead: rounding up would run its ticks to 2 above about 0.91, under a
  // title that says 0 to 1, and would put the top at 0 for a share that stays
  // at 0. A threshold band reaching past 1 is cut off at the top of the plot.
  const isShare = alertChartUnit(customOptions) === SHARE_AXIS_TITLE;
  const yAxisMax = isShare ? 1 : Math.ceil(metadata.maxValue * 1.1);
  const yAxisMin = isShare
    ? 0
    : Math.max(0, Math.floor(metadata.minValue * 0.9));

  const series = [
    {
      name: customOptions.seriesName || "",
      data: lineData,
    },
  ];

  const defaultOptions = {
    chart: {
      id: "simple-chart",
      type: "line",
      height: 350,
      toolbar: { show: false },
    },
    stroke: {
      curve: "smooth",
      width: 2,
      colors: [lineColor],
    },
    markers: {
      size: 0,
    },
    grid: {
      borderColor: "divider",
      strokeDashArray: 4,
    },
    xaxis: {
      type: "datetime",
      convertedCatToNumeric: false, // include this explicitly
      labels: {
        style: { fontSize: "12px", colors: axisLabelColor },
      },
    },
    yaxis: {
      min: yAxisMin,
      max: yAxisMax,
      labels: {
        style: { fontSize: "12px", colors: axisLabelColor },
        formatter: (value) =>
          formatYAxisValue(value, alertChartUnit(customOptions) === "$"),
      },
      title: {
        text: alertChartUnit(customOptions) || getYAxisUnit("default"),
      },
    },
    fill: {
      type: "solid",
    },
    legend: {
      show: true,
      showForSingleSeries: true,
      position: "top",
      horizontalAlign: "left",
      markers: {
        fillColors: [lineColor],
      },
      labels: {
        colors: theme?.palette.text.primary,
      },
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      y: {
        formatter: (value) => `${value}`,
      },
    },
  };

  // Add threshold annotations if provided
  if (customOptions?.thresholds) {
    defaultOptions.annotations = {
      yaxis: customOptions.thresholds.map((threshold) => ({
        y: threshold.value || 0,
        y2: threshold.y2 || 0,
        yAxisIndex: 0,
        fillColor: threshold.fillColor,
        borderColor: threshold.borderColor,
        strokeDashArray: threshold.strokeDashArray || 4,
        opacity: threshold.opacity || 0.5,
      })),
    };
  }

  // Merge with custom options

  const options = {
    ...defaultOptions,
    ...customOptions,
    // Ensure nested objects are properly merged
    chart: { ...defaultOptions.chart, ...customOptions.chart },
    stroke: { ...defaultOptions.stroke, ...customOptions.stroke },
    xaxis: { ...defaultOptions.xaxis, ...customOptions.xaxis },
    yaxis: { ...defaultOptions.yaxis, ...customOptions.yaxis },
  };

  if (customOptions?.thresholds && !Array.isArray(options.yaxis)) {
    options.yaxis = [
      {
        ...options.yaxis,
        seriesName: [customOptions.seriesName || series[0]?.name || ""],
      },
    ];
  }

  return { series, options, metadata };
}

const toEpochMs = (value) => {
  if (value == null || value === "") return null;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : null;
};

// A date-time string that ends without a UTC offset (no Z, no +hh:mm). The
// static graph's buckets come this way and mean UTC, but new Date() and the
// chart read them as browser-local time. A date-only string is read as UTC,
// so it does not count.
const UTC_OFFSET_SUFFIX = /(?:Z|[+-]\d{2}:?\d{2})$/i;
const isOffsetLessDateTime = (value) =>
  typeof value === "string" &&
  /\d{2}:\d{2}/.test(value) &&
  !UTC_OFFSET_SUFFIX.test(value.trim());

// The instant the server means by a timestamp it sends or reads: an
// offset-less date-time is UTC.
const toUtcEpochMs = (value) => {
  if (!isOffsetLessDateTime(value)) return toEpochMs(value);
  return toEpochMs(`${value.trim().replace(" ", "T")}Z`);
};

const MINUTE_MS = 60 * 1000;

// End of the alert detail chart's time axis. The graph endpoint returns only
// the buckets that hold data, so an axis fitted to the points stops at the
// newest bucket and leaves out a firing that came after it. The axis ends at
// the latest of: the newest point, the end of the minute of the graph read
// (never past the selected range's end), and the last firing when it falls
// inside the selected range.
//
// Every value is the instant the server means, the clock the chart's points
// are drawn on. A point without an offset is UTC. A preset range sends
// offset-less local text, which the server reads as UTC, and a custom range
// sends instants with a Z. Rounding the read time up to the whole minute
// keeps the axis end, and so the chart options, the same across polls that
// bring no new data.
export function getAlertChartXAxisRange(
  apiData,
  { dateRange, lastTriggered, readAt } = {},
) {
  const result = apiData?.result;
  const points = Array.isArray(result) ? result : result?.graph_data;
  let lastPoint = null;
  for (const point of Array.isArray(points) ? points : []) {
    const ms = toUtcEpochMs(point?.timestamp);
    if (ms !== null && (lastPoint === null || ms > lastPoint)) {
      lastPoint = ms;
    }
  }
  if (lastPoint === null) return {};

  const rangeStart = toUtcEpochMs(dateRange?.[0]);
  const rangeEnd = toUtcEpochMs(dateRange?.[1]);
  let end = lastPoint;

  const read = toEpochMs(readAt);
  const readMinuteEnd =
    read === null ? null : Math.ceil(read / MINUTE_MS) * MINUTE_MS;
  const readEnd =
    readMinuteEnd !== null && rangeEnd !== null
      ? Math.min(readMinuteEnd, rangeEnd)
      : readMinuteEnd;
  if (readEnd !== null) end = Math.max(end, readEnd);

  const fired = toUtcEpochMs(lastTriggered);
  const firedInRange =
    fired !== null &&
    (rangeStart === null || fired >= rangeStart) &&
    (rangeEnd === null || fired <= rangeEnd);
  if (firedInRange) end = Math.max(end, fired);

  return { max: end };
}

export const convertFiltersToPayload = (filters) => {
  const observation_type = [];
  const span_attributes_filters = [];

  if (filters?.length === 0)
    return {
      observation_type,
      span_attributes_filters,
    };

  for (let i = 0; i < filters?.length; i++) {
    const property = filters[i]?.property;
    if (property === "observationType") {
      if (filters[i]?.filterConfig.filterValue) {
        observation_type.push(filters[i]?.filterConfig.filterValue);
      }
    }
    if (property === "attributes") {
      span_attributes_filters.push({
        column_id: filters[i]?.propertyId,
        filter_config: {
          filter_type: filters[i]?.filterConfig?.filterType,
          filter_op: filters[i]?.filterConfig?.filterOp,
          filter_value: filters[i]?.filterConfig?.filterValue,
          col_type: "SPAN_ATTRIBUTE",
        },
      });
    }
  }

  return {
    observation_type,
    span_attributes_filters,
  };
};

export const isSpanAttrFilterValid = (spanFilters = []) => {
  if (spanFilters.length === 0) return true;

  return spanFilters.every((filter) => apiFilterHasValue(filter));
};

// FilterPanel `filterFields` for the issues table. `choices` hold the values
// the API expects; `choiceLabels` map them to what the user sees.
export const ISSUE_FILTER_FIELDS = [
  {
    value: "type",
    label: "Trigger Type",
    type: "enum",
    operators: ["is"],
    single: true,
    choices: ["critical", "warning"],
    choiceLabels: { critical: "Critical", warning: "Warning" },
  },
];

// Output types whose alert metric is the share of one chosen label. A Scoring
// eval may also carry labels, but its metric is the mean score, so it must not
// offer a choice.
export const CHOICE_THRESHOLD_OUTPUT_TYPES = ["Pass/Fail", "choices"];

export const evalUsesChoiceThreshold = (evaluation) =>
  CHOICE_THRESHOLD_OUTPUT_TYPES.includes(evaluation?.output_type) &&
  Array.isArray(evaluation?.choices) &&
  evaluation.choices.length > 0;
