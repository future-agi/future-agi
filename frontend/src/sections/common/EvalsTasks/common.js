import { endOfToday, sub } from "date-fns";
import EvalsAndTasksCustomTooltip from "./Renderers/EvalsAndTasksCustomToolTip";
import FilterChipsRenderer from "./Renderers/FilterChipsRenderer";
import RunningStatusRenderer from "./Renderers/RunningStatusRenderer";
import _ from "lodash";
import { dateValueFormatter } from "src/utils/dateTimeUtils";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { formatDate } from "src/utils/report-utils";
import { canonicalEntries } from "src/utils/utils";
import { NULL_OPERATORS } from "src/components/ComplexFilter/common";

// Operator categories shared by the task filter wire builders (validation.js,
// TaskLivePreview) and TaskFilterBar.
export const RANGE_OPS = new Set(["between", "not_between"]);
export const LIST_OPS = new Set(["in", "not_in"]);
export const NO_VALUE_OPS = new Set(NULL_OPERATORS);

export const getEvalsTaskColumnConfig = (observeId) => {
  const columns = [
    {
      headerName: "Task Name",
      field: "name",
      minWidth: 200,
      headerClass: "custom-header-text",
    },
    {
      headerName: "Filters Applied",
      field: "filters_applied",
      sortable: false,
      filter: false,
      headerClass: "custom-header-text",
      minWidth: 300,
      cellRenderer: FilterChipsRenderer,
      tooltipValueGetter: (params) => {
        const filters = params.value || [];
        return filters.join(", ");
      },
      tooltipComponent: EvalsAndTasksCustomTooltip,
      valueGetter: (params) => {
        const filters = [];
        const observation_types =
          params?.data?.filters_applied?.observation_type ?? [];
        if (observation_types?.length > 0) {
          filters.push(
            `Span Type is ${_.toUpper(params?.data?.filters_applied?.observation_type)}`,
          );
        }

        // Canonical key is `filters`; `span_attributes_filters` is the
        // legacy form still present on un-migrated rows.
        const spanAttributes =
          params?.data?.filters_applied?.filters ??
          params?.data?.filters_applied?.span_attributes_filters ??
          [];

        if (spanAttributes.length > 0) {
          const customAttributeString = `Custom attribute is ${spanAttributes
            .map((f) => `(${f.column_id})`)
            .join(",")}`;

          filters.push(customAttributeString);
        }
        return filters;
      },
    },
    {
      headerName: "Evals Applied",
      headerClass: "custom-header-text",
      field: "evals_applied",
      sortable: false,
      filter: false,
      minWidth: 300,
      cellRenderer: FilterChipsRenderer,
      tooltipValueGetter: (params) => {
        const filters = params?.data?.evals_applied || [];
        return filters.join(", ");
      },
      tooltipComponent: EvalsAndTasksCustomTooltip,
    },
    {
      headerName: "Sampling Rate",
      field: "sampling_rate",
      headerClass: "custom-header-text",
      minWidth: 200,
      valueFormatter: (params) => {
        return `${params.value}%`;
      },
    },
    {
      headerName: "Date Created",
      field: "created_at",
      headerClass: "custom-header-text",
      minWidth: 200,
      valueFormatter: dateValueFormatter,
    },
    {
      headerName: "Run Status",
      headerClass: "custom-header-text",
      field: "status",
      minWidth: 200,
      cellRenderer: RunningStatusRenderer,
    },
    {
      headerName: "Last Run",
      headerClass: "custom-header-text",
      field: "last_run",
      minWidth: 200,
      valueFormatter: dateValueFormatter,
    },
  ];

  if (!observeId) {
    columns.splice(1, 0, {
      headerName: "Project Name",
      headerClass: "custom-header-text",
      field: "project_name",
      minWidth: 200,
    });
  }

  return columns;
};

export const EvalTaskFilterDefinition = (observeId) => {
  const filters = [
    {
      propertyName: "Task Name",
      propertyId: "name",
      filterType: { type: "text" },
      defaultFilter: "contains",
    },
    {
      propertyName: "Sampling Rate",
      propertyId: "sampling_rate",
      filterType: { type: "number" },
    },
    {
      propertyName: "Date Created",
      propertyId: "created_at",
      filterType: { type: "date" },
    },
    {
      propertyName: "Run Status",
      propertyId: "status",
      filterType: {
        type: "option",
        options: [
          {
            value: "running",
            label: "Running",
          },
          {
            value: "completed",
            label: "Completed",
          },
          {
            value: "failed",
            label: "Failed",
          },
          {
            value: "pending",
            label: "Pending",
          },
          {
            value: "paused",
            label: "Paused",
          },
        ],
      },
    },
    {
      propertyName: "Last Run",
      propertyId: "lastRun",
      filterType: { type: "date" },
    },
  ];

  if (!observeId) {
    filters.splice(1, 0, {
      propertyName: "Project Name",
      propertyId: "projectName",
      filterType: { type: "text" },
      defaultFilter: "contains",
    });
  }

  return filters;
};

