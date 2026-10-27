import { Box, Button, Typography } from "@mui/material";
import React, { useEffect, useMemo, useRef, useState } from "react";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import PropTypes from "prop-types";
import { ShowComponent } from "src/components/show";
import ColumnConfigureDropDown from "src/sections/project-detail/ColumnDropdown/ColumnConfigureDropDown";
import logger from "src/utils/logger";
import { useDatasetOptimizationResultContext } from "./context/DatasetOptimizationResultContext";
import { transformColDefToColumnStructure } from "src/utils/optimization";

/**
 * Dataset Optimization Result Bar Component
 *
 * Similar to OptimizationResultBar from simulation.
 */
const DatasetOptimizationResultBar = ({ optimizationData }) => {
  const { getGridApi } = useDatasetOptimizationResultContext();
  const [openColumnConfigure, setOpenColumnConfigure] = useState(false);
  const columnConfigureRef = useRef(null);
  const isData = true;
  const iconStyles = {
    width: 16,
    height: 16,
    color: isData ? "text.primary" : "text.disabled",
  };

  const bestOptimization = useMemo(() => {
    const bestItem = optimizationData?.table?.find((item) => item?.is_best);
    return bestItem?.score_percentage_change;
  }, [optimizationData]);

  const [gridColumnDefs, setGridColumnDefs] = useState([]);
  const gridApi = getGridApi();

  useEffect(() => {
    if (!gridApi) return;

    const initialColumnDefs = gridApi.getColumnDefs();
    setGridColumnDefs(transformColDefToColumnStructure(initialColumnDefs));

    const handleColumnChange = () => {
      const latestColumnDefs = gridApi.getColumnDefs();
      setGridColumnDefs(transformColDefToColumnStructure(latestColumnDefs));
    };

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

    return () => {
      columnEvents.forEach((eventType) => {
        gridApi.removeEventListener(eventType, handleColumnChange);
      });
    };
  }, [gridApi]);

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

  const handleSetColumns = (newColumns) => {
    if (!gridApi || !Array.isArray(newColumns)) return;

    try {
      gridApi.setGridOption("columnDefs", newColumns);
    } catch (error) {
      logger.error("Error setting columns:", error);
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
          <Typography typography="s2_1" fontWeight="fontWeightRegular">
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

DatasetOptimizationResultBar.propTypes = {
  optimizationData: PropTypes.object,
};

export default DatasetOptimizationResultBar;
