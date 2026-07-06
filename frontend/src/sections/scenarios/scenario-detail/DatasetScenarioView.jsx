import React, { useEffect } from "react";
import { useParams, useNavigate, Navigate } from "react-router-dom";
import { Box, CircularProgress, Typography } from "@mui/material";
import { useGetScenarioDetail } from "src/api/scenarios/scenarios";

const DatasetScenarioView = () => {
  const { scenarioId } = useParams();
  const navigate = useNavigate();

  // Fetch scenario details
  const { data: scenario, isLoading, error } = useGetScenarioDetail(scenarioId);

  useEffect(() => {
    // If scenario is loaded and it's not a dataset type, redirect back
    if (scenario && scenario.scenario_type !== "dataset") {
      navigate("/dashboard/simulate/scenarios");
    }
  }, [scenario, navigate]);

  if (isLoading) {
    return (
      <Box
        sx={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Box
        sx={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography color="error">
          Error loading scenario: {error.message}
        </Typography>
      </Box>
    );
  }

  // Extract dataset ID - handle both possible field names from backend
  const datasetId =
    scenario?.datasetId || scenario?.dataset_id || scenario?.dataset;

  if (!scenario || !datasetId) {
    return (
      <Box
        sx={{
          height: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Typography>No dataset found for this scenario</Typography>
      </Box>
    );
  }

  // Redirect to the develop detail view with the dataset ID
  // This preserves all the existing functionality of DevelopDetailView
  return <Navigate to={`/dashboard/develop/${datasetId}`} replace />;
};

export default DatasetScenarioView;
