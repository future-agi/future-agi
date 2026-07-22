import { endOfToday, sub } from "date-fns";
import _ from "lodash";
import { formatDate } from "src/utils/report-utils";
import { getYAxisUnit } from "../LLMTracing/GraphSection/common";
import { apiFilterHasValue } from "src/sections/annotations/queues/utils/filter-operators";

// Helper function to format y-axis values with proper precision
const formatYAxisValue = (value) => {
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
  filters: normalizeAlertFilters(row?.filters),
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

export const alertDefinitionOptions = [
  { label: "Static Value: above or below {x}", value: "static" },
  {
    label:
      "Percentage Change: {x%} higher or lower compared to previous period",
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
          months: 6,
        }),
      ),
      formatDate(endOfToday()),
    ];
  };

  return {
    dateFilter: getDateArray(),
    dateOption: "6M",
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
Service provider error rate tracks issues from your external providers.

Includes:
- API outages.
- Rate limit responses.

> Example: "Alert if provider errors exceed 20 per 10 minutes."
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

export const formatConditionText = (condition) => {
  if (!condition) return "";

  const metric = _.capitalize(condition.metric);
  const operator = condition.operator ?? "";
  const value = condition.value ?? "";

  return `If ${metric} ${operator} ${value}`;
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

export function transformAlertToConditions(alertObj) {
  if (!alertObj || typeof alertObj !== "object") return [];

  const operatorMap = {
    greater_than: ">",
    less_than: "<",
    equal: "=",
    not_equal: "≠",
  };

  const metricName = alertObj?.metricName ?? "unknown";
  const operator =
    operatorMap[alertObj?.thresholdOperator] ??
    alertObj?.thresholdOperator ??
    "=";

  const conditions = [];

  if (alertObj?.criticalThresholdValue != null) {
    conditions.push({
      id: 1,
      condition: {
        metric: metricName,
        operator,
        value: alertObj.criticalThresholdValue,
      },
      description: "A critical issue is created",
      action: "Then send a notification to these emails",
    });
  }

  if (alertObj?.warningThresholdValue != null) {
    conditions.push({
      id: 2,
      condition: {
        metric: metricName,
        operator,
        value: alertObj.warningThresholdValue,
      },
      description: "A warning issue is created",
      action: "Then send a notification to these emails",
    });
  }

  return conditions;
}

const STATUS_COLORS = {
  HEALTHY: "#4DCC94",
  WARNING: "#FFCC00",
  CRITICAL: "#E85858",
  INSUFFICIENT_DATA: "#9E9E9E",
};

export function getCompareChartConfig(apiData, customOptions = {}) {
  if (!apiData?.result?.graph_data || !apiData?.result?.alert_bar_data) {
    throw new Error("Invalid API data structure");
  }

  const { graph_data: graphData, alert_bar_data: alertBarData } =
    apiData.result;

  const totalTimeSpan =
    new Date(graphData[graphData.length - 1].timestamp).getTime() -
    new Date(graphData[0].timestamp).getTime();
  const startTime = new Date(graphData[0].timestamp).getTime();

  const colorStops = [];
  alertBarData.forEach((period) => {
    const periodStart = new Date(period.start_timestamp).getTime();
    const periodEnd = new Date(period.end_timestamp).getTime();
    const startPercent = ((periodStart - startTime) / totalTimeSpan) * 100;
    const endPercent = ((periodEnd - startTime) / totalTimeSpan) * 100;

    let color;
    switch (period.status) {
      case "healthy":
        color = STATUS_COLORS.HEALTHY;
        break;
      case "warning":
        color = STATUS_COLORS.WARNING;
        break;
      case "critical":
        color = STATUS_COLORS.CRITICAL;
        break;
      default:
        color = STATUS_COLORS.INSUFFICIENT_DATA; // insufficient_data
    }

    colorStops.push({ offset: startPercent, color, opacity: 1 });
    if (endPercent !== startPercent) {
      colorStops.push({ offset: endPercent, color, opacity: 1 });
    }
  });

  const maxY = Math.max(...graphData.map((d) => d.value));
  const alertHeight = maxY * 0.02; // 2% of max

  const series = [
    {
      name: "HIDDEN",
      type: "area",
      data: graphData.map((item) => ({
        x: new Date(item.timestamp).getTime(),
        y: alertHeight, // only 2% height
      })),
    },
    {
      name: customOptions?.seriesName,
      type: "line",
      data: graphData.map((item) => ({
        x: new Date(item.timestamp).getTime(),
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
    colors: ["transparent", "#0066FF"], // 🔹 match legend + marker to series
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
        formatter: formatYAxisValue,
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
        fillColors: ["#CC91EA"],
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
      chartData.push({ x: item.timestamp, y });

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
  { isDark = false } = {},
) {
  const transformed = transformSimpleLineChart(apiData);
  const { lineData, metadata } = transformed;

  // Determine Y-axis range based on data
  const yAxisMax = Math.ceil(metadata.maxValue * 1.1); // Add 10% padding
  const yAxisMin = Math.max(0, Math.floor(metadata.minValue * 0.9)); // Ensure min is not negative

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
      colors: ["#CC91EA"], // Purple
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
        style: { fontSize: "12px", colors: isDark ? "#a1a1aa" : "#666" },
      },
    },
    yaxis: {
      min: yAxisMin,
      max: yAxisMax,
      labels: {
        style: { fontSize: "12px", colors: isDark ? "#a1a1aa" : "#999" },
        formatter: formatYAxisValue,
      },
      title: {
        text:
          getYAxisUnit(_.snakeCase(_.toLower(customOptions?.seriesName))) ||
          getYAxisUnit("default"),
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
        fillColors: ["#CC91EA"],
      },
    },
    tooltip: {
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
        fillColor: threshold.fillColor || "#00A25108",
        borderColor: threshold.borderColor || "#00A251",
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
