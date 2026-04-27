import React from "react";
import { DataGrid } from "@mui/x-data-grid";
import { Box, useTheme } from "@mui/material";
import Chart from "../../../components/charts/chart";
import { useNavigate } from "react-router";
import PropTypes from "prop-types";

const chatOptions = (palette) => ({
  chart: {
    zoom: { enabled: false },
    toolbar: {
      show: false,
    },
    sparkline: {
      enabled: true,
    },
  },
  tooltip: {
    enabled: false,
  },
  xaxis: {
    min: 0,
    axisBorder: {
      show: false,
    },
    axisTicks: {
      show: false,
    },
    labels: {
      show: false,
    },
    floating: true,
  },
  yaxis: {
    min: 0,
    floating: true,
    axisBorder: {
      show: false,
    },
    axisTicks: {
      show: false,
    },
    labels: {
      show: false,
    },
  },
  grid: {
    show: false,
  },
  stroke: {
    curve: "smooth",
    width: 1.5,
    colors: [palette.primary.main],
  },
  fill: {
    type: "gradient",
    gradient: {
      shadeIntensity: 1,
      opacityFrom: 0.7,
      opacityTo: 0.9,
      stops: [0, 90, 100],
      colorStops: [
        [
          {
            offset: 0.6,
            color: palette.primary.light,
            opacity: 1,
          },
          {
            offset: 100,
            color: "common.white",
            opacity: 1,
          },
        ],
      ],
    },
  },
});

const columns = (palette) => [
  { field: "user_model_id", headerName: "Name", flex: 1 },
  {
    field: "model_type",
    headerName: "Model Type",
    flex: 1,
    sortable: false,
  },
  {
    field: "total_count",
    headerName: "Volume",
    flex: 1,
    sortable: false,
  },
  {
    field: "volume",
    headerName: "Last 30 day volume",
    renderCell: ({ value }) => {
      return (
        <Chart
          series={[
            {
              name: "area",
              data: value,
            },
          ]}
          options={chatOptions(palette)}
          height={68}
          width="70%"
          type="area"
        />
      );
    },
    sortable: false,
    flex: 1,
  },
];

const ModelsTable = ({
  modelData,
  paginationModel,
  setPaginationModel,
  isLoading,
  sortModel,
  setSortModel,
}) => {
  const navigate = useNavigate();
  const theme = useTheme();

  return (
    <Box sx={{ height: "calc(100vh - 165px)", width: "100%" }}>
      <DataGrid
        rows={modelData?.results || []}
        columns={columns(theme.palette)}
        sx={{
          "& .MuiDataGrid-row:hover": {
            cursor: "pointer",
            backgroundColor: `${theme.palette.primary.main}07`,
          },
          "& .MuiDataGrid-columnHeaders": {
            fontSize: "12px",
            padding: "8px",
          },
          "& .MuiDataGrid-cell": {
            fontSize: "12px",
            padding: "8px",
          },
        }}
        rowCount={modelData?.count || 0}
        paginationModel={paginationModel}
        loading={isLoading}
        paginationMode="server"
        onPaginationModelChange={setPaginationModel}
        disableRowSelectionOnClick
        disableColumnFilter
        disableColumnMenu
        getRowHeight={() => 70}
        onRowClick={({ id, row }) => {
          navigate(`/dashboard/models/${id}/performance`, {
            state: { model: row },
          });
        }}
        sortModel={sortModel}
        onSortModelChange={(newSortModel) => setSortModel(newSortModel)}
      />
    </Box>
  );
};

ModelsTable.propTypes = {
  modelData: PropTypes.object,
  paginationModel: PropTypes.any,
  setPaginationModel: PropTypes.any,
  isLoading: PropTypes.bool,
  sortModel: PropTypes.object,
  setSortModel: PropTypes.func,
};

export default ModelsTable;
