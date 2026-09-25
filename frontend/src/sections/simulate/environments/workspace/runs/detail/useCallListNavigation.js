import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";

import {
  mapCallRow,
  runCallsQueryOptions,
  useRunCalls,
} from "src/api/simulate-environments/runCalls";

/**
 * Prev/next for the call drawer, worked out in the browser the way LLM Tracing
 * does it: the neighbours are the rows either side of the open call in the
 * list the trace table is showing, in the list's `results` order (grouping is
 * not applied). The table only holds one page, so at a page edge the adjacent
 * page is fetched through the same query the table uses.
 *
 * `tableQuery` is the exact query the table reads (null when the table isn't
 * mounted), so reading it here is a cache hit that shares the table's live
 * polling. A call opened from anywhere else, or no longer on the table's page,
 * gets no prev/next rather than a guess.
 *
 * @param {{ executionId: string,
 *   openCall: ?{ task: { id: string }, source: "table"|"analytics", page: ?number },
 *   tableQuery: ?{ page: number, limit: number, search: string, filters: Object, groupBy: string },
 *   live?: boolean,
 *   onStep: (next: { task: Object, source: "table", page: number }) => void }} args
 */
export default function useCallListNavigation({
  executionId,
  openCall,
  tableQuery,
  live = false,
  onStep,
}) {
  const queryClient = useQueryClient();
  const [crossing, setCrossing] = useState(false);

  const enabled = openCall?.source === "table" && !!tableQuery;
  const { tasks, totalPages } = useRunCalls(executionId, {
    ...(tableQuery ?? {}),
    enabled,
  });
  const page = tableQuery?.page ?? 1;
  const index = enabled
    ? tasks.findIndex((task) => task.id === openCall.task.id)
    : -1;
  const onFirstRow = index === 0;
  const onLastRow = index >= 0 && index === tasks.length - 1;

  const pageOptions = useCallback(
    (target) =>
      runCallsQueryOptions(executionId, { ...tableQuery, page: target }),
    [executionId, tableQuery],
  );

  // Preload the page a step off the edge would land on.
  useEffect(() => {
    if (onFirstRow && page > 1) {
      queryClient.prefetchQuery(pageOptions(page - 1));
    }
    if (onLastRow && page < totalPages) {
      queryClient.prefetchQuery(pageOptions(page + 1));
    }
  }, [queryClient, pageOptions, onFirstRow, onLastRow, page, totalPages]);

  const cross = async (direction) => {
    const target = page + direction;
    setCrossing(true);
    try {
      const data = await queryClient.fetchQuery({
        ...pageOptions(target),
        // A live run's rows shift, so don't step onto an old copy of the page.
        ...(live ? { staleTime: 0 } : {}),
      });
      const rows = data?.results ?? [];
      const row = direction > 0 ? rows[0] : rows[rows.length - 1];
      if (!row) return;
      onStep({
        task: mapCallRow(row, data.evaluation_columns ?? []),
        source: "table",
        page: target,
      });
    } catch {
      enqueueSnackbar(
        direction > 0
          ? "Couldn't load the next calls"
          : "Couldn't load the previous calls",
        { variant: "error" },
      );
    } finally {
      setCrossing(false);
    }
  };

  const step = (direction) => {
    if (index < 0 || crossing) return undefined;
    const next = index + direction;
    if (next >= 0 && next < tasks.length) {
      onStep({ task: tasks[next], source: "table", page });
      return undefined;
    }
    return cross(direction);
  };

  return {
    hasPrev: !crossing && index >= 0 && (index > 0 || page > 1),
    hasNext:
      !crossing && index >= 0 && (index < tasks.length - 1 || page < totalPages),
    onPrev: () => step(-1),
    onNext: () => step(1),
  };
}