// TraceFilterPanel `fieldCategory` → BE col_type enum. Fallback only —
// the row's `apiColType` (set by TraceFilterPanel via metricToTraceFilterProperty)
// is the source of truth when present.
export const FIELD_CATEGORY_TO_COL_TYPE = {
  attribute: "SPAN_ATTRIBUTE",
  system: "SYSTEM_METRIC",
  eval: "EVAL_METRIC",
  annotation: "ANNOTATION",
};

// Column ids the BE always routes through its annotation handler regardless
// of col_type. Pin them to ANNOTATION on the wire so the dispatcher doesn't
// also feed them to SPAN_ATTRIBUTE / SYSTEM_METRIC handlers.
export const ANNOTATION_COLUMN_IDS = new Set(["annotator", "my_annotations"]);

// Reserved metadata keys on the saved BE filters dict — every other key
// becomes a generic system filter row.
const RESERVED_FILTER_KEYS = new Set([
  "project_id",
  "date_range",
  "start_date",
  "end_date",
  "filters",
  "span_attributes_filters",
]);

// Legacy → current vocabulary aliases for the system-filter hydration path.
const FILTER_KEY_ALIAS = {
  observation_type: "span_kind",
};

export const formatTaskFilters = (filters_applied) => {
  if (!filters_applied) return [];

  // Attribute filters carry a {columnId, filterConfig} shape. Prefer the
  // canonical `filters` key; fall back to legacy `span_attributes_filters`.
  // `colType` round-trips as `apiColType` so the panel picks the right chip.
  const span_attributes_filters = (
    filters_applied.filters ||
    filters_applied.span_attributes_filters ||
    []
  ).map((i) => ({
    property: "attributes",
    propertyId: i?.columnId,
    apiColType: i?.filterConfig?.colType,
    filterConfig: {
      filterType: i?.filterConfig?.filterType,
      filterOp: i?.filterConfig?.filterOp,
      filterValue: i?.filterConfig?.filterValue,
      colType: i?.filterConfig?.colType,
    },
  }));

  // Every other top-level key → one generic system-filter row per value.
  // canonicalEntries skips the camelCase aliases the axios interceptor adds
  // (avoids duplicate chips + correctly filters reserved keys).
  const systemFilters = [];
  canonicalEntries(filters_applied).forEach(([key, vals]) => {
    if (RESERVED_FILTER_KEYS.has(key)) return;
    const field = FILTER_KEY_ALIAS[key] || key;
    const arr = Array.isArray(vals) ? vals : [vals];
    arr.forEach((v) => {
      if (v === undefined || v === null || v === "") return;
      systemFilters.push({
        property: field,
        filterConfig: {
          filterType: typeof v === "number" ? "number" : "text",
          filterOp: "equals",
          filterValue: v,
        },
      });
    });
  });

  return [...systemFilters, ...span_attributes_filters];
};

export const getDefaultTaskValues = (data, observeId) => {
  if (data) {
    const values = {
      name: data?.name,
      project: data?.project_id,
      filters: formatTaskFilters(data?.filters_applied || {}),
      spansLimit: Number(data?.spans_limit),
      samplingRate: Number(data?.sampling_rate),
      evalsDetails: data?.evals_applied,
      runType: data?.run_type,
      rowType: data?.row_type ?? "spans",
      startDate: formatDate(
        sub(new Date(), {
          months: 6,
        }),
      ),
      endDate: formatDate(endOfToday()),
    };

    if (data?.run_type != "continuous") {
      const startDateValue =
        data?.filters_applied?.start_date ||
        data?.filters_applied?.date_range?.[0];
      const endDateValue =
        data?.filters_applied?.end_date ||
        data?.filters_applied?.date_range?.[1];

      if (startDateValue) {
        values.startDate = formatDate(new Date(startDateValue));
      }
      if (endDateValue) {
        values.endDate = formatDate(new Date(endDateValue));
      }
    }

    return values;
  } else {
    return {
      name: "",
      project: observeId ? observeId : "",
      filters: [],
      spansLimit: "",
      samplingRate: 100,
      evalsDetails: [],
      rowType: "spans",
      startDate: formatDate(
        sub(new Date(), {
          months: 6,
        }),
      ),
      endDate: formatDate(endOfToday()),
      runType: "",
    };
  }
};

export const useGetTaskData = (taskId, options) => {
  return useQuery({
    ...options,
    queryKey: ["taskDetails", taskId],
    queryFn: () => axios.get(endpoints.project.getEvalTaskDetails(taskId)),
    select: (d) => d?.data?.result,
    retry: (failureCount, error) => {
      const status = error?.statusCode || error?.response?.status;
      if (status === 400 || status === 404) return false;
      return failureCount < 1;
    },
  });
};
