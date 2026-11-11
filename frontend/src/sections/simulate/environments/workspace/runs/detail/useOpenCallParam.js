import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import {
  taskFromCallDetail,
  useRunCalls,
} from "src/api/simulate-environments/runCalls";
import { useCallExecutionV3Detail } from "src/api/simulate-environments/runDetail";

const ROW_PARAM = "rowId";
const NOT_FOUND = new Set([400, 403, 404]);

const detailOptions = {
  // An unknown id won't appear on a retry.
  retry: (count, error) => !NOT_FOUND.has(error?.statusCode) && count < 1,
  // The page reports a failed link itself.
  meta: { errorHandled: true },
};

/**
 * The open call on the run page, mirrored in `?rowId=<callExecutionId>` so a
 * row can be linked to. Opening, stepping and closing write the param (replacing
 * the history entry); a param the page didn't write opens that row:
 *   - from the table's current page when it's there, so prev/next work,
 *   - otherwise from the call's own detail, with no list (and no prev/next),
 *   - or, for an unknown id, a "Row not found" snackbar and the param dropped.
 *
 * @param {{ executionId: string,
 *   tableQuery: ?Object,
 *   tableShown: boolean }} args  `tableQuery` is the query the trace table
 *   reads; `tableShown` says the table is (or is about to be) mounted, so its
 *   page is worth waiting for.
 * @returns {{ openCall: ?{ task: Object, source: string, page: ?number },
 *   showCall: (next: ?{ task: Object, source: string, page: ?number }) => void }}
 */
export default function useOpenCallParam({
  executionId,
  tableQuery,
  tableShown,
}) {
  const [openCall, setOpenCall] = useState(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const rowId = searchParams.get(ROW_PARAM);
  // The id this page last wrote: any other value came from outside (a link).
  const written = useRef(undefined);
  const reported = useRef(null);
  const previousRowId = useRef(rowId);

  const writeRowId = useCallback(
    (id) => {
      written.current = id;
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (id) next.set(ROW_PARAM, id);
          else next.delete(ROW_PARAM);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const showCall = useCallback(
    (next) => {
      setOpenCall(next);
      writeRowId(next?.task?.id ?? null);
    },
    [writeRowId],
  );

  const pending = rowId && rowId !== written.current ? rowId : null;

  const page = useRunCalls(executionId, {
    ...(tableQuery ?? {}),
    enabled: !!pending && !!tableQuery,
  });
  const pageRow =
    pending && tableQuery
      ? page.tasks.find((task) => task.id === pending)
      : undefined;
  const pageSettled = !tableShown || (!!tableQuery && !page.isLoading);

  const detailWanted = !!pending && pageSettled && !pageRow;
  const detail = useCallExecutionV3Detail(pending, detailWanted, detailOptions);

  useEffect(() => {
    if (!pending) {
      // Report the same bad link again if it's followed a second time.
      reported.current = null;
      return;
    }
    if (pageRow) {
      showCall({ task: pageRow, source: "table", page: null });
    } else if (detailWanted && detail.data) {
      showCall({
        task: taskFromCallDetail(detail.data),
        source: "link",
        page: null,
      });
    } else if (detailWanted && detail.error && reported.current !== pending) {
      reported.current = pending;
      enqueueSnackbar(
        NOT_FOUND.has(detail.error.statusCode)
          ? "Row not found"
          : "Couldn't load the row",
        { variant: "error" },
      );
      writeRowId(null);
    }
  }, [
    pending,
    pageRow,
    detailWanted,
    detail.data,
    detail.error,
    showCall,
    writeRowId,
  ]);

  // The param went away without the page closing the call (e.g. navigation).
  useEffect(() => {
    const hadRowId = !!previousRowId.current;
    previousRowId.current = rowId;
    if (hadRowId && !rowId) {
      written.current = null;
      setOpenCall(null);
    }
  }, [rowId]);

  return { openCall, showCall };
}
