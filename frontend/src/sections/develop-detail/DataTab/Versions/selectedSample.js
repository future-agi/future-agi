// IT-R2 G5 P2 (S8): the New experiment drawer's Sample is the first
// SELECTED row. The drawer's sample table holds the first 50 rows of the
// version, so selected rows past them (Row 251…) and pending door rows
// (`imp-1`) were never offered and the Sample fell back to Row 12. This
// hook merges them in front of the sample rows and returns the experiment
// form context with `rows`, `sampleRow`, `sampleRowLabel` and `rowLabel`
// overridden; the Data and Evaluations steps provide it to their children.
import { useMemo } from "react";
import { useWatch } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import { create } from "zustand";
import axios from "src/utils/axios";
import {
  rowLabelOf,
  useExperimentForm,
} from "../../Experiment/ExperimentFormContext";
import { useChangeSetStore } from "./changeSetStore";
import { tableRequestFor } from "./versionedSource";

const EMPTY = Object.freeze([]);
export const isPendingRowId = (id) => /^(new|imp)-/.test(String(id ?? ""));

/** The Sample the user picked, per dataset + selection (survives steps). */
const usePickedSample = create((set) => ({
  picked: {},
  pick: (key, rowId) => set((s) => ({ picked: { ...s.picked, [key]: rowId } })),
}));

/**
 * Pending door rows as table rows: `{row_id, pending_no, [colId]:
 * {cell_value}}` (`pending_no` = its number among the pending new rows).
 * Pure.
 */
export function pendingSampleRows(ops, rowIds) {
  const adds = (ops ?? []).filter((o) => o?.op === "add_row" && o.temp_id);
  const ids = new Set(rowIds ?? []);
  return adds
    .map((o, i) => ({ op: o, no: i + 1 }))
    .filter(({ op }) => ids.has(op.temp_id))
    .map(({ op, no }) => ({
      row_id: op.temp_id,
      pending_no: no,
      ...Object.fromEntries(
        Object.entries(op.cells ?? {}).map(([colId, v]) => [
          colId,
          { cell_value: v },
        ]),
      ),
    }));
}

/** `New row 1` for a pending row, else `Row 251`. */
export const sampleRowLabelOf = (row) =>
  row?.pending_no ? `New row ${row.pending_no}` : rowLabelOf(row);

function useSavedSelectedRows(datasetId, ids, enabled) {
  const key = ids.join(",");
  return useQuery({
    queryKey: ["experiment-create", "selected-rows", datasetId, key],
    queryFn: () => {
      const { url, params } = tableRequestFor({
        datasetId,
        params: {
          filters: JSON.stringify([
            {
              column_id: "system:row_ids",
              filter_config: { filter_value: ids },
            },
          ]),
          page_size: Math.min(500, ids.length),
          current_page_index: 0,
        },
      });
      return axios.get(url, { params });
    },
    select: (res) => res?.data?.result?.table ?? [],
    enabled: Boolean(datasetId) && enabled && ids.length > 0,
    staleTime: 30_000,
  });
}

/**
 * The experiment form context with the selected rows offered first and the
 * Sample defaulting to the first of them.
 * @param {object} control react-hook-form control of the drawer
 */
export function useSelectedSampleContext(control) {
  const ctx = useExperimentForm();
  const { datasetId, versions } = ctx;
  const rowIds = useWatch({ control, name: "rowIds" }) ?? EMPTY;
  const versionId = useWatch({ control, name: "datasetVersionId" });
  const ops = useChangeSetStore((st) => st.sets?.[datasetId]?.ops) ?? EMPTY;
  const pickKey = `${datasetId}|${rowIds.join(",")}`;
  const picked = usePickedSample((s) => s.picked[pickKey] ?? null);
  const pick = usePickedSample((s) => s.pick);
  const latestNumber = Math.max(0, ...(versions ?? []).map((v) => v.number));
  const pinned = (versions ?? []).find((v) => v.id === versionId);
  const onLatest = !pinned || pinned.number === latestNumber;
  const saved = rowIds.filter((id) => !isPendingRowId(id));
  const inSample = new Set((ctx.rows ?? []).map((r) => r.row_id));
  const missing = saved.filter((id) => !inSample.has(id));
  const fetched = useSavedSelectedRows(datasetId, missing, onLatest);
  const fetchedRows = fetched.data ?? EMPTY;

  return useMemo(() => {
    if (!rowIds.length) return ctx;
    const pending = pendingSampleRows(ops, rowIds);
    const byId = new Map(
      [...(ctx.rows ?? []), ...fetchedRows, ...pending].map((r) => [
        r.row_id,
        r,
      ]),
    );
    const selected = rowIds.map((id) => byId.get(id)).filter(Boolean);
    if (!selected.length) return ctx;
    const ids = new Set(selected.map((r) => r.row_id));
    const rows = [
      ...selected,
      ...(ctx.rows ?? []).filter((r) => !ids.has(r.row_id)),
    ];
    const sampleRow =
      (picked && rows.find((r) => r.row_id === picked)) ?? selected[0];
    return {
      ...ctx,
      rows,
      sampleRow,
      sampleRowLabel: sampleRowLabelOf(sampleRow),
      rowLabel: sampleRowLabelOf,
      sampleIsPending: Boolean(sampleRow?.pending_no),
      setSampleRowId: (id) => {
        pick(pickKey, id);
        ctx.setSampleRowId?.(id);
      },
    };
  }, [ctx, rowIds, ops, fetchedRows, picked, pick, pickKey]);
}
