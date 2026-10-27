import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { normalizePersonas } from "src/sections/persona/personaApiShape";

export const useGetPersonas = (search, type) => {
  const queryData = useInfiniteQuery({
    queryKey: ["personas", type, search],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.persona.list, {
        params: {
          search: search,
          page: pageParam,
          limit: 20,
          type,
        },
      }),
    getNextPageParam: (lastPage) => {
      return lastPage.data?.result?.next
        ? lastPage?.data?.result?.current_page + 1
        : undefined;
    },
    initialPageParam: 1,
    staleTime: Infinity,
  });

  return {
    ...queryData,
    personas: queryData.data?.pages.reduce((acc, curr) => {
      if (curr.data?.result?.results) {
        acc.push(...normalizePersonas(curr.data?.result?.results));
      }
      return acc;
    }, []),
    totalCount: queryData.data?.pages[0]?.data?.result?.count ?? 0,
  };
};

export const useGetPersonasPaginated = ({
  page = 1,
  pageSize = 25,
  search = null,
  type = null,
  simulationType = null,
  enabled = true,
} = {}) => {
  return useQuery({
    queryKey: [
      "personas",
      "paginated",
      page,
      pageSize,
      search,
      type,
      simulationType,
    ],
    queryFn: async () => {
      const { data } = await axios.get(endpoints.persona.list, {
        params: {
          page,
          limit: pageSize,
          search: search || undefined,
          type: type || undefined,
          simulation_type: simulationType || undefined,
        },
      });
      const result = data?.result || {};
      return {
        ...result,
        results: normalizePersonas(result.results),
      };
    },
    enabled,
    keepPreviousData: true,
  });
};

export const useBulkDeletePersonas = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (personaIds) => {
      const results = await Promise.allSettled(
        personaIds.map((id) =>
          axios.delete(endpoints.persona.delete(id)).then(() => ({
            id,
            ok: true,
          })),
        ),
      );
      const failed = results
        .map((r, i) => ({ r, id: personaIds[i] }))
        .filter(({ r }) => r.status === "rejected");
      return { deleted: personaIds.length - failed.length, failed };
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personas"] });
    },
  });
};
