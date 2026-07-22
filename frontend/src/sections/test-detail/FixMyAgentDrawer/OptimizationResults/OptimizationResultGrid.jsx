import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";

import "./OptimizationResultGrid.css";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { getOptimizationResultColumnConfig } from "./common";
import { useFixMyAgentDrawerStoreShallow } from "../state";
import { FixMyAgentDrawerSections } from "../common";
import { useNavigate, useParams } from "react-router";
import { useOptimizationResultContext } from "./context/OptimizationResultContext";

const defaultColDef = {
  lockVisible: true,
  sortable: false,
  filter: false,
  resizable: true,
  suppressMenu: true,
};

const OptimizationResultGrid = ({ optimizationId, isDrawer = true }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const navigate = useNavigate();
  const { testId, executionId } = useParams();
  const { setGridApi } = useOptimizationResultContext();
  const { data: optimizationData } = useQuery({
    queryKey: ["fix-my-agent-optimization-details", optimizationId],
    queryFn: () =>
      axios.get(
        endpoints.optimizeSimulate.getOptimizationDetails(optimizationId),
      ),
    enabled: false,
    select: (data) => data?.data?.result,
  });

  const { setOpenSection } = useFixMyAgentDrawerStoreShallow((state) => ({
    setOpenSection: state.setOpenSection,
  }));

  const columnDefs = useMemo(() => {
    return getOptimizationResultColumnConfig(optimizationData?.column_config);
  }, [optimizationData]);

  return (
    <Box sx={{ height: "100%" }}>
      <AgGridReact
        theme={agTheme}
        rowSelection={undefined}
        autoSizeStrategy={{
          type: "fitCellContents",
        }}
        onGridReady={(params) => {
          setGridApi(params.api);
        }}
        className="optimization-result-grid"
        domLayout="autoHeight"
        columnDefs={columnDefs}
        defaultColDef={defaultColDef}
        pagination={false}
        paginationPageSizeSelector={false}
        rowData={optimizationData?.table}
        rowStyle={{ cursor: "pointer" }}
        getRowId={({ data }) => data.id}
        tooltipShowDelay={200}
        tooltipInteraction={true}
        // Selection is disabled, so clicking a cell does nothing
        onCellClicked={(event) => {
          if (isDrawer) {
            setOpenSection({
              section: FixMyAgentDrawerSections.TRIAL_DETAIL,
              id: optimizationId,
              trialId: event?.data?.id,
            });
          } else if (event?.data?.id) {
            navigate(
              `/dashboard/simulate/test/${testId}/${executionId}/${optimizationId}/${event?.data?.id}`,
            );
          }
        }}
      />
    </Box>
  );
};

OptimizationResultGrid.propTypes = {
  optimizationId: PropTypes.string.isRequired,
  isDrawer: PropTypes.bool,
};

export default OptimizationResultGrid;
