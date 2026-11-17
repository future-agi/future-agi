import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";

export function getAgentDefinitionCreationErrorMessage(error) {
  const fallback =
    "Agent definition creation could not be confirmed. Check the agent definitions before trying again.";
  const statusCode = Number(
    error?.response?.status || error?.statusCode || error?.status,
  );
  // A failed response can follow a committed creation. Never echo a server
  // failure, even if its body carries a normally user-facing error code.
  if (!(statusCode >= 400 && statusCode < 500)) return fallback;
  return getSafeActionErrorMessage(error, fallback);
}

export const useAgentDefinitionVersions = ({ selectedAgentId }) => {
  return useInfiniteQuery({
    queryKey: ["agent-definition-versions", selectedAgentId],
    queryFn: async ({ pageParam = 1 }) => {
      const res = await axios.get(
        endpoints.agentDefinitions.versions(selectedAgentId),
        {
          params: {
            page: pageParam,
            limit: 10,
          },
        },
      );
      return res.data;
    },
    getNextPageParam: (lastPage) => {
      return lastPage.next ? lastPage.current_page + 1 : undefined;
    },
    initialPageParam: 1,
    enabled: !!selectedAgentId,
  });
};

// create agent definition version
export const useCreateAgentDefinitionVersion = ({ agentDefinitionId }) => {
  return useMutation({
    mutationFn: (payload) =>
      axios.post(
        endpoints.agentDefinitions.createVersion(agentDefinitionId),
        payload,
      ),
  });
};

// create agent definition
export const useCreateAgentDefinition = ({ onError } = {}) => {
  return useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.agentDefinitions.create, payload),
    // Creation may have committed before its response failed.
    retry: false,
    meta: { errorHandled: Boolean(onError) },
    onError,
  });
};
