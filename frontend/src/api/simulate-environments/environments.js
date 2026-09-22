import {
  useMutation,
  useQuery,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import {
  createHarnessJob,
  harnessIdempotencyKey,
  uploadHarnessSecretFile,
} from "src/api/harness/harness";
import { draftToPreflightPayload } from "src/api/simulate-environments/preflightPayload";
import {
  listHarnessEnvironments,
  deleteHarnessEnvironment,
  renameHarnessEnvironment,
  deleteAppliedEvaluation,
  getAvailableEvaluations,
  addEvaluation,
} from "src/api/simulate-environments/harnessEnvironments";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";
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

// §8 rename. The only editable field is the name; the response is the full §6
// detail body with `overview.name` updated, so seed the detail cache from it
// (no refetch) and invalidate the list so the row's name changes there too.
// Blank/too-long/unknown-field bodies come back 400 — the caller surfaces it.
export function useRenameEnvironment() {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ id, name }) => renameHarnessEnvironment(id, name),
    onSuccess: (detail, { id }) => {
      if (detail) queryClient.setQueryData(harnessEnvironmentKey(id), detail);
      queryClient.invalidateQueries({ queryKey: myEnvironmentsListKey() });
    },
  });
}

// §9 remove an applied evaluation (soft delete → 204). The contract says to
// re-fetch §6 and read `evaluations.selected` rather than dropping the row
// locally, so this invalidates the detail query. The caller must surface the
// error: 409 while still building, 404 if already removed (safe to retry).
export function useRemoveAppliedEvaluation() {
  const queryClient = useQueryClient();
  return useMutation({
    // Let the failure surface (409 while building, 404 already-removed): the
    // caller keeps the row on error, so a silent global-toast opt-out would hide
    // that the removal did not take.
    mutationFn: ({ id, evalConfigId }) =>
      deleteAppliedEvaluation(id, evalConfigId),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(id) });
    },
  });
}

// §10 the evaluations this environment can still add (catalogue filtered to its
// modality, minus what is already selected). Drives the add-eval picker.
export const availableEvaluationsKey = (envId) => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "available-evals",
  envId,
];

export function useAvailableEvaluations(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: availableEvaluationsKey(envId),
    queryFn: () => getAvailableEvaluations(envId),
    enabled: Boolean(envId) && enabled,
    select: (data) => (Array.isArray(data?.evaluations) ? data.evaluations : []),
  });
}

// §10 add an evaluation by name (mapping is resolved server-side by modality).
// The 201 body is the full §6 detail with the new row in evaluations.selected,
// so seed the detail cache from it and refresh the available list (the added
// name drops out of it). Idempotent server-side; 409 at the 8-eval cap or while
// building; the caller surfaces the failure.
export function useAddEvaluation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }) => addEvaluation(id, name),
    onSuccess: (detail, { id }) => {
      if (detail) queryClient.setQueryData(harnessEnvironmentKey(id), detail);
      queryClient.invalidateQueries({ queryKey: availableEvaluationsKey(id) });
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

// Upload a credential FILE to the vault (POST /secret-files/) and keep only the
// returned reference — the file bytes never enter the draft, store or cache. The
// panel default alias is the Google ADC JSON the credential_files check names.
// NOTE: the returned ref is a `harness_environment_file` ref, which the create
// schema does not yet accept, so this makes the ref real (vault-backed) but does
// not by itself let credential_files pass — that stays a backend follow-up.
export function useUploadSecretFile() {
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async ({ file, environmentName = "GOOGLE_APPLICATION_CREDENTIALS_JSON" }) => {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("environment_name", environmentName);
      const res = await uploadHarnessSecretFile(formData);
      return {
        secret_ref: res.secret_ref,
        environment_name: res.environment_name || environmentName,
        name: file.name,
        size: res.size ?? file.size,
      };
    },
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
