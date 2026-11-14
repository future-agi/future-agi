import { Box, Button, IconButton, styled } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { DatasetOptimizationStatus, convertKeysToSnakeCase } from "../common";
import SvgColor from "src/components/svg-color";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useDatasetOptimizationStoreShallow } from "../states";

const statusMap = {
  [DatasetOptimizationStatus.PENDING]: { role: "neutral", title: "Queue" },
  [DatasetOptimizationStatus.RUNNING]: { role: "info", title: "Running" },
  [DatasetOptimizationStatus.COMPLETED]: {
    role: "success",
    title: "Completed",
  },
  [DatasetOptimizationStatus.FAILED]: { role: "danger", title: "Failed" },
  [DatasetOptimizationStatus.CANCELLED]: {
    role: "neutral",
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
      <StatusBadge
        status={value}
        label={statusMap[value]?.title ?? "-"}
        role={statusMap[value]?.role ?? "neutral"}
      />

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
            borderColor: "divider",
            color: "status.danger.fg",
          }}
          startIcon={
            <SvgColor
              color={"status.danger.fg"}
              src={"/assets/icons/ic_stop_v2.svg"}
            />
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
