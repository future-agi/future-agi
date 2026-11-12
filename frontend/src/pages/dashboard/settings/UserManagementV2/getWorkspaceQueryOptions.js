//
import axios, { endpoints } from "src/utils/axios";

export const workspaceDirectoryKey = ["Workspace-detail"];

export const getWorkspaceQueryKey = (pageNumber, sort, searchQuery) => {
  return [...workspaceDirectoryKey, pageNumber, sort, searchQuery];
};

export const getWorkspaceQueryOptions = (
  { pageNumber, sort, search },
  extra,
) => {
  return {
    queryKey: getWorkspaceQueryKey(pageNumber, sort, search),
    queryFn: () =>
      axios.get(endpoints.workspace.workspaceList, {
        params: {
          page: pageNumber + 1,
          sort: sort,
          search: search,
          limit: 20,
        },
      }),
    staleTime: Infinity,
    ...extra,
  };
};
