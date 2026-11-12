import React from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { useParams, useSearchParams } from "react-router-dom";
import AsOfBanner from "./Versions/AsOfBanner";
import PendingChangesBar from "./Versions/PendingChangesBar";
import VersionHistoryPanel from "./Versions/VersionHistoryPanel";
import RestoreVersionDialog from "./Versions/RestoreVersionDialog";
import RowViewPanel from "./DatapointDrawerV2/RowViewPanel";
import GenerateRowsDialog from "src/sections/common/AddData/GenerateRowsDialog";

/**
 * Data tab frame (PRODUCT-SPEC §2.2, §7.2). The grid stays live in the
 * middle; lanes fill the slots without editing DevelopDataV2:
 *   top    <AsOfBanner/>           (L3) "Viewing v5 … read-only"
 *   right  <RowViewPanel/>         (L1) docked Row view, `?row=`
 *          <VersionHistoryPanel/>  (L3) docked History, `?history=1`
 *          Only one is open at a time; History wins while `history=1`.
 *   bottom <PendingChangesBar/>    (L3) "Unsaved changes — 1 edited"
 *
 * Right-slot contract: each panel receives what sits to its left as `grid`
 * and renders it exactly once, beside the panel when open (RowPanelShell /
 * ResizableSplit) or alone when closed. Keep `grid` at the same element
 * position in both states so AG Grid does not remount.
 *
 * The panels are nested, never swapped (swapping component types would
 * remount the grid on every History toggle):
 *   <VersionHistoryPanel open={history} grid={
 *     <RowViewPanel suppressed={history} grid={<the grid/>} />} />
 * While History is open the Row view stays closed (`row` stays in the URL,
 * so closing History brings the row back).
 */
const DataTabLayout = ({ children }) => {
  const [searchParams] = useSearchParams();
  const { dataset: datasetId } = useParams();
  const historyOpen = searchParams.get("history") === "1";

  return (
    <Box
      sx={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <AsOfBanner />
      <Box sx={{ flex: 1, minHeight: 0, display: "flex" }}>
        <VersionHistoryPanel
          open={historyOpen}
          grid={<RowViewPanel grid={children} suppressed={historyOpen} />}
        />
      </Box>
      <PendingChangesBar />
      {/* R3 (P2): `Restore as v7…` from History, the as-of banner, ⌘K. */}
      <RestoreVersionDialog datasetId={datasetId} />
      {/* W3-8a: Add rows › Generate rows, Generate more like these. */}
      <GenerateRowsDialog />
    </Box>
  );
};

DataTabLayout.propTypes = {
  children: PropTypes.node,
};

export default DataTabLayout;
