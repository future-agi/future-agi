import UserIdCellRenderer from "./UserCellRenderers/UserIdCellRenderer";
import LastActiveCellRenderer from "./UserCellRenderers/LastActiveCellRenderer";
import EvaluateCellRenderer from "./UserCellRenderers/EvaluateCellRenderer";
import { GeneralStatCellRenderer } from "./UserCellRenderers/GenericMetricCellRenderer";
import ActionsCellRenderer from "./UserCellRenderers/ActionsCellRenderer";
import {
  endOfToday,
  sub,
  format,
  parseISO,
  differenceInMinutes,
  differenceInHours,
  differenceInDays,
  differenceInMonths,
  startOfToday,
  startOfTomorrow,
  startOfYesterday,
} from "date-fns";
import logger from "src/utils/logger";
import { canonicalEntries, formatMs } from "../../../utils/utils";

export const initialSessionVisibility = {
  session_id: true,
  first_message: true,
  last_message: true,
  duration: true,
  total_cost: true,
  total_traces_count: true,
  start_time: true,
  end_time: true,
  user_id: true,
};

export const tabsData = [
  { label: "Traces", value: "traces" },
  { label: "Sessions", value: "sessions" },
];

export const DEFAULT_VISIBLE_COLUMNS = [
  "user_id",
  "activated_at",
  "last_active",
  "num_traces",
  "num_sessions",
  "total_tokens",
  "total_cost",
  "actions", // optional, commonly always visible
];

export const DateRangeButtonOptions = [
  { title: "Custom" },
  { title: "30 mins" },
  { title: "6 hrs" },
  { title: "Today" },
  { title: "Yesterday" },
  { title: "7D" },
  { title: "30D" },
  { title: "3M" },
];

export const toDisplayName = (metricKey) => {
  return metricKey
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/^./, (str) => str.toUpperCase());
};

export const getUnitForMetric = (metricKey) => {
  switch (metricKey) {
    case "cost":
      return "$";
    case "input_tokens":
    case "output_tokens":
      return "tokens";
    default:
      return "";
  }
};
export const DEFAULT_DATE_FILTER = {
  dateFilter: [
    sub(new Date(), { days: 30 }).toISOString(), // keeps time
    new Date().toISOString(), // now with time
  ],
  dateOption: "30D",
};

export const DEFAULT_ZOOM_RANGE = [null, null];

export const transformDateFilterToBackendFilters = (dateFilter) => {
  if (!dateFilter?.dateFilter || dateFilter.dateFilter.length !== 2) return [];

  return [
    {
      column_id: "created_at",
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: dateFilter.dateFilter.map((d) =>
          new Date(d).toISOString(),
        ),
      },
    },
  ];
};

export const transformGraphDataToChartData = (response) => {
  if (!response?.result) return [];

  const charts = [];

  // canonicalEntries filters out the camelCase aliases the axios
  // response interceptor attaches alongside snake_case metric keys.
  // Without it each metric renders twice (once for `tokens_used` and
  // once for `tokensUsed`, both pointing at the same series).
  for (const [metricKey, metricData] of canonicalEntries(response.result)) {
    if (!Array.isArray(metricData) || metricData.length === 0) continue;

    const displayName = toDisplayName(metricKey);

    // Compute the total sum for this graph
    const total = metricData.reduce(
      (sum, item) => sum + (item[metricKey] || 0),
      0,
    );

    charts.push({
      id: metricKey,
      label: displayName,
      unit: getUnitForMetric(metricKey),
      total,
      series: [
        {
          name: displayName,
          data: metricData.map((item) => [
            new Date(item.timestamp).getTime(),
            item[metricKey],
          ]),
        },
      ],
    });
  }

  return charts;
};

export const userDefaultFilter = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

