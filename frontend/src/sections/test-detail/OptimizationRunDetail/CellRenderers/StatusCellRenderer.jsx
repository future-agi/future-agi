import { Box, IconButton, styled, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { AgentPromptOptimizerStatus } from "../../FixMyAgentDrawer/common";
import SvgColor from "../../../../components/svg-color";
import { ShowComponent } from "../../../../components/show";
import CustomTooltip from "src/components/tooltip/CustomTooltip";
import { useOptimizationRunDetailStoreShallow } from "../states";
import { format } from "date-fns";

const statusColorMap = {
  [AgentPromptOptimizerStatus.PENDING]: {
    backgroundColor: "action.hover",
    color: "primary.main",
    title: "Queue",
  },
  [AgentPromptOptimizerStatus.RUNNING]: {
    backgroundColor: "blue.o10",
    color: "blue.500",
    title: "Running",
  },
  [AgentPromptOptimizerStatus.COMPLETED]: {
    backgroundColor: "green.o10",
    color: "green.500",
    title: "Completed",
  },
  [AgentPromptOptimizerStatus.FAILED]: {
    backgroundColor: "red.o10",
    color: "red.500",
    title: "Failed",
  },
};

const StyledIconButton = styled(IconButton)(({ theme }) => ({
  borderRadius: theme.spacing(1),
  border: `1px solid ${theme.palette.divider}`,
  padding: "6px",
  color: "text.primary",
}));

const StatusCellRenderer = ({ value, data }) => {
  const { setOpenOptimizationRerun } = useOptimizationRunDetailStoreShallow(
    (state) => ({
      setOpenOptimizationRerun: state.setOpenOptimizationRerun,
    }),
  );
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
      <ShowComponent condition={value === AgentPromptOptimizerStatus.FAILED}>
        <CustomTooltip
          show={true}
          title="Re run optimization. This will create a new optimization run."
          arrow
          size="small"
          type="black"
        >
          <StyledIconButton
            className="optimization-run-detail-refresh-button"
            onClick={() =>
              setOpenOptimizationRerun({
                name: `${data?.optimisation_name} - Rerun - ${format(new Date(), "dd MMM yyyy")}`,
                model: data?.model,
                optimiserType: data?.optimiser_type,
                configuration: data?.configuration,
              })
            }
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
            statusColorMap?.[AgentPromptOptimizerStatus.PENDING]
              ?.backgroundColor,
          color:
            statusColorMap?.[value]?.color ??
            statusColorMap?.[AgentPromptOptimizerStatus.PENDING]?.color,
          paddingX: 1,
          paddingY: 0.5,
          borderRadius: 0.5,
        }}
      >
        <Typography typography="s3" fontWeight="fontWeightMedium">
          {statusColorMap?.[value]?.title ?? "-"}
        </Typography>
      </Box>
    </Box>
  );
};

StatusCellRenderer.propTypes = {
  value: PropTypes.string,
  data: PropTypes.object,
};

export default StatusCellRenderer;
