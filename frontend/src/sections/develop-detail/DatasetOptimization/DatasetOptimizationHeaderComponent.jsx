import {
  Box,
  Button,
  Chip,
  Divider,
  Link,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useState } from "react";
import Image from "src/components/image";
import { ShowComponent } from "src/components/show";
import SvgColor from "src/components/svg-color";
import CallStatus from "src/sections/test/CallLogs/CallStatus";
import {
  DatasetOptimizationStatus,
  KeyOptimizerMapping,
  convertKeysToSnakeCase,
} from "./common";
import { useDatasetOptimizationStoreShallow } from "./states";
import CustomTooltip from "src/components/tooltip";
import { formatStartTimeByRequiredFormat } from "src/utils/utils";
import { format } from "date-fns";
import { useSearchParams } from "react-router-dom";
import StopOptimizationModal from "./StopOptimizationModal";
import { useQueryClient } from "@tanstack/react-query";

// Statuses that allow rerun
const RERUN_ALLOWED_STATUSES = [
  DatasetOptimizationStatus.COMPLETED,
  DatasetOptimizationStatus.FAILED,
];
const STOP_ALLOWED_STATUS = [
  DatasetOptimizationStatus.PENDING,
  DatasetOptimizationStatus.RUNNING,
];

// Skeleton component for loading state
const DatasetOptimizationHeaderSkeleton = () => (
  <Stack spacing={2}>
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
        <Box
          sx={{
            width: 200,
            height: 24,
            bgcolor: "background.neutral",
            borderRadius: 1,
          }}
        />
        <Box
          sx={{
            width: 80,
            height: 24,
            bgcolor: "background.neutral",
            borderRadius: 1,
          }}
        />
      </Box>
      <Box
        sx={{
          width: 180,
          height: 16,
          bgcolor: "background.neutral",
          borderRadius: 1,
        }}
      />
    </Box>
    <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
      <Box
        sx={{
          width: 180,
          height: 32,
          bgcolor: "background.neutral",
          borderRadius: 1,
        }}
      />
      <Box
        sx={{
          width: 120,
          height: 32,
          bgcolor: "background.neutral",
          borderRadius: 1,
        }}
      />
    </Box>
  </Stack>
);

// Docs links for optimizer types
const OPTIMIZER_DOCS_LINKS = {
  random_search:
    "https://docs.futureagi.com/docs/optimization/optimizers/random-search",
  bayesian:
    "https://docs.futureagi.com/docs/optimization/optimizers/bayesian-search",
  protegi: "https://docs.futureagi.com/docs/optimization/optimizers/protegi",
  metaprompt:
    "https://docs.futureagi.com/docs/optimization/optimizers/meta-prompt",
  promptwizard:
    "https://docs.futureagi.com/docs/optimization/optimizers/promptwizard",
  gepa: "https://docs.futureagi.com/docs/optimization/optimizers/gepa",
};

const getDocsLinkBasedOnOptimizer = (optimizerType) => {
  return (
    OPTIMIZER_DOCS_LINKS[optimizerType] ||
    "https://docs.futureagi.com/docs/optimization"
  );
};

/**
 * Dataset Optimization Header Component
 *
 * Similar to OptimizeAgentHeaderComponent from simulation, but without
 * the simulation-specific rerun modal and navigation.
 */
