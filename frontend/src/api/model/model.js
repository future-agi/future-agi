import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import axios, { endpoints } from "src/utils/axios";

export const useGetAllModelList = (options) => {
  const _qkey = options?.queryKey ?? [];
  const _qParams = options?.params ?? {};
  const _queryParams = useInfiniteQuery({
    ...options,
    queryKey: ["model-list", ..._qkey],
    getNextPageParam: (o) => (o.data.next ? o.data.current_page + 1 : null),
    queryFn: ({ pageParam }) =>
      axios.get(`${endpoints.model.modelList}`, {
        params: {
          page: pageParam ? pageParam : 1,
          ..._qParams,
        },
      }),
    initialPageParam: 1,
    staleTime: 30 * 60 * 1000, // 30 min stale time
  });

  const mergedData = useMemo(
    () =>
      _queryParams.data?.pages.reduce(
        (acc, curr) => [...acc, ...curr.data.results],
        [],
      ) || [],
    [_queryParams.data],
  );

  return { mergedData, ..._queryParams };
};

export const modelCatalogQuery = (search) => ({
  queryKey: ["model-catalog", search ?? ""],
  queryFn: () =>
    axios
      .get(endpoints.develop.modelList, { params: { search } })
      .then((res) => res.data),
  staleTime: 30 * 60 * 1000,
});

export const useGetModelDetail = (id, options) => {
  return useQuery({
    ...options,
    queryKey: ["model", id],
    queryFn: () => axios.get(endpoints.model.details(id)),
    select: (d) => d.data,
    staleTime: 1 * 60 * 1000, // 1 min stale time
  });
};
