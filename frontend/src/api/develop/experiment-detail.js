import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";

export const useExperimentList = (search_text, options) => {
  const [searchparams] = useSearchParams();
  const datasetId = searchparams.get("datasetId");
  return useQuery({
    ...options,
    queryKey: ["develop", "experiment-list", datasetId, search_text],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.experimentList, {
        params: {
          dataset_id: datasetId,
          ...(search_text && { search: search_text }),
        },
      }),
    select: (d) => d.data?.results ?? [],
    staleTime: 1 * 60 * 1000,
    enabled: !!datasetId,
  });
};

export const useInfiniteExperimentList = (search_text, options) => {
  const [searchparams] = useSearchParams();
  const datasetId = searchparams.get("datasetId");

  return useInfiniteQuery({
    ...options,
    queryKey: ["develop", "experiment-list", datasetId, search_text],
    queryFn: ({ pageParam = 1 }) =>
      axios.get(endpoints.develop.experiment.experimentList, {
        params: {
          dataset_id: datasetId,
          page: pageParam,
          ...(search_text && { search: search_text }),
        },
      }),
    getNextPageParam: (lastPage) => {
      const data = lastPage.data;
      // Return next page number if there's a next page, otherwise undefined
      return data.next ? data.current_page + 1 : undefined;
    },
    getPreviousPageParam: (firstPage) => {
      const data = firstPage.data;
      // Return previous page number if there's a previous page, otherwise undefined
      return data.previous ? data.current_page - 1 : undefined;
    },
    select: (data) => ({
      pages: data.pages,
      pageParams: data.pageParams,
      // Flatten all results from all pages into a single array
      allResults: data.pages.flatMap((page) => page.data?.results ?? []),
    }),
    staleTime: 1 * 60 * 1000,
    enabled: !!datasetId,
    initialPageParam: 1,
  });
};

export const useExperimentsOnDatasetList = (
  datasetId,
  searchText,
  pageNumber,
  pageSize,
) => {
  return useQuery({
    queryKey: [
      "develop",
      "experiments-on-dataset",
      datasetId,
      searchText,
      pageNumber,
      pageSize,
    ],
    queryFn: () =>
      axios.get(endpoints.develop.experiment.experimentListPaginated, {
        params: {
          dataset_id: datasetId,
          ...(searchText && { search: searchText }),
          page: pageNumber + 1,
          limit: pageSize,
        },
      }),
    select: (d) => d.data?.results ?? [],
    staleTime: 1 * 60 * 1000,
  });
};
