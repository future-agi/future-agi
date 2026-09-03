import React, {
  useState,
  useEffect,
  useMemo,
  useCallback,
  useRef,
} from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Checkbox,
  Chip,
  ClickAwayListener,
  Divider,
  FormControl,
  IconButton,
  Link,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Paper,
  Popper,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
  useTheme,
  InputAdornment,
  CircularProgress,
} from "@mui/material";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import ReactApexChart from "react-apexcharts";
import ChartLegend from "./ChartLegend";
import WidgetPieCharts from "./WidgetPieCharts";
import {
  buildTimeRangePayload,
  resolveInitialTimeRange,
} from "./dashboardDateRange";
import { paths } from "src/routes/paths";
import {
  useDashboardDetail,
  useDashboardQuery,
  useCreateWidget,
  useUpdateWidget,
  useDeleteWidget,
  isPropertyCatalogNotReadyError,
  useLegacyDashboardMetricsPaginated,
  usePropertyCatalog,
  useSimulationAgents,
} from "src/hooks/useDashboards";
import { useDebounce } from "src/hooks/use-debounce";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import Iconify from "src/components/iconify";
import BoundedCursorPaginationControl from "src/components/BoundedCursorPaginationControl";
import FilterValueLabel, {
  useResolvedFilterOptions,
} from "src/components/filter-value-label";
import { useSnackbar } from "src/components/snackbar";
import { ConfirmDialog } from "src/components/custom-dialog";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import WidgetDescriptionPopover from "./WidgetDescriptionPopover";
import TruncatedTooltipText from "./TruncatedTooltipText";
import { format } from "date-fns";
import CustomDateRangePicker from "src/components/custom-datepicker/DatePicker";
import AttributeInventoryControls from "src/sections/projects/LLMTracing/AttributeInventoryControls";
import {
  attributeInventoryKey,
  useLegacyCursorAttributeInventory,
} from "src/sections/projects/LLMTracing/useCursorAttributeInventory";
import useCanEditDashboard from "./hooks/useCanEditDashboard";
import {
  coerceFilterValue,
  FILTER_STRING_MAX_UTF8_BYTES,
  getUtf8ByteLength,
  isAllowedFilterOperator,
  normalizeColumnType,
  normalizeFilterType,
  TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES,
} from "src/api/contracts/filter-contract";
import {
  FILTER_VALUE_SEARCH_DEBOUNCE_MS,
  PROPERTY_CATALOG_PAGE_SIZE,
  PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
} from "src/config/runtime_limits";

import {
  DEFAULT_DECIMALS,
  escapeHtml,
  formatValueWithConfig,
  fromAxisConfigPayload,
  getAggColumnLabel,
  getAutoDecimals,
  getExactDashboardResult,
  getDashboardMetricSeriesState,
  getPlottedChartSeries,
  getSeriesScalar,
  groupPieSeries,
  getSuggestedUnitConfig,
  getUnitRendering,
  getYAxisRangeWarning,
  shouldConnectAcrossMissingBuckets,
  resolveSavedSelection,
  toAxisConfigPayload,
} from "./widgetUtils";
import {
  AGGREGATION_PREPARING_MESSAGE,
  createAggregationPollController,
  getFilterValueReadMessage,
  getAggregationRefreshState,
  getExactAggregationReadState,
} from "src/utils/queryReadState";
import {
  AGGREGATION_OPTIONS,
  ALL_AGGREGATIONS,
  PERCENTILE_OPTIONS,
  DATE_PRESETS,
  DEFAULT_WIDGET_HEIGHT,
} from "./constants";
import {
  WidgetEditorLoadFailure,
  WidgetPreviewStatus,
} from "./widgetEditorStatus";
import {
  getWidgetEditorLoadState,
  getWidgetPreviewState,
  shouldBlockWidgetPreviewForFailure,
  WIDGET_PREVIEW_MAX_WAIT_MS,
} from "./widgetEditorState";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

const WIDGET_PREVIEW_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

