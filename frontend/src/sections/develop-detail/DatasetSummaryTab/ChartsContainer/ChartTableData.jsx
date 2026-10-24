import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { Box, Typography } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import CompareDatasetSummaryIcon from "../CompareDatasetSummaryIcon";

const evaluationDefaultColDef = {
  lockVisible: true,
  sortable: true,
  filter: false,
  resizable: false,
  suppressHeaderMenuButton: true,
  suppressHeaderContextMenu: true,
  flex: 1,
};

const DatasetCellRenderer = (props) => {
  return (
    <Box
      sx={{ display: "flex", alignItems: "center", gap: 1, marginTop: "7px" }}
    >
      <CompareDatasetSummaryIcon index={props.data.datasetIndex} />
      <Typography
        sx={{ fontSize: "13px", fontWeight: 400, color: "text.primary" }}
      >
        {props.value}
      </Typography>
    </Box>
  );
};

DatasetCellRenderer.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
};

const ChartTableData = ({ data, graphLabels = [], datasetIndex }) => {
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const { evaluationTabColumnDefs, evaluationTableData } = useMemo(() => {
    const headerData = [
      {
        headerName: "Eval Name",
        field: "name",
        flex: 2.5,
        headerClass: "hasRightBorder",
        cellClass: "hasRightBorder",
        minWidth: 200,
        cellRenderer: DatasetCellRenderer,
      },
    ];

    graphLabels?.forEach((item) => {
      // @ts-ignore
      headerData.push({
        headerName: item.toUpperCase(),
        field: item,
        flex: 1,
        headerClass: "hasRightBorder",
        minWidth: 80,
        valueGetter: (params) => {
          return (
            params?.data[item] ??
            params?.data[String(item)] ??
            params?.data[Number(item)]
          );
        },
        cellStyle: (row) => ({
          backgroundColor: interpolateColorBasedOnScore(row.value, 1),
        }),
      });
    });
    const tableData = [];
    data?.forEach((item) => {
      const currentIndex =
        datasetIndex || datasetIndex === 0 ? datasetIndex : item.datasetIndex;
      const tempData = {};
      tempData["name"] = item.name;
      tempData["datasetIndex"] = currentIndex;
      item?.value?.forEach(
        (temp, index) => (tempData[graphLabels[index]] = temp),
      );
      tableData.push(tempData);
    });
    return {
      evaluationTabColumnDefs: headerData,
      evaluationTableData: tableData,
    };
  }, [graphLabels, data, datasetIndex]);

  return (
    <Box sx={{ height: "100%", width: "100%" }}>
      <AgGridReact
        theme={agTheme}
        columnDefs={evaluationTabColumnDefs}
        defaultColDef={evaluationDefaultColDef}
        pagination={false}
        domLayout="normal"
        rowData={evaluationTableData}
        maxBlocksInCache={1}
        debug={true}
      />
    </Box>
  );
};

export default ChartTableData;

ChartTableData.propTypes = {
  data: PropTypes.array,
  graphLabels: PropTypes.array,
  datasetIndex: PropTypes.number,
  height: PropTypes.number,
};
