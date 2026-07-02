import CustomTraceRenderer from "./CustomRenderers/CustomTraceRenderer";
import { getRandomId } from "src/utils/utils";
import { avoidDuplicateFilterSet } from "../../ComplexFilter/common";
import { AnnotationLabelTypes } from "src/utils/constants";
import {
  getAnnotationMetricFilterDefinition,
  getAttributesDefinition,
  getEvaluationMetricFilterDefinition,
} from "../../../utils/prototypeObserveUtils";
import TotalRowsStatusBar from "src/sections/develop-detail/Common/TotalRowsStatusBar";

export const getTraceListColumnDefs = (col) => {
  return {
    headerName: col.name,
    field: col.id,
    hide: !col?.isVisible,
    cellRenderer: CustomTraceRenderer,
    col,
  };
};

export const getSpanListColumnDefs = (col) => {
  return {
    headerName: col.name,
    field: col.id,
    hide: !col?.isVisible,
    cellRenderer: CustomTraceRenderer,
    col,
  };
};

export const AllowedGroups = ["Evaluation Metrics", "Annotation Metrics"];

export const generateTraceFilterDefinition = (
  columns,
  attributes,
  existingFilter,
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
      propertyName: "Start Time",
      propertyId: "start_time",
      filterType: {
        type: "date",
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

  const evaluationMetricDef = getEvaluationMetricFilterDefinition(columns);
  finalDefinition.push(...evaluationMetricDef);

  const attributeDef = getAttributesDefinition(attributes, existingFilter);
  finalDefinition.push(...attributeDef);

  const annotationMetricDef = getAnnotationMetricFilterDefinition(columns);
  finalDefinition.push(...annotationMetricDef);

  return finalDefinition;
};

export const generateSpanFilterDefinition = (
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
  finalDefinition.push(...attributeDef);

  const annotationMetricDef = getAnnotationMetricFilterDefinition(columns);
  finalDefinition.push(...annotationMetricDef);

  return finalDefinition;
};

export const applyQuickFilters =
  (setFilters, openQuickFilter, setFilterOpen) =>
  ({ col, value, filterAnchor }) => {
    let filter = null;
    if (!col.groupBy) {
      filter = {
        column_id: col.id,
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: value,
        },
        _meta: {
          parentProperty: col.id,
        },
        id: getRandomId(),
      };

      if (col.id === "node_type") {
        filter.filter_config = {
          filter_type: "text",
          filter_op: "contains",
          filter_value: [value],
        };
      }
    } else if (col?.groupBy === "Evaluation Metrics") {
      openQuickFilter({
        filterAnchor,
        value,
        filter: {
          column_id: col.id,
          filter_config: {
            filter_type: "number",
            filter_op: "equals",
            filter_value: [value, ""],
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
        column_id: col.id,
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
            filter_config: {
              filter_type: "number",
              filter_op: "equals",
              filter_value: [value, ""],
            },
          };
          break;
        }
        case AnnotationLabelTypes.TEXT: {
          filter = {
            ...filter,
            filter_config: {
              filter_type: "text",
              filter_op: "equals",
              filter_value: value,
            },
          };
          break;
        }
        case AnnotationLabelTypes.THUMBS_UP_DOWN: {
          filter = {
            ...filter,
            filter_config: {
              filter_type: "boolean",
              filter_op: "equals",
              filter_value: value === "up" ? true : false,
            },
          };
          break;
        }
        case AnnotationLabelTypes.CATEGORICAL: {
          filter = {
            ...filter,
            filter_config: {
              filter_type: "text",
              filter_op: "contains",
              filter_value: value,
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
              filter_config: {
                filter_type: "number",
                filter_op: "equals",
                filter_value: [value, ""],
              },
            },
          });
          return;
        }
      }
    }

    if (filter) {
      setFilters((prev) => avoidDuplicateFilterSet(prev, filter));
      setFilterOpen(true);
    }
  };

export const statusBar = {
  statusPanels: [
    {
      statusPanel: TotalRowsStatusBar,
      align: "left",
    },
  ],
};
