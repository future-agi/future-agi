import { filterDefinition as sessionFilterDefinition } from "src/sections/projects/SessionsView/common";

export const SESSION_DATE_FILTER_COLUMN = "created_at";

function sessionFilterTypeToPanelType(type) {
  if (type === "number") return "number";
  if (type === "date" || type === "datetime" || type === "timestamp") {
    return "datetime";
  }
  if (type === "boolean") return "boolean";
  return "string";
}

export function buildSessionSelectorFilterFields(columns = []) {
  const fields = sessionFilterDefinition.map((field) => ({
    id: field.propertyId,
    name: field.propertyName,
    category: "system",
    type: sessionFilterTypeToPanelType(field.filterType?.type),
  }));
  const seen = new Set(fields.map((field) => field.id));

  (columns || []).forEach((column) => {
    if (!column?.id || seen.has(column.id)) return;
    const name = column.name || column.headerName || column.id;
    fields.push({
      id: column.id,
      name,
      category:
        column.groupBy === "Annotation Metrics"
          ? "annotation"
          : column.groupBy === "Evaluation Metrics"
            ? "eval"
            : "system",
      type: sessionFilterTypeToPanelType(column.dataType || column.type),
    });
    seen.add(column.id);
  });

  return fields;
}

export function buildSessionSelectionFilters(
  mainFilters = [],
  dateFilterState,
) {
  const range = dateFilterState?.dateFilter;
  if (!range || !range[0] || !range[1]) return mainFilters || [];

  return [
    ...(mainFilters || []),
    {
      column_id: SESSION_DATE_FILTER_COLUMN,
      filter_config: {
        filter_type: "datetime",
        filter_op: "between",
        filter_value: [
          new Date(range[0]).toISOString(),
          new Date(range[1]).toISOString(),
        ],
      },
    },
  ];
}
