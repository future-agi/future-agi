import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { runResultsKey } from "src/api/develop/run-results";
import { resultsIdsParams } from "src/sections/common/filters/apiCallModel";

// W5-2 · a dataset's custom result columns (GET / PUT result-columns) and
// their values over a results view (GET result-columns/values). The door is
// shown only where the endpoints are registered (integration), so nothing points at
// an unanswered path.

/** True when the custom-column endpoints are registered. */
export const customColumnsAvailable = () =>
  typeof endpoints.develop?.w5?.resultColumns === "function";

export const customColumnsKey = (datasetId) => ["w5-result-columns", datasetId];

/**
 * The values of custom columns for a results view (the R3 params of that
 * view); `draft` (a definition) previews an unsaved column as "draft".
 */
export async function fetchCustomColumnValues(
  datasetId,
  view,
  { columnIds, draft } = {},
) {
  const { ids_only: _ids, ...params } = resultsIdsParams(view);
  const res = await axios.get(
    endpoints.develop.w5.resultColumnValues(datasetId),
    {
      params: {
        ...params,
        ...(columnIds?.length ? { columns: columnIds.join(",") } : {}),
        ...(draft ? { draft: JSON.stringify(draft) } : {}),
      },
    },
  );
  return res.data?.result ?? {};
}

/**
 * `{available, columns, isLoading, save(columns) → Promise<columns>}`.
 * Saving refreshes every results query of the dataset (filters may use the
 * columns as `col.<name>`).
 */
export default function useCustomColumns(datasetId, { enabled = true } = {}) {
  const available = customColumnsAvailable();
  const queryClient = useQueryClient();
  const key = customColumnsKey(datasetId);
  const query = useQuery({
    queryKey: key,
    queryFn: () =>
      axios
        .get(endpoints.develop.w5.resultColumns(datasetId))
        .then((r) => r.data?.result?.columns ?? []),
    enabled: available && enabled && !!datasetId,
    staleTime: 10_000,
  });
  const mutation = useMutation({
    mutationFn: (columns) =>
      axios
        .put(endpoints.develop.w5.resultColumns(datasetId), { columns })
        .then((r) => r.data?.result?.columns ?? []),
    onSuccess: (columns) => {
      queryClient.setQueryData(key, columns);
      queryClient.invalidateQueries({ queryKey: runResultsKey(datasetId) });
    },
  });
  return {
    available,
    columns: query.data ?? [],
    isLoading: query.isLoading,
    save: mutation.mutateAsync,
    saving: mutation.isPending,
  };
}
