import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { parseVersionParam } from "src/api/develop/dataset-versions";
import { isUserDataColumn } from "./versionedSource";

/**
 * What the Data grid needs to know about versions (the seam DevelopDataV2
 * calls; PRODUCT-SPEC §3.5). `versioned` is false for scenario grids.
 *   readOnly        `?version=` is set: no editing, user-data columns only
 *   versionNumber   the as-of number or null
 *   columnsFor(cfg) column_config without generated columns when read-only
 *   lockColumns(defs) colDefs with editing turned off when read-only
 */
export default function useAsOfGrid({ versioned = true } = {}) {
  const [searchParams] = useSearchParams();
  const parsed = parseVersionParam(searchParams.get("version"));
  // Scenario grids (versioned: false) never read past versions.
  const versionNumber = versioned ? parsed : null;
  const readOnly = versionNumber != null;

  const columnsFor = useCallback(
    (columns) =>
      readOnly ? (columns ?? []).filter(isUserDataColumn) : columns,
    [readOnly],
  );

  const lockColumns = useCallback(
    (defs) =>
      readOnly
        ? (defs ?? []).map((d) => ({
            ...d,
            editable: false,
            cellRendererParams: {
              ...(d.cellRendererParams ?? {}),
              editable: false,
            },
          }))
        : defs,
    [readOnly],
  );

  return useMemo(
    () => ({ readOnly, versionNumber, columnsFor, lockColumns }),
    [readOnly, versionNumber, columnsFor, lockColumns],
  );
}
