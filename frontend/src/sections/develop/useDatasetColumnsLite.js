// GS-06 (options-truth L3 G10): a dataset's columns for the dataset list —
// the same request and cache entry as develop-detail's useGetDatasetColumns,
// without importing that module (it pulls the Data tab's cell renderers,
// markdown and code highlighting into the list page).
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

/** `["get-dataset-columns", id, {}]`: useGetDatasetColumns(id)'s key. */
export const datasetColumnsKey = (datasetId) => [
  "get-dataset-columns",
  datasetId,
  {},
];

export default function useDatasetColumnsLite(datasetId, options = {}) {
  return useQuery({
    queryKey: datasetColumnsKey(datasetId),
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetColumns(datasetId), {
        params: {},
      }),
    select: (d) => d.data?.result?.columns,
    staleTime: 60 * 1000,
    ...options,
    enabled: Boolean(datasetId) && options.enabled !== false,
  });
}
