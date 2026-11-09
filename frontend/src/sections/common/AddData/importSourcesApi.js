// the interface contract (lane w3-rows-in A7): IM1 import jobs and a dataset's saved import
// sources. Shapes: src/_mock/prototype/CONTRACT.md "the interface contract · implementation".
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

const R2 = () => endpoints.develop.r2;

export const importSourcesKey = (datasetId) => [
  "develop",
  "import-sources",
  datasetId,
];

/**
 * GET imports/jobs/{id}/ → `{job_id, state, done, total, dataset_id,
 * result}`; polls every second while the job runs.
 */
export function useImportJob(jobId) {
  return useQuery({
    queryKey: ["develop", "import-job", jobId],
    queryFn: () =>
      axios.get(R2().importJob(jobId)).then((res) => res.data?.result),
    enabled: Boolean(jobId),
    refetchInterval: (query) =>
      query?.state?.data?.state === "done" ? false : 1000,
    staleTime: 0,
  });
}

/** GET a dataset's saved sources (`[]` when none). */
export function useImportSources(datasetId, options = {}) {
  return useQuery({
    queryKey: importSourcesKey(datasetId),
    queryFn: () =>
      axios
        .get(R2().importSources(datasetId))
        .then((res) => res.data?.result?.sources ?? []),
    enabled: Boolean(datasetId),
    staleTime: 15 * 1000,
    ...options,
  });
}

function useSourcesMutation(datasetId, mutationFn) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: importSourcesKey(datasetId) }),
  });
}

/** POST `{name, import, window, on_refresh}` → the saved source. */
export const useCreateImportSource = (datasetId) =>
  useSourcesMutation(datasetId, (body) =>
    axios
      .post(R2().importSources(datasetId), body)
      .then((res) => res.data?.result),
  );

/** PATCH `{sourceId, name?, window?, on_refresh?, paused?}`. */
export const useUpdateImportSource = (datasetId) =>
  useSourcesMutation(datasetId, ({ sourceId, ...patch }) =>
    axios
      .patch(R2().importSource(datasetId, sourceId), patch)
      .then((res) => res.data?.result),
  );

/** DELETE one source. */
export const useDeleteImportSource = (datasetId) =>
  useSourcesMutation(datasetId, (sourceId) =>
    axios
      .delete(R2().importSource(datasetId, sourceId))
      .then((res) => res.data?.result),
  );

/** POST Refresh now → `{added, operations, version, trace_ids, since}`. */
export const useRefreshImportSource = (datasetId) =>
  useSourcesMutation(datasetId, (sourceId) =>
    axios
      .post(R2().importSourceRefresh(datasetId, sourceId), {})
      .then((res) => res.data?.result),
  );
