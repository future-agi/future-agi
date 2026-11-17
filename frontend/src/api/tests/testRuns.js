import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import axios from "src/utils/axios";
import { endpoints } from "src/utils/axios";
import { useMemo } from "react";

export const useTestRunsList = ({
  searchText,
  enabled = true,
  limit = 10,
} = {}) => {
  const { data, fetchNextPage, isFetchingNextPage, isFetching } =
    useInfiniteQuery({
      queryKey: ["test-runs-list", searchText],
      queryFn: ({ pageParam = 1 }) =>
        axios.get(endpoints.runTests.list, {
          params: {
            page: pageParam,
            limit,
            search: searchText,
            summary: true,
          },
        }),
      getNextPageParam: ({ data: d }) => (d?.next ? d?.current_page + 1 : null),
      initialPageParam: 1,
      staleTime: 1000 * 60 * 5,
      enabled,
    });

  const testsList = useMemo(
    () => data?.pages.flatMap((page) => page.data?.results ?? []) ?? [],
    [data],
  );

  return { testsList, fetchNextPage, isFetchingNextPage, isFetching };
};

export const useUpdateTestRuns = (testId, extra) => {
  return useMutation({
    ...extra,
    mutationFn: (data) =>
      axios.patch(endpoints.runTests.updateTestRun(testId), data),
    mutationKey: ["update-test-runs", testId],
  });
};
