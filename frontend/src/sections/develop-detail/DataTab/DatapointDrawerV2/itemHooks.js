// Shared reads of the Row view kind tabs (R2 P5, L5b): the dataset's
// columns (the grid's deduped query), a row's cells by column name or data
// type with the pending change-set applied, and the dataset record (kind,
// environment) with a pending environment change applied.
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { METADATA_COLUMN, useChangeSet } from "../Versions/changeSetStore";

/** column_config of a dataset (same key and request as the grid header). */
export function useDatasetColumns(datasetId) {
  return useQuery({
    queryKey: ["dataset-detail-average", datasetId],
    queryFn: () =>
      axios.get(endpoints.develop.getDatasetDetail(datasetId), {
        params: { column_config_only: true },
      }),
    select: (d) => d.data?.result?.column_config ?? [],
    enabled: Boolean(datasetId),
  });
}

const rawCell = (row, colId) => {
  const cell = row?.[colId];
  if (cell == null) return null;
  if (typeof cell === "object" && "cell_value" in cell) return cell.cell_value;
  return cell;
};

/**
 * The row's cells, pending edits applied: `{byName, byType, columns,
 * original, metadata, originalMetadata, columnOf(name)}`.
 */
export function useRowCells(datasetId, row) {
  const { data: columns = [] } = useDatasetColumns(datasetId);
  const changeSet = useChangeSet(datasetId);
  const rowId = row?.row_id;
  return useMemo(() => {
    const edits = (rowId && changeSet.byCell?.[rowId]) || {};
    const byName = {};
    const original = {};
    const byType = {};
    columns.forEach((c) => {
      const base = rawCell(row, c.id);
      const value = edits[c.id] ? edits[c.id].after : base;
      byName[c.name] = value;
      original[c.name] = base;
      if (!byType[c.data_type]) byType[c.data_type] = { column: c, value };
    });
    const originalMetadata = row?.row_meta?.metadata ?? {};
    const metadata = edits[METADATA_COLUMN]
      ? edits[METADATA_COLUMN].after ?? {}
      : originalMetadata;
    return {
      columns,
      byName,
      byType,
      original,
      metadata,
      originalMetadata,
      columnOf: (name) => columns.find((c) => c.name === name) ?? null,
      edited: (name) => {
        const col = columns.find((c) => c.name === name);
        return Boolean(col && edits[col.id]);
      },
    };
  }, [columns, row, rowId, changeSet.byCell]);
}

/**
 * The dataset record from the name list (`kind`, `environment`), with the
 * pending `set_environment` op applied: `{kind, dataset, environment,
 * pendingEnvironment}`.
 */
export function useDatasetEnvironment(datasetId) {
  const { kind, dataset, isLoading } = useDatasetKind(datasetId);
  const { ops } = useChangeSet(datasetId);
  return useMemo(() => {
    const op = ops.find((o) => o.op === "set_environment") ?? null;
    const saved = dataset?.environment ?? null;
    let environment = saved;
    if (op) {
      const { op: _o, id: _i, at: _a, before: _b, ...patch } = op;
      environment = { ...(saved ?? {}), ...patch };
    }
    return { kind, dataset, isLoading, saved, environment, pendingOp: op };
  }, [kind, dataset, isLoading, ops]);
}
