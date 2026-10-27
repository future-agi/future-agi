//
import axios, { endpoints } from "src/utils/axios";
import { paramsSerializer } from "src/utils/utils";

export const DEFAULT_MEMBER_LIST_SORT = "-created_at";

export const ensureCanonicalMemberListSort = (sort) => {
  if (sort === undefined || sort === null || sort === "") {
    return DEFAULT_MEMBER_LIST_SORT;
  }

  if (typeof sort !== "string") {
    throw new TypeError(
      "getUserQueryOptions expects the backend sort query string, not UI grid state.",
    );
  }

  return sort.trim() || DEFAULT_MEMBER_LIST_SORT;
};

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
  const sortParam = ensureCanonicalMemberListSort(sort);
  return {
    queryKey: getUserQueryKey(
      pageNumber,
      sortParam,
      search,
      filterStatus,
      filterRole,
      workspaceId,
    ),
    queryFn: () =>
      axios.get(url, {
        params: {
          page: pageNumber + 1,
          sort: sortParam,
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
