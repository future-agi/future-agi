import React, { useMemo } from "react";
import { Box, Typography, useTheme, alpha } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { getUniqueColorPalette } from "src/utils/utils";

// Rank cell renderer - shows king icon for rank 1
const RankCellRenderer = (params) => {
  if (params.value === undefined || params.value === null) {
    return null;
  }

  if (params.value === 1) {
    return (
      <Box
        sx={{ display: "flex", justifyContent: "space-between", width: "100%" }}
      >
        <Typography
          sx={{
            fontSize: "13px",
            fontWeight: 400,
            color: "14px",
            textAlign: "center",
          }}
        >
          {params.value}
        </Typography>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            paddingRight: "15px",
          }}
        >
          <Iconify
            icon="solar:crown-bold"
            width={20}
            height={20}
            // eslint-disable-next-line no-restricted-syntax -- ds: data colour
            sx={{ color: "#FFD700" }}
          />
        </Box>
      </Box>
    );
  }

  return (
    <Typography
      sx={{
        width: "100%",
        fontSize: "13px",
        fontWeight: 400,
        color: "14px",
        alignItems: "flex-start",
      }}
    >
      {params.value}
    </Typography>
  );
};

const DEFAULT_COL_DEF = {
  lockVisible: true,
  sortable: false,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
};

