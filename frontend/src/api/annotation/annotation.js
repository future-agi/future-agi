import useSWR from "swr";
import { useMemo } from "react";

import { fetcher, endpoints, createQueryString } from "src/utils/axios";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios from "src/utils/axios";

export function useAnnotationTasks(predictiveJourney, page = 1) {
  const params = {
    page,
    predictive_journey: predictiveJourney,
  };

  const URL = endpoints.annotation.list;
  const queryString = createQueryString(params);
  const URLWithParams = queryString ? `${URL}?${queryString}` : URL;

  const { data, isLoading, error, isValidating } = useSWR(
    URLWithParams,
    fetcher,
  );
  const memoizedValue = useMemo(
    () => ({
      tasks: data?.results || [],
      tasksLoading: isLoading,
      tasksError: error,
      tasksValidating: isValidating,
      tasksEmpty: !isLoading && !data?.results.length,
      next: data?.next,
      previous: data?.previous,
      count: data?.count,
      totalPages: data?.total_pages,
      currentPage: data?.current_page,
    }),
    [data, error, isLoading, isValidating],
  );

  return memoizedValue;
}

export const useAnnotationLabels = (projectId) => {
  const _queryParams = useInfiniteQuery({
    queryKey: ["project-annotations-labels-paginated", projectId],
    queryFn: ({ pageParam }) =>
      axios.get(endpoints.project.getAnnotationLabels(), {
        params: { project_id: projectId, page: pageParam },
      }),
    getNextPageParam: (o) => {
      const nextPage = o.data.next ? o.data.current_page + 1 : null;
      return nextPage;
    },
    initialPageParam: 1,
  });

  const labels = useMemo(
    () =>
      _queryParams.data?.pages.reduce(
        (acc, curr) => [...acc, ...curr.data.results],
        [],
      ) || [],
    [_queryParams.data],
  );

  return { labels, ..._queryParams };
};
