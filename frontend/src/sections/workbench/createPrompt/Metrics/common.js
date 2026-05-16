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
  columnId: "",
  filterConfig: {
    filterType: "",
    filterOp: "",
    filterValue: "",
  },
};

export const getDefaultFilter = () => [
  { ...defaultFilterBase, id: getRandomId() },
];

export const normalizeFilters = (filters = []) => {
  return filters
    .filter((filter) => {
      // Filter out invalid filters
      return (
        filter.columnId &&
        filter.filterConfig?.filterType &&
        filter.filterConfig?.filterOp &&
        filter.filterConfig?.filterValue !== undefined &&
        filter.filterConfig?.filterValue !== null &&
        filter.filterConfig?.filterValue !== ""
      );
    })
    .map((filter) => {
      const newFilter = { ...filter };
      delete newFilter.id;
      delete newFilter._meta;

      // columnId is already in snake_case from the API

      const filterConfig = { ...filter.filterConfig };

      // Normalize number filters
      if (filterConfig?.filterType === "number") {
        let { filterValue } = filterConfig;
        const filterOp = filterConfig.filterOp;

        if (Array.isArray(filterValue)) {
          const cleaned = filterValue
            .filter((v) => v !== "" && v != null)
            .map(Number);

          if (["between", "not_between"].includes(filterOp)) {
            filterValue = cleaned.length === 2 ? cleaned : null;
          } else {
            filterValue = cleaned.length > 0 ? cleaned[0] : null;
          }
        } else if (filterValue != null && filterValue !== "") {
          filterValue = Number(filterValue);
        } else {
          filterValue = null;
        }

        filterConfig.filterValue = filterValue;
      }

      if (filterConfig?.filterType === "date") {
        filterConfig.filterType = "datetime";

        if (Array.isArray(filterConfig.filterValue)) {
          const cleaned = filterConfig.filterValue.filter(
            (v) => v !== "" && v != null,
          );

          if (
            [
              "equals",
              "not_equals",
              "greater_than",
              "less_than",
              "greater_than_or_equal",
              "less_than_or_equal",
            ].includes(filterConfig.filterOp)
          ) {
            filterConfig.filterValue = cleaned.length > 0 ? cleaned[0] : null;
          } else if (
            ["between", "not_between"].includes(filterConfig.filterOp)
          ) {
            filterConfig.filterValue = cleaned.length === 2 ? cleaned : null;
          }
        }
      }

      // Normalize boolean filters
      if (filterConfig?.filterType === "boolean") {
        if (filterConfig.filterValue === "true") {
          filterConfig.filterValue = true;
        } else if (filterConfig.filterValue === "false") {
          filterConfig.filterValue = false;
        } else {
          filterConfig.filterValue = null;
        }
      }

      return {
        column_id: newFilter.columnId,
        filter_config: {
          filter_type: filterConfig.filterType,
          filter_op: filterConfig.filterOp,
          filter_value: filterConfig.filterValue,
        },
      };
    });
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
};

export const buildFilterDefinitions = (
  columnDefs,
  ignoreOutputType = false,
) => {
  const groups = {};
  const filters = [];

  columnDefs.forEach((col) => {
    // Determine filter type from mapping
    const baseFilter = FILTER_TYPE_MAP[col.name] || { type: "text" };

    const filterDef = {
      propertyName: col.name,
      propertyId: col.id,
      maxUsage: 1,
      filterType: { type: baseFilter.type },
    };

    // Override type if outputType is float
    if (col.outputType === "float") {
      filterDef.filterType.type = "number";
    }
    if (!ignoreOutputType) {
      if (col.outputType === "choices" && Array.isArray(col.choices)) {
        filterDef.filterType.type = "option";
        filterDef.filterType.options = col.choices.map((choice) => ({
          label: choice,
          value: choice,
        }));
        filterDef.multiSelect = true;
      }

      if (col.outputType === "Pass/Fail") {
        filterDef.filterType.type = "boolean";
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

    if (col.groupBy) {
      // Add to group
      if (!groups[col.groupBy]) {
        groups[col.groupBy] = {
          propertyName: col.groupBy,
          stringConnector: "is",
          dependents: [],
        };
        filters.push(groups[col.groupBy]);
      }
      groups[col.groupBy].dependents.push(filterDef);
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
    hide: !col?.isVisible,
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