const CompareEvalTable = ({
  tableData,
  columnConfig,
  hasRankColumn,
  rowCount,
}) => {
  const muiTheme = useTheme();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.withColumnBorder);

  const DatasetCellRenderer = (params) => {
    const index = params.node.rowIndex;
    if (params?.value === "Average") return null;
    const { tagBackground, tagForeground } = getUniqueColorPalette(index);
    return (
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, marginTop: "7px" }}
      >
        <Typography
          sx={{
            color: tagForeground,
            backgroundColor: tagBackground,
            borderRadius: "6px",
            height: "24px",
            width: "24px",
            fontSize: "12px",
            fontWeight: 500,
            // paddingX: '4px',
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {String.fromCharCode(65 + index)}
        </Typography>
        <Typography
          sx={{ fontSize: "13px", fontWeight: 400, color: "text.primary" }}
        >
          {params.value}
        </Typography>
      </Box>
    );
  };

  // Percentage cell renderer
  const PercentageCellRenderer = (params) => {
    // Special handling for the Average row - just return as is
    if (params.data.datasetId === "average") {
      return params.value;
    }

    // Handle dash case - for null, undefined, or explicitly set to "-"
    if (params.value === null) {
      return "-";
    }

    // Format percentage values
    const value = parseFloat(params.value);
    if (isNaN(value)) return "-";

    return Number.isInteger(value) ? `${value}%` : `${Math.round(value)}%`;
  };

  // Define cell style based on value
  const getCellStyle = (params) => {
    // Handle "Average: X" format - always white background
    if (
      typeof params.value === "string" &&
      params.value.startsWith("Average:")
    ) {
      return { backgroundColor: "var(--bg-paper)" };
    }

    // For null or undefined values or dash, use the style for 0%
    if (
      params.value === null ||
      params.value === undefined ||
      params.value === "-"
    ) {
      return { backgroundColor: "var(--bg-paper)" };
    }

    const num =
      typeof params.value === "string"
        ? parseFloat(params.value.replace("%", ""))
        : parseFloat(params.value);

    if (isNaN(num)) return { backgroundColor: "var(--bg-paper)" };

    if (num > 60)
      return { backgroundColor: alpha(muiTheme.palette.success.main, 0.1) };
    if (num >= 40 && num <= 60)
      return { backgroundColor: alpha(muiTheme.palette.warning.main, 0.1) };
    return { backgroundColor: alpha(muiTheme.palette.error.main, 0.1) };
  };

  // Calculate average for dynamic columns
  const averageRow = useMemo(() => {
    if (!tableData.length || !columnConfig.length) return null;

    const averages = {};
    columnConfig.forEach((col) => {
      const values = tableData
        .map((row) => row[col.name])
        .filter(
          (val) =>
            val !== null &&
            val !== undefined &&
            val !== "-" &&
            !isNaN(parseFloat(val)),
        );

      if (values.length > 0) {
        const sum = values.reduce((acc, val) => acc + parseFloat(val), 0);
        const avgValue = Math.round(sum / values.length);
        // Just show the number for the average, not with % sign
        averages[col.name] = `Average: ${avgValue}%`;
      } else {
        averages[col.name] = "Average: 0%";
      }
    });

    return {
      datasetId: "average",
      datasetName: "Average",
      ...averages,
    };
  }, [tableData, columnConfig]);

  // Prepare column definitions for AG Grid
  const columnDefs = useMemo(() => {
    const cols = [];

    // Add rank column ONLY if hasRankColumn is true
    if (hasRankColumn) {
      cols.push({
        headerName: "Rank",
        field: "rank",
        width: 90,
        cellRenderer: RankCellRenderer,
        cellStyle: {
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        },
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        sortable: false, // Disable sorting
      });
    }

    // Add dataset name column
    cols.push({
      headerName: "Dataset Names",
      field: "dataset_name",
      flex: 1,
      cellRenderer: DatasetCellRenderer,
      autoHeight: true,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
      sortable: false, // Disable sorting
    });

    // Add dynamic columns from columnConfig
    columnConfig.forEach((col) => {
      cols.push({
        headerName: col.name,
        field: col.name,
        flex: 1,
        cellRenderer: PercentageCellRenderer,
        cellStyle: getCellStyle,
        suppressHeaderMenuButton: true,
        suppressHeaderContextMenu: true,
        sortable: false, // Disable sorting
      });
    });

    return cols;
  }, [columnConfig, hasRankColumn]);

  // Calculate the minimum height for the grid based on row count
  const getGridStyle = () => {
    const rowCount = averageRow ? tableData.length + 1 : tableData.length;

    // If 2 or fewer rows, set a minimum height
    if (rowCount <= 2) {
      return {
        width: "100%",
        minHeight: "150px", // Set a minimum height when few rows
      };
    }

    return {
      width: "100%",
      height: "auto",
    };
  };
  const hasData = tableData && tableData.length > 0;

  return (
    <>
      {hasData && (
        <Typography
          sx={{
            marginLeft: "49.9%",
            fontSize: "13px",
            color: "text.primary",
            fontWeight: 500,
            borderTop: "1px solid var(--border-light)",
            borderLeft: "1px solid var(--border-light)",
            borderRight: "1px solid var(--border-light)",
            borderTopLeftRadius: "5px",
            borderTopRightRadius: "5px",
            paddingX: "10px",
            paddingY: "5px",
          }}
        >
          Evaluation Metrics (from common {rowCount} datapoints)
        </Typography>
      )}

      <div style={getGridStyle()}>
        <AgGridReact
          columnDefs={columnDefs}
          rowData={averageRow ? [...tableData, averageRow] : tableData}
          domLayout="autoHeight"
          pagination={false}
          theme={agTheme}
          suppressRowClickSelection={true}
          suppressColumnVirtualisation={true}
          defaultColDef={DEFAULT_COL_DEF}
          getRowHeight={() => {
            return tableData.length === 1
              ? 75
              : tableData.length === 2
                ? 50
                : 40;
          }}
          loadingOverlayComponent="Loading..."
          loadingOverlayComponentParams={{ loadingMessage: "Loading data..." }}
          overlayLoadingTemplate={
            '<span class="ag-overlay-loading-center">Loading...</span>'
          }
        />
      </div>
    </>
  );
};

CompareEvalTable.propTypes = {
  tableData: PropTypes.array.isRequired,
  columnConfig: PropTypes.array.isRequired,
  hasRankColumn: PropTypes.bool.isRequired,
  rowCount: PropTypes.number,
};

export default CompareEvalTable;
