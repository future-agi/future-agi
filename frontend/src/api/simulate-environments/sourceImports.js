import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";
import { draftsKey } from "./versions";

const sourcesPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/sources/", { id: envId });
const capabilitiesPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/sources/capabilities/", {
    id: envId,
  });
const preparePath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/sources/imports/prepare/", {
    id: envId,
  });
const uploadPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/sources/uploads/prepare/", {
    id: envId,
  });
const acceptPath = (envId) =>
  apiPath("/simulate/api/harness-environments/{id}/sources/imports/accept/", {
    id: envId,
  });

export const sourceSummaryKey = (envId) => ["harness-env-sources", envId];
export const sourceCapabilitiesKey = (envId) => [
  ...sourceSummaryKey(envId),
  "capabilities",
];

export const useEnvironmentSources = (envId) =>
  useQuery({
    queryKey: sourceSummaryKey(envId),
    queryFn: async () => (await axios.get(sourcesPath(envId))).data,
    enabled: Boolean(envId),
  });

export const useSourceCapabilities = (envId) =>
  useQuery({
    queryKey: sourceCapabilitiesKey(envId),
    queryFn: async () => (await axios.get(capabilitiesPath(envId))).data,
    enabled: Boolean(envId),
  });

export const sourceReviewKey = (reviewId) => [
  "harness-source-review",
  reviewId,
];

export function useActiveEnvironmentSourceReview(envId) {
  const queryClient = useQueryClient();
  const storageKey = `harness-environment-source-review:${envId}`;
  const queryKey = ["active-environment-source-review", envId];
  const { data: reviewId } = useQuery({
    queryKey,
    queryFn: () => (envId ? sessionStorage.getItem(storageKey) : null),
    enabled: false,
    initialData: () => (envId ? sessionStorage.getItem(storageKey) : null),
  });
  const setReviewId = (id) => {
    if (id) sessionStorage.setItem(storageKey, id);
    else sessionStorage.removeItem(storageKey);
    queryClient.setQueryData(queryKey, id);
  };
  return { reviewId, setReviewId };
}

export const useEnvironmentSourceReview = (reviewId) =>
  useQuery({
    queryKey: sourceReviewKey(reviewId),
    queryFn: async () =>
      (
        await axios.get(
          apiPath("/simulate/api/harness-source-reviews/{id}/", {
            id: reviewId,
          }),
        )
      ).data,
    enabled: Boolean(reviewId),
    refetchOnMount: "always",
    refetchInterval: (query) =>
      query.state.data?.state === "reading" ? 1000 : false,
  });

export const sourceGenerationPending = (generation) =>
  ["queued", "running"].includes(generation?.state);

export const useEnvironmentSourceGeneration = (reviewId, initial) =>
  useQuery({
    queryKey: [...sourceReviewKey(reviewId), "generation"],
    queryFn: async () =>
      (
        await axios.get(
          apiPath(
            "/simulate/api/harness-source-reviews/{id}/generate-scenarios/",
            {
              id: reviewId,
            },
          ),
        )
      ).data,
    enabled: Boolean(reviewId && initial),
    initialData: initial,
    refetchInterval: (query) =>
      sourceGenerationPending(query.state.data) ? 1000 : false,
  });

function useSourceReviewMutation(path, afterSuccess) {
  const queryClient = useQueryClient();
  return useMutation({
    meta: { errorHandled: true },
    mutationFn: async ({ reviewId, ...body }) =>
      (await axios.post(path(reviewId), body)).data,
    onSuccess: async (review) => {
      const queryKey = sourceReviewKey(review.id);
      await queryClient.cancelQueries({ queryKey });
      queryClient.setQueryData(queryKey, review);
      afterSuccess?.(review);
    },
    onError: (error, { reviewId }) => {
      if (reviewId && error?.statusCode === 409) {
        queryClient.invalidateQueries({ queryKey: sourceReviewKey(reviewId) });
      }
    },
  });
}

export const useCreateSourceReview = () =>
  useSourceReviewMutation(() =>
    apiPath("/simulate/api/harness-source-reviews/"),
  );

export const useReprepareEnvironmentSourceReview = () =>
  useSourceReviewMutation((id) =>
    apiPath("/simulate/api/harness-source-reviews/{id}/reprepare/", { id }),
  );

export const useAcceptSourceReview = (afterSuccess) =>
  useSourceReviewMutation(
    (id) =>
      apiPath("/simulate/api/harness-source-reviews/{id}/accept/", { id }),
    afterSuccess,
  );

export const useCancelSourceReview = (afterSuccess) =>
  useSourceReviewMutation(
    (id) =>
      apiPath("/simulate/api/harness-source-reviews/{id}/cancel/", { id }),
    afterSuccess,
  );

export const usePrepareKbSource = (envId) =>
  useMutation({
    mutationFn: async (body) =>
      (await axios.post(preparePath(envId), body)).data,
  });

export const usePrepareUploadSource = (envId) =>
  useMutation({
    mutationFn: async ({ files, purpose, versionId, draft }) => {
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      body.append("expected_environment_version_id", versionId);
      body.append("expected_draft_revision", String(draft?.revision ?? 0));
      if (draft?.id) body.append("expected_draft_id", draft.id);
      if (purpose) body.append("purpose", purpose);
      return (await axios.post(uploadPath(envId), body)).data;
    },
  });

export const useAcceptSource = (envId) => {
  const queryClient = useQueryClient();
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: sourceSummaryKey(envId) });
    queryClient.invalidateQueries({ queryKey: draftsKey(envId) });
  };
  return useMutation({
    mutationFn: async (body) =>
      (await axios.post(acceptPath(envId), body)).data,
    onSuccess: refresh,
    onError: (error) => {
      if (error?.statusCode === 409) refresh();
    },
  });
};
