import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { runResultsKey } from "src/api/develop/run-results";
import { resultsIdsParams } from "src/sections/common/filters/apiCallModel";
import { routeOffered } from "src/sections/common/Collab/routeOffer";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";

// W5-2 · a dataset's custom result columns (GET / PUT result-columns) and
// their values over a results view (GET result-columns/values). The door is
// shown only where the endpoints are registered (integration), so nothing points at
// an unanswered path.

/** True when the custom-column endpoints are registered. */
export const customColumnsAvailable = () =>
  typeof endpoints.develop?.w5?.resultColumns === "function";

/** The custom-column API path templates. */
export const RESULT_COLUMNS_ROUTES = Object.freeze({
  columns: "/model-hub/datasets/{dataset_id}/result-columns/",
  values: "/model-hub/datasets/{dataset_id}/result-columns/values/",
});

/**
 * True when this build can call the custom-column routes. Where it cannot,
 * `Add column…` and `Save as a column…` stay on screen, turned off with the
 * shared line, nothing asks for the columns or their values, and a save is
 * refused with that line before any request.
 */
export const customColumnsOffered = () =>
  customColumnsAvailable() &&
  routeOffered(RESULT_COLUMNS_ROUTES.columns) &&
  routeOffered(RESULT_COLUMNS_ROUTES.values);

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
  if (!customColumnsOffered()) return {};
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
 * columns as `col.<name>`). Where the routes are not offered, `save` rejects
 * with the shared line and sends nothing.
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
    enabled: customColumnsOffered() && enabled && !!datasetId,
    staleTime: 10_000,
  });
  const mutation = useMutation({
    mutationFn: (columns) =>
      customColumnsOffered()
        ? axios
            .put(endpoints.develop.w5.resultColumns(datasetId), { columns })
            .then((r) => r.data?.result?.columns ?? [])
        : Promise.reject(new Error(NOT_IN_RELEASE)),
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
