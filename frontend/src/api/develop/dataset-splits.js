import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";

// N6 splits (PRODUCT-SPEC §6.2). Split {id, name, color, description,
// row_count, experiment_count}; rows reference them via row_meta.split_ids.

export const datasetSplitsQueryKey = (datasetId) => [
  "dataset-splits",
  datasetId,
];

const EMPTY = Object.freeze({ splits: [], no_split_count: 0 });

/** GET splits: `{splits, no_split_count, byId}`. */
export function useDatasetSplits(datasetId, { enabled = true } = {}) {
  return useQuery({
    queryKey: datasetSplitsQueryKey(datasetId),
    queryFn: () => axios.get(endpoints.develop.splits.list(datasetId)),
    enabled: Boolean(datasetId) && enabled,
    select: (res) => {
      const result = res?.data?.result ?? EMPTY;
      const splits = result.splits ?? [];
      return {
        splits,
        no_split_count: result.no_split_count ?? 0,
        byId: Object.fromEntries(splits.map((s) => [s.id, s])),
      };
    },
    staleTime: 30 * 1000,
  });
}

const errorText = (error, fallback) =>
  error?.result || error?.response?.data?.result || error?.message || fallback;

/** Refresh every reader of current split membership, including export snapshots. */
export function invalidateDatasetSplits(queryClient, datasetId) {
  return Promise.all([
    queryClient.invalidateQueries({
      queryKey: datasetSplitsQueryKey(datasetId),
    }),
    queryClient.invalidateQueries({
      queryKey: ["develop", "row-view", datasetId],
    }),
    queryClient.invalidateQueries({
      queryKey: ["develop", "dataset-splits", datasetId],
    }),
    queryClient.invalidateQueries({
      queryKey: ["dataset-detail", datasetId],
    }),
    queryClient.invalidateQueries({
      queryKey: ["training-set", datasetId],
    }),
  ]);
}

function useInvalidateSplits(datasetId) {
  const queryClient = useQueryClient();
  return () => invalidateDatasetSplits(queryClient, datasetId);
}

/** Apply membership changes atomically to existing rows. */
export function useAssignSplits(datasetId) {
  const invalidate = useInvalidateSplits(datasetId);
  return useMutation({
    mutationFn: (body) =>
      axios
        .post(`${endpoints.develop.splits.list(datasetId)}membership/`, body)
        .then((res) => res.data?.result),
    onSuccess: invalidate,
    onError: (error) =>
      enqueueSnackbar(errorText(error, "Couldn't save split membership."), {
        variant: "error",
      }),
  });
}

/** POST {name, color, description} → the created split. */
export function useCreateSplit(datasetId) {
  const invalidate = useInvalidateSplits(datasetId);
  return useMutation({
    mutationFn: (body) =>
      axios
        .post(endpoints.develop.splits.list(datasetId), body)
        .then((res) => res.data?.result),
    onSuccess: invalidate,
    onError: (error) =>
      enqueueSnackbar(errorText(error, "Couldn't create the split."), {
        variant: "error",
      }),
  });
}

/** PATCH {splitId, name?, color?, description?}: immediate, not versioned. */
export function useUpdateSplit(datasetId) {
  const invalidate = useInvalidateSplits(datasetId);
  return useMutation({
    mutationFn: ({ splitId, ...patch }) =>
      axios
        .patch(endpoints.develop.splits.detail(datasetId, splitId), patch)
        .then((res) => res.data?.result),
    onSuccess: invalidate,
    onError: (error) =>
      enqueueSnackbar(errorText(error, "Couldn't update the split."), {
        variant: "error",
      }),
  });
}

/** Delete a split and its live memberships without changing rows or versions. */
export function useDeleteSplit(datasetId) {
  const invalidate = useInvalidateSplits(datasetId);
  return useMutation({
    mutationFn: (splitId) =>
      axios
        .delete(endpoints.develop.splits.detail(datasetId, splitId))
        .then((res) => res.data?.result),
    onSuccess: invalidate,
    onError: (error) =>
      enqueueSnackbar(errorText(error, "Couldn't delete the split."), {
        variant: "error",
      }),
  });
}