export const getUsersColumnConfig = () => {
  const columns = [
    {
      headerName: "User ID",
      field: "user_id",
      minWidth: 300,
      flex: 1,
      cellRenderer: UserIdCellRenderer,
    },
    {
      headerName: "User ID Type",
      field: "user_id_type",
      minWidth: 200,
      flex: 1,
    },
    {
      headerName: "User ID Hash",
      field: "user_id_hash",
      minWidth: 200,
      flex: 1,
    },
    {
      headerName: "First Active",
      field: "activated_at",
      minWidth: 180,
      flex: 1,
      cellRenderer: LastActiveCellRenderer,
    },
    {
      headerName: "Last Active",
      field: "last_active",
      minWidth: 180,
      flex: 1,
      cellRenderer: LastActiveCellRenderer,
    },
    {
      headerName: "No. of Traces",
      field: "num_traces",
      minWidth: 200,
      flex: 1,
      valueFormatter: (params) => formatNumberWithCommas(params.value),
    },
    {
      headerName: "No. of Sessions",
      field: "num_sessions",
      minWidth: 200,
      flex: 1,
      valueFormatter: (params) => formatNumberWithCommas(params.value),
    },
    {
      headerName: "Avg Session Duration (s)",
      field: "avg_session_duration",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "duration" }, // You can define this as needed
    },
    {
      headerName: "Total Tokens",
      field: "total_tokens",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "tokens" },
    },
    {
      headerName: "Total Cost ($)",
      field: "total_cost",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "cost" },
      valueFormatter: (params) => params.value?.toFixed(2),
    },
    {
      headerName: "Avg Latency / Trace (ms)",
      field: "avg_trace_latency",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "latency" },
    },
    {
      headerName: "No. of LLM Calls",
      field: "num_llm_calls",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "count" },
      valueFormatter: (params) => formatNumberWithCommas(params.value),
    },
    {
      headerName: "Guardrails Triggered",
      field: "num_guardrails_triggered",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "count" },
    },
    {
      headerName: "Evals Pass Rate (%)",
      field: "eval_score",
      minWidth: 200,
      flex: 1,
      cellRenderer: EvaluateCellRenderer,
      cellRendererParams: {
        dataType: "float",
      },
      isFutureAgiEval: false,
      originType: "someOriginType",
      choicesMap: {},
    },
    {
      headerName: "Input Tokens",
      field: "input_tokens",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "tokens" },
    },
    {
      headerName: "Output Tokens",
      field: "output_tokens",
      minWidth: 200,
      flex: 1,
      cellRenderer: GeneralStatCellRenderer,
      cellRendererParams: { dataType: "tokens" },
    },
    {
      headerName: "Actions",
      field: "actions",
      width: 150,
      flex: 1,
      pinned: "right",
      cellRenderer: ActionsCellRenderer,
    },
  ];
  return columns.map((col) => ({
    ...col,
    hide: !DEFAULT_VISIBLE_COLUMNS.includes(col.field),
  }));
};

export const UsersFilterDefinition = () => {
  const filters = [
    {
      propertyName: "User ID",
      propertyId: "user_id",
      filterType: { type: "text" },
      defaultFilter: "contains",
    },
    {
      propertyName: "Last Active",
      propertyId: "last_active",
      filterType: { type: "date" },
    },
    {
      propertyName: "No. of Traces",
      propertyId: "num_traces",
      filterType: { type: "number" },
    },
    {
      propertyName: "No. of Sessions",
      propertyId: "num_sessions",
      filterType: { type: "number" },
    },
    {
      propertyName: "Avg Session Duration (s)",
      propertyId: "avg_session_duration",
      filterType: { type: "number" },
    },
    {
      propertyName: "Total Tokens",
      propertyId: "total_tokens",
      filterType: { type: "number" },
    },
    {
      propertyName: "Total Cost ($)",
      propertyId: "total_cost",
      filterType: { type: "number" },
    },
    {
      propertyName: "Avg Latency / Trace (ms)",
      propertyId: "avg_trace_latency",
      filterType: { type: "number" },
    },
    {
      propertyName: "No. of LLM Calls",
      propertyId: "num_llm_calls",
      filterType: { type: "number" },
    },
    {
      propertyName: "Guardrails Triggered",
      propertyId: "num_guardrails_triggered",
      filterType: { type: "number" },
    },
    {
      propertyName: "Evals Pass Rate (%)",
      propertyId: "eval_score",
      filterType: { type: "number" },
    },
    {
      propertyName: "Input Tokens",
      propertyId: "input_tokens",
      filterType: { type: "number" },
    },
    {
      propertyName: "Output Tokens",
      propertyId: "output_tokens",
      filterType: { type: "number" },
    },
  ];

  return filters;
};

export const SessionFilterDefinition = [
  {
    propertyName: "Session ID",
    propertyId: "session_id",
    filterType: {
      type: "text",
    },
  },
  {
    propertyName: "First Message",
    propertyId: "first_message",
    filterType: {
      type: "text",
    },
  },
  {
    propertyName: "Last Message",
    propertyId: "last_message",
    filterType: {
      type: "text",
    },
  },
  {
    propertyName: "Duration",
    propertyId: "duration",
    filterType: {
      type: "number",
    },
  },
  {
    propertyName: "Total Traces",
    propertyId: "total_traces_count",
    filterType: {
      type: "number",
    },
  },
  {
    propertyName: "Total Cost",
    propertyId: "total_cost",
    filterType: {
      type: "number",
    },
  },
  {
    propertyName: "Start Time",
    propertyId: "start_time",
    filterType: {
      type: "date",
    },
  },
  {
    propertyName: "End Time",
    propertyId: "end_time",
    filterType: {
      type: "date",
    },
  },
  {
    propertyName: "userId",
    propertyId: "user_id",
    filterType: {
      type: "text",
    },
  },
];

