import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { myEnvironmentsListKey } from "./environments";
import { harnessEnvironmentKey } from "./environment";

const reviewPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/design-review/", {
    id: envId,
  });
const editsPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/design-review/edits/", {
    id: envId,
  });
const editPath = (envId, index) =>
  apiPath(
    "/simulate/api/harness-environments/{id}/design-review/edits/{index}/",
    { id: envId, index },
  );
const impactPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/design-review/impact/", {
    id: envId,
  });
const approvePath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/design-review/approve/", {
    id: envId,
  });

export const designReviewKey = (envId) => ["design-review", envId];

export const getDesignReview = async (envId) =>
  (await axios.get(reviewPath(envId))).data;

export const getDesignImpact = async (envId, kind, id) =>
  (await axios.get(impactPath(envId), { params: { kind, id } })).data;

export function useDesignReview(envId, { enabled = true } = {}) {
  return useQuery({
    queryKey: designReviewKey(envId),
    queryFn: () => getDesignReview(envId),
    enabled: Boolean(envId) && enabled,
  });
}

function useReviewMutation(envId, mutationFn) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn,
    onSuccess: (review) => {
      queryClient.setQueryData(designReviewKey(envId), review);
    },
  });
}

export const useAddDesignEdit = (envId) =>
  useReviewMutation(
    envId,
    async (edit) => (await axios.post(editsPath(envId), edit)).data,
  );

export const useUndoDesignEdit = (envId) =>
  useReviewMutation(
    envId,
    async (index) => (await axios.delete(editPath(envId, index))).data,
  );

export function useApproveDesign(envId) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async () => (await axios.post(approvePath(envId), {})).data,
    onSuccess: (review) => {
      queryClient.setQueryData(designReviewKey(envId), review);
      queryClient.invalidateQueries({ queryKey: ["harness-job", envId] });
      queryClient.invalidateQueries({ queryKey: harnessEnvironmentKey(envId) });
      queryClient.invalidateQueries({ queryKey: myEnvironmentsListKey() });
    },
  });
}