const escapeCsvField = (field) => {
  const str = String(field ?? "");
  if (str.includes(",") || str.includes('"') || str.includes("\n")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
};

const SAVED_NAV_DELAY_MS = 400;
const AXIS_LABEL_MAX_LENGTH = 50;

const GRANULARITY_OPTIONS = [
  { label: "Minute", value: "minute" },
  { label: "Hour", value: "hour" },
  { label: "Day", value: "day" },
  { label: "Week", value: "week" },
  { label: "Month", value: "month" },
];

// Default granularity per time preset
const PRESET_DEFAULT_GRANULARITY = {
  "30m": "minute",
  "6h": "minute",
  today: "hour",
  yesterday: "hour",
  "7D": "day",
  "30D": "day",
  "3M": "month",
  "6M": "month",
  "12M": "month",
};

// Returns allowed granularity options for a given preset (or custom range in days)
function getAllowedGranularities(preset, customDays) {
  // Determine approximate range in days
  let days;
  if (preset === "custom" && customDays != null) {
    days = customDays;
  } else {
    const presetDays = {
      "30m": 0.02,
      "6h": 0.25,
      today: 1,
      yesterday: 1,
      "7D": 7,
      "30D": 30,
      "3M": 90,
      "6M": 180,
      "12M": 365,
    };
    days = presetDays[preset] ?? 30;
  }

  return GRANULARITY_OPTIONS.filter((g) => {
    if (g.value === "minute" && days > 7) return false;
    if (g.value === "hour" && days > 90) return false;
    return true;
  });
}

const CHART_TYPES = [
  { label: "Line", value: "line", icon: "mdi:chart-line", group: "line" },
  {
    label: "Stacked Line",
    value: "stacked_line",
    icon: "mdi:chart-line-stacked",
    group: "line",
  },
  { label: "Column", value: "column", icon: "mdi:chart-bar", group: "column" },
  {
    label: "Stacked Column",
    value: "stacked_column",
    icon: "mdi:chart-bar-stacked",
    group: "column",
  },
  {
    label: "Bar",
    value: "bar",
    icon: "mdi:chart-timeline-variant-shimmer",
    group: "bar",
  },
  {
    label: "Stacked Bar",
    value: "stacked_bar",
    icon: "mdi:chart-timeline-variant-shimmer",
    group: "bar",
  },
  { label: "Pie", value: "pie", icon: "mdi:chart-pie", group: "other" },
  { label: "Table", value: "table", icon: "mdi:table", group: "other" },
  { label: "Metric", value: "metric", icon: "mdi:pound", group: "other" },
];

// Curated list of unit presets shown in the widget editor's Unit
// dropdown. Keep in sync with ``UNIT_RENDERING`` in ``widgetUtils.js``
// (the formatter that places these as a prefix or suffix). "Custom" is
// rendered separately by the editor and maps to an empty unit value.
const UNIT_PRESETS = [
  { label: "$", value: "$" },
  { label: "%", value: "%" },
  { label: "#", value: "#" },
  { label: "ms", value: "ms" },
  { label: "s", value: "s" },
  { label: "tokens", value: "tokens" },
  { label: "cents", value: "cents" },
  { label: "wpm", value: "wpm" },
  { label: "/min", value: "/min" },
];

const METRIC_CATEGORIES = [
  { key: "all", label: "All", icon: "mdi:view-grid-outline" },
  {
    key: "trace",
    label: "Traces",
    icon: "mdi:chart-timeline-variant",
    source: "traces",
    category: "system_metric",
  },
  {
    key: "eval_metric",
    label: "Evals",
    icon: "mdi:check-circle-outline",
    category: "eval_metric",
  },
  {
    key: "prompt",
    label: "Prompts",
    icon: "mdi:text-box-edit-outline",
    source: "traces",
    category: "system_metric",
    nameFilter: "prompt_",
  },
  {
    key: "dataset",
    label: "Datasets",
    icon: "mdi:table-large",
    source: "datasets",
  },
  {
    key: "simulation",
    label: "Simulate",
    icon: "mdi:robot-outline",
    source: "simulation",
  },
  {
    key: "user",
    label: "Users",
    icon: "mdi:account-group-outline",
    source: "traces",
    category: "system_metric",
    nameFilter: "user",
  },
  {
    key: "annotation",
    label: "Annotations",
    icon: "mdi:format-quote-close",
    category: "annotation_metric",
  },
  {
    key: "custom_attribute",
    label: "Trace Attributes",
    icon: "mdi:tune-variant",
    source: "traces",
    category: "custom_attribute",
  },
];

// Default aggregations by eval output type (dataset workflow)
const EVAL_DEFAULT_AGGREGATIONS = {
  SCORE: "avg",
  PASS_FAIL: "pass_rate",
  CHOICE: "count",
  CHOICES: "count",
};

export function getWidgetMetricDataType(metric) {
  const category = metric?.category;
  const outputType = String(metric?.outputType || metric?.output_type || "");
  if (category === "annotation_metric" || category === "annotationMetric") {
    return ["numeric", "star"].includes(outputType.toLowerCase())
      ? "number"
      : "string";
  }
  if (category === "eval_metric" || category === "evalMetric") {
    return outputType.toUpperCase() === "SCORE" ? "number" : "string";
  }
  return metric?.type || "number";
}

const WIDGET_OPTION_TYPE_BY_CATALOG_CATEGORY = {
  system_metric: "system",
  eval_metric: "eval_metric",
  annotation_metric: "annotation",
  custom_attribute: "custom_attribute",
  custom_column: "custom_column",
};

const getEligibleWidgetAttributeTypes = (attributeTypes, pickerMode) =>
  attributeTypes.filter((dataType) =>
    pickerMode === "metric"
      ? dataType === "number"
      : pickerMode === "breakdown"
        ? ["string", "number", "boolean"].includes(dataType)
        : ["string", "number", "boolean", "array"].includes(dataType),
  );

const expandWidgetAttributeOption = ({
  option,
  attributeTypes,
  attributeTypesExact,
  pickerMode,
}) =>
  getEligibleWidgetAttributeTypes(attributeTypes, pickerMode).map(
    (dataType) => ({
      ...option,
      dataType,
      attributeTypes,
      attributeTypesExact,
    }),
  );

const normalizeWidgetCatalogSearchText = (value) =>
  String(value || "")
    .toLowerCase()
    .replace(/[_\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

const widgetOptionSources = (option) =>
  new Set(
    [option?.source, ...(option?.sources || [])].filter(Boolean).map(String),
  );

// eslint-disable-next-line react-refresh/only-export-components
export function isWidgetPickerOptionInCategory(option, pickerCategory) {
  if (!option || !pickerCategory || pickerCategory === "all") return true;
  const sources = widgetOptionSources(option);
  const targetsSource = (source) =>
    sources.has(source) || sources.has("all") || sources.has("both");
  const searchableIdentity = normalizeWidgetCatalogSearchText(
    `${option.id || ""} ${option.name || ""}`,
  );

  switch (pickerCategory) {
    case "trace":
      return option.type === "system" && targetsSource("traces");
    case "eval_metric":
      return option.type === "eval_metric";
    case "prompt":
      return (
        option.type === "system" &&
        targetsSource("traces") &&
        searchableIdentity.includes("prompt")
      );
    case "dataset":
      return targetsSource("datasets");
    case "simulation":
      return targetsSource("simulation");
    case "user":
      return (
        option.type === "system" &&
        targetsSource("traces") &&
        searchableIdentity.includes("user")
      );
    case "annotation":
      return option.type === "annotation";
    case "custom_attribute":
      return option.type === "custom_attribute";
    default:
      return false;
  }
}

// eslint-disable-next-line react-refresh/only-export-components
export function resolveWidgetCatalogResultMetrics({
  baseMetrics,
  scopedMetrics,
  scopedRequestActive,
  requestSettled,
}) {
  if (!requestSettled) return [];
  return scopedRequestActive ? scopedMetrics || [] : baseMetrics || [];
}

// eslint-disable-next-line react-refresh/only-export-components
export function isWidgetCatalogInventoryLoading({
  requestSettled,
  usesLegacyCatalog,
  legacyCatalogLoading,
  propertyCatalogLoading,
  propertyCatalogSearchPending,
  propertyCatalogNotReady,
}) {
  if (!requestSettled) return true;
  if (usesLegacyCatalog) return Boolean(legacyCatalogLoading);
  return Boolean(
    propertyCatalogLoading ||
      propertyCatalogSearchPending ||
      propertyCatalogNotReady,
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function buildWidgetCatalogPickerOptions({
  metrics: catalogMetrics,
  pickerMode,
  pickerCategory = "all",
  search = "",
  requestSettled = true,
  selectedMetricSources = [],
  targetMetricSource = null,
}) {
  if (!requestSettled) return [];

  const mappedOptions = (catalogMetrics || [])
    .filter((metric) => pickerMode !== "metric" || metric.role !== "dimension")
    .filter((metric) =>
      isWidgetCatalogOptionAllowed(metric, pickerMode, {
        selectedMetricSources,
        targetMetricSource,
      }),
    )
    .flatMap((metric) => {
      const option = {
        id: metric.name,
        registryId: metric.propertyId || metric.property_id,
        name: metric.displayName || metric.display_name || metric.name,
        type:
          WIDGET_OPTION_TYPE_BY_CATALOG_CATEGORY[metric.category] ||
          metric.category,
        source: metric.source,
        sources: metric.sources,
        dataType: getWidgetMetricDataType(metric),
        outputType: metric.outputType || metric.output_type,
        columnDataType: metric.dataType || metric.data_type,
        configIds: metric.configIds || metric.config_ids,
        evalKey: metric.evalKey || metric.eval_key,
        unit: metric.unit,
        choices: metric.choices,
        choiceOptions: metric.choiceOptions || metric.choice_options,
        allowedAggregations:
          metric.allowedAggregations || metric.allowed_aggregations,
      };
      const attributeTypes = [
        ...(metric.attributeTypes || metric.attribute_types || []),
      ].filter(
        (attributeType, index, values) =>
          attributeType && values.indexOf(attributeType) === index,
      );
      if (option.type !== "custom_attribute" || attributeTypes.length === 0) {
        return [option];
      }
      return expandWidgetAttributeOption({
        option,
        attributeTypes,
        attributeTypesExact:
          metric.attributeTypesExact ?? metric.attribute_types_exact ?? false,
        pickerMode,
      });
    })
    // The server category is authoritative, and this local guard prevents a
    // cached or compatibility response from leaking System rows into Trace
    // Attributes (or vice versa) after an explicit category selection.
    .filter((option) => isWidgetPickerOptionInCategory(option, pickerCategory));

  const seen = new Set();
  const uniqueOptions = mappedOptions.filter((option) => {
    const identity = `${option.registryId || `${option.source || "all"}:${option.type}:${option.id}`}:${option.dataType || ""}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });

  const normalizedSearch = normalizeWidgetCatalogSearchText(search);
  if (!normalizedSearch) return uniqueOptions;

  // Exact matches stay first, but never suppress fuzzy server matches from
  // other categories. `cost` under All must retain both Cost and matching
  // cost_breakdown.* custom attributes.
  const exactMatches = uniqueOptions.filter((option) =>
    [option.id, option.name].some(
      (candidate) =>
        normalizeWidgetCatalogSearchText(candidate) === normalizedSearch,
    ),
  );
  const exactSet = new Set(exactMatches);
  return [
    ...exactMatches,
    ...uniqueOptions.filter((option) => !exactSet.has(option)),
  ];
}

// eslint-disable-next-line react-refresh/only-export-components
export function getWidgetCatalogExactResultCount({
  request,
  categoryCounts,
  categoryCountsExact,
  requestSettled,
}) {
  if (!requestSettled || !categoryCountsExact || !categoryCounts) return null;
  const countKey = request?.category || "all";
  const count = categoryCounts[countKey];
  return Number.isSafeInteger(count) && count >= 0 ? count : null;
}

const WIDGET_SIDEBAR_COUNT_KEY = {
  all: "all",
  eval_metric: "eval_metric",
  annotation: "annotation_metric",
  custom_attribute: "custom_attribute",
};

// eslint-disable-next-line react-refresh/only-export-components
export function getWidgetCatalogSidebarCategoryCount({
  pickerCategory,
  categoryCounts,
  categoryCountsExact,
}) {
  if (!categoryCountsExact || !categoryCounts) return null;
  const countKey = WIDGET_SIDEBAR_COUNT_KEY[pickerCategory];
  if (!countKey) return null;
  const count = categoryCounts[countKey];
  return Number.isSafeInteger(count) && count >= 0 ? count : null;
}

// eslint-disable-next-line react-refresh/only-export-components
export function getWidgetCatalogCategoryCountPresentation(count) {
  if (Number.isSafeInteger(count) && count >= 0) {
    return { text: String(count), title: null, exact: true };
  }
  return {
    text: "—",
    title: "Exact count unavailable",
    exact: false,
  };
}

// eslint-disable-next-line react-refresh/only-export-components
export function resolveWidgetCatalogSidebarCounts({
  requestSettled,
  search,
  baseCategoryCounts,
  baseCategoryCountsExact,
  allSearchCategoryCounts,
  allSearchCategoryCountsExact,
}) {
  if (!requestSettled) return null;
  if (!String(search || "").trim()) {
    return baseCategoryCountsExact ? baseCategoryCounts || null : null;
  }
  return allSearchCategoryCountsExact ? allSearchCategoryCounts || null : null;
}

export function buildWidgetCursorAttributeOptions(
  cursorAttributes,
  pickerMode,
) {
  return (cursorAttributes || [])
    .flatMap((attribute) => {
      const key = attributeInventoryKey(attribute);
      if (!key) return [];
      const attributeTypes = [
        ...(typeof attribute === "string" ? ["string"] : [attribute.type]),
        ...(Array.isArray(attribute?.types) ? attribute.types : []),
      ].filter(
        (attributeType, index, values) =>
          attributeType && values.indexOf(attributeType) === index,
      );
      // Map/object values require a structured object editor that this Widget
      // picker does not have. JSON-only eval-mapping fields are likewise not a
      // Widget filter contract. Keep both out instead of coercing them to text.
      return expandWidgetAttributeOption({
        option: {
          id: key,
          registryId: `custom_attribute:${key}`,
          name: key,
          type: "custom_attribute",
          source: "traces",
        },
        attributeTypes,
        attributeTypesExact: attribute?.types_exact === true,
        pickerMode,
      });
    })
    .filter(Boolean);
}

export function mergeWidgetCursorAttributeOptions(
  catalogOptions,
  cursorOptions,
  cursorActive,
) {
  if (!cursorActive) return catalogOptions;
  const options = [
    ...catalogOptions.filter((option) => option.type !== "custom_attribute"),
    ...cursorOptions,
  ];
  const seen = new Set();
  return options.filter((option) => {
    const identity = `${option.registryId || `${option.source || "all"}:${option.type}:${option.id}`}:${option.dataType || ""}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

export function getWidgetMetricCatalogRequest({
  pickerCategory,
  search,
  pickerOpen,
  pickerMode = "metric",
}) {
  const activeCategory = METRIC_CATEGORIES.find(
    (category) => category.key === pickerCategory,
  );
  return {
    category: activeCategory?.category || "",
    source: activeCategory?.source || "",
    search: activeCategory?.nameFilter
      ? search || activeCategory.nameFilter
      : search,
    // Metric selection must page and count only aggregatable properties.
    // Without this server-side scope, a broad dimension-only search can say
    // "302 results" while rendering none and offer an effectively endless
    // Load more cursor through rows the picker intentionally hides.
    role: pickerMode === "metric" ? "metric" : "",
    pageSize: PROPERTY_CATALOG_SEARCH_PAGE_SIZE,
    // Only the legacy fallback reads this flag. The activated catalog always
    // includes custom attributes; the fallback must never rescan the large
    // source attribute maps while the catalog is unavailable.
    excludeCustomAttributes: true,
    enabled: pickerOpen,
  };
}

export function WidgetCatalogPaginationControl({
  pickerCategory,
  hasNextPage,
  continuationKey,
  isFetchingNextPage,
  isFetchNextPageError = false,
  onLoadMore,
  attributeHasNextPage = false,
  attributeContinuationKey,
  isFetchingAttributeNextPage = false,
  isFetchNextAttributePageError = false,
  onLoadMoreAttributes,
}) {
  const attributePageAvailable =
    (pickerCategory === "all" || pickerCategory === "custom_attribute") &&
    Boolean(attributeHasNextPage);
  return (
    <BoundedCursorPaginationControl
      channels={[
        {
          channelKey: "catalog",
          hasNextPage: Boolean(hasNextPage),
          continuationKey,
          isFetching: isFetchingNextPage,
          error: isFetchNextPageError,
          loadNextPage: onLoadMore,
        },
        {
          channelKey: "attributes",
          hasNextPage: attributePageAvailable,
          continuationKey: attributeContinuationKey,
          isFetching: isFetchingAttributeNextPage,
          error:
            (pickerCategory === "all" ||
              pickerCategory === "custom_attribute") &&
            isFetchNextAttributePageError,
          loadNextPage: onLoadMoreAttributes,
        },
      ]}
      testId="widget-catalog-pagination-sentinel"
      errorMessage="The next page failed. Loaded attributes remain available."
      retryLabel="Retry"
    />
  );
}

WidgetCatalogPaginationControl.propTypes = {
  pickerCategory: PropTypes.string.isRequired,
  hasNextPage: PropTypes.bool,
  continuationKey: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  isFetchingNextPage: PropTypes.bool,
  isFetchNextPageError: PropTypes.bool,
  onLoadMore: PropTypes.func.isRequired,
  attributeHasNextPage: PropTypes.bool,
  attributeContinuationKey: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.number,
  ]),
  isFetchingAttributeNextPage: PropTypes.bool,
  isFetchNextAttributePageError: PropTypes.bool,
  onLoadMoreAttributes: PropTypes.func,
};

const DATASET_WIDGET_DIMENSIONS = new Set([
  "dataset",
  "eval_template",
  "column_name",
  "column_source",
  "cell_status",
]);

const SIMULATION_FILTER_DIMENSIONS = new Set([
  "agent_definition",
  "agent_latency",
  "agent_version",
  "ai_interruption_rate",
  "ai_interruptions",
  "bot_wpm",
  "call_type",
  "customer_cost",
  "duration",
  "ended_reason",
  "llm_cost",
  "llm_latency",
  "message_count",
  "overall_score",
  "persona",
  "persona_accent",
  "persona_age_group",
  "persona_communication_style",
  "persona_conversation_speed",
  "persona_gender",
  "persona_language",
  "persona_location",
  "persona_personality",
  "persona_profession",
  "response_time",
  "run_test",
  "scenario",
  "scenario_type",
  "simulation",
  "status",
  "stop_time_after_interruption",
  "stt_cost",
  "stt_latency",
  "talk_ratio",
  "test_execution",
  "total_cost",
  "tts_cost",
  "tts_latency",
  "user_interruption_rate",
  "user_interruptions",
  "user_wpm",
]);

const SIMULATION_BREAKDOWN_DIMENSIONS = new Set([
  "agent_definition",
  "agent_version",
  "call_type",
  "ended_reason",
  "persona",
  "persona_accent",
  "persona_age_group",
  "persona_communication_style",
  "persona_conversation_speed",
  "persona_gender",
  "persona_language",
  "persona_location",
  "persona_personality",
  "persona_profession",
  "run_test",
  "scenario",
  "scenario_type",
  "simulation",
  "status",
  "test_execution",
]);

export function isWidgetCatalogOptionAllowed(
  metric,
  pickerMode,
  { selectedMetricSources = [], targetMetricSource = null } = {},
) {
  if (!["filter", "metric_filter", "breakdown"].includes(pickerMode)) {
    return true;
  }
  const declaredSources = [
    ...(metric.source ? [metric.source] : []),
    ...(metric.sources || []),
  ];
  const optionSources = new Set(
    declaredSources.length > 0 ? declaredSources : ["traces"],
  );
  const targetsTrace = ["traces", "all", "both"].some((source) =>
    optionSources.has(source),
  );
  const targetsDataset = ["datasets", "all", "both"].some((source) =>
    optionSources.has(source),
  );
  const targetsSimulation = optionSources.has("simulation");
  const isSupportedDatasetDimension =
    metric.category === "system_metric" &&
    DATASET_WIDGET_DIMENSIONS.has(metric.name);
  const isSupportedSimulationDimension =
    metric.category === "system_metric" &&
    (pickerMode === "breakdown"
      ? SIMULATION_BREAKDOWN_DIMENSIONS
      : SIMULATION_FILTER_DIMENSIONS
    ).has(metric.name);

  if (pickerMode === "metric_filter") {
    if (targetMetricSource === "datasets") {
      return isSupportedDatasetDimension;
    }
    return targetMetricSource === "simulation"
      ? targetsSimulation && isSupportedSimulationDimension
      : targetsTrace;
  }

  const selectedSources = new Set(selectedMetricSources);
  if (
    selectedSources.has("datasets") &&
    targetsDataset &&
    !isSupportedDatasetDimension
  ) {
    return false;
  }
  if (
    selectedSources.has("simulation") &&
    targetsSimulation &&
    !isSupportedSimulationDimension
  ) {
    return false;
  }
  return (
    (selectedSources.has("traces") && targetsTrace) ||
    (selectedSources.has("datasets") && targetsDataset) ||
    (selectedSources.has("simulation") && targetsSimulation)
  );
}

function getWidgetMetricAdapterSource(metric) {
  if (metric.source === "datasets") return "datasets";
  if (metric.source === "simulation" && metric.type !== "custom_attribute") {
    return "simulation";
  }
  return "traces";
}

// Additional aggregation options for dataset-specific types
const DATASET_EXTRA_AGGREGATIONS = [
  { label: "Pass Rate", value: "pass_rate" },
  { label: "Fail Rate", value: "fail_rate" },
  { label: "Pass Count", value: "pass_count" },
  { label: "Fail Count", value: "fail_count" },
  { label: "True Rate", value: "true_rate" },
];

const PRESENCE_FILTER_OPERATORS = [
  { label: "Is set", value: "is_set", noValue: true },
  { label: "Is not set", value: "is_not_set", noValue: true },
];

const STRING_FILTER_OPERATORS = [
  { label: "Is", value: "contains", multi: true },
  { label: "Is not", value: "not_contains", multi: true },
  { label: "Contains", value: "str_contains" },
  { label: "Does not contain", value: "str_not_contains" },
  ...PRESENCE_FILTER_OPERATORS,
];

const COMPARABLE_FILTER_OPERATORS = [
  { label: "Equals", value: "equal_to" },
  { label: "Not equal", value: "not_equal_to" },
  { label: "Greater than", value: "greater_than" },
  { label: "Greater than or equal to", value: "greater_than_or_equal" },
  { label: "Less than", value: "less_than" },
  { label: "Less than or equal to", value: "less_than_or_equal" },
  { label: "Between", value: "between", range: true },
  { label: "Not between", value: "not_between", range: true },
  ...PRESENCE_FILTER_OPERATORS,
];

const BOOLEAN_FILTER_OPERATORS = [
  { label: "Equals", value: "equal_to" },
  { label: "Not equal", value: "not_equal_to" },
  ...PRESENCE_FILTER_OPERATORS,
];

const ARRAY_FILTER_OPERATORS = [
  { label: "Contains", value: "str_contains", multi: true },
  { label: "Does not contain", value: "str_not_contains", multi: true },
  ...PRESENCE_FILTER_OPERATORS,
];

export const getWidgetFilterOperators = (dataType) => {
  const filterType = normalizeFilterType(dataType || "string");
  if (filterType === "number" || filterType === "datetime") {
    return COMPARABLE_FILTER_OPERATORS;
  }
  if (filterType === "boolean") return BOOLEAN_FILTER_OPERATORS;
  if (filterType === "array") return ARRAY_FILTER_OPERATORS;
  return STRING_FILTER_OPERATORS;
};

export function getWidgetFilterDefaults(dataType) {
  const filterType = normalizeFilterType(dataType || "string");
  if (["number", "datetime", "boolean"].includes(filterType)) {
    return { operator: "equal_to", value: "", opensValuePicker: false };
  }
  if (filterType === "array") {
    return { operator: "str_contains", value: [], opensValuePicker: true };
  }
  return { operator: "contains", value: [], opensValuePicker: true };
}

const NO_VALUE_OPERATORS = new Set(["is_set", "is_not_set"]);

const DASHBOARD_FILTER_OP_TO_API = {
  equal_to: "equals",
  not_equal_to: "not_equals",
  contains: "in",
  not_contains: "not_in",
  str_contains: "contains",
  str_not_contains: "not_contains",
  is_set: "is_not_null",
  is_not_set: "is_null",
};

export function buildWidgetFilterConfig(filter) {
  const filterType = normalizeFilterType(
    filter?.dataType || "string",
    filter?.value,
  );
  const filterOp =
    DASHBOARD_FILTER_OP_TO_API[filter?.operator] || filter?.operator;
  if (!isAllowedFilterOperator(filterType, filterOp)) return null;

  const filterValue = coerceFilterValue(filter?.value, filterOp, filterType);
  const colType = normalizeColumnType(DASHBOARD_TYPE_TO_COL_TYPE[filter?.type]);
  const attributeValueTypes = (() => {
    if (
      colType !== "SPAN_ATTRIBUTE" ||
      !["in", "not_in"].includes(filterOp) ||
      !Array.isArray(filterValue) ||
      !Array.isArray(filter?.valueTypes) ||
      filter.valueTypes.length !== filterValue.length
    ) {
      return undefined;
    }
    const valueTypes = filter.valueTypes.map((valueType) =>
      ["string", "number", "boolean"].includes(valueType) ? valueType : null,
    );
    return valueTypes.some(Boolean) ? valueTypes : undefined;
  })();
  return {
    filter_type: filterType,
    filter_op: filterOp,
    filter_value: filterValue,
    ...(colType && { col_type: colType }),
    ...(attributeValueTypes && {
      attribute_value_types: attributeValueTypes,
    }),
  };
}

export function hasWidgetFilterValue(filter) {
  if (!filter?.id) return false;
  if (NO_VALUE_OPERATORS.has(filter.operator)) return true;
  if (Array.isArray(filter.value)) return filter.value.length > 0;
  return (
    filter.value !== "" && filter.value !== null && filter.value !== undefined
  );
}

export function buildLinkedProjectFilter(projectIds) {
  return {
    id: "project",
    registryId: "system_attribute:traces:project",
    name: "Project",
    type: "system",
    dataType: "string",
    source: "traces",
    operator: "contains",
    value: projectIds,
  };
}

const DASHBOARD_TYPE_TO_COL_TYPE = {
  system: "SYSTEM_METRIC",
  eval_metric: "EVAL_METRIC",
  annotation: "ANNOTATION",
  custom_attribute: "SPAN_ATTRIBUTE",
  custom_column: "CUSTOM_COLUMN",
};

const COL_TYPE_TO_DASHBOARD_TYPE = {
  SYSTEM_METRIC: "system",
  EVAL_METRIC: "eval_metric",
  ANNOTATION: "annotation",
  SPAN_ATTRIBUTE: "custom_attribute",
  CUSTOM_COLUMN: "custom_column",
};

export function normalizeWidgetDashboardDataType(dataType) {
  const filterType = normalizeFilterType(dataType || "string");
  if (filterType === "text") return "string";
  if (filterType === "datetime") return "date";
  if (filterType === "categorical") return "string";
  return filterType;
}

export function toWidgetDashboardFilterOperator(operator, filterType) {
  if (operator === "in") return "contains";
  if (operator === "not_in") return "not_contains";
  if (operator === "contains") return "str_contains";
  if (operator === "not_contains") return "str_not_contains";
  if (operator === "is_not_null") return "is_set";
  if (operator === "is_null") return "is_not_set";
  if (operator === "equals") {
    return ["number", "datetime", "boolean"].includes(filterType)
      ? "equal_to"
      : "contains";
  }
  if (operator === "not_equals") {
    return ["number", "datetime", "boolean"].includes(filterType)
      ? "not_equal_to"
      : "not_contains";
  }
  return operator;
}

export function restoreWidgetFilterConfig(config) {
  const filterType = normalizeFilterType(config?.filter_type || "string");
  const operator = toWidgetDashboardFilterOperator(
    config?.filter_op,
    filterType,
  );
  const isMulti = operator === "contains" || operator === "not_contains";
  const value = NO_VALUE_OPERATORS.has(operator)
    ? ""
    : isMulti
      ? Array.isArray(config?.filter_value)
        ? config.filter_value
        : [config?.filter_value].filter(
            (candidate) => candidate !== null && candidate !== undefined,
          )
      : config?.filter_value ?? (filterType === "number" ? "" : []);
  const valueTypes = Array.isArray(config?.attribute_value_types)
    ? config.attribute_value_types
    : undefined;
  const attributeTypes = valueTypes
    ? [
        ...new Set(
          [normalizeWidgetDashboardDataType(filterType), ...valueTypes].filter(
            Boolean,
          ),
        ),
      ]
    : undefined;
  return {
    dataType: normalizeWidgetDashboardDataType(filterType),
    operator,
    value,
    ...(valueTypes && { valueTypes }),
    ...(attributeTypes && { attributeTypes }),
  };
}

const METRIC_TYPE_ICONS = {
  system: "mdi:cog-outline",
  eval_metric: "mdi:check-circle-outline",
  annotation: "mdi:format-quote-close",
  custom_attribute: "mdi:tune-variant",
  custom_column: "mdi:table-column",
};

const SERIES_COLORS = [
  "#7B56DB", // purple (primary)
  "#1ABCFE", // cyan
  "#FF6B6B", // coral red
  "#2ECB71", // emerald green
  "#F7B731", // amber
  "#E84393", // magenta pink
  "#0984E3", // ocean blue
  "#FD7E14", // tangerine orange
  "#00CEC9", // teal
  "#A29BFE", // lavender
];

const hashSeriesName = (name) => {
  const s = String(name || "");
  let h = 0;
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
};
const buildSeriesColorMap = (names) => {
  const map = {};
  const used = new Set();
  (names || []).forEach((name) => {
    const start = hashSeriesName(name) % SERIES_COLORS.length;
    let picked = start;
    for (let i = 0; i < SERIES_COLORS.length; i += 1) {
      const candidate = (start + i) % SERIES_COLORS.length;
      if (!used.has(candidate)) {
        picked = candidate;
        break;
      }
    }
    used.add(picked);
    map[name] = SERIES_COLORS[picked];
  });
  return map;
};
const getSeriesColor = (name, map) =>
  (map && map[name]) ||
  SERIES_COLORS[hashSeriesName(name) % SERIES_COLORS.length];

const LETTER_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

function ToggleButtons({ options, value, onChange, theme }) {
  return (
    <Box
      sx={{
        display: "inline-flex",
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      {options.map((opt, i) => (
        <Box
          key={opt.value}
          onClick={() => onChange(opt.value)}
          sx={{
            px: 1.5,
            py: 0.5,
            cursor: "pointer",
            fontSize: "13px",
            fontWeight: value === opt.value ? 600 : 400,
            color:
              value === opt.value
                ? theme.palette.text.primary
                : theme.palette.text.secondary,
            bgcolor:
              value === opt.value
                ? theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.08)"
                  : "rgba(0,0,0,0.06)"
                : "transparent",
            borderRight:
              i < options.length - 1
                ? `1px solid ${theme.palette.divider}`
                : "none",
            whiteSpace: "nowrap",
            userSelect: "none",
            transition: "all 0.15s",
            "&:hover": {
              bgcolor:
                theme.palette.mode === "dark"
                  ? "rgba(255,255,255,0.05)"
                  : "rgba(0,0,0,0.03)",
            },
          }}
        >
          {opt.label}
        </Box>
      ))}
    </Box>
  );
}

ToggleButtons.propTypes = {
  options: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.node.isRequired,
      value: PropTypes.any,
    }),
  ).isRequired,
  value: PropTypes.any,
  onChange: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
};

function AxisSection({ title, config, onChange, theme, showReset, onReset }) {
  return (
    <Box sx={{ mb: 3 }}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="subtitle2" fontWeight={700}>
          {title}
        </Typography>
        {showReset && (
          <Typography
            variant="caption"
            sx={{
              cursor: "pointer",
              color: "text.secondary",
              "&:hover": { color: "text.primary" },
            }}
            onClick={onReset}
          >
            Reset
          </Typography>
        )}
      </Stack>

      {/* Axis Visible/Hidden */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Axis
        </Typography>
        <ToggleButtons
          options={[
            { label: "Visible", value: true },
            { label: "Hidden", value: false },
          ]}
          value={config.visible}
          onChange={(v) => onChange("visible", v)}
          theme={theme}
        />
      </Stack>

      {/* Label */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Label
        </Typography>
        <TextField
          size="small"
          value={config.label}
          onChange={(e) => onChange("label", e.target.value)}
          placeholder="e.g. Cost ($)"
          inputProps={{ maxLength: AXIS_LABEL_MAX_LENGTH }}
          sx={{ width: 180, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
        />
      </Stack>

      {/* Unit */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Unit
        </Typography>
        <TextField
          select
          size="small"
          value={
            UNIT_PRESETS.some((u) => u.value === config.unit)
              ? config.unit
              : "custom"
          }
          onChange={(e) =>
            onChange("unit", e.target.value === "custom" ? "" : e.target.value)
          }
          sx={{ width: 180, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
        >
          {UNIT_PRESETS.map((opt) => (
            <MenuItem
              key={opt.value}
              value={opt.value}
              sx={{ fontSize: "13px" }}
            >
              {opt.label}
            </MenuItem>
          ))}
          <MenuItem value="custom" sx={{ fontSize: "13px" }}>
            Custom
          </MenuItem>
        </TextField>
      </Stack>

      {/* Prefix / Suffix */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Prefix / Suffix
        </Typography>
        <ToggleButtons
          options={[
            { label: "Prefix", value: "prefix" },
            { label: "Suffix", value: "suffix" },
          ]}
          value={config.prefixSuffix}
          onChange={(v) => onChange("prefixSuffix", v)}
          theme={theme}
        />
      </Stack>

      {/* Abbreviation */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Abbreviation
        </Typography>
        <ToggleButtons
          options={[
            { label: "Yes", value: true },
            { label: "No", value: false },
          ]}
          value={config.abbreviation}
          onChange={(v) => onChange("abbreviation", v)}
          theme={theme}
        />
      </Stack>

      {/* Decimals */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Decimals
        </Typography>
        <ToggleButtons
          options={[
            {
              label: "\u2190 .0",
              value: Math.max(0, (config.decimals ?? DEFAULT_DECIMALS) - 1),
            },
            {
              label: ".00 \u2192",
              value: (config.decimals ?? DEFAULT_DECIMALS) + 1,
            },
          ]}
          value={null}
          onChange={(v) => onChange("decimals", Math.max(0, Math.min(6, v)))}
          theme={theme}
        />
      </Stack>

      {/* Preview */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Preview
        </Typography>
        <Typography variant="body2" fontWeight={500}>
          {(() => {
            const sample = 1250000;
            return formatValueWithConfig(sample, config, {
              fallbackDecimals: DEFAULT_DECIMALS,
            });
          })()}
        </Typography>
      </Stack>

      {/* Threshold Bounds */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Threshold Bounds
        </Typography>
        <Stack direction="row" gap={0.5}>
          <TextField
            size="small"
            value={config.min}
            onChange={(e) => onChange("min", e.target.value)}
            placeholder="Min"
            sx={{ width: 80, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
          />
          <TextField
            size="small"
            value={config.max}
            onChange={(e) => onChange("max", e.target.value)}
            placeholder="Max"
            sx={{ width: 80, "& .MuiOutlinedInput-root": { fontSize: "13px" } }}
          />
        </Stack>
      </Stack>

      {/* Out of Bounds */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        sx={{ mb: 1.5 }}
      >
        <Typography variant="body2" color="text.secondary">
          Out of Bounds
        </Typography>
        <ToggleButtons
          options={[
            { label: "Visible", value: "visible" },
            { label: "Hidden", value: "hidden" },
          ]}
          value={config.outOfBounds}
          onChange={(v) => onChange("outOfBounds", v)}
          theme={theme}
        />
      </Stack>

      {/* Scale */}
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="body2" color="text.secondary">
          Scale
        </Typography>
        <ToggleButtons
          options={[
            { label: "Linear", value: "linear" },
            { label: "Logarithmic", value: "logarithmic" },
          ]}
          value={config.scale}
          onChange={(v) => onChange("scale", v)}
          theme={theme}
        />
      </Stack>
    </Box>
  );
}

AxisSection.propTypes = {
  title: PropTypes.string.isRequired,
  config: PropTypes.object.isRequired,
  onChange: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
  showReset: PropTypes.bool,
  onReset: PropTypes.func,
};

function AggregationPicker({
  value,
  onChange,
  theme,
  extraOptions,
  allowedAggregations,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [showPercentiles, setShowPercentiles] = useState(false);

  const handleOpen = (e) => {
    setAnchorEl(e.currentTarget);
    setShowPercentiles(false);
  };

  const handleClose = () => {
    setAnchorEl(null);
    setShowPercentiles(false);
  };

  const handleSelect = (val) => {
    onChange(val);
    handleClose();
  };

  const allAggs = extraOptions
    ? [...ALL_AGGREGATIONS, ...extraOptions]
    : ALL_AGGREGATIONS;
  const allowedSet = allowedAggregations?.length
    ? new Set(allowedAggregations)
    : null;
  const primaryAggs = allowedSet
    ? AGGREGATION_OPTIONS.filter((opt) => allowedSet.has(opt.value))
    : AGGREGATION_OPTIONS;
  const allowedExtraOptions = allowedSet
    ? (extraOptions || []).filter((opt) => allowedSet.has(opt.value))
    : extraOptions;
  const percentileAggs = allowedSet
    ? PERCENTILE_OPTIONS.filter((opt) => allowedSet.has(opt.value))
    : PERCENTILE_OPTIONS;
  const visibleAggs = allowedSet
    ? [...primaryAggs, ...(allowedExtraOptions || []), ...percentileAggs]
    : allAggs;
  const current = visibleAggs.find((a) => a.value === value);
  const open = Boolean(anchorEl);

  return (
    <>
      <Chip
        label={current?.label || value}
        size="small"
        variant="outlined"
        onClick={handleOpen}
        deleteIcon={<Iconify icon="mdi:chevron-down" width={14} />}
        onDelete={handleOpen}
        sx={{ mt: 1, cursor: "pointer", fontSize: "12px" }}
      />
      <Popper
        open={open}
        anchorEl={anchorEl}
        placement="bottom-start"
        sx={{ zIndex: 1400 }}
      >
        <ClickAwayListener onClickAway={handleClose}>
          <Paper
            elevation={8}
            sx={{
              minWidth: 180,
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: 1,
              overflow: "hidden",
            }}
          >
            {!showPercentiles ? (
              <List dense disablePadding>
                {primaryAggs.map((opt) => (
                  <ListItemButton
                    key={opt.value}
                    selected={value === opt.value}
                    onClick={() => handleSelect(opt.value)}
                    sx={{ py: 0.75 }}
                  >
                    <ListItemText
                      primary={opt.label}
                      primaryTypographyProps={{
                        variant: "body2",
                        fontSize: "13px",
                      }}
                    />
                  </ListItemButton>
                ))}
                {allowedExtraOptions && allowedExtraOptions.length > 0 && (
                  <>
                    <Divider />
                    {allowedExtraOptions.map((opt) => (
                      <ListItemButton
                        key={opt.value}
                        selected={value === opt.value}
                        onClick={() => handleSelect(opt.value)}
                        sx={{ py: 0.75 }}
                      >
                        <ListItemText
                          primary={opt.label}
                          primaryTypographyProps={{
                            variant: "body2",
                            fontSize: "13px",
                          }}
                        />
                      </ListItemButton>
                    ))}
                  </>
                )}
                {percentileAggs.length > 0 && (
                  <>
                    <Divider />
                    <ListItemButton
                      onClick={() => setShowPercentiles(true)}
                      sx={{ py: 0.75 }}
                    >
                      <ListItemText
                        primary="Percentile"
                        primaryTypographyProps={{
                          variant: "body2",
                          fontSize: "13px",
                          fontWeight: percentileAggs.some(
                            (p) => p.value === value,
                          )
                            ? 600
                            : 400,
                        }}
                      />
                      <Iconify
                        icon="mdi:chevron-right"
                        width={16}
                        sx={{ color: "text.secondary" }}
                      />
                    </ListItemButton>
                  </>
                )}
              </List>
            ) : (
              <List dense disablePadding>
                <ListItemButton
                  onClick={() => setShowPercentiles(false)}
                  sx={{ py: 0.75 }}
                >
                  <Iconify
                    icon="mdi:chevron-left"
                    width={16}
                    sx={{ color: "text.secondary", mr: 0.5 }}
                  />
                  <ListItemText
                    primary="Percentile"
                    primaryTypographyProps={{
                      variant: "body2",
                      fontSize: "13px",
                      fontWeight: 600,
                    }}
                  />
                </ListItemButton>
                <Divider />
                {percentileAggs.map((opt) => (
                  <ListItemButton
                    key={opt.value}
                    selected={value === opt.value}
                    onClick={() => handleSelect(opt.value)}
                    sx={{ py: 0.75 }}
                  >
                    <ListItemText
                      primary={opt.label}
                      primaryTypographyProps={{
                        variant: "body2",
                        fontSize: "13px",
                      }}
                    />
                  </ListItemButton>
                ))}
              </List>
            )}
          </Paper>
        </ClickAwayListener>
      </Popper>
    </>
  );
}

AggregationPicker.propTypes = {
  value: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
  theme: PropTypes.object.isRequired,
  extraOptions: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      value: PropTypes.string.isRequired,
    }),
  ),
  allowedAggregations: PropTypes.arrayOf(PropTypes.string),
};

const inferFilterValueStorageType = (value, configuredType) => {
  if (["string", "number", "boolean", "array"].includes(configuredType)) {
    return configuredType;
  }
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "string";
};

const selectedEntriesFromFilter = (filter) => {
  const values = Array.isArray(filter?.value) ? filter.value : [];
  const valueTypes = Array.isArray(filter?.valueTypes) ? filter.valueTypes : [];
  return values.map((value, index) => ({
    value,
    type: inferFilterValueStorageType(value, valueTypes[index]),
  }));
};

const filterValuePickerScopeKey = (filter, source) =>
  JSON.stringify([
    source,
    filter?.property_id || filter?.propertyId || filter?.registryId || null,
    filter?.metric_name ||
      filter?.metricName ||
      filter?.field ||
      filter?.name ||
      filter?.id ||
      null,
    filter?.metric_type || filter?.metricType || filter?.type || null,
    filter?.value || [],
    filter?.valueTypes || [],
  ]);

// Filter value picker popup — fetches distinct values for a given attribute
export function FilterValuePickerPopup({
  anchorEl,
  filter,
  onClose,
  onApply,
  source,
}) {
  const theme = useTheme();
  const [search, setSearch] = useState("");
  const [selectedEntries, setSelectedEntries] = useState(() =>
    selectedEntriesFromFilter(filter),
  );
  const valueOptionsListRef = useRef(null);
  const selectionScopeKey = filterValuePickerScopeKey(filter, source);
  const filterRef = useRef(filter);
  filterRef.current = filter;
  useEffect(() => {
    setSearch("");
    setSelectedEntries(selectedEntriesFromFilter(filterRef.current));
  }, [selectionScopeKey]);
  const optionStorageType = (option) => {
    return inferFilterValueStorageType(
      option?.value,
      option?.type || filter?.dataType,
    );
  };
  const selectedIdentity = (value, valueType) =>
    `${valueType}:${typeof value}:${JSON.stringify(value)}`;
  const isSelected = (value, valueType) => {
    const identity = selectedIdentity(value, valueType);
    return selectedEntries.some(
      (entry) => selectedIdentity(entry.value, entry.type) === identity,
    );
  };

  // Backend search (custom attributes) reaches values outside the fetched
  // page and the default lookback; the client-side filter below stays as the
  // instant layer on top of whatever is already loaded.
  const exactValueMaxUtf8Bytes =
    filter?.type === "custom_attribute"
      ? TYPED_ATTRIBUTE_STRING_FILTER_MAX_UTF8_BYTES
      : FILTER_STRING_MAX_UTF8_BYTES;
  const exactSearchValue = search.trim();
  const exactSearchValueTooLong =
    getUtf8ByteLength(exactSearchValue) > exactValueMaxUtf8Bytes;
  const debouncedSearch = useDebounce(search, FILTER_VALUE_SEARCH_DEBOUNCE_MS);
  const {
    options,
    isLoading,
    isError,
    fetchNextPage,
    hasNextPage,
    continuationKey,
    isFetchingNextPage,
    isFetchNextPageError,
    queryReadState,
    cursorChainStopped,
    retryFreshPage,
    isRetryingFreshPage,
    refetch,
  } = useResolvedFilterOptions(
    filter,
    source,
    !exactSearchValueTooLong,
    exactSearchValueTooLong ? "" : debouncedSearch,
    exactSearchValueTooLong ? "" : search,
  );
  const retryValues = retryFreshPage || refetch;
  const readMessage = exactSearchValueTooLong
    ? `Enter a value no longer than ${exactValueMaxUtf8Bytes.toLocaleString(
        "en-US",
      )} UTF-8 bytes.`
    : getFilterValueReadMessage(isError ? "error" : queryReadState);

  const filteredOptions = useMemo(() => {
    if (exactSearchValueTooLong) return [];
    if (!search) return options;
    const q = search.toLowerCase();
    return options.filter((option) =>
      [option?.label, option?.value, option?.description].some((candidate) =>
        String(candidate ?? "")
          .toLowerCase()
          .includes(q),
      ),
    );
  }, [exactSearchValueTooLong, options, search]);
  const searchMatchesLoadedOption = options.some((option) =>
    [option?.value, option?.label].some(
      (candidate) =>
        String(candidate ?? "")
          .trim()
          .toLocaleLowerCase() === exactSearchValue.toLocaleLowerCase(),
    ),
  );
  const canSpecifyExactValue = Boolean(
    exactSearchValue && !exactSearchValueTooLong && !searchMatchesLoadedOption,
  );

  const toggleValue = (value, valueType) => {
    const identity = selectedIdentity(value, valueType);
    setSelectedEntries((previous) =>
      previous.some(
        (entry) => selectedIdentity(entry.value, entry.type) === identity,
      )
        ? previous.filter(
            (entry) => selectedIdentity(entry.value, entry.type) !== identity,
          )
        : [...previous, { value, type: valueType }],
    );
  };

  const toggleAll = () => {
    const entries = filteredOptions.map((option) => ({
      value: option.value,
      type: optionStorageType(option),
    }));
    const visibleIdentities = new Set(
      entries.map((entry) => selectedIdentity(entry.value, entry.type)),
    );
    setSelectedEntries((previous) => {
      const selectedIdentities = new Set(
        previous.map((entry) => selectedIdentity(entry.value, entry.type)),
      );
      const allVisibleSelected =
        entries.length > 0 &&
        entries.every((entry) =>
          selectedIdentities.has(selectedIdentity(entry.value, entry.type)),
        );
      if (allVisibleSelected) {
        return previous.filter(
          (entry) =>
            !visibleIdentities.has(selectedIdentity(entry.value, entry.type)),
        );
      }
      return [
        ...previous,
        ...entries.filter(
          (entry) =>
            !selectedIdentities.has(selectedIdentity(entry.value, entry.type)),
        ),
      ];
    });
  };

  const exactValueType = ["string", "number", "boolean"].includes(
    filter?.dataType,
  )
    ? filter.dataType
    : "string";
  const allFilteredSelected =
    filteredOptions.length > 0 &&
    filteredOptions.every((option) =>
      isSelected(option.value, optionStorageType(option)),
    );
  const someFilteredSelected = filteredOptions.some((option) =>
    isSelected(option.value, optionStorageType(option)),
  );

  return (
    <Popper
      open={Boolean(anchorEl)}
      anchorEl={anchorEl}
      placement="bottom-start"
      sx={{ zIndex: 1400 }}
    >
      <ClickAwayListener onClickAway={onClose}>
        <Paper
          elevation={8}
          sx={{
            width: 300,
            display: "flex",
            flexDirection: "column",
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: 1,
            bgcolor: "background.paper",
          }}
        >
          {/* Search */}
          <Box sx={{ p: 1.5 }}>
            <TextField
              size="small"
              fullWidth
              placeholder="Search..."
              value={search}
              onChange={(e) =>
                setSearch(
                  String(e.target.value ?? "").slice(
                    0,
                    exactValueMaxUtf8Bytes + 1,
                  ),
                )
              }
              autoFocus
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <Iconify
                      icon="eva:search-fill"
                      width={18}
                      sx={{ color: "text.disabled" }}
                    />
                  </InputAdornment>
                ),
              }}
            />
          </Box>
          <Divider />

          {readMessage && (
            <Stack
              role="status"
              direction="row"
              alignItems="center"
              justifyContent="space-between"
              spacing={1}
              sx={{ px: 1.5, py: 1 }}
            >
              <Typography variant="caption" color="warning.main">
                {readMessage}
              </Typography>
              {cursorChainStopped && (
                <Button
                  size="small"
                  disabled={isRetryingFreshPage}
                  onClick={() =>
                    void Promise.resolve(retryValues?.()).catch(() => {})
                  }
                >
                  {isRetryingFreshPage ? "Retrying…" : "Retry"}
                </Button>
              )}
            </Stack>
          )}

          {/* Select all */}
          <Box
            onClick={toggleAll}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              px: 2,
              py: 1,
              cursor: "pointer",
              bgcolor: someFilteredSelected ? "action.selected" : "transparent",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Checkbox
              size="small"
              checked={allFilteredSelected}
              indeterminate={someFilteredSelected && !allFilteredSelected}
              sx={{ p: 0 }}
            />
            <Typography variant="body2" fontWeight={600} color="primary.main">
              Select all in list ({filteredOptions.length})
            </Typography>
          </Box>
          <Divider />

          {/* Options list */}
          <Box
            ref={valueOptionsListRef}
            sx={{ flex: 1, overflow: "auto", maxHeight: 240 }}
          >
            {canSpecifyExactValue && (
              <Box
                data-widget-filter-exact-value={exactSearchValue}
                onClick={() => toggleValue(exactSearchValue, exactValueType)}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 1,
                  px: 2,
                  py: 0.75,
                  cursor: "pointer",
                  bgcolor: isSelected(exactSearchValue, exactValueType)
                    ? "action.selected"
                    : "transparent",
                  "&:hover": { bgcolor: "action.hover" },
                }}
              >
                <Checkbox
                  size="small"
                  checked={isSelected(exactSearchValue, exactValueType)}
                  sx={{ p: 0 }}
                />
                <Typography
                  variant="body2"
                  noWrap
                  title={`Specify: ${exactSearchValue}`}
                  sx={{ minWidth: 0, fontSize: "13px" }}
                >
                  Specify: <strong>{exactSearchValue}</strong>
                </Typography>
              </Box>
            )}
            {isLoading ? (
              <Box sx={{ p: 2, textAlign: "center" }}>
                <CircularProgress size={20} />
              </Box>
            ) : isError && options.length === 0 ? (
              <Stack sx={{ p: 2 }} spacing={1} alignItems="center">
                <Typography variant="body2" color="warning.main">
                  Values could not be loaded. Retry this exact page.
                </Typography>
                <Button
                  size="small"
                  disabled={isRetryingFreshPage}
                  onClick={() =>
                    void Promise.resolve(retryValues?.()).catch(() => {})
                  }
                >
                  {isRetryingFreshPage ? "Retrying…" : "Retry"}
                </Button>
              </Stack>
            ) : filteredOptions.length === 0 && !canSpecifyExactValue ? (
              <Box sx={{ p: 2, textAlign: "center" }}>
                <Typography variant="body2" color="text.disabled">
                  No values found
                </Typography>
              </Box>
            ) : (
              filteredOptions.map((opt) => (
                <Box
                  key={`${opt.type || ""}:${typeof opt.value}:${JSON.stringify(opt.value)}`}
                  onClick={() => toggleValue(opt.value, optionStorageType(opt))}
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    px: 2,
                    py: 0.75,
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.hover" },
                    bgcolor: isSelected(opt.value, optionStorageType(opt))
                      ? "action.selected"
                      : "transparent",
                  }}
                >
                  <Checkbox
                    size="small"
                    checked={isSelected(opt.value, optionStorageType(opt))}
                    sx={{ p: 0 }}
                  />
                  <Typography
                    variant="body2"
                    noWrap
                    title={String(opt.label ?? opt.value ?? "")}
                    sx={{ minWidth: 0, fontSize: "13px" }}
                  >
                    {opt.label || opt.value}
                  </Typography>
                </Box>
              ))
            )}
            <BoundedCursorPaginationControl
              key={`${filterValuePickerScopeKey(filter, source)}:${debouncedSearch}`}
              rootRef={valueOptionsListRef}
              testId="widget-filter-value-pagination-sentinel"
              loadingLabel="Loading more values…"
              retryLabel="Retry next page"
              errorMessage="The next page failed. Loaded values remain available."
              channels={[
                {
                  channelKey: "widget-filter-values",
                  hasNextPage: Boolean(hasNextPage),
                  continuationKey,
                  isFetching: isFetchingNextPage,
                  error: isFetchNextPageError,
                  loadNextPage: fetchNextPage,
                },
              ]}
            />
          </Box>

          {/* Add button */}
          <Box sx={{ p: 1.5, borderTop: `1px solid ${theme.palette.divider}` }}>
            <Button
              fullWidth
              variant="contained"
              onClick={() =>
                onApply(
                  selectedEntries.map(({ value }) => value),
                  selectedEntries.map(({ type }) => type),
                )
              }
              disabled={selectedEntries.length === 0}
            >
              Add
            </Button>
          </Box>
        </Paper>
      </ClickAwayListener>
    </Popper>
  );
}

FilterValuePickerPopup.propTypes = {
  anchorEl: PropTypes.object,
  filter: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onApply: PropTypes.func.isRequired,
  source: PropTypes.string.isRequired,
};

export default function WidgetEditorView() {
  const theme = useTheme();
  const navigate = useNavigate();
  const { dashboardId, widgetId } = useParams();
  const { currentWorkspaceId } = useWorkspace();
  const [searchParams] = useSearchParams();
  const { enqueueSnackbar } = useSnackbar();
  const [createdWidgetId, setCreatedWidgetId] = useState(null);
  const effectiveWidgetId = createdWidgetId || widgetId;
  const isEditing = effectiveWidgetId && effectiveWidgetId !== "new";
  const isRouteEditing = Boolean(widgetId && widgetId !== "new");

  const { canDelete, isReadOnly } = useCanEditDashboard();

  const {
    data: dashboard,
    isLoading: isDashboardLoading,
    isError: isDashboardError,
    refetch: refetchDashboard,
  } = useDashboardDetail(dashboardId);
  const createMutation = useCreateWidget();
  const updateMutation = useUpdateWidget();
  const deleteMutation = useDeleteWidget();
  const queryMutation = useDashboardQuery();
  const mutateDashboardQuery = queryMutation.mutate;
  const resetDashboardQuery = queryMutation.reset;
  const { data: simulationAgents = [] } = useSimulationAgents();
  const [lastExactPreview, setLastExactPreview] = useState(null);
  const currentPreviewSignatureRef = useRef("");
  const previewPollTimerRef = useRef(null);
  const previewRequestControllerRef = useRef(null);
  const previewRequestTimerRef = useRef(null);
  const previewGenerationRef = useRef(0);
  const [isPreviewRefreshing, setIsPreviewRefreshing] = useState(false);
  const [previewFailed, setPreviewFailed] = useState(false);

  // Build a map: agent_definition_id → observability project for cross-source correlation
  const simAgentObsMap = useMemo(() => {
    const map = {};
    simulationAgents.forEach((a) => {
      if (a.observabilityProjectId) {
        map[a.id] = {
          projectId: a.observabilityProjectId,
          projectName: a.observabilityProjectName,
          agentName: a.name,
        };
      }
    });
    return map;
  }, [simulationAgents]);

  // Form state
  const [chartName, setChartName] = useState("");
  const [chartDescription, setChartDescription] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [descOpen, setDescOpen] = useState(false);
  const descSlotRef = useRef(null);
  const trimmedDescription = (chartDescription || "").trim();
  const [moreMenuAnchor, setMoreMenuAnchor] = useState(null);
  const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);

  const incomingTimePreset = searchParams.get("timePreset");
  const dashboardDetailUrl = `${paths.dashboard.dashboards.detail(dashboardId)}${incomingTimePreset ? `?timePreset=${incomingTimePreset}` : ""}`;

  const confirmDeleteWidget = () => {
    if (!isEditing) {
      setConfirmDeleteOpen(false);
      return;
    }
    deleteMutation.mutate(
      { dashboardId, widgetId: effectiveWidgetId },
      {
        onSuccess: () => {
          enqueueSnackbar("Widget deleted", { variant: "success" });
          navigate(dashboardDetailUrl);
        },
        // Close once the request settles, not before it starts.
        onSettled: () => setConfirmDeleteOpen(false),
      },
    );
  };

  const [timePreset, setTimePreset] = useState(incomingTimePreset || "30D");
  const [granularity, setGranularity] = useState("day");
  const [chartType, setChartType] = useState("line");
  const [metrics, setMetrics] = useState([]);
  const [filters, setFilters] = useState([]);
  const [breakdowns, setBreakdowns] = useState([]);
  const [rightTab, setRightTab] = useState(0);
  // Shared picker state: used for metric, filter, and breakdown pickers
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerCatalogSession, setPickerCatalogSession] = useState(0);
  const [pickerAnchor, setPickerAnchor] = useState(null);
  const [pickerCategory, setPickerCategory] = useState("all");
  const [pickerSearch, setPickerSearch] = useState("");
  const [debouncedPickerSearch, setDebouncedPickerSearch] = useState("");
  // pickerMode: "metric" | "filter" | "breakdown" | "metric_filter"
  const [pickerMode, setPickerMode] = useState("metric");
  // For filter/breakdown: which index we're editing (null = adding new)
  const [pickerTargetIndex, setPickerTargetIndex] = useState(null);
  // For metric_filter picker: which metric index we're adding a filter to
  const [pickerMetricIndex, setPickerMetricIndex] = useState(null);
  // Per-metric filter value picker state
  const [mfValueAnchor, setMfValueAnchor] = useState(null);
  const [mfValueTarget, setMfValueTarget] = useState(null); // { metricIdx, filterIdx }
  const mfValueRefs = useRef({});
  const [initialized, setInitialized] = useState(false);
  // "chart" = chart only, "split-chart" = chart bigger, "split-table" = table bigger, "table" = table only
  const [viewMode, setViewMode] = useState("split-chart");
  const [chartHeight, setChartHeight] = useState(300);
  const [isDragging, setIsDragging] = useState(false);
  const [tableSearch, setTableSearch] = useState("");
  const [visibleSeries, setVisibleSeries] = useState(null); // null = all visible, Set = selected indices
  // Saved selection keys, applied once previewSeries loads.
  const pendingVisibleSeriesRef = useRef(undefined);
  const [isDatePickerOpen, setIsDatePickerOpen] = useState(false);
  const [customDateRange, setCustomDateRange] = useState(null); // [startDate, endDate]
  const customDateAnchorRef = useRef(null);
  const lineChartRef = useRef(null);
  const saveNavTimerRef = useRef(null);
  useEffect(() => () => clearTimeout(saveNavTimerRef.current), []);

  // Auto-set granularity when time preset changes
  const customDays =
    customDateRange && customDateRange[0] && customDateRange[1]
      ? Math.ceil(
          (customDateRange[1] - customDateRange[0]) / (1000 * 60 * 60 * 24),
        )
      : null;
  const allowedGranularities = useMemo(
    () => getAllowedGranularities(timePreset, customDays),
    [timePreset, customDays],
  );

  useEffect(() => {
    if (timePreset === "custom") {
      // For custom, pick a sensible default based on range
      if (customDays != null) {
        const def =
          customDays <= 1
            ? "hour"
            : customDays <= 7
              ? "day"
              : customDays <= 90
                ? "day"
                : "month";
        setGranularity((prev) => {
          const allowed = getAllowedGranularities("custom", customDays);
          if (allowed.some((g) => g.value === prev)) return prev;
          return def;
        });
      }
    } else {
      const def = PRESET_DEFAULT_GRANULARITY[timePreset] || "day";
      setGranularity(def);
    }
  }, [timePreset, customDays]);

  // Filter value picker state
  const [filterValueAnchor, setFilterValueAnchor] = useState(null);
  const [filterValueIndex, setFilterValueIndex] = useState(null); // which filter is open
  const [_filterValueSearch, setFilterValueSearch] = useState("");
  const [pendingFilterOpen, setPendingFilterOpen] = useState(null); // index of filter to auto-open value picker
  const filterValueRefs = useRef({}); // refs to "Select value..." elements

  // Auto-open value picker for newly added filter
  useEffect(() => {
    if (pendingFilterOpen == null) return;
    const timer = setTimeout(() => {
      const el = filterValueRefs.current[pendingFilterOpen];
      if (el) {
        setFilterValueAnchor(el);
        setFilterValueIndex(pendingFilterOpen);
        setFilterValueSearch("");
      }
      setPendingFilterOpen(null);
    }, 150);
    return () => clearTimeout(timer);
  }, [pendingFilterOpen]);

  // Debounce picker search for server-side filtering
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedPickerSearch(pickerSearch);
    }, 300);
    return () => clearTimeout(timer);
  }, [pickerSearch]);

  const preservedDashboardAttributeKeys = useMemo(() => {
    const keys = new Set();
    const append = (option) => {
      if (option?.type === "custom_attribute" && option.id) {
        keys.add(option.id);
      }
    };
    metrics.forEach((metric) => {
      append(metric);
      (metric.filters || []).forEach(append);
    });
    filters.forEach(append);
    breakdowns.forEach(append);
    return [...keys];
  }, [breakdowns, filters, metrics]);

  const trimmedPickerSearch = pickerSearch.trim();
  const trimmedDebouncedPickerSearch = debouncedPickerSearch.trim();
  const pickerSearchSettled =
    trimmedPickerSearch === trimmedDebouncedPickerSearch;
  const baseWidgetCatalogRequest = useMemo(
    () =>
      getWidgetMetricCatalogRequest({
        pickerCategory: "all",
        search: "",
        pickerOpen,
        pickerMode,
      }),
    [pickerOpen, pickerMode],
  );
  const scopedWidgetCatalogRequest = useMemo(
    () =>
      getWidgetMetricCatalogRequest({
        pickerCategory,
        search: trimmedDebouncedPickerSearch,
        pickerOpen,
        pickerMode,
      }),
    [pickerCategory, trimmedDebouncedPickerSearch, pickerOpen, pickerMode],
  );
  const allSearchWidgetCatalogRequest = useMemo(
    () =>
      getWidgetMetricCatalogRequest({
        pickerCategory: "all",
        search: trimmedDebouncedPickerSearch,
        pickerOpen,
        pickerMode,
      }),
    [trimmedDebouncedPickerSearch, pickerOpen, pickerMode],
  );
  const scopedWidgetCatalogActive = Boolean(
    pickerCategory !== "all" || trimmedDebouncedPickerSearch,
  );

  // Keep the unsearched All lane mounted as the stable exact-count baseline.
  // Search/category requests use an independent cache lane; once settled,
  // only that lane may own rows and cursor state.
  const basePropertyCatalog = usePropertyCatalog({
    category: baseWidgetCatalogRequest.category,
    source: baseWidgetCatalogRequest.source,
    search: baseWidgetCatalogRequest.search,
    role: baseWidgetCatalogRequest.role,
    pageSize: baseWidgetCatalogRequest.pageSize,
    enabled: baseWidgetCatalogRequest.enabled,
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `widget-property-catalog:${currentWorkspaceId || ""}:base`,
    cacheScopeKey: `widget-picker:${pickerCatalogSession}:base`,
  });
  const scopedPropertyCatalog = usePropertyCatalog({
    category: scopedWidgetCatalogRequest.category,
    source: scopedWidgetCatalogRequest.source,
    search: scopedWidgetCatalogRequest.search,
    role: scopedWidgetCatalogRequest.role,
    pageSize: scopedWidgetCatalogRequest.pageSize,
    enabled: scopedWidgetCatalogRequest.enabled && scopedWidgetCatalogActive,
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `widget-property-catalog:${currentWorkspaceId || ""}:scoped`,
    cacheScopeKey: `widget-picker:${pickerCatalogSession}:scoped`,
  });
  // Keep search-wide counts mounted independently from the selected category.
  // While All is selected this shares the scoped query key and is deduplicated;
  // after a category click it prevents scoped counts from replacing the All
  // breakdown shown in the sidebar.
  const allSearchPropertyCatalog = usePropertyCatalog({
    category: allSearchWidgetCatalogRequest.category,
    source: allSearchWidgetCatalogRequest.source,
    search: allSearchWidgetCatalogRequest.search,
    role: allSearchWidgetCatalogRequest.role,
    pageSize: allSearchWidgetCatalogRequest.pageSize,
    enabled: Boolean(
      allSearchWidgetCatalogRequest.enabled && trimmedDebouncedPickerSearch,
    ),
    allowLegacyNotReadyFallback: true,
    fallbackScopeKey: `widget-property-catalog:${currentWorkspaceId || ""}:all-search`,
    cacheScopeKey: `widget-picker:${pickerCatalogSession}:scoped`,
  });
  const baseLegacyMetricCatalog = useLegacyDashboardMetricsPaginated({
    ...baseWidgetCatalogRequest,
    enabled:
      baseWidgetCatalogRequest.enabled &&
      basePropertyCatalog.legacyFallbackRequired,
  });
  const scopedLegacyMetricCatalog = useLegacyDashboardMetricsPaginated({
    ...scopedWidgetCatalogRequest,
    enabled:
      scopedWidgetCatalogRequest.enabled &&
      scopedWidgetCatalogActive &&
      scopedPropertyCatalog.legacyFallbackRequired,
  });
  const baseUsesLegacyCatalog = basePropertyCatalog.legacyFallbackRequired;
  const scopedUsesLegacyCatalog = scopedPropertyCatalog.legacyFallbackRequired;
  const scopedRequestOwnsResults = Boolean(
    scopedWidgetCatalogActive && pickerSearchSettled,
  );
  const activeCatalogRequest = scopedRequestOwnsResults
    ? scopedWidgetCatalogRequest
    : baseWidgetCatalogRequest;
  const activePropertyCatalog = scopedRequestOwnsResults
    ? scopedPropertyCatalog
    : basePropertyCatalog;
  const activeLegacyMetricCatalog = scopedRequestOwnsResults
    ? scopedLegacyMetricCatalog
    : baseLegacyMetricCatalog;
  const activeUsesLegacyCatalog = scopedRequestOwnsResults
    ? scopedUsesLegacyCatalog
    : baseUsesLegacyCatalog;
  // A typed catalog-not-ready response is the only compatibility signal that
  // activates the retained workspace attribute endpoint. Its query remains
  // cursor-bounded and is disabled for ordinary catalog/network failures.
  const cursorAttributePickerActive = Boolean(
    pickerOpen && activeUsesLegacyCatalog,
  );
  const {
    filteredAttributes: cursorAttributes,
    inventoryControlProps: cursorAttributeControlProps,
  } = useLegacyCursorAttributeInventory({
    workspaceScope: true,
    workspaceScopeKey: currentWorkspaceId,
    rowType: "spans",
    discoveryMode: "filter",
    search: pickerSearch,
    preservedKeys: preservedDashboardAttributeKeys,
    enabled: cursorAttributePickerActive,
    pageSize: PROPERTY_CATALOG_PAGE_SIZE,
  });
  const activeRequestSettled = Boolean(
    pickerSearchSettled &&
      !activePropertyCatalog.isPlaceholderData &&
      (!activeUsesLegacyCatalog ||
        !activeLegacyMetricCatalog.isPlaceholderData),
  );
  const baseMetrics = baseUsesLegacyCatalog
    ? baseLegacyMetricCatalog.metrics
    : basePropertyCatalog.metrics;
  const scopedMetrics = scopedUsesLegacyCatalog
    ? scopedLegacyMetricCatalog.metrics
    : scopedPropertyCatalog.metrics;
  const paginatedMetrics = resolveWidgetCatalogResultMetrics({
    baseMetrics,
    scopedMetrics,
    scopedRequestActive: scopedRequestOwnsResults,
    requestSettled: activeRequestSettled,
  });
  const activeCategoryCounts = activeUsesLegacyCatalog
    ? null
    : activePropertyCatalog.categoryCounts;
  const activeCategoryCountsExact = Boolean(
    !activeUsesLegacyCatalog && activePropertyCatalog.categoryCountsExact,
  );
  const pickerSidebarCategoryCounts = resolveWidgetCatalogSidebarCounts({
    requestSettled: activeRequestSettled,
    search: trimmedDebouncedPickerSearch,
    baseCategoryCounts: basePropertyCatalog.categoryCounts,
    baseCategoryCountsExact: basePropertyCatalog.categoryCountsExact,
    allSearchCategoryCounts: allSearchPropertyCatalog.categoryCounts,
    allSearchCategoryCountsExact: allSearchPropertyCatalog.categoryCountsExact,
  });
  const pickerSidebarCategoryCountsExact = Boolean(pickerSidebarCategoryCounts);
  const activeCatalogLoading = isWidgetCatalogInventoryLoading({
    requestSettled: activeRequestSettled,
    usesLegacyCatalog: activeUsesLegacyCatalog,
    legacyCatalogLoading: activeLegacyMetricCatalog.isLoading,
    propertyCatalogLoading: activePropertyCatalog.isLoading,
    propertyCatalogSearchPending:
      activePropertyCatalog.isRemoteCatalogSearchPending,
    propertyCatalogNotReady: isPropertyCatalogNotReadyError(
      activePropertyCatalog.error,
    ),
  });
  const paginatedTotal =
    !activeRequestSettled || activeCatalogLoading
      ? null
      : activeUsesLegacyCatalog
        ? activeLegacyMetricCatalog.total
        : getWidgetCatalogExactResultCount({
            request: activeCatalogRequest,
            categoryCounts: activeCategoryCounts,
            categoryCountsExact: activeCategoryCountsExact,
            requestSettled: activeRequestSettled,
          });
  const fetchNextPage = activeUsesLegacyCatalog
    ? activeLegacyMetricCatalog.fetchNextPage
    : activePropertyCatalog.fetchNextPage;
  const hasNextPage = activeRequestSettled
    ? activeUsesLegacyCatalog
      ? activeLegacyMetricCatalog.hasNextPage
      : Boolean(activePropertyCatalog.hasNextPage)
    : false;
  const catalogContinuationKey = activeRequestSettled
    ? activeUsesLegacyCatalog
      ? activeLegacyMetricCatalog.continuationKey
      : activePropertyCatalog.continuationKey
    : null;
  const isFetchingNextPage = Boolean(
    activeRequestSettled &&
      (activeUsesLegacyCatalog
        ? activeLegacyMetricCatalog.isFetchingNextPage
        : activePropertyCatalog.isFetchingNextPage),
  );
  const isFetchNextPageError = Boolean(
    activeRequestSettled &&
      (activeUsesLegacyCatalog
        ? activeLegacyMetricCatalog.isFetchNextPageError
        : activePropertyCatalog.isFetchNextPageError),
  );
  const isPaginatedLoading = Boolean(
    !activeRequestSettled || activeCatalogLoading,
  );

  const selectedMetricSources = useMemo(
    () => metrics.map(getWidgetMetricAdapterSource),
    [metrics],
  );
  const targetMetricSource =
    pickerMetricIndex == null
      ? null
      : getWidgetMetricAdapterSource(metrics[pickerMetricIndex] || {});
  const catalogMetricOptions = useMemo(
    () =>
      buildWidgetCatalogPickerOptions({
        metrics: paginatedMetrics,
        pickerMode,
        pickerCategory,
        search: trimmedDebouncedPickerSearch,
        requestSettled: activeRequestSettled,
        selectedMetricSources,
        targetMetricSource,
      }),
    [
      activeRequestSettled,
      paginatedMetrics,
      pickerCategory,
      pickerMode,
      selectedMetricSources,
      targetMetricSource,
      trimmedDebouncedPickerSearch,
    ],
  );

  const cursorAttributeOptions = useMemo(
    () => buildWidgetCursorAttributeOptions(cursorAttributes, pickerMode),
    [cursorAttributes, pickerMode],
  );

  const pickerMetricOptions = useMemo(
    () =>
      mergeWidgetCursorAttributeOptions(
        catalogMetricOptions,
        cursorAttributeOptions,
        cursorAttributePickerActive,
      ),
    [catalogMetricOptions, cursorAttributeOptions, cursorAttributePickerActive],
  );

  const isPickerInventoryLoading = isPaginatedLoading;

  // Chart tab — axis config
  const [axisConfig, setAxisConfig] = useState({
    leftY: {
      visible: true,
      label: "",
      unit: "",
      prefixSuffix: "prefix",
      abbreviation: true,
      decimals: DEFAULT_DECIMALS,
      min: "",
      max: "",
      outOfBounds: "visible",
      scale: "linear",
    },
    rightY: {
      visible: false,
      label: "",
      unit: "",
      prefixSuffix: "prefix",
      abbreviation: true,
      decimals: DEFAULT_DECIMALS,
      min: "",
      max: "",
      outOfBounds: "hidden",
      scale: "linear",
    },
    xAxis: { visible: true, label: "" },
    seriesAxis: {}, // { [seriesIndex]: "left" | "right" }
  });
  // Tracks the unit value we last auto-applied to ``axisConfig.leftY``.
  // Stored (rather than a single boolean) so we can tell the difference
  // between "user picked this manually" and "we set it on their behalf
  // when the metric set last changed". When the suggested unit changes
  // — e.g. swapping a ``duration`` (s) metric for an annotation (no
  // unit) — we reconcile the axis only if the current axis unit is the
  // one we last auto-applied; user-chosen units are never overwritten.
  const [autoAppliedLeftAxisUnit, setAutoAppliedLeftAxisUnit] = useState(null);
  const updateAxis = (axis, key, val) =>
    setAxisConfig((prev) => ({
      ...prev,
      [axis]: { ...prev[axis], [key]: val },
    }));
  const setSeriesAxis = (si, side) =>
    setAxisConfig((prev) => {
      const newSeriesAxis = { ...prev.seriesAxis, [si]: side };
      const hasAnyRight = Object.values(newSeriesAxis).some(
        (s) => s === "right",
      );
      return {
        ...prev,
        seriesAxis: newSeriesAxis,
        rightY: { ...prev.rightY, visible: hasAnyRight },
      };
    });

  // Initialize from existing widget when editing
  useEffect(() => {
    if (initialized) return;
    if (isEditing && dashboard?.widgets) {
      const widget = dashboard.widgets.find((w) => w.id === widgetId);
      if (widget) {
        setChartName(widget.name || "");
        setChartDescription(widget.description || "");
        const qc = widget.queryConfig || widget.query_config || {};
        const cc = widget.chartConfig || widget.chart_config || {};
        // undefined = nothing to restore; null = all visible; array = saved keys.
        pendingVisibleSeriesRef.current = cc.visible_series;
        const { timePreset: initialPreset, customDateRange: initialRange } =
          resolveInitialTimeRange(
            qc.timeRange || qc.time_range,
            searchParams.get("timePreset"),
          );
        setTimePreset(initialPreset);
        if (initialRange) setCustomDateRange(initialRange);
        setGranularity(qc.granularity || "day");
        setChartType(cc.chartType || cc.chart_type || "line");
        // Restore axis config if saved
        const savedAxis = cc.axis_config;
        if (savedAxis) {
          const restoredAxis = fromAxisConfigPayload(savedAxis);
          setAxisConfig((prev) => ({
            leftY: { ...prev.leftY, ...restoredAxis.leftY },
            rightY: { ...prev.rightY, ...restoredAxis.rightY },
            xAxis: { ...prev.xAxis, ...restoredAxis.xAxis },
            seriesAxis: restoredAxis.seriesAxis,
          }));
        }
        // Restore metrics with frontend type keys + source
        const savedMetrics = (qc.metrics || []).map((m) => {
          const frontendType = toDashboardFilterType(m.type);
          // Infer source from old workflow field if metric lacks source
          const source =
            m.source ||
            (qc.workflow === "simulation"
              ? "simulation"
              : qc.workflow === "dataset"
                ? "datasets"
                : "traces");
          // Restore per-metric filters from saved backend format
          const restoredFilters = (m.filters || []).map((f) =>
            restoreFilterPayload(f, source),
          );
          return {
            ...m,
            id: m.name || m.id,
            registryId: m.property_id || m.propertyId,
            name: m.displayName || m.display_name || m.name || m.id,
            type: frontendType,
            dataType: m.dataType || m.data_type || m.attribute_type || "number",
            source,
            filters: restoredFilters,
          };
        });
        setMetrics(savedMetrics);
        setFilters(
          (qc.filters || []).map((f) =>
            restoreFilterPayload(
              f,
              qc.workflow === "simulation"
                ? "simulation"
                : qc.workflow === "dataset"
                  ? "datasets"
                  : "traces",
            ),
          ),
        );
        // Restore breakdowns — saved format uses "name" as the key,
        // but the frontend picker/filter logic expects "id".
        const bdTypeMap = {
          system_metric: "system",
          systemMetric: "system",
          annotation_metric: "annotation",
          annotationMetric: "annotation",
          custom_column: "custom_column",
          customColumn: "custom_column",
          custom_attribute: "custom_attribute",
          customAttribute: "custom_attribute",
          eval_metric: "eval_metric",
          evalMetric: "eval_metric",
        };
        const savedBreakdowns = (qc.breakdowns || []).map((b) => ({
          ...b,
          id: b.id || b.name,
          registryId: b.property_id || b.propertyId,
          name: b.displayName || b.display_name || b.name || b.id,
          type: bdTypeMap[b.type] || b.type || "system",
          dataType: b.dataType || b.data_type || b.attribute_type || "string",
          source:
            b.source ||
            (qc.workflow === "simulation"
              ? "simulation"
              : qc.workflow === "dataset"
                ? "datasets"
                : "traces"),
        }));
        setBreakdowns(savedBreakdowns);
        setInitialized(true);
      }
    }
  }, [isEditing, dashboard, widgetId, initialized]);

  // Map frontend type keys to backend metric_type values
  const toBackendType = (type) => {
    const map = {
      system: "system_metric",
      eval_metric: "eval_metric",
      annotation: "annotation_metric",
      custom_attribute: "custom_attribute",
      custom_column: "custom_column",
    };
    return map[type] || type;
  };

  const toDashboardFilterType = (backendType) => {
    const map = {
      system_metric: "system",
      eval_metric: "eval_metric",
      annotation_metric: "annotation",
      custom_attribute: "custom_attribute",
      custom_column: "custom_column",
    };
    return map[backendType] || backendType || "system";
  };

  const buildFilterPayload = (f) => {
    const filterConfig = buildWidgetFilterConfig(f);
    if (!filterConfig) return null;

    return {
      column_id: f.id,
      ...(f.registryId && { property_id: f.registryId }),
      ...(f.name && { display_name: f.name }),
      ...(f.source && { source: f.source }),
      ...(f.outputType && { output_type: f.outputType }),
      filter_config: filterConfig,
    };
  };

  const restoreFilterPayload = (f, fallbackSource = "traces") => {
    const config = f?.filter_config;
    if (!config) {
      const backendType =
        f.metric_type || f.metricType || f.type || "system_metric";
      return {
        id: f.metric_name || f.metricName || f.id,
        registryId: f.property_id || f.propertyId,
        name: f.metric_name || f.metricName || f.name || f.id || "",
        type: toDashboardFilterType(backendType),
        dataType: normalizeWidgetDashboardDataType(
          f.dataType || f.data_type || "string",
        ),
        source: f.source || fallbackSource,
        outputType: f.output_type || f.outputType,
        operator:
          f.operator === "is_numeric"
            ? "is_set"
            : f.operator === "is_not_numeric"
              ? "is_not_set"
              : f.operator || "contains",
        value: f.value ?? [],
      };
    }

    const colType = normalizeColumnType(config.col_type);
    const dashboardType =
      COL_TYPE_TO_DASHBOARD_TYPE[colType] || toDashboardFilterType(f.type);

    return {
      id: f.column_id,
      registryId: f.property_id || f.propertyId,
      name: f.display_name || f.column_id,
      type: dashboardType,
      source: f.source || fallbackSource,
      outputType: f.output_type,
      ...restoreWidgetFilterConfig(config),
    };
  };

  const buildMetricPayload = (m, i) => {
    const backendType = toBackendType(m.type);
    const aggregation =
      m.allowedAggregations?.length &&
      !m.allowedAggregations.includes(m.aggregation)
        ? m.allowedAggregations[0]
        : m.aggregation || "avg";
    const base = {
      id: m.id || `m${i}`,
      name: m.id,
      ...(m.registryId && { property_id: m.registryId }),
      display_name: m.name || m.id,
      type: backendType,
      source: m.source || "traces",
      aggregation,
    };
    if (backendType === "eval_metric") {
      // m.id is eval_template_id from the metrics endpoint
      if (m.outputType) base.output_type = m.outputType;
      if (m.evalKey) base.eval_key = m.evalKey;
    } else if (backendType === "annotation_metric") {
      base.name = m.name || m.id; // Use display name for annotations (label_id is the identifier)
      base.label_id = m.id;
      if (m.outputType) base.output_type = m.outputType;
    } else if (backendType === "custom_attribute") {
      base.attribute_key = m.id;
      // Preserve the typed-Map family returned by the metric catalog. Omitting
      // this field made the API default numeric attributes (for example
      // call.total_turns) to string and reject avg/percentile queries before
      // ClickHouse was reached.
      base.attribute_type = normalizeWidgetDashboardDataType(
        m.dataType || m.data_type || "string",
      );
    } else if (backendType === "custom_column") {
      base.column_id = m.id;
      if (m.columnDataType) base.data_type = m.columnDataType;
    }
    // Per-metric filters
    if (m.filters && m.filters.length > 0) {
      base.filters = m.filters
        .filter(hasWidgetFilterValue)
        .map((f) => buildFilterPayload(f))
        .filter(Boolean);
    }
    return base;
  };

  const buildBreakdownPayload = (b) => {
    const backendType = toBackendType(b.type);
    // b.id = backend key
    const base = {
      name: b.id,
      ...(b.registryId && { property_id: b.registryId }),
      display_name: b.name || b.id,
      type: backendType,
      source: b.source || "traces",
    };
    if (backendType === "custom_attribute") {
      base.attribute_type = normalizeWidgetDashboardDataType(
        b.dataType || b.data_type || "string",
      );
    }
    if (backendType === "annotation_metric") {
      base.label_id = b.id;
      if (b.outputType) base.output_type = b.outputType;
    }
    if (backendType === "eval_metric") {
      // b.id is eval_template_id
      if (b.outputType) base.output_type = b.outputType;
    }
    return base;
  };

  const buildQueryConfig = useCallback(() => {
    const timeRange = buildTimeRangePayload(timePreset, customDateRange);
    return {
      project_ids: [],
      time_range: timeRange,
      granularity,
      metrics: metrics.map((m, i) => buildMetricPayload(m, i)),
      filters: filters
        .filter(hasWidgetFilterValue)
        .map((f) => buildFilterPayload(f))
        .filter(Boolean),
      breakdowns: breakdowns
        .filter((b) => b.id)
        .map((b) => buildBreakdownPayload(b)),
    };
  }, [timePreset, customDateRange, granularity, metrics, filters, breakdowns]);

  const previewQueryConfig = buildQueryConfig();
  const previewQuerySignature = JSON.stringify(previewQueryConfig);
  currentPreviewSignatureRef.current = previewQuerySignature;

  useEffect(() => {
    previewGenerationRef.current += 1;
    previewRequestControllerRef.current?.abort();
    previewRequestControllerRef.current = null;
    clearTimeout(previewRequestTimerRef.current);
    previewRequestTimerRef.current = null;
    clearTimeout(previewPollTimerRef.current);
    previewPollTimerRef.current = null;
    setIsPreviewRefreshing(false);
    setPreviewFailed(false);
  }, [previewQuerySignature]);

  useEffect(
    () => () => {
      previewGenerationRef.current += 1;
      previewRequestControllerRef.current?.abort();
      previewRequestControllerRef.current = null;
      clearTimeout(previewRequestTimerRef.current);
      clearTimeout(previewPollTimerRef.current);
    },
    [],
  );

  const runPreviewQuery = useCallback(
    (queryConfig, { refresh = false } = {}) => {
      const signature = JSON.stringify(queryConfig);
      const generation = previewGenerationRef.current + 1;
      previewGenerationRef.current = generation;
      clearTimeout(previewPollTimerRef.current);
      previewPollTimerRef.current = null;
      const pollingController = createAggregationPollController();
      // Include the first preview request in the same sub-ten-second action
      // budget as any pending-response polls. Retry creates a fresh controller.
      pollingController.start();
      pollingController.recordAttempt();
      let refreshWasQueued = false;
      setPreviewFailed(false);

      const isCurrent = () =>
        previewGenerationRef.current === generation &&
        currentPreviewSignatureRef.current === signature;

      const schedulePoll = () => {
        if (!isCurrent() || previewPollTimerRef.current !== null) return;
        pollingController.start();
        const delay = pollingController.nextDelay();
        if (delay === false) {
          setIsPreviewRefreshing(false);
          setPreviewFailed(true);
          return;
        }
        previewPollTimerRef.current = window.setTimeout(() => {
          previewPollTimerRef.current = null;
          pollingController.recordAttempt();
          execute(false);
        }, delay);
      };

      const execute = (forceRefresh) => {
        previewRequestControllerRef.current?.abort();
        clearTimeout(previewRequestTimerRef.current);
        const requestController = new AbortController();
        previewRequestControllerRef.current = requestController;
        const finishAttempt = () => {
          clearTimeout(previewRequestTimerRef.current);
          previewRequestTimerRef.current = null;
          if (previewRequestControllerRef.current === requestController) {
            previewRequestControllerRef.current = null;
          }
        };
        const requestTimeoutMs = pollingController.remainingMs(
          WIDGET_PREVIEW_REQUEST_TIMEOUT_MS,
        );
        previewRequestTimerRef.current = window.setTimeout(() => {
          if (
            !isCurrent() ||
            previewRequestControllerRef.current !== requestController
          ) {
            return;
          }
          finishAttempt();
          previewGenerationRef.current = generation + 1;
          requestController.abort();
          setIsPreviewRefreshing(false);
          setPreviewFailed(true);
        }, requestTimeoutMs);
        mutateDashboardQuery(
          {
            queryConfig,
            refresh: forceRefresh,
            signal: requestController.signal,
          },
          {
            onSuccess: (response) => {
              finishAttempt();
              if (!isCurrent()) return;

              const exactResult = getExactDashboardResult(response);
              const { isRefreshing, refreshFailed } =
                getAggregationRefreshState(response);
              const readState = getExactAggregationReadState(response);
              const responsePreviewState = getWidgetPreviewState(
                response?.data?.result,
                { isSuccess: true },
              );
              pollingController.recordSuccess();
              if (exactResult) {
                setLastExactPreview({ signature, result: exactResult });
                setPreviewFailed(false);
              }
              if (
                isRefreshing &&
                !refreshFailed &&
                (exactResult || readState === "pending")
              ) {
                refreshWasQueued = true;
                setIsPreviewRefreshing(true);
                schedulePoll();
                return;
              }
              if (
                !refreshFailed &&
                !exactResult &&
                responsePreviewState === "preparing"
              ) {
                refreshWasQueued = true;
                setIsPreviewRefreshing(true);
                schedulePoll();
                return;
              }
              setIsPreviewRefreshing(false);
              if (
                !exactResult &&
                (refreshFailed ||
                  responsePreviewState === "failed" ||
                  readState !== "complete")
              ) {
                setPreviewFailed(true);
              }
            },
            onError: () => {
              finishAttempt();
              if (!isCurrent()) return;
              if (refreshWasQueued && pollingController.recordFailure()) {
                schedulePoll();
                return;
              }
              setIsPreviewRefreshing(false);
              setPreviewFailed(true);
            },
          },
        );
      };

      execute(refresh);
    },
    [mutateDashboardQuery],
  );

  // Auto-preview when config changes (debounced)
  const previewTimerRef = useRef(null);
  useEffect(() => {
    const customWithoutRange = timePreset === "custom" && !customDateRange;
    if (metrics.length > 0 && !customWithoutRange) {
      clearTimeout(previewTimerRef.current);
      previewTimerRef.current = setTimeout(() => {
        runPreviewQuery(buildQueryConfig());
      }, 400);
    } else {
      resetDashboardQuery();
    }
    return () => clearTimeout(previewTimerRef.current);
  }, [
    metrics.length,
    timePreset,
    customDateRange,
    granularity,
    metrics
      .map(
        (m) =>
          `${m.id}:${m.aggregation}:${JSON.stringify(
            (m.filters || [])
              .filter((f) => f.id && hasWidgetFilterValue(f))
              .map((f) => ({ id: f.id, op: f.operator, val: f.value })),
          )}`,
      )
      .join(","),
    JSON.stringify(
      filters
        .filter((f) => f.id && hasWidgetFilterValue(f))
        .map((f) => ({ id: f.id, op: f.operator, val: f.value })),
    ),
    JSON.stringify(breakdowns.filter((b) => b.id).map((b) => b.id)),
    runPreviewQuery,
    resetDashboardQuery,
  ]);

  const openPicker = (e, mode, targetIndex = null, metricIndex = null) => {
    setPickerCatalogSession((session) => session + 1);
    setPickerAnchor(e.currentTarget);
    setPickerOpen(true);
    setPickerCategory("all");
    setPickerSearch("");
    setDebouncedPickerSearch("");
    setPickerMode(mode);
    setPickerTargetIndex(targetIndex);
    setPickerMetricIndex(metricIndex);
  };

  const handlePickerSelect = (option) => {
    // option: { id: "latency", name: "Latency", type: "system" }
    // id = backend key, name = display name, type = frontend category key
    if (pickerMode === "metric") {
      // Determine default aggregation based on output type (dataset evals)
      let defaultAgg = "avg";
      if (option.outputType && EVAL_DEFAULT_AGGREGATIONS[option.outputType]) {
        defaultAgg = EVAL_DEFAULT_AGGREGATIONS[option.outputType];
      } else if (option.columnDataType === "boolean") {
        defaultAgg = "true_rate";
      } else if (option.type === "system" && option.id === "row_count") {
        defaultAgg = "count";
      } else if (option.source === "simulation" && option.id === "call_count") {
        defaultAgg = "count";
      }
      if (
        option.allowedAggregations?.length &&
        !option.allowedAggregations.includes(defaultAgg)
      ) {
        [defaultAgg] = option.allowedAggregations;
      }
      const newMetric = {
        ...option,
        aggregation: defaultAgg,
        source: option.source || "traces",
      };

      // Cross-source correlation: when adding a trace metric alongside
      // simulation metrics, auto-link to the simulation agent's observability project
      if (
        newMetric.source === "traces" &&
        metrics.some((m) => m.source === "simulation") &&
        Object.keys(simAgentObsMap).length > 0
      ) {
        const obsProjects = Object.values(simAgentObsMap);
        if (obsProjects.length > 0) {
          const projectIds = [...new Set(obsProjects.map((p) => p.projectId))];
          newMetric.filters = [
            ...(newMetric.filters || []),
            buildLinkedProjectFilter(projectIds),
          ];
          newMetric._linkedAgents = obsProjects
            .map((p) => p.agentName)
            .join(", ");
        }
      }

      if (pickerTargetIndex != null) {
        // Replace existing metric, preserving filters
        const updated = [...metrics];
        newMetric.filters = updated[pickerTargetIndex].filters;
        updated[pickerTargetIndex] = newMetric;
        setMetrics(updated);
      } else {
        if (metrics.length >= 5) return;
        setMetrics([...metrics, newMetric]);
      }
    } else if (pickerMode === "filter") {
      // For eval metrics with Pass/Fail or Choices, treat as string (selectable options)
      const evalOt = (option.outputType || "").toUpperCase();
      const isEvalCategorical =
        option.type === "eval_metric" &&
        ["PASS_FAIL", "CHOICE", "CHOICES"].includes(evalOt);
      const dataType = isEvalCategorical
        ? "string"
        : option.dataType || "string";
      const defaults = getWidgetFilterDefaults(dataType);
      const entry = {
        id: option.id,
        registryId: option.registryId,
        name: option.name,
        type: option.type,
        dataType,
        attributeTypes: option.attributeTypes,
        attributeTypesExact: option.attributeTypesExact,
        source: option.source || "traces",
        outputType: option.outputType,
        choices: option.choiceOptions || option.choices,
      };
      if (pickerTargetIndex != null) {
        const updated = [...filters];
        updated[pickerTargetIndex] = {
          ...updated[pickerTargetIndex],
          ...entry,
          operator: defaults.operator,
          value: defaults.value,
        };
        setFilters(updated);
        if (defaults.opensValuePicker) setPendingFilterOpen(pickerTargetIndex);
      } else {
        const newIndex = filters.length;
        setFilters([
          ...filters,
          {
            ...entry,
            operator: defaults.operator,
            value: defaults.value,
          },
        ]);
        if (defaults.opensValuePicker) setPendingFilterOpen(newIndex);
      }
    } else if (pickerMode === "breakdown") {
      const entry = {
        id: option.id,
        registryId: option.registryId,
        name: option.name,
        type: option.type,
        dataType: option.dataType || "string",
        attributeTypes: option.attributeTypes,
        attributeTypesExact: option.attributeTypesExact,
        source: option.source || "traces",
        outputType: option.outputType,
      };
      if (pickerTargetIndex != null) {
        const updated = [...breakdowns];
        updated[pickerTargetIndex] = entry;
        setBreakdowns(updated);
      } else {
        setBreakdowns([...breakdowns, entry]);
      }
    } else if (pickerMode === "metric_filter" && pickerMetricIndex != null) {
      // Add or replace a per-metric filter
      const mfEvalOt = (option.outputType || "").toUpperCase();
      const mfIsEvalCategorical =
        option.type === "eval_metric" &&
        ["PASS_FAIL", "CHOICE", "CHOICES"].includes(mfEvalOt);
      const dataType = mfIsEvalCategorical
        ? "string"
        : option.dataType || "string";
      const defaults = getWidgetFilterDefaults(dataType);
      const newFilter = {
        id: option.id,
        registryId: option.registryId,
        name: option.name,
        type: option.type,
        dataType,
        attributeTypes: option.attributeTypes,
        attributeTypesExact: option.attributeTypesExact,
        source: option.source || "traces",
        outputType: option.outputType,
        choices: option.choiceOptions || option.choices,
        operator: defaults.operator,
        value: defaults.value,
      };
      const updated = [...metrics];
      const currentFilters = [...(updated[pickerMetricIndex].filters || [])];
      let fIdx;
      if (pickerTargetIndex != null) {
        // Replace existing filter attribute
        currentFilters[pickerTargetIndex] = newFilter;
        fIdx = pickerTargetIndex;
      } else {
        // Add new filter
        fIdx = currentFilters.length;
        currentFilters.push(newFilter);
      }
      updated[pickerMetricIndex] = {
        ...updated[pickerMetricIndex],
        filters: currentFilters,
      };
      setMetrics(updated);
      // Auto-open the retained-value picker for multi-value filters.
      if (defaults.opensValuePicker) {
        setTimeout(() => {
          const refKey = `${pickerMetricIndex}_${fIdx}`;
          const el = mfValueRefs.current[refKey];
          if (el) {
            setMfValueAnchor(el);
            setMfValueTarget({ metricIdx: pickerMetricIndex, filterIdx: fIdx });
          }
        }, 150);
      }
    }
    setPickerOpen(false);
  };

  const handleRemoveMetric = (index) => {
    setMetrics(metrics.filter((_, i) => i !== index));
  };

  const handleUpdateMetricAggregation = (index, agg) => {
    const updated = [...metrics];
    updated[index] = { ...updated[index], aggregation: agg };
    setMetrics(updated);
  };

  const handleRemoveMetricFilter = (metricIdx, filterIdx) => {
    const updated = [...metrics];
    const mf = [...(updated[metricIdx].filters || [])];
    mf.splice(filterIdx, 1);
    updated[metricIdx] = { ...updated[metricIdx], filters: mf };
    setMetrics(updated);
  };

  const handleUpdateMetricFilter = (metricIdx, filterIdx, patch) => {
    const updated = [...metrics];
    const mf = [...(updated[metricIdx].filters || [])];
    mf[filterIdx] = { ...mf[filterIdx], ...patch };
    updated[metricIdx] = { ...updated[metricIdx], filters: mf };
    setMetrics(updated);
  };

  const handleRemoveFilter = (index) => {
    setFilters(filters.filter((_, i) => i !== index));
  };

  const handleRemoveBreakdown = (index) => {
    const next = breakdowns.filter((_, i) => i !== index);
    setBreakdowns(next);
    // Pie needs something to slice by. Fall back here rather than in an effect,
    // so loading a legacy breakdown-less pie widget for editing doesn't get
    // silently rewritten before the user touches anything.
    if (chartType === "pie" && !next.some((b) => b.id)) setChartType("column");
  };

  const [saveStatus, setSaveStatus] = useState("idle"); // "idle" | "saving" | "saved"

  const handleSave = async () => {
    if (metrics.length === 0) {
      enqueueSnackbar("Add at least one metric", { variant: "warning" });
      return;
    }

    // visible_series lives in chart_config since query_config's serializer
    // rejects unknown fields.
    const data = {
      name: chartName.trim() || "Untitled widget",
      description: chartDescription.trim(),
      query_config: buildQueryConfig(),
      chart_config: {
        chart_type: chartType,
        axis_config: toAxisConfigPayload(axisConfig),
        visible_series: currentVisibleSeriesKeys(),
      },
    };

    setSaveStatus("saving");
    try {
      if (isEditing) {
        // Only send name + config fields — preserve width/height/position from layout
        await updateMutation.mutateAsync({
          dashboardId,
          widgetId: effectiveWidgetId,
          data,
        });
      } else {
        const result = await createMutation.mutateAsync({
          dashboardId,
          data: { ...data, width: 12, height: 320, position: 999 },
        });
        // Track created ID so subsequent saves update instead of creating again
        const newWidgetId = result?.data?.result?.id || result?.data?.id;
        if (newWidgetId) {
          setCreatedWidgetId(newWidgetId);
        }
      }
      setSaveStatus("saved");
      clearTimeout(saveNavTimerRef.current);
      saveNavTimerRef.current = setTimeout(() => {
        navigate(dashboardDetailUrl);
        setSaveStatus("idle");
      }, SAVED_NAV_DELAY_MS);
    } catch {
      setSaveStatus("idle");
      enqueueSnackbar(`Failed to ${isEditing ? "update" : "create"} widget`, {
        variant: "error",
      });
    }
  };

  // Chart preview
  // Backend returns: { metrics: [{ name, aggregation, unit, series: [{ name, data: [{ timestamp, value }] }] }] }
  const activeExactPreview =
    lastExactPreview?.signature === previewQuerySignature
      ? lastExactPreview
      : null;
  const previewResult = activeExactPreview?.result;
  const { renderableMetrics: previewRenderableMetrics, series: previewSeries } =
    useMemo(
      () => getDashboardMetricSeriesState(previewResult?.metrics),
      [previewResult?.metrics],
    );

  // Current selection as stable keys for persistence; null = all visible.
  const currentVisibleSeriesKeys = useCallback(
    () =>
      visibleSeries === null
        ? null
        : [...visibleSeries].map((i) => previewSeries[i]?.key).filter(Boolean),
    [visibleSeries, previewSeries],
  );

  // Auto-select top 10 series when there are more than 10 breakdown series
  const MAX_CHART_SERIES = 10;
  useEffect(() => {
    if (previewSeries.length === 0) return;

    // Restore a saved selection once, on the first non-empty previewSeries;
    // consuming the ref lets later re-queries fall through to the default below.
    if (pendingVisibleSeriesRef.current !== undefined) {
      const saved = pendingVisibleSeriesRef.current;
      pendingVisibleSeriesRef.current = undefined;
      const decision = resolveSavedSelection(saved, previewSeries);
      // undefined = the saved selection is stale → fall through to the default.
      if (decision !== undefined) {
        setVisibleSeries(decision);
        return;
      }
    }

    if (previewSeries.length <= MAX_CHART_SERIES) {
      // Show all if within limit
      if (visibleSeries !== null) setVisibleSeries(null);
      return;
    }
    // Rank series by total value, select top 10
    const ranked = previewSeries
      .map((s, i) => ({
        i,
        total: s.data.reduce((sum, pt) => sum + (pt.y || 0), 0),
      }))
      .sort((a, b) => b.total - a.total);
    const topIndices = new Set(
      ranked.slice(0, MAX_CHART_SERIES).map((r) => r.i),
    );
    setVisibleSeries(topIndices);
  }, [previewSeries]); // eslint-disable-line react-hooks/exhaustive-deps

  // Derive ApexCharts type and stacking from our chartType
  const apexType = useMemo(() => {
    const map = {
      line: "line",
      stacked_line: "area",
      column: "bar",
      stacked_column: "bar",
      bar: "bar",
      stacked_bar: "bar",
      pie: "pie",
      table: "line",
      metric: "line",
    };
    return map[chartType] || "line";
  }, [chartType]);
  const isStacked = chartType.startsWith("stacked_");
  const isHorizontal = chartType === "bar" || chartType === "stacked_bar";
  const isPie = chartType === "pie";
  const isTable = chartType === "table";
  const isMetricCard = chartType === "metric";
  const isLineChart = apexType === "line";
  const connectsAcrossMissingBuckets =
    shouldConnectAcrossMissingBuckets(apexType);

  const aggColumnLabel = useMemo(
    () => getAggColumnLabel(metrics, ALL_AGGREGATIONS),
    [metrics],
  );

  // Filtered series for chart — respects checkbox visibility, preserving original colors
  const chartSeries = useMemo(() => {
    if (visibleSeries === null) return previewSeries;
    return previewSeries.filter((_, i) => visibleSeries.has(i));
  }, [previewSeries, visibleSeries]);

  // Match the saved-dashboard renderer: null means an absent aggregate
  // bucket, not zero, so line previews connect the neighbouring exact points.
  const plottedChartSeries = useMemo(
    () => getPlottedChartSeries(chartSeries, connectsAcrossMissingBuckets),
    [chartSeries, connectsAcrossMissingBuckets],
  );

  const outOfRangeWarning = useMemo(
    () => getYAxisRangeWarning(chartSeries, axisConfig),
    [chartSeries, axisConfig],
  );

  const autoDecimals = useMemo(
    () => getAutoDecimals(chartSeries),
    [chartSeries],
  );
  const suggestedLeftAxisUnit = useMemo(
    () => getSuggestedUnitConfig(metrics),
    [metrics],
  );
  const leftAxisFormatConfig = useMemo(() => {
    const leftAxis = axisConfig.leftY || {};
    const metricUnits = previewRenderableMetrics.map(
      ({ metric }) => metric?.unit ?? "",
    );
    const isMixedUnits = new Set(metricUnits).size > 1;
    const effectiveUnit = isMixedUnits
      ? ""
      : leftAxis.unit || suggestedLeftAxisUnit.unit;
    return {
      ...leftAxis,
      unit: effectiveUnit,
      prefixSuffix: effectiveUnit
        ? leftAxis.prefixSuffix ||
          suggestedLeftAxisUnit.prefixSuffix ||
          "prefix"
        : suggestedLeftAxisUnit.prefixSuffix,
    };
  }, [axisConfig.leftY, suggestedLeftAxisUnit, previewRenderableMetrics]);

  useEffect(() => {
    const currentUnit = axisConfig.leftY.unit;
    const suggested = suggestedLeftAxisUnit.unit;
    // Skip if the user picked something different from what we
    // auto-applied — that's their choice and we leave it alone.
    if (currentUnit && currentUnit !== autoAppliedLeftAxisUnit) {
      return;
    }
    if (suggested === currentUnit) return;
    setAxisConfig((prev) => ({
      ...prev,
      leftY: {
        ...prev.leftY,
        unit: suggested,
        prefixSuffix: suggested
          ? suggestedLeftAxisUnit.prefixSuffix
          : prev.leftY.prefixSuffix || "prefix",
      },
    }));
    setAutoAppliedLeftAxisUnit(suggested || null);
  }, [axisConfig.leftY.unit, autoAppliedLeftAxisUnit, suggestedLeftAxisUnit]);

  // Hash-derived colors give cross-reload stability; the map walker
  // advances to the next free slot on collision so ≤10 series never
  // share a color inside one widget. Built from previewSeries so hidden
  // series keep their color when re-checked.
  const seriesColorMap = useMemo(
    () => buildSeriesColorMap(previewSeries.map((s) => s.name)),
    [previewSeries],
  );
  const chartColors = useMemo(
    () => chartSeries.map((s) => getSeriesColor(s.name, seriesColorMap)),
    [chartSeries, seriesColorMap],
  );

  // Pie slices are breakdown values, which repeat across metrics — key their
  // colours by the raw breakdown name so a project stays one colour in every pie.
  const pieColorMap = useMemo(
    () =>
      buildSeriesColorMap([
        ...new Set(previewSeries.map((s) => s.breakdownName)),
      ]),
    [previewSeries],
  );
  const pieColorFor = useCallback(
    (name) => getSeriesColor(name, pieColorMap),
    [pieColorMap],
  );

  // A pie needs a category to slice by. `hasBreakdown` mirrors the filter in
  // buildQueryConfig so the gate matches what is actually queried;
  // `pieHasBreakdown` is response-driven so legacy saved pies render correctly.
  const hasBreakdown = breakdowns.some((b) => b.id);
  const pieHasBreakdown = useMemo(
    () => previewSeries.some((s) => s.breakdownName !== "total"),
    [previewSeries],
  );
  // From previewSeries, not the top-10-filtered chartSeries: a global cap can
  // starve one metric of every slice, and groupPieSeries caps per metric.
  const pieGroups = useMemo(
    () => (isPie && pieHasBreakdown ? groupPieSeries(previewSeries) : []),
    [isPie, pieHasBreakdown, previewSeries],
  );

  // Legend hover → highlight series by dimming others via SVG opacity
  const handleLegendHover = useCallback((seriesIndex) => {
    const el = lineChartRef.current;
    if (!el) return;
    const paths = el.querySelectorAll(".apexcharts-series");
    paths.forEach((p, i) => {
      p.style.opacity = i === seriesIndex ? "1" : "0.15";
      p.style.transition = "opacity 0.15s";
    });
  }, []);

  const handleLegendLeave = useCallback(() => {
    const el = lineChartRef.current;
    if (!el) return;
    const paths = el.querySelectorAll(".apexcharts-series");
    paths.forEach((p) => {
      p.style.opacity = "1";
    });
  }, []);

  const isDark = theme.palette.mode === "dark";
  const formatValFn = useCallback(
    (val) =>
      formatValueWithConfig(val, leftAxisFormatConfig, {
        fallbackDecimals: autoDecimals,
      }),
    [autoDecimals, leftAxisFormatConfig],
  );

  const chartOptions = useMemo(() => {
    const makeFormatter =
      (cfg, fallbackDecimals = autoDecimals, includeUnit = true) =>
      (val) =>
        formatValueWithConfig(val, cfg, { fallbackDecimals, includeUnit });
    const formatVal = makeFormatter(leftAxisFormatConfig);
    return {
      chart: {
        type: apexType,
        toolbar: { show: false },
        zoom: { enabled: true },
        stacked: isStacked,
        animations: { enabled: true, easing: "easeinout", speed: 400 },
        events: {
          mouseMove: (event, chartContext, config) => {
            const el = chartContext?.el;
            if (!el || !config) return;
            // Don't override legend hover highlight
            if (el.getAttribute("data-legend-highlight")) return;
            const paths = el.querySelectorAll(".apexcharts-series");
            let si = config.seriesIndex;
            const dpi = config.dataPointIndex;

            // For stacked charts, seriesIndex is always the topmost area.
            // Compute which stacked band the mouse Y falls in.
            if (isStacked && dpi >= 0) {
              const w = chartContext.w;
              const gridRect = w?.globals?.gridRect;
              if (!gridRect) return;
              const chartRect = el.getBoundingClientRect();
              const mouseY = event.clientY - chartRect.top - gridRect.y;
              const plotH = gridRect.height;
              const minY = w.globals.minY;
              const maxY = w.globals.maxY;
              const mouseVal = maxY - (mouseY / plotH) * (maxY - minY);

              let cumSum = 0;
              si = w.globals.series.length - 1;
              for (let i = 0; i < w.globals.series.length; i++) {
                cumSum += w.globals.series[i]?.[dpi] || 0;
                if (mouseVal <= cumSum) {
                  si = i;
                  break;
                }
              }
            }

            if (si >= 0 && paths.length > 1) {
              el.setAttribute("data-custom-highlight", "1");
              paths.forEach((p, i) => {
                p.style.transition = "opacity 0.15s ease";
                p.style.opacity = i === si ? "1" : "0.15";
              });
            } else if (el.getAttribute("data-custom-highlight")) {
              el.removeAttribute("data-custom-highlight");
              paths.forEach((p) => {
                p.style.opacity = "1";
              });
            }
          },
          mouseLeave: (event, chartContext) => {
            const el = chartContext?.el;
            if (!el) return;
            if (el.getAttribute("data-custom-highlight")) {
              el.removeAttribute("data-custom-highlight");
              el.querySelectorAll(".apexcharts-series").forEach((p) => {
                p.style.transition = "opacity 0.2s ease";
                p.style.opacity = "1";
              });
            }
          },
          mounted: (chartContext) => {
            const el = chartContext?.el;
            if (!el) return;
            const bindLegend = () => {
              const items = el.querySelectorAll(".apexcharts-legend-series");
              items.forEach((item, idx) => {
                if (item.getAttribute("data-hover-bound")) return;
                item.setAttribute("data-hover-bound", "1");
                item.addEventListener("mouseenter", () => {
                  el.setAttribute("data-legend-highlight", "1");
                  el.querySelectorAll(".apexcharts-series").forEach((p, i) => {
                    p.style.transition = "opacity 0.15s ease";
                    p.style.opacity = i === idx ? "1" : "0.15";
                  });
                });
                item.addEventListener("mouseleave", () => {
                  el.removeAttribute("data-legend-highlight");
                  el.querySelectorAll(".apexcharts-series").forEach((p) => {
                    p.style.transition = "opacity 0.2s ease";
                    p.style.opacity = "1";
                  });
                });
              });
            };
            bindLegend();
          },
          updated: (chartContext) => {
            const el = chartContext?.el;
            if (!el) return;
            const items = el.querySelectorAll(".apexcharts-legend-series");
            items.forEach((item, idx) => {
              if (item.getAttribute("data-hover-bound")) return;
              item.setAttribute("data-hover-bound", "1");
              item.addEventListener("mouseenter", () => {
                el.setAttribute("data-legend-highlight", "1");
                el.querySelectorAll(".apexcharts-series").forEach((p, i) => {
                  p.style.transition = "opacity 0.15s ease";
                  p.style.opacity = i === idx ? "1" : "0.15";
                });
              });
              item.addEventListener("mouseleave", () => {
                el.removeAttribute("data-legend-highlight");
                el.querySelectorAll(".apexcharts-series").forEach((p) => {
                  p.style.transition = "opacity 0.2s ease";
                  p.style.opacity = "1";
                });
              });
            });
          },
        },
      },
      dataLabels: isHorizontal
        ? {
            enabled: true,
            style: { fontSize: "13px", fontWeight: 500 },
            formatter: formatVal,
            offsetX: 6,
          }
        : { enabled: false },
      plotOptions: {
        bar: {
          horizontal: isHorizontal,
          barHeight: isHorizontal ? "50%" : undefined,
          columnWidth: !isHorizontal ? "60%" : undefined,
          borderRadius: 4,
          distributed: isHorizontal,
        },
      },
      xaxis: isHorizontal
        ? {
            labels: {
              show: false,
              style: { colors: theme.palette.text.secondary, fontSize: "11px" },
            },
            axisBorder: { show: false },
            axisTicks: { show: false },
          }
        : {
            type: "datetime",
            tickAmount: Math.min(chartSeries[0]?.data?.length || 10, 12),
            labels: {
              show: axisConfig.xAxis.visible,
              style: { colors: theme.palette.text.secondary, fontSize: "11px" },
              datetimeUTC: false,
              datetimeFormatter: {
                year: "MMMM",
                month: "MMMM",
                day: "MMM dd",
                hour: "HH:mm",
              },
            },
            axisBorder: { show: false },
            axisTicks: { show: false },
            ...(axisConfig.xAxis.label && {
              title: {
                text: axisConfig.xAxis.label,
                style: {
                  fontSize: "12px",
                  color: theme.palette.text.secondary,
                },
              },
            }),
            crosshairs: {
              show: true,
              width: 1,
              position: "back",
              stroke: {
                color: theme.palette.text.disabled,
                width: 1,
                dashArray: 3,
              },
            },
          },
      yaxis: (() => {
        const hasRightAxis =
          axisConfig.rightY.visible &&
          Object.values(axisConfig.seriesAxis).some((s) => s === "right");
        if (!hasRightAxis) {
          return {
            show: axisConfig.leftY.visible,
            tickAmount: 5,
            forceNiceScale: axisConfig.leftY.outOfBounds !== "hidden",
            logarithmic: axisConfig.leftY.scale === "logarithmic",
            ...(axisConfig.leftY.min !== "" && {
              min: Number(axisConfig.leftY.min),
            }),
            ...(axisConfig.leftY.max !== "" && {
              max: Number(axisConfig.leftY.max),
            }),
            ...(axisConfig.leftY.label && {
              title: {
                text: axisConfig.leftY.label,
                style: {
                  fontSize: "12px",
                  color: theme.palette.text.secondary,
                },
              },
            }),
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "11px" },
              formatter: formatVal,
            },
          };
        }
        // Dual axis
        return chartSeries.map((_, i) => {
          const origIdx = visibleSeries === null ? i : [...visibleSeries][i];
          const side = axisConfig.seriesAxis[origIdx] || "left";
          const cfg = side === "right" ? axisConfig.rightY : axisConfig.leftY;
          return {
            show:
              i === 0 ||
              (side === "right" &&
                !chartSeries
                  .slice(0, i)
                  .some(
                    (__, j) =>
                      (axisConfig.seriesAxis[
                        visibleSeries === null ? j : [...visibleSeries][j]
                      ] || "left") === "right",
                  )),
            opposite: side === "right",
            tickAmount: 5,
            forceNiceScale: cfg.outOfBounds !== "hidden",
            logarithmic: cfg.scale === "logarithmic",
            ...(cfg.min !== "" && { min: Number(cfg.min) }),
            ...(cfg.max !== "" && { max: Number(cfg.max) }),
            ...(cfg.label && {
              title: {
                text: cfg.label,
                style: {
                  fontSize: "12px",
                  color: theme.palette.text.secondary,
                },
              },
            }),
            labels: {
              style: { colors: theme.palette.text.secondary, fontSize: "11px" },
              formatter: makeFormatter(cfg),
            },
          };
        });
      })(),
      stroke: {
        curve: "monotoneCubic",
        width: apexType === "area" ? 2 : apexType === "line" ? 2.5 : 0,
      },
      fill: (() => {
        if (apexType !== "area") return { type: "solid", opacity: 1 };
        if (isStacked) return { type: "solid", opacity: 0.7 };
        return {
          type: "gradient",
          opacity: 1,
          gradient: {
            shadeIntensity: 1,
            opacityFrom: 0.35,
            opacityTo: 0.05,
            stops: [0, 90, 100],
          },
        };
      })(),
      markers: {
        size: isLineChart ? 5 : apexType === "area" ? 4 : 0,
        strokeWidth: 2,
        strokeColors: isDark ? theme.palette.background.paper : "#fff",
        hover: isLineChart
          ? { size: 8, sizeOffset: 2 }
          : { size: 6, sizeOffset: 3 },
      },
      states: {
        hover: {
          filter: { type: "none" },
        },
        active: {
          allowMultipleDataPointsSelection: false,
          filter: { type: "none" },
        },
      },
      grid: {
        borderColor: theme.palette.divider,
        strokeDashArray: 3,
        xaxis: { lines: { show: false } },
        padding: { left: 8, right: 8 },
      },
      colors: chartColors,
      legend: { show: false, height: 0 },
      tooltip: isStacked
        ? {
            enabled: true,
            shared: true,
            intersect: false,
            theme: theme.palette.mode,
            style: { fontSize: "12px" },
            x: {
              format: "MMM dd, yyyy",
            },
            y: {
              formatter: formatVal,
            },
          }
        : {
            enabled: true,
            shared: false,
            intersect: isLineChart,
            custom: ({ series, seriesIndex, dataPointIndex, w }) => {
              const sName = w.globals.seriesNames[seriesIndex] || "";
              const color = w.globals.colors[seriesIndex] || "#6366F1";
              const val = series[seriesIndex]?.[dataPointIndex];
              const prevVal =
                dataPointIndex > 0
                  ? series[seriesIndex]?.[dataPointIndex - 1]
                  : null;
              const ts = w.globals.seriesX[seriesIndex]?.[dataPointIndex];
              const dateStr = ts ? format(new Date(ts), "MMM dd, yyyy") : "";
              const fmtVal = formatVal(val);
              const bg = isDark ? "#1e1e2e" : "#fff";
              const _border = isDark
                ? "rgba(255,255,255,0.08)"
                : "rgba(0,0,0,0.06)";
              const textPrimary = isDark ? "#fff" : "#1a1a2e";
              const textSecondary = isDark
                ? "rgba(255,255,255,0.5)"
                : "rgba(0,0,0,0.45)";
              let changeHtml = "";
              if (prevVal != null && prevVal !== 0 && val != null) {
                const pct = ((val - prevVal) / Math.abs(prevVal)) * 100;
                const sign = pct >= 0 ? "+" : "";
                const changeColor = pct >= 0 ? "#22C55E" : "#FF4842";
                changeHtml = `<div style="display:flex;align-items:center;gap:6px;margin-top:6px"><span style="color:${changeColor};font-weight:600;font-size:14px">${sign}${pct.toFixed(2)}%</span><span style="color:${textSecondary};font-size:13px">from previous</span></div>`;
              }
              return `<div style="display:flex;background:${bg};border:none;border-radius:12px;box-shadow:0 8px 24px ${isDark ? "rgba(0,0,0,0.5)" : "rgba(0,0,0,0.08)"};overflow:hidden;min-width:200px">
                <div style="width:4px;flex-shrink:0;background:${color}"></div>
                <div style="padding:14px 16px;flex:1">
                  <div style="font-weight:700;font-size:14px;color:${textPrimary};line-height:1.3">${escapeHtml(sName)}</div>
                  <div style="font-size:12px;color:${textSecondary};margin-top:3px">${escapeHtml(dateStr)}</div>
                  <div style="display:flex;align-items:baseline;gap:8px;margin-top:8px">
                    <span style="font-weight:700;font-size:20px;color:${textPrimary}">${escapeHtml(fmtVal)}</span>
                  </div>
                  ${changeHtml}
                </div>
              </div>`;
            },
          },
    };
  }, [
    apexType,
    isLineChart,
    isStacked,
    isHorizontal,
    chartSeries,
    chartColors,
    theme,
    axisConfig,
    autoDecimals,
    isDark,
    leftAxisFormatConfig,
    visibleSeries,
  ]);

  // Horizontal bar: aggregate each series into one bar
  const barData = useMemo(() => {
    if (!isHorizontal) return null;
    const categories = chartSeries.map((s) => {
      const name = s.name || "";
      return name.length > 25 ? name.slice(0, 22) + "..." : name;
    });
    const values = chartSeries.map((s) => {
      // Same aggregation-aware value the metric card, table and pie use, so
      // one widget cannot read differently per chart type.
      const value = getSeriesScalar(s.data, s.aggregation);
      return {
        value,
        numericValue: value == null ? 0 : value,
      };
    });
    return {
      categories,
      series: [
        { name: "Value", data: values.map((item) => item.numericValue) },
      ],
      rows: values,
    };
  }, [isHorizontal, chartSeries]);

  const showChart = viewMode !== "table" && chartHeight > 0;
  const _showTable = true;

  // A preview query only fires when there's at least one metric AND (if a custom
  // range is chosen) a range is actually set — mirrors the auto-preview effect.
  const canPreview =
    metrics.length > 0 && !(timePreset === "custom" && !customDateRange);

  const serverPreviewState = getWidgetPreviewState(
    queryMutation.data?.data?.result,
    queryMutation,
  );
  const previewPreparing =
    !previewFailed &&
    canPreview &&
    !activeExactPreview &&
    (queryMutation.isPending ||
      isPreviewRefreshing ||
      serverPreviewState === "preparing");
  const previewActivity = previewPreparing && !activeExactPreview;
  const previewFailureBlocksRendering = shouldBlockWidgetPreviewForFailure({
    previewFailed,
    hasExactPreview: Boolean(activeExactPreview),
  });

  useEffect(() => {
    if (!previewActivity) return undefined;
    const timer = setTimeout(() => {
      previewGenerationRef.current += 1;
      clearTimeout(previewPollTimerRef.current);
      previewPollTimerRef.current = null;
      setIsPreviewRefreshing(false);
      setPreviewFailed(true);
    }, WIDGET_PREVIEW_MAX_WAIT_MS);
    return () => clearTimeout(timer);
  }, [previewActivity]);

  const retryPreview = useCallback(() => {
    setPreviewFailed(false);
    runPreviewQuery(buildQueryConfig(), { refresh: true });
  }, [buildQueryConfig, runPreviewQuery]);

  const previewLoading =
    !previewFailed &&
    ((queryMutation.isPending && !activeExactPreview) ||
      (isEditing && isDashboardLoading) ||
      (canPreview && queryMutation.isIdle && !activeExactPreview));

  const emptyPreviewMessage = canPreview
    ? activeExactPreview
      ? "No data for this selection"
      : previewPreparing
        ? AGGREGATION_PREPARING_MESSAGE
        : "No data for this selection"
    : "Fill in the required fields to see preview";

  const cleanupDragRef = useRef(null);
  const handleDragStart = useCallback(
    (e) => {
      e.preventDefault();
      setIsDragging(true);
      const startY = e.clientY;
      const startHeight = chartHeight;
      let rafId = null;
      const onMouseMove = (ev) => {
        if (rafId) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {
          const delta = ev.clientY - startY;
          const newH = Math.max(60, Math.min(600, startHeight + delta));
          setChartHeight(newH);
          if (newH <= 120) setViewMode("table");
          else if (newH <= 250) setViewMode("split-table");
          else if (newH <= 450) setViewMode("split-chart");
          else setViewMode("chart");
        });
      };
      const onMouseUp = () => {
        setIsDragging(false);
        if (rafId) cancelAnimationFrame(rafId);
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
        cleanupDragRef.current = null;
      };
      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
      cleanupDragRef.current = () => {
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      };
    },
    [chartHeight],
  );

  useEffect(() => {
    return () => {
      if (cleanupDragRef.current) cleanupDragRef.current();
    };
  }, []);

  const editorLoadState = getWidgetEditorLoadState({
    isEditing: isRouteEditing,
    isLoading: isDashboardLoading,
    isError: isDashboardError,
    dashboard,
    widgetId,
  });
  if (editorLoadState === "error") {
    return (
      <WidgetEditorLoadFailure
        kind="error"
        onRetry={() => refetchDashboard()}
        onBack={() => navigate(dashboardDetailUrl)}
      />
    );
  }
  if (editorLoadState === "missing") {
    return (
      <WidgetEditorLoadFailure
        kind="missing"
        onBack={() => navigate(dashboardDetailUrl)}
      />
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        bgcolor: "background.paper",
      }}
    >
      {/* Top bar */}
      <Stack
        direction="row"
        alignItems="center"
        sx={{
          px: 2,
          py: 1,
          borderBottom: `1px solid ${theme.palette.divider}`,
          gap: 1,
          minHeight: 48,
        }}
      >
        {/* Breadcrumb links */}
        <Breadcrumbs
          separator={<Iconify icon="mdi:chevron-right" width={14} />}
          sx={{ flexShrink: 0 }}
        >
          <Link
            underline="hover"
            color="text.secondary"
            sx={{ cursor: "pointer", fontSize: "13px" }}
            onClick={() => navigate(paths.dashboard.dashboards.root)}
          >
            Dashboards
          </Link>
          <Link
            underline="hover"
            color="text.secondary"
            sx={{
              cursor: "pointer",
              fontSize: "13px",
              maxWidth: 140,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              display: "block",
            }}
            onClick={() => navigate(dashboardDetailUrl)}
          >
            {dashboard?.name || "Dashboard"}
          </Link>
        </Breadcrumbs>
        <Iconify
          icon="mdi:chevron-right"
          width={14}
          sx={{ color: "text.disabled", flexShrink: 0 }}
        />

        {/* Inline editable name */}
        {editingName ? (
          <TextField
            value={chartName}
            onChange={(e) => setChartName(e.target.value)}
            onBlur={() => setEditingName(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter") setEditingName(false);
            }}
            autoFocus
            size="small"
            variant="outlined"
            placeholder="Untitled widget"
            sx={{
              minWidth: 200,
              maxWidth: 350,
              "& .MuiOutlinedInput-input": {
                py: 0.5,
                fontSize: "14px",
                fontWeight: 500,
              },
            }}
          />
        ) : (
          <Typography
            onClick={() => !isReadOnly && setEditingName(true)}
            sx={{
              fontSize: "14px",
              fontWeight: 500,
              cursor: isReadOnly ? "default" : "pointer",
              maxWidth: 300,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              "&:hover": isReadOnly ? undefined : { color: "primary.main" },
            }}
          >
            {chartName || "Untitled widget"}
          </Typography>
        )}

        {/* Description — a capped preview in the bar; the full text is read
            and edited in a popover, so the bar can't be blown out by length. */}
        <Box ref={descSlotRef} sx={{ display: "flex", minWidth: 0 }}>
          {trimmedDescription ? (
            <TruncatedTooltipText text={trimmedDescription}>
              {(measureRef) => (
                <Stack
                  direction="row"
                  alignItems="center"
                  gap={0.5}
                  role={isReadOnly ? undefined : "button"}
                  tabIndex={isReadOnly ? undefined : 0}
                  aria-label={
                    isReadOnly
                      ? undefined
                      : `Edit description: ${trimmedDescription}`
                  }
                  onClick={() => !isReadOnly && setDescOpen(true)}
                  onKeyDown={(e) => {
                    if (isReadOnly) return;
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setDescOpen(true);
                    }
                  }}
                  sx={{
                    minWidth: 0,
                    maxWidth: 260,
                    px: 0.75,
                    py: 0.25,
                    borderRadius: 1,
                    color: "text.secondary",
                    cursor: isReadOnly ? "default" : "pointer",
                    transition: "background-color 0.15s, color 0.15s",
                    "&:hover": isReadOnly
                      ? undefined
                      : { bgcolor: "action.hover", color: "text.primary" },
                    "&:focus-visible": {
                      outline: "2px solid",
                      outlineColor: "primary.main",
                      outlineOffset: 2,
                    },
                  }}
                >
                  <Iconify
                    icon="mdi:text-box-outline"
                    width={14}
                    sx={{ flexShrink: 0 }}
                  />
                  <Typography
                    ref={measureRef}
                    noWrap
                    sx={{ fontSize: "13px", minWidth: 0 }}
                  >
                    {trimmedDescription}
                  </Typography>
                </Stack>
              )}
            </TruncatedTooltipText>
          ) : (
            !isReadOnly && (
              <Button
                size="small"
                startIcon={<Iconify icon="mdi:plus" width={14} />}
                onClick={() => setDescOpen(true)}
                sx={{
                  flexShrink: 0,
                  fontSize: "13px",
                  fontWeight: 400,
                  color: "text.disabled",
                  "&:hover": { color: "text.secondary" },
                }}
              >
                Add description
              </Button>
            )
          )}
        </Box>

        <WidgetDescriptionPopover
          open={descOpen}
          anchorEl={descSlotRef.current}
          value={chartDescription}
          onChange={setChartDescription}
          onClose={() => setDescOpen(false)}
        />

        {/* Spacer */}
        <Box sx={{ flex: 1 }} />

        {/* Icon buttons */}
        <IconButton
          size="small"
          onClick={(e) => setMoreMenuAnchor(e.currentTarget)}
          sx={{ color: "text.secondary" }}
        >
          <Iconify icon="mdi:dots-horizontal" width={20} />
        </IconButton>

        {/* More menu */}
        <Menu
          anchorEl={moreMenuAnchor}
          open={Boolean(moreMenuAnchor)}
          onClose={() => setMoreMenuAnchor(null)}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
          slotProps={{ paper: { sx: { minWidth: 180 } } }}
        >
          {isEditing && canDelete && (
            <MenuItem
              onClick={() => {
                setMoreMenuAnchor(null);
                setConfirmDeleteOpen(true);
              }}
              sx={{ color: "error.main" }}
            >
              <ListItemIcon>
                <Iconify
                  icon="mdi:delete-outline"
                  width={18}
                  sx={{ color: "error.main" }}
                />
              </ListItemIcon>
              <ListItemText>Delete</ListItemText>
            </MenuItem>
          )}
          {!isReadOnly && (
            <MenuItem
              onClick={() => {
                setMoreMenuAnchor(null);
                // Delay to let MUI Menu close and release focus trap
                setTimeout(() => setEditingName(true), 150);
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:pencil-outline" width={18} />
              </ListItemIcon>
              <ListItemText>Rename</ListItemText>
            </MenuItem>
          )}
          {!isReadOnly && (
            <MenuItem
              onClick={() => {
                setMoreMenuAnchor(null);
                const source = dashboard?.widgets?.find(
                  (w) => w.id === widgetId,
                );
                const dupData = {
                  name: `${chartName || "Untitled widget"} (copy)`,
                  description: trimmedDescription,
                  // Carry the source widget's own layout and sit the copy next
                  // to it, as the backend's duplicate endpoint does. The
                  // hardcoded height of 1 fell under the card's >50 guard, so
                  // it silently rendered at the default height instead.
                  width: source?.width || 12,
                  height: source?.height || DEFAULT_WIDGET_HEIGHT,
                  position: (source?.position ?? 0) + 1,
                  query_config: buildQueryConfig(),
                  chart_config: {
                    chart_type: chartType,
                    axis_config: toAxisConfigPayload(axisConfig),
                    visible_series: currentVisibleSeriesKeys(),
                  },
                };
                createMutation
                  .mutateAsync({ dashboardId, data: dupData })
                  .then(() => {
                    enqueueSnackbar("Widget duplicated", {
                      variant: "success",
                    });
                  });
              }}
            >
              <ListItemIcon>
                <Iconify icon="mdi:content-copy" width={18} />
              </ListItemIcon>
              <ListItemText>Duplicate</ListItemText>
            </MenuItem>
          )}
          <Divider />
          <MenuItem
            onClick={() => {
              setMoreMenuAnchor(null);
              if (!previewSeries.length) return;
              const header = [
                "Metric",
                aggColumnLabel,
                ...(previewSeries[0]?.data || []).map((pt) =>
                  format(new Date(pt.x), "yyyy-MM-dd"),
                ),
              ];
              const rows = previewSeries.map((s) => {
                // Agg column header is aggregation-derived, so the value must
                // honour that aggregation too.
                const avg = getSeriesScalar(s.data, s.aggregation);
                return [
                  s.name,
                  avg == null ? "—" : avg.toFixed(2),
                  ...s.data.map((pt) => (pt.y != null ? pt.y : "")),
                ];
              });
              const csv = [header, ...rows]
                .map((r) => r.map(escapeCsvField).join(","))
                .join("\n");
              const blob = new Blob([csv], { type: "text/csv" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = `${chartName || "widget"}.csv`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:download-outline" width={18} />
            </ListItemIcon>
            <ListItemText>Export CSV</ListItemText>
          </MenuItem>
          <MenuItem
            onClick={() => {
              setMoreMenuAnchor(null);
              if (!previewSeries.length) return;
              const header = [
                "Metric",
                aggColumnLabel,
                ...(previewSeries[0]?.data || []).map((pt) =>
                  format(new Date(pt.x), "yyyy-MM-dd"),
                ),
              ];
              const rows = previewSeries.map((s) => {
                // Agg column header is aggregation-derived, so the value must
                // honour that aggregation too.
                const avg = getSeriesScalar(s.data, s.aggregation);
                return [
                  s.name,
                  avg == null ? "—" : avg.toFixed(2),
                  ...s.data.map((pt) => (pt.y != null ? pt.y : "")),
                ];
              });
              const csv = [header, ...rows].map((r) => r.join("\t")).join("\n");
              navigator.clipboard.writeText(csv);
              enqueueSnackbar("Copied to clipboard", { variant: "success" });
            }}
          >
            <ListItemIcon>
              <Iconify icon="mdi:clipboard-outline" width={18} />
            </ListItemIcon>
            <ListItemText>Copy CSV</ListItemText>
          </MenuItem>
          <Divider />
          <MenuItem
            disabled={metrics.length === 0 || isPreviewRefreshing}
            onClick={() => {
              setMoreMenuAnchor(null);
              if (metrics.length > 0) {
                runPreviewQuery(buildQueryConfig(), { refresh: true });
              }
            }}
          >
            <ListItemIcon>
              {isPreviewRefreshing ? (
                <CircularProgress size={16} />
              ) : (
                <Iconify icon="mdi:refresh" width={18} />
              )}
            </ListItemIcon>
            <ListItemText>
              {isPreviewRefreshing ? "Refreshing Data" : "Refresh Data"}
            </ListItemText>
          </MenuItem>
        </Menu>

        <ConfirmDialog
          open={confirmDeleteOpen}
          onClose={() => setConfirmDeleteOpen(false)}
          title="Delete Widget"
          content={`Are you sure you want to delete "${chartName || "this widget"}"? This action cannot be undone.`}
          action={
            <Button
              variant="contained"
              color="error"
              size="small"
              onClick={confirmDeleteWidget}
              disabled={deleteMutation.isPending}
            >
              Delete
            </Button>
          }
        />

        <Button
          onClick={() => navigate(dashboardDetailUrl)}
          sx={{ color: "text.primary", fontWeight: 500 }}
        >
          Close
        </Button>
        <CustomTooltip
          show={isReadOnly}
          type=""
          title="You don't have permission to edit widgets."
          size="small"
          arrow
        >
          <span>
            <Button
              variant="contained"
              onClick={handleSave}
              disabled={isReadOnly || saveStatus !== "idle"}
              color={saveStatus === "saved" ? "success" : "primary"}
              startIcon={
                saveStatus === "saved" ? (
                  <Iconify icon="mdi:check" width={18} />
                ) : undefined
              }
            >
              {saveStatus === "saving"
                ? "Saving..."
                : saveStatus === "saved"
                  ? "Saved"
                  : "Save"}
            </Button>
          </span>
        </CustomTooltip>
      </Stack>

      {/* Main content area */}
      <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* Left: Chart area */}
        <Box
          sx={{
            flex: 1,
            p: 2,
            display: "flex",
            flexDirection: "column",
            gap: 2,
            overflow: "auto",
          }}
        >
          {/* Time range + granularity + chart type — single row */}
          <Stack direction="row" alignItems="center" gap={1.5} flexWrap="wrap">
            {/* Time preset pill group */}
            <Box
              sx={{
                display: "inline-flex",
                border: `1px solid ${theme.palette.divider}`,
                borderRadius: 1,
                overflow: "hidden",
                flexShrink: 0,
              }}
            >
              {DATE_PRESETS.map((p, i) => (
                <Box
                  key={p.value}
                  ref={p.value === "custom" ? customDateAnchorRef : undefined}
                  onClick={() => {
                    if (p.value === "custom") {
                      setTimePreset("custom");
                      setIsDatePickerOpen(true);
                    } else {
                      setTimePreset(p.value);
                    }
                  }}
                  sx={{
                    px: 1.5,
                    py: 0.6,
                    cursor: "pointer",
                    fontSize: "13px",
                    fontWeight: timePreset === p.value ? 600 : 400,
                    color:
                      timePreset === p.value
                        ? theme.palette.text.primary
                        : theme.palette.text.secondary,
                    bgcolor:
                      timePreset === p.value
                        ? theme.palette.mode === "dark"
                          ? "rgba(255,255,255,0.08)"
                          : "rgba(0,0,0,0.06)"
                        : "transparent",
                    borderRight:
                      i < DATE_PRESETS.length - 1
                        ? `1px solid ${theme.palette.divider}`
                        : "none",
                    whiteSpace: "nowrap",
                    userSelect: "none",
                    transition: "all 0.15s",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 0.5,
                    "&:hover": {
                      bgcolor:
                        theme.palette.mode === "dark"
                          ? "rgba(255,255,255,0.05)"
                          : "rgba(0,0,0,0.03)",
                    },
                  }}
                >
                  {p.value === "custom" && (
                    <Iconify icon="mdi:calendar-outline" width={15} />
                  )}
                  {p.value === "custom" && customDateRange
                    ? `${format(customDateRange[0], "MMM dd")} - ${format(customDateRange[1], "MMM dd")}`
                    : p.label}
                </Box>
              ))}
            </Box>

            <CustomDateRangePicker
              open={isDatePickerOpen}
              onClose={() => setIsDatePickerOpen(false)}
              anchorEl={customDateAnchorRef.current}
              value={customDateRange}
              setDateFilter={(filter) => {
                if (filter && filter[0] && filter[1]) {
                  setCustomDateRange([
                    new Date(filter[0]),
                    new Date(filter[1]),
                  ]);
                }
              }}
              setDateOption={() => setTimePreset("custom")}
            />

            <Box sx={{ flex: 1, minWidth: 0 }} />

            {/* Granularity dropdown */}
            <FormControl size="small" sx={{ minWidth: 80 }}>
              <Select
                value={granularity}
                onChange={(e) => setGranularity(e.target.value)}
                sx={{ fontSize: "13px", "& .MuiSelect-select": { py: 0.7 } }}
              >
                {allowedGranularities.map((g) => (
                  <MenuItem key={g.value} value={g.value}>
                    {g.label}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {/* Chart type dropdown */}
            <FormControl size="small" sx={{ minWidth: 120 }}>
              <Select
                value={chartType}
                onChange={(e) => setChartType(e.target.value)}
                sx={{ fontSize: "13px", "& .MuiSelect-select": { py: 0.7 } }}
                renderValue={(val) => {
                  const ct = CHART_TYPES.find((t) => t.value === val);
                  return (
                    <Stack direction="row" alignItems="center" gap={0.5}>
                      <Iconify icon={ct?.icon || "mdi:chart-line"} width={16} />
                      {ct?.label || val}
                    </Stack>
                  );
                }}
              >
                {CHART_TYPES.map((ct, i) => {
                  const prev = i > 0 ? CHART_TYPES[i - 1] : null;
                  const showDivider = prev && prev.group !== ct.group;
                  // A pie shows parts of one whole, so it needs a breakdown to
                  // slice by — without one every metric is a single value and
                  // would render as a meaningless 100% circle (TH-6530).
                  const pieDisabled = ct.value === "pie" && !hasBreakdown;
                  const label = (
                    <Stack direction="row" alignItems="center" gap={0.5}>
                      <Iconify icon={ct.icon} width={16} />
                      {ct.label}
                    </Stack>
                  );
                  return [
                    showDivider && <Divider key={`div-${i}`} />,
                    // The tooltip goes inside the MenuItem, never around it:
                    // Select matches its current value against `props.value` on
                    // its direct children, and a wrapper has none. A legacy
                    // breakdown-less pie widget is exactly the case where the
                    // disabled item is also the selected one, so wrapping it
                    // made MUI log an out-of-range value and forward option
                    // props onto the wrapper.
                    <MenuItem
                      key={ct.value}
                      value={ct.value}
                      disabled={pieDisabled}
                    >
                      {pieDisabled ? (
                        <CustomTooltip
                          show
                          size="small"
                          title="Add a breakdown to slice the pie by"
                          placement="right"
                        >
                          {/* A disabled MenuItem sets pointer-events: none, so
                              the trigger has to opt back in to receive hover —
                              and span the whole row, or the dead area beside
                              the label swallows the pointer. */}
                          <Stack sx={{ pointerEvents: "auto", width: "100%" }}>
                            {label}
                          </Stack>
                        </CustomTooltip>
                      ) : (
                        label
                      )}
                    </MenuItem>,
                  ];
                })}
              </Select>
            </FormControl>
          </Stack>

          {/* Chart + View toggles + Data table */}
          <Box
            sx={{
              flex: 1,
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: 1,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
            }}
          >
            {/* Bar chart — horizontal bars (left) + search/checkboxes (right) */}
            {isHorizontal && (previewLoading || previewPreparing) && (
              <Box
                sx={{
                  flex: 1,
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center",
                }}
              >
                <WidgetPreviewStatus
                  state={previewPreparing ? "preparing" : "loading"}
                />
              </Box>
            )}
            {isHorizontal && previewFailureBlocksRendering && (
              <Box sx={{ p: 2 }}>
                <WidgetPreviewStatus state="failed" onRetry={retryPreview} />
              </Box>
            )}
            {isHorizontal &&
              !previewLoading &&
              !previewPreparing &&
              !previewFailureBlocksRendering &&
              previewSeries.length === 0 && (
                <Box
                  sx={{
                    flex: 1,
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                  }}
                >
                  <Typography variant="body2" color="text.secondary">
                    {emptyPreviewMessage}
                  </Typography>
                </Box>
              )}
            {isHorizontal &&
            previewSeries.length > 0 &&
            !previewLoading &&
            !previewPreparing &&
            !previewFailureBlocksRendering
              ? (() => {
                  const maxVal = Math.max(
                    ...barData.series[0].data.map(Math.abs),
                    1,
                  );
                  const allIndicesBar = previewSeries.map((_, idx) => idx);
                  const toggleBarSeries = (si) => {
                    const current = visibleSeries || new Set(allIndicesBar);
                    const next = new Set(current);
                    if (next.has(si)) next.delete(si);
                    else next.add(si);
                    setVisibleSeries(
                      next.size === previewSeries.length ? null : next,
                    );
                  };
                  const allBarChecked =
                    visibleSeries === null ||
                    visibleSeries?.size === previewSeries.length;
                  const someBarChecked =
                    visibleSeries !== null &&
                    visibleSeries.size > 0 &&
                    visibleSeries.size < previewSeries.length;
                  return (
                    <Box
                      sx={{
                        flex: 1,
                        display: "flex",
                        flexDirection: "column",
                        overflow: "hidden",
                      }}
                    >
                      {/* Legend */}
                      <Stack
                        direction="row"
                        gap={2}
                        flexWrap="wrap"
                        justifyContent="center"
                        sx={{ px: 2, pt: 2, pb: 1 }}
                      >
                        {chartSeries.map((s, i) => {
                          const color = getSeriesColor(s.name, seriesColorMap);
                          return (
                            <Stack
                              key={i}
                              direction="row"
                              alignItems="center"
                              gap={0.5}
                            >
                              <Box
                                sx={{
                                  width: 10,
                                  height: 10,
                                  borderRadius: "2px",
                                  bgcolor: color,
                                  flexShrink: 0,
                                }}
                              />
                              <Typography
                                variant="caption"
                                sx={{
                                  color: "text.secondary",
                                  fontWeight: 500,
                                  fontSize: "13px",
                                }}
                              >
                                {s.name}
                              </Typography>
                            </Stack>
                          );
                        })}
                      </Stack>
                      {/* Main row: bars left, checkboxes right */}
                      <Box
                        sx={{ flex: 1, display: "flex", overflow: "hidden" }}
                      >
                        {/* Bar table */}
                        <Box
                          sx={{
                            flex: 1,
                            display: "flex",
                            flexDirection: "column",
                            overflow: "hidden",
                          }}
                        >
                          {/* Column headers */}
                          <Box
                            sx={{
                              display: "flex",
                              alignItems: "center",
                              px: 2,
                              py: 1,
                              borderBottom: `1px solid ${theme.palette.divider}`,
                            }}
                          >
                            <Typography
                              variant="caption"
                              sx={{
                                width: 160,
                                minWidth: 160,
                                flexShrink: 0,
                                fontWeight: 600,
                                color: "text.secondary",
                                fontSize: "12px",
                              }}
                            >
                              Metric
                            </Typography>
                            <Typography
                              variant="caption"
                              sx={{
                                flex: 1,
                                fontWeight: 600,
                                color: "text.secondary",
                                fontSize: "12px",
                              }}
                            >
                              Value
                            </Typography>
                          </Box>
                          {/* Bar rows */}
                          <Box sx={{ flex: 1, overflow: "auto", px: 2 }}>
                            {barData.rows.map((row, i) => {
                              const val = row.numericValue;
                              const color = getSeriesColor(
                                row.name || row.label,
                                seriesColorMap,
                              );
                              const pct =
                                maxVal > 0 ? (Math.abs(val) / maxVal) * 100 : 0;
                              const fmtVal =
                                row.value == null
                                  ? "—"
                                  : formatValFn(row.value);
                              return (
                                <Box
                                  key={i}
                                  sx={{
                                    display: "flex",
                                    alignItems: "center",
                                    py: 1.2,
                                    borderBottom: `1px solid ${theme.palette.divider}`,
                                    "&:last-child": { borderBottom: "none" },
                                  }}
                                >
                                  <Typography
                                    variant="body2"
                                    sx={{
                                      width: 160,
                                      minWidth: 160,
                                      flexShrink: 0,
                                      fontWeight: 500,
                                      fontSize: "13px",
                                      color: "text.primary",
                                      overflow: "hidden",
                                      textOverflow: "ellipsis",
                                      whiteSpace: "nowrap",
                                      pr: 1.5,
                                    }}
                                    title={
                                      chartSeries[i]?.name ||
                                      barData.categories[i]
                                    }
                                  >
                                    {barData.categories[i]}
                                  </Typography>
                                  <Box
                                    sx={{
                                      flex: 1,
                                      display: "flex",
                                      alignItems: "center",
                                      gap: 1.5,
                                    }}
                                  >
                                    <Box
                                      sx={{
                                        flex: 1,
                                        height: 22,
                                        bgcolor: isDark
                                          ? "rgba(255,255,255,0.04)"
                                          : "rgba(0,0,0,0.02)",
                                        borderRadius: "4px",
                                        overflow: "hidden",
                                      }}
                                    >
                                      <Box
                                        sx={{
                                          height: "100%",
                                          width: `${Math.max(pct, 1)}%`,
                                          bgcolor: color,
                                          borderRadius: "4px",
                                          transition: "width 0.4s ease",
                                        }}
                                      />
                                    </Box>
                                    <Typography
                                      variant="body2"
                                      sx={{
                                        minWidth: 70,
                                        textAlign: "right",
                                        fontWeight: 600,
                                        fontSize: "13px",
                                        color: "text.primary",
                                        fontVariantNumeric: "tabular-nums",
                                        flexShrink: 0,
                                      }}
                                    >
                                      {fmtVal}
                                    </Typography>
                                  </Box>
                                </Box>
                              );
                            })}
                          </Box>
                        </Box>
                        {/* Right sidebar: search + checkboxes */}
                        <Box
                          sx={{
                            width: 200,
                            minWidth: 200,
                            borderLeft: `1px solid ${theme.palette.divider}`,
                            display: "flex",
                            flexDirection: "column",
                            overflow: "hidden",
                          }}
                        >
                          {/* Search */}
                          <Box sx={{ px: 1.5, pt: 1.5, pb: 1 }}>
                            <TextField
                              size="small"
                              fullWidth
                              placeholder="Search"
                              value={tableSearch}
                              onChange={(e) => setTableSearch(e.target.value)}
                              InputProps={{
                                startAdornment: (
                                  <InputAdornment position="start">
                                    <Iconify
                                      icon="eva:search-fill"
                                      width={16}
                                      sx={{ color: "text.disabled" }}
                                    />
                                  </InputAdornment>
                                ),
                              }}
                              sx={{
                                "& .MuiOutlinedInput-root": {
                                  fontSize: "12px",
                                },
                              }}
                            />
                          </Box>
                          {/* Metric count */}
                          <Box sx={{ px: 1.5, pb: 0.5 }}>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontSize: "11px" }}
                            >
                              Metric {chartSeries.length} of{" "}
                              {previewSeries.length}
                            </Typography>
                          </Box>
                          {/* Select all */}
                          <Box
                            sx={{
                              px: 1.5,
                              pb: 0.5,
                              display: "flex",
                              alignItems: "center",
                              gap: 0.5,
                              cursor: "pointer",
                            }}
                            onClick={() => {
                              if (allBarChecked) setVisibleSeries(new Set());
                              else setVisibleSeries(null);
                            }}
                          >
                            <Checkbox
                              size="small"
                              checked={allBarChecked}
                              indeterminate={someBarChecked}
                              sx={{ p: 0 }}
                            />
                            <Typography
                              variant="body2"
                              sx={{ fontWeight: 600, fontSize: "13px" }}
                            >
                              Select all
                            </Typography>
                          </Box>
                          {/* Individual checkboxes */}
                          <Box sx={{ flex: 1, overflow: "auto", px: 0.5 }}>
                            {previewSeries.map((s, si) => {
                              if (
                                tableSearch.trim() &&
                                !s.name
                                  .toLowerCase()
                                  .includes(tableSearch.toLowerCase())
                              )
                                return null;
                              const checked =
                                visibleSeries === null ||
                                visibleSeries?.has(si);
                              const color = getSeriesColor(
                                s.name,
                                seriesColorMap,
                              );
                              return (
                                <Box
                                  key={si}
                                  sx={{
                                    display: "flex",
                                    alignItems: "center",
                                    gap: 0.5,
                                    px: 1,
                                    py: 0.6,
                                    borderRadius: 0.5,
                                    cursor: "pointer",
                                    "&:hover": { bgcolor: "action.hover" },
                                  }}
                                  onClick={() => toggleBarSeries(si)}
                                >
                                  <Checkbox
                                    size="small"
                                    checked={checked}
                                    tabIndex={-1}
                                    sx={{
                                      p: 0,
                                      color,
                                      "&.Mui-checked": { color },
                                    }}
                                  />
                                  <Typography
                                    variant="body2"
                                    sx={{
                                      fontSize: "12px",
                                      fontWeight: 500,
                                      overflow: "hidden",
                                      textOverflow: "ellipsis",
                                      whiteSpace: "nowrap",
                                    }}
                                  >
                                    {s.name}
                                  </Typography>
                                </Box>
                              );
                            })}
                          </Box>
                        </Box>
                      </Box>
                    </Box>
                  );
                })()
              : null}

            {/* Chart area (non-bar) */}
            {!isHorizontal && showChart && (
              <Box
                sx={{
                  height:
                    viewMode === "chart" ? "calc(100% - 40px)" : chartHeight,
                  minHeight: viewMode === "table" ? 0 : 150,
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  p: 2,
                  overflow: "hidden",
                }}
              >
                {previewLoading || previewPreparing ? (
                  <WidgetPreviewStatus
                    state={previewPreparing ? "preparing" : "loading"}
                  />
                ) : previewFailureBlocksRendering ? (
                  <WidgetPreviewStatus state="failed" onRetry={retryPreview} />
                ) : previewSeries.length > 0 ? (
                  <Box sx={{ width: "100%", height: "100%" }}>
                    {isMetricCard || (isPie && !pieHasBreakdown) ? (
                      <Stack
                        direction="row"
                        gap={3}
                        justifyContent="center"
                        alignItems="center"
                        sx={{ height: "100%" }}
                      >
                        {chartSeries.map((s, i) => {
                          // Honour the metric's own aggregation and unit, so
                          // this matches the saved widget exactly.
                          const value = getSeriesScalar(s.data, s.aggregation);
                          const cellConfig = s.unit
                            ? {
                                ...leftAxisFormatConfig,
                                ...getUnitRendering(s.unit),
                              }
                            : leftAxisFormatConfig;
                          return (
                            <Box key={i} sx={{ textAlign: "center" }}>
                              <Typography
                                variant="h2"
                                sx={{
                                  color: chartColors[i % chartColors.length],
                                }}
                              >
                                {value == null
                                  ? "—"
                                  : formatValueWithConfig(value, cellConfig, {
                                      fallbackDecimals: autoDecimals,
                                    })}
                              </Typography>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                {s.name}
                              </Typography>
                            </Box>
                          );
                        })}
                      </Stack>
                    ) : isTable ? (
                      /* Data table — Time as rows, Segments as columns */
                      (() => {
                        const timeData = chartSeries[0]?.data || [];
                        const dateFmt =
                          granularity === "minute"
                            ? "HH:mm"
                            : granularity === "hour"
                              ? "MMM d, HH:mm"
                              : granularity === "month"
                                ? "MMM yyyy"
                                : "MMM d";
                        return (
                          <Box
                            sx={{
                              overflow: "auto",
                              width: "100%",
                              flex: 1,
                              minHeight: 0,
                            }}
                          >
                            <table
                              style={{
                                width: "100%",
                                borderCollapse: "collapse",
                                fontSize: "13px",
                              }}
                            >
                              <thead>
                                <tr>
                                  <th
                                    style={{
                                      textAlign: "left",
                                      padding: "8px 12px",
                                      fontWeight: 600,
                                      fontSize: "12px",
                                      color: theme.palette.text.secondary,
                                      borderBottom: `2px solid ${theme.palette.divider}`,
                                      position: "sticky",
                                      top: 0,
                                      left: 0,
                                      background:
                                        theme.palette.background.paper,
                                      zIndex: 3,
                                      minWidth: 120,
                                    }}
                                  >
                                    Time
                                  </th>
                                  {chartSeries.map((s, i) => (
                                    <th
                                      key={i}
                                      style={{
                                        textAlign: "right",
                                        padding: "8px 12px",
                                        fontWeight: 500,
                                        fontSize: "12px",
                                        color: theme.palette.text.secondary,
                                        borderBottom: `2px solid ${theme.palette.divider}`,
                                        position: "sticky",
                                        top: 0,
                                        background:
                                          theme.palette.background.paper,
                                        zIndex: 2,
                                        whiteSpace: "nowrap",
                                      }}
                                    >
                                      <span
                                        style={{
                                          display: "inline-flex",
                                          alignItems: "center",
                                          gap: 4,
                                        }}
                                      >
                                        <Box
                                          component="span"
                                          sx={{
                                            width: 8,
                                            height: 8,
                                            borderRadius: "2px",
                                            bgcolor: getSeriesColor(
                                              s.name,
                                              seriesColorMap,
                                            ),
                                            display: "inline-block",
                                          }}
                                        />
                                        {s.name}
                                      </span>
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {timeData.map((pt, ri) => {
                                  const hasData = chartSeries.some(
                                    (s) =>
                                      s.data[ri]?.y != null &&
                                      s.data[ri].y !== 0,
                                  );
                                  return (
                                    <tr
                                      key={ri}
                                      style={{
                                        borderBottom: `1px solid ${theme.palette.divider}`,
                                        opacity: hasData ? 1 : 0.5,
                                      }}
                                    >
                                      <td
                                        style={{
                                          padding: "6px 12px",
                                          fontWeight: 500,
                                          color: theme.palette.text.primary,
                                          position: "sticky",
                                          left: 0,
                                          background:
                                            theme.palette.background.paper,
                                          zIndex: 1,
                                          whiteSpace: "nowrap",
                                        }}
                                      >
                                        {format(new Date(pt.x), dateFmt)}
                                      </td>
                                      {chartSeries.map((s, si) => {
                                        const val = s.data[ri]?.y;
                                        return (
                                          <td
                                            key={si}
                                            style={{
                                              textAlign: "right",
                                              padding: "6px 12px",
                                              fontVariantNumeric:
                                                "tabular-nums",
                                              color:
                                                val && val !== 0
                                                  ? theme.palette.text.primary
                                                  : theme.palette.text.disabled,
                                            }}
                                          >
                                            {val != null
                                              ? val >= 1000
                                                ? val.toLocaleString(
                                                    undefined,
                                                    {
                                                      maximumFractionDigits: 0,
                                                    },
                                                  )
                                                : val % 1 === 0
                                                  ? val
                                                  : val.toFixed(2)
                                              : "-"}
                                          </td>
                                        );
                                      })}
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </Box>
                        );
                      })()
                    ) : isPie ? (
                      <WidgetPieCharts
                        groups={pieGroups}
                        colorFor={pieColorFor}
                        baseFormatConfig={leftAxisFormatConfig}
                        fallbackDecimals={autoDecimals}
                      />
                    ) : outOfRangeWarning ? (
                      <Box
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          width: "100%",
                          height: "100%",
                          px: 2,
                        }}
                      >
                        <Alert severity="warning" sx={{ width: "100%" }}>
                          {outOfRangeWarning}
                        </Alert>
                      </Box>
                    ) : (
                      <Box
                        ref={lineChartRef}
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          width: "100%",
                          height: "100%",
                        }}
                      >
                        {chartSeries.length > 1 && (
                          <ChartLegend
                            items={chartSeries.map((s) => s.name)}
                            colors={chartColors}
                            onHoverSeries={handleLegendHover}
                            onLeaveSeries={handleLegendLeave}
                          />
                        )}
                        <Box sx={{ flex: 1, minHeight: 0 }}>
                          <ReactApexChart
                            key={`${axisConfig.leftY.unit}-${axisConfig.leftY.prefixSuffix}-${axisConfig.leftY.abbreviation}-${axisConfig.leftY.decimals}-${axisConfig.leftY.outOfBounds}-${axisConfig.rightY.unit}-${axisConfig.rightY.prefixSuffix}-${axisConfig.rightY.abbreviation}-${axisConfig.rightY.decimals}-${axisConfig.rightY.outOfBounds}-${JSON.stringify(axisConfig.seriesAxis)}-${axisConfig.rightY.visible}`}
                            options={chartOptions}
                            series={plottedChartSeries}
                            type={apexType}
                            height="100%"
                          />
                        </Box>
                      </Box>
                    )}
                  </Box>
                ) : (
                  <Typography variant="body2" color="text.secondary">
                    {emptyPreviewMessage}
                  </Typography>
                )}
              </Box>
            )}

            {/* Divider bar with toggle buttons + drag handle */}
            {previewSeries.length > 0 &&
              !isMetricCard &&
              !isHorizontal &&
              !isTable && (
                <Box
                  onMouseDown={handleDragStart}
                  sx={{
                    position: "relative",
                    flexShrink: 0,
                    cursor: "row-resize",
                    py: 0.5,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    "&:hover .divider-line": {
                      bgcolor: "primary.main",
                      opacity: 1,
                    },
                  }}
                >
                  {/* Divider line — subtle by default, colored on hover */}
                  <Box
                    className="divider-line"
                    sx={{
                      position: "absolute",
                      left: 0,
                      right: 0,
                      top: "50%",
                      transform: "translateY(-50%)",
                      height: 3,
                      bgcolor: "divider",
                      opacity: 1,
                      transition: "background-color 0.15s, opacity 0.15s",
                      ...(isDragging && {
                        bgcolor: "primary.main",
                        opacity: 1,
                      }),
                    }}
                  />
                  {/* Toggle buttons on top of the line */}
                  <Stack
                    direction="row"
                    sx={{
                      position: "relative",
                      zIndex: 1,
                      border: `1px solid ${theme.palette.divider}`,
                      borderRadius: "8px",
                      overflow: "hidden",
                      bgcolor: "background.paper",
                      boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
                    }}
                  >
                    {[
                      {
                        mode: "table",
                        icon: "mdi:table",
                        height: 0,
                        tip: "Table",
                      },
                      {
                        mode: "split-table",
                        icon: "mdi:page-layout-header",
                        height: 180,
                        tip: "Chart + Table",
                      },
                      {
                        mode: "split-chart",
                        icon: "mdi:page-layout-body",
                        height: 350,
                        tip: "Chart (expanded)",
                      },
                      {
                        mode: "chart",
                        icon: "mdi:chart-line",
                        height: 600,
                        tip: "Chart only",
                      },
                    ].map(({ mode, icon, height, tip }) => (
                      <Tooltip key={mode} title={tip} placement="top">
                        <IconButton
                          size="small"
                          onClick={(e) => {
                            e.stopPropagation();
                            setViewMode(mode);
                            setChartHeight(height);
                          }}
                          onMouseDown={(e) => e.stopPropagation()}
                          sx={{
                            borderRadius: 0,
                            px: 1.2,
                            py: 0.6,
                            bgcolor:
                              viewMode === mode
                                ? "action.selected"
                                : "transparent",
                            "&:hover": {
                              bgcolor:
                                viewMode === mode
                                  ? "action.selected"
                                  : "action.hover",
                            },
                          }}
                        >
                          <Iconify
                            icon={icon}
                            width={18}
                            sx={{
                              color:
                                viewMode === mode
                                  ? "text.primary"
                                  : "text.disabled",
                            }}
                          />
                        </IconButton>
                      </Tooltip>
                    ))}
                  </Stack>
                </Box>
              )}

            {/* Pie chart: summary columns below the chart */}
            {/* Pie chart: one summary column per metric */}
            {isPie && pieGroups.length > 0 && (
              <Box
                sx={{
                  flex: 1,
                  display: "flex",
                  alignItems: "flex-start",
                  justifyContent: "center",
                  borderTop: `1px solid ${theme.palette.divider}`,
                  overflow: "auto",
                }}
              >
                {pieGroups.map((g, i) => (
                  <Box
                    key={g.metricIndex}
                    sx={{
                      flex: 1,
                      textAlign: "center",
                      py: 2,
                      px: 1.5,
                      borderRight:
                        i < pieGroups.length - 1
                          ? `1px solid ${theme.palette.divider}`
                          : "none",
                    }}
                  >
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: 600,
                        fontSize: "13px",
                        color: "text.primary",
                      }}
                    >
                      {LETTER_LABELS[g.metricIndex]} {g.metricName}
                    </Typography>
                    <Typography
                      variant="caption"
                      sx={{
                        color: "text.secondary",
                        fontSize: "11px",
                        display: "block",
                      }}
                    >
                      {g.aggregation} · {g.slices.length}{" "}
                      {g.slices.length === 1 ? "slice" : "slices"}
                    </Typography>
                  </Box>
                ))}
              </Box>
            )}

            {/* Data table — with inline checkboxes (not for bar/table/pie charts) */}
            {previewSeries.length > 0 &&
              !isMetricCard &&
              !isHorizontal &&
              !isTable &&
              !isPie &&
              (() => {
                const allIndices = previewSeries.map((_, i) => i);
                const allChecked =
                  visibleSeries === null ||
                  visibleSeries?.size === previewSeries.length;
                const someChecked =
                  visibleSeries !== null &&
                  visibleSeries.size > 0 &&
                  visibleSeries.size < previewSeries.length;
                // Show all time columns — the table scrolls horizontally
                const allDataPoints = previewSeries[0]?.data || [];
                const displayData = allDataPoints;
                const displayIndicesSet = new Set(
                  allDataPoints.map((_, i) => i),
                );

                const toggleSeries = (si) => {
                  const current = visibleSeries || new Set(allIndices);
                  const next = new Set(current);
                  if (next.has(si)) next.delete(si);
                  else next.add(si);
                  setVisibleSeries(
                    next.size === previewSeries.length ? null : next,
                  );
                };

                // Filter table rows by search, sort by average descending
                const tableRows = allIndices
                  .filter(
                    (i) =>
                      !tableSearch.trim() ||
                      previewSeries[i].name
                        .toLowerCase()
                        .includes(tableSearch.toLowerCase()),
                  )
                  .sort((a, b) => {
                    const avgA = getSeriesScalar(
                      previewSeries[a].data,
                      previewSeries[a].aggregation,
                    );
                    const avgB = getSeriesScalar(
                      previewSeries[b].data,
                      previewSeries[b].aggregation,
                    );
                    const scoreA =
                      avgA == null ? Number.NEGATIVE_INFINITY : avgA;
                    const scoreB =
                      avgB == null ? Number.NEGATIVE_INFINITY : avgB;
                    return scoreB - scoreA;
                  });

                return (
                  <Box
                    sx={{
                      flex: 1,
                      display: "flex",
                      flexDirection: "column",
                      overflow: "hidden",
                    }}
                  >
                    {/* Search bar */}
                    <Box
                      sx={{
                        px: 2,
                        py: 1,
                        display: "flex",
                        alignItems: "center",
                        gap: 1.5,
                        borderBottom: `1px solid ${theme.palette.divider}`,
                      }}
                    >
                      <TextField
                        size="small"
                        placeholder="Search"
                        value={tableSearch}
                        onChange={(e) => setTableSearch(e.target.value)}
                        InputProps={{
                          startAdornment: (
                            <InputAdornment position="start">
                              <Iconify
                                icon="eva:search-fill"
                                width={16}
                                sx={{ color: "text.disabled" }}
                              />
                            </InputAdornment>
                          ),
                        }}
                        sx={{
                          flex: 1,
                          maxWidth: 300,
                          "& .MuiOutlinedInput-root": { fontSize: "13px" },
                        }}
                      />
                    </Box>
                    {/* Table */}
                    <Box sx={{ flex: 1, overflow: "auto" }}>
                      <table
                        style={{
                          width: "100%",
                          borderCollapse: "collapse",
                          fontSize: "13px",
                          tableLayout: "auto",
                        }}
                      >
                        <thead>
                          <tr>
                            <th
                              style={{
                                textAlign: "left",
                                padding: "8px 12px",
                                color: theme.palette.text.secondary,
                                fontWeight: 500,
                                fontSize: "12px",
                                position: "sticky",
                                top: 0,
                                left: 0,
                                background: theme.palette.background.paper,
                                zIndex: 3,
                                minWidth: 220,
                                borderBottom: `1px solid ${theme.palette.divider}`,
                              }}
                            >
                              <span
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 6,
                                }}
                              >
                                <Checkbox
                                  size="small"
                                  checked={allChecked}
                                  indeterminate={someChecked}
                                  onChange={() => {
                                    if (allChecked) setVisibleSeries(new Set());
                                    else setVisibleSeries(null);
                                  }}
                                  sx={{ p: 0, mr: 0.25 }}
                                />
                                <span
                                  style={{
                                    fontWeight: 600,
                                    color: theme.palette.text.primary,
                                  }}
                                >
                                  Metric
                                </span>
                                <span
                                  style={{ color: theme.palette.text.disabled }}
                                >
                                  {previewSeries.length}
                                </span>
                              </span>
                            </th>
                            <th
                              style={{
                                textAlign: "right",
                                padding: "8px 12px",
                                color: theme.palette.text.secondary,
                                fontWeight: 500,
                                fontSize: "12px",
                                minWidth: 90,
                                borderLeft: `1px solid ${theme.palette.divider}`,
                                borderBottom: `1px solid ${theme.palette.divider}`,
                                position: "sticky",
                                top: 0,
                                background: theme.palette.background.paper,
                                zIndex: 2,
                              }}
                            >
                              {aggColumnLabel}
                            </th>
                            {displayData.map((pt, ci) => (
                              <th
                                key={ci}
                                style={{
                                  textAlign: "right",
                                  padding: "8px 12px",
                                  color: theme.palette.text.secondary,
                                  fontWeight: 400,
                                  fontSize: "12px",
                                  whiteSpace: "nowrap",
                                  minWidth: 80,
                                  borderBottom: `1px solid ${theme.palette.divider}`,
                                  position: "sticky",
                                  top: 0,
                                  background: theme.palette.background.paper,
                                  zIndex: 2,
                                }}
                              >
                                {format(
                                  new Date(pt.x),
                                  granularity === "minute"
                                    ? "HH:mm"
                                    : granularity === "hour"
                                      ? "MMM d HH:mm"
                                      : "MMM d",
                                )}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {tableRows.map((si) => {
                            const s = previewSeries[si];
                            const checked =
                              visibleSeries === null || visibleSeries.has(si);
                            const avg = getSeriesScalar(s.data, s.aggregation);
                            const color = getSeriesColor(
                              s.name,
                              seriesColorMap,
                            );
                            return (
                              <tr
                                key={si}
                                style={{
                                  borderBottom: `1px solid ${theme.palette.divider}`,
                                }}
                              >
                                <td
                                  style={{
                                    padding: "8px 12px",
                                    position: "sticky",
                                    left: 0,
                                    background: theme.palette.background.paper,
                                    zIndex: 1,
                                    cursor: "pointer",
                                  }}
                                  onClick={() => toggleSeries(si)}
                                >
                                  <span
                                    style={{
                                      display: "inline-flex",
                                      alignItems: "center",
                                      gap: 6,
                                    }}
                                  >
                                    <Checkbox
                                      size="small"
                                      checked={checked}
                                      tabIndex={-1}
                                      sx={{
                                        p: 0,
                                        color: color,
                                        "&.Mui-checked": { color },
                                      }}
                                    />
                                    <span
                                      style={{
                                        color: theme.palette.text.primary,
                                        fontWeight: 500,
                                        fontSize: "13px",
                                      }}
                                    >
                                      {s.name}
                                    </span>
                                  </span>
                                </td>
                                <td
                                  style={{
                                    textAlign: "right",
                                    padding: "8px 12px",
                                    color: theme.palette.text.primary,
                                    fontWeight: 500,
                                    fontVariantNumeric: "tabular-nums",
                                    borderLeft: `1px solid ${theme.palette.divider}`,
                                  }}
                                >
                                  {avg == null
                                    ? "—"
                                    : formatValueWithConfig(
                                        avg,
                                        s.unit
                                          ? {
                                              ...leftAxisFormatConfig,
                                              ...getUnitRendering(s.unit),
                                            }
                                          : leftAxisFormatConfig,
                                        { fallbackDecimals: autoDecimals },
                                      )}
                                </td>
                                {s.data.map((pt, ci) => {
                                  if (!displayIndicesSet.has(ci)) return null;
                                  return (
                                    <td
                                      key={ci}
                                      style={{
                                        textAlign: "right",
                                        padding: "8px 12px",
                                        color: theme.palette.text.primary,
                                        fontVariantNumeric: "tabular-nums",
                                      }}
                                    >
                                      {pt.y != null
                                        ? pt.y >= 1000
                                          ? pt.y.toLocaleString(undefined, {
                                              maximumFractionDigits: 0,
                                            })
                                          : pt.y % 1 === 0
                                            ? pt.y
                                            : pt.y.toFixed(2)
                                        : "-"}
                                    </td>
                                  );
                                })}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </Box>
                  </Box>
                );
              })()}
          </Box>
        </Box>

        {/* Right panel — config surface, hidden for read-only (viewer) users */}
        {!isReadOnly && (
          <Box
            sx={{
              width: 320,
              minWidth: 320,
              borderLeft: `1px solid ${theme.palette.divider}`,
              display: "flex",
              flexDirection: "column",
              overflow: "auto",
            }}
          >
            {/* Tabs */}
            <Tabs
              value={rightTab}
              onChange={(_, v) => setRightTab(v)}
              sx={{ px: 2, minHeight: 40 }}
            >
              <Tab
                label="Query"
                sx={{ minHeight: 40, textTransform: "none" }}
              />
              <Tab
                label="Chart"
                sx={{ minHeight: 40, textTransform: "none" }}
              />
            </Tabs>
            <Divider />

            {rightTab === 0 && (
              <Box
                sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}
              >
                {/* Metric section */}
                <Box>
                  <Tooltip
                    placement="left"
                    arrow
                    componentsProps={{
                      tooltip: {
                        sx: {
                          bgcolor: isDark ? "#1a1a2e" : "#fff",
                          borderRadius: 2,
                          p: 2,
                          maxWidth: 180,
                          boxShadow: isDark
                            ? "0 4px 20px rgba(0,0,0,0.5)"
                            : "0 4px 20px rgba(0,0,0,0.12)",
                          border: isDark ? "none" : "1px solid",
                          borderColor: isDark ? "transparent" : "divider",
                        },
                      },
                      arrow: {
                        sx: {
                          color: isDark ? "#1a1a2e" : "#fff",
                          "&::before": {
                            border: isDark ? "none" : "1px solid",
                            borderColor: isDark ? "transparent" : "divider",
                          },
                        },
                      },
                    }}
                    title={
                      <Box sx={{ textAlign: "center" }}>
                        <svg
                          width="120"
                          height="90"
                          viewBox="0 0 120 90"
                          fill="none"
                          xmlns="http://www.w3.org/2000/svg"
                        >
                          {/* Line chart */}
                          <line
                            x1="15"
                            y1="75"
                            x2="15"
                            y2="10"
                            stroke={
                              isDark
                                ? "rgba(255,255,255,0.15)"
                                : "rgba(0,0,0,0.1)"
                            }
                            strokeWidth="1"
                          />
                          <line
                            x1="15"
                            y1="75"
                            x2="110"
                            y2="75"
                            stroke={
                              isDark
                                ? "rgba(255,255,255,0.15)"
                                : "rgba(0,0,0,0.1)"
                            }
                            strokeWidth="1"
                          />
                          {/* Grid lines */}
                          <line
                            x1="15"
                            y1="55"
                            x2="110"
                            y2="55"
                            stroke={
                              isDark
                                ? "rgba(255,255,255,0.06)"
                                : "rgba(0,0,0,0.04)"
                            }
                            strokeWidth="1"
                            strokeDasharray="3 3"
                          />
                          <line
                            x1="15"
                            y1="35"
                            x2="110"
                            y2="35"
                            stroke={
                              isDark
                                ? "rgba(255,255,255,0.06)"
                                : "rgba(0,0,0,0.04)"
                            }
                            strokeWidth="1"
                            strokeDasharray="3 3"
                          />
                          {/* Line 1 - purple */}
                          <polyline
                            points="20,60 35,45 50,50 65,28 80,32 95,18 105,22"
                            fill="none"
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                          {/* Line 2 - teal */}
                          <polyline
                            points="20,68 35,62 50,58 65,48 80,52 95,42 105,45"
                            fill="none"
                            stroke={isDark ? "#5BE49B" : "#22C55E"}
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                          {/* Data points - purple */}
                          <circle
                            cx="20"
                            cy="60"
                            r="2.5"
                            fill={isDark ? "#916BFF" : "#7C4DFF"}
                          />
                          <circle
                            cx="50"
                            cy="50"
                            r="2.5"
                            fill={isDark ? "#916BFF" : "#7C4DFF"}
                          />
                          <circle
                            cx="65"
                            cy="28"
                            r="2.5"
                            fill={isDark ? "#916BFF" : "#7C4DFF"}
                          />
                          <circle
                            cx="95"
                            cy="18"
                            r="2.5"
                            fill={isDark ? "#916BFF" : "#7C4DFF"}
                          />
                          {/* Data points - teal */}
                          <circle
                            cx="20"
                            cy="68"
                            r="2.5"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="50"
                            cy="58"
                            r="2.5"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="65"
                            cy="48"
                            r="2.5"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="95"
                            cy="42"
                            r="2.5"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                        </svg>
                        <Typography
                          variant="caption"
                          sx={{
                            color: isDark
                              ? "rgba(255,255,255,0.7)"
                              : "text.secondary",
                            mt: 0.5,
                            display: "block",
                            lineHeight: 1.3,
                          }}
                        >
                          Choose what to measure and track.
                        </Typography>
                      </Box>
                    }
                  >
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                      onClick={(e) => {
                        if (metrics.length < 5) openPicker(e, "metric");
                      }}
                      sx={{
                        cursor: metrics.length >= 5 ? "default" : "pointer",
                        borderRadius: 1,
                        px: 1,
                        py: 0.5,
                        mx: -1,
                        transition: "background-color 0.15s",
                        "&:hover":
                          metrics.length < 5
                            ? {
                                bgcolor: (t) =>
                                  t.palette.mode === "dark"
                                    ? "rgba(145, 107, 255, 0.12)"
                                    : "rgba(105, 65, 198, 0.08)",
                                "& .metric-section-title": {
                                  color: "primary.main",
                                },
                              }
                            : {},
                      }}
                    >
                      <Typography
                        className="metric-section-title"
                        variant="body2"
                        fontWeight="fontWeightSemiBold"
                        sx={{ transition: "color 0.15s" }}
                      >
                        Metric
                        <Typography component="span" color="error.main">
                          *
                        </Typography>
                      </Typography>
                      <Iconify
                        icon="mdi:plus"
                        width={18}
                        sx={{
                          color:
                            metrics.length >= 5
                              ? "text.disabled"
                              : "text.secondary",
                        }}
                      />
                    </Stack>
                  </Tooltip>

                  {/* Added metrics */}
                  {metrics.map((m, i) => (
                    <Box
                      key={i}
                      sx={{
                        mt: 1,
                        p: 1.5,
                        border: `1px solid ${theme.palette.divider}`,
                        borderRadius: 1,
                        "&:hover .metric-hover-action": {
                          opacity: 1,
                        },
                      }}
                    >
                      <Stack direction="row" alignItems="center" gap={1}>
                        <Chip
                          label={LETTER_LABELS[i]}
                          size="small"
                          variant="outlined"
                          sx={{
                            minWidth: 24,
                            height: 24,
                            fontSize: "12px",
                            fontWeight: 600,
                            "& .MuiChip-label": {
                              paddingLeft: "0px !important",
                              paddingRight: "0px !important",
                              overflow: "visible !important",
                              textOverflow: "clip !important",
                            },
                          }}
                        />
                        <Iconify
                          icon={METRIC_TYPE_ICONS[m.type] || "mdi:cog-outline"}
                          width={16}
                          sx={{ color: "text.secondary" }}
                        />
                        <Typography
                          variant="body2"
                          noWrap
                          title={m.name}
                          sx={{
                            flex: 1,
                            cursor: "pointer",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            maxWidth: 160,
                            "&:hover": { color: "primary.main" },
                          }}
                          onClick={(e) => openPicker(e, "metric", i)}
                        >
                          {m.name}
                        </Typography>
                        {m._linkedAgents && (
                          <Tooltip
                            title={`Linked to observability: ${m._linkedAgents}`}
                          >
                            <Chip
                              label="Linked"
                              size="small"
                              color="info"
                              variant="outlined"
                              sx={{
                                height: 20,
                                fontSize: "10px",
                                "& .MuiChip-label": { px: 0.75 },
                              }}
                            />
                          </Tooltip>
                        )}
                        <Tooltip title="Add filter to this metric">
                          <IconButton
                            className="metric-hover-action"
                            size="small"
                            onClick={(e) =>
                              openPicker(e, "metric_filter", null, i)
                            }
                            sx={{
                              opacity: m.filters?.length > 0 ? 1 : 0,
                              transition: "opacity 0.15s",
                              color:
                                m.filters?.length > 0
                                  ? "primary.main"
                                  : "text.secondary",
                            }}
                          >
                            <Iconify icon="mdi:filter-outline" width={16} />
                          </IconButton>
                        </Tooltip>
                        <IconButton
                          className="metric-hover-action"
                          size="small"
                          onClick={() => handleRemoveMetric(i)}
                          sx={{
                            opacity: 0,
                            transition: "opacity 0.15s",
                          }}
                        >
                          <Iconify icon="mdi:close" width={14} />
                        </IconButton>
                      </Stack>
                      <AggregationPicker
                        value={
                          m.allowedAggregations?.length &&
                          !m.allowedAggregations.includes(m.aggregation)
                            ? m.allowedAggregations[0]
                            : m.aggregation
                        }
                        onChange={(val) =>
                          handleUpdateMetricAggregation(i, val)
                        }
                        theme={theme}
                        allowedAggregations={m.allowedAggregations}
                        extraOptions={
                          m.source === "datasets" ||
                          m.source === "simulation" ||
                          m.source === "all"
                            ? DATASET_EXTRA_AGGREGATIONS
                            : undefined
                        }
                      />

                      {/* Per-metric inline filters */}
                      {(m.filters || []).map((mf, fi) => {
                        const mfOps = getWidgetFilterOperators(mf.dataType);
                        const curMfOp = mfOps.find(
                          (o) => o.value === mf.operator,
                        );
                        return (
                          <Box
                            key={fi}
                            sx={{
                              mt: 1,
                              pl: 1,
                              borderLeft: `2px solid ${theme.palette.primary.main}`,
                            }}
                          >
                            <Stack
                              direction="row"
                              alignItems="center"
                              gap={0.5}
                            >
                              <Iconify
                                icon={
                                  METRIC_TYPE_ICONS[mf.type] ||
                                  "mdi:filter-outline"
                                }
                                width={14}
                                sx={{ color: "primary.main" }}
                              />
                              <Typography
                                variant="caption"
                                sx={{
                                  flex: 1,
                                  fontWeight: 500,
                                  cursor: "pointer",
                                  "&:hover": { color: "primary.main" },
                                }}
                                onClick={(e) =>
                                  openPicker(e, "metric_filter", fi, i)
                                }
                              >
                                {mf.name}
                              </Typography>
                              <IconButton
                                size="small"
                                onClick={() => handleRemoveMetricFilter(i, fi)}
                                sx={{ p: 0.25 }}
                              >
                                <Iconify icon="mdi:close" width={12} />
                              </IconButton>
                            </Stack>
                            <Stack
                              direction="row"
                              alignItems="center"
                              gap={0.5}
                              sx={{ mt: 0.5 }}
                            >
                              <FormControl size="small" sx={{ minWidth: 70 }}>
                                <Select
                                  value={mf.operator}
                                  onChange={(e) => {
                                    const newOp = e.target.value;
                                    const newDef = mfOps.find(
                                      (o) => o.value === newOp,
                                    );
                                    let newVal = mf.value;
                                    if (newDef?.noValue) newVal = "";
                                    else if (newDef?.multi && !curMfOp?.multi)
                                      newVal = [];
                                    else if (newDef?.range && !curMfOp?.range)
                                      newVal = ["", ""];
                                    else if (!newDef?.multi && curMfOp?.multi)
                                      newVal = "";
                                    else if (!newDef?.range && curMfOp?.range)
                                      newVal = "";
                                    handleUpdateMetricFilter(i, fi, {
                                      operator: newOp,
                                      value: newVal,
                                    });
                                  }}
                                  variant="standard"
                                  sx={{ fontSize: "12px" }}
                                >
                                  {mfOps.map((op) => (
                                    <MenuItem key={op.value} value={op.value}>
                                      {op.label}
                                    </MenuItem>
                                  ))}
                                </Select>
                              </FormControl>
                              {curMfOp?.noValue ? null : curMfOp?.multi ? (
                                <FilterValueLabel
                                  filter={mf}
                                  source={mf.source || "traces"}
                                  variant="caption"
                                  innerRef={(el) => {
                                    mfValueRefs.current[`${i}_${fi}`] = el;
                                  }}
                                  onClick={(e) => {
                                    setMfValueAnchor(e.currentTarget);
                                    setMfValueTarget({
                                      metricIdx: i,
                                      filterIdx: fi,
                                    });
                                  }}
                                />
                              ) : curMfOp?.range ? (
                                <Stack
                                  direction="row"
                                  alignItems="center"
                                  gap={0.5}
                                  sx={{ flex: 1 }}
                                >
                                  <TextField
                                    size="small"
                                    variant="standard"
                                    placeholder="Min"
                                    type="number"
                                    value={
                                      Array.isArray(mf.value)
                                        ? mf.value[0] ?? ""
                                        : ""
                                    }
                                    onChange={(e) => {
                                      const cur = Array.isArray(mf.value)
                                        ? [...mf.value]
                                        : ["", ""];
                                      cur[0] = e.target.value;
                                      handleUpdateMetricFilter(i, fi, {
                                        value: cur,
                                      });
                                    }}
                                    sx={{ flex: 1, fontSize: "12px" }}
                                  />
                                  <Typography
                                    variant="caption"
                                    color="text.secondary"
                                  >
                                    –
                                  </Typography>
                                  <TextField
                                    size="small"
                                    variant="standard"
                                    placeholder="Max"
                                    type="number"
                                    value={
                                      Array.isArray(mf.value)
                                        ? mf.value[1] ?? ""
                                        : ""
                                    }
                                    onChange={(e) => {
                                      const cur = Array.isArray(mf.value)
                                        ? [...mf.value]
                                        : ["", ""];
                                      cur[1] = e.target.value;
                                      handleUpdateMetricFilter(i, fi, {
                                        value: cur,
                                      });
                                    }}
                                    sx={{ flex: 1, fontSize: "12px" }}
                                  />
                                </Stack>
                              ) : mf.dataType === "boolean" ? (
                                <FormControl size="small" sx={{ flex: 1 }}>
                                  <Select
                                    displayEmpty
                                    value={
                                      mf.value === true
                                        ? "true"
                                        : mf.value === false
                                          ? "false"
                                          : mf.value ?? ""
                                    }
                                    onChange={(e) =>
                                      handleUpdateMetricFilter(i, fi, {
                                        value: e.target.value,
                                      })
                                    }
                                    variant="standard"
                                    renderValue={(value) => value || "Value"}
                                    sx={{ fontSize: "12px" }}
                                  >
                                    <MenuItem disabled value="">
                                      Value
                                    </MenuItem>
                                    <MenuItem value="true">true</MenuItem>
                                    <MenuItem value="false">false</MenuItem>
                                  </Select>
                                </FormControl>
                              ) : (
                                <TextField
                                  size="small"
                                  variant="standard"
                                  placeholder="Value"
                                  type={
                                    mf.dataType === "number" ? "number" : "text"
                                  }
                                  value={mf.value ?? ""}
                                  onChange={(e) =>
                                    handleUpdateMetricFilter(i, fi, {
                                      value: e.target.value,
                                    })
                                  }
                                  sx={{ flex: 1, fontSize: "12px" }}
                                />
                              )}
                            </Stack>
                            {fi < (m.filters || []).length - 1 && (
                              <Typography
                                variant="caption"
                                color="text.secondary"
                                sx={{
                                  display: "inline-block",
                                  mt: 0.5,
                                  px: 1,
                                  py: 0.25,
                                  borderRadius: 0.5,
                                  bgcolor: "action.hover",
                                  fontSize: "11px",
                                }}
                              >
                                And
                              </Typography>
                            )}
                          </Box>
                        );
                      })}
                    </Box>
                  ))}

                  {/* Empty metric slot */}
                  {metrics.length === 0 && (
                    <Box
                      sx={{
                        mt: 1,
                        p: 1.5,
                        border: `1px solid ${theme.palette.divider}`,
                        borderRadius: 1,
                        cursor: "pointer",
                        "&:hover": { borderColor: "primary.main" },
                      }}
                      onClick={(e) => openPicker(e, "metric")}
                    >
                      <Stack direction="row" alignItems="center" gap={0.75}>
                        <Iconify
                          icon="mdi:plus-circle-outline"
                          width={18}
                          sx={{ color: "primary.main" }}
                        />
                        <Typography variant="body2" color="primary.main">
                          Select Metric
                        </Typography>
                      </Stack>
                    </Box>
                  )}
                </Box>

                <Divider />

                {/* Filter section */}
                <Box>
                  <Tooltip
                    placement="left"
                    arrow
                    componentsProps={{
                      tooltip: {
                        sx: {
                          bgcolor: isDark ? "#1a1a2e" : "#fff",
                          borderRadius: 2,
                          p: 2,
                          maxWidth: 180,
                          boxShadow: isDark
                            ? "0 4px 20px rgba(0,0,0,0.5)"
                            : "0 4px 20px rgba(0,0,0,0.12)",
                          border: isDark ? "none" : "1px solid",
                          borderColor: isDark ? "transparent" : "divider",
                        },
                      },
                      arrow: {
                        sx: {
                          color: isDark ? "#1a1a2e" : "#fff",
                          "&::before": {
                            border: isDark ? "none" : "1px solid",
                            borderColor: isDark ? "transparent" : "divider",
                          },
                        },
                      },
                    }}
                    title={
                      <Box sx={{ textAlign: "center" }}>
                        <svg
                          width="120"
                          height="90"
                          viewBox="0 0 120 90"
                          fill="none"
                          xmlns="http://www.w3.org/2000/svg"
                        >
                          {/* Funnel shape */}
                          <rect
                            x="10"
                            y="10"
                            width="100"
                            height="16"
                            rx="3"
                            fill={
                              isDark
                                ? "rgba(145,107,255,0.25)"
                                : "rgba(105,65,198,0.1)"
                            }
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="1.5"
                          />
                          <rect
                            x="25"
                            y="34"
                            width="70"
                            height="16"
                            rx="3"
                            fill={
                              isDark
                                ? "rgba(145,107,255,0.4)"
                                : "rgba(105,65,198,0.18)"
                            }
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="1.5"
                          />
                          <rect
                            x="40"
                            y="58"
                            width="40"
                            height="16"
                            rx="3"
                            fill={
                              isDark
                                ? "rgba(145,107,255,0.6)"
                                : "rgba(105,65,198,0.28)"
                            }
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="1.5"
                          />
                          {/* Connecting lines */}
                          <line
                            x1="25"
                            y1="26"
                            x2="25"
                            y2="34"
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="1"
                            strokeDasharray="2 2"
                          />
                          <line
                            x1="95"
                            y1="26"
                            x2="95"
                            y2="34"
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="1"
                            strokeDasharray="2 2"
                          />
                          <line
                            x1="40"
                            y1="50"
                            x2="40"
                            y2="58"
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="1"
                            strokeDasharray="2 2"
                          />
                          <line
                            x1="80"
                            y1="50"
                            x2="80"
                            y2="58"
                            stroke={isDark ? "#916BFF" : "#7C4DFF"}
                            strokeWidth="1"
                            strokeDasharray="2 2"
                          />
                          {/* Data dots */}
                          <circle
                            cx="30"
                            cy="18"
                            r="2"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="50"
                            cy="18"
                            r="2"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="70"
                            cy="18"
                            r="2"
                            fill={isDark ? "#FF6B6B" : "#EF4444"}
                          />
                          <circle
                            cx="90"
                            cy="18"
                            r="2"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="40"
                            cy="42"
                            r="2"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="60"
                            cy="42"
                            r="2"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="80"
                            cy="42"
                            r="2"
                            fill={isDark ? "#FF6B6B" : "#EF4444"}
                          />
                          <circle
                            cx="52"
                            cy="66"
                            r="2"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                          <circle
                            cx="68"
                            cy="66"
                            r="2"
                            fill={isDark ? "#5BE49B" : "#22C55E"}
                          />
                        </svg>
                        <Typography
                          variant="caption"
                          sx={{
                            color: isDark
                              ? "rgba(255,255,255,0.7)"
                              : "text.secondary",
                            mt: 0.5,
                            display: "block",
                            lineHeight: 1.3,
                          }}
                        >
                          Filter to include or exclude specific data.
                        </Typography>
                      </Box>
                    }
                  >
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                      onClick={(e) => openPicker(e, "filter")}
                      sx={{
                        cursor: "pointer",
                        borderRadius: 1,
                        px: 1,
                        py: 0.5,
                        mx: -1,
                        transition: "background-color 0.15s",
                        "&:hover": {
                          bgcolor: (t) =>
                            t.palette.mode === "dark"
                              ? "rgba(145, 107, 255, 0.12)"
                              : "rgba(105, 65, 198, 0.08)",
                          "& .filter-section-title": {
                            color: "primary.main",
                          },
                        },
                      }}
                    >
                      <Typography
                        className="filter-section-title"
                        variant="body2"
                        fontWeight="fontWeightSemiBold"
                        sx={{ transition: "color 0.15s" }}
                      >
                        Filter
                      </Typography>
                      <Iconify
                        icon="mdi:plus"
                        width={18}
                        sx={{ color: "text.secondary" }}
                      />
                    </Stack>
                  </Tooltip>
                  {filters.map((f, i) => (
                    <Box
                      key={i}
                      sx={{
                        mt: 1,
                        p: 1.5,
                        border: `1px solid ${theme.palette.divider}`,
                        borderRadius: 1,
                        "&:hover .filter-hover-action": {
                          opacity: 1,
                        },
                      }}
                    >
                      <Stack direction="row" alignItems="center" gap={1}>
                        <Iconify
                          icon={
                            METRIC_TYPE_ICONS[f.type] || "mdi:filter-outline"
                          }
                          width={16}
                          sx={{ color: "text.secondary" }}
                        />
                        <Typography
                          variant="body2"
                          sx={{
                            flex: 1,
                            cursor: "pointer",
                            "&:hover": { color: "primary.main" },
                          }}
                          onClick={(e) => openPicker(e, "filter", i)}
                        >
                          {f.name || "Select attribute"}
                        </Typography>
                        <IconButton
                          className="filter-hover-action"
                          size="small"
                          onClick={() => handleRemoveFilter(i)}
                          sx={{
                            opacity: 0,
                            transition: "opacity 0.15s",
                          }}
                        >
                          <Iconify icon="mdi:close" width={14} />
                        </IconButton>
                      </Stack>
                      {f.name &&
                        (() => {
                          const ops = getWidgetFilterOperators(f.dataType);
                          const currentOp = ops.find(
                            (o) => o.value === f.operator,
                          );
                          return (
                            <Stack
                              direction="row"
                              alignItems="center"
                              gap={1}
                              sx={{ mt: 1 }}
                            >
                              <FormControl size="small" sx={{ minWidth: 80 }}>
                                <Select
                                  value={f.operator}
                                  onChange={(e) => {
                                    const updated = [...filters];
                                    const newOp = e.target.value;
                                    const newDef = ops.find(
                                      (o) => o.value === newOp,
                                    );
                                    let newVal = f.value;
                                    if (newDef?.noValue) newVal = "";
                                    else if (newDef?.multi && !currentOp?.multi)
                                      newVal = [];
                                    else if (newDef?.range && !currentOp?.range)
                                      newVal = ["", ""];
                                    else if (!newDef?.multi && currentOp?.multi)
                                      newVal = "";
                                    else if (!newDef?.range && currentOp?.range)
                                      newVal = "";
                                    updated[i] = {
                                      ...updated[i],
                                      operator: newOp,
                                      value: newVal,
                                    };
                                    setFilters(updated);
                                  }}
                                  variant="standard"
                                  sx={{ fontSize: "13px" }}
                                >
                                  {ops.map((op) => (
                                    <MenuItem key={op.value} value={op.value}>
                                      {op.label}
                                    </MenuItem>
                                  ))}
                                </Select>
                              </FormControl>
                              {currentOp?.noValue ? null : currentOp?.multi ? (
                                <FilterValueLabel
                                  filter={f}
                                  source={f.source || "traces"}
                                  variant="body2"
                                  innerRef={(el) => {
                                    filterValueRefs.current[i] = el;
                                  }}
                                  onClick={(e) => {
                                    setFilterValueAnchor(e.currentTarget);
                                    setFilterValueIndex(i);
                                    setFilterValueSearch("");
                                  }}
                                />
                              ) : currentOp?.range ? (
                                <Stack
                                  direction="row"
                                  alignItems="center"
                                  gap={0.5}
                                  sx={{ flex: 1 }}
                                >
                                  <TextField
                                    size="small"
                                    variant="standard"
                                    placeholder="Min"
                                    type="number"
                                    value={
                                      Array.isArray(f.value)
                                        ? f.value[0] ?? ""
                                        : ""
                                    }
                                    onChange={(e) => {
                                      const updated = [...filters];
                                      const cur = Array.isArray(f.value)
                                        ? [...f.value]
                                        : ["", ""];
                                      cur[0] = e.target.value;
                                      updated[i] = {
                                        ...updated[i],
                                        value: cur,
                                      };
                                      setFilters(updated);
                                    }}
                                    sx={{ flex: 1, fontSize: "13px" }}
                                  />
                                  <Typography
                                    variant="caption"
                                    color="text.secondary"
                                  >
                                    and
                                  </Typography>
                                  <TextField
                                    size="small"
                                    variant="standard"
                                    placeholder="Max"
                                    type="number"
                                    value={
                                      Array.isArray(f.value)
                                        ? f.value[1] ?? ""
                                        : ""
                                    }
                                    onChange={(e) => {
                                      const updated = [...filters];
                                      const cur = Array.isArray(f.value)
                                        ? [...f.value]
                                        : ["", ""];
                                      cur[1] = e.target.value;
                                      updated[i] = {
                                        ...updated[i],
                                        value: cur,
                                      };
                                      setFilters(updated);
                                    }}
                                    sx={{ flex: 1, fontSize: "13px" }}
                                  />
                                </Stack>
                              ) : f.dataType === "boolean" ? (
                                <FormControl size="small" sx={{ flex: 1 }}>
                                  <Select
                                    displayEmpty
                                    value={
                                      f.value === true
                                        ? "true"
                                        : f.value === false
                                          ? "false"
                                          : f.value ?? ""
                                    }
                                    onChange={(e) => {
                                      const updated = [...filters];
                                      updated[i] = {
                                        ...updated[i],
                                        value: e.target.value,
                                      };
                                      setFilters(updated);
                                    }}
                                    variant="standard"
                                    renderValue={(value) => value || "Value"}
                                    sx={{ fontSize: "13px" }}
                                  >
                                    <MenuItem disabled value="">
                                      Value
                                    </MenuItem>
                                    <MenuItem value="true">true</MenuItem>
                                    <MenuItem value="false">false</MenuItem>
                                  </Select>
                                </FormControl>
                              ) : (
                                <TextField
                                  size="small"
                                  variant="standard"
                                  placeholder="Value"
                                  type={
                                    f.dataType === "number" ? "number" : "text"
                                  }
                                  value={f.value ?? ""}
                                  onChange={(e) => {
                                    const updated = [...filters];
                                    updated[i] = {
                                      ...updated[i],
                                      value: e.target.value,
                                    };
                                    setFilters(updated);
                                  }}
                                  sx={{ flex: 1, fontSize: "13px" }}
                                />
                              )}
                            </Stack>
                          );
                        })()}
                    </Box>
                  ))}
                </Box>

                <Divider />

                {/* Breakdown section */}
                <Box>
                  <Tooltip
                    placement="left"
                    arrow
                    componentsProps={{
                      tooltip: {
                        sx: {
                          bgcolor: isDark ? "#1a1a2e" : "#fff",
                          borderRadius: 2,
                          p: 2,
                          maxWidth: 180,
                          boxShadow: isDark
                            ? "0 4px 20px rgba(0,0,0,0.5)"
                            : "0 4px 20px rgba(0,0,0,0.12)",
                          border: isDark ? "none" : "1px solid",
                          borderColor: isDark ? "transparent" : "divider",
                        },
                      },
                      arrow: {
                        sx: {
                          color: isDark ? "#1a1a2e" : "#fff",
                          "&::before": {
                            border: isDark ? "none" : "1px solid",
                            borderColor: isDark ? "transparent" : "divider",
                          },
                        },
                      },
                    }}
                    title={
                      <Box sx={{ textAlign: "center" }}>
                        {(() => {
                          const teal = isDark ? "#5BE49B" : "#16A34A";
                          const tealFill1 = isDark
                            ? "rgba(91,228,155,0.2)"
                            : "rgba(22,163,74,0.12)";
                          const tealFill2 = isDark
                            ? "rgba(91,228,155,0.3)"
                            : "rgba(22,163,74,0.2)";
                          const tealFill3 = isDark
                            ? "rgba(91,228,155,0.15)"
                            : "rgba(22,163,74,0.08)";
                          const tealFill4 = isDark
                            ? "rgba(91,228,155,0.25)"
                            : "rgba(22,163,74,0.15)";
                          const purple = isDark ? "#916BFF" : "#7C4DFF";
                          const purpleFill1 = isDark
                            ? "rgba(145,107,255,0.2)"
                            : "rgba(105,65,198,0.12)";
                          const purpleFill2 = isDark
                            ? "rgba(145,107,255,0.3)"
                            : "rgba(105,65,198,0.2)";
                          const purpleFill3 = isDark
                            ? "rgba(145,107,255,0.15)"
                            : "rgba(105,65,198,0.08)";
                          const purpleFill4 = isDark
                            ? "rgba(145,107,255,0.25)"
                            : "rgba(105,65,198,0.15)";
                          const coral = isDark ? "#FF6B6B" : "#EF4444";
                          const coralFill1 = isDark
                            ? "rgba(255,107,107,0.2)"
                            : "rgba(239,68,68,0.12)";
                          const coralFill2 = isDark
                            ? "rgba(255,107,107,0.3)"
                            : "rgba(239,68,68,0.2)";
                          const coralFill3 = isDark
                            ? "rgba(255,107,107,0.15)"
                            : "rgba(239,68,68,0.08)";
                          const coralFill4 = isDark
                            ? "rgba(255,107,107,0.25)"
                            : "rgba(239,68,68,0.15)";
                          return (
                            <svg
                              width="120"
                              height="90"
                              viewBox="0 0 120 90"
                              fill="none"
                              xmlns="http://www.w3.org/2000/svg"
                            >
                              {/* Cube group 1 - teal */}
                              <g transform="translate(8, 40)">
                                <path
                                  d="M15 0 L30 8 L30 24 L15 32 L0 24 L0 8 Z"
                                  fill={tealFill1}
                                  stroke={teal}
                                  strokeWidth="1.2"
                                />
                                <path
                                  d="M15 0 L30 8 L15 16 L0 8 Z"
                                  fill={tealFill2}
                                  stroke={teal}
                                  strokeWidth="1.2"
                                />
                                <line
                                  x1="15"
                                  y1="16"
                                  x2="15"
                                  y2="32"
                                  stroke={teal}
                                  strokeWidth="1.2"
                                />
                              </g>
                              <g transform="translate(8, 22)">
                                <path
                                  d="M15 0 L30 8 L30 24 L15 32 L0 24 L0 8 Z"
                                  fill={tealFill3}
                                  stroke={teal}
                                  strokeWidth="1.2"
                                />
                                <path
                                  d="M15 0 L30 8 L15 16 L0 8 Z"
                                  fill={tealFill4}
                                  stroke={teal}
                                  strokeWidth="1.2"
                                />
                                <line
                                  x1="15"
                                  y1="16"
                                  x2="15"
                                  y2="32"
                                  stroke={teal}
                                  strokeWidth="1.2"
                                />
                              </g>
                              {/* Cube group 2 - purple */}
                              <g transform="translate(44, 30)">
                                <path
                                  d="M15 0 L30 8 L30 24 L15 32 L0 24 L0 8 Z"
                                  fill={purpleFill1}
                                  stroke={purple}
                                  strokeWidth="1.2"
                                />
                                <path
                                  d="M15 0 L30 8 L15 16 L0 8 Z"
                                  fill={purpleFill2}
                                  stroke={purple}
                                  strokeWidth="1.2"
                                />
                                <line
                                  x1="15"
                                  y1="16"
                                  x2="15"
                                  y2="32"
                                  stroke={purple}
                                  strokeWidth="1.2"
                                />
                              </g>
                              <g transform="translate(44, 12)">
                                <path
                                  d="M15 0 L30 8 L30 24 L15 32 L0 24 L0 8 Z"
                                  fill={purpleFill3}
                                  stroke={purple}
                                  strokeWidth="1.2"
                                />
                                <path
                                  d="M15 0 L30 8 L15 16 L0 8 Z"
                                  fill={purpleFill4}
                                  stroke={purple}
                                  strokeWidth="1.2"
                                />
                                <line
                                  x1="15"
                                  y1="16"
                                  x2="15"
                                  y2="32"
                                  stroke={purple}
                                  strokeWidth="1.2"
                                />
                              </g>
                              {/* Cube group 3 - coral */}
                              <g transform="translate(80, 38)">
                                <path
                                  d="M15 0 L30 8 L30 24 L15 32 L0 24 L0 8 Z"
                                  fill={coralFill1}
                                  stroke={coral}
                                  strokeWidth="1.2"
                                />
                                <path
                                  d="M15 0 L30 8 L15 16 L0 8 Z"
                                  fill={coralFill2}
                                  stroke={coral}
                                  strokeWidth="1.2"
                                />
                                <line
                                  x1="15"
                                  y1="16"
                                  x2="15"
                                  y2="32"
                                  stroke={coral}
                                  strokeWidth="1.2"
                                />
                              </g>
                              <g transform="translate(80, 20)">
                                <path
                                  d="M15 0 L30 8 L30 24 L15 32 L0 24 L0 8 Z"
                                  fill={coralFill3}
                                  stroke={coral}
                                  strokeWidth="1.2"
                                />
                                <path
                                  d="M15 0 L30 8 L15 16 L0 8 Z"
                                  fill={coralFill4}
                                  stroke={coral}
                                  strokeWidth="1.2"
                                />
                                <line
                                  x1="15"
                                  y1="16"
                                  x2="15"
                                  y2="32"
                                  stroke={coral}
                                  strokeWidth="1.2"
                                />
                              </g>
                            </svg>
                          );
                        })()}
                        <Typography
                          variant="caption"
                          sx={{
                            color: isDark
                              ? "rgba(255,255,255,0.7)"
                              : "text.secondary",
                            mt: 0.5,
                            display: "block",
                            lineHeight: 1.3,
                          }}
                        >
                          Segment your data into different categories.
                        </Typography>
                      </Box>
                    }
                  >
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                      onClick={(e) => openPicker(e, "breakdown")}
                      sx={{
                        cursor: "pointer",
                        borderRadius: 1,
                        px: 1,
                        py: 0.5,
                        mx: -1,
                        transition: "background-color 0.15s",
                        "&:hover": {
                          bgcolor: (t) =>
                            t.palette.mode === "dark"
                              ? "rgba(145, 107, 255, 0.12)"
                              : "rgba(105, 65, 198, 0.08)",
                          "& .breakdown-section-title": {
                            color: "primary.main",
                          },
                        },
                      }}
                    >
                      <Typography
                        className="breakdown-section-title"
                        variant="body2"
                        fontWeight="fontWeightSemiBold"
                        sx={{ transition: "color 0.15s" }}
                      >
                        Breakdown
                      </Typography>
                      <Iconify
                        icon="mdi:plus"
                        width={18}
                        sx={{ color: "text.secondary" }}
                      />
                    </Stack>
                  </Tooltip>
                  {breakdowns.map((b, i) => (
                    <Box
                      key={i}
                      sx={{
                        mt: 1,
                        p: 1.5,
                        border: `1px solid ${theme.palette.divider}`,
                        borderRadius: 1,
                        "&:hover .breakdown-hover-action": {
                          opacity: 1,
                        },
                      }}
                    >
                      <Stack direction="row" alignItems="center" gap={1}>
                        <Iconify
                          icon={
                            METRIC_TYPE_ICONS[b.type] ||
                            "mdi:chart-timeline-variant"
                          }
                          width={16}
                          sx={{ color: "text.secondary" }}
                        />
                        <Typography
                          variant="body2"
                          sx={{
                            flex: 1,
                            cursor: "pointer",
                            "&:hover": { color: "primary.main" },
                          }}
                          onClick={(e) => openPicker(e, "breakdown", i)}
                        >
                          {b.name || "Select attribute"}
                        </Typography>
                        <IconButton
                          className="breakdown-hover-action"
                          size="small"
                          onClick={() => handleRemoveBreakdown(i)}
                          sx={{
                            opacity: 0,
                            transition: "opacity 0.15s",
                          }}
                        >
                          <Iconify icon="mdi:close" width={14} />
                        </IconButton>
                      </Stack>
                    </Box>
                  ))}
                </Box>
              </Box>
            )}

            {rightTab === 1 && (
              <Box sx={{ p: 2, overflow: "auto" }}>
                {isPie || isTable || isMetricCard ? (
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ fontStyle: "italic", textAlign: "center", mt: 4 }}
                  >
                    {isPie
                      ? "Pie charts do not have axis settings"
                      : isTable
                        ? "Table view does not have axis settings"
                        : "Metric cards do not have axis settings"}
                  </Typography>
                ) : (
                  <>
                    {/* AXIS collapsible section */}
                    <Typography
                      variant="overline"
                      fontWeight={700}
                      sx={{ mb: 2, display: "block", letterSpacing: 1.5 }}
                    >
                      AXIS
                    </Typography>

                    {/* Axis Assignment */}
                    <Box>
                      <Typography
                        variant="subtitle2"
                        fontWeight={700}
                        sx={{ mb: 1.5 }}
                      >
                        Axis Assignment
                      </Typography>
                      {previewSeries.map((s, si) => {
                        const seriesColor = getSeriesColor(
                          s.name,
                          seriesColorMap,
                        );
                        return (
                          <Stack
                            key={si}
                            direction="row"
                            alignItems="center"
                            justifyContent="space-between"
                            sx={{ mb: 1 }}
                          >
                            <Stack
                              direction="row"
                              alignItems="center"
                              gap={1}
                              sx={{ flex: 1, minWidth: 0 }}
                            >
                              <Box
                                sx={{
                                  width: 22,
                                  height: 22,
                                  borderRadius: 0.5,
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  bgcolor: seriesColor + "22",
                                  color: seriesColor,
                                  fontSize: "11px",
                                  fontWeight: 700,
                                }}
                              >
                                {LETTER_LABELS[si] || si}
                              </Box>
                              <Iconify
                                icon="mdi:chart-line"
                                width={16}
                                sx={{
                                  color: seriesColor,
                                  flexShrink: 0,
                                }}
                              />
                              <Typography
                                variant="body2"
                                noWrap
                                sx={{ fontWeight: 500 }}
                              >
                                {s.name?.split(" (")[0] || s.name}
                              </Typography>
                            </Stack>
                            <ToggleButtons
                              options={[
                                { label: "L", value: "left" },
                                { label: "R", value: "right" },
                              ]}
                              value={axisConfig.seriesAxis[si] || "left"}
                              onChange={(v) => setSeriesAxis(si, v)}
                              theme={theme}
                            />
                          </Stack>
                        );
                      })}
                      {previewSeries.length === 0 && (
                        <Typography
                          variant="body2"
                          color="text.secondary"
                          sx={{ fontStyle: "italic" }}
                        >
                          Add metrics to see axis assignments
                        </Typography>
                      )}
                    </Box>

                    <Divider sx={{ my: 2 }} />

                    {/* Left Y-Axis */}
                    <AxisSection
                      title="Left Y-Axis"
                      config={axisConfig.leftY}
                      onChange={(key, val) => updateAxis("leftY", key, val)}
                      theme={theme}
                      showReset
                      onReset={() =>
                        setAxisConfig((prev) => ({
                          ...prev,
                          leftY: {
                            visible: true,
                            label: "",
                            unit: "",
                            prefixSuffix: "prefix",
                            abbreviation: true,
                            decimals: DEFAULT_DECIMALS,
                            min: "",
                            max: "",
                            outOfBounds: "visible",
                            scale: "linear",
                          },
                        }))
                      }
                    />

                    <Divider sx={{ my: 2 }} />

                    {/* Right Y-Axis */}
                    <AxisSection
                      title="Right Y-Axis"
                      config={axisConfig.rightY}
                      onChange={(key, val) => updateAxis("rightY", key, val)}
                      theme={theme}
                      showReset
                      onReset={() =>
                        setAxisConfig((prev) => ({
                          ...prev,
                          rightY: {
                            visible: false,
                            label: "",
                            unit: "",
                            prefixSuffix: "prefix",
                            abbreviation: true,
                            decimals: DEFAULT_DECIMALS,
                            min: "",
                            max: "",
                            outOfBounds: "hidden",
                            scale: "linear",
                          },
                        }))
                      }
                    />

                    <Divider sx={{ my: 2 }} />

                    {/* X-Axis */}
                    <Box sx={{ mb: 3 }}>
                      <Typography
                        variant="subtitle2"
                        fontWeight={700}
                        sx={{ mb: 1.5 }}
                      >
                        X-Axis
                      </Typography>
                      <Stack
                        direction="row"
                        justifyContent="space-between"
                        alignItems="center"
                        sx={{ mb: 1.5 }}
                      >
                        <Typography variant="body2" color="text.secondary">
                          Axis
                        </Typography>
                        <ToggleButtons
                          options={[
                            { label: "Visible", value: true },
                            { label: "Hidden", value: false },
                          ]}
                          value={axisConfig.xAxis.visible}
                          onChange={(v) => updateAxis("xAxis", "visible", v)}
                          theme={theme}
                        />
                      </Stack>
                      <Stack
                        direction="row"
                        justifyContent="space-between"
                        alignItems="center"
                      >
                        <Typography variant="body2" color="text.secondary">
                          Label
                        </Typography>
                        <TextField
                          size="small"
                          value={axisConfig.xAxis.label}
                          onChange={(e) =>
                            updateAxis("xAxis", "label", e.target.value)
                          }
                          placeholder="e.g. Time (s)"
                          inputProps={{ maxLength: AXIS_LABEL_MAX_LENGTH }}
                          sx={{
                            width: 180,
                            "& .MuiOutlinedInput-root": { fontSize: "13px" },
                          }}
                        />
                      </Stack>
                    </Box>
                  </>
                )}
              </Box>
            )}
          </Box>
        )}
      </Box>

      {/* Shared Picker Popper — used for metric, filter, and breakdown */}
      <Popper
        open={pickerOpen}
        anchorEl={pickerAnchor}
        placement="bottom-start"
        sx={{ zIndex: 1300 }}
      >
        <ClickAwayListener onClickAway={() => setPickerOpen(false)}>
          <Paper
            elevation={8}
            sx={{
              width: 600,
              maxHeight: 440,
              display: "flex",
              flexDirection: "column",
              border: `1px solid ${theme.palette.divider}`,
              borderRadius: 2,
            }}
          >
            {/* Search + count */}
            <Box sx={{ p: 1.5 }}>
              <TextField
                size="small"
                fullWidth
                placeholder={`Search ${pickerMode === "metric" ? "metrics" : pickerMode === "metric_filter" ? "filter attributes" : pickerMode === "filter" ? "filter attributes" : "breakdown attributes"}...`}
                value={pickerSearch}
                onChange={(e) => setPickerSearch(e.target.value)}
                autoFocus
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Iconify
                        icon="eva:search-fill"
                        width={18}
                        sx={{ color: "text.disabled" }}
                      />
                    </InputAdornment>
                  ),
                  endAdornment: isPickerInventoryLoading ? (
                    <InputAdornment position="end">
                      <CircularProgress
                        size={14}
                        aria-label="Searching dashboard properties"
                      />
                    </InputAdornment>
                  ) : !cursorAttributePickerActive &&
                    Number.isSafeInteger(paginatedTotal) ? (
                      <InputAdornment position="end">
                        <Typography
                          variant="caption"
                          sx={{ color: "text.disabled", fontSize: 11 }}
                        >
                          {paginatedTotal} results
                        </Typography>
                      </InputAdornment>
                    ) : null,
                }}
              />
            </Box>
            <Divider />
            {/* Two column layout */}
            <Box sx={{ display: "flex", flex: 1, overflow: "hidden" }}>
              {/* Left: categories */}
              <Box
                sx={{
                  width: 160,
                  borderRight: `1px solid ${theme.palette.divider}`,
                  overflow: "auto",
                  py: 0.5,
                }}
              >
                {METRIC_CATEGORIES.map((cat) => {
                  const categoryCount = getWidgetCatalogSidebarCategoryCount({
                    pickerCategory: cat.key,
                    categoryCounts: pickerSidebarCategoryCounts,
                    categoryCountsExact: pickerSidebarCategoryCountsExact,
                  });
                  const categoryCountPresentation =
                    getWidgetCatalogCategoryCountPresentation(categoryCount);
                  return (
                    <Box
                      key={cat.key}
                      onClick={() => setPickerCategory(cat.key)}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        px: 1.5,
                        py: 0.75,
                        cursor: "pointer",
                        borderRadius: 1,
                        mx: 0.5,
                        bgcolor:
                          pickerCategory === cat.key
                            ? "action.selected"
                            : "transparent",
                        "&:hover": {
                          bgcolor:
                            pickerCategory === cat.key
                              ? "action.selected"
                              : "action.hover",
                        },
                      }}
                    >
                      <Iconify
                        icon={cat.icon}
                        width={16}
                        sx={{
                          color:
                            pickerCategory === cat.key
                              ? "primary.main"
                              : "text.secondary",
                        }}
                      />
                      <Typography
                        variant="body2"
                        sx={{
                          fontSize: "12px",
                          fontWeight: pickerCategory === cat.key ? 600 : 400,
                          color:
                            pickerCategory === cat.key
                              ? "text.primary"
                              : "text.secondary",
                          flex: 1,
                        }}
                      >
                        {cat.label}
                      </Typography>
                      <Typography
                        variant="caption"
                        aria-label={`${cat.label} property count: ${
                          categoryCountPresentation.exact
                            ? categoryCountPresentation.text
                            : "exact count unavailable"
                        }`}
                        title={categoryCountPresentation.title || undefined}
                        sx={{ color: "text.disabled", fontSize: 10 }}
                      >
                        {categoryCountPresentation.text}
                      </Typography>
                    </Box>
                  );
                })}
              </Box>
              {/* Right: items — the end sentinel advances one cursor page. */}
              <Box sx={{ flex: 1, overflow: "auto", maxHeight: 340 }}>
                {isPickerInventoryLoading &&
                  pickerMetricOptions.length === 0 && (
                    <Box
                      role="status"
                      aria-live="polite"
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        px: 1.5,
                        pt: 1.25,
                        color: "text.secondary",
                      }}
                    >
                      <CircularProgress size={14} />
                      <Typography variant="caption">
                        Searching properties…
                      </Typography>
                    </Box>
                  )}
                {isPickerInventoryLoading &&
                  pickerMetricOptions.length === 0 &&
                  Array.from({ length: 8 }).map((_, i) => (
                    <Box
                      key={`skel-${i}`}
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        px: 1.5,
                        py: 0.85,
                      }}
                    >
                      <Box
                        sx={{
                          width: 15,
                          height: 15,
                          borderRadius: 0.5,
                          bgcolor: "action.hover",
                          flexShrink: 0,
                        }}
                      />
                      <Box
                        sx={{
                          height: 12,
                          borderRadius: 0.5,
                          bgcolor: "action.hover",
                          flex: 1,
                          maxWidth: `${55 + Math.random() * 35}%`,
                          animation: "pulse 1.5s ease-in-out infinite",
                          "@keyframes pulse": {
                            "0%, 100%": { opacity: 0.4 },
                            "50%": { opacity: 0.8 },
                          },
                        }}
                      />
                      <Box
                        sx={{
                          width: 40,
                          height: 16,
                          borderRadius: 2,
                          bgcolor: "action.hover",
                          flexShrink: 0,
                          animation: "pulse 1.5s ease-in-out infinite",
                          "@keyframes pulse": {
                            "0%, 100%": { opacity: 0.4 },
                            "50%": { opacity: 0.8 },
                          },
                        }}
                      />
                    </Box>
                  ))}
                {pickerMetricOptions.map((opt) => {
                  const alreadyUsed = false;

                  const sourceBadge =
                    opt.type === "eval_metric" || opt.type === "annotation"
                      ? null
                      : opt.sources && opt.sources.length > 1
                        ? null
                        : opt.source === "simulation"
                          ? { label: "Sim", color: "secondary" }
                          : opt.source === "datasets"
                            ? { label: "Dataset", color: "default" }
                            : opt.source === "traces"
                              ? { label: "Trace", color: "primary" }
                              : null;

                  return (
                    <Box
                      key={`${opt.source || "all"}-${opt.type}-${opt.id}-${opt.dataType || ""}`}
                      onClick={
                        alreadyUsed ? undefined : () => handlePickerSelect(opt)
                      }
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        gap: 1,
                        px: 1.5,
                        py: 0.75,
                        cursor: alreadyUsed ? "default" : "pointer",
                        opacity: alreadyUsed ? 0.4 : 1,
                        "&:hover": {
                          bgcolor: alreadyUsed ? "transparent" : "action.hover",
                        },
                      }}
                    >
                      <Iconify
                        icon={METRIC_TYPE_ICONS[opt.type] || "mdi:cog-outline"}
                        width={15}
                        sx={{ color: "text.disabled", flexShrink: 0 }}
                      />
                      <Typography
                        variant="body2"
                        title={opt.name}
                        sx={{
                          fontSize: "13px",
                          flex: 1,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {opt.name}
                      </Typography>
                      {opt.outputType && (
                        <Chip
                          size="small"
                          variant="outlined"
                          label={
                            opt.outputType === "SCORE"
                              ? "score"
                              : opt.outputType === "PASS_FAIL"
                                ? "P/F"
                                : opt.outputType === "CHOICE"
                                  ? "choice"
                                  : opt.outputType
                          }
                          sx={{
                            height: 18,
                            fontSize: 10,
                            flexShrink: 0,
                          }}
                        />
                      )}
                      {opt.type === "custom_attribute" && opt.dataType && (
                        <Chip
                          size="small"
                          variant="outlined"
                          label={opt.dataType}
                          sx={{ height: 18, fontSize: 10, flexShrink: 0 }}
                        />
                      )}
                      {sourceBadge && (
                        <Chip
                          size="small"
                          label={sourceBadge.label}
                          color={sourceBadge.color}
                          variant="outlined"
                          sx={{
                            height: 18,
                            fontSize: 10,
                            flexShrink: 0,
                          }}
                        />
                      )}
                    </Box>
                  );
                })}
                <WidgetCatalogPaginationControl
                  key={`${pickerCatalogSession}:${pickerMode}:${pickerCategory}:${debouncedPickerSearch}`}
                  pickerCategory={pickerCategory}
                  hasNextPage={hasNextPage}
                  continuationKey={catalogContinuationKey}
                  isFetchingNextPage={isFetchingNextPage}
                  isFetchNextPageError={isFetchNextPageError}
                  onLoadMore={fetchNextPage}
                  attributeHasNextPage={
                    cursorAttributePickerActive &&
                    cursorAttributeControlProps.hasNextPage
                  }
                  attributeContinuationKey={
                    cursorAttributePickerActive
                      ? cursorAttributeControlProps.continuationKey
                      : null
                  }
                  isFetchingAttributeNextPage={
                    cursorAttributePickerActive &&
                    cursorAttributeControlProps.isFetchingNextPage
                  }
                  isFetchNextAttributePageError={
                    cursorAttributePickerActive &&
                    cursorAttributeControlProps.isFetchNextPageError
                  }
                  onLoadMoreAttributes={cursorAttributeControlProps.onLoadMore}
                />
                {cursorAttributePickerActive && (
                  <Box sx={{ px: 1.5, pb: 1.5 }}>
                    <AttributeInventoryControls
                      {...cursorAttributeControlProps}
                      search={pickerSearch}
                      showSearch={false}
                      showLoadMore={false}
                    />
                  </Box>
                )}
                {pickerMetricOptions.length === 0 &&
                  !isPickerInventoryLoading && (
                    <Box sx={{ p: 3, textAlign: "center" }}>
                      <Typography variant="body2" color="text.disabled">
                        No attributes found
                      </Typography>
                    </Box>
                  )}
              </Box>
            </Box>
          </Paper>
        </ClickAwayListener>
      </Popper>

      {/* Filter value picker popup (global filters) */}
      {filterValueIndex != null && filterValueAnchor && (
        <FilterValuePickerPopup
          anchorEl={filterValueAnchor}
          filter={filters[filterValueIndex]}
          source={filters[filterValueIndex]?.source || "traces"}
          onClose={() => {
            setFilterValueAnchor(null);
            setFilterValueIndex(null);
          }}
          onApply={(selected, valueTypes) => {
            const updated = [...filters];
            updated[filterValueIndex] = {
              ...updated[filterValueIndex],
              value: selected,
              valueTypes,
            };
            setFilters(updated);
            setFilterValueAnchor(null);
            setFilterValueIndex(null);
          }}
        />
      )}

      {/* Per-metric filter value picker popup */}
      {mfValueTarget != null &&
        mfValueAnchor &&
        (() => {
          const mfFilter =
            metrics[mfValueTarget.metricIdx]?.filters?.[
              mfValueTarget.filterIdx
            ];
          return mfFilter ? (
            <FilterValuePickerPopup
              anchorEl={mfValueAnchor}
              filter={mfFilter}
              source={mfFilter.source || "traces"}
              onClose={() => {
                setMfValueAnchor(null);
                setMfValueTarget(null);
              }}
              onApply={(selected, valueTypes) => {
                handleUpdateMetricFilter(
                  mfValueTarget.metricIdx,
                  mfValueTarget.filterIdx,
                  { value: selected, valueTypes },
                );
                setMfValueAnchor(null);
                setMfValueTarget(null);
              }}
            />
          ) : null;
        })()}
    </Box>
  );
}