export const getSummaryCards = (data) => [
  {
    title: "Active Days",
    value: data?.active_days?.toLocaleString() ?? 0,
    icon: "user_active_days",
    color: "blue.500",
    bgColor: "blue.o5",
  },
  {
    title: "Total Tokens used",
    value: data?.total_tokens?.toLocaleString() ?? 0,
    icon: "user_total_tokens_used",
    color: "primary.main",
    bgColor: "action.hover",
  },
  // {
  //   title: "Unsuccessful sessions",
  //   value: data?.numUnsuccessfulSessions?.toLocaleString() ?? 0,
  //   icon: "user_unsuccessful_session",
  //   color: "red.500",
  //   bgColor: "red.o5",
  // },
  {
    title: "Avg Latency per trace",
    value: data?.avg_trace_latency ? formatMs(data.avg_trace_latency) : 0,
    icon: "user_avg_latency_per_trace",
    color: "orange.500",
    bgColor: "orange.o5",
  },
  {
    title: "Avg Latency per session",
    // session duration is in seconds
    value: data?.avg_session_duration
      ? formatMs(data.avg_session_duration * 1000)
      : 0,
    icon: "user_avg_latency_per_session",
    color: "blue.500",
    bgColor: "blue.o5",
  },
  {
    title: "Traces with error",
    value: data?.num_traces_with_errors?.toLocaleString() ?? 0,
    icon: "user_trace_with_error",
    color: "red.500",
    bgColor: "red.o5",
  },
  {
    title: "Traces with guardrails triggered",
    value: data?.num_guardrails_triggered?.toLocaleString() ?? 0,
    icon: "user_traces_with_guardrails_triggered",
    color: "red.500",
    bgColor: "red.o5",
  },
];
export const detectTimePeriod = (start, end) => {
  if (!start || !end) {
    return null;
  }

  try {
    // Parse the dates if they are strings (format: "2025-05-15 15:12:25")
    const startDate = typeof start === "string" ? parseISO(start) : start;
    const endDate = typeof end === "string" ? parseISO(end) : end;

    // Calculate the difference
    const minutesDiff = differenceInMinutes(endDate, startDate);
    const hoursDiff = differenceInHours(endDate, startDate);
    const daysDiff = differenceInDays(endDate, startDate);
    const monthsDiff = differenceInMonths(endDate, startDate);

    // Check if it matches any predefined option
    if (minutesDiff >= 25 && minutesDiff <= 35) {
      return "30 mins";
    } else if (hoursDiff >= 5.5 && hoursDiff <= 6.5) {
      return "6 hrs";
    } else if (
      format(startDate, "yyyy-MM-dd") ===
        format(startOfToday(), "yyyy-MM-dd") &&
      (format(endDate, "yyyy-MM-dd") ===
        format(startOfTomorrow(), "yyyy-MM-dd") ||
        format(endDate, "yyyy-MM-dd") === format(endOfToday(), "yyyy-MM-dd"))
    ) {
      return "Today";
    } else if (
      format(startDate, "yyyy-MM-dd") ===
        format(startOfYesterday(), "yyyy-MM-dd") &&
      format(endDate, "yyyy-MM-dd") === format(startOfToday(), "yyyy-MM-dd")
    ) {
      return "Yesterday";
    } else if (daysDiff >= 6 && daysDiff <= 8) {
      return "7D";
    } else if (daysDiff >= 29 && daysDiff <= 31) {
      return "30D";
    } else if (monthsDiff >= 2.8 && monthsDiff <= 3.2) {
      return "3M";
    } else if (monthsDiff >= 5.8 && monthsDiff <= 6.2) {
      return "6M";
    } else if (monthsDiff >= 11.8 && monthsDiff <= 12.2) {
      return "12M";
    }

    return "Custom";
  } catch (error) {
    logger.error("Error detecting time period:", error);
    return "Custom";
  }
};

export const userTraceRowHeightMapping = {
  Short: {
    height: 40,
    autoHeight: false,
  },
  Medium: {
    height: 80,
    autoHeight: false,
  },
  Large: {
    height: 100,
    autoHeight: false,
  },
  "Extra Large": {
    height: 150,
    autoHeight: false,
  },
  // "Full Cell": {
  //     height: undefined,
  //     autoHeight: true,
  // }
};

export const timeFilters = [
  { label: "Last 24 hours", value: 1 },
  { label: "Last 7 days", value: 7 },
  { label: "Last 14 days", value: 14 },
  { label: "Last 30 days", value: 30 },
  { label: "Last 90 days", value: 90 },
];

export const formatNumberWithCommas = (value) => {
  if (value == null || isNaN(value)) return value;
  const [intPart, decPart] = value.toString().split(".");
  const formattedInt = Number(intPart).toLocaleString();
  return decPart ? `${formattedInt}.${decPart}` : formattedInt;
};

export const LAST_ACTIVE_STYLES = {
  fontFamily: "IBM Plex Sans",
  fontWeight: 400,
  fontStyle: "normal",
  fontSize: "13px",
  lineHeight: "20px",
  letterSpacing: "0%",
  verticalAlign: "middle",
  color: "var(--text-primary)",
};
