import { getRandomId, safeParse } from "src/utils/utils";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { AnnotationLabelTypes, PROJECT_SOURCE } from "src/utils/constants";
import CustomTraceHeaderRenderer from "./Renderers/CustomTraceHeaderRenderer";
import {
  getAnnotationMetricFilterDefinition,
  getAttributesDefinition,
  getEvaluationMetricFilterDefinition,
  getSystemMetricFilterDefinition,
} from "../../../utils/prototypeObserveUtils";
import { avoidDuplicateFilterSet } from "../../../components/ComplexFilter/common";
import logger from "src/utils/logger";
import _ from "lodash";
import React from "react";
import { Skeleton } from "@mui/material";
import CustomTraceRenderer from "./Renderers/CustomTraceRenderer";
import { RENDERER_CONFIG } from "./Renderers/common";
import { NameCell } from "./Renderers";
import IPOPCell from "./Renderers/IPOPCell";
import IPOPTooltipComponent from "./Renderers/IPOPTooltipComponent";
import { isCellValueEmpty } from "src/components/table/utils";
import AnnotationHeaderCellRenderer from "../../agents/CallLogs/AnnotationHeaderCellRenderer";
import NewAnnotationCellRenderer from "../../agents/NewAnnotationCellRenderer";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";

export const AllowedGroups = [
  "Evaluation Metrics",
  "Annotation Metrics",
  "Custom Columns",
];

export const mergeCellStyle =
  (colDef, overrides = {}) =>
  (params) => {
    const baseStyle =
      typeof colDef.cellStyle === "function"
        ? colDef.cellStyle(params) || {}
        : colDef.cellStyle || {};
    return { ...baseStyle, ...overrides };
  };

