import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { EMPTY_ENV_FILTERS } from "src/api/simulate-environments/environmentListParams";

// The My Environments filters live in the page URL, so a filtered list
// survives a refresh and can be shared. Other params (the tab) are kept.
const URL_KEYS = {
  search: "search",
  status: "status",
  agentType: "agent_type",
  agentId: "agent_id",
};

export default function useEnvironmentFilters() {
  const [params, setParams] = useSearchParams();

  const filters = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(URL_KEYS).map(([key, urlKey]) => [
          key,
          params.get(urlKey) || "",
        ]),
      ),
    [params],
  );

  const setFilters = (next) => {
    const q = new URLSearchParams(params);
    Object.entries(URL_KEYS).forEach(([key, urlKey]) => {
      const value = (next?.[key] ?? EMPTY_ENV_FILTERS[key]) || "";
      // A search of only spaces isn't a filter, so it doesn't reach the URL.
      if (value.trim()) q.set(urlKey, value);
      else q.delete(urlKey);
    });
    setParams(q, { replace: true });
  };

  return { filters, setFilters };
}
