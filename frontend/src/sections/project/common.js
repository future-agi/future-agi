import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { buildApiFilterFromPanelRow } from "src/api/contracts/filter-contract";

export const useProjectDetails = ({
  page,
  pageLimit,
  debouncedSearchQuery,
  sort_by,
  sort_direction,
}) => {
  const { data, isLoading, error } = useQuery({
    queryKey: [
      "tracing-list-projects",
      page,
      pageLimit,
      debouncedSearchQuery,
      sort_by,
      sort_direction,
    ],

    queryFn: () =>
      axios.get(endpoints.project.projectObserveList, {
        params: {
          name: debouncedSearchQuery?.length ? debouncedSearchQuery : null,
          page_number: page - 1,
          page_size: pageLimit,
          project_type: "observe",
          ...(sort_by && { sort_by }),
          ...(sort_direction && { sort_direction }),
        },
      }),

    select: (response) => ({
      data: response?.data?.result?.table,
      totalPages: response?.data?.result?.metadata?.total_pages,
    }),
  });

  return {
    data: data?.data,
    totalPages: data?.totalPages,
    isLoading,
    error,
  };
};

// Observe project-list filter fields — `text` so the panel renders a single
// free-text value input (no value-options endpoint exists here).
export const PROJECT_FILTER_PROPERTIES = [
  { id: "name", name: "Name", category: "system", type: "text" },
  { id: "tags", name: "Tags", category: "system", type: "text" },
];

// Text search defaults to `contains` (substring), not the panel-wide `in`.
export const PROJECT_FILTER_DEFAULT_OPERATORS = { text: "contains" };

// Empty/added rows start on `contains` so picking a field doesn't flip it.
export const PROJECT_FILTER_DEFAULT_ROW = {
  field: "",
  fieldCategory: "system",
  operator: "contains",
  value: [],
};

// These screens have no create form; the project row is made at ingest by
// get_or_create_project, so every surface says that instead of promising one.
const PROJECT_CREATED_AT_INGEST =
  "Your project is created automatically when the first trace arrives.";

export const PROJECT_FTUX_COPY = {
  observe: {
    title: "Welcome to Observe",
    description: `${PROJECT_CREATED_AT_INGEST} Run the snippet below to send one.`,
  },
  experiment: {
    title: "Welcome to Prototype",
    description: `${PROJECT_CREATED_AT_INGEST} Run the snippet below to send one.`,
  },
};

// The populated list renders no snippet of its own; Add Project opens it.
export const PROJECT_LIST_SUBTITLE = `${PROJECT_CREATED_AT_INGEST} Add Project opens the snippet.`;

// Operators apply_project_list_filters can honor; hides the rest from the panel.
const PROJECT_FILTER_SUPPORTED_OPS = new Set([
  "in",
  "not_in",
  "contains",
  "not_contains",
]);
export const projectOperatorFilter = (op) =>
  PROJECT_FILTER_SUPPORTED_OPS.has(op.value);

// The panel emits the canonical trace vocab (`in`/`not_in`); the project-list
// backend only speaks equals/not_equals/contains/not_contains on strings.
const PANEL_OP_TO_BACKEND_OP = {
  in: "equals",
  not_in: "not_equals",
  contains: "contains",
  not_contains: "not_contains",
};

// Translate panel rows into the JSON `filters` param apply_project_list_filters
// parses. Unsupported ops drop; multi-value entries collapse to the first.
export const buildProjectListApiFilters = (filters) => {
  if (!filters?.length) return null;
  const out = filters
    .map((f) => {
      const backendOp = PANEL_OP_TO_BACKEND_OP[f.operator];
      if (!backendOp) return null;
      // Observe text fields always yield a string here; the array branch is
      // defensive for any future multi-value field wired into this flow.
      const value = Array.isArray(f.value) ? f.value[0] : f.value;
      if (value == null || value === "") return null;
      return buildApiFilterFromPanelRow({
        field: f.field,
        operator: backendOp,
        value,
      });
    })
    .filter(Boolean);
  return out.length ? JSON.stringify(out) : null;
};
