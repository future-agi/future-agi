import { Box } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";

import SummaryPercentileCellRenderer from "./Renderers/SummaryPercentileCellRenderer";
import SummaryAverageCellRenderer from "./Renderers/SummaryAverageCellRenderer";

const AVERAGE_GRID_THEME_PARAMS = {
  headerColumnBorder: { width: "0px" },
  checkboxBorderRadius: 2,
  checkboxBorderWidth: 1,
  rowBorder: true,
  headerFontSize: "13px",
  wrapperBorderRadius: 1,
};

const PERCENTILE_GRID_THEME_PARAMS = {
  columnBorder: true,
  rowBorder: true,
  headerFontSize: "13px",
  wrapperBorderRadius: 1,
};

const ResultSummary = ({ data }) => {
  const averageGridTheme = useAgThemeWith(AVERAGE_GRID_THEME_PARAMS);
  const percentileGridTheme = useAgThemeWith(PERCENTILE_GRID_THEME_PARAMS);
  const averageScoreColumns = [
    {
      field: "eval",
      headerName: "Evaluation metrics",
    },
    {
      field: "averageScore",
      headerName: "Average Score",
    },
  ];

  const percentileColumns = [
    {
      field: "eval",
      headerName: "Evaluation",
      minWidth: 200,
    },
    {
      field: "averageScore",
      headerName: "Average",
      minWidth: 120,
      cellRenderer: SummaryPercentileCellRenderer,
    },
    {
      field: "p20",
      headerName: "P20",
      minWidth: 80,
      cellRenderer: SummaryPercentileCellRenderer,
    },
    {
      field: "p40",
      headerName: "P40",
      minWidth: 80,
      cellRenderer: SummaryPercentileCellRenderer,
    },
    {
      field: "p60",
      headerName: "P60",
      minWidth: 80,
      cellRenderer: SummaryPercentileCellRenderer,
    },
    {
      field: "p80",
      headerName: "P80",
      minWidth: 80,
      cellRenderer: SummaryPercentileCellRenderer,
    },
    {
      field: "p90",
      headerName: "P90",
      minWidth: 80,
      cellRenderer: SummaryPercentileCellRenderer,
    },
    {
      field: "p100",
      headerName: "P100",
      minWidth: 90,
      cellRenderer: SummaryPercentileCellRenderer,
    },
  ];

  const averageRows = useMemo(() => {
    const rows = [];

    Object.values(data?.evaluationResults || {}).forEach((evalResult) => {
      const type = evalResult?.results?.[0]?.output;
      rows.push({
        eval: evalResult.name,
        averageScore:
          type === "score"
            ? evalResult.averageScore * 100
            : evalResult.averageScore,
      });
    });

    return rows;
  }, [data]);

  const percentileRows = useMemo(() => {
    const rows = [];

    Object.values(data?.evaluationResults || {}).forEach((evalResult) => {
      const obj = {};
      const type = evalResult?.results?.[0]?.output;

      obj.eval = evalResult.name;
      obj.averageScore =
        type === "score"
          ? evalResult.averageScore * 100
          : evalResult.averageScore;

      obj.p20 = evalResult.p20;
      obj.p40 = evalResult.p40;
      obj.p60 = evalResult.p60;
      obj.p80 = evalResult.p80;
      obj.p90 = evalResult.p90;
      obj.p100 = evalResult.p100;

      rows.push(obj);
    });

    return rows;
  }, [data]);

  const averageDefaultColDef = {
    flex: 1,
    cellRenderer: SummaryAverageCellRenderer,
    cellStyle: {
      padding: 0,
      height: "100%",
      display: "flex",
      flex: 1,
      flexDirection: "column",
      lineHeight: 1.5,
    },
  };

  const percentileDefaultColDef = {
    flex: 1,
    cellStyle: {
      padding: 0,
      height: "100%",
      display: "flex",
      flex: 1,
      flexDirection: "column",
      lineHeight: 1.5,
    },
    cellRenderer: SummaryPercentileCellRenderer,
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <AgGridReact
        columnDefs={averageScoreColumns}
        rowData={averageRows}
        alwaysShowHorizontalScroll
        theme={averageGridTheme}
        defaultColDef={averageDefaultColDef}
        domLayout="autoHeight"
        animateRows={true}
        rowHeight={44}
      />
      <AgGridReact
        columnDefs={percentileColumns}
        rowData={percentileRows}
        alwaysShowHorizontalScroll
        theme={percentileGridTheme}
        rowHeight={44}
        defaultColDef={percentileDefaultColDef}
        domLayout="autoHeight"
        animateRows={true}
      />
    </Box>
  );
};

ResultSummary.propTypes = {
  data: PropTypes.object,
};

export default ResultSummary;
