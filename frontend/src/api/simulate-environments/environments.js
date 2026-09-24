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
  addRunEvaluation,
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

// Rename. The only editable field is the name; the response is the full
// environment detail with `overview.name` updated, so seed the detail cache from it
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

// Remove an applied evaluation (soft delete → 204). Re-fetch the detail and
// read `evaluations.selected` rather than dropping the row locally. The caller
// must surface the error: 409 while still building, 404 if already removed.
//
// `onSettled`, not `onSuccess`: a 404 means the row is already gone on the
// server, so the list on screen is the stale one — refetching is exactly what
// reconciles it, and without that the row sits there refusing every retry
// until something else happens to refetch.
//
// Unlike the two add paths this also invalidates the offer list, which a
// remove genuinely stales (the eval can be added again). Nothing is observing
// it here — remove is pressed from the Evaluations tab with the picker closed
// — so this only marks it stale; there is no open drawer for a refetch to
// pull a row out from under.
export function useRemoveAppliedEvaluation() {
  const queryClient = useQueryClient();
  return useMutation({
    // The global handler only fires on `error?.result`, which this endpoint's
    // `{"detail": …}` body never has — `EvalsStep` shows the message itself.
    meta: { errorHandled: true },
    mutationFn: ({ id, evalConfigId }) =>
      deleteAppliedEvaluation(id, evalConfigId),
    onSettled: (_data, _error, { id }) => {
      queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(id) });
      queryClient.invalidateQueries({ queryKey: availableEvaluationsKey(id) });
    },
  });
}

// The evaluations this environment can still add (catalogue filtered to its
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

// THE RULE BOTH ADD PATHS FOLLOW: neither refetches anything while the picker
// that triggered it can still be open. Both adds are pressed from that drawer,
// and both of its lists are being observed by it, so a same-tick refetch pulls
// rows out from under the click that caused them — the offer list comes back
// without the just-added eval, and a detail read can land before the write it
// is meant to confirm and flip the row from "Added" back to "Add". The drawer
// refetches once, on the way out, where a stale read costs nothing.
//
// The cost of the rule: an add still in flight at that moment gets no refetch
// of its own, so the applied list, the tab badge and the pre-flight tile keep
// the last read until the drawer next closes. Nothing on screen is showing
// that list in the meantime, and the drawer's own handling of a pending
// mutation already leaves exactly this window open.
//
// Add an evaluation by name (the mapping is resolved server-side by modality).
// The 201 body is the full environment detail with the new row in
// `evaluations.selected`, so seed the detail cache from it — that is what
// flips the row to "Added", and it is the server's own answer rather than
// patched-up client state. Idempotent server-side; 409 at the 8-eval cap or
// while building; the caller surfaces it.
export function useAddEvaluation() {
  const queryClient = useQueryClient();
  return useMutation({
    // Same reasoning as `useRemoveAppliedEvaluation`'s opt-out; kept explicit
    // so `AddEvaluationDrawer`'s own Alert stays the single owner of the
    // message if the error shape ever changes.
    meta: { errorHandled: true },
    mutationFn: ({ id, name }) => addEvaluation(id, name),
    onSuccess: (detail, { id }) => {
      if (detail) queryClient.setQueryData(harnessEnvironmentKey(id), detail);
    },
  });
}

// Add an evaluation from inside a run. Same body as the environment-level add
// (`{ name }`), same refusals, but the 202 body is the five grading counts
// rather than the detail — so there is nothing to seed, and by the rule above
// nothing is invalidated here either. The receipt the counts render is this
// click's confirmation, and the drawer's close refetches the detail. The
// counts stay on the mutation (`mutation.data`) for the caller to render;
// they are a receipt for one click, not cached state.
export function useAddRunEvaluation() {
  return useMutation({
    // Same reasoning as `useAddEvaluation` above.
    meta: { errorHandled: true },
    mutationFn: ({ id, executionId, name }) =>
      addRunEvaluation(id, executionId, name),
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
