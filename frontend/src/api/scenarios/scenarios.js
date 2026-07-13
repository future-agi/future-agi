import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useGetScenarioDetail = (scenarioId, options = {}) => {
  return useQuery({
    queryKey: ["scenario-detail", scenarioId],
    queryFn: () => axios.get(endpoints.scenarios.detail(scenarioId)),
    select: (d) => d.data,
    enabled: !!scenarioId,
    refetchInterval: ({ state }) => {
      return state?.data?.data?.status === "Processing" ? 5000 : false;
    },
    ...options,
  });
};

export const useGetScenarioList = (
  search_text,
  { simulationType, ...options } = {},
) => {
  return useInfiniteQuery({
    ...options,
    queryKey: ["scenarios", search_text, simulationType],
    queryFn: ({ pageParam }) => {
      return axios.get(endpoints.scenarios.list, {
        params: {
          page: pageParam,
          limit: 20,
          ...(search_text && { search: search_text }),
          ...(simulationType && { agent_type: simulationType }),
        },
      });
    },
    getNextPageParam: (lastPage) => {
      return lastPage.data?.next ? lastPage.data.current_page + 1 : undefined;
    },
    initialPageParam: 1,
  });
};
