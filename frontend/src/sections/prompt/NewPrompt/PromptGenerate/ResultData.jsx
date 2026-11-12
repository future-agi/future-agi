import { Box, IconButton, Tooltip } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import React, { useMemo, useRef } from "react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import PropTypes from "prop-types";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { enqueueSnackbar } from "notistack";

import ResultsCellRenderer from "./Renderers/ResultsCellRenderer";
import JsonOutputRenderer from "./Renderers/JsonOutputRenderer";
import CellMarkdown from "src/sections/common/CellMarkdown";

/**
 * Check if a string is valid JSON
 * @param {string} str - String to check
 * @returns {boolean} - True if valid JSON
 */
const isValidJson = (str) => {
  if (typeof str !== "string") return false;
  try {
    const parsed = JSON.parse(str);
    return typeof parsed === "object" && parsed !== null;
  } catch {
    return false;
  }
};

const RESULT_DATA_THEME_PARAMS = {
  columnBorder: true,
  checkboxBorderRadius: 2,
  checkboxBorderWidth: 1.5,
  rowBorder: true,
  headerFontSize: "13px",
  headerColumnBorder: { width: "1px" },
  headerRowBorder: { width: "1px" },
  wrapperBorderRadius: 1,
};

const ResultData = ({ data, responseFormat, columnName = "output" }) => {
  const agTheme = useAgThemeWith(RESULT_DATA_THEME_PARAMS);
  const gridRef = useRef(null);

  // Determine if we should render as JSON
  const isJsonOutput = useMemo(() => {
    const responseType = responseFormat?.type;
    const isJsonFormat =
      responseType === "json_object" ||
      responseType === "object" ||
      responseType === "json";

    // If explicitly JSON format, or if output is valid JSON
    if (isJsonFormat) return true;

    // Auto-detect JSON in output
    if (data?.output?.length === 1 && isValidJson(data.output[0])) {
      return true;
    }

    return false;
  }, [responseFormat, data?.output]);

  const gridTheme = agTheme;

  const getRowHeight = (params) => {
    const gridContainer = document.querySelector(".ag-root-wrapper");
    if (!gridContainer) {
      return 0;
    }

    const isEvalPreset = Object.keys(data?.evaluationResults || {})?.length > 0;

    if (isEvalPreset) {
      if (params.node.rowIndex === 0) {
        return 320;
      } else {
        return 152;
      }
    }

    const containerHeight = gridContainer.clientHeight;
    const headerHeight = 50; // Header height
    const availableHeight = containerHeight - headerHeight - 20;

    return availableHeight;
  };

  const columns = useMemo(() => {
    const cols = [];
    const isEvalPreset = Object.keys(data?.evaluationResults || {})?.length > 0;
    if (isEvalPreset) {
      cols.push({
        field: "evaluation",
        headerName: "Evals",
        cellRenderer: ResultsCellRenderer,
        flex: 1,
        autoHeight: true,
        wrapText: true,
        minWidth: 300,
      });
    }
    data?.output?.forEach((_, index) => {
      cols.push({
        field: `result-${index + 1}`,
        headerName: `Result ${index + 1}`,
        flex: 1,
        cellRenderer: ResultsCellRenderer,
        minWidth: 300,
        autoHeight: true,
        wrapText: true,
      });
    });

    return cols;
  }, [data]);

  const rows = useMemo(() => {
    const rows = [];
    const isEvalPreset = Object.keys(data?.evaluationResults || {})?.length > 0;

    if (data?.output?.length > 0) {
      const initialRow = {};
      if (isEvalPreset) {
        initialRow.evaluation = "";
      }
      for (const [index, result] of data?.output?.entries() || []) {
        initialRow[`result-${index + 1}`] = result;
      }
      rows.push(initialRow);
    }

    if (isEvalPreset) {
      for (const [_, evalResult] of Object.entries(
        data?.evaluationResults || {},
      ) || []) {
        const eachRow = {};
        eachRow.evaluation = evalResult.name;
        for (const [index] of data?.output?.entries() || []) {
          eachRow[`result-${index + 1}`] = evalResult.results?.[index];
        }
        rows.push(eachRow);
      }
    }

    return rows;
  }, [data]);

  const defaultColDef = {
    flex: 1,
    minWidth: 200,
    sortable: false,
    filter: false,
    resizable: false,
    cellClass: "ag-cell-wrap-text",
    suppressHeaderMenuButton: true,
    suppressContextMenu: true,
    suppressHeaderContextMenu: true,
    cellRenderer: ResultsCellRenderer,
    cellStyle: {
      padding: 0,
      height: "100%",
      display: "flex",
      flex: 1,
      flexGrow: 1,
      flexDirection: "column",
    },
  };

  const handleCopyClick = () => {
    if (data?.output?.length === 1) {
      copyToClipboard(data.output[0]);
      enqueueSnackbar("Copied to clipboard", {
        variant: "success",
      });
    }
  };

  return (
    <>
      {data?.output?.length === 1 ? (
        <Box
          sx={{
            border: "2px solid var(--border-light)",
            borderRadius: "8px",
            padding: "16px 16px 16px 24px",
            flex: "1",
            overflow: "auto",
            position: "relative",
          }}
        >
          {!isJsonOutput && (
            <Tooltip title={"Copy"} placement="bottom" arrow>
              <IconButton
                sx={{ position: "absolute", top: "5px", right: "5px" }}
                size="small"
                onClick={handleCopyClick}
              >
                <Iconify
                  icon="solar:copy-linear"
                  sx={{ color: "text.disabled" }}
                />
              </IconButton>
            </Tooltip>
          )}
          {isJsonOutput ? (
            <JsonOutputRenderer
              data={data.output[0]}
              columnName={columnName}
              showPaths={true}
              initialExpanded={true}
            />
          ) : (
            <CellMarkdown spacing={0} text={data.output[0]} />
          )}
        </Box>
      ) : (
        <Box
          sx={{
            height: "100%",
            "& .ag-root-wrapper": {
              height: "100% !important",
            },
            "& .ag-root": {
              height: "100% !important",
            },
            "& .ag-body": {
              height: "100% !important",
            },
            "& .ag-body-viewport": {
              height: "100% !important",
            },
          }}
        >
          <AgGridReact
            columnDefs={columns}
            ref={gridRef}
            rowData={rows}
            alwaysShowHorizontalScroll
            theme={gridTheme}
            defaultColDef={defaultColDef}
            domLayout="normal"
            enableCellTextSelection={true}
            animateRows={true}
            getRowHeight={getRowHeight}
            onGridSizeChanged={() => {
              if (gridRef.current) {
                gridRef.current.api.resetRowHeights();
              }
            }}
          />
        </Box>
      )}
    </>
  );
};

ResultData.propTypes = {
  data: PropTypes.object,
  responseFormat: PropTypes.object,
  columnName: PropTypes.string,
};

export default ResultData;