const DatasetOptimizationHeaderComponent = ({ optimization, isLoading }) => {
  const {
    optimiserType,
    model,
    status,
    optimiserName,
    providerLogo,
    parameters,
    startTime,
    configuration,
    columnId,
    columnName,
    optimizerModelId,
    userEvalTemplates,
  } = optimization || {};

  const [anchorRef, setAnchorRef] = useState(null);
  const [searchParams] = useSearchParams();
  const optimizationId = searchParams.get("optimizationId");
  const {
    setRerunDefaultValues,
    setIsCreateDrawerOpen,
    setStopOptimizationId,
  } = useDatasetOptimizationStoreShallow((state) => ({
    setRerunDefaultValues: state.setRerunDefaultValues,
    setIsCreateDrawerOpen: state.setIsCreateDrawerOpen,
    setStopOptimizationId: state.setStopOptimizationId,
  }));

  const queryClient = useQueryClient();
  const handleStopSuccess = () => {
    queryClient.invalidateQueries(["dataset-optimization-details"]);
  };

  const handleRerunOptimization = () => {
    // Convert configuration keys from camelCase (API response) to snake_case (form expects)
    const snakeCaseConfig = convertKeysToSnakeCase(configuration);

    // Generate new name for rerun
    const rerunName = `${optimiserName} - Rerun - ${format(new Date(), "dd MMM yyyy, HH:mm")}`;

    // Pre-populate drawer with previous optimization settings
    const rerunValues = {
      name: rerunName,
      optimizer_model_id: optimizerModelId,
      optimizer_algorithm: optimiserType,
      optimizer_config: snakeCaseConfig,
      column_id: columnId,
      column_name: columnName,
      userEvalTemplateIds: userEvalTemplates || [],
    };
    setRerunDefaultValues(rerunValues);
    setIsCreateDrawerOpen(true);
  };

  if (isLoading) {
    return <DatasetOptimizationHeaderSkeleton />;
  }

  const formattedStartTime = formatStartTimeByRequiredFormat(
    startTime,
    "MMM dd, yyyy 'at' h:mm a",
  );

  return (
    <Stack spacing={2}>
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Typography variant="m3" fontWeight="fontWeightSemiBold">
            {optimiserName}
          </Typography>
          <CallStatus value={status ?? "completed"} />
        </Box>

        <ShowComponent condition={!!startTime && !!formattedStartTime}>
          <Typography typography="s3" fontWeight="fontWeightRegular">
            Optimization ran on {formattedStartTime}
          </Typography>
        </ShowComponent>
      </Box>

      <Box sx={{ display: "flex", justifyContent: "space-between" }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Chip
            sx={{
              backgroundColor: "action.selected",
              color: "primary.main",
              ":hover": {
                backgroundColor: "action.selected",
              },
              "& .MuiChip-icon": { color: "primary.main" },
            }}
            icon={
              <SvgColor
                sx={{ width: 16 }}
                src="/assets/icons/navbar/ic_optimize.svg"
              />
            }
            label={`Optimizer Used - ${KeyOptimizerMapping?.[optimiserType] ?? optimiserType}`}
          />

          <Chip
            sx={{
              backgroundColor: "blue.o10",
              color: "blue.500",
              ":hover": {
                backgroundColor: "blue.o10",
              },
              "& .MuiChip-icon": { color: "blue.500" },
            }}
            icon={
              providerLogo ? (
                <Image
                  ratio="1/1"
                  src={providerLogo}
                  alt={model}
                  style={{ width: "16px", height: "16px" }}
                />
              ) : undefined
            }
            label={`Model Used - ${model || "N/A"}`}
          />

          <ShowComponent condition={parameters?.length > 0}>
            <Button
              sx={{ px: "8px" }}
              variant="outlined"
              size="small"
              startIcon={
                <SvgColor
                  src="/assets/prompt/slider-options.svg"
                  sx={{ height: "16px", width: "16px", color: "text.primary" }}
                />
              }
              onClick={(e) => setAnchorRef(e.currentTarget)}
            >
              {`Parameters${parameters?.length > 0 ? ` (${parameters?.length})` : ""}`}
            </Button>
          </ShowComponent>
        </Box>
        <ShowComponent condition={STOP_ALLOWED_STATUS.includes(status)}>
          <Button
            variant="outlined"
            size="small"
            sx={{ borderColor: "#9C9C9C" }}
            startIcon={<SvgColor src="/assets/icons/ic_stop_v2.svg" />}
            onClick={() => {
              setStopOptimizationId(optimizationId);
            }}
          >
            Stop Optimization
          </Button>
        </ShowComponent>
        {/* Rerun Optimization Button - shows when completed or failed */}
        <ShowComponent condition={RERUN_ALLOWED_STATUSES.includes(status)}>
          <Box sx={{ display: "flex", gap: 1 }}>
            <Button
              variant="outlined"
              size="small"
              sx={{ borderColor: "divider" }}
              startIcon={
                <SvgColor src="/assets/icons/navbar/ic_optimize.svg" />
              }
              onClick={handleRerunOptimization}
            >
              Rerun Optimization
            </Button>
          </Box>
        </ShowComponent>
      </Box>
      <StopOptimizationModal onSuccess={handleStopSuccess} />

      {/* Parameters Popover */}
      <Popover
        open={Boolean(anchorRef)}
        anchorEl={anchorRef}
        onClose={() => setAnchorRef(null)}
        anchorOrigin={{
          vertical: "bottom",
          horizontal: "left",
        }}
      >
        <Box sx={{ p: 1, minWidth: 288 }}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "row",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <Typography
              typography="s1"
              fontWeight="fontWeightBold"
              gutterBottom
            >
              Parameters
            </Typography>
            <Link
              href={getDocsLinkBasedOnOptimizer(optimiserType)}
              color="blue.500"
              target="_blank"
              rel="noopener noreferrer"
              fontWeight="fontWeightMedium"
              fontSize="14px"
              sx={{
                textDecoration: "underline",
                fontSize: "13px",
                alignSelf: "center",
                marginBottom: 1,
              }}
            >
              Learn more
            </Link>
          </Box>

          <ShowComponent condition={parameters?.length > 0}>
            <Stack spacing={1}>
              {parameters?.map((param, index) => (
                <Stack key={param?.key || index} spacing={0.5}>
                  <Box
                    sx={{
                      display: "flex",
                      flexDirection: "row",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <Box
                      sx={{
                        display: "flex",
                        flexDirection: "row",
                        alignItems: "center",
                        gap: 0.5,
                      }}
                    >
                      <Typography
                        typography="s2_1"
                        fontWeight="fontWeightRegular"
                      >
                        {param?.label}
                      </Typography>
                      <CustomTooltip
                        type="black"
                        show={!!param?.description}
                        title={param?.description}
                        placement="top"
                      >
                        <SvgColor
                          sx={{
                            width: "12px",
                            height: "12px",
                            marginTop: "4px",
                          }}
                          src="/assets/icons/ic_info.svg"
                        />
                      </CustomTooltip>
                    </Box>

                    <Typography typography="s2_1" fontWeight="fontWeightMedium">
                      {param?.value}
                    </Typography>
                  </Box>
                  <ShowComponent condition={index !== parameters?.length - 1}>
                    <Divider />
                  </ShowComponent>
                </Stack>
              ))}
            </Stack>
          </ShowComponent>
        </Box>
      </Popover>
    </Stack>
  );
};

DatasetOptimizationHeaderComponent.propTypes = {
  optimization: PropTypes.shape({
    id: PropTypes.string,
    optimiserType: PropTypes.string,
    model: PropTypes.string,
    status: PropTypes.string,
    startTime: PropTypes.string,
    parameters: PropTypes.array,
    optimiserName: PropTypes.string,
    providerLogo: PropTypes.string,
    configuration: PropTypes.object,
    columnId: PropTypes.string,
    columnName: PropTypes.string,
    optimizerModelId: PropTypes.string,
    userEvalTemplates: PropTypes.array,
  }),
  isLoading: PropTypes.bool,
};

export default DatasetOptimizationHeaderComponent;
