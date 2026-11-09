// Round 3 hooks (R3 G0 §B10; wire shapes in src/_mock/prototype/CONTRACT.md
// "Round 3" and fixtures/world/r3Shapes.js). Frozen API: useTaskCompare,
// useTaskReview, useTaskReviewMutation, useSavedViews and the query keys.
import { useContext, useMemo } from "react";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { serializeTrials } from "src/sections/common/Analysis/analysisLinks";

const csv = (ids) => (ids ?? []).filter(Boolean).join(",");

// ------------------------------------------------------------------ keys

export const taskCompareKey = (datasetId, params) => [
  "r3",
  "task-compare",
  datasetId,
  params ?? {},
];
export const taskReviewKey = (datasetId, params) => [
  "r3",
  "task-review",
  datasetId,
  params ?? {},
];
export const savedViewsKey = (surface, scopeId) => [
  "r3",
  "saved-views",
  surface,
  scopeId ?? null,
];

// ---------------------------------------------------------- task compare

/**
 * GET the side-by-side payload for one task (TaskCompareResponse).
 * @param {{datasetId: string, rowId: string, runIds: string[],
 *   trials?: Object<string, number>}} args
 * @param {object} [opts]  react-query options (`enabled`, …)
 */
export function useTaskCompare(
  { datasetId, rowId, runIds, trials },
  opts = {},
) {
  const runs = csv(runIds);
  const trial = serializeTrials(trials ?? {});
  const params = { row: rowId, runs, ...(trial ? { trials: trial } : {}) };
  const { enabled = true, ...rest } = opts;
  return useQuery({
    queryKey: taskCompareKey(datasetId, params),
    queryFn: () =>
      axios.get(endpoints.develop.r3.taskCompare(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId && rowId && runs) && enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    ...rest,
  });
}

/** GET the per-user review state of one task (TaskReviewResponse). */
export function useTaskReview({ datasetId, rowId, runIds }, opts = {}) {
  const params = { row: rowId, runs: csv(runIds) };
  const { enabled = true, ...rest } = opts;
  return useQuery({
    queryKey: taskReviewKey(datasetId, params),
    queryFn: () =>
      axios.get(endpoints.develop.r3.taskReview(datasetId), { params }),
    select: (res) => res.data?.result,
    enabled: Boolean(datasetId && rowId && params.runs) && enabled,
    ...rest,
  });
}

/**
 * POST a review write: mutate({datasetId, row_id, run_ids, viewed?,
 * flagged?, verdict?, note?}) → TaskReviewResponse. Updates the cache.
 */
export function useTaskReviewMutation(options = {}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ datasetId, ...body }) =>
      axios
        .post(endpoints.develop.r3.taskReview(datasetId), body)
        .then((res) => res.data?.result),
    ...options,
    onSuccess: (result, vars, ctx) => {
      const params = { row: vars.row_id, runs: csv(vars.run_ids) };
      queryClient.setQueryData(taskReviewKey(vars.datasetId, params), {
        data: { status: true, result },
      });
      queryClient.invalidateQueries({
        queryKey: ["r3", "task-review", vars.datasetId],
      });
      options.onSuccess?.(result, vars, ctx);
    },
  });
}

// ------------------------------------------------------------ saved views

/**
 * Saved views of a surface in a scope (usually a dataset): the user's own
 * plus every view shared with the workspace (W9 A0: each carries `shared`,
 * `mine` and `can_delete`).
 * @returns {{views: SavedView[], isLoading: boolean,
 *   create: (name: string, state: object, opts?: {shared?: boolean}) => Promise<SavedView>,
 *   remove: (id: string) => Promise,
 *   restore: (id: string) => Promise<SavedView>, isSaving: boolean}}
 *   `restore` (W9) brings a deleted view back as it was (same id, author
 *   and created_at): the delete toast's Undo.
 */
export function useSavedViews(surface, scopeId) {
  const queryClient = useQueryClient();
  const auth = useContext(AuthContext);
  const { currentOrganizationId } = useOrganization();
  const { currentWorkspaceId } = useWorkspace();
  const key = [
    ...savedViewsKey(surface, scopeId),
    auth?.user?.id ?? null,
    currentOrganizationId,
    currentWorkspaceId,
  ];
  const list = useQuery({
    queryKey: key,
    queryFn: async () => {
      const response = await axios.get(endpoints.develop.r3.savedViews, {
        params: { surface, scope: scopeId ?? "" },
      });
      if (surface !== "grid") return response;
      const results = [...response.data.result.results];
      const seen = new Set();
      let after = response.data.result.next;
      while (after) {
        if (seen.has(after))
          throw new Error("Saved view pagination did not advance.");
        seen.add(after);
        const page = await axios.get(endpoints.develop.r3.savedViews, {
          params: { surface, scope: scopeId, after },
        });
        results.push(...page.data.result.results);
        after = page.data.result.next;
      }
      return { ...response, data: { ...response.data, result: { results } } };
    },
    select: (res) => res.data?.result?.results ?? [],
    enabled: Boolean(surface && (surface !== "grid" || scopeId)),
    retry: false,
    staleTime: 30_000,
  });
  const create = useMutation({
    mutationFn: ({ name, state, shared }) =>
      axios
        .post(endpoints.develop.r3.savedViews, {
          surface,
          scope: scopeId ?? "",
          name,
          state,
          ...(shared ? { shared: true } : {}),
        })
        .then((res) => res.data?.result),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
  const remove = useMutation({
    mutationFn: (id) =>
      axios
        .delete(endpoints.develop.r3.savedView(id))
        .then((res) => res.data?.result),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
  const restore = useMutation({
    mutationFn: (id) =>
      axios
        .post(endpoints.develop.r3.savedViews, {
          restore_id: id,
          ...(surface === "grid" ? { surface, scope: scopeId } : {}),
        })
        .then((res) => res.data?.result),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: key }),
  });
  const views = useMemo(() => list.data ?? [], [list.data]);
  return {
    views,
    isLoading: list.isLoading,
    loadError: list.error,
    reload: list.refetch,
    create: (name, state, { shared } = {}) =>
      create.mutateAsync({ name, state, shared }),
    remove: (id) => remove.mutateAsync(id),
    restore: (id) => restore.mutateAsync(id),
    isSaving: create.isPending,
  };
}
