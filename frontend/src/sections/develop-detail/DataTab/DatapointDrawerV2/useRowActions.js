import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import axios, { endpoints } from "src/utils/axios";
import { copyToClipboard } from "src/utils/utils";
import { useDevelopDetailContext } from "src/sections/develop-detail/Context/DevelopDetailContext";
import { useRunExperimentStore } from "src/sections/develop-detail/states";
import { rowViewKeys } from "src/api/develop/row-view";
import { notify } from "src/sections/common/ux/toast";
import { useChangeSetStore } from "../Versions/changeSetStore";
import { usePendingChangesGate } from "../Versions/PendingChangesGate";
import { writeExperimentRows } from "../Versions/experimentRowsUrl";
import { cellOf, isUserColumn, rowAsJson, rowNumberOf } from "./rowModel";
import { gridWritesClassic } from "src/sections/develop/realBackendCaps";
import {
  deleteRowsClassic,
  duplicateRowsClassic,
  refusalSentence,
} from "../Versions/classicWrites";

/** How often the Row view re-reads a re-running cell, and for how long. */
export const RERUN_POLL_MS = 700;
export const RERUN_POLL_MAX = 40;

/**
 * Is any cached copy of the row still computing these columns? (pure; the
 * Row view keeps polling while it is.)
 */
export function cellsStillRunning(queriesData, rowId, columnIds) {
  const cached = (queriesData ?? [])
    .map(([, data]) => data?.current)
    .filter((cur) => cur && (cur.row_id ?? cur.id ?? rowId) === rowId);
  if (!cached.length) return true;
  return cached.some((cur) =>
    columnIds.some((id) => cellOf(cur, id)?.status === "running"),
  );
}

