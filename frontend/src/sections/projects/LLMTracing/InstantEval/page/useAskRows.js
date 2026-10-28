import { useEffect, useRef } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { paramsSerializer } from "src/utils/utils";
import { filterParams } from "./askFilter";
import { askRunKeys, retryServerErrors, toRecord } from "./useAskRun";

// The rows behind the charts, read from the server a page at a time and
// narrowed by whatever the charts select. A live run's first page is read
// again as its answers land (`version`); once more pages are loaded they are
// kept as they are until the run ends, then all are read again in place.

export const ROWS_PAGE = 50;

const result = (response) => response?.data?.result;

// Stay below nginx's 8 KB request-line limit and Django's 1,000 GET fields.
// The same selection is used for the table, Insights hand-overs and exports.
function readSelection(url, params, options = {}) {
  const serializer = paramsSerializer();
  if (
    (params.group?.length || 0) >= 500 ||
    url.length + 1 + serializer.serialize(params).length > 6000
  ) {
    return axios.post(url, params, options);
  }
  return axios.get(url, {
    ...options,
    params,
    paramsSerializer: serializer,
  });
}

export function rowsParams({ projectId, taskId, tz, filter, sort }) {
  return {
    project_id: projectId,
    eval_task_id: taskId,
    tz,
    sort: sort || "-time",
    ...filterParams(filter),
  };
}

export function useAskRows({
  projectId,
  taskId,
  unit,
  tz,
  filter,
  sort,
  version,
  enabled = true,
}) {
  const params = rowsParams({ projectId, taskId, tz, filter, sort });
  const active = Boolean(projectId && taskId) && enabled;
  const query = useInfiniteQuery({
    // `version` is not in the key: a new key started a new infinite query on
    // every 2 s poll, so pages loaded with "load more" collapsed back to the
    // first one and the scroll jumped.
    queryKey: [...askRunKeys.rows(projectId, taskId), params],
    enabled: active,
    initialPageParam: null,
    queryFn: async ({ pageParam, signal }) => {
      const response = await readSelection(
        endpoints.project.instantEvalRows(),
        {
          ...params,
          page_size: ROWS_PAGE,
          ...(pageParam ? { cursor: pageParam } : {}),
        },
        { signal },
      );
      const data = result(response) || {};
      return {
        rows: (data.rows || []).map((row) => toRecord(row, unit)),
        next: data.next_cursor || null,
        total: data.total ?? 0,
      };
    },
    getNextPageParam: (last) => last.next || undefined,
    placeholderData: (previous) => previous,
    // A refused read (a selection the server won't take) is shown at once.
    retry: retryServerErrors,
    meta: { errorHandled: true },
  });
  const pages = query.data?.pages || [];
  const seenVersion = useRef(version);
  const { refetch } = query;
  useEffect(() => {
    // `refetch` ignores `enabled`: a table hidden behind a chart's id
    // selection is not read on every poll. A version it missed is read once
    // it shows again: enabling alone skips rows read under 5 s ago.
    if (!active || seenVersion.current === version) return;
    seenVersion.current = version;
    if (version === "done" || pages.length <= 1) refetch();
  }, [version, pages.length, refetch, active]);
  return {
    rows: pages.flatMap((page) => page.rows),
    total: pages[0]?.total ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    hasMore: Boolean(query.hasNextPage),
    isFetchingMore: query.isFetchingNextPage,
    loadMore: query.fetchNextPage,
    refetch: query.refetch,
  };
}

/** The ids of up to `limit` rows a filter selects, for hand-overs. */
export async function fetchRowIds({
  projectId,
  taskId,
  tz,
  filter,
  limit,
  unit,
}) {
  const out = [];
  let cursor = null;
  do {
    // eslint-disable-next-line no-await-in-loop
    const response = await readSelection(endpoints.project.instantEvalRows(), {
      ...rowsParams({ projectId, taskId, tz, filter }),
      compact: true,
      page_size: Math.min(1500, limit - out.length),
      ...(cursor ? { cursor } : {}),
    });
    const data = result(response) || {};
    out.push(...(data.rows || []).map((row) => toRecord(row, unit)));
    cursor = data.next_cursor;
  } while (cursor && out.length < limit);
  return out.slice(0, limit);
}

/**
 * Download every row a filter selects as CSV: a user question's users, or
 * with `sessions` their sessions, a line each.
 */
export async function downloadRowsCsv({
  projectId,
  taskId,
  tz,
  filter,
  name,
  sessions = false,
}) {
  const response = await readSelection(
    endpoints.project.instantEvalExport(),
    {
      ...rowsParams({ projectId, taskId, tz, filter }),
      ...(sessions ? { sessions: true } : {}),
    },
    { responseType: "blob" },
  );
  const url = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = name || `instant-eval-${taskId}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
