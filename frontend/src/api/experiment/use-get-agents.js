import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

export const useGetListOfAgents = (
  enabled = true,
  search = "",
  pinnedIds = [],
  isTemplate = false,
) => {
  return useInfiniteQuery({
    queryKey: ["agents", search],
    enabled: enabled,
    queryFn: async ({ pageParam = 1 }) => {
      const params = {
        page_number: pageParam,
        page_size: 20,
        search,
        ...(pinnedIds?.length > 0 && { pinnedIds }),
        ...(isTemplate && { isTemplate }),
      };
      const response = await axios.get(endpoints.agentPlayground.listGraphs, {
        params,
      });

      return response.data?.result || response.data;
    },
    getNextPageParam: (lastPage) => {
      const metadata = lastPage?.metadata;
      const pageNumber = metadata?.page_number;
      const totalPages = metadata?.total_pages;
      if (pageNumber && totalPages && pageNumber < totalPages) {
        return pageNumber + 1;
      }
      return null;
    },
    initialPageParam: 1,
  });
};
export const useGetAgentVersions = (id, search = null) => {
  return useInfiniteQuery({
    queryKey: ["agents-version", id, search],
    enabled: !!id,
    queryFn: async ({ pageParam = 1 }) => {
      const result = await axios.get(
        endpoints.agentPlayground.graphVersions(id),
        {
          params: {
            page_number: pageParam,
            ...(search && { search }),
          },
        },
      );
      return result.data;
    },
    getNextPageParam: (lastPage) => lastPage?.result?.metadata?.next_page,
    initialPageParam: 1,
  });
};
