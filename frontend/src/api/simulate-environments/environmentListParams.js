// The My Environments filters, as the list keeps them, and the arguments
// listHarnessEnvironments takes for them. Every filter is single-value and
// server-side; a filter left on "All" is an empty string and isn't sent.
// Every set filter is sent, even ones the backend doesn't honour yet: it
// ignores those and returns the full list until it supports them.
export const EMPTY_ENV_FILTERS = Object.freeze({
  search: "",
  status: "",
  agentType: "",
  agentId: "",
});

// Whether any filter narrows the list. A search of only spaces doesn't.
export const hasActiveFilters = (filters = EMPTY_ENV_FILTERS) =>
  Boolean(
    (filters.search || "").trim() ||
      filters.status ||
      filters.agentType ||
      filters.agentId,
  );

// `page` is the table's 0-based page; the endpoint counts from 1.
export const buildEnvironmentListParams = ({
  page = 0,
  pageSize = 25,
  filters = EMPTY_ENV_FILTERS,
} = {}) => {
  const search = (filters.search || "").trim();
  return {
    page: page + 1,
    limit: pageSize,
    ...(search && { search }),
    ...(filters.status && { status: filters.status }),
    ...(filters.agentType && { agentType: filters.agentType }),
    ...(filters.agentId && { agentDefinitionId: filters.agentId }),
  };
};
