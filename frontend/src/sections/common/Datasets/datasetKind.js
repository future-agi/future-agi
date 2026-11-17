// What kind of dataset a dataset is (R2-SPEC P5): `rows` (default),
// `task_suite` (task rows run in an environment) or `conversations`
// (imported sessions). `kind` is additive on the dataset list items
// (get-datasets and get-datasets-names; W0c).
//
// Frozen API (R2 manifest row 56): DATASET_KINDS, datasetKindOf,
// useDatasetKind.
import { useMemo } from "react";
import { useDevelopDatasetList } from "src/api/develop/develop-detail";

export const DATASET_KINDS = Object.freeze([
  "rows",
  "task_suite",
  "conversations",
]);

/** `dataset.kind`, else `rows`. */
export function datasetKindOf(dataset) {
  const kind = dataset?.kind;
  return DATASET_KINDS.includes(kind) ? kind : "rows";
}

/**
 * The kind of one dataset from the dataset name list (a shared query, so
 * usually no extra request): `{kind, dataset, isLoading}`; `kind` reads
 * `rows` until the list loads. `dataset` also carries the additive
 * `scratch_experiment_id` (Try on one row).
 */
export function useDatasetKind(datasetId) {
  const { data, isLoading } = useDevelopDatasetList();
  return useMemo(() => {
    const dataset = (data ?? []).find((d) => d.datasetId === datasetId) ?? null;
    return { kind: datasetKindOf(dataset), dataset, isLoading };
  }, [data, datasetId, isLoading]);
}

/**
 * A row's one name on every screen (grid, drawer, Matrix, side by side):
 * `T4` in a task suite, else `Row 4`. The row id only ever appears inside a
 * copy chip.
 */
export function rowLabel(kind, n) {
  if (n == null || n === "" || Number.isNaN(Number(n))) return "Row";
  return kind === "task_suite" ? `T${n}` : `Row ${n}`;
}
