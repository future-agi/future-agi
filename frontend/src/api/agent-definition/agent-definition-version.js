import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useAgentDefinitionVersions = ({
  selectedAgentId,
  refetchInterval,
}) => {
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
    refetchInterval,
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
export const useCreateAgentDefinition = () => {
  return useMutation({
    mutationFn: (payload) =>
      axios.post(endpoints.agentDefinitions.create, payload),
  });
};
