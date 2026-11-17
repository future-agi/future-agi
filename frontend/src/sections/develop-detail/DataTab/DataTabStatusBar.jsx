import { Box, Link, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getDatasetQueryKey } from "src/api/develop/develop-detail";
import {
  latestOf,
  useDatasetVersions,
  versionByNumber,
} from "src/api/develop/dataset-versions";
import { versionedQueryOptions } from "./Versions/versionedSource";
import { asOfFooterCounts } from "./asOfFooterCounts";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useDevelopSelectedRowsStoreShallow } from "../states";
import { useChangeSetStore } from "./Versions/changeSetStore";
import { selectedCount } from "./DataGrid/selectionRows";
import { dataFooterParts } from "./DataGrid/dataHelp";
import { useDataGridView } from "./DataGrid/useDataGridView";
import { useDatasetKind } from "src/sections/common/Datasets/datasetKind";
import { fmtCount } from "src/sections/common/format/fmt";
import Term from "src/sections/common/ux/Term";
import CollabFooterLinks from "src/sections/common/Collab/CollabFooterLinks";

/**
 * AG Grid sizes a status-bar section to its content and clips whatever
 * spills past the bar. Bounding the footer by the bar lets the summary on
 * the left ellipsize before the collaboration links on the right are cut.
 * Returns `[ref, maxWidth]` (`null` until measured).
 */