const downloadJson = (name, data) => {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

/**
 * The Row view's actions (header ⋯ menu, C, Re-run this cell): every write
 * goes to the change set (pending until Save version) except re-runs of
 * generated cells, which never version (C28).
 */
export default function useRowActions({
  datasetId,
  rowId,
  row,
  columns,
  changeSet,
}) {
  const queryClient = useQueryClient();
  const { gridApi, refreshGrid } = useDevelopDetailContext();
  const [rerunningId, setRerunningId] = useState(null);
  const n = rowNumberOf(row);
  const label = n ? `Row ${n}` : "This row";

  const copyLink = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("row", rowId);
    copyToClipboard(url.toString());
    // RV#14: link copies keep a toast, with the link in it.
    notify(`Copied link to ${label} · ${url.pathname}${url.search}`, {
      key: "row-link-copy",
      variant: "success",
    });
  }, [rowId, label]);

  const download = useCallback(() => {
    downloadJson(`row-${n ?? rowId}.json`, rowAsJson(columns, row));
    enqueueSnackbar(`Downloaded ${label} as JSON`, { variant: "success" });
  }, [columns, row, n, rowId, label]);

  const copyRowJson = useCallback(() => {
    copyToClipboard(JSON.stringify(rowAsJson(columns, row), null, 2));
    enqueueSnackbar(`Copied ${label} as JSON`, { variant: "success" });
  }, [columns, row, label]);

  // L1 (the capability contract): on the real backend the row is copied and
  // deleted at once (duplicate-rows, delete_row); nothing is pending.
  const classic = gridWritesClassic();
  const afterWrite = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: rowViewKeys.all(datasetId) });
    refreshGrid?.();
  }, [queryClient, datasetId, refreshGrid]);

  const duplicate = useCallback(() => {
    if (classic) {
      duplicateRowsClassic({ datasetId, rowIds: [rowId] }).then(
        () => {
          afterWrite();
          enqueueSnackbar(`Added a copy of ${label}`, { variant: "success" });
        },
        (e) =>
          enqueueSnackbar(refusalSentence(e, `Couldn't copy ${label}.`), {
            variant: "error",
          }),
      );
      return;
    }
    const cells = {};
    (columns ?? []).filter(isUserColumn).forEach((c) => {
      const v = row?.[c.id]?.cell_value;
      if (v != null && v !== "") cells[c.id] = v;
    });
    changeSet.addOp({
      op: "add_row",
      cells,
      split_ids: row?.row_meta?.split_ids ?? [],
      metadata: { ...(row?.row_meta?.metadata ?? {}), duplicated_from: rowId },
    });
    enqueueSnackbar(
      `Duplicate of ${label} added · pending until you save a version`,
      {
        variant: "success",
      },
    );
  }, [columns, row, rowId, label, changeSet, classic, datasetId, afterWrite]);

  // RV#42: the caller confirms first; Undo (10 s) takes back only the
  // delete, never the row's other pending edits.
  const removePending = () => {
    changeSet.addOp({ op: "delete_rows", row_ids: [rowId] });
    notify(`${label} deleted · saved with the next version`, {
      key: "row-delete",
      undo: {
        ms: 10000,
        onUndo: () => {
          const ops = useChangeSetStore.getState().sets?.[datasetId]?.ops ?? [];
          const op = ops.find(
            (o) =>
              o.op === "delete_rows" &&
              o.row_ids?.length === 1 &&
              o.row_ids[0] === rowId,
          );
          changeSet.revert(op ? op.id : rowId);
        },
      },
    });
  };
  const remove = useCallback(
    ({ onDeleted } = {}) => {
      if (classic) {
        return deleteRowsClassic({ datasetId, rowIds: [rowId] }).then(
          () => {
            afterWrite();
            onDeleted?.();
            notify(`${label} deleted`, {
              key: "row-delete",
              variant: "success",
            });
          },
          (e) =>
            notify(refusalSentence(e, `Couldn't delete ${label}.`), {
              key: "row-delete",
              variant: "error",
            }),
        );
      }
      return removePending();
    },
    // removePending reads changeSet, rowId, label and datasetId.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [classic, datasetId, rowId, label, afterWrite, changeSet],
  );

  const selectInGrid = useCallback(() => {
    const node = gridApi?.current?.getRowNode?.(rowId);
    if (!node) {
      enqueueSnackbar(
        "Scroll the grid to this row, then select it to copy it",
        {
          variant: "info",
        },
      );
      return;
    }
    node.setSelected(true);
    enqueueSnackbar(
      `${label} selected · use Add to dataset in the selection bar to copy it to another dataset`,
      {
        variant: "info",
      },
    );
  }, [gridApi, rowId, label]);

  // v2.1 g8 (door:run-on-rows:row-drawer-new-experiment): unsaved edits
  // go through the pending-changes gate first (experiments run on a saved
  // version), and `rows=` keeps the one row across a reload.
  const gate = usePendingChangesGate(datasetId);
  const newExperiment = useCallback(() => {
    gate("experiment", () => {
      writeExperimentRows([rowId]);
      useRunExperimentStore
        .getState()
        .initiateCreateMode({ prefill: { row_ids: [rowId] } });
    });
  }, [gate, rowId]);

  const poll = useRef(null);
  const stopPoll = useCallback(() => {
    if (poll.current) clearTimeout(poll.current);
    poll.current = null;
  }, []);
  useEffect(() => stopPoll, [stopPoll]);
  const startPoll = useCallback(
    (columnIds) => {
      stopPoll();
      let n = 0;
      const tick = async () => {
        n += 1;
        await queryClient.invalidateQueries({
          queryKey: rowViewKeys.all(datasetId),
        });
        const data = queryClient.getQueriesData({
          queryKey: [...rowViewKeys.all(datasetId), "row", rowId],
        });
        if (cellsStillRunning(data, rowId, columnIds) && n < RERUN_POLL_MAX) {
          poll.current = setTimeout(tick, RERUN_POLL_MS);
          return;
        }
        poll.current = null;
        setRerunningId(null);
        refreshGrid?.();
      };
      poll.current = setTimeout(tick, RERUN_POLL_MS);
    },
    [queryClient, datasetId, rowId, refreshGrid, stopPoll],
  );

  const { mutate: rerun } = useMutation({
    meta: { errorHandled: true },
    mutationFn: (column) => {
      setRerunningId(column.id);
      return axios.post(endpoints.develop.evaluateRunRows(), {
        run_prompt_ids: [column.id],
        row_ids: [rowId],
        selected_all_rows: false,
      });
    },
    onSuccess: (res, column) => {
      enqueueSnackbar(`Re-run started for ${column.name} on ${label}`, {
        variant: "success",
      });
      // The cell (and the evals that read it) compute on the server: keep
      // reading the row until none of them is running, then the new value
      // and scores show (options-truth L3 G7 the interface contract).
      const ids = res?.data?.result?.columns?.length
        ? res.data.result.columns
        : [column.id];
      setRerunningId(column.id);
      startPoll(ids);
      refreshGrid?.();
    },
    onError: () => {
      setRerunningId(null);
      enqueueSnackbar("Couldn't start the re-run. Try again.", {
        variant: "error",
      });
    },
  });

  return {
    copyLink,
    download,
    copyRowJson,
    duplicate,
    remove,
    selectInGrid,
    newExperiment,
    rerun,
    rerunningId,
  };
}
