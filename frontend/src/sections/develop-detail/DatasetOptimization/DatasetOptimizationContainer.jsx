import React, { useMemo, useCallback, useEffect } from "react";
import { Box } from "@mui/material";
import DatasetOptimizationRunList from "./DatasetOptimizationRunList";
import DatasetOptimizationDetail from "./DatasetOptimizationDetail";
import DatasetOptimizationTrialDetail from "./DatasetOptimizationTrialDetail";
import DatasetOptimizationDrawer from "./DatasetOptimizationDrawer";
import { useRunOptimizationStore } from "../states";
import { useUrlState } from "src/routes/hooks/use-url-state";
import PropTypes from "prop-types";
import { optimizationCreateOffered } from "./createDoor";

/**
 * Main container component for Dataset Optimization.
 * Manages view switching between:
 * - List view (optimization runs)
 * - Detail view (single optimization with trials)
 * - Trial detail view (single trial with prompt comparison)
 *
 * Uses URL parameters for navigation state to maintain browser history:
 * - ?optimizationId=xxx - shows optimization detail
 * - ?optimizationId=xxx&trialId=yyy - shows trial detail
 */
const DatasetOptimizationContainer = ({
  datasetId,
  columnId,
  columns = [],
}) => {
  // URL-based state for navigation (persists across page refreshes)
  const [optimizationId, setOptimizationId, removeOptimizationId] = useUrlState(
    "optimizationId",
    null,
  );
  const [trialId, setTrialId, removeTrialId] = useUrlState("trialId", null);

  // Derive the current view from URL parameters
  const detailView = useMemo(() => {
    if (optimizationId && trialId) return "trial";
    if (optimizationId) return "detail";
    return null;
  }, [optimizationId, trialId]);

  // Zustand store for drawer state (doesn't need URL persistence)
  const { setOpenRunOptimization } = useRunOptimizationStore();

  // Filter columns to only show RUN_PROMPT columns for optimization
  // Normalize case comparison since API may return different cases
  const columnOptions = useMemo(() => {
    return columns
      .filter(
        (col) =>
          col.originType?.toLowerCase() === "run_prompt" ||
          col.source?.toLowerCase() === "run_prompt",
      )
      .map((col) => ({
        value: col.field || col.id,
        label: col.headerName || col.name,
      }));
  }, [columns]);

  // Navigation handlers that update URL
  const handleSelectOptimization = useCallback(
    (id) => {
      setOptimizationId(id);
    },
    [setOptimizationId],
  );

  const handleSelectTrial = useCallback(
    (id) => {
      setTrialId(id);
    },
    [setTrialId],
  );

  const handleBackToList = useCallback(() => {
    removeTrialId();
    removeOptimizationId();
  }, [removeOptimizationId, removeTrialId]);

  const handleBackToDetail = useCallback(() => {
    removeTrialId();
  }, [removeTrialId]);

  const handleCreateClick = useCallback(() => {
    setOpenRunOptimization(true);
  }, [setOpenRunOptimization]);
  // W6 C0: no create door where nothing answers it (createDoor.js).
  const canCreate = optimizationCreateOffered();

  // Clean up URL params when the optimization container unmounts (e.g., tab switch)
  useEffect(() => {
    return () => {
      removeOptimizationId();
      removeTrialId();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // run only on mount

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* List View */}
      {!detailView && (
        <DatasetOptimizationRunList
          datasetId={datasetId}
          columnId={columnId}
          onCreateClick={canCreate ? handleCreateClick : undefined}
          onSelectOptimization={handleSelectOptimization}
        />
      )}

      {/* Detail View */}
      {detailView === "detail" && optimizationId && (
        <DatasetOptimizationDetail
          optimizationId={optimizationId}
          onBack={handleBackToList}
          onSelectTrial={handleSelectTrial}
        />
      )}

      {/* Trial Detail View */}
      {detailView === "trial" && optimizationId && trialId && (
        <DatasetOptimizationTrialDetail
          optimizationId={optimizationId}
          trialId={trialId}
          onBack={handleBackToDetail}
          onBackToList={handleBackToList}
        />
      )}

      {/* Create/Rerun Drawer */}
      {canCreate ? (
        <DatasetOptimizationDrawer
          datasetId={datasetId}
          columnOptions={columnOptions}
          allColumns={columns}
        />
      ) : null}
    </Box>
  );
};

DatasetOptimizationContainer.propTypes = {
  datasetId: PropTypes.string,
  columnId: PropTypes.string,
  columns: PropTypes.array,
};

export default DatasetOptimizationContainer;
