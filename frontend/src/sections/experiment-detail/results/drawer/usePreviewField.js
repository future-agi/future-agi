import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { runResultsKey } from "src/api/develop/run-results";
import { inputsPreviewChoice } from "src/sections/common/Preview/distinguishingField";
import { displayInputs } from "../cells/displayInputs";

// HO results R10 (W5 C-H6, the compare drawer half): the drawer's Input
// leads with the field the grid previews, the one that differs most across
// the rows in view. It reads the grid's own R3 read from the query cache
// (the same rows, so the same `chooseField` answer as the Input column).

const rowsOf = (data) => data?.data?.result?.rows ?? data?.rows ?? null;

/**
 * The grid read whose rows are the drawer's `rowIds` (same ids, same order),
 * else the read that holds the row with the most rows. Pure.
 * @param {Array<[unknown, object]>} entries queryClient.getQueriesData()
 */
export function gridRowsFor(entries, rowIds, exampleId) {
  const reads = (entries ?? [])
    .map(([, data]) => rowsOf(data))
    .filter((rows) => Array.isArray(rows) && rows.length);
  const ids = rowIds ?? [];
  const same = reads.find(
    (rows) =>
      ids.length > 0 &&
      rows.length === ids.length &&
      rows.every((r, i) => r.example_id === ids[i]),
  );
  if (same) return same;
  const holding = reads
    .filter((rows) => rows.some((r) => r.example_id === exampleId))
    .sort((a, b) => b.length - a.length);
  return holding[0] ?? null;
}

/** The field to show first for this row, or null (then the first filled one). */
export function previewFieldOf(rows, inputs) {
  if (!rows?.length) return null;
  const field = inputsPreviewChoice(rows).field;
  if (!field) return null;
  const v = inputs?.[field];
  const empty = v == null || v === "" || (Array.isArray(v) && v.length === 0);
  return empty ? null : field;
}

/**
 * `{field}` for the drawer's row: the grid's preview field when the row has
 * it. `inputNames` ({column id: name}) reads a native run's input envelope
 * by column name, as the grid's Input cell does (displayInputs).
 */
export default function usePreviewField(datasetId, row, rowIds, inputNames) {
  const queryClient = useQueryClient();
  const entries = queryClient.getQueriesData({
    queryKey: [...runResultsKey(datasetId), "grid"],
  });
  const rows = gridRowsFor(entries, rowIds, row?.example_id);
  // The grid read changes identity when it refetches.
  return useMemo(() => {
    const named = (r) => {
      const inputs = displayInputs(r?.inputs, inputNames);
      return inputs === r?.inputs ? r : { ...r, inputs };
    };
    return { field: previewFieldOf(rows?.map(named), named(row)?.inputs) };
  }, [rows, row, inputNames]);
}