export const generateObserveTraceFilterDefinition = (
  columns,
  attributes,
  existingFilter,
  source = null,
) => {
  const finalDefinition = [
    {
      propertyName: "Trace Id",
      propertyId: "trace_id",
      filterType: {
        type: "text",
      },
      maxUsage: 1,
    },
    {
      propertyName: "Trace Name",
      propertyId: "trace_name",
      filterType: {
        type: "text",
      },
      maxUsage: 1,
    },
    {
      propertyName: "Node Type",
      propertyId: "node_type",
      maxUsage: 1,
      multiSelect: true,
      filterType: {
        type: "option",
        options: [
          { label: "Chain", value: "chain" },
          { label: "Retriever", value: "retriever" },
          { label: "Generation", value: "generation" },
          { label: "LLM", value: "llm" },
          { label: "Tool", value: "tool" },
          { label: "Agent", value: "agent" },
          { label: "Embedding", value: "embedding" },
        ],
      },
    },
    {
      propertyName: "startTime",
      propertyId: "start_time",
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
    {
      propertyName: "Status",
      propertyId: "status",
      filterType: {
        type: "option",
        options: [
          { label: "OK", value: "OK" },
          { label: "Error", value: "ERROR" },
          { label: "Unset", value: "UNSET" },
        ],
      },
    },
  ];

  const updatedFilterDefinition =
    source === PROJECT_SOURCE.SIMULATOR ? [] : finalDefinition;

  const attributeDef = getAttributesDefinition(attributes, existingFilter);
  updatedFilterDefinition.filter((def) => def?.propertyName !== "Attribute");
  updatedFilterDefinition.push(...attributeDef);
  const evaluationMetricDef = getEvaluationMetricFilterDefinition(columns);
  updatedFilterDefinition.push(...evaluationMetricDef);
  if (source === PROJECT_SOURCE.SIMULATOR) {
    const systemMetricDef = getSystemMetricFilterDefinition();
    updatedFilterDefinition.push(...systemMetricDef);
  }
  const annotationMetricDef = getAnnotationMetricFilterDefinition(columns);
  updatedFilterDefinition.push(...annotationMetricDef);

  return updatedFilterDefinition;
};

export const generateSpanObserveFilterDefinition = (
  columns,
  attributes,
  existingFilter,
) => {
  const finalDefinition = [
    {
      propertyName: "Trace Id",
      propertyId: "trace_id",
      maxUsage: 1,
      filterType: {
        type: "text",
      },
    },
    {
      propertyName: "Span Name",
      propertyId: "span_name",
      filterType: {
        type: "text",
      },
      maxUsage: 1,
    },
    {
      propertyName: "Span Id",
      propertyId: "span_id",
      maxUsage: 1,
      filterType: {
        type: "text",
      },
    },
    {
      propertyName: "Node Type",
      propertyId: "node_type",
      maxUsage: 1,
      multiSelect: true,
      filterType: {
        type: "option",
        options: [
          { label: "Chain", value: "chain" },
          { label: "Retriever", value: "retriever" },
          { label: "Generation", value: "generation" },
          { label: "LLM", value: "llm" },
          { label: "Tool", value: "tool" },
          { label: "Agent", value: "agent" },
          { label: "Embedding", value: "embedding" },
        ],
      },
    },
  ];

  const evaluationMetricDef = getEvaluationMetricFilterDefinition(columns);
  finalDefinition.push(...evaluationMetricDef);

  const attributeDef = getAttributesDefinition(attributes, existingFilter);
  finalDefinition.filter((def) => def?.propertyName !== "Attribute");
  finalDefinition.push(...attributeDef);

  const annotationMetricDef = getAnnotationMetricFilterDefinition(columns);
  finalDefinition.push(...annotationMetricDef);

  return finalDefinition;
};

const NUMBER_FILTER_FIELDS = [
  "Median Input Tokens",
  "Median Output Tokens",
  "Median Cost",
  "Median Latency",
];

const DATE_FILTER_FIELDS = ["Last Used", "First Used", "Start Time"];

export const applyQuickFilters =
  (setFilters, openQuickFilter, setFilterOpen) =>
  ({ col, value, filterAnchor }) => {
    let filter = null;

    // Early return for number fields with popup
    if (NUMBER_FILTER_FIELDS.includes(col.name)) {
      openQuickFilter({
        filterAnchor,
        value,
        filter: {
          columnId: col.id,
          filterConfig: {
            filterType: "number",
            filterOp: "equals",
            filterValue: [value, ""],
          },
          _meta: {
            parentProperty: col.id,
          },
          id: getRandomId(),
        },
      });
      return;
    }

    if (!col.groupBy) {
      let filterType = "text";
      if (DATE_FILTER_FIELDS.includes(col.name)) {
        filterType = "date";
      }

      filter = {
        columnId: col.id,
        filterConfig: {
          filterType: filterType,
          filterOp: "equals",
          filterValue: value,
        },
        _meta: {
          parentProperty: col.id,
        },
        id: getRandomId(),
      };

      if (col.id === "node_type") {
        filter.filterConfig = {
          filterType: "text",
          filterOp: "contains",
          filterValue: [value],
        };
      }
      if (DATE_FILTER_FIELDS.includes(col.name)) {
        filter = {
          columnId: _.snakeCase(col.id),
          filterConfig: {
            filterType: "datetime",
            filterOp: "equals",
            filterValue: [value],
          },
          _meta: {
            parentProperty: col.id,
          },
          id: getRandomId(),
        };
      }
    } else if (
      col?.groupBy === "Evaluation Metrics" &&
      col?.sourceField !== "reason"
    ) {
      openQuickFilter({
        filterAnchor,
        value,
        filter: {
          columnId: col.id,
          filterConfig: {
            filterType: "number",
            filterOp: "equals",
            filterValue: [value, ""],
          },
          _meta: {
            parentProperty: "Evaluation Metrics",
            "Evaluation Metrics": col.id,
          },
          id: getRandomId(),
        },
      });
    } else if (col?.groupBy === "Annotation Metrics") {
      filter = {
        columnId: col.id,
        _meta: {
          parentProperty: "Annotation Metrics",
          "Annotation Metrics": col.id,
        },
        id: getRandomId(),
      };
      switch (col.annotationLabelType) {
        case AnnotationLabelTypes.STAR: {
          filter = {
            ...filter,
            filterConfig: {
              filterType: "number",
              filterOp: "equals",
              filterValue: [value, ""],
            },
          };
          break;
        }
        case AnnotationLabelTypes.TEXT: {
          filter = {
            ...filter,
            filterConfig: {
              filterType: "text",
              filterOp: "equals",
              filterValue: value,
            },
          };
          break;
        }
        case AnnotationLabelTypes.THUMBS_UP_DOWN: {
          filter = {
            ...filter,
            filterConfig: {
              filterType: "boolean",
              filterOp: "equals",
              filterValue: value === "up" ? true : false,
            },
          };
          break;
        }
        case AnnotationLabelTypes.CATEGORICAL: {
          filter = {
            ...filter,
            filterConfig: {
              filterType: "text",
              filterOp: "contains",
              filterValue: value,
            },
          };
          break;
        }
        case AnnotationLabelTypes.NUMERIC: {
          openQuickFilter({
            filterAnchor,
            value,
            filter: {
              ...filter,
              filterConfig: {
                filterType: "number",
                filterOp: "equals",
                filterValue: [value, ""],
              },
            },
          });
          return;
        }
      }
    }

    if (filter) {
      const extraFilter = buildApiFilterFromPanelRow({
        field: filter.columnId,
        fieldType: filter.filterConfig?.filterType,
        operator: filter.filterConfig?.filterOp,
        value: filter.filterConfig?.filterValue,
      });
      setFilters((prev) => {
        const exists = (prev || []).some(
          (f) =>
            f.column_id === extraFilter.column_id &&
            f.filter_config?.filter_value ===
              extraFilter.filter_config.filter_value,
        );
        return exists ? prev : [...(prev || []), extraFilter];
      });
    }
  };

export const useProjectList = (search_text, enabled = true) => {
  const payload = {};
  const queryKey = ["project-list"];

  if (search_text?.length) {
    payload.search_text = search_text;
    queryKey.push(search_text);
  }

  return useQuery({
    queryKey,
    queryFn: () =>
      axios.get(endpoints.project.listProjects(), {
        params: {
          project_type: "observe",
          ...(search_text ? { name: search_text } : {}),
        },
      }),
    select: (data) => data.data?.result?.projects,
    staleTime: 1 * 60 * 1000,
    enabled,
  });
};

const primaryGraphDropdown = "primaryGraphDropdown";

export const createCachePrimaryFilter = (userId, projectId, value) => {
  try {
    const existing = JSON.parse(
      localStorage.getItem(primaryGraphDropdown) || "{}",
    );

    if (!existing[userId]) {
      existing[userId] = {};
    }

    existing[userId][projectId] = value;

    localStorage.setItem(primaryGraphDropdown, JSON.stringify(existing));
  } catch (error) {
    logger.error("Error saving cache primary filter:", error);
  }
};

export const getCachePrimaryFilter = (userId, projectId) => {
  try {
    const existing = JSON.parse(
      localStorage.getItem(primaryGraphDropdown) || "{}",
    );
    return existing[userId]?.[projectId] || "";
  } catch (error) {
    return "";
  }
};

const LoadingHeader = () =>
  React.createElement(Skeleton, { variant: "text", width: 100, height: 20 });

export const TRACE_DEFAULT_COLUMNS = [
  {
    headerComponent: LoadingHeader,
    field: "trace_name",
    flex: 2,
    minWidth: 250,
  },
  { headerComponent: LoadingHeader, field: "input", flex: 2, minWidth: 200 },
  { headerComponent: LoadingHeader, field: "output", flex: 2, minWidth: 200 },
  {
    headerComponent: LoadingHeader,
    field: "start_time",
    flex: 1,
    minWidth: 170,
  },
  { headerComponent: LoadingHeader, field: "status", flex: 0, minWidth: 100 },
  { headerComponent: LoadingHeader, field: "latency", flex: 0, minWidth: 120 },
  {
    headerComponent: LoadingHeader,
    field: "total_tokens",
    flex: 1,
    minWidth: 200,
  },
  { headerComponent: LoadingHeader, field: "cost", flex: 0, minWidth: 130 },
  { headerComponent: LoadingHeader, field: "model", flex: 1, minWidth: 130 },
  { headerComponent: LoadingHeader, field: "tags", flex: 1, minWidth: 150 },
  { headerComponent: LoadingHeader, field: "user_id", flex: 1, minWidth: 120 },
];

export const SPAN_DEFAULT_COLUMNS = [
  {
    headerComponent: LoadingHeader,
    field: "operation_name",
    flex: 2,
    minWidth: 250,
  },
  { headerComponent: LoadingHeader, field: "status", flex: 0, minWidth: 100 },
  { headerComponent: LoadingHeader, field: "input", flex: 2, minWidth: 200 },
  { headerComponent: LoadingHeader, field: "output", flex: 2, minWidth: 200 },
  { headerComponent: LoadingHeader, field: "duration", flex: 0, minWidth: 120 },
  {
    headerComponent: LoadingHeader,
    field: "total_tokens",
    flex: 0,
    minWidth: 110,
  },
  { headerComponent: LoadingHeader, field: "cost", flex: 0, minWidth: 110 },
  { headerComponent: LoadingHeader, field: "model", flex: 1, minWidth: 130 },
  {
    headerComponent: LoadingHeader,
    field: "start_time",
    flex: 1,
    minWidth: 140,
  },
];

// Column-specific width overrides for a polished layout
const COLUMN_SIZE_MAP = {
  trace_name: { minWidth: 250, flex: 2 },
  name: { minWidth: 250, flex: 2 },
  status: { minWidth: 130, maxWidth: 160, flex: 0 },
  latency: { minWidth: 100, maxWidth: 140, flex: 0 },
  latency_ms: { minWidth: 100, maxWidth: 140, flex: 0 },
  total_tokens: { minWidth: 200, flex: 1 },
  prompt_tokens: { minWidth: 100, maxWidth: 130, flex: 0 },
  completion_tokens: { minWidth: 100, maxWidth: 130, flex: 0 },
  total_cost: { minWidth: 130, flex: 0 },
  cost: { minWidth: 130, flex: 0 },
  model: { minWidth: 120, flex: 1 },
  start_time: { minWidth: 170, flex: 1 },
  input: { minWidth: 200, flex: 2 },
  output: { minWidth: 200, flex: 2 },
  tags: { minWidth: 150, flex: 1 },
  labels: { minWidth: 150, flex: 1 },
  user_id: { minWidth: 120, flex: 1 },
  observation_levels: { minWidth: 140, maxWidth: 200, flex: 0 },
  operation_name: { minWidth: 250, flex: 2 },
  duration: { minWidth: 100, maxWidth: 140, flex: 0 },
};

// Override display names for specific columns
const COLUMN_NAME_OVERRIDES = {
  start_time: "Timestamp",
  total_tokens: "Tokens",
  cost: "Total Cost",
  observation_levels: "Observation Levels",
};

export const getTraceListColumnDefs = (col) => {
  const colId = col?.id;
  const isInputOutput = colId === "input" || colId === "output";
  const isCustomColumn = col?.groupBy === "Custom Columns";
  const isReasonColumn = col?.sourceField === "reason";
  const sizeOverrides = COLUMN_SIZE_MAP[colId] || {};

  // Eval, annotation, and custom columns need wider minWidth for readable names.
  // Reason columns are text-heavy so default to an even wider min width.
  const isEvalOrAnnotation =
    col?.groupBy === "Evaluation Metrics" ||
    col?.groupBy === "Annotation Metrics";
  const defaultMinWidth = isReasonColumn
    ? 240
    : isCustomColumn
      ? 180
      : isEvalOrAnnotation
        ? 150
        : 80;

  // Custom columns need a valueGetter that handles dot-notation keys
  // and extracts values from flat row data (attributes included by backend).
  const getCustomValueGetter = () => {
    return {
      valueGetter: (params) => {
        if (!params.data) return null;
        let value = params.data[colId];
        // Try dot-notation traversal
        if (value === undefined && colId.includes(".")) {
          value = colId
            .split(".")
            .reduce((obj, key) => obj?.[key], params.data);
        }
        if (value === undefined || value === null) return null;
        if (Array.isArray(value) || typeof value === "object") {
          return JSON.stringify(value);
        }
        return String(value);
      },
    };
  };

  return {
    headerName: COLUMN_NAME_OVERRIDES[colId] || col.name,
    ...(isCustomColumn ? { colId: col.id } : { field: col.id }),
    hide: !col?.isVisible,
    col,
    minWidth: defaultMinWidth,
    flex: 1,
    resizable: true,
    ...sizeOverrides,
    headerComponent: CustomTraceHeaderRenderer,
    headerComponentParams: {
      group: col?.groupBy,
    },
    // Use valueGetter for input/output columns to normalize objects to strings
    // This prevents flickering when polling because AG Grid compares normalized strings
    // instead of object references
    ...(isCustomColumn
      ? getCustomValueGetter()
      : isInputOutput
        ? {
            valueGetter: (params) => {
              const value = params.data?.[colId];
              if (isCellValueEmpty(value)) {
                return null;
              }
              // Normalize objects to JSON strings for stable comparison
              if (typeof value === "object") {
                return JSON.stringify(value);
              }
              return value;
            },
          }
        : {}),
    cellStyle: (params) => {
      const value = params.value;
      if (isCellValueEmpty(value)) {
        return {
          display: "flex",
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
    // Add tooltip for input/output and custom columns — both can contain
    // long text or JSON payloads that overflow the cell; tooltip lets the
    // user read the full value on hover.
    ...(col?.id === "input" || col?.id === "output" || isCustomColumn
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
  };
};
export const generateAnnotationColumnsForTracing = (
  items = [],
  expandedMetrics = [],
) => {
  if (!items.length) {
    return [];
  }

  const grouping = {};
  for (const eachCol of items) {
    if (!grouping[eachCol?.groupBy]) {
      grouping[eachCol?.groupBy] = [eachCol];
    } else {
      grouping[eachCol?.groupBy].push(eachCol);
    }
  }

  return Object.values(grouping).flatMap((metrics) =>
    metrics.flatMap((metric) => {
      const metricId = metric?.id;
      const displayName = metric?.name?.replace(/_/g, " ") || metricId;
      const outputType = metric?.annotationLabelType;
      const settings = metric?.settings || {};
      const isExpanded =
        outputType === "text" || expandedMetrics.includes(metricId);

      if (!isExpanded) {
        return {
          headerName: displayName,
          field: metricId,
          flex: 1,
          minWidth: 200,
          headerComponent: AnnotationHeaderCellRenderer,
          headerComponentParams: {
            displayName: displayName,
            metricId,
            isTextType: outputType === "text",
            showActions: true,
          },
          valueGetter: (params) => {
            const metricData = params?.data?.[metricId];
            if (!metricData) return null;
            if (metricData.score !== undefined) return metricData.score;
            const { annotators: _, ...aggregates } = metricData;
            return Object.keys(aggregates)?.length > 0 ? aggregates : null;
          },
          cellRenderer: NewAnnotationCellRenderer,
          cellRendererParams: {
            annotationType: outputType,
            isAverage: true,
            settings,
            originType: "Tracing",
          },
        };
      }

      // Expanded columns stay flat so AG Grid does not create a tall global
      // grouped-header row that makes unrelated columns look oversized.
      const metricAnnotators = Object.values(metric?.annotators || {});

      const avgColumn = {
        headerName: "Avg",
        field: `${metricId}.score`,
        flex: 1,
        minWidth: 200,
        headerComponent: AnnotationHeaderCellRenderer,
        headerComponentParams: {
          displayName,
          metricId,
          isTextType: false,
          subLabel: "Avg",
          subLabelType: "average",
          showActions: true,
        },
        valueGetter: (params) => {
          const metricData = params?.data?.[metricId];
          if (!metricData) return null;
          if (metricData?.score !== undefined) return metricData?.score;
          const { annotators: _, ...aggregates } = metricData;
          return Object.keys(aggregates)?.length > 0 ? aggregates : null;
        },
        cellRenderer: NewAnnotationCellRenderer,
        cellRendererParams: {
          annotationType: outputType,
          isAverage: true,
          settings,
          originType: "Tracing",
        },
      };

      const annotatorColumns = metricAnnotators.map((annotator) => ({
        headerName: annotator?.user_name,
        field: `${metricId}.annotators.${annotator?.user_id}`,
        flex: 1,
        minWidth: 200,
        ...(outputType === "text" ? { wrapText: true, autoHeight: true } : {}),
        headerComponent: AnnotationHeaderCellRenderer,
        headerComponentParams: {
          displayName,
          metricId,
          isTextType: outputType === "text",
          subLabel: annotator?.user_name,
          subLabelType: "person",
          showActions: outputType === "text",
        },
        valueGetter: (params) => {
          const annotatorData =
            params?.data?.[metricId]?.annotators?.[annotator.user_id];
          if (!annotatorData) return null;
          if (annotatorData?.score !== undefined) return annotatorData?.score;

          return annotatorData.value ?? null;
        },
        cellRenderer: NewAnnotationCellRenderer,
        cellRendererParams: {
          annotationType: outputType,
          isAverage: false,
          settings,
          originType: "Tracing",
        },
      }));

      return [
        ...(outputType !== "text" ? [avgColumn] : []),
        ...annotatorColumns,
      ];
    }),
  );
};

export const DOC_LINKS = {
  llmTracing: "https://docs.futureagi.com/docs/observe",
  sessions: "https://docs.futureagi.com/docs/observe/features/session",
  evals: "https://docs.futureagi.com/docs/observe/features/evals",
  alerts: "https://docs.futureagi.com/docs/observe/features/alerts",
  users: "https://docs.futureagi.com/docs/tracing/manual/set-session-user-id",
  charts: "https://docs.futureagi.com/docs/observe/features/evals",
};

export const LLM_TABS = {
  TRACE: "trace",
  SPAN: "spans",
};

export const FILTER_FOR_HAS_EVAL = {
  columnId: "has_eval",
  filterConfig: {
    filterType: "boolean",
    filterOp: "equals",
    filterValue: true,
  },
};

export const FILTER_FOR_ERRORS = {
  column_id: "status",
  filter_config: {
    filter_type: "text",
    filter_op: "equals",
    filter_value: "ERROR",
  },
};

export const FILTER_FOR_NON_ANNOTATED = {
  column_id: "has_annotation",
  filter_config: {
    filter_type: "boolean",
    filter_op: "equals",
    filter_value: false,
  },
};
