import { Box, Breadcrumbs, Button, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useEffect } from "react";
import axios, { endpoints } from "src/utils/axios";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";

// Reuse simulation loader component (doesn't have simulation-specific logic)
import OptmizationLoaderComponent from "src/sections/test-detail/OptimizeAgentDrawer.jsx/OptmizationLoaderComponent";
import DatasetOptimizationHeaderComponent from "./DatasetOptimizationHeaderComponent";
import DatasetOptimizingSteps from "./DatasetOptimizingSteps";
import DatasetOptimizationResultGraph from "./DatasetOptimizationResultGraph";
import DatasetOptimizationResultGrid from "./DatasetOptimizationResultGrid";
import DatasetOptimizationResultBar from "./DatasetOptimizationResultBar";
import DatasetOptimizationResultProvider from "./context/DatasetOptimizationResultProvider";
import DatasetOptimizationCancelled from "./DatasetOptimizationCancelled";
import { useDatasetOptimizationStore } from "./states";
import { normalizeDatasetOptimizationDetail } from "./common";

// Re-export context for use in child components
export { useDatasetOptimizationResultContext } from "./context/DatasetOptimizationResultContext";

// Status constants matching simulation
const AgentPromptOptimizerStatus = {
  PENDING: "pending",
  RUNNING: "running",
  COMPLETED: "completed",
  FAILED: "failed",
  CANCELLED: "cancelled",
};

const AgentPromptOptimizerRefetchStates = [
  AgentPromptOptimizerStatus.PENDING,
  AgentPromptOptimizerStatus.RUNNING,
];

/**
 * Dataset Optimization Detail View
 *
 * This component follows the same pattern as EachOptimizationComponent from simulation,
 * reusing the same UI components but fetching from dataset optimization endpoints.
 */
const DatasetOptimizationDetail = ({
  optimizationId,
  onBack,
  onSelectTrial,
}) => {
  // Fetch optimization details - same query pattern as simulation
  const { data: optimizationData, isPending: isPendingOptimizationData } =
    useQuery({
      queryKey: ["dataset-optimization-details", optimizationId],
      queryFn: () =>
        axios.get(endpoints.develop.datasetOptimization.detail(optimizationId)),
      enabled: !!optimizationId,
      refetchInterval: ({ state }) => {
        const result = state?.data?.data?.result;
        if (AgentPromptOptimizerRefetchStates.includes(result?.status)) {
          return 5000;
        }
        return false;
      },
      select: (data) =>
        normalizeDatasetOptimizationDetail(data?.data?.result ?? {}),
    });

  const handleTrialClick = (trialId) => {
    onSelectTrial?.(trialId);
  };

  // Reset store when leaving the container
  useEffect(() => {
    return () => {
      useDatasetOptimizationStore.getState().reset();
    };
  }, []);

  return (
    <Stack
      spacing={2}
      sx={{ padding: 2, width: "100%", height: "100%", paddingTop: 0 }}
    >
      {/* Breadcrumb navigation */}
      <Breadcrumbs
        sx={{
          "& .MuiBreadcrumbs-separator": {
            marginLeft: 0,
            marginRight: 0,
          },
        }}
        separator={
          <SvgColor
            src="/assets/icons/custom/lucide--chevron-right.svg"
            sx={{ width: 20, height: 20, bgcolor: "text.primary" }}
          />
        }
      >
        <Typography
          component={Button}
          size="small"
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.secondary"
          onClick={onBack}
          sx={{
            px: "10px",
            "&:hover": { backgroundColor: "transparent" },
          }}
        >
          Optimization Runs
        </Typography>
        <Typography
          typography="s1"
          fontWeight="fontWeightMedium"
          color="text.primary"
          sx={{ px: "10px" }}
        >
          {optimizationData?.optimiserName ?? "..."}
        </Typography>
      </Breadcrumbs>

      {/* Dataset-specific header component (without simulation rerun button) */}
      <DatasetOptimizationHeaderComponent
        optimization={optimizationData}
        isLoading={isPendingOptimizationData}
      />
      <ShowComponent
        condition={
          optimizationData?.status !== AgentPromptOptimizerStatus.CANCELLED
        }
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            height: "100%",
            overflow: "auto",
          }}
        >
          {/* Steps component - uses dataset optimization endpoints */}
          <DatasetOptimizingSteps
            status={optimizationData?.status}
            optimizationId={optimizationId}
          />

          {/* Loading/Error state - reuse from simulation */}
          <ShowComponent
            condition={
              optimizationData?.status !== AgentPromptOptimizerStatus.COMPLETED
            }
          >
            <OptmizationLoaderComponent
              optimizationStatus={optimizationData?.status}
              reason={optimizationData?.errorMessage}
            />
          </ShowComponent>

          {/* Results - only show when completed */}
          <ShowComponent
            condition={
              optimizationData?.status === AgentPromptOptimizerStatus.COMPLETED
            }
          >
            <DatasetOptimizationResultGraph optimizationId={optimizationId} />
            <DatasetOptimizationResultProvider>
              <DatasetOptimizationResultBar
                optimizationData={optimizationData}
              />
              <DatasetOptimizationResultGrid
                optimizationId={optimizationId}
                onTrialClick={handleTrialClick}
              />
            </DatasetOptimizationResultProvider>
          </ShowComponent>
        </Box>
      </ShowComponent>
      <ShowComponent
        condition={
          optimizationData?.status === AgentPromptOptimizerStatus.CANCELLED
        }
      >
        <DatasetOptimizationCancelled optimization={optimizationData} />
      </ShowComponent>
    </Stack>
  );
};

DatasetOptimizationDetail.propTypes = {
  optimizationId: PropTypes.string.isRequired,
  onBack: PropTypes.func.isRequired,
  onSelectTrial: PropTypes.func,
};

export default DatasetOptimizationDetail;
