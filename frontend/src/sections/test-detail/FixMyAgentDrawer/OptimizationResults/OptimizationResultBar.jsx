import { Box, Button, Typography } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import SvgColor from "../../../../components/svg-color";
import CustomTooltip from "../../../../components/tooltip";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import logger from "src/utils/logger";
import { useOptimizationResultContext } from "./context/OptimizationResultContext";
import { transformColDefToColumnStructure } from "src/utils/optimization";

const OptimizationResultBar = ({ optimizationData }) => {
  const { getGridApi } = useOptimizationResultContext();
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const columnConfigureRef = useRef(null);
  const isData = true;
  const iconStyles = {
    width: 16,
    height: 16,
    color: isData ? "text.primary" : "divider",
  };
  const bestOptimization = useMemo(() => {
    const bestItem = optimizationData?.table?.find((item) => item?.is_best);
    return bestItem?.score_percentage_change;
  }, [optimizationData]);
  const [gridColumnDefs, setGridColumnDefs] = useState([]);
  const gridApi = getGridApi();

  // Watch for column changes using gridApi
  useEffect(() => {
    if (!gridApi) return;

    // Get initial column definitions
    const initialColumnDefs = gridApi.getColumnDefs();
    logger.debug({
      initialColumnDefs,
      transformed: initialColumnDefs,
    });
    setGridColumnDefs(transformColDefToColumnStructure(initialColumnDefs));

    // Listen for column-related events to update column definitions
    const handleColumnChange = () => {
      const latestColumnDefs = gridApi.getColumnDefs();

      setGridColumnDefs(transformColDefToColumnStructure(latestColumnDefs));
    };

    // Add event listeners for various column events
    const columnEvents = [
      "columnVisible",
      "columnPinned",
      "columnResized",
      "columnMoved",
      "columnValueChanged",
      "newColumnsLoaded",
      "gridColumnsChanged",
    ];

    columnEvents.forEach((eventType) => {
      gridApi.addEventListener(eventType, handleColumnChange);
    });

    // Cleanup event listeners
    return () => {
      columnEvents.forEach((eventType) => {
        gridApi.removeEventListener(eventType, handleColumnChange);
      });
    };
  }, [gridApi]);

  // Handle column visibility change in AG Grid
  const handleColumnVisibilityChange = (newVisibilityMap) => {
    if (!gridApi) return;

    const latestColumnDefs = gridApi.getColumnDefs();

    const newColumnDefs = latestColumnDefs?.map((col) => {
      if (col?.children) {
        const newChildMap = col?.children?.map((child) => {
          return {
            ...child,
            hide: !newVisibilityMap[child.colId],
            isVisible: newVisibilityMap[child.colId],
          };
        });
        return {
          ...col,
          children: newChildMap,
        };
      } else {
        return {
          ...col,
          hide: !newVisibilityMap[col.colId],
          isVisible: newVisibilityMap[col.colId],
        };
      }
    });

    try {
      gridApi.setGridOption("columnDefs", newColumnDefs);
    } catch (error) {
      logger.error("Error setting column visibility:", error);
    }
  };

  // Handle column reordering in AG Grid
  const handleSetColumns = (newColumns) => {
    if (!gridApi || !Array.isArray(newColumns)) return;

    logger.debug("handleSetColumns | newColumns", { newColumns });

    try {
      gridApi.setGridOption("columnDefs", newColumns);
    } catch (error) {
      logger.error("Error setting column visibility:", error);
    }
  };

  return (
    <Box
      sx={{
        gap: "12px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <ShowComponent
        condition={bestOptimization !== null && bestOptimization !== undefined}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <SvgColor
            src="/assets/icons/ic_info.svg"
            sx={{
              color: "blue.500",
              width: "16px",
              height: "16px",
            }}
          />
          <Typography typography={"s2_1"} fontWeight={"fontWeightRegular"}>
            Improvement percentages shown below represent the improvement from
            your baseline prompt scores
          </Typography>
        </Box>
      </ShowComponent>

      <CustomTooltip
        title="View Column"
        arrow
        show={true}
        size="small"
        type="black"
      >
        <Button
          variant="outlined"
          size="small"
          startIcon={
            <SvgColor
              src="/assets/icons/action_buttons/ic_column.svg"
              sx={iconStyles}
            />
          }
          ref={columnConfigureRef}
          onClick={() => setOpenColumnConfigure(true)}
        >
          View Column
        </Button>
      </CustomTooltip>
      <ColumnConfigureDropDown
        open={openColumnConfigure}
        onClose={() => setOpenColumnConfigure(false)}
        anchorEl={columnConfigureRef?.current}
        columns={gridColumnDefs}
        onColumnVisibilityChange={handleColumnVisibilityChange}
        setColumns={handleSetColumns}
      />
    </Box>
  );
};

OptimizationResultBar.propTypes = {
  optimizationData: PropTypes.object,
};

export default OptimizationResultBar;
