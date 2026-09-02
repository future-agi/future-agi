import _ from "lodash";
import { LABELS } from "./constants";
import { getRandomId, safeParse } from "src/utils/utils";
import CustomTraceRenderer from "src/sections/projects/LLMTracing/Renderers/CustomTraceRenderer";
import CustomTraceGroupHeaderRenderer from "src/sections/projects/LLMTracing/Renderers/CustomTraceGroupHeaderRenderer";
import { isCellValueEmpty } from "src/components/table/utils";
import { RENDERER_CONFIG } from "src/sections/projects/LLMTracing/Renderers/common";
import { NameCell } from "src/sections/projects/LLMTracing/Renderers";
import IPOPCell from "src/sections/projects/LLMTracing/Renderers/IPOPCell";
import IPOPTooltipComponent from "src/sections/projects/LLMTracing/Renderers/IPOPTooltipComponent";
import { serializeFilterListForApi } from "src/api/contracts/filter-contract";

const LABEL_BG_COLORS = {
  [LABELS.PRODUCTION]: "green.o10",
  [LABELS.STAGING]: "orange.o10",
  [LABELS.DEFAULT]: "action.hover",
  fallback: "background.neutral",
};

const LABEL_TEXT_COLORS = {
  [LABELS.PRODUCTION]: "green.500",
  [LABELS.STAGING]: "orange.500",
  [LABELS.DEFAULT]: "primary.main",
  fallback: "text.secondary",
};

export const getBgColor = (label) => {
  const key = _.toLower(label);
  return LABEL_BG_COLORS[key] || LABEL_BG_COLORS.fallback;
};

export const getTextColor = (label) => {
  const key = _.toLower(label);
  return LABEL_TEXT_COLORS[key] || LABEL_TEXT_COLORS.fallback;
};

export const containerStyle = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  width: "100%",
};

export const gridWrapperStyle = {
  flex: "1 1 auto",
  height: "calc(100vh - 250px)",
  width: "100%",
  minHeight: 0, // Important for flex children to shrink properly
};

export const gridStyle = {
  height: "100%",
  width: "100%",
  "& .ag-root-wrapper": {
    height: "100%",
  },
  "& .ag-cell": {
    display: "flex",
    alignItems: "center",
    padding: 0,
  },
  "& .ag-cell-wrapper": {
    display: "flex",
    alignItems: "center",
    height: "100%",
  },
};

export const getMetricsTabSx = (theme) => ({
  borderBottom: 1,
  borderColor: "divider",
  minHeight: 42,
  "& .MuiTabs-flexContainer": { gap: 0 },
  "& .MuiTab-root": {
    minHeight: 42,
    paddingX: theme.spacing(2),
    margin: theme.spacing(0),
    marginRight: `${theme.spacing(0)} !important`,
    minWidth: "auto",
    fontWeight: "fontWeightMedium",
    typography: "s1",
    color: "text.disabled",
    textTransform: "none",
    transition: theme.transitions.create(["color", "background-color"], {
      duration: theme.transitions.duration.short,
    }),
    "&.Mui-selected": {
      color: "primary.main",
      fontWeight: "fontWeightSemiBold",
    },
    "&:not(.Mui-selected)": { color: theme.palette.text.disabled },
    "&:first-of-type": { marginLeft: 0 },
  },
});

export const defaultFilterBase = {
  column_id: "",
  filter_config: {
    filter_type: "",
    filter_op: "",
    filter_value: "",
  },
};

export const getDefaultFilter = () => [
  { ...defaultFilterBase, id: getRandomId() },
];

export const normalizeFilters = (filters = []) => {
  const ready = filters.filter((filter) => {
    const value = filter?.filter_config?.filter_value;
    return (
      filter?.column_id &&
      filter?.filter_config?.filter_type &&
      filter?.filter_config?.filter_op &&
      value !== undefined &&
      value !== null &&
      value !== "" &&
      !(
        Array.isArray(value) &&
        value.filter((v) => v !== "" && v !== null && v !== undefined)
          .length === 0
      )
    );
  });
  return serializeFilterListForApi(ready);
};

// mapping col.name => filter type config
export const FILTER_TYPE_MAP = {
  Versions: { type: "text" },
  Labels: {
    type: "option",
    multiSelect: true,
    options: [
      { label: "Production", value: "production" },
      { label: "Staging", value: "staging" },
      { label: "Default", value: "default" },
    ],
  },
  "Median Input Tokens": { type: "number" },
  "Median Output Tokens": { type: "number" },
  "Median Cost": { type: "number" },
  "Median Latency": { type: "number" },
  "Last Used": { type: "date" },
  "First Used": { type: "date" },
  "No. of traces": { type: "number" },
};

const FILTER_TYPE_BY_COLUMN_ID = {
  avg_cost: "number",
  avg_latency: "number",
  avg_input_tokens: "number",
  avg_output_tokens: "number",
  unique_traces: "number",
  first_used: "date",
  last_used: "date",
};

const FILTER_TYPE_ALIASES = {
  array: "option",
  boolean: "boolean",
  categorical: "option",
  date: "date",
  datetime: "date",
  number: "number",
  text: "text",
};

const OPERATOR_LABELS = {
  greater_than: "Greater Than",
  less_than: "Less Than",
  equals: "Equals",
  not_equals: "Not Equals",
  greater_than_or_equal: "Greater Than Or Equal",
  less_than_or_equal: "Less Than Or Equal",
  between: "Between",
  not_between: "Not Between",
  contains: "Contains",
  not_contains: "Does Not Contain",
  starts_with: "Starts With",
  ends_with: "Ends With",
};

