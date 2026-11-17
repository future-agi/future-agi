import { Box, Typography, CircularProgress } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import NoOptimizationSelected from "./NoOptimizationSelected";
import TemplateAccordion from "src/sections/common/TemplateAccordion";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";

const OPTIMIZATION_THEME_PARAMS = {
  columnBorder: true,
  rowVerticalPaddingScale: 2.6,
  headerVerticalPaddingScale: 2,
};

const OptimizationDetail = ({ selectedOptimization }) => {
  const agTheme = useAgThemeWith(OPTIMIZATION_THEME_PARAMS);
  const { data } = useQuery({
    queryKey: ["develop-optimization-detail", selectedOptimization?.id],
    queryFn: () =>
      axios.get(
        endpoints.develop.optimizeDevelop.detail(selectedOptimization?.id),
      ),
    enabled: !!selectedOptimization?.id,
    select: (res) => res.data,
  });

  const getScoreColor = (value) => {
    if (value >= 70) return "var(--score-green-light)"; // Light green for scores 70-100
    if (value >= 50) return "var(--score-yellow)"; // Light yellow for scores 50-70
    return "var(--score-red-light)"; // Light red for scores 0-50
  };

  const columnDefs = [
    {
      headerName: "Metric",
      field: "eval_name",
      cellStyle: {
        display: "flex",
        alignItems: "center",
        textAlign: "left",
        paddingLeft: "10px",
      },
    },
    {
      headerName: "Temp 1",
      field: "new_prompt",
      valueFormatter: (params) => {
        const value = params.value;
        return value ? `${Math.round(value)}%` : "0%";
      },
      cellStyle: (params) => {
        const value = params.value || 0;
        return {
          backgroundColor: getScoreColor(value),
          display: "flex",
          alignItems: "center",
          textAlign: "left",
          paddingLeft: "20px",
          height: "100%",
          minHeight: "48px",
          color: "var(--text-secondary)",
          fontWeight: 400,
          fontsize: "14px",
          lineHeight: "18px",
          margin: 0,
        };
      },
      cellClass: "cell-wrap-text",
    },
    {
      headerName: "Temp 2",
      field: "old_prompt",
      valueFormatter: (params) => {
        const value = params.value;
        return value ? `${Math.round(value)}%` : "0%";
      },
      cellStyle: (params) => {
        const value = params.value || 0;
        return {
          backgroundColor: getScoreColor(value),
          display: "flex",
          alignItems: "center",
          textAlign: "left",
          paddingLeft: "20px",
          height: "100%",
          minHeight: "48px",
          color: "var(--text-secondary)",
          fontWeight: 400,
          fontsize: "14px",
          lineHeight: "18px",
          margin: 0,
        };
      },
      cellClass: "cell-wrap-text",
    },
  ];

  const defaultColDef = {
    filter: false,
    resizable: true,
    wrapText: true,
    flex: 1,
    cellStyle: {
      whiteSpace: "pre-wrap",
      lineHeight: "1",
      display: "flex",
      alignItems: "center",
    },
    headerClass: "ag-header-cell-padding",
    autoHeight: false,
    valueGetter: (v) => {
      if (v.column.colId === "evalName") return v.data?.eval_name;
      return v.data?.[v.column.colId]?.average?.average;
    },
  };

  if (!selectedOptimization)
    return (
      <Box sx={{ flexBasis: "70%" }}>
        <NoOptimizationSelected />
      </Box>
    );

  if (selectedOptimization?.status === "Running") {
    return (
      <Box
        sx={{
          maxWidth: "70%",
          width: "70%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 2,
        }}
      >
        <CircularProgress size={60} />
        <Typography variant="subtitle2" color="text.primary">
          Optimization is running , please wait for few minutes
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        maxWidth: "70%",
        width: "70%",
        padding: "20px",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        height: "100%",
        overflow: "auto",
      }}
    >
      <TemplateAccordion
        templates={data?.optimizedKPrompts || []}
        mode="outlined"
      />
      <Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: "14px" }}>
          {/* <Iconify icon="solar:card-linear" /> */}
          <Typography fontWeight={500} fontSize="13px">
            Template Score
          </Typography>
        </Box>
        <Box
          className="ag-theme-quartz"
          style={{ flex: 1, paddingTop: "14px", height: "400px" }}
        >
          <AgGridReact
            theme={agTheme}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            pagination={false}
            suppressRowClickSelection={true}
            rowData={data?.evaluationColumns || []}
            domLayout="normal"
          />
        </Box>
      </Box>
    </Box>
  );
};

OptimizationDetail.propTypes = {
  selectedOptimization: PropTypes.object,
};

export default OptimizationDetail;
