// The Data tab grid's column layout (one view, A1-10): the server column
// config stays the LIST of columns (and their defaults: shown, frozen); what
// THIS user shows, hides, freezes and reorders is their layout (per user,
// per dataset, remembered, inside saved views). The grid (DevelopDataV2,
// via useAgColumnShaping) and the toolbar (View menu → Columns, `C`) call
// this hook with the same dataset, so they share one layout store.
import { useMemo } from "react";
import { isPrototypeMode } from "src/utils/prototype";
import { useQuery } from "@tanstack/react-query";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { useDatasetSplits } from "src/api/develop/dataset-splits";
import useColumnLayout from "src/sections/common/ux/oneview/useColumnLayout";
import { useColumnFromPathStore } from "src/sections/common/Values/columnFromPathStore";
import {
  columnGroupKey,
  COLUMN_GROUPS,
  DEFAULT_SYSTEM_COLS,
} from "./dataGridView";
import { collabCatalogEntries, datasetHasTags } from "./collabColumns";

// HO D4: the W4 comment and assignment reads are served in prototype mode.
const COLLAB_COLUMNS_SERVED = isPrototypeMode();

/** Layout surface of the Data grid; also the saved views' surface. */
export const DATA_GRID_SURFACE = "grid";

const originOf = (c) => c?.origin_type ?? c?.originType;
const frozenOf = (c) => c?.is_frozen ?? c?.isFrozen;
const visibleOf = (c) => c?.is_visible ?? c?.isVisible;

/** ColumnsMenu groups: the dataset's role groups, then System. */
export const DATA_GRID_GROUPS = [
  ...COLUMN_GROUPS.map((g) => ({ id: g.key, label: g.label })),
  { id: "system", label: "Row info" },
];

/**
 * The catalog, in grid order: `Row` (locked, frozen), `Splits` (when the
 * dataset has splits), the dataset's columns (eval reasons ride with their
 * eval), `Source`, `Updated`. Ids equal the grid's colIds. Pure; tested.
 *
 * @param {object[]} columnConfig  `result.column_config`
 * @param {{hasSplits?: boolean}} [opts]
 */
export function dataGridCatalog(
  columnConfig,
  { hasSplits = false, collab = [] } = {},
) {
  const system = (id, label, defaultVisible) => ({
    id,
    label,
    group: "system",
    defaultVisible,
    pinnable: false,
  });
  const sourceColumns = (columnConfig ?? []).filter(
    (c) => c?.id && originOf(c) !== "evaluation_reason",
  );
  // Keep the row number plus one useful identity column in view by default.
  // `id` is the explicit identifier in multimodal data; the first input is the
  // stable fallback for conversation and task datasets. Stored one-view prefs
  // still override this catalog default through useColumnLayout.
  const identity =
    sourceColumns.find((c) => {
      const name = String(c.name ?? c.id)
        .trim()
        .toLowerCase();
      return name === "id" || name === "uuid" || name === "row_id";
    }) ?? sourceColumns.find((c) => c.role === "input");
  const identityId = identity?.id;
  const data = sourceColumns.map((c) => {
    const frozen = frozenOf(c);
    return {
      id: c.id,
      label: c.name ?? c.id,
      group: columnGroupKey(c),
      defaultVisible: visibleOf(c) !== false,
      ...(frozen === "left" || frozen === true || c.id === identityId
        ? { defaultPinned: "left" }
        : {}),
    };
  });
  return [
    {
      id: "system:order",
      label: "Row",
      group: "system",
      locked: true,
      defaultPinned: "left",
    },
    ...(hasSplits
      ? [system("system:splits", "Splits", DEFAULT_SYSTEM_COLS.splits)]
      : []),
    ...data,
    system("system:source", "Source", DEFAULT_SYSTEM_COLS.source),
    system("system:updated", "Updated", DEFAULT_SYSTEM_COLS.updated),
    // HO D4: Comments, Assigned to, Tags (off by default).
    ...collab,
  ];
}

/**
 * @param {string} datasetId
 * @param {{commands?: boolean, enabled?: boolean}} [opts]  `commands`
 *   registers ⌘K `Columns…` / `Reset columns` (one caller per page);
 *   `enabled: false` (scenario grids) → an empty catalog
 */
export default function useDataGridLayout(
  datasetId,
  { commands = true, enabled = true } = {},
) {
  const { data } = useQuery(
    getDatasetQueryOptions(datasetId, 0, [], [], "", { enabled: false }),
  );
  const { data: splitData } = useDatasetSplits(datasetId, { enabled });
  const config = data?.data?.result?.column_config;
  const hasSplits = (splitData?.splits?.length ?? 0) > 0;
  const hasTags = datasetHasTags(data?.data?.result?.metadata);
  const columns = useMemo(
    () =>
      enabled
        ? dataGridCatalog(config, {
            hasSplits,
            collab: collabCatalogEntries({
              served: COLLAB_COLUMNS_SERVED,
              hasTags,
            }),
          })
        : [],
    [enabled, config, hasSplits, hasTags],
  );
  const layout = useColumnLayout({
    surface: DATA_GRID_SURFACE,
    scopeId: datasetId,
    columns,
    commands: commands && enabled,
  });
  // IT-R1 G5 P11: the Columns editor's headings read `Input`, `Expected`,
  // `Row info`, never the raw group ids.
  // P8: View › Columns › `Add column from path…` (any JSON column).
  return useMemo(
    () => ({
      ...layout,
      groups: DATA_GRID_GROUPS,
      addFromPath: enabled
        ? () => useColumnFromPathStore.getState().openFor({ datasetId })
        : undefined,
    }),
    [layout, enabled, datasetId],
  );
}
