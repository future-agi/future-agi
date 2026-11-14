import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { ModelHubApiModelsListListResponse } from "src/generated/api-contracts/api.zod";

// The endpoint also serves ordinary model rows. For this explicit query, the
// generated decision fields must all be present before entering the cache.
const decisionGroupSchema =
  ModelHubApiModelsListListResponse.shape.results.element
    .required({
      label: true,
      author: true,
      version: true,
      question_types: true,
      min_choices: true,
      max_choices: true,
      max_state_bytes: true,
      notice: true,
      unavailable_reason: true,
      connections: true,
    })
    .refine(
      (group) => group.type === "decision" && group.connections.length > 0,
    );
const decisionCatalogSchema = ModelHubApiModelsListListResponse.extend({
  results: decisionGroupSchema.array(),
});

export const decisionModelsQueryKey = ["eval-decision-models"];

export default function useDecisionModels(enabled = true) {
  return useQuery({
    queryKey: decisionModelsQueryKey,
    queryFn: async () => {
      const response = await axios.get(endpoints.develop.modelList, {
        params: { model_type: "decision", limit: 100 },
      });
      return decisionCatalogSchema.parse(response.data).results;
    },
    staleTime: 60 * 1000,
    enabled,
  });
}
