import {
  useMutation,
  useQuery,
  useQueryClient,
  keepPreviousData,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
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
  addRunEvaluation,
} from "src/api/simulate-environments/harnessEnvironments";
import { harnessEnvironmentKey } from "src/api/simulate-environments/environment";
import { harnessEnvToRow } from "src/sections/simulate/environments/helpers/harnessJobToRow";
import { LIVE_ENV_STATUSES } from "src/sections/simulate/environments/myEnvironments.constants";

export const SIMULATE_ENVIRONMENTS_KEY = ["simulate-environments"];
// The prefix every page of the list shares — invalidating it refetches whatever
// page is currently shown.
export const myEnvironmentsListKey = () => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "list",
];
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

const LIST_REFETCH_MS = 3000;

// Server-paginated: `page` is the table's 0-indexed page, the endpoint is
// 1-indexed. keepPreviousData holds the current page on screen while the next
// one loads, so paging doesn't flash an empty table.
export function useMyEnvironments({ page = 0, pageSize = 25 } = {}) {
  return useQuery({
    queryKey: myEnvironmentsQueryKey(page, pageSize),
    queryFn: () => listHarnessEnvironments({ page: page + 1, limit: pageSize }),
    select: toPage,
    placeholderData: keepPreviousData,
    // Poll while a row on this page is still building or running, so it flips
    // to its final state without a reload. `select` never touches the cache, so
    // `query.state.data` is the raw payload with the backend's `status`; an
    // unknown status stops the poll rather than polling forever.
    refetchInterval: (query) =>
      (query.state.data?.results ?? []).some((row) =>
        LIVE_ENV_STATUSES.has(row?.status),
      )
        ? LIST_REFETCH_MS
        : false,
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
// This also invalidates every run test's eval list (the remove has only the
// environment id, not the run test's), which the picker's "Added evaluations"
// box reads, so a removed eval can be picked again.
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
      queryClient.invalidateQueries({
        queryKey: [...SIMULATE_ENVIRONMENTS_KEY, "run-test"],
      });
    },
  });
}

// A harness environment's evals live on its run test, like any simulation's,
// so the picker reads and adds them through the run test's own endpoints.
export const environmentRunTestKey = (runTestId) => [
  ...SIMULATE_ENVIRONMENTS_KEY,
  "run-test",
  runTestId,
];

// Every eval bound to the run test — including the result columns the harness
// reports itself — so the picker can show all of them as already added.
export function useEnvironmentRunTest(runTestId, { enabled = true } = {}) {
  return useQuery({
    queryKey: environmentRunTestKey(runTestId),
    // The drawer shows this read's failures itself: a Retry when nothing has
    // loaded, a warning when a refresh fails.
    meta: { errorHandled: true },
    queryFn: async () =>
      (await axios.get(endpoints.runTests.detail(runTestId))).data,
    enabled: Boolean(runTestId) && enabled,
    select: (data) =>
      Array.isArray(data?.simulate_eval_configs_detail)
        ? data.simulate_eval_configs_detail
        : [],
  });
}

// Add one eval with the person's own mapping, exactly as the simulation page
// does. Refreshing the run test's list here is what moves the new eval out of
// the picker's list and into its "Added evaluations" box.
export function useAddRunTestEval() {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: ({ runTestId, body }) =>
      axios.post(endpoints.runTests.addEvals(runTestId), {
        evaluations_config: [body],
      }),
    onSuccess: (_data, { runTestId }) => {
      queryClient.invalidateQueries({
        queryKey: environmentRunTestKey(runTestId),
      });
    },
  });
}

// The run-level add refetches nothing: its 202 is a receipt of grading counts,
// not the detail, so there is nothing to seed here, and the drawer refetches
// the environment detail once, when it closes, where a stale read costs
// nothing.
//
// Add an evaluation from inside a run, by name. Same refusals as the other add
// paths, but the 202 body is the five grading counts rather than the detail.
// The receipt the counts render is this click's confirmation. The counts stay
// on the mutation (`mutation.data`) for the caller to render; they are a
// receipt for one click, not cached state.
export function useAddRunEvaluation() {
  return useMutation({
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
        return {
          envId: `env-${Date.now().toString(36)}`,
          skipped: built.skipped,
        };
      }
      const dto = await createHarnessJob(
        built.payload,
        harnessIdempotencyKey(),
      );
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
// returned reference — the file bytes never enter the draft, store or cache.
//
// Two names are in play and they are not interchangeable. The upload endpoint
// accepts exactly one label, `GOOGLE_APPLICATION_CREDENTIALS` — Google's own
// variable, whose value is a file PATH — and 422s anything else. The vault
// stores the JSON text rather than a path, so the reply renames it
// `GOOGLE_APPLICATION_CREDENTIALS_JSON`, and that is the name every later step
// uses: the key `agent.secret_refs` is built under, the name the launch check
// scans for, and what the sandbox reads before writing the 0600 file and
// exporting the standard variable itself. So: upload under the plain name, then
// keep whatever the reply calls it.
export function useUploadSecretFile() {
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async ({
      file,
      environmentName = "GOOGLE_APPLICATION_CREDENTIALS",
    }) => {
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