const normalizeOutputType = (value) =>
  String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[/\s]+/g, "_");

const publishedFilterType = (col) => {
  const value = col.filterType || col.filter_type;
  return FILTER_TYPE_ALIASES[String(value || "").toLowerCase()];
};

export const buildFilterDefinitions = (
  columnDefs,
  ignoreOutputType = false,
) => {
  const groups = {};
  const filters = [];

  columnDefs.forEach((col) => {
    const outputType = normalizeOutputType(col.outputType || col.output_type);
    const propertyKind = col.propertyKind || col.property_kind;
    const isEvalColumn =
      propertyKind === "eval_config" ||
      (col.groupBy || col.group_by) === "Evaluation Metrics";
    const apiFilterType = publishedFilterType(col);
    // Determine filter type from mapping
    const baseFilter = FILTER_TYPE_MAP[col.name] || {
      type: FILTER_TYPE_BY_COLUMN_ID[col.id] || "text",
    };

    const filterDef = {
      propertyName: col.name,
      propertyId: col.id,
      registryId: col.propertyId || col.property_id,
      propertyKind,
      propertySource: col.propertySource || col.property_source,
      maxUsage: 1,
      filterType: { type: baseFilter.type },
    };

    if (apiFilterType) {
      filterDef.filterType.type = apiFilterType;
    }

    // Aggregate evals, including expanded choices/pass-fail, are percentages.
    if (ignoreOutputType && isEvalColumn) {
      filterDef.filterType.type = "number";
    }

    if (!ignoreOutputType) {
      if (
        outputType === "choices" &&
        Array.isArray(col.choices || col.choices_map)
      ) {
        filterDef.filterType.type = "option";
        filterDef.filterType.options = (col.choices || col.choices_map).map(
          (choice) => ({
            label: choice,
            value: choice,
          }),
        );
        filterDef.multiSelect = true;
      }

      if (outputType === "pass_fail") {
        filterDef.filterType.type = "boolean";
      }

      if (
        ["score", "float", "numeric", "percentage", "reason"].includes(
          outputType,
        )
      ) {
        filterDef.filterType.type = "number";
      }
    }

    const supportedFilterOps =
      col.supportedFilterOps || col.supported_filter_ops;
    if (Array.isArray(supportedFilterOps) && supportedFilterOps.length) {
      filterDef.overrideOperators = supportedFilterOps.map((value) => ({
        value,
        label: OPERATOR_LABELS[value] || value,
      }));
      if (filterDef.filterType.type !== "option") {
        filterDef.showOperator = true;
      }
    }

    // spread special settings
    if (baseFilter.multiSelect) {
      filterDef.multiSelect = true;
      filterDef.filterType.options = baseFilter.options || [];
    }

    if (baseFilter.allowTypeChange) {
      filterDef.allowTypeChange = true;
    }

    if (baseFilter.showOperator) {
      filterDef.showOperator = true;
    }

    const groupBy = col.groupBy || col.group_by;
    if (groupBy) {
      // Add to group
      if (!groups[groupBy]) {
        groups[groupBy] = {
          propertyName: groupBy,
          stringConnector: "is",
          dependents: [],
        };
        filters.push(groups[groupBy]);
      }
      groups[groupBy].dependents.push(filterDef);
    } else {
      filters.push(filterDef);
    }
  });

  return filters;
};

export const getMetricsListColumnDefs = (col) => {
  return {
    headerName: col.name,
    field: col.id,
    hide: !col?.is_visible,
    context: { sourceColumn: col },
    cellStyle: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        return {
          display: "flex",
          alignItems: "center",
          height: "100%",
          justifyContent: "center",
        };
      }
    },
    valueFormatter: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        return "-"; // shown when no renderer is used
      }
      // For input/output columns, valueGetter already normalized the value
      // so we don't need to do anything here
      return value;
    },
    cellRendererSelector: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        // No renderer for empty values
        return null;
      }
      const column = params?.colDef?.col;
      const colId = column?.id;

      if (RENDERER_CONFIG.nameColumns.includes(colId)) {
        return {
          component: NameCell,
        };
      }
      if (colId === "input" || colId === "output") {
        return {
          component: IPOPCell,
        };
      }
      // Use CustomTraceRenderer for non-empty values
      return { component: CustomTraceRenderer };
    },
    // Add tooltip for input/output columns
    ...(col?.id === "input" || col?.id === "output"
      ? {
          tooltipComponent: IPOPTooltipComponent,
          tooltipValueGetter: (params) => {
            const value = params.value;
            // Parse value according to its type - if string (JSON from valueGetter), parse to object
            // Otherwise return as is
            if (value === null || value === undefined || value === "") {
              return null;
            }
            // If value is a string, try to parse it (it might be a JSON string from valueGetter)
            if (typeof value === "string") {
              const parsed = safeParse(value);
              // If parsing succeeded and result is an object, use it; otherwise use original string
              return typeof parsed === "object" && parsed !== null
                ? parsed
                : value;
            }
            // If value is already an object, return it directly
            return value;
          },
        }
      : {}),
    col,
    headerComponent: CustomTraceGroupHeaderRenderer,
    headerComponentParams: {
      group: col?.groupBy,
    },
  };
};
