import { Box, Button, IconButton, styled, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { DatasetOptimizationStatus, convertKeysToSnakeCase } from "../common";
import SvgColor from "src/components/svg-color";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useDatasetOptimizationStoreShallow } from "../states";

const statusColorMap = {
  [DatasetOptimizationStatus.PENDING]: {
    backgroundColor: "action.hover",
    color: "primary.main",
    title: "Queue",
  },
  [DatasetOptimizationStatus.RUNNING]: {
    backgroundColor: "blue.o10",
    color: "blue.500",
    title: "Running",
  },
  [DatasetOptimizationStatus.COMPLETED]: {
    backgroundColor: "green.o10",
    color: "green.500",
    title: "Completed",
  },
  [DatasetOptimizationStatus.FAILED]: {
    backgroundColor: "red.o10",
    color: "red.500",
    title: "Failed",
  },
  [DatasetOptimizationStatus.CANCELLED]: {
    backgroundColor: "red.o10",
    color: "red.500",
    title: "Cancelled",
  },
};

const StyledIconButton = styled(IconButton)(({ theme }) => ({
  borderRadius: theme.spacing(1),
  border: `1px solid ${theme.palette.divider}`,
  padding: "6px",
  color: "text.primary",
}));

const StatusCellRenderer = ({ value, data }) => {
  const {
    setRerunDefaultValues,
    setIsCreateDrawerOpen,
    setStopOptimizationId,
  } = useDatasetOptimizationStoreShallow((state) => ({
    setRerunDefaultValues: state.setRerunDefaultValues,
    setIsCreateDrawerOpen: state.setIsCreateDrawerOpen,
    setStopOptimizationId: state.setStopOptimizationId,
  }));

  const handleRerun = () => {
    // Convert optimizer_config keys to snake_case since the form expects snake_case
    const snakeCaseConfig = convertKeysToSnakeCase(data?.optimizerConfig);

    // Don't set name - let the drawer auto-generate it based on column/optimizer/timestamp
    setRerunDefaultValues({
      optimizer_model_id: data?.optimizerModelId,
      optimizer_algorithm: data?.optimizerAlgorithm,
      optimizer_config: snakeCaseConfig,
      column_id: data?.columnId,
    });
    setIsCreateDrawerOpen(true);
  };

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        lineHeight: 1.5,
        height: "100%",
      }}
    >
      <ShowComponent condition={value === DatasetOptimizationStatus.FAILED}>
        <CustomTooltip
          show={true}
          title="Re-run optimization. This will create a new optimization run."
          arrow
          size="small"
          type="black"
        >
          <StyledIconButton
            className="optimization-run-detail-refresh-button"
            onClick={handleRerun}
          >
            <SvgColor
              src="/assets/icons/ic_refresh.svg"
              sx={{ width: 16, height: 16, color: "text.primary" }}
            />
          </StyledIconButton>
        </CustomTooltip>
      </ShowComponent>
      <Box
        sx={{
          backgroundColor:
            statusColorMap?.[value]?.backgroundColor ??
            statusColorMap?.[DatasetOptimizationStatus.PENDING]
              ?.backgroundColor,
          color:
            statusColorMap?.[value]?.color ??
            statusColorMap?.[DatasetOptimizationStatus.PENDING]?.color,
          paddingX: 1,
          paddingY: 0.5,
          borderRadius: 0.5,
        }}
      >
        <Typography typography="s3" fontWeight="fontWeightMedium">
          {statusColorMap?.[value]?.title ?? "-"}
        </Typography>
      </Box>

      <ShowComponent
        condition={
          value === DatasetOptimizationStatus.PENDING ||
          value === DatasetOptimizationStatus.RUNNING
        }
      >
        <Button
          size="small"
          className="optimization-run-detail-refresh-button"
          onClick={(e) => {
            e.stopPropagation();
            setStopOptimizationId(data?.id);
          }}
          sx={{
            border: "1px solid",
            borderColor: "black.100",
            color: "red.500",
          }}
          startIcon={
            <SvgColor color={"red.500"} src={"/assets/icons/ic_stop_v2.svg"} />
          }
        >
          Stop
        </Button>
      </ShowComponent>
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
};

export default StatusCellRenderer;
