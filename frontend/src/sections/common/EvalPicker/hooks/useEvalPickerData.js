import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useDebounce } from "src/hooks/use-debounce";
import axios, { endpoints } from "src/utils/axios";
import { useEvalsList } from "src/sections/evals/hooks/useEvalsList";
import { paramsSerializer } from "src/utils/utils";

/**
 * Hook for fetching eval list data in the picker context.
 *
 * Uses the old getEvalsList endpoint (which returns ALL evals including system)
 * when a sourceId is available, falls back to listEvalTemplates otherwise.
 */
export function useEvalPickerData({
  sourceId = "",
  enabled = true,
  lockedFilters = null,
} = {}) {
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(25);
  const [sorting, setSorting] = useState([{ id: "lastUpdated", desc: true }]);
  const [filters, setFilters] = useState(null);

  const debouncedSearch = useDebounce(searchQuery.trim(), 500);

  const ownerFilter = filters?.owner || "all";
  const apiFilters = useMemo(() => {
    const f = {};
    if (filters?.eval_type) f.eval_type = filters.eval_type;
    if (filters?.output_type) f.output_type = filters.output_type;
    if (filters?.tags) f.tags = filters.tags;
    // Locked filters override and cannot be removed by the user.
    if (lockedFilters?.eval_type) f.eval_type = lockedFilters.eval_type;
    if (lockedFilters?.output_type) f.output_type = lockedFilters.output_type;
    return Object.keys(f).length > 0 ? f : null;
  }, [filters, lockedFilters]);

  // Use the old endpoint that returns ALL evals (system + user) when sourceId is available
  const oldEndpointQuery = useQuery({
    queryKey: ["eval-picker", "all-evals", sourceId, debouncedSearch],
    queryFn: async () => {
      const params = {};
      if (debouncedSearch) params.search_text = debouncedSearch;
      const { data } = await axios.get(
        endpoints.develop.eval.getEvalsList(sourceId),
        { params, paramsSerializer: paramsSerializer() },
      );
      return data?.result;
    },
    enabled: enabled && !!sourceId,
    keepPreviousData: true,
  });

  // Fallback to the new templates endpoint when no sourceId
  const SORT_FIELD_MAP = {
    name: "name",
    last_updated: "updated_at",
    created_by_name: "created_at",
  };
  const sortBy = sorting[0]
    ? SORT_FIELD_MAP[sorting[0].id] || "updated_at"
    : "updated_at";
  const sortOrder = sorting[0]?.desc ? "desc" : "asc";

  const newEndpointQuery = useEvalsList({
    page,
    pageSize,
    search: debouncedSearch || null,
    ownerFilter,
    filters: apiFilters,
    sortBy,
    sortOrder,
    enabled: enabled && !sourceId,
  });

  // Normalize response — the old endpoint returns { evals: [...], evalRecommendations: [...] }
  // The new endpoint returns { items: [...], total: N }
  const isUsingOldEndpoint = !!sourceId;
  const rawData = isUsingOldEndpoint
    ? oldEndpointQuery.data
    : newEndpointQuery.data;
  const isLoading = isUsingOldEndpoint
    ? oldEndpointQuery.isLoading
    : newEndpointQuery.isLoading;
  const isFetching = isUsingOldEndpoint
    ? oldEndpointQuery.isFetching
    : newEndpointQuery.isFetching;

  // True while the user is typing (debounce window) or the server is fetching
  // results. `isLoading` only flips during the initial load because the
  // queries use keepPreviousData, so we expose this as the search-in-progress
  // signal for the input adornment.
  const isSearchPending = searchQuery.trim() !== debouncedSearch;
  const isSearching = isSearchPending || isFetching;

  const items = useMemo(() => {
    if (!rawData) return [];
    if (isUsingOldEndpoint) {
      // Old endpoint: { evals: [...] }. As of the GetEvalsListView patch,
      // the backend now returns evalType / outputType / createdByName /
      // owner / updatedAt directly — we trust those fields. The old
      // tag-based inference is kept as a last-resort fallback for any
      // stale client/server version skew.
      const evals = rawData?.evals || [];
      return evals.map((e) => {
        const owner =
          e.owner || (e.type === "futureagi_built" ? "system" : "user");
        const evalType =
          e.eval_type ||
          (e.eval_template_tags?.includes("CODE_EVAL")
            ? "code"
            : e.eval_template_tags?.includes("AGENT_EVAL")
              ? "agent"
              : "llm");
        const createdByName =
          e.created_by_name || (owner === "system" ? "System" : "User");
        const templateId = e.template_id || e.eval_template_id || e.id;
        return {
          // IMPORTANT: the picker's config/detail flow operates on eval
          // TEMPLATE ids, not user-eval binding ids. Old getEvalsList
          // returns both (`id` = user eval, `template_id` = template). Use
          // templateId as the canonical row id so opening the config drawer
          // fetches the correct eval detail and renders the right type.
          id: templateId,
          templateId,
          // Only already-attached UserEvalMetric rows carry a distinct
          // `template_id` from the backend; catalog rows (preset / custom
          // templates) return `id = template.id` with no `template_id`.
          // Forwarding `e.id` on catalog rows would cause EvaluationDrawer
          // to route "Add" clicks to /edit_and_run_user_eval/<template_id>
          // and 404 with "Eval not found" (TH-4533).
          userEvalId: e.template_id ? e.id : undefined,
          name: e.name || e.eval_template_name,
          templateType: e.template_type || "single",
          evalType,
          outputType: e.output_type || e.output || "pass_fail",
          createdByName,
          lastUpdated: e.updated_at || e.created_at,
          currentVersion: e.current_version || null,
          isDraft: e.is_draft || false,
          requiredKeys: e.eval_required_keys || e.required_keys || [],
          description: e.description,
          model: e.model || e.selected_model,
          owner,
          evalTemplateTags: e.eval_template_tags,
          // Keep original for pass-through
          _original: e,
        };
      });
    }
    // New endpoint already returns normalized items; still ensure the
    // picker has a stable templateId alias for edit/config flows.
    return (rawData?.items || []).map((item) => ({
      ...item,
      templateId:
        item.templateId || item.template_id || item.eval_template_id || item.id,
    }));
  }, [rawData, isUsingOldEndpoint]);

  // Client-side filtering for the old endpoint. The `getEvalsList(sourceId)`
  // API only accepts `search_text` — it ignores eval_type / output_type /
  // owner / template_type filters. Without this layer the Filter popover
  // looked like it did nothing in the dataset flow.
  const filteredItems = useMemo(() => {
    if (!isUsingOldEndpoint) return items;
    if (!filters && !lockedFilters) return items;
    const evalTypes = lockedFilters?.eval_type || filters?.eval_type;
    const outputTypes = lockedFilters?.output_type || filters?.output_type;
    const owner = filters?.owner;
    const templateType = filters?.template_type;
    const tags = filters?.tags;
    const nameMatch = filters?.search;
    return items.filter((it) => {
      if (evalTypes?.length && !evalTypes.includes(it.evalType)) return false;
      if (outputTypes?.length && !outputTypes.includes(it.outputType))
        return false;
      if (owner && owner !== "all" && it.owner !== owner) return false;
      if (templateType && it.templateType !== templateType) return false;
      if (tags?.length && !tags.some((t) => it.evalTemplateTags?.includes(t)))
        return false;
      if (
        nameMatch &&
        !it.name?.toLowerCase().includes(nameMatch.toLowerCase())
      )
        return false;
      return true;
    });
  }, [items, isUsingOldEndpoint, filters, lockedFilters]);

  const total = isUsingOldEndpoint ? filteredItems.length : rawData?.total || 0;

  // Client-side pagination for old endpoint (it returns all evals at once)
  const paginatedItems = useMemo(() => {
    if (!isUsingOldEndpoint) return filteredItems;
    const start = page * pageSize;
    return filteredItems.slice(start, start + pageSize);
  }, [filteredItems, isUsingOldEndpoint, page, pageSize]);

  return {
    items: paginatedItems,
    total,
    isLoading,
    isSearching,
    searchQuery,
    setSearchQuery,
    page,
    setPage,
    pageSize,
    setPageSize,
    sorting,
    setSorting,
    filters,
    setFilters,
  };
}
