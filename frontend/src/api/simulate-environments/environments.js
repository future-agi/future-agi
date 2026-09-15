import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { listHarnessJobs } from "src/api/harness/harness";
import { harnessJobToRow } from "src/sections/simulate/environments/helpers/harnessJobToRow";

export const SIMULATE_ENVIRONMENTS_KEY = ["simulate-environments"];
export const myEnvironmentsQueryKey = () => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "list",
];

// The mutation hooks below still mock TH-7962's endpoints behind a react-query
// surface; swapping each mutationFn to axios is a one-file change once the
// backend lands.

// The query cache holds the RAW harness-jobs payload ({ job, status }[]);
// `select` maps it to table rows on read, so the delete updater below must
// filter the raw shape, not the mapped rows.
const toRows = (data) =>
  (Array.isArray(data) ? data : []).map(harnessJobToRow);

// TODO(TH-7962): interim source — the My Environments table reads from the
// harness-jobs list and maps each job to a flat row. Several columns (see
// harnessJobToRow) have no field in this payload and render as placeholders;
// replace with the dedicated environments endpoint once it lands.
export function useMyEnvironments() {
  return useQuery({
    queryKey: myEnvironmentsQueryKey(),
    queryFn: listHarnessJobs,
    select: toRows,
  });
}

// TODO(TH-7962): axios.delete(endpoints.simulateEnvironments.detail(envId))
export function useDeleteEnvironment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (envId) => ({ id: envId }),
    onSuccess: ({ id }) => {
      // The cache holds raw { job, status } items, so match on job.job_id.
      queryClient.setQueryData(myEnvironmentsQueryKey(), (items) =>
        (items || []).filter((item) => item?.job?.job_id !== id),
      );
    },
  });
}

// TODO(TH-7962): axios.post(endpoints.simulateEnvironments.build, source)
// Return only the server-minted id — never echo the raw source back, since it
// can carry apiKey/envText secrets that would then sit in the mutation cache.
export function useBuildEnvironment() {
  return useMutation({
    mutationFn: async () => ({
      envId: `env-${Date.now().toString(36)}`,
    }),
  });
}

// TODO(TH-7962): POST to /secret-files (uploadHarnessSecretFile) — never inline
// file contents. The UI holds only the returned reference, not the bytes.
export function useUploadSecretFile() {
  return useMutation({
    mutationFn: async ({ file }) => ({
      secret_ref: `sref-${Math.random().toString(36).slice(2)}`,
      name: file.name,
      size: file.size,
    }),
  });
}

// TODO(TH-7962): axios.post(endpoints.simulateEnvironments.run(envId))
export function useRunSimulation() {
  return useMutation({
    mutationFn: async (envId) => ({
      envId,
      runId: `run-${Date.now().toString(36)}`,
    }),
  });
}
