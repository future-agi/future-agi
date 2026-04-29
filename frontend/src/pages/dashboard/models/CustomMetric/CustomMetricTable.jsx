import { Box, Button, useTheme } from "@mui/material";
import { DataGrid } from "@mui/x-data-grid";
import React from "react";
import PropTypes from "prop-types";
import { useNavigate, useParams } from "react-router";
import Iconify from "src/components/iconify";

const getEvaluationType = (evalRagContext, evalPromptTemplate) => {
  if (evalRagContext) return "EVALUATE_CONTEXT";
  if (evalPromptTemplate) return "EVALUATE_PROMPT_TEMPLATE";
  return "EVALUATE_CHAT";
};

const columns = ({ setUpdateMetricData }) => {
  return [
    { field: "name", headerName: "Metric Name", flex: 1 },
    {
      field: "text_prompt",
      headerName: "Description",
      flex: 1,
      sortable: false,
    },
    {
      field: "metric_type",
      headerName: "Type",
      flex: 1,
      sortable: false,
    },
    {
      field: "datasets",
      headerName: "Datasets",
      flex: 1,
      sortable: false,
    },
    {
      headerName: "",
      sortable: false,
      renderCell: ({ row }) => (
        <Button
          variant="contained"
          color="primary"
          size="small"
          fullWidth
          startIcon={
            <Iconify
              icon="solar:pen-bold"
              sx={{ color: "common.white" }}
              width={16}
            />
          }
          onClick={(e) => {
            e.stopPropagation();
            // trackEvent(Events.customMetricEditStart);
            setUpdateMetricData({
              id: row.id,
              name: row.name,
              prompt: row.text_prompt,
              metricType: row.metric_type === "StepwiseModelInference" ? 2 : 1,
              datasets: row.raw_datasets,
              evaluationType: getEvaluationType(
                row.eval_rag_context,
                row.eval_prompt_template,
              ),
            });
          }}
        >
          Edit
        </Button>
      ),
    },
  ];
};

const CustomMetricTable = ({
  customMetricData,
  paginationModel,
  setPaginationModel,
  isLoading,
  sortModel,
  setSortModel,
  setUpdateMetricData,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { id } = useParams();

  return (
    <Box sx={{ width: "100%", height: "calc(100vh - 230px)" }}>
      <DataGrid
        rows={customMetricData?.results || []}
        columns={columns({ setUpdateMetricData })}
        sx={{
          "& .MuiDataGrid-row:hover": {
            cursor: "pointer",
            backgroundColor: `${theme.palette.primary.main}07`,
          },
          height: "100%",
          "& .MuiDataGrid-columnHeaders": {
            fontSize: "12px",
            padding: "8px",
          },
          "& .MuiDataGrid-cell": {
            fontSize: "12px",
            padding: "8px",
          },
        }}
        rowCount={customMetricData?.count || 0}
        paginationModel={paginationModel}
        loading={isLoading}
        paginationMode="server"
        onPaginationModelChange={setPaginationModel}
        disableRowSelectionOnClick
        disableColumnFilter
        disableColumnMenu
        sortModel={sortModel}
        onSortModelChange={(newSortModel) => setSortModel(newSortModel)}
        onRowClick={(data) =>
          navigate(`/dashboard/models/${id}/performance?metricId=${data.id}`)
        }
      />
    </Box>
  );
};

CustomMetricTable.propTypes = {
  customMetricData: PropTypes.object,
  paginationModel: PropTypes.any,
  setPaginationModel: PropTypes.any,
  isLoading: PropTypes.bool,
  sortModel: PropTypes.object,
  setSortModel: PropTypes.func,
  setUpdateMetricData: PropTypes.func,
};

export default CustomMetricTable;
