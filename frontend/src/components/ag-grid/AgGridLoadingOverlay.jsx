import React, { useLayoutEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Skeleton } from "@mui/material";

// AG Grid's loading overlay for the whole product (set as the default in
// utils/ag-grid-setup.js): skeleton rows under the real column headers, one
// bar per visible column at that column's width — the same table loading
// state as DataTable, instead of AG Grid's "Loading..." popup. It covers the
// server-side model's own placeholder row, so a grid shows one loader.
// A refetch over rows already on screen only dims them, so the table never
// wipes back to skeleton on paging, search or refocus.

const ROW_COUNT = 8;
const BAR_WIDTHS = [70, 45, 55, 35];

function hasLoadedRows(api) {
  const first = api?.getDisplayedRowAtIndex?.(0);
  return Boolean(first && !first.stub && first.data);
}

export default function AgGridLoadingOverlay({ api }) {
  const columns = api?.getAllDisplayedColumns?.() ?? [];
  const refetch = hasLoadedRows(api);
  // AG Grid's overlay layer covers the header too; start below the real
  // header (measured, so grouped or filter headers are respected).
  const ref = useRef(null);
  const [headerHeight, setHeaderHeight] = useState(32);
  useLayoutEffect(() => {
    const header = ref.current
      ?.closest(".ag-root-wrapper")
      ?.querySelector(".ag-header");
    if (header) setHeaderHeight(header.getBoundingClientRect().height);
  }, []);

  return (
    <Box
      ref={ref}
      role="progressbar"
      aria-label="Loading"
      aria-busy="true"
      sx={{
        position: "absolute",
        top: headerHeight,
        left: 0,
        right: 0,
        bottom: 0,
        bgcolor: "background.paper",
        opacity: refetch ? 0.5 : 1,
        overflow: "hidden",
        pointerEvents: "none",
      }}
    >
      {!refetch &&
        Array.from({ length: ROW_COUNT }, (_, row) => (
          <Box
            // eslint-disable-next-line react/no-array-index-key
            key={row}
            sx={{
              display: "flex",
              alignItems: "center",
              height: "var(--ag-row-height, 36px)",
              borderBottom: 1,
              borderColor: "divider",
            }}
          >
            {columns.map((col, i) => (
              <Box
                key={col.getColId()}
                sx={{ width: col.getActualWidth(), flexShrink: 0, px: "10px" }}
              >
                {!col.getColDef().checkboxSelection &&
                  !col.getColId().startsWith("ag-Grid-SelectionColumn") && (
                    <Skeleton
                      variant="rounded"
                      height={10}
                      width={`${BAR_WIDTHS[(i + row) % BAR_WIDTHS.length]}%`}
                    />
                  )}
              </Box>
            ))}
          </Box>
        ))}
    </Box>
  );
}

AgGridLoadingOverlay.propTypes = {
  api: PropTypes.object,
};
