//
import axios, { endpoints } from "src/utils/axios";
import { paramsSerializer } from "src/utils/utils";

export const getUserQueryKey = (
  pageNumber,
  sort,
  searchQuery,
  filterStatus,
  filterRole,
  workspaceId,
) => {
  return [
    "user-detail",
    pageNumber,
    sort,
    searchQuery,
    filterStatus,
    filterRole,
    workspaceId,
  ];
};

export const getUserQueryOptions = (
  { pageNumber, sort, search, filterStatus, filterRole, workspaceId, endpoint },
  extra,
) => {
  const url = endpoint || endpoints.rbac.memberList;
  return {
    queryKey: getUserQueryKey(
      pageNumber,
      sort,
      search,
      filterStatus,
      filterRole,
      workspaceId,
    ),
    queryFn: () =>
      axios.get(url, {
        params: {
          page: pageNumber + 1,
          sort: sort,
          search: search,
          limit: 20,
          filter_status: filterStatus || [],
          filter_role: filterRole || [],
        },
        paramsSerializer: paramsSerializer(),
        headers: workspaceId ? { "X-Workspace-Id": workspaceId } : {}, // Consistent casing: X-Workspace-Id
      }),
    staleTime: Infinity,
    ...extra,
  };
};
