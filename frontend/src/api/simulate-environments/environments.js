import {
  useMutation,
  useQuery,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import {
  createHarnessJob,
  harnessIdempotencyKey,
} from "src/api/harness/harness";
import { draftToPreflightPayload } from "src/api/simulate-environments/preflightPayload";
import {
  listHarnessEnvironments,
  deleteHarnessEnvironment,
} from "src/api/simulate-environments/harnessEnvironments";
import { harnessEnvToRow } from "src/sections/simulate/environments/helpers/harnessJobToRow";

export const SIMULATE_ENVIRONMENTS_KEY = ["simulate-environments"];
// The prefix every page of the list shares — invalidating it refetches whatever
// page is currently shown.
export const myEnvironmentsListKey = () => [...SIMULATE_ENVIRONMENTS_KEY, "list"];
export const myEnvironmentsQueryKey = (page = 0, pageSize = 25) => [
  ...myEnvironmentsListKey(),
  { page, pageSize },
];

// Map the RAW paginated payload ({ count, …, results }) to the page the table
// needs: the mapped rows plus the server's total row count for the pager.
const toPage = (data) => ({
  rows: (Array.isArray(data?.results) ? data.results : []).map(harnessEnvToRow),
  total: data?.count ?? 0,
});

// Server-paginated: `page` is the table's 0-indexed page, the endpoint is
// 1-indexed. keepPreviousData holds the current page on screen while the next
// one loads, so paging doesn't flash an empty table.
export function useMyEnvironments({ page = 0, pageSize = 25 } = {}) {
  return useQuery({
    queryKey: myEnvironmentsQueryKey(page, pageSize),
    queryFn: () => listHarnessEnvironments({ page: page + 1, limit: pageSize }),
    select: toPage,
    placeholderData: keepPreviousData,
  });
}

export function useDeleteEnvironment() {
  const queryClient = useQueryClient();
  return useMutation({
    // The caller shows its own error snackbar; suppress the global one.
    meta: { errorHandled: true },
    mutationFn: (id) => deleteHarnessEnvironment(id),
    // Refetch the visible page (its rows shift up from later pages), rather than
    // filtering one page in place — which server pagination can't do correctly.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: myEnvironmentsListKey() });
    },
  });
}

// Create the real harness job for a build. The draft is mapped to the same
// schema-valid body the preflight used (`draftToPreflightPayload`) — per the
// contract, a create body equals its validated preflight body. Only the
// server-minted job id is returned; the raw draft is never echoed back, since
// it can carry source details that would then sit in the mutation cache.
//
// A draft that cannot be built for real (`{ skipped }`) keeps the client-minted
// id, so the mock building path is unchanged for those sources.
export function useBuildEnvironment() {
  const queryClient = useQueryClient();
  return useMutation({
    // The caller surfaces the failure itself (a snackbar on the build page), so
    // opt out of the global mutation-error toast to avoid a double message.
    meta: { errorHandled: true },
    mutationFn: async (draft) => {
      const built = draftToPreflightPayload(draft);
      if (built.skipped) {
        return { envId: `env-${Date.now().toString(36)}`, skipped: built.skipped };
      }
      const dto = await createHarnessJob(built.payload, harnessIdempotencyKey());
      return { envId: dto.job.job_id };
    },
    onSuccess: (result) => {
      // A real job now exists on the server, so the next My-Environments read
      // must include it. The skip path minted a client-side id and created
      // nothing, so it leaves the list alone.
      if (!result.skipped) {
        queryClient.invalidateQueries({ queryKey: myEnvironmentsListKey() });
      }
    },
  });
}

// TODO: POST to /secret-files (uploadHarnessSecretFile) — never inline
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

// TODO: swap to axios.post(endpoints.simulateEnvironments.adopt, { templateId })
// Adopting a prebuilt template mints a fresh environment instance from the
// library entry. Return only the server-minted id — never echo the template
// back into the mutation cache.
export function useAdoptTemplate() {
  return useMutation({
    mutationFn: async () => ({
      envId: `env-${Math.random().toString(36).slice(2, 10)}`,
    }),
  });
}

// TODO: axios.post(endpoints.simulateEnvironments.run(envId))
export function useRunSimulation() {
  return useMutation({
    mutationFn: async (envId) => ({
      envId,
      runId: `run-${Date.now().toString(36)}`,
    }),
  });
}
