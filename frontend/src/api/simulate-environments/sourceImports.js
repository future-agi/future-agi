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

export const useEnvironmentSourceReview = (reviewId) =>
  useQuery({
    queryKey: ["harness-source-review", reviewId],
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
  });

export const useReprepareEnvironmentSourceReview = () =>
  useMutation({
    mutationFn: async ({ reviewId, ...body }) =>
      (
        await axios.post(
          apiPath("/simulate/api/harness-source-reviews/{id}/reprepare/", {
            id: reviewId,
          }),
          body,
        )
      ).data,
  });

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