function useFitToStatusBar() {
  const [node, setNode] = useState(null);
  const [maxWidth, setMaxWidth] = useState(null);
  useEffect(() => {
    const bar = node?.closest?.(".ag-status-bar");
    if (!bar || typeof ResizeObserver === "undefined") return undefined;
    const measure = () => {
      const pad = parseFloat(window.getComputedStyle(bar).paddingRight) || 0;
      const room =
        bar.getBoundingClientRect().right -
        pad -
        node.getBoundingClientRect().left;
      setMaxWidth(room > 0 ? Math.floor(room) : null);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(bar);
    return () => observer.disconnect();
  }, [node]);
  return [setNode, maxWidth];
}

/**
 * Grid status bar. Versioned dataset grid (G11 + one view A1-09): the
 * help line folded in, no extra row above the grid —
 * `30 tasks · each has an input, an expected result and a start state ·
 *  3 fail at least one check · v2` (the failed count turns on Failed
 * only; `check` is a glossary Term). Scenario grids keep the classic
 * "Showing Rows: n / Total Rows: m".
 */
const DataTabStatusBar = ({ datasetId, versioned = false }) => {
  const [loadedRows, setLoadedRows] = useState(0);
  const [totalRows, setTotalRows] = useState(0);
  // Saved rows the grid shows. Kept in state: after Save version the total
  // (saved + pending) can stay the same while this moves, e.g. 24 + 6 → 30.
  const [filteredRows, setFilteredRows] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [fitRef, fitWidth] = useFitToStatusBar();
  const queryClient = useQueryClient();
  // `?version=5` (read-only as-of view) labels the bar with that version.
  const [searchParams] = useSearchParams();
  const asOfVersion = Number(searchParams.get("version")) || null;
  // Latest from the versions list (refetched after Save version); the
  // page-0 table read below can be an idle, stale cache entry.
  const { data: versions } = useDatasetVersions(
    versioned ? datasetId : undefined,
  );
  const { selectAll, toggledNodes } = useDevelopSelectedRowsStoreShallow(
    (s) => ({
      selectAll: s.selectAll,
      toggledNodes: s.toggledNodes,
    }),
  );

  const { gridApi } = useDevelopDetailContext();
  const api = gridApi.current;
  const { kind } = useDatasetKind(versioned ? datasetId : undefined);
  const gridView = useDataGridView(datasetId);
  // Pending new rows (Add row / Duplicate) until a version saves them.
  const ops = useChangeSetStore((s) =>
    versioned ? s.sets[datasetId]?.ops ?? null : null,
  );
  const pendingRows = (ops ?? []).filter((o) => o.op === "add_row");
  const added = pendingRows.length;

  const getVisibleRowCount = useCallback(() => {
    if (!api) return 0;
    const lastDisplayedRow = api.getLastDisplayedRowIndex();
    return lastDisplayedRow + 1;
  }, [api]);

  const updateRowCount = useCallback(() => {
    if (!api) return;
    const context = api?.getGridOption?.("context");
    const total = context?.totalRowCount ?? api.getDisplayedRowCount();
    setTotalRows(total);
    setFilteredRows(context?.filteredRowCount ?? null);
    setLoadedRows(getVisibleRowCount());

    if (context?.totalRowCount !== undefined) {
      setIsLoading(false);
    }
  }, [api, getVisibleRowCount]);

  useEffect(() => {
    if (!api) return;

    // Initial update
    updateRowCount();

    // Listen to grid events that affect visible rows
    const events = [
      "modelUpdated", // Fires on data changes, filtering, sorting
      "bodyScroll", // Fires on scroll - gives real-time feedback
      "viewportChanged", // Fires when viewport changes
      "firstDataRendered", // Fires when first data is rendered
    ];

    events.forEach((event) => {
      api.addEventListener(event, updateRowCount);
    });

    // Subscribe to grid updates
    return () => {
      if (!api.isDestroyed()) {
        events.forEach((event) => {
          api.removeEventListener(event, updateRowCount);
        });
      }
    };
  }, [api, getVisibleRowCount, updateRowCount]);

  if (isLoading) return <Box sx={{ padding: 1 }} />;

  if (!versioned) {
    return (
      <Box sx={{ padding: 1, display: "flex", alignItems: "center", gap: 1 }}>
        <span>
          Showing Rows: {loadedRows} / Total Rows: {totalRows || 0}
        </span>
      </Box>
    );
  }

  // Unfiltered size and version come from the page-0 table read (no filters).
  const baseResult = queryClient.getQueryData(
    getDatasetQueryKey(datasetId, 0, [], [], ""),
  )?.data?.result;
  // v2.1 g8 (versions:as-of): `?version=N` counts that version's rows (its
  // N1 row_count, else its own unfiltered page-0 read), never latest's.
  const asOfRow = asOfVersion ? versionByNumber(versions, asOfVersion) : null;
  const asOfResult = asOfVersion
    ? queryClient.getQueryData(
        versionedQueryOptions(queryClient, datasetId, 0, [], [], "").queryKey,
      )?.data?.result
    : null;
  const base = baseResult?.metadata;
  const context = api?.getGridOption?.("context");
  const shown = filteredRows ?? context?.filteredRowCount ?? totalRows;
  const counts = asOfFooterCounts({
    asOfVersion,
    versionRowCount: asOfRow?.row_count,
    asOfTotal: asOfResult?.metadata?.total_rows,
    latestTotal: base?.total_rows,
    shown,
    totalRows,
  });
  const selected = selectedCount({ selectAll, toggledNodes, total: totalRows });

  const parts = dataFooterParts({
    kind,
    total: counts.total,
    shown,
    columns: asOfResult?.column_config ?? baseResult?.column_config,
    // The failed count describes the saved rows of latest; hide it while
    // Failed only is already on (the grid shows exactly those) and as of
    // an older version (its generated columns are not shown).
    failedRows:
      gridView.failedOnly || asOfVersion ? null : base?.counts?.failed_rows,
    selected,
    version: asOfVersion ?? latestOf(versions)?.number ?? base?.version?.number,
    added: asOfVersion ? 0 : added,
    pendingRows: asOfVersion ? [] : pendingRows,
  });
  const dot = (
    <Box component="span" sx={{ color: "text.disabled", px: 0.75 }}>
      ·
    </Box>
  );

  return (
    <Box
      ref={fitRef}
      data-testid="data-grid-footer"
      sx={{
        px: 1,
        py: 0.75,
        display: "flex",
        alignItems: "center",
        minWidth: 0,
        maxWidth: fitWidth ?? undefined,
      }}
    >
      <Typography
        typography="s2"
        color="text.secondary"
        noWrap
        sx={{ fontVariantNumeric: "tabular-nums", minWidth: 0 }}
      >
        {parts.count}
        {parts.each ? (
          <>
            {dot}
            {parts.each}
          </>
        ) : null}
        {parts.failed ? (
          <>
            {dot}
            <Link
              component="button"
              type="button"
              typography="s2"
              color="inherit"
              underline="always"
              onClick={() => gridView.setFailedOnly(true)}
              sx={{ verticalAlign: "baseline", textDecorationStyle: "dotted" }}
            >
              {fmtCount(parts.failed)} fail
            </Link>{" "}
            at least one <Term k="check">check</Term>
          </>
        ) : null}
        {parts.selected ? (
          <>
            {dot}
            {parts.selected}
          </>
        ) : null}
        {parts.version ? (
          <>
            {dot}
            {parts.version}
          </>
        ) : null}
      </Typography>
      <CollabFooterLinks
        datasetId={datasetId}
        gridView={gridView}
        selection={{ selectAll, toggledNodes, count: selected, api }}
      />
    </Box>
  );
};

DataTabStatusBar.propTypes = {
  api: PropTypes.object,
  context: PropTypes.object,
  datasetId: PropTypes.string,
  versioned: PropTypes.bool,
};

export default DataTabStatusBar;
